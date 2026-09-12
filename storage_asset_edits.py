"""Recoverable input-catalog edits coordinated with an accepted chain mirror.

The input root is a separate host grant, never derived from a browser payload.
Original editing methods produce the staged catalog and enforce domain rules.
Exact before/after bytes and results are durable before input publication; a
pending marker prevents another edit from stepping across a half-finished one.
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
    from .storage_project_assets import CATALOG, media_entries, catalog_contract
    from .storage_resolver import confined
    from .project_ownership import project_write_guard
    from .processing_persistence import atomic_json, sync_directory, sync_file, publish_new_file
    from .storage_project import _hash_file, _indexed_record, payload_key, PAYLOAD, STAGE
    from .storage_layout import OrganizedStorageLayout
else:
    import storage_state as state
    from storage_project_assets import CATALOG, media_entries, catalog_contract
    from storage_resolver import confined
    from project_ownership import project_write_guard
    from processing_persistence import atomic_json, sync_directory, sync_file, publish_new_file
    from storage_project import _hash_file, _indexed_record, payload_key, PAYLOAD, STAGE
    from storage_layout import OrganizedStorageLayout


FORMAT = 'h3_input_asset_edit_v1'
# Operations execute against a staged catalog, not the input tree.
METHODS = ('update', 'duplicate', 'create_folder', 'update_folder', 'delete_folder',
           'reorder_folders', 'reorder', 'sync_reference_slots', 'import_file',
           'bind_reference_slot', 'register_derived_image', 'derive_image', 'register_model_image',
           'register_captured_frame', 'import_project_asset', 'import_backup_asset', 'delete')


def _require(service, assets):
    service.require_write()
    runtime = service.runtime
    if runtime.asset_input_root is None or runtime.asset_input_root != Path(assets.input_root):
        raise ValueError('Input asset edits require this exact host-granted input root.')


def upload_path(service, assets, filename, proof):
    """Reserve a caller-owned ephemeral upload name, never a catalog/media file."""
    _require(service,assets)
    runtime = service.runtime
    if __package__:
        from .project_assets import ALL_MEDIA_EXTENSIONS
    else:
        from project_assets import ALL_MEDIA_EXTENSIONS
    suffix = Path(str(filename or '')).suffix.lower()
    if suffix not in ALL_MEDIA_EXTENSIONS:
        raise ValueError('Unsupported upload extension.')
    if runtime.has_node_proof:
        if proof is not None and proof != runtime.node_write_proof:
            raise ValueError('Upload staging cannot change node ownership proof.')
        proof = runtime.node_write_proof
    with project_write_guard(runtime.output,runtime.run,proof,'stage an input project asset'):
        _require(service,assets)
        directory = confined(runtime.asset_input_root,'h3_projects/'+runtime.run+'/.uploads')
        directory.mkdir(parents=True,exist_ok=True)
        return str(confined(directory,uuid.uuid4().hex+suffix))


def _raw(path):
    return state._read_bytes(path) if path.exists() else None


def _catalog_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def _check_scopes(base, current, scopes):
    previous = base._validated_root()['scope_revisions']
    latest = current._validated_root()['scope_revisions']
    for scope in scopes:
        if previous.get(scope) != latest.get(scope):
            raise state.StateConflict('Project changed before input asset edit publication; refresh before editing.')


def _prepared_media(runtime, receipts, catalog):
    entries = media_entries(catalog, runtime.run)
    staged = {}
    for receipt in receipts:
        record = runtime.store._staged_record(receipt)
        relative = record['address'].removeprefix('project_assets/')
        if (not record['address'].startswith('project_assets/')
                or relative not in entries or relative in staged or record['scope'] != 'project'
                or record['immutable'] is not True
                or entries[relative] != (record['file']['sha256'], record['file']['size'])):
            raise state.StateConflict('Staged import does not match its catalog.')
        staged[relative] = record
    return staged


def _verify_media(runtime, base, directory, catalog, staged=None):
    """Reject broken inputs/backups before publishing either catalog store."""
    for relative, expected in media_entries(catalog, runtime.run).items():
        source = confined(directory, relative)
        if not source.exists() and relative in (staged or {}):
            source = confined(runtime.project, staged[relative]['file']['path'])
        digest, signature = _hash_file(source)
        if (digest, signature[2]) != expected:
            raise state.StateConflict('Input asset differs from its catalog: '+relative)
        address = 'project_assets/'+relative
        key = payload_key(address)
        if key in base._validated_root()['documents']:
            record = _indexed_record(base, key)
            if (record['file']['sha256'], record['file']['size']) != expected:
                raise state.StateConflict('Accepted asset backup differs from its catalog: '+relative)
            runtime.store.payload_path(base, address, verify=True)


def _publish_media_file(source, destination, expected):
    if destination.exists():
        digest, signature = _hash_file(destination)
        if (digest, signature[2]) != expected:
            raise state.StateConflict('Import destination is occupied; existing bytes retained.')
        sync_directory(destination.parent)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Same-directory publication works even when input/output are separate
        # filesystems. No overwrite is permitted, including a racing writer.
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.h3-', suffix='.part', delete=False) as handle:
            partial = Path(handle.name)
            try:
                with source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, handle, 1024*1024)
                handle.flush()
            except BaseException:
                handle.close()
                partial.unlink()
                raise
        try:
            digest, signature = _hash_file(partial)
            if (digest, signature[2]) != expected:
                raise state.StateConflict('Input import copy failed verification.')
            sync_file(partial)
            publish_new_file(partial, destination)
            sync_directory(destination.parent)
        finally:
            if partial.exists():
                partial.unlink()  # Only this operation's verified local temporary file.
    except FileExistsError:
        # A same-content retry may have completed publication concurrently.
        digest, signature = _hash_file(destination)
        if (digest, signature[2]) != expected:
            raise state.StateConflict('Import destination was occupied during publication.')


def _publish_input_media(runtime, directory, staged):
    for relative, record in staged.items():
        _publish_media_file(confined(runtime.project, record['file']['path']),
                            confined(directory, relative),
                            (record['file']['sha256'], record['file']['size']))


def edit_catalog(service, assets, method, inputs, operation, proof):
    if __package__:
        from .project_assets import ProjectAssetStore, _safe_project, _catalog_lock, _catalog_file_lock
    else:
        from project_assets import ProjectAssetStore, _safe_project, _catalog_lock, _catalog_file_lock
    _require(service, assets)
    runtime = service.runtime
    operation = state._token(operation)
    if (method.__name__ not in METHODS
            or getattr(ProjectAssetStore, method.__name__).__wrapped__ is not method):
        raise ValueError('This asset operation has no input/media transaction port yet.')
    inputs = copy.deepcopy(inputs)
    for key in ('source_path', 'rendered_path'):
        if key in inputs:
            inputs[key] = os.path.realpath(os.path.abspath(os.path.expanduser(str(inputs[key] or '').strip())))
    if _safe_project(inputs['project']) != runtime.run:
        raise ValueError('Input asset edit belongs to a different project.')
    if runtime.has_node_proof:
        if proof is not None and proof != runtime.node_write_proof:
            raise ValueError('Input asset edit cannot change node ownership proof.')
        proof = runtime.node_write_proof
    directory = confined(runtime.asset_input_root, 'h3_projects/'+runtime.run)
    path = confined(directory, 'catalog.json')
    pending = confined(directory, '.h3-assets-pending.json')
    confined(directory, 'catalog.json.lock')
    intent_address = 'project/jobs/asset-edit-'+operation+'.json'
    intent_path = confined(runtime.project,intent_address)
    with project_write_guard(runtime.output,runtime.run,proof,'edit input project assets'):
        _require(service,assets)
        with _catalog_lock(str(path)), _catalog_file_lock(str(path)):
            request_inputs = copy.deepcopy(inputs)
            if inputs.get('source_kind') == 'upload' and 'source_path' in inputs:
                # HTTP retries re-upload to a fresh private temporary name.
                # The original filename and exact bytes, not that random path,
                # identify the request. Never trust a browser-supplied digest.
                digest, signature = _hash_file(Path(inputs['source_path']))
                request_inputs['source_path'] = dict(sha256=digest,size=signature[2],
                    suffix=Path(inputs['source_path']).suffix.lower())
            expected = dict(format=FORMAT, operation_id=operation, input_pin=runtime.pin,
                input_root_sha256=state._hash(str(runtime.asset_input_root).encode()),
                method=method.__name__, inputs=request_inputs)
            request_hash = state._hash(state._encode(expected))
            marker = dict(format=FORMAT,operation_id=operation,request_sha256=request_hash,
                          project_sha256=state._hash(str(runtime.project).encode()))
            if pending.exists() and state._decode(state._read_bytes(pending)) != marker:
                raise state.StateConflict('Another input asset edit is pending; finish that exact operation first.')
            current = runtime.check()
            if intent_path.exists():
                envelope = state._decode(state._read_bytes(intent_path))
                intent = envelope.get('value')
                if (not isinstance(intent,dict) or state._hash(state._encode(intent)) != envelope.get('sha256')
                        or intent.get('request') != expected):
                    raise state.StateConflict('Input asset operation identity was reused or its intent is corrupt.')
            else:
                base = runtime.accepted
                runtime.store.validate_snapshot(base,current=current)
                descriptor = base.state['documents'].get(CATALOG)
                contract = catalog_contract(descriptor)
                _check_scopes(base,current,{'project',contract['scope']})
                before = _raw(path)
                saved = base.read(CATALOG) if descriptor is not None else None
                if before != saved:
                    raise state.StateConflict('Input catalog differs from the accepted backup; refresh/recover it before editing.')
                catalog = state._decode(before) if before is not None else ProjectAssetStore._empty_catalog(runtime.run)
                media_entries(catalog,runtime.run)
                if before is not None:
                    catalog = ProjectAssetStore._normalize_catalog(catalog,runtime.run)
                # Media producers perform I/O before the final catalog intent.
                # Fence this earlier phase too: changed parameters or source
                # bytes must never reuse already reserved media identities.
                source_files = {}
                for key in ('source_path','rendered_path'):
                    if key in inputs:
                        digest, signature = _hash_file(Path(inputs[key]))
                        source_files[key] = dict(sha256=digest,size=signature[2])
                state._immutable(runtime.project,'project/jobs/asset-request-'+operation+'.json',
                    state._encode(dict(request=expected,source_files=source_files)),
                    runtime.store._marker()[0]['path_budget'])
                if service.after_stage:
                    service.after_stage('input_edit_requested')
                port = _CatalogEdit(service,assets,catalog,has_catalog=before is not None,
                                    base=base,operation=operation,
                                    sources={inputs[key]:value for key,value in source_files.items()})
                staged = ProjectAssetStore(assets.input_root,assets.output_root)
                staged._catalog_edit = port
                try:
                    result = method(staged,**inputs)
                finally:
                    port.closed = True
                after = _catalog_bytes(port.catalog)
                intent = dict(request=expected,base=base.reference,before=before.decode() if before is not None else None,
                              after=after.decode(),result=result,media=list(port.media.values()))
                state._immutable(runtime.project,intent_address,
                    state._encode(dict(value=intent,sha256=state._hash(state._encode(intent)))),
                    runtime.store._marker()[0]['path_budget'])
            if service.after_stage:
                service.after_stage('input_edit_prepared')
            before = intent['before'].encode() if intent['before'] is not None else None
            after = intent['after'].encode()
            base = state.Snapshot(runtime.project,intent['base'])
            runtime.store.validate_snapshot(base,current=current)
            receipt = current.state['operations'].get(operation)
            if receipt is not None:
                saved = runtime.store.committed_snapshot(receipt)
                if state._decode(saved.read('project_assets/.edits/'+operation+'.json')) != intent:
                    raise state.StateConflict('Accepted input asset edit does not match its prepared result.')
                for scope, revision in receipt['scope_revisions'].items():
                    if current._validated_root()['scope_revisions'].get(scope) != revision:
                        raise state.StateConflict('Project changed after input asset edit; reload before retry.')
            else:
                contract = catalog_contract(base.state['documents'].get(CATALOG))
                _check_scopes(base,current,{'project',contract['scope']})
            actual = _raw(path)
            if actual not in (before,after) or (receipt is not None and actual != after):
                raise state.StateConflict('Input catalog changed after this edit; no newer catalog was overwritten.')
            staged_media = intent.get('media', [])
            staged = _prepared_media(runtime, staged_media, state._decode(after))
            _verify_media(runtime, saved if receipt is not None else base,
                          directory, state._decode(after), staged)
            if not pending.exists():
                atomic_json(pending,marker)
            if service.after_stage:
                service.after_stage('input_edit_pending')
            _publish_input_media(runtime,directory,staged)
            if service.after_stage:
                service.after_stage('input_media_published')
            if actual != after:
                atomic_json(path,state._decode(after))
            if _raw(path) != after:
                raise state.StateConflict('Input catalog publication did not produce the prepared bytes.')
            if service.after_stage:
                service.after_stage('input_edit_published')
            # Mirror and edit witness are accepted together. On interruption,
            # the pending marker and immutable intent preserve the exact result.
            service._refresh(assets.input_root,operation,proof,edit_witness=intent,staged_media=staged_media)
            if _raw(path) != after or state._decode(state._read_bytes(pending)) != marker:
                raise state.StateConflict('Input asset state changed before its acknowledgement.')
            _require(service,assets)
            if service.after_stage:
                service.after_stage('input_edit_committed')
            if method.__name__ == 'delete':
                # Only the exact removed, unshared input file can disappear.
                # Its accepted immutable backup and edit receipt remain. A
                # changed file is retained, never removed using stale metadata.
                removed = intent['result']['asset']
                relative = removed.get('relative_path')
                if relative and relative not in media_entries(state._decode(after), runtime.run):
                    target = confined(directory, relative)
                    if target.exists():
                        backup = runtime.store.payload_path(base, 'project_assets/'+relative, verify=True)
                        expected_media = (removed['sha256'], removed['size'])
                        digest, signature = _hash_file(backup)
                        if (digest, signature[2]) != expected_media:
                            raise state.StateConflict('Deleted asset recovery copy does not match its identity.')
                        digest, signature = _hash_file(target)
                        if (digest, signature[2]) == expected_media:
                            target.unlink()
                            sync_directory(target.parent)
            pending.unlink()  # Only our verified coordination marker; intent/result remain retained.
            sync_directory(directory)
            return copy.deepcopy(intent['result'])


class _CatalogEdit:
    def __init__(self,service,assets,catalog,*,has_catalog,base,operation,sources):
        self.service,self.assets = service,assets
        self.catalog = copy.deepcopy(catalog)
        self.closed = False
        self.has_catalog = has_catalog
        self.base,self.operation = base,operation
        self.media = {}
        self.sources = sources

    def check(self,project,method):
        _require(self.service,self.assets)
        if self.closed or project != self.service.runtime.run or method not in (*METHODS,'load','_save_catalog','asset','upload_path'):
            raise ValueError('Asset catalog staging escaped its exact operation.')

    def load(self,*,create=False):
        self.check(self.service.runtime.run,'load')
        return copy.deepcopy(self.catalog)

    def source_bundle(self, project, identity, pin, *, tracks):
        self.check(self.service.runtime.run, 'import_project_asset')
        if __package__:
            from .storage_asset_sources import source_for
        else:
            from storage_asset_sources import source_for
        runtime = self.service.runtime
        source = source_for(runtime, project, pin)
        bundle = source.read(runtime, identity, tracks=tracks)
        for item in bundle['media'].values():
            entry = item['entry']
            self.sources[item['path']] = dict(sha256=entry['sha256'], size=entry['size'])
        return bundle

    def import_media(self, source, relative, digest, size):
        runtime = self.service.runtime
        self.check(runtime.run,'import_file')
        if str(source) in self.sources and self.sources[str(source)] != dict(sha256=digest,size=size):
            raise state.StateConflict('Source changed after this input asset request was prepared.')
        address = 'project_assets/'+relative
        key = payload_key(address)
        if key in self.base._validated_root()['documents']:
            record = _indexed_record(self.base,key)
            if (record['file']['sha256'],record['file']['size']) != (digest,size):
                raise state.StateConflict('Existing asset address has different media; original retained.')
            return str(runtime.store.payload_path(self.base,address,verify=True))
        if relative not in self.media:
            identity = uuid.uuid5(uuid.UUID(self.operation),address).hex
            layout = OrganizedStorageLayout(str(runtime.project),runtime.store._marker()[0]['path_budget'])
            target = layout.project_data('assets','media',identity+Path(relative).suffix.lower())
            reservation = confined(runtime.project,'project/jobs/payload-'+identity+'.json')
            if reservation.exists():
                intent = state._decode(state._read_bytes(reservation))
                record = dict(format=PAYLOAD,address=address,scope='project',immutable=True,
                              file=dict(path=target,sha256=digest,size=size))
                if intent.get('format') != STAGE or intent.get('record') != record:
                    raise state.StateConflict('Reserved import does not match this exact media request.')
                # A derived image/upload can have a new ephemeral source name.
                # Complete ONLY this operation's reservation with identical
                # verified bytes. The generic payload source-path fence stays
                # unchanged; an unreserved destination is never adopted here.
                _publish_media_file(Path(source),confined(runtime.project,target),(digest,size))
                receipt = dict(operation_id=identity,record=record)
            else:
                receipt = runtime.store.stage_payload(address,source,target,scope='project',operation_id=identity,immutable=True)
            self.media[relative] = receipt
        record = runtime.store._staged_record(self.media[relative])
        if (record['file']['sha256'],record['file']['size']) != (digest,size):
            raise state.StateConflict('Imported source changed while staging.')
        if self.service.after_stage:
            self.service.after_stage('input_media_staged')
        return str(confined(runtime.project,record['file']['path']))

    def render_path(self, project, filename):
        self.check(project,'upload_path')
        if Path(str(filename)).suffix.lower() != '.png':
            raise ValueError('Catalog-staged image rendering requires PNG output.')
        runtime = self.service.runtime
        layout = OrganizedStorageLayout(str(runtime.project),runtime.store._marker()[0]['path_budget'])
        path = confined(runtime.project,layout.project_data('assets','renders',uuid.uuid4().hex+'.png'))
        path.parent.mkdir(parents=True,exist_ok=True)
        return str(path)

    def asset_path(self, project, entry):
        self.check(project,'asset')
        if entry not in self.catalog['assets']:
            raise ValueError('Asset does not belong to the staged catalog.')
        relative = entry['relative_path']
        if relative in self.media:
            record = self.service.runtime.store._staged_record(self.media[relative])
            path = confined(self.service.runtime.project,record['file']['path'])
        else:
            record = _indexed_record(self.base,payload_key('project_assets/'+relative))
            path = self.service.runtime.store.payload_path(self.base,'project_assets/'+relative,verify=True)
        if (record['file']['sha256'],record['file']['size']) != (entry['sha256'],entry['size']):
            raise state.StateConflict('Staged asset lookup does not match its media identity.')
        return str(path)

    def save(self,document,expected_revision):
        self.check(self.service.runtime.run,'_save_catalog')
        if str(self.catalog.get('storage_revision') or '') != expected_revision:
            raise state.StateConflict('Staged asset catalog changed between nested edits.')
        media_entries(document,self.service.runtime.run)
        self.catalog = copy.deepcopy(document)
        self.has_catalog = True
        return copy.deepcopy(self.catalog)
