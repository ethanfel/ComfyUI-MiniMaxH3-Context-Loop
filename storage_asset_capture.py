"""Pinned source selection and durable frame capture on explicit copied hosts."""
import copy
from pathlib import Path, PureWindowsPath
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_asset_edits import _require, _check_scopes, _publish_media_file
    from .storage_project_assets import CATALOG, accepted_catalog, accepted_asset_path, catalog_contract
    from .storage_project import _hash_file
    from .storage_resolver import confined, artifact_address
    from .storage_layout import OrganizedStorageLayout
    from .project_assets import VIDEO_EXTENSIONS, _catalog_lock, _catalog_file_lock
    from .project_ownership import project_write_guard
    from .processing_persistence import sync_file, sync_directory
else:
    import storage_state as state
    from storage_asset_edits import _require, _check_scopes, _publish_media_file
    from storage_project_assets import CATALOG, accepted_catalog, accepted_asset_path, catalog_contract
    from storage_project import _hash_file
    from storage_resolver import confined, artifact_address
    from storage_layout import OrganizedStorageLayout
    from project_assets import VIDEO_EXTENSIONS, _catalog_lock, _catalog_file_lock
    from project_ownership import project_write_guard
    from processing_persistence import sync_file, sync_directory


FORMAT = 'h3_asset_frame_capture_v1'


def source_path(runtime, roots, filename, subfolder, kind):
    """A /view triple may name a saved logical path or its accepted physical file."""
    runtime.check()
    kind = str(kind or 'output').strip().lower()
    if kind not in ('input','output','temp'):
        raise ValueError('Video source type must be input, output, or temp.')
    name, sub = str(filename or '').strip(), str(subfolder or '').strip()
    if (not name or Path(name).is_absolute() or Path(sub).is_absolute()
            or PureWindowsPath(name).drive or PureWindowsPath(sub).drive):
        raise ValueError('Video source needs a nonblank relative filename and subfolder.')
    relative = artifact_address(sub+'/'+name if sub else name)
    path = confined(roots[kind],relative)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError('Frame capture requires a supported video file, not a playlist.')
    indexed = False
    if kind == 'output' and relative.startswith('h3_chains/'):
        address = runtime.reader.address(path)  # Reject other projects/unaccepted internals.
        runtime.reader._track(address)
        path = runtime.store.payload_path(runtime.base,address,verify=True)
        indexed = True
    elif kind == 'input' and relative.startswith('h3_projects/'):
        prefix = 'h3_projects/'+runtime.run+'/'
        if not relative.startswith(prefix):
            raise ValueError('Cross-project capture requires an independently pinned source.')
        catalog = accepted_catalog(runtime.reader)
        entry = next((a for a in (catalog or {}).get('assets',[])
                      if a['relative_path']==relative[len(prefix):]),None)
        if entry is None or entry['kind']!='video':
            raise ValueError('Capture source is not in the accepted project asset catalog.')
        path = accepted_asset_path(runtime.reader,entry)
        indexed = True
    if not path.is_file():
        raise FileNotFoundError('Frame capture source is unavailable.')
    return path,indexed


