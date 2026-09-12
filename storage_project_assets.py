"""Pinned asset reads and atomic recovery-mirror refresh on hosted copies.

ComfyUI input originals remain authoritative. Refresh captures their exact
catalog and verified media. Input metadata editing delegates to the separate
two-store coordinator and input-root grant. Media creation/previews, missing
input restoration and production activation are not enabled by these ports.
"""
import copy
import json
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project import payload_key, _indexed_record, _hash_file
    from .storage_resolver import confined, artifact_address
    from .storage_layout import OrganizedStorageLayout
    from .project_ownership import project_write_guard
else:
    import storage_state as state
    from storage_project import payload_key, _indexed_record, _hash_file
    from storage_resolver import confined, artifact_address
    from storage_layout import OrganizedStorageLayout
    from project_ownership import project_write_guard


CATALOG = 'project_assets/catalog.json'
FORMAT = 'h3_project_asset_mirror_operation_v1'


def catalog_contract(descriptor):
    if descriptor is None:
        return dict(scope='project', category='assets', immutable=False)
    contract = {key:descriptor[key] for key in ('scope','category','immutable')}
    # Earlier importers retained the exact catalog as an opaque legacy file.
    # Keep that contract and version its bytes with a replacement witness;
    # never change an immutable descriptor into a mutable one during refresh.
    if ((contract['scope'], contract['category']) == ('project','assets')
            or contract == dict(scope='archive:legacy',category='legacy',immutable=True)):
        return contract
    raise ValueError('Unsupported project asset catalog import contract.')


