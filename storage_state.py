"""Copy-only control-state transactions; not a production storage reader.

Exact legacy document bytes are versioned under project/. A single storage.json
selects an immutable complete root. Pinned readers never mix generations;
writers compare their read/changed scopes, not a process-global active branch.
This control-only rehearsal deliberately cannot be opened by normal H3 nodes.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import copy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_resolver as resolver
    from .storage_rehearsal import _copy_root, _lock
    from .processing_persistence import atomic_json, sync_directory, publish_new_file, require_atomic_control_files
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_resolver as resolver
    from storage_rehearsal import _copy_root, _lock
    from processing_persistence import atomic_json, sync_directory, publish_new_file, require_atomic_control_files
    from storage_layout import OrganizedStorageLayout

FORMAT = 'h3_control_state_rehearsal_v1'
ROOT = 'h3_control_state_root_v1'
INTENT = 'h3_control_state_intent_v1'
_ACCESS = ContextVar('h3_control_rehearsal', default=None)
_CATEGORIES = frozenset(('legacy', 'branches', 'passes', 'takes', 'cuts', 'payloads',
                        'history', 'recovery', 'reviews', 'assets', 'reference_cache', 'jobs'))


class StateConflict(ValueError):
    pass


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def _token(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Control-state operation requires a full 32-hex identity.')
    return value


def _scope(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'(?:project|(?:branch|pass|history|cache|exports|archive|jobs):[A-Za-z0-9_-]{1,96})', value):
        raise ValueError('Invalid explicit control-state scope.')
    return value


def _logical(value):
    # This validates an address, never a physical location or a saved hash input.
    if not isinstance(value, str) or resolver.artifact_address(value) != value:
        raise ValueError('Control-state identity must be a canonical logical address.')
    if value.split('/')[0] in ('storage.json', 'project', 'media', 'exports') or value.endswith('.lock'):
        raise ValueError('Reserved control-state identity or coordination file.')
    return value


@contextmanager
def control_rehearsal_access(project):
    """Explicit private test/tool scope, never called by normal nodes or routes."""
    token = _ACCESS.set(str(Path(project).absolute()))
    try:
        yield
    finally:
        _ACCESS.reset(token)


def _mkdir(path, boundary):
    if path == boundary or path.exists():
        return
    _mkdir(path.parent, boundary)
    path.mkdir()
    sync_directory(path.parent)


def _immutable(project, address, raw, budget):
    """Publish independent bytes with no replacement; retain uncertain staging."""
    if len(raw) > 32*1024*1024:
        raise ValueError('Control-state record exceeds the supported size.')
    layout = OrganizedStorageLayout(str(project), budget)
    layout.check_atomic_json_budget(address)
    path = resolver.confined(project, address)
    if path.exists():
        if _read_bytes(path) != raw:
            raise ValueError('Immutable control-state collision; existing file retained.')
        sync_directory(path.parent)
        return {'path': address, 'sha256': _hash(raw), 'size': len(raw)}
    _mkdir(path.parent, project)
    temporary = resolver.confined(project,
        (path.parent/('.tmp-'+uuid.uuid4().hex)).relative_to(project).as_posix())
    with temporary.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    publish_new_file(temporary, path)  # own independent staging, never the source
    sync_directory(path.parent)
    return {'path': address, 'sha256': _hash(raw), 'size': len(raw)}


def _read_bytes(path):
    with path.open('rb') as handle:
        before = resolver._stat_signature(os.fstat(handle.fileno()))
        raw = handle.read(32*1024*1024+1)
        after = resolver._stat_signature(os.fstat(handle.fileno()))
    if len(raw) > 32*1024*1024 or after != before or resolver._signature(path) != before:
        raise ValueError('Control record is too large or changed during reading.')
    return raw


def _validate_reference(ref, pattern):
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'size'}
            or not re.fullmatch(pattern, str(ref.get('path')))
            or not re.fullmatch('[0-9a-f]{64}', str(ref.get('sha256')))
            or type(ref.get('size')) is not int or not 0 <= ref['size'] <= 32*1024*1024):
        raise ValueError('Invalid control-state reference.')


def _reference(project, ref, pattern):
    _validate_reference(ref, pattern)
    raw = _read_bytes(resolver.confined(project, ref['path']))
    if len(raw) != ref['size'] or _hash(raw) != ref['sha256']:
        raise ValueError('Control-state authority checksum mismatch; no legacy fallback.')
    return raw


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate control-state authority key.')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Non-finite authority value.')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError('Control-state authority must be an object.')
    return value


def _descriptor(value):
    if (not isinstance(value, dict) or set(value) != {'scope', 'category', 'immutable', 'file'}
            or value['category'] not in _CATEGORIES or type(value['immutable']) is not bool):
        raise ValueError('Invalid control document descriptor.')
    _scope(value['scope'])
    return value


@dataclass(frozen=True, init=False)
class Snapshot:
    project: Path
    _path: str
    _sha256: str
    _size: int

    def __init__(self, project, reference):
        _validate_reference(reference, r'project/roots/[0-9a-f]{32}\.json')
        object.__setattr__(self, 'project', Path(project).absolute())
        object.__setattr__(self, '_path', reference['path'])
        object.__setattr__(self, '_sha256', reference['sha256'])
        object.__setattr__(self, '_size', reference['size'])

    @property
    def reference(self):
        return {'path': self._path, 'sha256': self._sha256, 'size': self._size}

    def _validated_root(self):
        if _ACCESS.get() != str(self.project):
            raise ValueError('Control-state snapshots require explicit copy-only test access.')
        path = resolver.confined(self.project, self._path)
        return _cached_root(str(self.project), self._path, self._sha256, self._size,
                            resolver._signature(path))

    def _root(self):
        # Callers preparing a commit may mutate their private copy. Never expose
        # the cached authority dictionary through public state/read properties.
        return copy.deepcopy(self._validated_root())

    def _read_root(self):
        if _ACCESS.get() != str(self.project):
            raise ValueError('Control-state snapshots require explicit copy-only test access.')
        raw = _reference(self.project, self.reference, r'project/roots/[0-9a-f]{32}\.json')
        root = _decode(raw)
        if (root.get('format') != ROOT or root.get('run_name') != self.project.name
                or type(root.get('epoch')) is not int or root['epoch'] < 1
                or type(root.get('generation')) is not int or root['generation'] < 0
                or not isinstance(root.get('documents'), dict)
                or not isinstance(root.get('scope_revisions'), dict)
                or not isinstance(root.get('operations'), dict)):
            raise ValueError('Invalid control-state root.')
        names = set()
        for address, descriptor in root['documents'].items():
            _logical(address)
            _descriptor(descriptor)
            _validate_reference(descriptor['file'],
                                'project/'+descriptor['category']+r'/[0-9a-f]{32}\.(?:json|txt)')
            if address.casefold() in names or descriptor['scope'] not in root['scope_revisions']:
                raise ValueError('Duplicate/case-colliding identity or missing scope revision.')
            names.add(address.casefold())
        for address in names:
            if any('/'.join(address.split('/')[:i]) in names for i in range(1, len(address.split('/')))):
                raise ValueError('Control-state file/directory identity collision.')
        for scope, revision in root['scope_revisions'].items():
            _scope(scope)
            _token(revision)
        if root.get('parent') is not None:
            _validate_reference(root['parent'], r'project/roots/[0-9a-f]{32}\.json')
        if (root['generation'] == 0) != (root.get('parent') is None):
            raise ValueError('Control-state parent/generation mismatch.')
        for operation, receipt in root['operations'].items():
            _token(operation)
            if (not isinstance(receipt, dict) or receipt.get('operation_id') != operation
                    or type(receipt.get('generation')) is not int
                    or not 1 <= receipt['generation'] <= root['generation']
                    or not re.fullmatch('[0-9a-f]{64}', str(receipt.get('request_sha256')))
                    or not isinstance(receipt.get('scope_revisions'), dict)):
                raise ValueError('Invalid control-state operation receipt.')
            for scope, revision in receipt['scope_revisions'].items():
                _scope(scope)
                _token(revision)
        return root

    @property
    def state(self):
        return self._root()

    def read(self, address):
        value = self._validated_root()['documents'][_logical(address)]
        return _reference(self.project, value['file'],
                          'project/'+value['category']+r'/[0-9a-f]{32}\.(?:json|txt)')

    def verify(self):
        root = self._validated_root()
        signatures = {}
        for descriptor in root['documents'].values():
            path = resolver.confined(self.project, descriptor['file']['path'])
            signatures[path] = resolver._signature(path)
            _reference(self.project, descriptor['file'],
                       'project/'+descriptor['category']+r'/[0-9a-f]{32}\.(?:json|txt)')
        if any(resolver._signature(path) != signature for path, signature in signatures.items()):
            raise ValueError('Control files changed during snapshot verification.')
        self._root()  # also recheck the immutable root after the document pass
        return len(root['documents'])


@lru_cache(maxsize=16)
def _cached_root(project, path, digest, size, signature):
    snapshot = Snapshot(project, {'path': path, 'sha256': digest, 'size': size})
    root = snapshot._read_root()
    if resolver._signature(resolver.confined(project, path)) != signature:
        raise ValueError('Control-state root changed during validation.')
    return root


class ControlStore:
    FORMAT = FORMAT
    MODE = 'control_only_rehearsal'

    def __init__(self, project):
        self.project = Path(project).absolute()
        if self.project.parent.name != 'h3_chains':
            raise ValueError('Control rehearsal requires an explicit project root.')
        resolver.confined(self.project, 'storage.json')

    def _authority(self):
        if _ACCESS.get() != str(self.project):
            raise ValueError('Control-state storage is copy-only and requires explicit test access.')
        raw = _read_bytes(resolver.confined(self.project, 'storage.json'))
        marker = _decode(raw)
        if 'commit_protocol' in marker:
            if __package__:
                from .storage_commit_log import CommitLog
            else:
                from storage_commit_log import CommitLog
            with _lock(self.project):
                head = CommitLog(self.project, raw).read()
            marker, raw = head.value, head.witness
        return marker, raw

    def _marker(self):
        marker, raw = self._authority()
        if (marker.get('format') != self.FORMAT or type(marker.get('version')) is not int
                or marker['version'] != 1 or marker.get('mode') != self.MODE
                or marker.get('phase') != 'ready' or marker.get('run_name') != self.project.name
                or type(marker.get('path_budget')) is not int or marker['path_budget'] < 1):
            raise ValueError('Unsupported or incomplete control-state storage; no fallback.')
        snapshot = Snapshot(self.project, marker['root'])
        # Internal scalar validation must not deepcopy every indexed payload.
        # The cached root never escapes here; public Snapshot.state/_root still
        # return independent dictionaries to callers.
        state = snapshot._validated_root()
        if (type(marker.get('epoch')) is not int or type(marker.get('generation')) is not int
                or state['epoch'] != marker['epoch'] or state['generation'] != marker['generation']):
            raise ValueError('Control-state root and pointer disagree.')
        return marker, raw, snapshot

    def _require_writer_filesystem(self, fallback=None):
        marker = _decode(_read_bytes(resolver.confined(self.project, 'storage.json')))
        if 'commit_protocol' in marker:
            self._marker()  # validate the opted-in authority, not a bare feature flag
        else:
            (fallback or require_atomic_control_files)(self.project)

    def _acknowledge_commit(self):
        raw = _read_bytes(resolver.confined(self.project, 'storage.json'))
        if 'commit_protocol' not in _decode(raw):
            return
        if __package__:
            from .storage_commit_log import CommitLog
        else:
            from storage_commit_log import CommitLog
        log = CommitLog(self.project, raw)
        head = log.read()
        if not head.acknowledged:
            candidate = Snapshot(self.project, head.value['root'])
            parent = candidate.state['parent']
            if parent is None:
                if (head.sequence != 1 or log.initial.get('phase') != 'building'
                        or candidate.state['generation'] != 0 or self.FORMAT != FORMAT):
                    raise StateConflict('A logged commit must have an accepted parent root.')
                candidate.verify()  # exact initial control import, no payloads
            else:
                self._validate_candidate(candidate, dict(head.value, root=parent))
        log.acknowledge(head)

    def snapshot(self):
        return self._marker()[2]

    def committed_snapshot(self, receipt):
        """Resolve an exact successful commit receipt, never a latest-root guess.

        Receipts remain unchanged on disk. Their generation identifies the
        accepted ancestor even when another writer commits before this call.
        Unpublished roots and lookalike/edited receipts cannot select a result.
        """
        if (not isinstance(receipt, dict) or type(receipt.get('generation')) is not int
                or receipt['generation'] < 1):
            raise ValueError('Invalid committed-state receipt.')
        operation = _token(receipt.get('operation_id'))
        current = self.snapshot()
        cursor, root = current, current.state
        if _encode(root['operations'].get(operation, {})) != _encode(receipt):
            raise StateConflict('Receipt is not present in accepted storage history.')
        while root['generation'] > receipt['generation']:
            if root['parent'] is None:
                raise StateConflict('Receipt has no accepted ancestor.')
            parent = Snapshot(self.project, root['parent'])
            older = parent.state
            if (older['generation'] != root['generation']-1
                    or older['epoch'] not in (root['epoch'], root['epoch']-1)):
                raise StateConflict('Receipt ancestry is inconsistent.')
            cursor, root = parent, older
        if (root['generation'] != receipt['generation'] or
                _encode(root['operations'].get(operation, {})) != _encode(receipt)):
            raise StateConflict('Receipt does not identify its original committed root.')
        self.validate_snapshot(cursor, current=current)
        return cursor

    def validate_snapshot(self, base, *, current=None):
        """Accept only this project's published root or a committed ancestor."""
        if not isinstance(base, Snapshot) or base.project != self.project:
            raise ValueError('Pinned snapshot belongs to a different project.')
        current = current or self.snapshot()
        if not isinstance(current, Snapshot) or current.project != self.project:
            raise ValueError('Current snapshot belongs to a different project.')
        original = base._root()
        cursor, node = current, current._root()
        while cursor.reference != base.reference:
            if node['generation'] <= original['generation'] or node['parent'] is None:
                raise StateConflict('Base snapshot is not a committed ancestor of the current root.')
            parent = Snapshot(self.project, node['parent'])
            older = parent._root()
            if (older['generation'] != node['generation']-1
                    or older['epoch'] not in (node['epoch'], node['epoch']-1)):
                raise StateConflict('Control-state root ancestry is inconsistent.')
            cursor, node = parent, older
        return base

    def _validate_candidate(self, candidate, marker):
        """Subclass boundary for payload witnesses; no physical path writes here."""
        candidate.verify()

    def _write_root(self, root, operation, budget):
        return _immutable(self.project, 'project/roots/'+uuid.uuid4().hex+'.json', _encode(root), budget)

    def _publish(self, marker, old_raw, root, operation, after_stage):
        ref = self._write_root(root, operation, marker['path_budget'])
        candidate = Snapshot(self.project, ref)
        if after_stage:
            after_stage('root')
        self._validate_candidate(candidate, marker)
        if 'commit_protocol' in marker:
            try:
                actual = self._authority()[1]
            except ValueError as error:
                raise StateConflict('Control-state authority changed outside the writer lock.') from error
        else:
            actual = _read_bytes(resolver.confined(self.project, 'storage.json'))
        if actual != old_raw:
            raise StateConflict('Control-state pointer changed outside the writer lock.')
        # Old roots, documents and intents are retained on uncertain publication.
        updated = dict(marker, root=ref, generation=root['generation'], epoch=root['epoch'])
        if 'commit_protocol' in marker:
            if __package__:
                from .storage_commit_log import CommitLog
            else:
                from storage_commit_log import CommitLog
            raw = _read_bytes(resolver.confined(self.project, 'storage.json'))
            log = CommitLog(self.project, raw)
            if not log.read().acknowledged:
                self._acknowledge_commit()
            log.append(updated, old_raw, after_stage=after_stage)
        else:
            atomic_json(resolver.confined(self.project, 'storage.json'), updated)
        return copy.deepcopy(root['operations'][operation])

    def commit(self, base, changes, *, operation_id, read_scopes=(), after_stage=None,
               retire_pointers=()):
        """Publish all changes together; compare every written and declared read scope.

        changes[address] = {data: bytes, scope: str, category: str, immutable: bool}
        retire_pointers removes only mutable canonical checkpoint assignments
        from the new root. All old roots, control bytes and media stay retained.
        This is not a general deletion/purge or scope reassignment API.
        """
        return self._commit_changes(base, changes, operation_id=operation_id,
            read_scopes=read_scopes, after_stage=after_stage, retire_pointers=retire_pointers)

    def _commit_changes(self, base, changes, *, operation_id, read_scopes=(), after_stage=None,
                        retire_pointers=(), retire_documents=None, restore_documents=None, replace_documents=None):
        """Internal delta kernel; domain services own quarantine authorization.

        Unlike the public assignment API, exact document retirement is used
        only with a domain receipt retaining all descriptors and original bytes.
        This kernel never removes a physical file or rewrites an old root.
        """
        self._require_writer_filesystem()
        operation = _token(operation_id)
        if (not isinstance(base, Snapshot) or base.project != self.project
                or not isinstance(changes, dict) or not (changes or retire_pointers or retire_documents)):
            raise ValueError('Commit requires this project\'s pinned snapshot and nonempty changes.')
        if not isinstance(retire_pointers, (tuple, list)):
            raise ValueError('Pointer retirement requires an explicit address list.')
        retire_pointers = tuple(retire_pointers)
        retiring = {}
        base_root = base._validated_root()
        if retire_documents is not None and not isinstance(retire_documents, dict):
            raise ValueError('Document retirement requires exact accepted descriptors.')
        for address, expected in (retire_documents or {}).items():
            _logical(address)
            _descriptor(expected)
            if address in changes or address in retire_pointers:
                raise ValueError('Cannot replace and retire the same accepted document.')
            if base_root['documents'].get(address) != expected:
                raise StateConflict('Document retirement differs from its accepted input descriptor.')
            base.read(address)
            retiring[address] = copy.deepcopy(expected)
        for address in retire_pointers:
            _logical(address)
            match = re.fullmatch(r'(?:branches/([0-9a-f]{32})/)?checkpoints/clip_([0-9]{4})\.json', address)
            if match is None or int(match[2]) < 1:
                raise ValueError('Only canonical scene checkpoint assignments can be retired.')
            if address in retiring or address in changes:
                raise ValueError('Pointer retirement contains a duplicate or a simultaneous replacement.')
            previous = base_root['documents'].get(address)
            if previous is None:
                raise StateConflict('Cannot retire a checkpoint absent from the input snapshot.')
            if (previous['scope'] != 'branch:'+(match[1] or 'main')
                    or previous['category'] != 'branches' or previous['immutable']):
                raise StateConflict('Checkpoint retirement has an unsupported ownership contract.')
            base.read(address)  # Do not hide missing/corrupt accepted bytes.
            retiring[address] = copy.deepcopy(previous)
        # Freeze caller-owned data before taking a lock or running callbacks.
        proposed = copy.deepcopy(changes)
        replacements = copy.deepcopy(replace_documents or {})
        if not isinstance(replacements, dict) or set(replacements)-set(proposed):
            raise ValueError('Version replacement requires exact descriptors for declared changes.')
        for address, expected in replacements.items():
            _logical(address)
            _descriptor(expected)
            if not expected['immutable'] or base_root['documents'].get(address) != expected:
                raise StateConflict('Version replacement differs from its accepted immutable descriptor.')
            base.read(address)
        restoring = copy.deepcopy(restore_documents or {})
        if not isinstance(restoring, dict) or set(restoring)-set(proposed):
            raise ValueError('Restoration requires exact descriptors for declared changes.')
        descriptors = {}
        for address, value in proposed.items():
            _logical(address)
            if not isinstance(value, dict) or set(value) != {'data', 'scope', 'category', 'immutable'}:
                raise ValueError('Invalid control-state change.')
            raw = value.pop('data')
            if not isinstance(raw, bytes) or len(raw) > 32*1024*1024:
                raise ValueError('Control-state changes require bounded exact bytes.')
            value['file'] = {'sha256': _hash(raw), 'size': len(raw)}
            _descriptor(value)
            if address in restoring:
                restored = _descriptor(restoring[address])
                if (any(restored[key] != value[key] for key in ('scope', 'category', 'immutable'))
                        or restored['file'].get('sha256') != value['file']['sha256']
                        or restored['file'].get('size') != len(raw)):
                    raise StateConflict('Restored descriptor changes the original bytes or ownership contract.')
                if _reference(self.project, restored['file'],
                        'project/'+restored['category']+r'/[0-9a-f]{32}\.(?:json|txt)') != raw:
                    raise StateConflict('Restored control bytes differ from their retained version.')
                value = restored
            descriptors[address] = (value, raw)
        watched = sorted({_scope(s) for s in read_scopes} | {v[0]['scope'] for v in descriptors.values()}
                         | {v['scope'] for v in retiring.values()})
        request = _hash(_encode({'action': 'commit', 'base': base.reference,
            'changes': {p: v[0] for p, v in descriptors.items()}, 'read_scopes': watched,
            **({'retire_pointers': retiring} if retiring else {}),
            **({'retire_documents': sorted(retire_documents)} if retire_documents else {}),
            **({'replace_documents': replacements} if replacements else {}),
            **({'restore_documents': restoring} if restoring else {})}))
        with _lock(self.project):
            marker, old_raw, current_snapshot = self._marker()
            current, original = current_snapshot._root(), base._root()
            receipt = current['operations'].get(operation)
            if receipt is not None:
                if receipt['request_sha256'] != request:
                    raise StateConflict('Operation identity was reused with different control changes.')
                self._acknowledge_commit()
                return copy.deepcopy(receipt)
            if current['epoch'] != original['epoch']:
                raise StateConflict('Storage epoch changed; reload before saving queued work.')
            self.validate_snapshot(base, current=current_snapshot)
            for scope in watched:
                if current['scope_revisions'].get(scope) != original['scope_revisions'].get(scope):
                    raise StateConflict('Control-state scope changed: '+scope)
            for address, previous in retiring.items():
                if current['documents'].get(address) != previous:
                    raise StateConflict('Checkpoint assignment changed before retirement.')
                current_snapshot.read(address)
            for address, (descriptor, _) in descriptors.items():
                previous = current['documents'].get(address)
                if address in replacements and previous != replacements[address]:
                    raise StateConflict('Immutable version changed before its witnessed replacement.')
                if previous and (previous['scope'] != descriptor['scope']
                        or previous['category'] != descriptor['category']
                        or previous['immutable'] != descriptor['immutable']
                        or (previous['immutable'] and previous['file']['sha256'] != descriptor['file']['sha256']
                            and address not in replacements)):
                    raise StateConflict('Cannot replace immutable work or reassign a document\'s scope/category.')
            intent = {'format': INTENT, 'operation_id': operation, 'request_sha256': request,
                      'base': base.reference, 'read_scopes': watched,
                      **({'retire_pointers': retiring} if retiring else {}),
                      **({'retire_documents': sorted(retire_documents)} if retire_documents else {}),
                      **({'replace_documents': replacements} if replacements else {}),
                      **({'restore_documents': restoring} if restoring else {})}
            _immutable(self.project, 'project/jobs/'+operation+'.json', _encode(intent), marker['path_budget'])
            changed_scopes = set()
            for address, (descriptor, raw) in descriptors.items():
                suffix = '.txt' if address.endswith('.txt') else '.json'
                identity = _hash(_encode([address, descriptor]))[:32]
                if address not in restoring:
                    descriptor['file'] = _immutable(self.project,
                        'project/'+descriptor['category']+'/'+identity+suffix, raw, marker['path_budget'])
                current['documents'][address] = descriptor
                changed_scopes.add(descriptor['scope'])
                if after_stage:
                    after_stage('document')
            for address, previous in retiring.items():
                del current['documents'][address]
                changed_scopes.add(previous['scope'])
                if after_stage:
                    after_stage('retired_document' if address in (retire_documents or {}) else 'retired_pointer')
            for scope in changed_scopes:
                current['scope_revisions'][scope] = uuid.uuid4().hex
            current['generation'] += 1
            current['parent'] = current_snapshot.reference
            current['operations'][operation] = {'operation_id': operation,
                'generation': current['generation'], 'request_sha256': request,
                'scope_revisions': {s: current['scope_revisions'][s] for s in sorted(changed_scopes)}}
            return self._publish(marker, old_raw, current, operation, after_stage)

    def advance_epoch(self, base, *, operation_id):
        """Maintenance fencing only: never change branch selection or roll back state."""
        self._require_writer_filesystem()
        operation = _token(operation_id)
        if not isinstance(base, Snapshot) or base.project != self.project:
            raise ValueError('Maintenance requires this project\'s pinned snapshot.')
        request = _hash(_encode({'action': 'advance_epoch', 'base': base.reference}))
        with _lock(self.project):
            marker, old_raw, snapshot = self._marker()
            root = snapshot._root()
            receipt = root['operations'].get(operation)
            if receipt:
                if receipt['request_sha256'] != request:
                    raise StateConflict('Operation identity was reused.')
                self._acknowledge_commit()
                return copy.deepcopy(receipt)
            if snapshot.reference != base.reference:
                raise StateConflict('Project changed before maintenance; reload its state.')
            root.update(epoch=root['epoch']+1, generation=root['generation']+1, parent=snapshot.reference)
            root['operations'][operation] = {'operation_id': operation, 'generation': root['generation'],
                'request_sha256': request, 'scope_revisions': {}}
            return self._publish(marker, old_raw, root, operation, None)


