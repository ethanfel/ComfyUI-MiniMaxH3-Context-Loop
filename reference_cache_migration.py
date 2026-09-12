"""Lossless legacy reference-cache conversion and success-gated retirement.

The original JSON is retained as a compatibility address. Its neighbouring
*.converted.json is a self-contained V3 manifest. No checkpoint is rewritten.
Conversion never removes a bundle; only a verified, committed render may do so.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

if __package__:
    from . import processing_persistence as persistence
    from .reference_cache_store import FORMAT, ReferenceTensorStore, objects_digest, tensor_digest
    from .storage_resolver import resolve_output, logical_output
else:  # Standalone maintenance CLI, without importing ComfyUI or loading models.
    import processing_persistence as persistence
    from reference_cache_store import FORMAT, ReferenceTensorStore, objects_digest, tensor_digest
    from storage_resolver import resolve_output, logical_output


LEGACY_FORMATS = ("h3_reference_cache_v1", "h3_reference_cache_v2")


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def descriptor(metadata):
    keys = ("format", "signature", "reference_fingerprint", "metadata", "tensors_sha256")
    if metadata.get("format") in LEGACY_FORMATS:
        keys += ("tensors",)
    if not all(isinstance(metadata.get(key), str) and metadata[key] for key in keys):
        raise ValueError("Invalid reference-cache descriptor.")
    return {key: metadata[key] for key in keys}


def matches_legacy(converted, legacy):
    """Location-independent identity also covers a verified run-local copy."""
    conversion = converted.get("legacy_conversion") or {}
    source = conversion.get("source") or {}
    return (converted.get("format") == FORMAT and conversion.get("version") == 1
            and legacy.get("format") in LEGACY_FORMATS
            and all(source.get(key) == legacy.get(key) for key in (
                "format", "signature", "reference_fingerprint", "tensors_sha256")))


def converted_path(path):
    return Path(path).with_suffix(".converted.json")


def sync_directory(path):
    if os.name == "nt":
        return
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class ReferenceCacheMigrator:
    def __init__(self, output_root):
        self.root = Path(output_root).resolve()

    def absolute(self, path):
        return resolve_output(self.root, path)

    def relative(self, path):
        return logical_output(self.root, self.absolute(path))

    def read(self, path):
        with open(self.absolute(path), encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("Reference-cache metadata must be a JSON object.")
        return value

    def atomic_json(self, path, value):
        path = self.absolute(path)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def lock(self, metadata_path, blocking=True):
        path = self.absolute(self.absolute(metadata_path).with_suffix(".conversion.lock"))
        with open(path, "a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def store(self, metadata):
        parent = self.absolute(metadata["metadata"]).parent
        shared = self.absolute("h3_reference_cache")
        root = shared if parent.parent == shared else parent
        return ReferenceTensorStore(str(self.root), str(root / "objects"), file_digest)

    def verify_converted(self, metadata, legacy=None):
        if metadata.get("format") != FORMAT:
            raise ValueError("Converted reference cache is not V3.")
        if legacy is not None and not matches_legacy(metadata, legacy):
            raise ValueError("Converted reference cache belongs to another legacy bundle.")
        objects = metadata.get("tensor_objects")
        if objects_digest(objects) != metadata.get("tensors_sha256"):
            raise ValueError("Converted reference-cache object map failed integrity checks.")
        store = self.store(metadata)
        for record in objects.values():
            store.verify(record)
        return metadata

    def resolve(self, legacy):
        path = converted_path(self.absolute(legacy["metadata"]))
        if not path.is_file():
            return None
        converted = self.read(path)
        if self.absolute(converted["metadata"]) != path:
            raise ValueError("Converted reference cache has an invalid metadata path.")
        self.verify_converted(converted, descriptor(legacy))
        # Retaining the original JSON lets all immutable checkpoint descriptors
        # continue to work after the large bundle is retired.
        if file_digest(self.absolute(legacy["metadata"])) != converted[
                "legacy_conversion"]["source_metadata_sha256"]:
            raise ValueError("Legacy reference-cache metadata changed after conversion.")
        return converted

    def _legacy_target(self, metadata_path, metadata):
        path = self.absolute(metadata_path)
        parts = path.relative_to(self.root).parts
        managed = ((len(parts) == 3 and parts[0] == "h3_reference_cache") or
                   (len(parts) == 4 and parts[0] == "h3_chains" and parts[2] == "reference_cache"))
        if not managed or not re.fullmatch(r"scene_\d+\.[^.]+\.json", path.name):
            raise ValueError("Conversion requires a managed scene reference-cache JSON.")
        legacy = descriptor(metadata)
        if legacy["format"] not in LEGACY_FORMATS or self.absolute(legacy["metadata"]) != path:
            raise ValueError("Conversion requires an exact V1/V2 metadata address.")
        if (not re.fullmatch(r"[0-9a-f]{64}", legacy["signature"])
                or path.name != "scene_%04d.%s.json" % (
                    int(metadata["scene"]), legacy["signature"][:24])):
            raise ValueError("Legacy cache filename does not match its scene/signature.")
        target = self.absolute(legacy["tensors"])
        if target != path.with_suffix(".safetensors") or Path(
                self.root / legacy["tensors"]).is_symlink():
            raise ValueError("Legacy bundle must be the scene JSON's own sibling safetensors file.")
        return target

    def convert(self, metadata_path, apply=False):
        metadata_path = self.absolute(metadata_path)
        self._legacy_target(metadata_path, self.read(metadata_path))
        if apply:
            with self.lock(metadata_path):
                return self._convert(metadata_path, True)
        return self._convert(metadata_path, False)

    def _convert(self, metadata_path, apply):
        from safetensors import safe_open

        legacy = self.read(metadata_path)
        target = self._legacy_target(metadata_path, legacy)
        existing = self.resolve(legacy)
        if existing is not None:
            return {"status": "already_converted", "metadata": existing["metadata"],
                    "legacy_present": target.is_file()}
        before_metadata = file_digest(metadata_path)
        if file_digest(target) != legacy["tensors_sha256"]:
            raise ValueError("Legacy reference bundle failed SHA-256 integrity checks.")
        report = {"status": "would_convert", "metadata": self.relative(metadata_path),
                  "legacy_bytes": target.stat().st_size}
        if not apply:
            with safe_open(str(target), framework="pt", device="cpu") as handle:
                report["tensor_count"] = len(handle.keys())
            return report
        objects = {}
        store = self.store(legacy)
        # The original tensor dtype and bytes are preserved. No model, VAE,
        # quantization or media decode participates in conversion.
        with safe_open(str(target), framework="pt", device="cpu") as handle:
            for key in handle.keys():
                value = handle.get_tensor(key)
                objects[key] = store.put(value)
                persistence.sync_file(store.verify(objects[key]))
                del value
        if objects:
            sync_directory(store.root)
        if (file_digest(metadata_path) != before_metadata or
                file_digest(target) != legacy["tensors_sha256"]):
            raise ValueError("Legacy cache changed during conversion; nothing was retired.")
        converted = dict(legacy)
        converted.pop("tensors", None)
        converted.update({"format": FORMAT,
                          "metadata": self.relative(converted_path(metadata_path)),
                          "tensor_objects": objects, "tensors_sha256": objects_digest(objects),
                          "legacy_conversion": {"version": 1, "source": descriptor(legacy),
                                                "source_metadata_sha256": before_metadata,
                                                "delete_after_success": True}})
        self.verify_converted(converted, legacy)
        self.atomic_json(converted_path(metadata_path), converted)
        self.resolve(legacy)
        return {**report, "status": "converted", "metadata": converted["metadata"],
                "unique_objects": len({item["tensor_sha256"] for item in objects.values()}),
                "legacy_present": True}

    def legacy_manifests(self):
        # Restrict discovery to cache namespaces; never recurse through videos,
        # checkpoints, model weights or arbitrary user directories.
        shared = self.root / "h3_reference_cache"
        runs = self.root / "h3_chains"
        parents = []
        if shared.is_dir():
            parents.extend(path for path in shared.iterdir() if path.is_dir() and path.name != "objects")
        if runs.is_dir():
            parents.extend(path / "reference_cache" for path in runs.iterdir()
                           if path.is_dir() and (path / "reference_cache").is_dir())
        for parent in parents:
            for path in parent.iterdir():
                if re.fullmatch(r"scene_\d+\.[^.]+\.json", path.name):
                    yield self.absolute(path)

    def retire_after_success(self, converted, saved_metadata):
        """Called only by the execution-scoped receipt tracker after save commit."""
        conversion = converted.get("legacy_conversion") or {}
        if conversion.get("version") != 1 or conversion.get("delete_after_success") is not True:
            return None
        legacy_descriptor = conversion["source"]
        original = self.absolute(legacy_descriptor["metadata"])
        with self.lock(original, blocking=False):
            legacy = self.read(original)
            target = self._legacy_target(original, legacy)
            if not target.exists():
                return None
            if descriptor(legacy) != legacy_descriptor:
                raise ValueError("Legacy reference-cache address changed; retaining its bundle.")
            current = self.resolve(legacy)
            if current is None or current["tensors_sha256"] != converted["tensors_sha256"]:
                raise ValueError("Converted cache changed since use; retaining the legacy bundle.")
            from safetensors import safe_open
            with safe_open(str(target), framework="pt", device="cpu") as handle:
                if set(handle.keys()) != set(current["tensor_objects"]):
                    raise ValueError("Conversion omitted legacy tensors; retaining the bundle.")
                for key in handle.keys():
                    if tensor_digest(handle.get_tensor(key)) != current["tensor_objects"][key]["tensor_sha256"]:
                        raise ValueError("Converted tensor differs from the legacy bundle.")
            # Verify the converted content, one tensor at a time, before the
            # irreversible step. A file hash alone cannot prove its tensor ID.
            store = self.store(current)
            for record in current["tensor_objects"].values():
                store.load(record)
            saved_path = self.absolute(saved_metadata)
            saved = self.read(saved_path)
            if saved.get("format") not in ("h3_chain_segment_v3", "h3_chain_upscale_segment_v1"):
                raise ValueError("Reference-cache retirement requires a committed H3 render.")
            segment = saved.get("segment") or {}
            for field in ("segment", "checkpoint"):
                artifact = self.absolute(segment[field])
                if file_digest(artifact) != segment[field + "_sha256"]:
                    raise ValueError("Saved render failed verification; retaining the legacy bundle.")
            # All checkpoint descriptors use a cache JSON address. Audit any
            # other cache JSON pointing to this same pathname before unlinking.
            # Other hard links are distinct paths and remain intact.
            for path in self.legacy_manifests():
                metadata = self.read(path)
                if metadata.get("format") in LEGACY_FORMATS and self.absolute(metadata["tensors"]) == target:
                    if self.resolve(metadata) is None:
                        raise ValueError("Another scene still requires this legacy bundle.")
            if (file_digest(target) != legacy["tensors_sha256"] or
                    file_digest(original) != current["legacy_conversion"]["source_metadata_sha256"]):
                raise ValueError("Legacy bundle changed; refusing retirement.")
            receipt = {"format": "h3_reference_cache_retirement_v1",
                       "legacy": legacy_descriptor, "converted": descriptor(current),
                       "saved_metadata": self.relative(saved_path),
                       "saved_metadata_sha256": file_digest(saved_path),
                       "retired_bytes": target.stat().st_size}
            # Durable success evidence precedes unlink, so a crash is either a
            # harmless retained bundle or a complete, readable conversion.
            self.atomic_json(original.with_suffix(".retired.json"), receipt)
            target.unlink()
            try:
                sync_directory(target.parent)
            except OSError as exc:
                # The deletion did happen. Do not report that the old bundle
                # was retained if a filesystem rejects only the final sync.
                receipt["directory_sync_error"] = str(exc)
            return receipt
