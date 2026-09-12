"""Durable model renders followed by one recoverable input/catalog edit.

The original pinned parent and complete request are fenced before compute.
Rendering holds only an operation-local lock, not the ownership/catalog lock.
A synced render receipt permits retries without the model or another forward.
The render is staging, not a catalog asset, until register_model_image commits.
"""
import copy
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_asset_edits import _require, _check_scopes, _publish_media_file
    from .storage_project_assets import CATALOG, catalog_contract
    from .storage_project import _hash_file
    from .storage_execution_digest import execution_digest
    from .storage_layout import OrganizedStorageLayout
    from .storage_resolver import confined
    from .project_assets import _catalog_lock, _catalog_file_lock
    from .project_ownership import project_write_guard
    from .processing_persistence import sync_file, sync_directory
else:
    import storage_state as state
    from storage_asset_edits import _require, _check_scopes, _publish_media_file
    from storage_project_assets import CATALOG, catalog_contract
    from storage_project import _hash_file
    from storage_execution_digest import execution_digest
    from storage_layout import OrganizedStorageLayout
    from storage_resolver import confined
    from project_assets import _catalog_lock, _catalog_file_lock
    from project_ownership import project_write_guard
    from processing_persistence import sync_file, sync_directory


FORMAT = 'h3_asset_model_render_v1'


def _model_witness(model):
    # Never substitute object identity/repr for weights. Unsupported custom
    # patch structures fail explicitly instead of weakening retry identity.
    module = getattr(model, 'model', None)
    if module is None or not callable(getattr(module, 'state_dict', None)):
        raise ValueError('Model asset rendering requires a core UPSCALE_MODEL descriptor with saved weights.')
    return dict(descriptor=type(model).__module__+'.'+type(model).__qualname__,
        model=type(module).__module__+'.'+type(module).__qualname__,
        scale=float(model.scale), weights=execution_digest(dict(module.state_dict())),
        patches=execution_digest(getattr(model.patcher, 'patches', {})))