def create_control_rehearsal(receipt_path, destination_output, documents, *, path_budget=240,
                             commit_protocol=None):
    """Import reviewed control records into a NEW control-only copy.

    documents[logical] = {source: receipted-copy-relative path, sha256: digest,
                         scope: explicit owner, category: storage group, immutable: bool}
    No media is copied or rebound. Failed imports remain gated for inspection;
    retry into a different new destination, never adopt an existing directory.
    """
    source, _ = _copy_root(receipt_path)
    lab, output = Path(receipt_path).absolute().parent, Path(destination_output).absolute()
    if commit_protocol not in (None, 'immutable_slots_v1'):
        raise ValueError('Unsupported control-state commit protocol.')
    if commit_protocol is None:
        require_atomic_control_files(output)
    if (output == lab or not output.is_relative_to(lab)
            or output.is_relative_to(source.parent.parent) or source.is_relative_to(output)):
        raise ValueError('Control rehearsal must be a separate new output beneath the receipt lab.')
    resolver.confined(lab, output.relative_to(lab).as_posix())
    if output.exists() or not documents:
        raise ValueError('Use a new empty destination and explicit nonempty control inventory.')
    project = output/'h3_chains'/source.name
    policy = OrganizedStorageLayout(str(project), path_budget)
    policy.check_atomic_json_budget('storage.json')
    initial, captured = {}, {}
    with _lock(source), resolver.rehearsal_access(source):
        resolver.storage_state(source)
        for address, request in copy.deepcopy(documents).items():
            _logical(address)
            path = resolver.confined(source, request['source'])
            raw = _read_bytes(path)
            if _hash(raw) != request['sha256']:
                raise ValueError('Import source checksum changed; no state imported.')
            initial[address] = {'scope': request['scope'], 'category': request['category'],
                                'immutable': request['immutable'], 'file': {}}
            _descriptor(initial[address])
            captured[address] = (path, raw)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir()  # exclusive, never adopt a user-owned folder
        sync_directory(output.parent)
        _mkdir(project, output)
        marker = {'format': FORMAT, 'version': 1, 'run_name': source.name,
                  'mode': 'control_only_rehearsal', 'phase': 'building', 'path_budget': path_budget}
        if commit_protocol is None:
            atomic_json(project/'storage.json', marker)
        else:
            marker['commit_protocol'] = commit_protocol
            _immutable(project, 'storage.json', _encode(marker), path_budget)
        for address, descriptor in initial.items():
            raw = captured[address][1]
            suffix = '.txt' if address.endswith('.txt') else '.json'
            identity = _hash(_encode([address, descriptor, _hash(raw)]))[:32]
            descriptor['file'] = _immutable(project, 'project/'+descriptor['category']+'/'+identity+suffix,
                                             raw, path_budget)
        root = {'format': ROOT, 'run_name': source.name, 'epoch': 1, 'generation': 0,
                'documents': initial, 'scope_revisions': {v['scope']: uuid.uuid4().hex for v in initial.values()},
                'operations': {}, 'parent': None, 'import_source': str(source)}
        ref = _immutable(project, 'project/roots/'+uuid.uuid4().hex+'.json', _encode(root), path_budget)
        with control_rehearsal_access(project):
            Snapshot(project, ref).verify()
        # The source may have been edited by a non-participating external tool.
        if any(_read_bytes(path) != raw for path, raw in captured.values()):
            raise StateConflict('Import source changed; incomplete copy retained, no publication.')
        marker.update(phase='ready', root=ref, epoch=1, generation=0)
        if commit_protocol is None:
            atomic_json(project/'storage.json', marker)
        else:
            if __package__:
                from .storage_commit_log import CommitLog
            else:
                from storage_commit_log import CommitLog
            log = CommitLog(project, _read_bytes(project/'storage.json'))
            log.append(marker, log.read().witness)
    return ControlStore(project)
