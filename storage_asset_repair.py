"""Explicit, inspected input restoration from one verified accepted asset mirror.

Browsing never calls this writer. Damaged bytes are independently preserved
before replacement. Different readable catalogs are conflicts, not an implicit
request to roll back newer authoring. A durable intent and the shared input
pending marker fence interrupted publication and exact retries.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

if __package__:
    from . import storage_state as state
    from .storage_asset_edits import _require, _check_scopes, _publish_media_file
    from .storage_project_assets import CATALOG, catalog_contract, media_entries
    from .storage_project import _hash_file, _indexed_record, payload_key
    from .storage_resolver import confined
    from .storage_layout import OrganizedStorageLayout
    from .project_ownership import project_write_guard
    from .processing_persistence import atomic_json, sync_file, sync_directory
else:
    import storage_state as state
    from storage_asset_edits import _require, _check_scopes, _publish_media_file
    from storage_project_assets import CATALOG, catalog_contract, media_entries
    from storage_project import _hash_file, _indexed_record, payload_key
    from storage_resolver import confined
    from storage_layout import OrganizedStorageLayout
    from project_ownership import project_write_guard
    from processing_persistence import atomic_json, sync_file, sync_directory


FORMAT = 'h3_input_asset_repair_v1'


def _fingerprint(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise state.StateConflict('Input asset repair requires regular files: '+str(path))
    digest, signature = _hash_file(path)
    return dict(sha256=digest, size=signature[2])


def _binding(service, assets):
    runtime = service.runtime
    runtime.check()
    if assets._read_view(runtime.run) is not runtime.reader:
        raise ValueError('Input asset repair requires the current exact storage reader.')
    if runtime.asset_input_root is not None and runtime.asset_input_root != Path(assets.input_root):
        raise ValueError('Input asset repair belongs to another input root.')
    return confined(assets.input_root, 'h3_projects/'+runtime.run)


def _sources(runtime, base):
    descriptor = base.state['documents'].get(CATALOG)
    if descriptor is None:
        raise ValueError('No accepted asset backup is available for input repair.')
    scopes = {'project', catalog_contract(descriptor)['scope']}
    raw = base.read(CATALOG)
    entries = media_entries(state._decode(raw), runtime.run)
    sources = {'catalog.json': raw}
    desired = {'catalog.json': dict(sha256=state._hash(raw), size=len(raw))}
    for relative, (digest, size) in sorted(entries.items()):
        address = 'project_assets/'+relative
        record = _indexed_record(base, payload_key(address))
        if (record['file']['sha256'], record['file']['size']) != (digest, size):
            raise state.StateConflict('Asset backup differs from its catalog: '+relative)
        sources[relative] = runtime.store.payload_path(base, address, verify=True)
        desired[relative] = dict(sha256=digest, size=size)
        scopes.add(record['scope'])
    return sources, desired, scopes


def _inspection(runtime, directory, base, desired):
    files = []
    for relative, wanted in desired.items():
        path = confined(directory, relative)
        before = _fingerprint(path)
        action = 'keep' if before == wanted else 'restore' if before is None else 'preserve_restore'
        if relative == 'catalog.json' and action == 'preserve_restore':
            # Even an unfamiliar-but-readable catalog may be a newer schema
            # or belong to another project. Do not silently rewrite it.
            if before['size'] > 32*1024*1024:
                action = 'conflict'
            else:
                raw = state._read_bytes(path)
                if dict(sha256=state._hash(raw), size=len(raw)) != before:
                    raise state.StateConflict('Input catalog changed during repair inspection.')
                try:
                    json.loads(raw)
                except (ValueError, UnicodeError):
                    pass
                else:
                    action = 'conflict'
        files.append(dict(path=relative, before=before, after=wanted, action=action))
    return dict(format=FORMAT, project=runtime.run, input_pin=runtime.pin,
        base=base.reference, input_root_sha256=state._hash(str(directory).encode()), files=files,
        repairable=not any(item['action'] == 'conflict' for item in files))


def inspect_inputs(service, assets):
    """Read-only: no directory creation, lock files, catalog repair or receipts."""
    directory = _binding(service, assets)
    runtime = service.runtime
    base = runtime.accepted
    current = runtime.check()
    runtime.store.validate_snapshot(base, current=current)
    _, desired, scopes = _sources(runtime, base)
    _check_scopes(base, current, scopes)
    return _inspection(runtime, directory, base, desired)


def _input_states(directory, files, *, completed=False):
    for item in files:
        actual = _fingerprint(confined(directory, item['path']))
        if actual != item['after'] and (completed or actual != item['before']):
            raise state.StateConflict('Input changed after repair inspection: '+item['path'])


def _replace(source, directory, item, check):
    destination = confined(directory, item['path'])
    actual = _fingerprint(destination)
    if actual == item['after']:
        return
    if actual != item['before']:
        raise state.StateConflict('Input changed before repair publication: '+item['path'])
    expected = (item['after']['sha256'], item['after']['size'])
    if actual is None:
        check()
        _publish_media_file(source, confined(directory, item['path']), expected)
        return
    # Only an inspected damaged file reaches replacement, after its independent
    # recovery copy has been synced and verified. Never delete before copying.
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.h3-repair-', suffix='.part', delete=False) as handle:
        partial = Path(handle.name)
        try:
            with Path(source).open('rb') as incoming:
                shutil.copyfileobj(incoming, handle, 1024*1024)
            handle.flush()
        except BaseException:
            handle.close()
            partial.unlink()
            raise
    try:
        if _fingerprint(partial) != item['after']:
            raise state.StateConflict('Input repair copy failed verification.')
        sync_file(partial)
        check()
        destination = confined(directory, item['path'])
        if _fingerprint(destination) != item['before']:
            raise state.StateConflict('Input changed during repair copy; existing bytes retained.')
        os.replace(partial, destination)
        sync_directory(destination.parent)
    finally:
        if partial.exists():
            partial.unlink()  # Only this call's private publication temporary.


def repair(service, assets, inspection, operation_id, proof):
    if __package__:
        from .project_assets import _catalog_lock, _catalog_file_lock
    else:
        from project_assets import _catalog_lock, _catalog_file_lock
    _require(service, assets)
    directory = _binding(service, assets)
    runtime = service.runtime
    operation = state._token(operation_id)
    if not isinstance(inspection, dict):
        raise ValueError('Repair requires the complete read-only inspection.')
    inspection = copy.deepcopy(inspection)
    if runtime.has_node_proof:
        if proof is not None and proof != runtime.node_write_proof:
            raise ValueError('Input repair cannot change node ownership proof.')
        proof = runtime.node_write_proof
    request = dict(format=FORMAT, operation_id=operation, inspection=inspection)
    pending = confined(directory, '.h3-assets-pending.json')
    path = confined(directory, 'catalog.json')
    confined(directory, 'catalog.json.lock')
    marker = dict(format=FORMAT, operation_id=operation,
        request_sha256=state._hash(state._encode(request)),
        project_sha256=state._hash(str(runtime.project).encode()))
    job = 'project/jobs/asset-repair-'+operation+'.json'
    job_path = confined(runtime.project, job)
    witness_address = 'project_assets/.repairs/'+operation+'.json'
    budget = runtime.store._marker()[0]['path_budget']
    with project_write_guard(runtime.output, runtime.run, proof, 'repair input project assets'):
        _require(service, assets)
        with _catalog_lock(str(path)), _catalog_file_lock(str(path)):
            if pending.exists() and state._decode(state._read_bytes(pending)) != marker:
                raise state.StateConflict('Another input asset operation is pending; finish it first.')
            current = runtime.check()
            if job_path.exists():
                envelope = state._decode(state._read_bytes(job_path))
                intent = envelope.get('value')
                if (not isinstance(intent, dict) or intent.get('request') != request
                        or state._hash(state._encode(intent)) != envelope.get('sha256')):
                    raise state.StateConflict('Repair request identity was reused or its intent is corrupt.')
                base = state.Snapshot(runtime.project, inspection['base'])
                if inspection['input_pin'] != runtime.pin:
                    raise state.StateConflict('Repair retry must use its original input pin.')
                sources, desired, scopes = _sources(runtime, base)
            else:
                if operation in current.state['operations']:
                    raise state.StateConflict('Repair identity already belongs to another operation.')
                base = runtime.accepted
                sources, desired, scopes = _sources(runtime, base)
                runtime.store.validate_snapshot(base, current=current)
                _check_scopes(base, current, scopes)
                if inspection != _inspection(runtime, directory, base, desired):
                    raise state.StateConflict('Input changed since inspection; inspect again before repair.')
                if not inspection['repairable']:
                    raise state.StateConflict('A different readable catalog exists; repair will not overwrite it.')
                state._immutable(runtime.project, 'project/jobs/asset-repair-request-'+operation+'.json',
                    state._encode(request), budget)
                if service.after_stage:
                    service.after_stage('input_repair_requested')
                layout = OrganizedStorageLayout(str(runtime.project), budget)
                preserved = []
                for item in inspection['files']:
                    if item['action'] != 'preserve_restore':
                        continue
                    identity = uuid.uuid5(uuid.UUID(operation), item['path']).hex
                    address = 'project_assets/.repairs/'+operation+'/'+item['path']
                    receipt = runtime.store.stage_payloads([dict(address=address,
                        source=confined(directory, item['path']),
                        target=layout.project_data('assets', 'recovery', identity+'.bin'),
                        scope='project', operation_id=identity, immutable=True)])[0]
                    record = runtime.store._staged_record(receipt)
                    if {key:record['file'][key] for key in ('sha256','size')} != item['before']:
                        raise state.StateConflict('Damaged input changed while preserving its bytes.')
                    preserved.append(dict(path=item['path'], receipt=receipt))
                intent = dict(request=request, preserved=preserved,
                    result=dict(repaired_files=[item['path'] for item in inspection['files'] if item['action'] != 'keep'],
                        preserved_files=[item['path'] for item in preserved], catalog_unchanged=True))
                state._immutable(runtime.project, job,
                    state._encode(dict(value=intent, sha256=state._hash(state._encode(intent)))), budget)
            runtime.store.validate_snapshot(base, current=current)
            receipt = current.state['operations'].get(operation)
            if receipt is not None:
                saved = runtime.store.committed_snapshot(receipt)
                if state._decode(saved.read(witness_address)) != intent:
                    raise state.StateConflict('Accepted repair differs from its prepared intent.')
                for scope, revision in receipt['scope_revisions'].items():
                    if current.state['scope_revisions'].get(scope) != revision:
                        raise state.StateConflict('Project changed after repair; reload before retry.')
            else:
                _check_scopes(base, current, scopes)
            # Re-derive owned paths and desired bytes, rather than trusting
            # paths carried by a client or a damaged prepared request.
            if ({item['path']:item['after'] for item in inspection['files']} != desired
                    or inspection['input_root_sha256'] != state._hash(str(directory).encode())):
                raise state.StateConflict('Repair intent no longer matches its pinned source/input root.')
            preserved = {}
            for item in intent['preserved']:
                record = runtime.store._staged_record(item['receipt'])
                source = confined(runtime.project, record['file']['path'])
                wanted = next(row['before'] for row in inspection['files'] if row['path'] == item['path'])
                if _fingerprint(source) != wanted:
                    raise state.StateConflict('Preserved damaged input failed verification.')
                preserved[item['path']] = record
            if set(preserved) != {item['path'] for item in inspection['files'] if item['action'] == 'preserve_restore'}:
                raise state.StateConflict('Repair is missing its damaged-byte recovery copies.')
            _input_states(directory, inspection['files'], completed=receipt is not None)
            if service.after_stage:
                service.after_stage('input_repair_prepared')
            if receipt is None:
                atomic_json(pending, marker)
                if service.after_stage:
                    service.after_stage('input_repair_pending')
                # Preserve the original catalog encoding exactly, including
                # imported catalogs whose layout predates normalized JSON.
                catalog_source = 'project/jobs/asset-repair-catalog-'+operation+'.json'
                state._immutable(runtime.project, catalog_source, sources['catalog.json'], budget)
                sources['catalog.json'] = confined(runtime.project, catalog_source)
                def check():
                    _require(service, assets)
                    _check_scopes(base, runtime.check(), scopes)
                    if state._decode(state._read_bytes(pending)) != marker:
                        raise state.StateConflict('Input repair coordination marker changed.')
                # Media first, catalog last. The pending marker fences legacy
                # readers and all other edits until both stores are accepted.
                for item in sorted(inspection['files'], key=lambda row: row['path'] == 'catalog.json'):
                    check()
                    _replace(sources[item['path']], directory, item, check)
                    if service.after_stage:
                        service.after_stage('input_repair_file')
                _input_states(directory, inspection['files'], completed=True)
                check()
                controls = {witness_address: dict(data=state._encode(intent),
                    scope='project', category='assets', immutable=True)}
                for record in preserved.values():
                    controls[payload_key(record['address'])] = dict(data=state._encode(record),
                        scope=record['scope'], category='payloads', immutable=True)
                receipt = runtime.store._commit_changes(base, controls, operation_id=operation,
                    read_scopes=scopes, after_stage=service.after_stage)
            runtime.store._acknowledge_commit()
            runtime.record_commit(receipt)
            if service.after_stage:
                service.after_stage('input_repair_committed')
            _input_states(directory, inspection['files'], completed=True)
            if pending.exists():
                if state._decode(state._read_bytes(pending)) != marker:
                    raise state.StateConflict('Input repair marker changed before acknowledgement.')
                pending.unlink()  # Only this verified operation's marker, not user media.
                sync_directory(directory)
            return copy.deepcopy(intent['result'])
