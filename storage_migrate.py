"""Offline, resumable migration coordinator. Never replaces the source project.

Run preview, copy, verify, then activate the independent destination. Existing
addresses inside metadata stay byte-for-byte unchanged; the runtime resolves
them through the accepted index. ComfyUI must be stopped while switching its
output directory (or moving the independently verified project into place).
"""
import json
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state, storage_resolver as resolver, storage_recovery as recovery
    from . import storage_project_migration as migration, storage_ownership_recovery as ownership
    from .storage_project import ProjectStore, payload_catalog
    from .storage_layout import OrganizedStorageLayout, storage_stage
    from .storage_branch_controls import BranchControlDocuments
    from .storage_handoff_controls import HandoffControlDocuments
    from .storage_host import ACTIVATION, FORMAT as ACTIVE_FORMAT
else:
    import storage_state as state
    import storage_resolver as resolver
    import storage_recovery as recovery
    import storage_project_migration as migration
    import storage_ownership_recovery as ownership
    from storage_project import ProjectStore, payload_catalog
    from storage_layout import OrganizedStorageLayout, storage_stage
    from storage_branch_controls import BranchControlDocuments
    from storage_handoff_controls import HandoffControlDocuments
    from storage_host import ACTIVATION, FORMAT as ACTIVE_FORMAT

FORMAT = 'h3_offline_migration_v1'


def _id(value):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'h3-import:'+value).hex


def control_contract(address):
    try:
        return BranchControlDocuments._contract(address)
    except ValueError:
        pass
    if address.startswith('orchestration/'):
        return HandoffControlDocuments.contract(address)
    branch, relative = 'main', address
    match = re.fullmatch(r'branches/([0-9a-f]{32})/(.+)', address)
    if match:
        branch, relative = match.groups()
    if relative in ('workflow.json', 'api_prompt.json', 'plan_studio_presentation.json', 'manifest.json'):
        return 'branch:'+branch, 'branches', False
    take = re.fullmatch(r'(?:checkpoints|segments)/clip_\d{4}\.([0-9a-f]{32})\.(?:json|prompt\.txt)', relative)
    if take:
        return 'archive:'+take[1], 'takes', True
    processing = re.fullmatch(r'((?:chapters/[^/]+/)?upscaled/[^/]+)/(.*)', relative)
    if processing:
        profile, tail = processing.groups()
        scope = 'pass:'+state._hash(state._encode([branch, profile]))[:32]
        mutable = tail in ('upscale_manifest.json', 'partial.json') or bool(
            re.fullmatch(r'checkpoints/clip_\d{4}\.json', tail))
        return scope, 'passes', not mutable
    if address == 'project_assets/catalog.json':
        return 'project', 'assets', False
    if relative.startswith('prompt_history/'):
        return 'history:'+branch, 'history', not relative.endswith('/index.json')
    if re.fullmatch(r'chapters/[^/]+/chapter\.json', relative):
        return 'branch:'+branch, 'cuts', False
    if re.fullmatch(r'chapter_heads/\d{4,}\.json', relative):
        return 'branch:'+branch, 'cuts', False
    if relative.startswith('reference_cache/'):
        return 'cache:'+_id(address), 'reference_cache', True
    return 'archive:legacy', 'legacy', True