def execute(runtime, assets, operation, model, renderer, *, operation_id,
            proof=None, reference_templates=None):
    """Renderer returns (parent entry, PIL image, transform), as on legacy roots."""
    _require(runtime.assets, assets)
    if assets._read_view(runtime.run) is not runtime.reader:
        raise ValueError('Model asset rendering requires the current pinned runtime reader.')
    identity = state._token(operation_id)
    if runtime.has_node_proof:
        if proof is not None and proof != runtime.node_write_proof:
            raise ValueError('Model asset rendering cannot change node ownership proof.')
        proof = runtime.node_write_proof
    parent, _ = assets.asset(runtime.run, operation.get('asset_id'))
    if parent.get('kind') != 'image':
        raise ValueError('Only image assets can create model variants.')
    expected = dict(format=FORMAT, operation_id=identity, input_pin=runtime.pin,
        input_root_sha256=state._hash(str(runtime.asset_input_root).encode()),
        operation=copy.deepcopy(operation), parent=parent,
        reference_templates=copy.deepcopy(reference_templates))
    # Check all JSON inputs before creating a reservation or running a model.
    expected = state._decode(state._encode(expected))
    layout = OrganizedStorageLayout(str(runtime.project),runtime.store._marker()[0]['path_budget'])
    request_address = 'project/jobs/asset-model-'+identity+'.json'
    ready_address = 'project/jobs/asset-render-'+identity+'.json'
    request_path, ready_path = (confined(runtime.project,a) for a in (request_address,ready_address))
    target_address = 'project/assets/renders/'+identity+'.png'
    target = confined(runtime.project,target_address)
    for address in (request_address,ready_address,target_address,request_address+'.lock'):
        layout.check_budget(address)

    def guard():
        return project_write_guard(runtime.output,runtime.run,proof,'publish a model-derived project asset')

    def source_check():
        _require(runtime.assets,assets)
        if assets.asset(runtime.run,operation.get('asset_id'))[0] != parent:
            raise state.StateConflict('Model variant parent changed during rendering.')

    def fault(stage):
        if runtime.assets.after_stage:
            runtime.assets.after_stage(stage)

    # Establish authority before creating even the private job lock.
    with guard():
        source_check()
        request_path.parent.mkdir(parents=True,exist_ok=True)
    with _catalog_lock(str(request_path)), _catalog_file_lock(str(request_path)):
        with guard():
            source_check()
            if request_path.exists():
                request = state._decode(state._read_bytes(request_path))
                if (not isinstance(request,dict) or request.get('request') != expected
                        or not isinstance(request.get('model'),dict)):
                    raise state.StateConflict('Model render operation was reused with different inputs.')
                if model is not None and _model_witness(model) != request['model']:
                    raise state.StateConflict('Model render operation was reused with different weights or patches.')
            else:
                if ready_path.exists() or target.exists():
                    raise state.StateConflict('Unowned model render destination is occupied; original retained.')
                if model is None:
                    raise ValueError('Connect a core UPSCALE_MODEL before using Model upscale.')
                contract = catalog_contract(runtime.base.state['documents'].get(CATALOG))
                _check_scopes(runtime.base,runtime.check(),{'project',contract['scope']})
                request = dict(request=expected,model=_model_witness(model))
                state._immutable(runtime.project,request_address,state._encode(request),layout.path_budget)
            fault('model_render_requested')

        if not ready_path.exists():
            if model is None:
                raise ValueError('Model render is not finished; reconnect the same UPSCALE_MODEL to resume.')
            rendered_parent, rendered, transform = renderer()
            if rendered_parent != parent:
                raise state.StateConflict('Model renderer used a different parent asset.')
            # Ownership may have changed while the model ran. No catalog or
            # durable render is published under a stale workflow's proof.
            with guard():
                source_check()
                if _model_witness(model) != request['model']:
                    raise state.StateConflict('Model weights or patches changed during rendering.')
                contract = catalog_contract(runtime.base.state['documents'].get(CATALOG))
                _check_scopes(runtime.base,runtime.check(),{'project',contract['scope']})
                temporary_address = 'project/assets/renders/'+uuid.uuid4().hex+'.png'
                layout.check_budget(temporary_address)
                temporary = confined(runtime.project,temporary_address)
                temporary.parent.mkdir(parents=True,exist_ok=True)
                reserved = created = False
                try:
                    with temporary.open('xb') as handle:
                        created = True
                        rendered.save(handle,format='PNG')
                        handle.flush()
                    sync_file(temporary)
                    sync_directory(temporary.parent)
                    digest, signature = _hash_file(temporary)
                    ready = dict(request_sha256=state._hash(state._encode(request)),
                        sha256=digest,size=signature[2],temporary=temporary_address,
                        transform=dict(transform,model_witness=request['model']),
                        width=rendered.width,height=rendered.height,mode=rendered.mode)
                    state._immutable(runtime.project,ready_address,
                        state._encode(dict(value=ready,sha256=state._hash(state._encode(ready)))),layout.path_budget)
                    reserved = True
                    fault('model_render_prepared')
                finally:
                    if created and not reserved and temporary.exists():
                        temporary.unlink()  # Only our unpublished private render.

        envelope = state._decode(state._read_bytes(ready_path))
        ready = envelope.get('value') if isinstance(envelope,dict) else None
        if (not isinstance(ready,dict) or set(ready) != {'request_sha256','sha256','size','temporary',
                'transform','width','height','mode'}
                or envelope.get('sha256') != state._hash(state._encode(ready))
                or ready['request_sha256'] != state._hash(state._encode(request))
                or not isinstance(ready['temporary'],str)
                or not re.fullmatch(r'project/assets/renders/[0-9a-f]{32}\.png',ready['temporary'])):
            raise state.StateConflict('Model render receipt is invalid; retained for recovery.')
        temporary = confined(runtime.project,ready['temporary'])
        with guard():
            source_check()
            _publish_media_file(temporary,target,(ready['sha256'],ready['size']))
            from PIL import Image
            with Image.open(target) as opened:
                if (opened.size,opened.mode) != ((ready['width'],ready['height']),ready['mode']):
                    raise state.StateConflict('Model render geometry differs from its receipt.')
                opened.verify()
            fault('model_render_published')
            result = assets.register_model_image(runtime.run,operation.get('asset_id'),str(target),
                tag=operation.get('tag',''),folder_id=operation.get('folder_id'),
                transform=ready['transform'],operation_id=operation.get('operation_id',''),
                reference_templates=reference_templates,storage_operation_id=identity,ownership_proof=proof)
            # Keep the durable render and receipt until an explicit job-retention
            # operation; delete only the verified duplicate staging file.
            if temporary != target and temporary.exists():
                digest, signature = _hash_file(temporary)
                if (digest,signature[2]) == (ready['sha256'],ready['size']):
                    temporary.unlink()
                    sync_directory(temporary.parent)
            return result
