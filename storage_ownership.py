"""External, no-overwrite ownership authority for copied runtime projects.

The fence must outlive deletion of generated output. It therefore does not live
in a project snapshot. Every operation holds the existing project lock, reads
the latest ownership head, and preserves the imported legacy record unchanged.
"""
import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import re
import threading

if __package__:
    from . import storage_state as state
    from .storage_commit_log import CommitLog, PROTOCOL
    from .checkpoint_manager import _raw_checkpoint_run_lock
else:
    import storage_state as state
    from storage_commit_log import CommitLog, PROTOCOL
    from checkpoint_manager import _raw_checkpoint_run_lock

FORMAT = 'h3_external_ownership_rehearsal_v1'
_HELD = {}  # Protected by the run lock; reject cross-task RLock re-entry.


def _execution_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def authority_directory(output, run):
    return state.resolver.confined(Path(output)/'h3_chains',
        '.project_ownership/'+state._hash(str(run).encode())[:32])


def read_authority(directory, run, legacy_witness):
    """Read an existing head; caller holds the project's ownership/run locks."""
    raw = state._read_bytes(state.resolver.confined(directory, 'storage.json'))
    log = CommitLog(directory, raw)
    head = log.read()
    value = head.value
    keys = {'format', 'version', 'commit_protocol', 'path_budget', 'run_name', 'legacy', 'record'}
    if (set(value) != keys or set(log.initial) != keys
            or value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1
            or value['run_name'] != run or value['legacy'] != legacy_witness
            or any(value[k] != log.initial[k] for k in value if k != 'record')):
        raise ValueError('Invalid or changed external ownership authority; no fallback.')
    return {'value': value, 'log': log, 'head': head}


class OwnershipLog:
    def __init__(self, runtime):
        self.runtime = runtime
        self.directory = authority_directory(runtime.output, runtime.run)
        self.legacy = state.resolver.confined(runtime.output/'h3_chains',
                                            '.project_ownership/'+runtime.run+'.json')
        self._operation = ContextVar('h3_ownership_log_operation', default=None)

    def _legacy(self):
        if not self.legacy.exists():
            return None, None
        raw = state._read_bytes(self.legacy)
        return state._decode(raw), {'sha256': state._hash(raw), 'size': len(raw)}

    def _load(self):
        legacy, witness = self._legacy()
        path = state.resolver.confined(self.directory, 'storage.json')
        if not path.exists():
            if self.directory.exists():
                for child in self.directory.iterdir():
                    state.resolver.confined(self.directory, child.name)
                    if not re.fullmatch(r'\.tmp-[0-9a-f]{32}', child.name) or not child.is_file():
                        raise ValueError('Missing external ownership bootstrap; no legacy fallback.')
            budget = self.runtime.store._marker()[0]['path_budget']
            value = {'format': FORMAT, 'version': 1, 'commit_protocol': PROTOCOL,
                     'path_budget': budget, 'run_name': self.runtime.run,
                     'legacy': witness, 'record': legacy}
            return {'value': value, 'log': None, 'head': None}
        return read_authority(self.directory, self.runtime.run, witness)

    @contextmanager
    def lock(self):
        self.runtime.check()
        with _raw_checkpoint_run_lock(str(self.runtime.output), self.runtime.run):
            self.runtime.check()
            key = (str(self.runtime.output), self.runtime.run)
            owner = _execution_owner()
            previous = _HELD.get(key)
            if previous is not None and previous != owner:
                raise state.StateConflict('An ownership guard cannot be shared across async tasks.')
            existing = self._operation.get()
            if existing is not None and existing['owner'] == owner:
                yield
                return
            session = self._load()
            session['owner'] = owner
            _HELD[key] = owner
            token = self._operation.set(session)
            try:
                yield
            finally:
                self._operation.reset(token)
                if previous is None:
                    _HELD.pop(key)

    def _session(self):
        self.runtime.check()
        session = self._operation.get()
        if session is None or session['owner'] != _execution_owner():
            raise ValueError('Ownership I/O requires its shared project lock.')
        return session

    def read(self):
        record = self._session()['value']['record']
        if record is None:
            return None
        if __package__:
            from .project_ownership import _normalize_record
        else:
            from project_ownership import _normalize_record
        return _normalize_record(copy.deepcopy(record), self.runtime.run)

    def write(self, record):
        session = self._session()
        if not self.runtime.ownership_writes:
            raise ValueError('Runtime binding does not enable ownership changes.')
        # Normalize/validate through the domain before publishing, not afterward.
        if __package__:
            from .project_ownership import _normalize_record
        else:
            from project_ownership import _normalize_record
        _normalize_record(record, self.runtime.run)
        current = self._load()
        if current['value'] != session['value']:
            raise state.StateConflict('Ownership changed outside its shared project lock.')
        if session['log'] is None:
            raw = state._encode(session['value'])
            budget = session['value']['path_budget']
            layout = state.OrganizedStorageLayout(str(self.directory), budget)
            layout.check_atomic_json_budget('storage.json')
            layout.check_atomic_json_budget(CommitLog._address(1, ack=True))
            state._mkdir(self.directory, self.runtime.output/'h3_chains')
            state._immutable(self.directory, 'storage.json', raw, budget)
            session['log'] = CommitLog(self.directory, raw)
            session['head'] = session['log'].read()
        log = session['log']
        value = dict(session['value'], record=copy.deepcopy(record))
        if value == session['value']:
            log.acknowledge(session['head'])
        else:
            log.append(value, session['head'].witness)
        session.update(value=value, head=log.read())
