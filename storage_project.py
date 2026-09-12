"""Combined media/control publication on isolated copies; no runtime activation.

Each accepted payload has a checksum-bound descriptor in the SAME immutable
root as branch/take/pass controls. Staging alone never makes a payload visible.
Old roots retain their exact file versions. No mutable blob paths are exposed.
"""
import copy
import hashlib
import os
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state, storage_resolver as resolver
    from .storage_rehearsal import _lock
    from .processing_persistence import sync_directory, publish_new_file, require_atomic_control_files
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_state as state
    import storage_resolver as resolver
    from storage_rehearsal import _lock
    from processing_persistence import sync_directory, publish_new_file, require_atomic_control_files
    from storage_layout import OrganizedStorageLayout

FORMAT = 'h3_project_storage_rehearsal_v1'
PAYLOAD = 'h3_project_payload_v1'
STAGE = 'h3_project_payload_stage_v1'
PREFIX = '__storage__/payloads/'


def payload_key(address):
    address = state._logical(address)
    if address.startswith('__storage__/'):
        raise ValueError('Reserved internal project identity.')
    return PREFIX+state._hash(address.encode())+'.json'


def _target(value):
    if not isinstance(value, str) or resolver.artifact_address(value) != value:
        raise ValueError('Payload target must be a canonical organized address.')
    parts = value.split('/')
    allowed_project = ('assets', 'reference_cache', 'recovery', 'optional', 'legacy', 'takes')
    if (len(parts) < 3 or parts[0] not in ('media', 'exports', 'project')
            or (parts[0] == 'project' and parts[1] not in allowed_project)
            or value.endswith('.lock')):
        raise ValueError('Payload cannot occupy state authority or coordination paths.')
    return value


def _record(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'address', 'scope', 'immutable', 'file'}
            or value['format'] != PAYLOAD or type(value['immutable']) is not bool):
        raise ValueError('Invalid project payload descriptor.')
    payload_key(value['address'])
    state._scope(value['scope'])
    ref = value['file']
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'size'}
            or type(ref['size']) is not int or ref['size'] < 0
            or not re.fullmatch('[0-9a-f]{64}', str(ref['sha256']))):
        raise ValueError('Invalid payload file witness.')
    _target(ref['path'])
    return value


def _hash_file(path):
    with path.open('rb') as handle:
        # CIFS can report a cached client timestamp until open revalidates it.
        # Bind the hash to the OPENED file, then check that file and its path.
        # Never ignore mtime/ctime differences or retry a changing source away.
        before = resolver._stat_signature(os.fstat(handle.fileno()))
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        after = resolver._stat_signature(os.fstat(handle.fileno()))
    if before != after or before != resolver._signature(path):
        raise state.StateConflict('Payload changed while hashing.')
    return digest, before


def _verify_file(project, record):
    path = resolver.confined(project, _target(record['file']['path']))
    digest, signature = _hash_file(path)
    if digest != record['file']['sha256'] or signature[2] != record['file']['size']:
        raise state.StateConflict('Payload checksum/size differs from accepted descriptor: '+record['address'])
    return path, signature


def _indexed_record(snapshot, key, descriptor=None):
    if descriptor is None:
        descriptor = snapshot._validated_root()['documents'][key]
    record = _record(state._decode(snapshot.read(key)))
    if (key != payload_key(record['address']) or descriptor['category'] != 'payloads'
            or descriptor['scope'] != record['scope'] or descriptor['immutable'] != record['immutable']):
        raise ValueError('Payload index identity or ownership mismatch.')
    return record


def payload_catalog(snapshot):
    """Validate all indexed identities without loading/hashing media tensors."""
    root = snapshot.state
    records, names, targets = {}, {}, {}
    for address, descriptor in root['documents'].items():
        if not address.startswith(PREFIX):
            if address.startswith('__storage__/'):
                raise ValueError('Unknown internal project record; update this reader.')
            names[address.casefold()] = address
            continue
        record = _indexed_record(snapshot, address, descriptor)
        folded = record['address'].casefold()
        physical = record['file']['path'].casefold()
        if folded in names or physical in targets:
            raise ValueError('Duplicate/case-colliding payload identity or physical owner.')
        names[folded], targets[physical] = record['address'], record['address']
        records[record['address']] = record
    # Check against control identities too, independent of document ordering.
    control_names = {address.casefold() for address in root['documents'] if not address.startswith(PREFIX)}
    if control_names & {address.casefold() for address in records}:
        raise ValueError('Payload cannot hide a control document.')
    physical_controls = {descriptor['file']['path'].casefold() for descriptor in root['documents'].values()}
    if physical_controls & set(targets):
        raise ValueError('Payload cannot overwrite an immutable control version.')
    for entries in (set(names), set(targets) | physical_controls):
        for address in entries:
            parts = address.split('/')
            if any('/'.join(parts[:i]) in entries for i in range(1, len(parts))):
                raise ValueError('Payload/control file-directory collision.')
    return records


