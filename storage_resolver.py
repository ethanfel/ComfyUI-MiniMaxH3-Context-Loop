"""Explicit relocation bridge; persisted addresses and hash inputs never change.

No marker means V1. Unsupported, incomplete or damaged markers fail closed.
The bridge is currently restricted to copy-only rehearsals: production writers
are not migrated yet. No filesystem monkey patches or implicit migrations.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re

if __package__:
    from .artifact_paths import artifact_address, is_link_or_junction
else:
    from artifact_paths import artifact_address, is_link_or_junction

FORMAT = "h3_storage_relocation_bridge_v1"
ALIASES = "h3_storage_aliases_v1"
_REHEARSAL = ContextVar("h3_storage_rehearsal", default=None)


class StorageError(ValueError):
    pass


def confined(root, address):
    """Reject links in both legacy and physical paths, including missing leaves."""
    root = Path(os.path.abspath(root))
    path = root
    # Do not let resolve() erase evidence of a linked project/output root.
    for item in (root, *root.parents):
        if is_link_or_junction(item):
            raise StorageError("Storage paths cannot follow symlinks or junctions.")
    for part in artifact_address(address).split("/"):
        path /= part
        if is_link_or_junction(path):
            raise StorageError("Storage paths cannot follow symlinks or junctions.")
    return path


def _signature(path):
    return _stat_signature(path.stat())


def _stat_signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _json_bytes(path):
    with path.open("rb") as handle:
        raw = handle.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise StorageError("Storage record exceeds the supported size.")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise StorageError("Duplicate key in storage authority.")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise StorageError("Storage authority must be an object.")
    return value, raw


def validate_aliases(value):
    if value.get("format") != ALIASES:
        raise StorageError("Unsupported storage alias format.")
    files, directories = value.get("files"), value.get("directories")
    if not isinstance(files, dict) or not isinstance(directories, dict):
        raise StorageError("Storage aliases require file and directory maps.")
    sources, targets = {}, {}
    for kind, rows in (("file", files), ("directory", directories)):
        for source, target in rows.items():
            if source != artifact_address(source) or target != artifact_address(target):
                raise StorageError("Alias authorities require canonical portable addresses.")
            if (source.split("/")[0] in ("media", "exports", "project", "storage.json")
                    or target.split("/")[0] not in ("media", "exports", "project")
                    or len(target.split("/")) < (2 if kind == "directory" else 3)
                    or source.endswith(".lock") or target.endswith(".lock")):
                raise StorageError("Invalid relocation namespace or coordination-file alias.")
            for key, seen in ((source, sources), (target, targets)):
                folded = key.casefold()
                if folded in seen:
                    raise StorageError("Duplicate/case-colliding storage alias.")
                seen[folded] = kind
    # File entries inside a mapped directory are redundant and can disagree
    # after append/delete; require a single, invertible location authority.
    for source, target in directories.items():
        for candidate in sources:
            if candidate.startswith(source.casefold() + "/"):
                raise StorageError("Overlapping storage source mappings.")
        for candidate in targets:
            if candidate.startswith(target.casefold() + "/"):
                raise StorageError("Overlapping storage destination mappings.")
    # A file must never also be an ancestor directory of another target/source.
    for seen in (sources, targets):
        for name in seen:
            parts = name.split("/")
            if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
                raise StorageError("Nested/overlapping storage aliases.")
    return {"files": dict(files), "directories": dict(directories),
            "reverse_files": {v: k for k, v in files.items()},
            "reverse_directories": {v: k for k, v in directories.items()}}


@lru_cache(maxsize=32)
def _load_aliases(filename, signature, digest):
    path = Path(filename)
    value, raw = _json_bytes(path)
    if _signature(path) != signature or hashlib.sha256(raw).hexdigest() != digest:
        raise StorageError("Storage alias map changed or failed its SHA-256 check.")
    return validate_aliases(value)


@lru_cache(maxsize=64)
def _load_marker(filename, signature):
    path = Path(filename)
    value, _ = _json_bytes(path)
    if _signature(path) != signature:
        raise StorageError("Storage marker changed during lookup; retry.")
    return value


def storage_state(project):
    project = Path(os.path.abspath(project))
    marker = confined(project, "storage.json")
    if not marker.exists():
        return None
    value = _load_marker(str(marker), _signature(marker))
    if (value.get("format") != FORMAT or type(value.get("version")) is not int
            or value.get("version") != 1):
        raise StorageError("Unsupported H3 storage version; update the reader before opening this project.")
    if value.get("phase") != "ready":
        raise StorageError("H3 storage migration is incomplete; resume or roll back its journal before using this project.")
    if value.get("mode") != "rehearsal" or _REHEARSAL.get() != str(project):
        raise StorageError("Relocated storage is currently copy-only; production activation is not enabled.")
    name, digest = value.get("aliases"), value.get("aliases_sha256")
    if (not isinstance(name, str) or not re.fullmatch(r"project/aliases/[0-9a-f]{32}\.json", name)
            or not re.fullmatch(r"[0-9a-f]{64}", str(digest))):
        raise StorageError("Invalid H3 storage authority address.")
    path = confined(project, name)
    if not path.is_file():
        raise StorageError("Required H3 storage alias map is missing; legacy fallback is forbidden.")
    try:
        return _load_aliases(str(path), _signature(path), digest)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise StorageError("Cannot read H3 storage authority.") from exc


@contextmanager
def rehearsal_access(project):
    """Explicit tool/test scope, never entered by UI/nodes or normal startup."""
    project = Path(os.path.abspath(project))
    token = _REHEARSAL.set(str(project))
    try:
        yield
    finally:
        _REHEARSAL.reset(token)


def _translate(relative, files, directories):
    if relative in files:
        return files[relative]
    for source, target in directories.items():
        if relative == source or relative.startswith(source + "/"):
            return target + relative[len(source):]
    return relative


def _address(output, value):
    output = Path(os.path.abspath(output))
    path = Path(value)
    if path.is_absolute():
        try:
            value = path.relative_to(output).as_posix()
        except ValueError as exc:
            raise StorageError("Storage address escapes the output directory.") from exc
    return output, artifact_address(str(value))


def resolve_output(output, value):
    output, address = _address(output, value)
    path = confined(output, address)
    parts = address.split("/")
    if len(parts) < 2 or parts[0] != "h3_chains" or parts[1].startswith("."):
        return path
    project = confined(output, "/".join(parts[:2]))
    state = storage_state(project)
    if state and len(parts) > 2:
        relative = "/".join(parts[2:])
        translated = _translate(relative, state["files"], state["directories"])
        # Coordination files remain at their original logical addresses even
        # when the containing PNG directory is relocated.
        if relative.endswith(".lock"):
            translated = _translate(relative, state["reverse_files"], state["reverse_directories"])
        path = confined(project, translated)
    return path


def logical_output(output, value):
    """Reverse lookup for persistence, ownership comparisons and source hashes."""
    output, address = _address(output, value)
    confined(output, address)
    parts = address.split("/")
    if len(parts) >= 3 and parts[0] == "h3_chains" and not parts[1].startswith("."):
        state = storage_state(confined(output, "/".join(parts[:2])))
        if state:
            relative = _translate("/".join(parts[2:]), state["reverse_files"], state["reverse_directories"])
            address = "/".join(parts[:2]) + "/" + relative
    return address


def mapped_directories(output, project, pattern):
    """Supplement legacy globs when a whole export tree has moved."""
    state = storage_state(project)
    if not state:
        return []
    return [confined(project, target) for source, target in state["directories"].items()
            if Path(source).match(pattern)]


def logical_children(output, directory):
    """Logical children of retained V1 containers plus moved directory aliases."""
    if Path(os.path.abspath(directory)) == Path(os.path.abspath(output)):
        return sorted(Path(output).iterdir()) if Path(output).is_dir() else []
    address = logical_output(output, directory)
    logical = confined(output, address)
    names = {p.name for p in logical.iterdir()} if logical.is_dir() else set()
    parts = address.split("/")
    if len(parts) >= 2 and parts[0] == "h3_chains":
        state = storage_state(confined(output, "/".join(parts[:2])))
        if state:
            prefix = "/".join(parts[2:])
            names.update(Path(p).name for p in state["directories"] if Path(p).parent.as_posix() == prefix)
    return [logical / name for name in sorted(names)]
