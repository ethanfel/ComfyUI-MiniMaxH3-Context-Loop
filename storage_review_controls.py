"""Pinned, durable Review inventory on explicitly hosted storage copies.

Review records use the host's orchestration (handoff) grant, never a branch or
media grant. Pending snapshots and decision receipts are immutable. A decision
projects the existing public snapshot schema without rewriting an imported
archive. The same projection is readable after recovery to an ordinary tree.
No live executor future, prompt, tensor or ownership proof is stored here.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import hashlib
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_resolver import confined
else:
    import storage_state as state
    from storage_resolver import confined


class RuntimeReviewDocuments:
    def __init__(self, runtime):
        self.runtime = runtime
        self.project = runtime.project
        self._operation = ContextVar('h3_review_inventory_operation', default=None)

    def working_root(self, directory):
        self.runtime.check()
        directory = Path(directory)
        if directory == self.project:
            branch = 'main'
        elif directory.parent == self.project/'branches' and re.fullmatch('[0-9a-f]{32}', directory.name):
            branch = directory.name
        else:
            raise ValueError('Review inventory belongs to a different project or branch directory.')
        prefix = '' if branch == 'main' else 'branches/'+branch+'/'
        confined(self.project, prefix+'orchestration/review_probe.json')
        return branch, prefix

    @contextmanager
    def operation(self, directory, *, write=False):
        if self._operation.get() is not None:
            raise ValueError('Review inventory cannot nest or switch operations.')
        branch, prefix = self.working_root(directory)
        if write:
            self._require_write(branch)
        base = self.runtime.accepted if write else self.runtime.base
        current = self.runtime.check()
        self.runtime.store.validate_snapshot(base, current=current)
        documents = base.state['documents']
        if branch != 'main' and prefix+'branch.json' not in documents:
            raise ValueError('Review inventory branch is unavailable at the input pin.')
        session = dict(base=base, branch=branch, prefix=prefix, documents=documents,
                       changes={}, reads=set(), write=write)
        token = self._operation.set(session)
        try:
            yield self
            if session['changes']:
                self._require_write(branch)
                if __package__:
                    from .project_ownership import project_write_guard
                else:
                    from project_ownership import project_write_guard
                # Direct host maintenance is separately authorized. Nodes must
                # use only their captured caller proof, rechecked at publication.
                with self._guard(project_write_guard):
                    receipt = self.runtime.store.commit(base, session['changes'],
                        operation_id=uuid.uuid4().hex, read_scopes=session['reads'])
                    self.runtime.record_commit(receipt)
            self.runtime.check()
        finally:
            self._operation.reset(token)

    @contextmanager
    def _guard(self, guard):
        if self.runtime.has_node_proof:
            with guard(self.runtime.output, self.runtime.run,
                       self.runtime.node_write_proof, 'save scene review inventory'):
                yield
        else:
            yield

    def _require_write(self, branch):
        self.runtime.handoffs._require_write()
        if branch != self.runtime.selected:
            raise ValueError('Review write belongs to a different runtime branch.')

    def _session(self):
        self.runtime.check()
        session = self._operation.get()
        if session is None:
            raise ValueError('Review inventory requires a pinned operation.')
        return session

    def _address(self, path):
        session = self._session()
        address = Path(path).relative_to(self.project).as_posix()
        state._logical(address)
        prefix = session['prefix']+'orchestration/'
        if not address.startswith(prefix):
            raise ValueError('Review inventory path belongs to a different branch.')
        tail = address[len(prefix):]
        if not (re.fullmatch(r'[A-Za-z0-9._-]{1,140}\.json', tail) or
                re.fullmatch(r'review_decisions/[0-9a-f]{64}\.json', tail)):
            raise ValueError('Invalid review inventory address.')
        confined(self.project, address)
        return address

    def read(self, path):
        address = self._address(path)
        session = self._session()
        if address in session['changes']:
            return state._decode(session['changes'][address]['data'])
        descriptor = session['documents'].get(address)
        if descriptor is None:
            raise FileNotFoundError('Missing accepted review inventory: '+address)
        session['reads'].add(descriptor['scope'])
        # Integrity failures propagate. Never mask a damaged accepted blob as
        # an absent review or search old physical folders for a replacement.
        try:
            return state._decode(session['base'].read(address))
        except FileNotFoundError as exc:
            # A missing accepted blob is corruption, unlike an address absent
            # from the pinned catalogue. In particular a lost decision must
            # never make an already-decided review look pending again.
            raise state.StateConflict('Accepted review inventory bytes are missing: '+address) from exc

    def create(self, path, value):
        session = self._session()
        if not session['write']:
            raise ValueError('Review inventory operation is read-only.')
        self._require_write(session['branch'])
        address = self._address(path)
        if value.get('run_name') != self.runtime.run or value.get('_branch_id', 'main') != session['branch']:
            raise ValueError('Review identity does not match its project and branch.')
        if value.get('format') not in ('h3_review_snapshot_v1', 'h3_review_decision_v1'):
            raise ValueError('Unsupported review inventory record.')
        tail = address[len(session['prefix']+'orchestration/'):]
        pattern = (r'review_[A-Za-z0-9._-]{1,128}\.json' if value['format'] == 'h3_review_snapshot_v1'
                   else r'review_decisions/[0-9a-f]{64}\.json')
        if not re.fullmatch(pattern, tail):
            raise ValueError('Review record format does not match its address.')
        raw = state._encode(value)
        if address in session['documents'] or address in session['changes']:
            previous = self.read(path)
            if state._encode(previous) != raw:
                raise state.StateConflict('Review identity already contains a different saved record.')
            return
        # Independent gates may legitimately start from the same saved scene
        # pin. A per-review scope prevents another gate's append-only inventory
        # from making that pin unusable. Competing decisions for THIS token
        # still conflict, including when their pending archive was imported.
        identity = session['branch']+':'+str(value.get('token') or '')
        scope = 'jobs:review_'+hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]
        session['changes'][address] = dict(data=raw, scope=scope, category='jobs', immutable=True)

    def names(self, directory):
        session = self._session()
        prefix = session['prefix']+'orchestration/'
        if Path(directory) != self.project/prefix:
            raise ValueError('Invalid review inventory directory.')
        return sorted(address[len(prefix):] for address in
                      set(session['documents']) | set(session['changes'])
                      if address.startswith(prefix) and '/' not in address[len(prefix):])
