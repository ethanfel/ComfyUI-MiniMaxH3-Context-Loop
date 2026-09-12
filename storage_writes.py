"""Organized payload reservations and whole-operation migration fencing.

Only explicitly enabled copy rehearsals may allocate relocated destinations.
The alias pointer is committed before any payload writer receives a path. An
interruption can leave an unused reservation, never a saved take with no address.
Legacy control documents keep their existing schemas and commit protocols.
"""

from contextlib import contextmanager
from functools import wraps
import hashlib
import inspect
import json
from pathlib import Path
import uuid

if __package__:
    from .storage_resolver import (ALIASES, StorageError, confined, storage_state,
                                   resolve_output, logical_output, validate_aliases, _json_bytes)
    from .processing_persistence import atomic_json
else:
    from storage_resolver import (ALIASES, StorageError, confined, storage_state,
                                  resolve_output, logical_output, validate_aliases, _json_bytes)
    from processing_persistence import atomic_json

POLICY = "organized_payloads_v1"


def _identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()[:32]


def _project(output, value):
    address = logical_output(output, value)
    parts = address.split("/")
    if len(parts) < 3 or parts[0] != "h3_chains" or parts[1].startswith("."):
        return None, None
    return confined(output, "/".join(parts[:2])), "/".join(parts[2:])


def enabled(project):
    state = storage_state(project)
    if state is None:
        return False
    marker, _ = _json_bytes(confined(project, "storage.json"))
    policy = marker.get("writer_policy")
    if policy not in (None, POLICY):
        raise StorageError("Unsupported organized writer policy.")
    return policy == POLICY


@contextmanager
def writer_scope(output, run):
    # Same cached re-entrant lock as checkpoint commits and migration, including
    # the encoding/staging phase that used to precede the pointer-only lock.
    if __package__:
        from .checkpoint_manager import checkpoint_run_lock
    else:
        from checkpoint_manager import checkpoint_run_lock
    with checkpoint_run_lock(str(output), run):
        yield


def project_writer(function=None, *, domain=None):
    """Fence a synchronous H3 writer from its first I/O to its final commit."""
    if function is None:
        return lambda wrapped: project_writer(wrapped, domain=domain)
    signature = inspect.signature(function)

    def run(value):
        if not isinstance(value, dict):
            return None
        if value.get("run_name"):
            return value["run_name"]
        for name in ("plan", "state", "source_manifest"):
            found = run(value.get(name))
            if found:
                return found
        return None

    @wraps(function)
    def wrapped(*args, **kwargs):
        values = signature.bind(*args, **kwargs).arguments
        selected = next((name for item in values.values() if (name := run(item))), None)
        if selected is None and isinstance(values.get("run_name"), str):
            selected = values["run_name"]
        if selected is None:
            return function(*args, **kwargs)
        namespace = function.__globals__
        chain = namespace.get("chain") or values.get("chain")
        output = chain._output_root() if chain is not None else namespace["_output_root"]()
        if __package__:
            from .storage_runtime import current_runtime
        else:
            from storage_runtime import current_runtime
        runtime = current_runtime(output, selected)
        if runtime is not None:
            if domain not in ('generation', 'processing', 'exports'):
                raise ValueError('This writer is not ported to combined storage.')
            getattr(runtime, domain).require_write()
            return function(*args, **kwargs)
        with writer_scope(output, selected):
            return function(*args, **kwargs)
    return wrapped


def store_writer(method):
    """Migration fence for non-node history and orchestration mutations."""
    signature = inspect.signature(method)
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        values = signature.bind(self, *args, **kwargs).arguments
        output = getattr(self, "output_root", None) or getattr(self, "_root", None)
        validate = method.__globals__.get("_validate_run_name") or method.__globals__.get("_strict_run_name")
        run = validate(values["run_name"]) if validate else values["run_name"]
        with writer_scope(output, run):
            return method(self, *args, **kwargs)
    return wrapped


