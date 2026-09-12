"""Copy-only transactional handoffs, with branch and maintenance fences.

No queue submission occurs here. Old imported handoffs without a storage pin
remain readable/cancellable but cannot be claimed for automatic continuation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
else:
    import storage_state as state


class HandoffControlDocuments:
    def __init__(self, store, *, base=None, after_stage=None):
        if not isinstance(store, state.ControlStore):
            raise TypeError('Handoff controls require an explicit rehearsal store.')
        if base is not None and (not isinstance(base, state.Snapshot) or base.project != store.project):
            raise ValueError('Handoff controls require this project\'s pinned root.')
        self.store, self.project, self.base = store, store.project, base
        self.after_stage = after_stage
        self._operation = ContextVar('h3_handoff_controls', default=None)

    @contextmanager
    def operation(self, *, write=False):
        if self._operation.get() is not None:
            yield
            return
        current = self.store.snapshot()
        base = self._snapshot_for_operation(current, write=write)
        if base is not current:
            self.store.validate_snapshot(base, current=current)
        session = {'base': base, 'changes': {}, 'reads': {'jobs:handoffs'}, 'error': None}
        token = self._operation.set(session)
        try:
            yield
            if session['changes']:
                self._committed(self._commit(session))
            # Each read verifies its own immutable bytes. Storage corruption
            # raises ValueError outside the legacy per-record error tolerance;
            # no need to reopen thousands of unrelated PNG descriptors here.
            if session['error'] is not None:
                raise session['error']
        finally:
            self._operation.reset(token)

    def _snapshot_for_operation(self, current, *, write):
        return self.base or current

    def _commit(self, session):
        return self.store.commit(session['base'], session['changes'], operation_id=uuid.uuid4().hex,
                                 read_scopes=session['reads'], after_stage=self.after_stage)

    def _committed(self, receipt):
        """Runtime ports may forward the exact successfully published result."""

    def _session(self):
        value = self._operation.get()
        if value is None:
            raise ValueError('Handoff controls require a pinned operation.')
        return value

    def directory(self, run):
        self._session()
        if run != self.project.name:
            raise ValueError('Handoff controls belong to a different project.')
        return str(self.project/'orchestration')

    @staticmethod
    def contract(address):
        if not re.fullmatch(r'orchestration/[A-Za-z0-9._-]{1,128}\.json', address):
            raise ValueError('Unsupported handoff control identity.')
        return 'jobs:handoffs', 'jobs', False

    def _address(self, path):
        address = Path(path).relative_to(self.project).as_posix()
        contract = self.contract(address)
        previous = self._session()['base']._validated_root()['documents'].get(address)
        if previous and tuple(previous[key] for key in ('scope', 'category', 'immutable')) != contract:
            raise ValueError('Imported handoff has an unsupported ownership contract; re-import required.')
        return address

    def exists(self, path):
        address = self._address(path)
        session = self._session()
        return address in session['changes'] or address in session['base']._validated_root()['documents']

    def read(self, path):
        address = self._address(path)
        session = self._session()
        if address in session['changes']:
            raw = session['changes'][address]['data']
        else:
            try:
                raw = session['base'].read(address)
            except KeyError:
                raise FileNotFoundError('Missing accepted handoff: '+address) from None
        return state._decode(raw)

    def write(self, path, value):
        address = self._address(path)
        scope, category, immutable = self.contract(address)
        self._session()['changes'][address] = {'data': state._encode(value), 'scope': scope,
                                             'category': category, 'immutable': immutable}

    def names(self, directory):
        if Path(directory) != self.project/'orchestration':
            raise ValueError('Unsupported handoff directory.')
        session = self._session()
        return sorted(address[len('orchestration/'):] for address in
                      set(session['base']._validated_root()['documents']) | set(session['changes'])
                      if address.startswith('orchestration/') and '/' not in address[len('orchestration/'):])

    def pin_record(self, record):
        session = self._session()
        selected = record.get('working_branch_id', 'main')
        scope = 'branch:'+selected
        root = session['base']._validated_root()
        revision = root['scope_revisions'].get(scope)
        if revision is None:
            raise ValueError('Cannot create a handoff without a saved source branch.')
        session['reads'].add(scope)
        result = copy.deepcopy(record)
        result['storage_dependency'] = {'epoch': root['epoch'], 'branch': selected, 'revision': revision}
        return result

    def claim_dependencies(self, record):
        selected = record.get('working_branch_id', 'main')
        pin = record.get('storage_dependency')
        if (not isinstance(pin, dict) or set(pin) != {'epoch', 'branch', 'revision'}
                or type(pin['epoch']) is not int or pin['branch'] != selected
                or not re.fullmatch('[0-9a-f]{32}', str(pin['revision']))):
            raise state.StateConflict('Imported handoff has no valid storage pin; manual resume required.')
        root = self._session()['base']._validated_root()
        scope = 'branch:'+selected
        self._session()['reads'].add(scope)
        if root['epoch'] != pin['epoch'] or root['scope_revisions'].get(scope) != pin['revision']:
            raise state.StateConflict('Handoff source branch or storage epoch changed; manual resume required.')

    def fail_after_commit(self, error):
        """Persist the explicit exhausted-budget terminal state, then report it."""
        self._session()['error'] = error