def media_entries(catalog, run):
    """Validate catalogue identity and exact, deduplicated owned media paths."""
    if (not isinstance(catalog, dict) or catalog.get('format') != 'h3_project_assets_v1'
            or type(catalog.get('version')) is not int or catalog['version'] != 1
            or catalog.get('project') != run or not isinstance(catalog.get('assets'), list)
            or len(catalog['assets']) > 512):
        raise ValueError('Invalid accepted project asset catalog.')
    entries, folded, ids = {}, {}, set()
    groups = {'image': 'images', 'video': 'videos', 'audio': 'audio'}
    for entry in catalog['assets']:
        if not isinstance(entry, dict):
            raise ValueError('Invalid project asset record.')
        identity = entry.get('id')
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError('Project asset IDs must be unique nonempty strings.')
        ids.add(identity)
        relative = artifact_address(entry.get('relative_path'))
        if (relative != entry['relative_path'] or len(relative.split('/')) != 2
                or relative.split('/')[0] != groups.get(entry.get('kind'))):
            raise ValueError('Invalid project asset media address.')
        if (not isinstance(entry.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                or type(entry.get('size')) is not int or entry['size'] < 0):
            raise ValueError('Invalid project asset media fingerprint.')
        witness = (entry['sha256'], entry['size'])
        if (relative.casefold() in folded and folded[relative.casefold()] != relative
                or relative in entries and entries[relative] != witness):
            raise ValueError('Conflicting project asset media identities.')
        entries[relative], folded[relative.casefold()] = witness, relative
    return entries


def accepted_catalog(view):
    session = view._session()
    if CATALOG not in session['controls']:
        return None
    descriptor = session['controls'][CATALOG]
    catalog_contract(descriptor)
    catalog = view.read(view.project/CATALOG)
    media_entries(catalog, view.project.name)
    return catalog


def plan_asset_inputs(store, values):
    """Host-granted Plan adapter: project-global assets keep the exact root.

    The Carousel has no branch selector. Its catalog can feed another Plan
    branch, but cannot carry that branch's authoring or silently follow latest.
    Verify the complete catalog against its pin before changing only the branch
    selector; the normal mixed-root gate still checks all other node inputs.
    """
    if __package__:
        from .storage_runtime import runtime_access
        from .project_assets import ProjectAssetStore
        from .branch_scope import branch_id
    else:
        from storage_runtime import runtime_access
        from project_assets import ProjectAssetStore
        from branch_scope import branch_id
    record = values.get('project_assets')
    if not isinstance(record, dict) or '_storage_pin' not in record:
        return values
    pin = record['_storage_pin']
    if (record.get('format') != 'h3_project_assets_v1' or record.get('project') != store.project.name
            or not isinstance(pin, dict) or record.get('_branch_id') != pin.get('branch_id')):
        raise ValueError('Invalid pinned project asset envelope.')
    selected = values.get('working_branch_id', 'main')
    authored = values.get('plan_json_input') or values.get('plan_json')
    if isinstance(authored, str):
        authored = json.loads(authored)
    if isinstance(authored, dict):
        selected = authored.get('_branch_id', selected)
    if isinstance(values.get('plan'), dict):
        selected = values['plan'].get('_branch_id', selected)
    selected = branch_id(selected)
    with runtime_access(store, pin=pin, selected=branch_id(pin.get('branch_id'))) as bound:
        catalog = accepted_catalog(bound.reader)
        if catalog is None or ProjectAssetStore._normalize_catalog(catalog, bound.run) != record.get('catalog'):
            raise state.StateConflict('Project asset envelope differs from its accepted catalog.')
    return dict(values, project_assets=dict(record, _branch_id=selected,
                _storage_pin=dict(pin, branch_id=selected)))


def accepted_asset_path(view, entry, *, verify=True):
    catalog = accepted_catalog(view)
    if catalog is None or entry not in catalog['assets']:
        raise ValueError('Project asset does not belong to the accepted catalog.')
    address = 'project_assets/'+entry['relative_path']
    snapshot = view._session()['snapshot']
    key = payload_key(address)
    if key not in snapshot._validated_root()['documents']:
        raise FileNotFoundError('Accepted project asset backup is missing: '+address)
    record = _indexed_record(snapshot, key)
    if record['file']['sha256'] != entry['sha256'] or record['file']['size'] != entry['size']:
        raise state.StateConflict('Project asset backup differs from its accepted catalog.')
    view._track(address)
    return view.store.payload_path(snapshot, address, verify=verify)


class RuntimeProjectAssets:
    def __init__(self, runtime):
        self.runtime = runtime
        self.after_stage = None

    def require_write(self):
        runtime = self.runtime
        runtime.check()
        if not runtime.asset_writes or runtime.node_asset_write.get() is False:
            raise ValueError('Runtime binding is read-only; asset backup writes were not enabled.')

    def refresh(self, input_root, operation_id, proof):
        runtime = self.runtime
        self.require_write()
        operation = state._token(operation_id)
        if runtime.has_node_proof:
            if proof is not None and proof != runtime.node_write_proof:
                raise ValueError('Asset refresh cannot change node ownership proof.')
            proof = runtime.node_write_proof
        with project_write_guard(runtime.output, runtime.run, proof, 'refresh project asset backup'):
            pending = confined(input_root, 'h3_projects/'+runtime.run+'/.h3-assets-pending.json')
            if pending.exists():
                raise state.StateConflict('Finish the pending input asset edit before refreshing its backup.')
            return self._refresh(input_root, operation, proof)

    def edit(self, assets, method, inputs, operation, proof):
        if __package__:
            from .storage_asset_edits import edit_catalog
        else:
            from storage_asset_edits import edit_catalog
        return edit_catalog(self, assets, method, inputs, operation, proof)

    def upload_path(self, assets, filename, proof):
        if __package__:
            from .storage_asset_edits import upload_path
        else:
            from storage_asset_edits import upload_path
        return upload_path(self,assets,filename,proof)

    def inspect_inputs(self, assets):
        if __package__:
            from .storage_asset_repair import inspect_inputs
        else:
            from storage_asset_repair import inspect_inputs
        return inspect_inputs(self, assets)

    def repair_inputs(self, assets, inspection, operation, proof):
        if __package__:
            from .storage_asset_repair import repair
        else:
            from storage_asset_repair import repair
        return repair(self, assets, inspection, operation, proof)

    def _refresh(self, input_root, operation, proof, *, edit_witness=None, staged_media=()):
        runtime = self.runtime
        current = runtime.check()
        source_root = confined(input_root, 'h3_projects/'+runtime.run)
        source_catalog = confined(source_root, 'catalog.json')
        # Never call legacy load(): it may repair the primary from a mirror.
        raw = state._read_bytes(source_catalog)
        catalog = state._decode(raw)
        entries = media_entries(catalog, runtime.run)
        expected = dict(format=FORMAT, operation_id=operation, input_pin=runtime.pin,
            source_root_sha256=state._hash(str(source_root).encode()),
            catalog_sha256=state._hash(raw))
        if edit_witness is not None:
            expected['input_edit_sha256'] = state._hash(state._encode(edit_witness))
        witness_address = 'project_assets/.operations/'+operation+'.json'
        receipt = current.state['operations'].get(operation)
        if receipt is not None:
            saved = runtime.store.committed_snapshot(receipt)
            witness = state._decode(saved.read(witness_address))
            if any(witness.get(key) != value for key, value in expected.items()):
                raise state.StateConflict('Asset backup operation was reused with different inputs.')
            for scope, revision in receipt['scope_revisions'].items():
                if current.state['scope_revisions'].get(scope) != revision:
                    raise state.StateConflict('Project changed after asset backup; reload before retry.')
            # A reply is only recoverable while the accepted backup is intact.
            for relative, (digest, size) in entries.items():
                address = 'project_assets/'+relative
                record = _indexed_record(saved, payload_key(address))
                if (record['file']['sha256'], record['file']['size']) != (digest, size):
                    raise state.StateConflict('Accepted asset backup receipt has different media.')
                runtime.store.payload_path(saved, address, verify=True)
            runtime.store._acknowledge_commit()
            runtime.record_commit(receipt)
            return copy.deepcopy(witness['result'])

        base = runtime.accepted
        runtime.store.validate_snapshot(base, current=current)
        descriptor = base.state['documents'].get(CATALOG)
        contract = catalog_contract(descriptor)
        replaced = {CATALOG:descriptor} if descriptor is not None and descriptor['immutable'] else {}
        intent = dict(expected, base=base.reference)
        intent_address = 'project/jobs/assets-'+operation+'.json'
        state._immutable(runtime.project, intent_address, state._encode(intent),
                         runtime.store._marker()[0]['path_budget'])
        if self.after_stage:
            self.after_stage('asset_intent')
        scopes, requests = {'project',contract['scope']}, []
        prepared = {}
        for receipt in staged_media:
            record = runtime.store._staged_record(receipt)
            relative = record['address'].removeprefix('project_assets/')
            if (not record['address'].startswith('project_assets/')
                    or relative not in entries or relative in prepared or record['scope'] != 'project'
                    or record['immutable'] is not True
                    or entries[relative] != (record['file']['sha256'],record['file']['size'])):
                raise state.StateConflict('Prepared import differs from the asset mirror catalog.')
            prepared[relative] = receipt
        layout = OrganizedStorageLayout(str(runtime.project), runtime.store._marker()[0]['path_budget'])
        for relative, (digest, size) in sorted(entries.items()):
            source = confined(source_root, relative)
            actual, signature = _hash_file(source)
            if (actual, signature[2]) != (digest, size):
                raise state.StateConflict('Input asset differs from its catalog: '+relative)
            address = 'project_assets/'+relative
            key = payload_key(address)
            if key in base.state['documents']:
                record = _indexed_record(base, key)
                scopes.add(record['scope'])
                if (record['file']['sha256'], record['file']['size']) != (digest, size):
                    raise state.StateConflict('An existing backup has different content at '+relative)
                runtime.store.payload_path(base, address, verify=True)
                continue
            if relative in prepared:
                continue
            identity = uuid.uuid5(uuid.UUID(operation), address).hex
            target = layout.project_data('assets', 'media', identity+Path(relative).suffix.lower())
            requests.append(dict(address=address, source=source, target=target,
                scope='project', operation_id=identity, immutable=True))
        staged = list(prepared.values()) + (runtime.store.stage_payloads(requests) if requests else [])
        for receipt in staged:
            record = runtime.store._staged_record(receipt)
            wanted = entries[record['address'].removeprefix('project_assets/')]
            if (record['file']['sha256'], record['file']['size']) != wanted:
                raise state.StateConflict('Copied asset differs from its catalog.')
        if self.after_stage:
            self.after_stage('asset_payloads')
        if state._read_bytes(source_catalog) != raw:
            raise state.StateConflict('Input asset catalog changed during backup capture.')
        result = dict(catalog=catalog, catalog_sha256=expected['catalog_sha256'],
            asset_count=len(catalog['assets']), media_file_count=len(entries),
            copied_media_files=len(staged), retained_previous_media=True)
        witness = dict(intent, result=result, replaced=replaced)
        controls = {CATALOG: dict(data=raw, **contract),
            witness_address: dict(data=state._encode(witness), scope='project', category='assets', immutable=True)}
        if edit_witness is not None:
            controls['project_assets/.edits/'+operation+'.json'] = dict(
                data=state._encode(edit_witness), scope='project', category='assets', immutable=True)
        for item in staged:
            record = runtime.store._staged_record(item)
            controls[payload_key(record['address'])] = dict(data=state._encode(record),
                scope=record['scope'],category='payloads',immutable=record['immutable'])
        self.require_write()
        receipt = runtime.store._commit_changes(base, controls, operation_id=operation,
            read_scopes=scopes, replace_documents=replaced, after_stage=self.after_stage)
        runtime.record_commit(receipt)
        return result