class ProjectStore(state.ControlStore):
    FORMAT = FORMAT
    MODE = 'project_rehearsal'

    def commit(self, base, changes, **kwargs):
        if any(address.startswith('retention/') for address in changes):
            raise ValueError('Retention receipts require the quarantine transaction service.')
        if any(address.startswith('__storage__/') or item.get('category') == 'payloads'
               for address, item in changes.items()):
            raise ValueError('Payload registration requires a verified staging receipt.')
        return super().commit(base, changes, **kwargs)

    def commit_artifacts(self, base, controls, staged, *, operation_id, read_scopes=(), after_stage=None):
        self._require_writer_filesystem(fallback=require_atomic_control_files)
        changes = copy.deepcopy(controls)
        if any(address.startswith('retention/') for address in changes):
            raise ValueError('Retention receipts require the quarantine transaction service.')
        if any(address.startswith('__storage__/') or item.get('category') == 'payloads'
               for address, item in changes.items()):
            raise ValueError('Caller controls cannot replace the payload catalogue.')
        for receipt in staged:
            record = self._staged_record(receipt)
            key = payload_key(record['address'])
            if key in changes:
                raise ValueError('Duplicate staged payload identity.')
            changes[key] = {'data': state._encode(record), 'scope': record['scope'],
                            'category': 'payloads', 'immutable': record['immutable']}
        return super().commit(base, changes, operation_id=operation_id,
                              read_scopes=read_scopes, after_stage=after_stage)

    def _staged_record(self, receipt):
        if not isinstance(receipt, dict) or set(receipt) != {'operation_id', 'record'}:
            raise ValueError('Invalid payload staging receipt.')
        operation = state._token(receipt['operation_id'])
        path = resolver.confined(self.project, 'project/jobs/payload-'+operation+'.json')
        intent = state._decode(state._read_bytes(path))
        record = _record(copy.deepcopy(receipt['record']))
        if intent.get('format') != STAGE or intent.get('record') != record:
            raise ValueError('Staged payload does not match its reservation intent.')
        _verify_file(self.project, record)
        return record

    def _validate_candidate(self, candidate, marker):
        records = payload_catalog(candidate)
        old = state.Snapshot(self.project, marker['root'])
        previous = payload_catalog(old)
        if __package__:
            from .storage_retention_custody import candidate_exceptions, signature_unchanged
        else:
            from storage_retention_custody import candidate_exceptions, signature_unchanged
        custody = candidate_exceptions(candidate, old)
        signatures = {}
        for address, record in records.items():
            if previous.get(address) != record:
                path, signature = custody.get(address) or _verify_file(self.project, record)
                signatures[path] = signature
        # Quarantine promises intact retained bytes for undo. Removing an
        # index entry must not hide corruption introduced after its preview.
        for address in previous.keys()-records.keys():
            path, signature = custody.get(address) or _verify_file(self.project, previous[address])
            signatures[path] = signature
        candidate.verify()
        if any(not signature_unchanged(self.project, path, signature) for path, signature in signatures.items()):
            raise state.StateConflict('Payload changed during control publication verification.')

    def verify_payloads(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        if snapshot.project != self.project:
            raise ValueError('Payload verification belongs to another project.')
        signatures = {}
        records = payload_catalog(snapshot)
        for record in records.values():
            path, signature = _verify_file(self.project, record)
            signatures[path] = signature
        snapshot.verify()
        if any(resolver._signature(path) != signature for path, signature in signatures.items()):
            raise state.StateConflict('Payload changed during complete project verification.')
        return len(records)

    def payload_path(self, snapshot, address, *, verify=False):
        """Read-only path for an EXACT accepted file, never a writer reservation."""
        if snapshot.project != self.project:
            raise ValueError('Payload snapshot belongs to another project.')
        key = payload_key(address)
        record = _indexed_record(snapshot, key)
        if record['address'] != address:
            raise ValueError('Payload address mismatch.')
        if verify:
            return _verify_file(self.project, record)[0]
        path = resolver.confined(self.project, record['file']['path'])
        if not path.is_file() or path.stat().st_size != record['file']['size']:
            raise ValueError('Accepted payload is missing or has the wrong size.')
        return path

    def stage_payload(self, address, source, target, *, scope, operation_id, immutable=True):
        """Copy independent immutable file bytes; root acceptance is a later commit.

        Existing unowned destinations are never adopted. A retry is permitted
        only with the exact pre-existing reservation intent and matching bytes.
        """
        return self.stage_payloads([dict(address=address, source=source, target=target,
            scope=scope, operation_id=operation_id, immutable=immutable)])[0]

    def stage_payloads(self, requests, *, batch_size=32, after_stage=None):
        """Stage ordered files under bounded, separately revalidated lock batches.

        Each file still has its own exact reservation, independent copy, fsync
        and checksum witness. Only repeated authority/history reads are shared
        while the same project lock is held. This does not accept any payload,
        grant write access, or bypass the later commit's dependency/epoch checks.
        """
        if (not isinstance(requests, (list, tuple)) or not requests
                or type(batch_size) is not int or not 1 <= batch_size <= 128
                or (after_stage is not None and not callable(after_stage))):
            raise ValueError('Payload staging needs a nonempty ordered batch and bounded batch size.')
        normalized, identities, addresses, targets = [], set(), set(), set()
        for requested in requests:
            if (not isinstance(requested, dict)
                    or not {'address','source','target','scope','operation_id'} <= set(requested)
                    or set(requested)-{'address','source','target','scope','operation_id','immutable'}):
                raise ValueError('Invalid payload staging batch entry.')
            item = dict(requested, source=Path(requested['source']).absolute(),
                        immutable=requested.get('immutable', True))
            payload_key(item['address'])
            state._token(item['operation_id'])
            state._scope(item['scope'])
            _target(item['target'])
            if type(item['immutable']) is not bool:
                raise ValueError('Payload immutability must be an explicit boolean.')
            identity, address, target = item['operation_id'], item['address'].casefold(), item['target'].casefold()
            if identity in identities or address in addresses or target in targets:
                raise ValueError('Duplicate/case-colliding identity or destination in payload batch.')
            identities.add(identity)
            addresses.add(address)
            targets.add(target)
            normalized.append(item)
        receipts = []
        for start in range(0, len(normalized), batch_size):
            self._require_writer_filesystem(fallback=require_atomic_control_files)
            with _lock(self.project):
                marker, _, _ = self._marker()
                for item in normalized[start:start+batch_size]:
                    receipt = self._stage_payload_locked(item, marker)
                    receipts.append(receipt)
                    if after_stage is not None:
                        after_stage(copy.deepcopy(receipt))
        return receipts

    def _stage_payload_locked(self, item, marker):
        # Internal helper: called only with a validated current marker and the
        # project lock held. Never expose the marker as a caller-supplied grant.
        address, source, target = item['address'], item['source'], item['target']
        operation, scope, immutable = item['operation_id'], item['scope'], item['immutable']
        destination = resolver.confined(self.project, target)
        intent_address = 'project/jobs/payload-'+operation+'.json'
        intent_path = resolver.confined(self.project, intent_address)
        policy = OrganizedStorageLayout(str(self.project), marker['path_budget'])
        policy.check_budget(target)
        policy.check_atomic_json_budget(intent_address)
        digest, signature = _hash_file(source)
        record = _record({'format': PAYLOAD, 'address': address, 'scope': scope,
                          'immutable': immutable, 'file': {'path': target, 'sha256': digest, 'size': signature[2]}})
        intent = {'format': STAGE, 'source': str(source), 'record': record}
        raw = state._encode(intent)
        if intent_path.exists():
            if state._read_bytes(intent_path) != raw:
                raise state.StateConflict('Payload operation ID reused for a different request.')
        elif destination.exists():
            raise FileExistsError('Unowned payload destination is occupied; existing bytes retained.')
        state._immutable(self.project, intent_address, raw, marker['path_budget'])
        if destination.exists():
            _verify_file(self.project, record)
            sync_directory(destination.parent)
            return {'operation_id': operation, 'record': record}
        partial_address = 'project/jobs/'+uuid.uuid4().hex+'.part'
        policy.check_budget(partial_address)
        partial = resolver.confined(self.project, partial_address)
        with source.open('rb') as src, partial.open('xb') as dst:
            hasher = hashlib.sha256()
            for block in iter(lambda: src.read(1024*1024), b''):
                hasher.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
        if hasher.hexdigest() != digest or resolver._signature(source) != signature:
            raise state.StateConflict('Source changed while staging payload; partial retained.')
        state._mkdir(destination.parent, self.project)
        publish_new_file(partial, destination)  # own staging only, never the source
        sync_directory(destination.parent)
        return {'operation_id': operation, 'record': record}
