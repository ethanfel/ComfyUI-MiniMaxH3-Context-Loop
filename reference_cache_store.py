"""Immutable, content-addressed tensor objects for H3 scene cache manifests.

No scene names, prompts, reference positions, or VAE names enter an object's
identity: dtype, shape, and exact encoded bytes do. Different recipes may share
an object only when their actual results are identical. This is disk
deduplication, not a memoization of VAE calls.
"""

import hashlib
import json
import os
import re
import shutil
import uuid


FORMAT = "h3_reference_cache_v3"


def tensor_digest(value):
    import torch

    value = value.detach().cpu().contiguous()
    digest = hashlib.sha256()
    digest.update(json.dumps([str(value.dtype), list(value.shape)],
                             separators=(",", ":")).encode("ascii"))
    # Viewing bytes also supports bfloat16, which NumPy cannot represent.
    if value.numel():
        digest.update(memoryview(value.reshape(-1).view(torch.uint8).numpy()))
    return digest.hexdigest()


def objects_digest(objects):
    if not isinstance(objects, dict):
        raise ValueError("H3 reference cache has no tensor-object manifest.")
    identity = {}
    for key, record in objects.items():
        if not isinstance(key, str) or not key or not isinstance(record, dict):
            raise ValueError("H3 reference cache has an invalid tensor object.")
        for field in ("tensor_sha256", "tensors_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(record.get(field, ""))):
                raise ValueError("H3 reference cache has an invalid object digest.")
        if not isinstance(record.get("tensors"), str) or not record["tensors"]:
            raise ValueError("H3 reference cache has no tensor-object path.")
        identity[key] = {field: record[field] for field in (
            "tensor_sha256", "tensors_sha256")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


class ReferenceTensorStore:
    def __init__(self, output_root, objects_root, file_sha256):
        self.output_root = os.path.realpath(output_root)
        self.root = os.path.realpath(objects_root)
        self.file_sha256 = file_sha256
        if os.path.commonpath((self.output_root, self.root)) != self.output_root:
            raise ValueError("H3 reference objects escape the output directory.")

    def _path(self, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("H3 reference object has an invalid content digest.")
        path = os.path.join(self.root, digest + ".safetensors")
        # Do not follow individual object symlinks outside this store.
        if os.path.realpath(path) != path:
            raise ValueError("H3 reference object path must not be a symlink.")
        return path

    def _record(self, path, digest):
        return {"tensor_sha256": digest,
                "tensors": os.path.relpath(path, self.output_root),
                "tensors_sha256": self.file_sha256(path)}

    def verify(self, record):
        objects_digest({"object": record})
        expected = self._path(record["tensor_sha256"])
        actual = os.path.realpath(os.path.join(self.output_root, record["tensors"]))
        if actual != expected:
            raise ValueError("H3 reference tensor object is outside its cache store.")
        if (not os.path.isfile(expected) or
                self.file_sha256(expected) != record["tensors_sha256"]):
            raise ValueError("H3 reference tensor object failed SHA-256 integrity checks.")
        return expected

    def load(self, record):
        from safetensors.torch import load_file

        tensors = load_file(self.verify(record))
        if set(tensors) != {"data"} or tensor_digest(tensors["data"]) != record["tensor_sha256"]:
            raise ValueError("H3 reference tensor object content failed integrity checks.")
        return tensors["data"]

    def put(self, value):
        from safetensors.torch import load_file, save_file

        value = value.detach().cpu().contiguous()
        digest = tensor_digest(value)
        path = self._path(digest)
        if os.path.isfile(path):
            # A corrupt object is never silently reused, deleted or overwritten:
            # other scene manifests may depend on it.
            tensors = load_file(path)
            if set(tensors) != {"data"} or tensor_digest(tensors["data"]) != digest:
                raise ValueError("Existing H3 reference object failed integrity checks.")
            return self._record(path, digest)
        os.makedirs(self.root, exist_ok=True)
        temporary = path + "." + uuid.uuid4().hex + ".tmp"
        try:
            save_file({"data": value}, temporary)
            # Competing writers publish the same content at this address. No
            # reader observes a partial file; manifests are published last.
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self._record(path, digest)

    def adopt(self, source_store, record):
        source = source_store.verify(record)
        target = self._path(record["tensor_sha256"])
        local = {**record, "tensors": os.path.relpath(target, self.output_root)}
        if os.path.isfile(target):
            self.verify(local)
            return local
        os.makedirs(self.root, exist_ok=True)
        temporary = target + "." + uuid.uuid4().hex + ".tmp"
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copy2(source, temporary)
            if self.file_sha256(temporary) != record["tensors_sha256"]:
                raise ValueError("Run-local H3 reference object failed integrity checks.")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return local
