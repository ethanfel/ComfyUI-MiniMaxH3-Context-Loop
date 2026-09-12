"""Portable, versioned media bindings for saved H3 chain runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any


# The canvas keeps a small number of direct loader sockets. Semantic Anchor
# Bundle contributes many loader bindings through one dedicated Run Manager
# socket, so the persisted manifest accepts a larger bounded collection.
MAX_DIRECT_ASSET_BINDINGS = 12
MAX_ASSET_BINDINGS = 128
ASSET_MANIFEST_FORMAT = "h3_chain_assets_v1"
ASSET_ROLES = ("picture", "video", "audio_reference", "source_track")


def _safe_name(value: Any, fallback: str = "asset") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")
    return (text or fallback)[:128]


def _strict_run_name(value: Any) -> str:
    requested = str(value or "").strip()
    normalized = _safe_name(requested, "")[:96]
    if not normalized:
        raise ValueError("A non-empty H3 chain run_name is required.")
    if requested != normalized:
        raise ValueError("H3 asset recovery requires the exact saved run name.")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(os.path.dirname(path), os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                pass
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _inside(root: str, path: str) -> bool:
    try:
        return os.path.commonpath(
            (os.path.realpath(root), os.path.realpath(path))) == os.path.realpath(root)
    except ValueError:
        return False


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _role_group(role: str) -> str:
    if role == "picture":
        return "images"
    if role == "video":
        return "video"
    return "audio"


def _binding_id(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", text):
        return text
    return uuid.uuid4().hex


class RunAssetStore:
    """Save loader-backed media and materialize missing-input fallbacks."""

    def __init__(self, output_root: str, input_root: str | None):
        self.output_root = os.path.realpath(os.path.abspath(output_root))
        self.input_root = (os.path.realpath(os.path.abspath(input_root))
                           if input_root is not None else None)
        self.chains_root = os.path.join(self.output_root, "h3_chains")

    def _run_dir(self, run_name: Any) -> tuple[str, str]:
        run = _strict_run_name(run_name)
        path = os.path.realpath(os.path.join(self.chains_root, run))
        if not _inside(self.output_root, path):
            raise ValueError("H3 asset run path escapes the output directory.")
        return path, run

    def _manifest_path(self, run_name: Any) -> tuple[str, str, str]:
        directory, run = self._run_dir(run_name)
        return (os.path.join(directory, "references", "manifest.json"),
                directory, run)

    def _source_path(self, value: Any) -> str | None:
        if self.input_root is None or not isinstance(value, str):
            return None
        name = value.strip()
        if not name:
            return None
        if name.endswith("[input]"):
            name = name[:-7].rstrip()
        elif name.endswith("[output]") or name.endswith("[temp]"):
            return None
        path = os.path.abspath(os.path.join(self.input_root, name))
        if not _inside(self.input_root, path) or not os.path.isfile(path):
            return None
        return path

    def _archived_path(self, run_dir: str, relative: Any) -> str | None:
        if not isinstance(relative, str) or not relative:
            return None
        path = os.path.realpath(os.path.join(run_dir, relative))
        if not _inside(run_dir, path) or not os.path.isfile(path):
            return None
        return path

    def _copy_to_archive(self, source: str, run_dir: str,
                         role: str) -> dict[str, Any]:
        group = _role_group(role)
        destination_dir = os.path.join(run_dir, "references", group)
        os.makedirs(destination_dir, exist_ok=True)
        suffix = os.path.splitext(source)[1][:24]
        basename = _safe_name(os.path.splitext(os.path.basename(source))[0])
        temporary = os.path.join(destination_dir, ".%s.tmp" % uuid.uuid4().hex)
        digest = hashlib.sha256()
        size = 0
        try:
            with open(source, "rb") as source_handle, open(temporary, "xb") as target:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            filename = "%s_%s%s" % (sha256, basename, suffix)
            destination = os.path.join(destination_dir, filename)
            if (os.path.isfile(destination)
                    and os.path.getsize(destination) == size
                    and _file_sha256(destination) == sha256):
                os.unlink(temporary)
            else:
                os.replace(temporary, destination)
            relative = os.path.relpath(destination, run_dir).replace(os.sep, "/")
            return {
                "relative_path": relative,
                "sha256": sha256,
                "size": size,
                "saved_at": _utc_now(),
                "original_basename": os.path.basename(source),
            }
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _normalized_binding(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        role = str(raw.get("role") or "").strip()
        if role not in ASSET_ROLES:
            return None
        output_slot = raw.get("output_slot", 0)
        node_id = raw.get("node_id")
        try:
            output_slot = int(output_slot)
        except (TypeError, ValueError):
            output_slot = 0
        return {
            "binding_id": _binding_id(raw.get("binding_id")),
            "label": str(raw.get("label") or role).strip()[:160],
            "role": role,
            "node_id": str(node_id) if node_id is not None else "",
            "node_type": str(raw.get("node_type") or "").strip()[:160],
            "node_title": str(raw.get("node_title") or "").strip()[:160],
            "output_slot": max(0, output_slot),
            "output_type": str(raw.get("output_type") or "").strip()[:80],
            "widget_name": str(raw.get("widget_name") or "").strip()[:120],
            "original_value": str(raw.get("original_value") or "").strip()[:4096],
        }

    def load_manifest(self, run_name: Any) -> dict[str, Any] | None:
        path, _directory, _run = self._manifest_path(run_name)
        if not os.path.isfile(path):
            return None
        document = _read_json(path)
        if (not isinstance(document, dict)
                or document.get("format") != ASSET_MANIFEST_FORMAT
                or not isinstance(document.get("bindings"), list)):
            raise ValueError("Saved H3 reference manifest has an unsupported format.")
        return document

    def summary(self, run_name: Any) -> dict[str, Any]:
        _manifest_path, run_dir, _run = self._manifest_path(run_name)
        manifest = self.load_manifest(run_name)
        if manifest is None:
            return {"asset_count": 0, "asset_bytes": 0}
        archived = 0
        for binding in manifest.get("bindings", []):
            archive = binding.get("archive") if isinstance(binding, dict) else None
            if isinstance(archive, dict):
                archived += 1
        total = 0
        archive_files = 0
        # Count the actual retained snapshots, including older versions and
        # safe orphaned files left by a removed binding. Recovery data is not
        # deleted implicitly merely to make a manifest smaller.
        for group in ("images", "audio", "video"):
            directory = os.path.join(run_dir, "references", group)
            if not os.path.isdir(directory):
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.startswith(".") or not entry.is_file(
                            follow_symlinks=False):
                        continue
                    archive_files += 1
                    total += max(0, entry.stat(follow_symlinks=False).st_size)
        return {
            "asset_count": len(manifest.get("bindings", [])),
            "archived_asset_count": archived,
            "asset_file_count": archive_files,
            "asset_bytes": total,
        }

    def save(self, run_name: Any, bindings: Any,
             policies: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest_path, run_dir, run = self._manifest_path(run_name)
        if not isinstance(bindings, list):
            raise ValueError("H3 asset bindings must be a JSON list.")
        if len(bindings) > MAX_ASSET_BINDINGS:
            raise ValueError("H3 Run Manager supports at most %d asset bindings."
                             % MAX_ASSET_BINDINGS)
        policies = policies if isinstance(policies, dict) else {}
        enabled = {
            "images": bool(policies.get("images", True)),
            "audio": bool(policies.get("audio", True)),
            "video": bool(policies.get("video", False)),
        }
        try:
            old_manifest = self.load_manifest(run) or {}
        except (OSError, ValueError, json.JSONDecodeError):
            old_manifest = {}
        old_bindings = {
            item.get("binding_id"): item
            for item in old_manifest.get("bindings", [])
            if isinstance(item, dict) and item.get("binding_id")
        }
        saved = []
        warnings = []
        for raw in bindings:
            binding = self._normalized_binding(raw)
            if binding is None:
                warnings.append("Ignored one binding with an unsupported media role.")
                continue
            previous = old_bindings.get(binding["binding_id"], {})
            versions = list(previous.get("versions") or [])
            prior_archive = previous.get("archive")
            same_source = (
                previous.get("original_value") == binding["original_value"])
            source = self._source_path(binding["original_value"])
            group = _role_group(binding["role"])
            archive = (prior_archive if same_source
                       and isinstance(prior_archive, dict) else None)
            if (isinstance(prior_archive, dict) and not same_source
                    and not any(item.get("sha256") == prior_archive.get("sha256")
                                for item in versions if isinstance(item, dict))):
                versions.append(prior_archive)
            if source is not None and enabled[group]:
                next_archive = self._copy_to_archive(source, run_dir, binding["role"])
                if (archive and archive.get("sha256") != next_archive.get("sha256")
                        and not any(item.get("sha256") == archive.get("sha256")
                                    for item in versions if isinstance(item, dict))):
                    versions.append(archive)
                archive = next_archive
            elif not enabled[group] and archive:
                if not any(item.get("sha256") == archive.get("sha256")
                           for item in versions if isinstance(item, dict)):
                    versions.append(archive)
                archive = None
            if source is None:
                warnings.append(
                    "%s: original input file is unavailable%s." % (
                        binding["label"],
                        " and no archived fallback exists" if not archive else ""))
            binding["archive_policy"] = (
                "fallback_copy" if enabled[group] else "path_only")
            binding["source_available_when_saved"] = source is not None
            if archive:
                binding["archive"] = archive
            if versions:
                binding["versions"] = versions
            saved.append(binding)

        document = {
            "format": ASSET_MANIFEST_FORMAT,
            "run_name": run,
            "updated_at": _utc_now(),
            "policies": enabled,
            "bindings": saved,
        }
        _atomic_json(manifest_path, document)
        summary = self.summary(run)
        return {
            "run_name": run,
            "manifest": os.path.relpath(manifest_path, self.output_root).replace(os.sep, "/"),
            "bindings": saved,
            "warnings": warnings,
            **summary,
        }

    def _materialize_archive(self, run: str, archived_path: str,
                             archive: dict[str, Any]) -> str:
        if self.input_root is None:
            raise ValueError("The ComfyUI input directory is unavailable.")
        os.makedirs(self.input_root, exist_ok=True)
        digest = str(archive.get("sha256") or "")
        basename = _safe_name(
            archive.get("original_basename") or os.path.basename(archived_path))
        filename = "h3_%s_%s_%s" % (run, digest[:16] or "asset", basename)
        destination = os.path.join(self.input_root, filename)
        if not _inside(self.input_root, destination):
            raise ValueError("Restored H3 asset path escapes the input directory.")
        expected = str(archive.get("sha256") or "")
        destination_ready = (os.path.isfile(destination)
                             and (not expected
                                  or _file_sha256(destination) == expected))
        if not destination_ready:
            temporary = "%s.%s.tmp" % (destination, uuid.uuid4().hex)
            try:
                shutil.copy2(archived_path, temporary)
                if expected and _file_sha256(temporary) != expected:
                    raise ValueError("Archived fallback failed its SHA-256 check.")
                os.replace(temporary, destination)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return os.path.basename(destination)

    def prepare_restore(self, run_name: Any) -> dict[str, Any]:
        _path, run_dir, run = self._manifest_path(run_name)
        manifest = self.load_manifest(run)
        if manifest is None:
            return {"run_name": run, "bindings": [], "warnings": []}
        restored = []
        warnings = []
        for original in manifest.get("bindings", []):
            if not isinstance(original, dict):
                continue
            binding = dict(original)
            source = self._source_path(binding.get("original_value"))
            if source is not None:
                binding["restore_value"] = binding.get("original_value")
                binding["restore_source"] = "original_input"
            else:
                archive = binding.get("archive")
                archived_path = self._archived_path(
                    run_dir, archive.get("relative_path")
                    if isinstance(archive, dict) else None)
                if archived_path is None:
                    binding["restore_source"] = "missing"
                    warnings.append("%s: original and archived files are missing."
                                    % binding.get("label", binding.get("role", "asset")))
                else:
                    try:
                        binding["restore_value"] = self._materialize_archive(
                            run, archived_path, archive)
                        binding["restore_source"] = "archived_fallback"
                    except (OSError, ValueError) as exc:
                        binding["restore_source"] = "missing"
                        warnings.append("%s: %s" % (
                            binding.get("label", binding.get("role", "asset")), exc))
            restored.append(binding)
        return {"run_name": run, "bindings": restored, "warnings": warnings}