def _inventory(source, rows, destination, budget):
    prefix = 'h3_chains/'+source.name+'/'
    files = {row['target'].removeprefix(prefix):row for row in rows if row['role'] != 'authority'}
    layout = OrganizedStorageLayout(str(destination), budget)
    controls, targets, metadata = {}, {}, {}
    for address, row in files.items():
        if row['role'] == 'control':
            scope, category, immutable = control_contract(address)
            controls[address] = dict(source=row['source'], sha256=row['sha256'],
                scope=scope, category=category, immutable=immutable)
            if address.endswith('.json') and row['size'] <= 16*1024*1024:
                # Only known metadata fields below drive media placement.
                # Authoring/embedded workflow strings are never rewritten.
                try:
                    value = state._decode(resolver.confined(source, row['source']).read_bytes())
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict):
                    metadata[address] = value
    def put(address, target, scope):
        if address in files and files[address]['role'] == 'payload' and address not in targets:
            layout.check_budget(target)
            targets[address] = dict(target=target, scope=scope, immutable=True)
    for address, value in sorted(metadata.items()):
        segment = value.get('segment')
        if (not isinstance(segment, dict) or not re.search(r'clip_\d{4}\.[0-9a-f]{32}\.json$', address)
                or value.get('format') not in ('h3_chain_segment_v3', 'h3_chain_upscale_segment_v1')):
            continue
        processed = value['format'] == 'h3_chain_upscale_segment_v1'
        stage = storage_stage(profile_config=value.get('profile_config')) if processed else storage_stage(
            take_kind=segment.get('take_kind'))
        take = _id(address)
        paths = layout.media(stage, take, pass_id=_id(str(Path(address).parent.parent)) if processed else None)
        scope = controls[address]['scope']
        for field, role in (('segment','video'), ('checkpoint','checkpoint'),
                            ('generated_audio','audio'), ('blend_segment','overlap')):
            logical = segment.get(field)
            if isinstance(logical, str) and logical.startswith(prefix):
                put(logical[len(prefix):], paths[role], scope)
    for address, value in metadata.items():
        if value.get('format') not in ('h3_video_png_sequence_v1', 'h3_chain_png_export_v1'):
            continue
        parent = str(Path(address).parent)
        for logical in files:
            if logical.startswith(parent+'/'):
                suffix = logical[len(parent)+1:]
                put(logical, layout.export('png', _id(parent))+'/'+suffix, 'exports:png_'+_id(parent))
    for address, row in files.items():
        if row['role'] != 'payload' or address in targets:
            continue
        suffix = Path(address).suffix
        suffix = suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,12}', suffix) else '.bin'
        if address.startswith('project_assets/'):
            target = layout.project_data('assets', 'media', _id(address)+suffix)
            scope = 'project'
        elif address.startswith('reference_cache/'):
            target = layout.project_data('reference_cache', 'objects', _id(address)+suffix)
            scope = 'cache:'+_id(address)
        elif '/final/' in address or address.startswith('final/'):
            target = layout.export('video', _id(str(Path(address).parent)))+'/'+_id(address)+suffix
            scope = 'exports:video_'+_id(str(Path(address).parent))
        elif address.startswith(('.plan_studio_thumbnails/', '.plan_studio_source_previews/')):
            target = layout.optional('previews', _id(address)+suffix)
            scope = 'archive:legacy'
        else:
            target = layout.project_data('legacy', _id(address)+suffix)
            scope = 'archive:legacy'
        put(address, target, scope)
    if len({item['target'].casefold() for item in targets.values()}) != len(targets):
        raise ValueError('Migration contains colliding output paths.')
    return controls, targets


def prepare(source, workspace, *, path_budget=240):
    source, workspace = Path(source).absolute(), Path(workspace).absolute()
    if (source.parent.name != 'h3_chains' or not source.is_dir()
            or workspace.exists() or workspace.is_relative_to(source.parent.parent)
            or source.is_relative_to(workspace)):
        raise ValueError('Choose a new migration workspace outside the source output directory.')
    resolver.confined(source, 'storage.json')
    resolver.confined(workspace, 'migration.json')
    with resolver.rehearsal_access(source):
        rows, locks = recovery._capture(source)
    external = ownership.capture(source)
    destination = workspace/'output/h3_chains'/source.name
    controls, targets = _inventory(source, rows, destination, path_budget)
    if not controls:
        raise ValueError('Source has no H3 project control records.')
    value = dict(format=FORMAT, source=str(source), workspace=str(workspace),
        path_budget=path_budget, rows=rows, excluded_locks=locks,
        controls=controls, targets=targets, external_ownership=external)
    workspace.mkdir(mode=0o700, parents=True)
    state._immutable(workspace, 'migration.json', state._encode(value), path_budget)
    receipt = dict(format='h3_offline_migration_source_v1', source=str(source),
                   source_manifest_sha256=state._hash(state._encode(value)))
    state._immutable(workspace, 'source.json', state._encode(receipt), path_budget)
    return summary(value)