def capture(runtime, assets, roots, request, extract, *, operation_id, proof=None):
    """Extract(source, seconds, private_output) uses the existing ffmpeg contract."""
    _require(runtime.assets,assets)
    if assets._read_view(runtime.run) is not runtime.reader:
        raise ValueError('Frame capture requires the current pinned runtime reader.')
    identity = state._token(operation_id)
    if runtime.has_node_proof:
        if proof is not None and proof != runtime.node_write_proof:
            raise ValueError('Frame capture cannot change node ownership proof.')
        proof = runtime.node_write_proof
    expected = state._decode(state._encode(dict(format=FORMAT,operation_id=identity,
        input_pin=runtime.pin, roots_sha256=state._hash(state._encode({k:str(v) for k,v in roots.items()})),
        request=copy.deepcopy(request))))
    layout = OrganizedStorageLayout(str(runtime.project),runtime.store._marker()[0]['path_budget'])
    intent_address = 'project/jobs/asset-capture-'+identity+'.json'
    ready_address = 'project/jobs/frame-'+identity+'.json'
    target_address = 'project/assets/renders/'+identity+'.png'
    intent_path,ready_path,target = (confined(runtime.project,a) for a in
                                   (intent_address,ready_address,target_address))
    for address in (intent_address,ready_address,target_address,intent_address+'.lock'):
        layout.check_budget(address)

    def guard():
        return project_write_guard(runtime.output,runtime.run,proof,'capture a project asset frame')

    def source():
        path,indexed = source_path(runtime,roots,request['filename'],request['subfolder'],request['type'])
        digest,signature = _hash_file(path)
        return path,dict(sha256=digest,size=signature[2],indexed=indexed)

    def fault(stage):
        if runtime.assets.after_stage:
            runtime.assets.after_stage(stage)

    with guard():
        _require(runtime.assets,assets)
        intent_path.parent.mkdir(parents=True,exist_ok=True)
    with _catalog_lock(str(intent_path)), _catalog_file_lock(str(intent_path)):
        with guard():
            _require(runtime.assets,assets)
            if intent_path.exists():
                intent = state._decode(state._read_bytes(intent_path))
                if not isinstance(intent,dict) or intent.get('request')!=expected or not isinstance(intent.get('source'),dict):
                    raise state.StateConflict('Capture operation was reused with different inputs.')
            else:
                if ready_path.exists() or target.exists() or identity in runtime.check().state['operations']:
                    raise state.StateConflict('Capture operation destination is already owned; files retained.')
                contract = catalog_contract(runtime.base.state['documents'].get(CATALOG))
                _check_scopes(runtime.base,runtime.check(),{'project',contract['scope']})
                _,witness = source()
                intent = dict(request=expected,source=witness)
                state._immutable(runtime.project,intent_address,state._encode(intent),layout.path_budget)
            fault('frame_capture_requested')

        def verify_source():
            try:
                path,witness = source()
            except FileNotFoundError:
                # A completed external/temp capture is self-contained. Missing
                # accepted source payloads are corruption, never a fallback.
                if intent['source'].get('indexed') is False and ready_path.exists():
                    return None
                raise
            if witness != intent['source']:
                raise state.StateConflict('Frame capture source bytes changed; original intent retained.')
            return path

        path = verify_source()
        if not ready_path.exists():
            contract = catalog_contract(runtime.base.state['documents'].get(CATALOG))
            _check_scopes(runtime.base,runtime.check(),{'project',contract['scope']})
            # Decoder output may replace only this invocation's private name.
            temporary_address = 'project/assets/renders/'+uuid.uuid4().hex+'.png'
            layout.check_budget(temporary_address,staging_suffix='.'+'f'*32+'.tmp.png')
            temporary = confined(runtime.project,temporary_address)
            temporary.parent.mkdir(parents=True,exist_ok=True)
            with temporary.open('xb'):
                pass
            reserved = False
            try:
                extract(str(path),request['time_seconds'],str(temporary))
                from PIL import Image
                with Image.open(temporary) as frame:
                    frame.verify()
                sync_file(temporary)
                sync_directory(temporary.parent)
                digest,signature = _hash_file(temporary)
                with guard():
                    _require(runtime.assets,assets)
                    verify_source()
                    _check_scopes(runtime.base,runtime.check(),{'project',contract['scope']})
                    ready = dict(intent_sha256=state._hash(state._encode(intent)),
                        sha256=digest,size=signature[2],temporary=temporary_address)
                    state._immutable(runtime.project,ready_address,
                        state._encode(dict(value=ready,sha256=state._hash(state._encode(ready)))),layout.path_budget)
                    reserved = True
                    fault('frame_capture_prepared')
            finally:
                if not reserved and temporary.exists():
                    temporary.unlink()  # Only the current decoder's private output.

        envelope = state._decode(state._read_bytes(ready_path))
        ready = envelope.get('value') if isinstance(envelope,dict) else None
        if (not isinstance(ready,dict) or set(ready)!={'intent_sha256','sha256','size','temporary'}
                or envelope.get('sha256')!=state._hash(state._encode(ready))
                or ready['intent_sha256']!=state._hash(state._encode(intent))
                or not isinstance(ready['temporary'],str)
                or not re.fullmatch(r'project/assets/renders/[0-9a-f]{32}\.png',ready['temporary'])):
            raise state.StateConflict('Frame capture receipt is damaged; retained for recovery.')
        temporary = confined(runtime.project,ready['temporary'])
        with guard():
            _require(runtime.assets,assets)
            verify_source()
            _publish_media_file(temporary,target,(ready['sha256'],ready['size']))
            fault('frame_capture_published')
            result = assets.register_captured_frame(runtime.run,str(target),tag=request['tag'],
                role=request['role'],folder_id=request['folder_id'],
                source=dict(intent['source'],view={k:request[k] for k in ('filename','subfolder','type')},
                            pin=runtime.pin if intent['source']['indexed'] else None),
                time_seconds=request['time_seconds'],storage_operation_id=identity,ownership_proof=proof)
            if temporary!=target and temporary.exists():
                digest,signature = _hash_file(temporary)
                if (digest,signature[2])==(ready['sha256'],ready['size']):
                    temporary.unlink()
                    sync_directory(temporary.parent)
            return result
