"""Explicit, copy-only control-document port for WorkingBranches.

Logical Paths here are identities, never writable blob filenames. Each outer
branch operation pins one root, stages all writes, then publishes once. No UI,
route or normal node constructs this port; payload operations remain disabled.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from fnmatch import fnmatchcase
from pathlib import Path
import re
import uuid

if __package__:
    from .storage_state import ControlStore, Snapshot, StateConflict, _encode, _decode, _lock
    from .processing_persistence import sync_directory
else:
    from storage_state import ControlStore, Snapshot, StateConflict, _encode, _decode, _lock
    from processing_persistence import sync_directory


class BranchControlDocuments:
    def __init__(self, store, *, base=None, after_stage=None):
        if not isinstance(store, ControlStore):
            raise TypeError('Branch controls require an explicit rehearsal ControlStore.')
        if base is not None and (not isinstance(base, Snapshot) or base.project != store.project):
            raise ValueError('Branch controls require this project\'s pinned base.')
        self.store, self.project = store, store.project
        self.base, self.after_stage = base, after_stage
        self._operation = ContextVar('h3_branch_control_operation', default=None)

    @contextmanager
    def operation(self):
        if self._operation.get() is not None:
            yield
            return
        # Always check current format/access, even for a queued historical base.
        current = self.store.snapshot()
        base = self.base or current
        if self.base is not None:
            self.store.validate_snapshot(base, current=current)
        session = {'base': base, 'documents': base.state['documents'],
                   'changes': {}, 'reads': set(), 'retire_pointers': set()}
        token = self._operation.set(session)
        try:
            yield
            if session['changes'] or session['retire_pointers']:
                # A domain retry may generate fresh revision/creation IDs after
                # a pre-publication crash. Storage attempt IDs are separate from
                # durable branch save/create receipts, just as in the V1 API.
                self._committed(self._commit(session))
        finally:
            self._operation.reset(token)

    def _commit(self, session):
        return self.store.commit(session['base'], session['changes'], operation_id=uuid.uuid4().hex,
                                 read_scopes=session['reads'], after_stage=self.after_stage,
                                 **({'retire_pointers': sorted(session['retire_pointers'])}
                                    if session['retire_pointers'] else {}))

    def _committed(self, receipt):
        """Runtime ports can forward a successfully accepted commit result."""

    def retry_save(self, path, operation_id, request_hash):
        """Recover only an exact accepted save, without rebasing queued edits.

        Domain save IDs already live in branch records. Keep that format and
        locate the storage receipt that introduced its current version. A later
        assignment/branch mutation invalidates retry, even if the branch record
        itself still contains the old save ID. Other branches may have advanced.
        """
        session = self._session()
        if not operation_id or self.base is None or session['changes'] or session['retire_pointers']:
            return None
        address = self._address(path)
        if not re.fullmatch(r'branches/(?:main\.json|[0-9a-f]{32}/branch\.json)', address):
            raise ValueError('Save retry requires an exact branch record.')
        _, contract = self._watch(address)
        scope = contract[0]
        with _lock(self.project):
            current = self.store.snapshot()
            self.store.validate_snapshot(session['base'], current=current)
            base_root, root = session['base'].state, current.state
            if root['epoch'] != base_root['epoch']:
                raise StateConflict('Storage epoch changed before save retry.')
            if current.reference == session['base'].reference or address not in root['documents']:
                return None
            descriptor = root['documents'][address]
            if tuple(descriptor[key] for key in ('scope', 'category', 'immutable')) != contract:
                raise ValueError('Save retry branch contract mismatch.')
            record = _decode(current.read(address))
            if (not isinstance(record, dict) or record.get('format') != 'h3_working_branch_v1'
                    or record.get('run_name') != self.project.name or record.get('id') != scope.split(':', 1)[1]):
                raise ValueError('Invalid saved H3 working branch during retry.')
            retry = record.get('last_save_operation', {})
            if retry.get('id') != operation_id:
                return None
            if retry.get('hash') != request_hash:
                raise StateConflict('Branch operation id was reused with different settings.')
            if base_root['documents'].get(address) == descriptor:
                if root['scope_revisions'].get(scope) != base_root['scope_revisions'].get(scope):
                    raise StateConflict('Branch changed after saved operation; reload before retry.')
                return None  # This input already contains the acknowledged save.
            cursor, cursor_root = current, root
            while cursor_root['parent'] is not None:
                parent = Snapshot(self.project, cursor_root['parent'])
                older = parent.state
                if (older['generation'] != cursor_root['generation']-1 or
                        older['epoch'] not in (cursor_root['epoch'], cursor_root['epoch']-1)):
                    raise StateConflict('Save retry ancestry is inconsistent.')
                if older['documents'].get(address) != descriptor:
                    break
                cursor, cursor_root = parent, older
            if (cursor_root['epoch'] != root['epoch'] or
                    cursor_root['scope_revisions'].get(scope) != root['scope_revisions'].get(scope)):
                raise StateConflict('Branch changed after saved operation; reload before retry.')
            self.store.validate_snapshot(session['base'], current=cursor)
            receipts = [item for item in cursor_root['operations'].values()
                        if item['generation'] == cursor_root['generation']]
            if len(receipts) != 1:
                raise StateConflict('Save retry has no unique accepted storage receipt.')
            receipt = receipts[0]
            if self.store.committed_snapshot(receipt).reference != cursor.reference:
                raise StateConflict('Save retry receipt does not identify its branch publication.')
            # Acknowledgement may itself have failed after no-overwrite commit.
            # Revalidate and sync under the writer lock, without a second save.
            self.store._require_writer_filesystem()
            self.store._acknowledge_commit()
            sync_directory(self.project)
            self._committed(receipt)
            return record

    def _session(self):
        session = self._operation.get()
        if session is None:
            raise ValueError('Branch control I/O requires a pinned operation.')
        return session

    def _address(self, path):
        # Do not resolve logical identities through old physical directories.
        address = Path(path).relative_to(self.project).as_posix()
        if any(part in ('', '.', '..') for part in address.split('/')):
            raise ValueError('Invalid logical branch control address.')
        return address

    @staticmethod
    def _contract(address):
        if address == 'branches/default.json':
            return 'project', 'branches', False
        if address == 'branches/main.json':
            return 'branch:main', 'branches', False
        backup = re.fullmatch(r'branches/authoring_backups/(main|[0-9a-f]{32})/[0-9a-f]{64}\.json', address)
        if backup:
            return 'branch:'+backup[1], 'branches', True
        branch, relative = 'main', address
        match = re.fullmatch(r'branches/([0-9a-f]{32})/(.+)', address)
        if match:
            branch, relative = match.groups()
        if (relative in ('plan.json', 'editorial.json', 'plan_studio_presentation.json')
                or (branch != 'main' and relative == 'branch.json')
                or re.fullmatch(r'checkpoints/clip_[0-9]{4}\.json', relative)):
            return 'branch:'+branch, 'branches', False
        raise ValueError('Unsupported branch control document: '+address)

    def _watch(self, address):
        session = self._session()
        contract = self._contract(address)
        previous = session['documents'].get(address)
        if previous and tuple(previous[k] for k in ('scope', 'category', 'immutable')) != contract:
            raise ValueError('Branch control import contract mismatch: '+address)
        session['reads'].add(contract[0])
        return session, contract

    def exists(self, path):
        address = self._address(path)
        session, _ = self._watch(address)
        return address not in session['retire_pointers'] and (
            address in session['changes'] or address in session['documents'])

    def read(self, path):
        address = self._address(path)
        session, _ = self._watch(address)
        if address in session['retire_pointers']:
            raise FileNotFoundError('Retired branch checkpoint assignment: '+address)
        if address in session['changes']:
            raw = session['changes'][address]['data']
        else:
            if address not in session['documents']:
                raise FileNotFoundError('Missing branch control document: '+address)
            raw = session['base'].read(address)
        return _decode(raw)

    def write(self, path, value):
        address = self._address(path)
        session, contract = self._watch(address)
        if address in session['retire_pointers']:
            raise ValueError('Cannot replace and retire the same checkpoint assignment.')
        scope, category, immutable = contract
        session['changes'][address] = {'data': _encode(value), 'scope': scope,
                                      'category': category, 'immutable': immutable}

    def watch_documents(self, paths):
        """Fence already-read control/index dependencies at publication.

        This grants no writes. Identities must belong to the operation's input
        root, including immutable take metadata and payload index records.
        """
        session = self._session()
        for path in paths:
            address = self._address(path)
            descriptor = session['documents'].get(address)
            if descriptor is None:
                raise StateConflict('Read dependency is absent from the input snapshot: '+address)
            session['reads'].add(descriptor['scope'])

    def retire_pointer(self, path):
        """Retire a mutable assignment in this transaction, never its files."""
        address = self._address(path)
        match = re.fullmatch(r'(?:branches/[0-9a-f]{32}/)?checkpoints/clip_([0-9]{4})\.json', address)
        if match is None or int(match[1]) < 1:
            raise ValueError('Only canonical checkpoint assignments can be retired.')
        session, _ = self._watch(address)
        if address in session['changes'] or address in session['retire_pointers']:
            raise ValueError('Duplicate checkpoint retirement or simultaneous replacement.')
        if address not in session['documents']:
            raise StateConflict('Cannot retire a checkpoint absent from the input snapshot.')
        session['base'].read(address)
        session['retire_pointers'].add(address)

    def matching(self, directory, pattern):
        """Pinned one-directory enumeration, including pending restore journals."""
        relative = Path(directory).relative_to(self.project).as_posix()
        session = self._session()
        match = re.fullmatch(r'(?:branches/([0-9a-f]{32})/)?checkpoints(?:/\.transactions)?', relative)
        if not match:
            raise ValueError('Unsupported branch control directory scan.')
        session['reads'].add('branch:'+(match[1] or 'main'))
        prefix = relative+'/'
        return [self.project/address for address in sorted(
                    (set(session['documents']) | set(session['changes'])) - session['retire_pointers'])
                if address.startswith(prefix) and '/' not in address[len(prefix):]
                and fnmatchcase(address[len(prefix):], pattern)]

    def branch_ids(self):
        session = self._session()
        return sorted({match[1] for address in set(session['documents']) | set(session['changes'])
                       if (match := re.fullmatch(r'branches/([0-9a-f]{32})/branch\.json', address))})

    def sync(self):
        # A previous call may have published storage.json then lost its directory
        # fsync acknowledgement. Retry syncs the pointer's parent, not a blob path.
        self._session()
        sync_directory(self.project)
