"""No-overwrite progress journals for explicitly scoped copy operations.

Callers validate the receipted source/target scope BEFORE calling read/create/
advance. The bootstrap can be read without acquiring or creating a lock, so
untrusted journal paths cannot create coordination files before scope checks.
Domain services retain their project locks and phase/ownership validation.
"""
from pathlib import Path
import os
import threading
import uuid

if __package__:
    from . import storage_state as state
    from .storage_commit_log import CommitLog, PROTOCOL
    from .checkpoint_manager import _RunMutationLock
else:
    import storage_state as state
    from storage_commit_log import CommitLog, PROTOCOL
    from checkpoint_manager import _RunMutationLock

_LOCKS = {}
_GUARD = threading.Lock()
_IDENTITY = ('format', 'operation_id', 'plan_sha256', 'commit_protocol', 'path_budget')


def protocol(value):
    if value not in (None, PROTOCOL):
        raise ValueError('Unsupported copy-journal commit protocol.')
    return value


def bootstrap(path):
    path = Path(path).absolute()
    raw = state._read_bytes(state.resolver.confined(path.parent, path.name))
    value = state._decode(raw)
    protocol(value.get('commit_protocol'))
    return value, raw


def _lock(path):
    path = Path(path).absolute()
    if path.name != 'journal.json' or not path.parent.is_dir():
        raise ValueError('Copy journal requires its existing dedicated directory.')
    identity = str(state.resolver.confined(path.parent, '.journal.lock'))
    with _GUARD:
        return _LOCKS.setdefault(identity, _RunMutationLock(identity))


def _log(path):
    initial, raw = bootstrap(path)
    if initial.get('commit_protocol') != PROTOCOL:
        raise ValueError('This journal does not use immutable progress records.')
    return CommitLog(Path(path).absolute().parent, raw)


def publish_once(path, value, *, path_budget=240, staging_directory=None):
    """Publish an operation-owned plan, gate or proof, never replace its bytes."""
    path = Path(path).absolute()
    state.resolver.confined(path.parent, path.name)
    boundary = path.parent
    while not boundary.exists():
        boundary = boundary.parent
    state._mkdir(path.parent, boundary)
    raw = state._encode(value)
    if staging_directory is None:
        return state._immutable(path.parent, path.name, raw, path_budget)
    state.OrganizedStorageLayout(str(path.parent), path_budget).check_atomic_json_budget(path.name)
    if len(raw) > 32*1024*1024:
        raise ValueError('Copy authority record exceeds the supported size.')
    if path.exists():
        if state._read_bytes(path) != raw:
            raise ValueError('Immutable copy authority collision; existing file retained.')
    else:
        staging = Path(staging_directory).absolute()
        state.resolver.confined(staging.parent, staging.name)
        state._mkdir(staging, staging.parent)
        temporary = state.resolver.confined(staging, uuid.uuid4().hex+'.part')
        with temporary.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        state.publish_new_file(temporary, path)
    state.sync_directory(path.parent)
    return {'path': path.name, 'sha256': state._hash(raw), 'size': len(raw)}


def create(path, value, *, path_budget=240):
    path = Path(path).absolute()
    if path.exists() or any(key in value for key in ('commit_protocol', 'path_budget')):
        raise ValueError('Use a fresh immutable journal and unambiguous metadata.')
    initial = dict(value, commit_protocol=PROTOCOL, path_budget=path_budget)
    with _lock(path):
        if path.exists():
            raise FileExistsError('Journal appeared during creation; no overwrite.')
        publish_once(path, initial, path_budget=path_budget)


def read(path):
    """Requires an already validated, operation-owned journal directory."""
    with _lock(path):
        return _log(path).read().value


def advance(path, expected, value):
    """Compare exact prior progress; duplicate publication only re-flushes."""
    with _lock(path):
        log = _log(path)
        current = log.read()
        proposed = dict(value, commit_protocol=PROTOCOL, path_budget=log.budget)
        if any(proposed.get(key) != log.initial.get(key) for key in _IDENTITY):
            raise state.StateConflict('Copy-journal identity changed; no publication.')
        if current.value == proposed:
            log.acknowledge(current)
            return proposed
        if current.value != expected:
            raise state.StateConflict('Copy-journal phase changed; reload before advancing.')
        log.append(proposed, current.witness)
        return proposed


def authority(project):
    """Raw/effective root authority, including a migration's incomplete gate.

    The caller holds the project lock and explicit control rehearsal scope.
    Do not use this maintenance boundary to bypass normal reader validation.
    """
    return state.ControlStore(project)._authority()


def advance_authority(project, expected_witness, value):
    """Publish a verified maintenance gate under the caller's project lock."""
    project = Path(project).absolute()
    state.ControlStore(project)._authority()  # enforces explicit test access
    raw = state._read_bytes(state.resolver.confined(project, 'storage.json'))
    log = CommitLog(project, raw)
    log.append(value, expected_witness)


def acknowledge_authority(project):
    project = Path(project).absolute()
    state.ControlStore(project)._authority()
    log = CommitLog(project, state._read_bytes(project/'storage.json'))
    log.acknowledge(log.read())