def summary(value):
    return dict(workspace=value['workspace'], source=value['source'],
        destination=str(Path(value['workspace'])/'output/h3_chains'/Path(value['source']).name),
        controls=len(value['controls']), payloads=len(value['targets']),
        payload_bytes=sum(row['size'] for row in value['rows'] if row['role'] == 'payload'),
        source_preserved=True)


def _load(workspace):
    workspace = Path(workspace).absolute()
    source, _ = migration._copy_root(workspace/'source.json')
    value = state._decode((workspace/'migration.json').read_bytes())
    if value.get('format') != FORMAT or value['workspace'] != str(workspace) or value['source'] != str(source):
        raise ValueError('Migration inventory does not match this workspace.')
    return workspace, source, value


def copy_project(workspace, *, after_copy=None):
    workspace, source, value = _load(workspace)
    output = workspace/'output'
    destination = output/'h3_chains'/source.name
    if (destination/ACTIVATION).exists():
        raise ValueError('Project is already activated; do not replay its initial migration.')
    recovery._verify_source(value, source)
    if not output.exists():
        control = state.create_control_rehearsal(workspace/'source.json', output, value['controls'],
            path_budget=value['path_budget'], commit_protocol='immutable_slots_v1')
    else:
        control = state.ControlStore(destination)
    with state.control_rehearsal_access(destination):
        journal = workspace/'join-owned/journal.json'
        if not journal.exists():
            migration.prepare_join(workspace/'source.json', control, journal.parent, value['targets'])
        migration.join_payloads(journal, after_copy=after_copy)
        ownership.copy_files(source, output, journal.parent, value)
        recovery._verify_source(value, source)
    return summary(value)


def verify(workspace):
    workspace, source, value = _load(workspace)
    destination = workspace/'output/h3_chains'/source.name
    with state.control_rehearsal_access(destination):
        store = ProjectStore(destination)
        snapshot = store.snapshot()
        snapshot.verify()
        if store.verify_payloads(snapshot) != len(value['targets']):
            raise ValueError('Migrated payload count differs from the frozen source.')
        for address, entry in value['controls'].items():
            if state._hash(snapshot.read(address)) != entry['sha256']:
                raise ValueError('Migrated control differs from the frozen source: '+address)
        recovery._verify_source(value, source)
        receipt = dict(format='h3_offline_migration_verified_v1', root=snapshot.reference,
            bootstrap_sha256=state._hash((destination/'storage.json').read_bytes()),
            inventory_sha256=state._hash((workspace/'migration.json').read_bytes()))
        state._immutable(workspace, 'verified.json', state._encode(receipt), value['path_budget'])
    return dict(summary(value), verified=True)


def activate(workspace):
    workspace, source, value = _load(workspace)
    destination = workspace/'output/h3_chains'/source.name
    verified = state._decode((workspace/'verified.json').read_bytes())
    with state.control_rehearsal_access(destination):
        snapshot = ProjectStore(destination).snapshot()
        bootstrap = state._hash((destination/'storage.json').read_bytes())
        if (verified.get('format') != 'h3_offline_migration_verified_v1'
                or verified.get('inventory_sha256') != state._hash((workspace/'migration.json').read_bytes())
                or verified.get('root') != snapshot.reference or verified.get('bootstrap_sha256') != bootstrap):
            raise ValueError('Project changed since verification; activation refused.')
        state._immutable(destination, ACTIVATION, state._encode(dict(format=ACTIVE_FORMAT,
            run_name=source.name, bootstrap_sha256=bootstrap)), value['path_budget'])
    return dict(summary(value), activated=True, output_directory=str(workspace/'output'))
