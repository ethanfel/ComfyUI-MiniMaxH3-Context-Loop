"""Scoped, preview-confirmed deletion of processed takes (never originals)."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from .checkpoint_manager import (
    CheckpointDeleteBlocked, _strict_run_name, checkpoint_run_lock,
)
from .checkpoint_variants import validate_processing_lineage


REVISION = re.compile(r"clip_(\d{4})\.([0-9a-f]{32})\.json")
POINTER = re.compile(r"clip_\d{4}\.json")
MANIFESTS = {"h3_chain_upscale_manifest_v1", "h3_chain_upscale_partial_manifest_v1"}
ARTIFACTS = {
    "segment": ("segments", ".mp4", "Processed video"),
    "checkpoint": ("checkpoints", ".safetensors", "Processed checkpoint"),
    "prompt_file": ("prompts", ".txt", "Prompt snapshot"),
    "generated_audio": ("audio", ".wav", "Processed audio"),
}


def _independent_pixel_take(metadata):
    """Only the saved pixel backend proves that HQ prefixes were not used.

    context_steps alone describes the outgoing tail, not incoming context.
    Never infer independence from a profile name, a UI stage, or missing data.
    """
    config = metadata.get("profile_config")
    segment = metadata.get("segment")
    return (isinstance(config, dict) and config.get("backend") == "pixel"
            and isinstance(segment, dict)
            and type(segment.get("context_steps")) is int
            and segment["context_steps"] == 0)


class ProcessingCheckpointManager:
    def __init__(self, output_root):
        self.root = Path(output_root).resolve()

    def _path(self, address):
        if not isinstance(address, str) or not address:
            raise ValueError("A saved processing artifact address is required.")
        parts = PurePosixPath(address).parts
        if (PurePosixPath(address).is_absolute() or ".." in parts or
                "\\" in address or str(PurePosixPath(address)) != address):
            raise ValueError("Invalid processing artifact address.")
        path = self.root
        for part in parts:
            path /= part
            if path.is_symlink():
                raise ValueError("Processing deletion cannot follow symlinks: %s" % address)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Processing artifact escapes the output directory.")
        if path.exists() and not (path.is_file() or path.is_dir()):
            raise ValueError("Unsupported processing artifact: %s" % address)
        return path

    def _address(self, path):
        return path.relative_to(self.root).as_posix()

    def _read(self, path):
        self._path(self._address(path))
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Processing metadata must be an object: %s" % path.name)
        return value

    def _target(self, run, address):
        path = self._path(address)
        parts = PurePosixPath(address).parts
        if not (parts[:2] == ("h3_chains", run) and (
                len(parts) == 6 and parts[2] == "upscaled" or
                len(parts) == 8 and parts[2] == "chapters" and parts[4] == "upscaled")
                and parts[-2] == "checkpoints" and REVISION.fullmatch(parts[-1])):
            raise ValueError("Select an immutable processed checkpoint inside this run.")
        if not path.is_file():
            raise FileNotFoundError("The selected processed take no longer exists.")
        return path, path.parent.parent

    def _documents(self, run):
        run_dir = self._path("h3_chains/" + run)
        parents = [run_dir / "upscaled"]
        chapters = self._path(self._address(run_dir / "chapters"))
        if chapters.is_dir():
            for chapter in chapters.iterdir():
                self._path(self._address(chapter))
                if chapter.is_dir():
                    parents.append(chapter / "upscaled")
        docs = {}
        for parent in parents:
            self._path(self._address(parent))
            if not parent.is_dir():
                continue
            for profile in sorted(parent.iterdir()):
                self._path(self._address(profile))
                if not profile.is_dir():
                    continue
                folder = self._path(self._address(profile / "checkpoints"))
                files = list(folder.glob("clip_*.json")) if folder.is_dir() else []
                files.append(profile / "upscale_manifest.json")
                partial = self._path(self._address(profile / "partial"))
                if partial.is_dir():
                    files.extend(partial.glob("through_clip_*.manifest.json"))
                for path in sorted(files):
                    self._path(self._address(path))
                    if not path.exists():
                        continue
                    value = self._read(path)
                    if (value.get("run_name") != run or value.get("profile") != profile.name or
                            value.get("format") not in MANIFESTS | {"h3_chain_upscale_segment_v1"}):
                        raise ValueError("Cannot verify processing metadata: %s" % self._address(path))
                    if path.parent == folder:
                        if value["format"] != "h3_chain_upscale_segment_v1":
                            raise ValueError("Not a processing checkpoint: %s" % path.name)
                        match = REVISION.fullmatch(path.name)
                        if not match and not POINTER.fullmatch(path.name):
                            raise ValueError("Unrecognized processing checkpoint: %s" % path.name)
                        segment = value.get("segment")
                        if not isinstance(segment, dict):
                            raise ValueError("Processing checkpoint has no segment: %s" % path.name)
                        scene, revision = segment.get("index"), segment.get("revision")
                        if (type(scene) is not int or scene < 1 or
                                not re.fullmatch(r"[0-9a-f]{32}", str(revision)) or
                                path.name != ("clip_%04d.%s.json" % (scene, revision) if match
                                              else "clip_%04d.json" % scene)):
                            raise ValueError("Processing revision identity mismatch: %s" % path.name)
                        canonical = folder / ("clip_%04d.%s.json" % (scene, revision))
                        if self._path(segment.get("revision_metadata")) != canonical:
                            raise ValueError("Processing revision address mismatch: %s" % path.name)
                        if "processing_lineage" in value:
                            validate_processing_lineage(value["processing_lineage"])
                    elif not isinstance(value.get("segments"), list):
                        raise ValueError("Processing manifest has no segment list: %s" % path.name)
                    docs[path] = value
        return docs

    def _preview(self, run, address):
        target, profile = self._target(run, address)
        docs = self._documents(run)
        metadata = docs[target]
        segment = metadata["segment"]
        scene, revision = segment["index"], segment["revision"]
        stem = "clip_%04d.%s" % (scene, revision)
        owned = {target: "Revision metadata"}
        for field, (folder, suffix, label) in ARTIFACTS.items():
            if not segment.get(field):
                continue  # Missing optional or already-lost artifacts do not prevent cleanup.
            path = self._path(segment[field])
            if path != profile / folder / (stem + suffix):
                raise ValueError("Processed take does not own its %s path." % field)
            owned[path] = label
        pointer = profile / "checkpoints" / ("clip_%04d.json" % scene)
        if pointer in docs and docs[pointer]["segment"]["revision"] == revision:
            owned[pointer] = "Current processed-take pointer (cleared, not rolled back)"

        def refers(value):
            if isinstance(value, dict):
                if any(value.get(k) == revision for k in (
                        "predecessor_revision", "previous_revision", "context_revision")):
                    return True
                if (value.get("source_revision") == revision and
                        (not value.get("source_checkpoint") or value["source_checkpoint"] in addresses)):
                    return True
                address = value.get("revision_metadata", value.get("metadata_path"))
                if (value.get("revision") == revision and value.get("index", value.get("scene")) == scene
                        and (not address or address in addresses)):
                    return True
                return any(refers(v) for k, v in value.items() if k != "supersedes")
            if isinstance(value, list):
                return any(refers(v) for v in value)
            return isinstance(value, str) and value in addresses

        addresses = {self._address(path) for path in owned if path != pointer}
        independent = _independent_pixel_take(metadata)
        retained = {}

        def independent_successor(item):
            if not independent or not isinstance(item, dict) or not item.get("revision_metadata"):
                return False
            path = self._path(item["revision_metadata"])
            saved = docs.get(path)
            if (path.parent.parent != profile or not saved or not _independent_pixel_take(saved)
                    or saved["segment"]["index"] <= scene
                    or any(saved["segment"].get(k) != item.get(k)
                           for k in ("index", "revision", "checkpoint_sha256"))):
                return False
            retained[item["revision_metadata"]] = {
                "scene": item["index"], "revision": item["revision"],
                "metadata_path": item["revision_metadata"],
            }
            return True

        dependents = []
        for path, value in docs.items():
            if path in owned:
                continue
            if value["format"] in MANIFESTS:
                if refers(value.get("source_manifest")):
                    dependents.append({"metadata_path": self._address(path),
                                       "reason": "uses this take as a processing source"})
                elif refers(value["segments"]):
                    positions = [i for i, item in enumerate(value["segments"]) if refers(item)]
                    later = value["segments"][positions[0] + 1:]
                    # Sequence membership is not an input dependency for
                    # independently rendered pixel takes. Invalidate this
                    # sequence's manifest, never delete its surviving clips.
                    blocked = [item for item in later if not independent_successor(item)]
                    if blocked:
                        for item in blocked:
                            dependents.append({"scene": item.get("index"), "revision": item.get("revision"),
                                               "metadata_path": item.get("revision_metadata") or self._address(path),
                                               "reason": "saved branch continues after this take"})
                    else:
                        owned[path] = "Affected branch manifest (invalidated; assembled video kept)"
            else:
                other = value["segment"]
                ignored = {"segment"}
                if (refers(value.get("processing_lineage"))
                        and independent_successor(other)
                        and _independent_pixel_take(value)):
                    ignored.add("processing_lineage")
                if (refers({k: v for k, v in value.items() if k not in ignored})
                        or refers(other)
                        or (not value.get("processing_lineage") and path.parent.parent == profile
                            and other["index"] > scene and segment.get("context_steps", 0))):
                    dependents.append({"scene": other["index"], "revision": other["revision"],
                                       "metadata_path": other["revision_metadata"],
                                       "reason": "saved take depends on this source or branch"})
        dependents = list({item["metadata_path"]: item for item in dependents}.values())
        for item in dependents:
            retained.pop(item["metadata_path"], None)
        files = []
        for path, label in sorted(owned.items()):
            self._path(self._address(path))
            if path.exists() and not path.is_file():
                raise ValueError("Expected a regular processing file: %s" % path.name)
            stat = path.stat() if path.exists() else None
            files.append({"path": self._address(path), "label": label, "owned": True,
                          "shared": False, "exists": stat is not None,
                          "size_bytes": stat.st_size if stat else 0,
                          "_identity": (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                                        stat.st_ctime_ns) if stat else None})
        snapshot = hashlib.sha256(json.dumps({
            "run": run, "target": address, "files": files,
            "documents": {self._address(p): v for p, v in docs.items()},
        }, sort_keys=True).encode()).hexdigest()
        return {"ok": True, "run_name": run, "metadata_path": address,
                "scene": scene, "revision": revision, "profile": profile.name,
                "allowed": not dependents, "dependents": dependents,
                "retained_independent_takes": sorted(retained.values(), key=lambda item: (
                    item["scene"], item["metadata_path"])),
                "blockers": (["Delete the dependent processed takes first; their saved branch/source still uses this take."]
                             if dependents else []),
                "files": [{k: v for k, v in f.items() if not k.startswith("_")} for f in files],
                "owned_file_count": sum(f["exists"] for f in files),
                "reclaimed_bytes": sum(f["size_bytes"] for f in files), "snapshot": snapshot,
                "not_deleted": ["Original generation clips and checkpoints", "Shared references and reference caches",
                                "Other processed takes and profiles", "Assembled videos, run archives and prompt history"]}

    def deletion_preview(self, run_name, metadata_path):
        run = _strict_run_name(run_name)
        with checkpoint_run_lock(str(self.root), run):
            return self._preview(run, metadata_path)

    def delete(self, run_name, metadata_path, expected_snapshot=""):
        run = _strict_run_name(run_name)
        with checkpoint_run_lock(str(self.root), run):
            preview = self._preview(run, metadata_path)
            if not preview["allowed"]:
                raise CheckpointDeleteBlocked(" ".join(preview["blockers"]), preview)
            if not expected_snapshot or expected_snapshot != preview["snapshot"]:
                raise CheckpointDeleteBlocked("Processed files or dependencies changed; preview deletion again.", preview)
            staged = []
            transaction = uuid.uuid4().hex
            try:
                for item in preview["files"]:
                    if not item["exists"]:
                        continue
                    path = self._path(item["path"])
                    temporary = path.with_name(path.name + ".delete." + transaction + ".tmp")
                    os.replace(path, temporary)
                    staged.append((path, temporary, item["size_bytes"]))
            except Exception:
                for path, temporary, _ in reversed(staged):
                    os.replace(temporary, path)
                raise
            pending, reclaimed = [], 0
            for path, temporary, size in staged:
                try:
                    temporary.unlink()
                    reclaimed += size
                except OSError:
                    pending.append(self._address(temporary))
            return {"ok": True, "deleted_files": len(staged) - len(pending),
                    "reclaimed_bytes": reclaimed, "cleanup_pending": pending,
                    "message": "Deleted processed scene %d take %s (%s). Original clips are unchanged.%s" % (
                        preview["scene"], preview["revision"][:8], preview["profile"],
                        " Some staged files could not be removed; cleanup_pending lists their paths." if pending else "")}


def require_saved_processing_segments(output_root, segments):
    """Fence in-flight saves/loop completion against a concurrent deletion.

    Called under checkpoint_run_lock, before publishing pointers or manifests.
    JSON identity and file presence only; never load/hash multi-GB tensors here.
    """
    manager = ProcessingCheckpointManager(output_root)
    for segment in segments:
        path = manager._path(segment.get("revision_metadata"))
        if not path.is_file():
            raise ValueError("A processed source/branch take was deleted while this run was executing; reselect the source and requeue.")
        saved = manager._read(path).get("segment", {})
        if any(saved.get(key) != segment.get(key) for key in (
                "index", "revision", "checkpoint", "checkpoint_sha256")):
            raise ValueError("Processed source/branch identity changed while this run was executing.")
        if not manager._path(saved.get("checkpoint")).is_file():
            raise ValueError("A processed source/branch checkpoint is missing; reselect the source and requeue.")