def _reserve(output, project, files=None, directories=None):
    """Publish one complete immutable map, then atomically switch its pointer.

    No files are moved or guessed here. Migration must cover already occupied
    logical destinations before an organized writer can take ownership of them.
    """
    with writer_scope(output, project.name):
        if not enabled(project):
            return
        state = storage_state(project)
        marker, _ = _json_bytes(confined(project, "storage.json"))
        aliases = {"format": ALIASES, "files": dict(state["files"]),
                   "directories": dict(state["directories"])}
        changed = False
        for kind, rows in (("files", files or {}), ("directories", directories or {})):
            for source, target in rows.items():
                logical = confined(project, source)
                existing = resolve_output(output, logical)
                destination = confined(project, target)
                if existing != logical:
                    # Existing migrated assignments own their physical layout;
                    # future writers must not rename or reinterpret them.
                    continue
                if kind == "directories" and logical.is_dir():
                    # Do not hide dangling links/junctions or unknown children
                    # merely because is_file() would say they are not files.
                    for child in logical.rglob("*"):
                        confined(project, child.relative_to(project).as_posix())
                if (kind == "files" and logical.exists()) or (
                        kind == "directories" and logical.exists() and
                        (not logical.is_dir() or any(p.is_file() and not p.name.endswith(".lock")
                                                    for p in logical.rglob("*")))):
                    raise StorageError("Unmigrated files occupy the requested destination: " + source)
                if destination.exists():
                    raise StorageError("Unowned organized destination is occupied: " + target)
                aliases[kind][source] = target
                changed = True
        if not changed:
            return
        validate_aliases(aliases)
        # Use the actual active root, not a representative shorter prefix.
        if __package__:
            from .storage_layout import OrganizedStorageLayout
        else:
            from storage_layout import OrganizedStorageLayout
        layout = OrganizedStorageLayout(str(project), marker.get("path_budget", 240))
        for target in list((files or {}).values()) + list((directories or {}).values()):
            layout.check_budget(target)
            if target.endswith(".json"):
                layout.check_atomic_json_budget(target)
        token = uuid.uuid4().hex
        address = "project/aliases/" + token + ".json"
        authority = confined(project, address)
        layout.check_atomic_json_budget(address)
        if authority.exists():
            raise StorageError("Alias authority reservation collision.")
        atomic_json(authority, aliases)
        # Do not clean either authority on an uncertain rename/fsync result.
        # Only this small pointer makes the new reservation visible.
        marker.update(aliases=address, aliases_sha256=hashlib.sha256(authority.read_bytes()).hexdigest(),
                      writer_generation=int(marker.get("writer_generation", 0)) + 1)
        atomic_json(confined(project, "storage.json"), marker)


def reserve_take(output, paths, *, stage, identity, pass_identity=None):
    """Route owned immutable payloads; leave canonical/revision JSON untouched."""
    if not paths:
        return paths
    project, _ = _project(output, next(iter(paths.values())))
    if project is None or not enabled(project):
        return paths
    if __package__:
        from .storage_layout import OrganizedStorageLayout
    else:
        from storage_layout import OrganizedStorageLayout
    storage_id = _identity("take", identity)
    pass_id = _identity("pass", pass_identity) if pass_identity is not None else None
    layout = OrganizedStorageLayout(str(project))
    roles = layout.media(stage, storage_id, pass_id=pass_id)
    roles["prompt"] = layout.take_prompt(storage_id)
    mappings = {}
    for role, path in paths.items():
        owner, address = _project(output, path)
        if owner != project or role not in roles:
            raise StorageError("A saved take reservation must use known roles in one project.")
        mappings[address] = roles[role]
    _reserve(output, project, files=mappings)
    return {key: str(resolve_output(output, path)) for key, path in paths.items()}


def reserve_directory(output, directory, purpose):
    project, address = _project(output, directory)
    if project is None or not enabled(project):
        return str(resolve_output(output, directory))
    physical = resolve_output(output, directory)
    if physical != confined(project, address):
        return str(physical)
    allowed = {"png": "exports/png", "video": "exports/video",
               "recovery": "project/recovery", "previews": "project/optional/previews",
               "thumbnails": "project/optional/thumbnails", "assets": "project/assets",
               "reference_cache": "project/reference_cache"}
    if purpose not in allowed:
        raise StorageError("Unknown organized directory purpose.")
    target = allowed[purpose] + "/" + _identity("directory", address)
    _reserve(output, project, directories={address: target})
    return str(resolve_output(output, directory))


def reserve_export(output, paths, *, identity):
    project, _ = _project(output, next(iter(paths.values())))
    if project is None or not enabled(project):
        return paths
    names = {"video": "video.mp4", "audio": "audio.wav", "subtitles": "subtitles.srt",
             "metadata": "video.json"}
    prefix = "exports/video/" + _identity("export", identity)
    files = {}
    for role, path in paths.items():
        owner, address = _project(output, path)
        if owner != project or role not in names:
            raise StorageError("Export roles must belong to one project.")
        files[address] = prefix + "/" + names[role]
    _reserve(output, project, files=files)
    return {key: str(resolve_output(output, path)) for key, path in paths.items()}
