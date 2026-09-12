"""Normal node/request entry points for explicitly activated organized projects.

No activation record means no behavioral change. Filesystem locations and
writer grants come from this package and ComfyUI, never from workflow JSON.
The existing pinned transactions and ownership checks remain authoritative.
"""
from contextlib import contextmanager, ExitStack
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import asyncio
import inspect
import json
from pathlib import Path
import re
import sys
import uuid

if __package__:
    from . import storage_carriers as carriers, storage_state as state
    from .storage_project import ProjectStore
    from .storage_resolver import confined
else:
    import storage_carriers as carriers
    import storage_state as state
    from storage_project import ProjectStore
    from storage_resolver import confined

ACTIVATION = 'project/activation.json'
FORMAT = 'h3_organized_runtime_v1'
_RUN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?\Z')
# These handlers use request data only, never a live execution future/socket.
# Bound concurrent scans so a carousel cannot start dozens of full NAS reads.
_READ_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix='h3-storage-read')
_BACKGROUND_READS = frozenset(('_project_asset_catalog', '_project_asset_media',
    '_list_saved_checkpoints', '_working_branch_command'))


async def _background_read(function, *args):
    context = copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        _READ_WORKERS, context.run, function, *args)


class _ReadRequest(dict):
    """Detached request data; never move aiohttp's connection to a worker."""
    def __init__(self, request, body=None):
        from multidict import CIMultiDict
        super().__init__()
        self.method = request.method
        self.query = dict(request.query)
        self.headers = CIMultiDict(request.headers)
        self.content_type = request.content_type
        self.body = body

    async def json(self):
        return self.body


def activated_project(output, run):
    if not isinstance(run, str) or not _RUN.fullmatch(run):
        raise ValueError('Invalid H3 project name for storage access.')
    project = confined(Path(output).resolve(), 'h3_chains/'+run)
    activation = confined(project, ACTIVATION)
    if not activation.exists():
        return None
    value = state._decode(state._read_bytes(activation))
    if (set(value) != {'format', 'run_name', 'bootstrap_sha256'}
            or value['format'] != FORMAT or value['run_name'] != run
            or value['bootstrap_sha256'] != state._hash(state._read_bytes(project/'storage.json'))):
        raise ValueError('Organized project activation does not match its storage authority.')
    return project


@contextmanager
def project_read(output, run):
    """Bind one discovery read without granting writes or changing its pin."""
    current = carriers.runtime.current_runtime(output, run)
    if current is not None:
        yield current
        return
    project = activated_project(output, run)
    if project is None:
        yield None
        return
    with state.control_rehearsal_access(project), carriers.runtime.runtime_access(
            ProjectStore(project)) as bound:
        yield bound


def _modules():
    # Do not import the model/node package from standalone maintenance tools.
    prefix = __package__+'.' if __package__ else ''
    return sys.modules.get(prefix+'chain_nodes'), sys.modules.get(prefix+'upscale_nodes')


def _functions(module, names):
    if module is None:
        return ()
    result = []
    for name in names:
        cls = getattr(module, name, None)
        if cls is not None:
            result.append(getattr(cls, cls.FUNCTION))
    return tuple(result)


def node_options():
    chain, upscale = _modules()
    if __package__:
        from . import storage_continuation, storage_processing_continuation, storage_review_execution
        from .storage_project_assets import plan_asset_inputs
    else:
        import storage_continuation, storage_processing_continuation, storage_review_execution
        from storage_project_assets import plan_asset_inputs
    adapters = {}
    for module, name, adapter in (
            (chain, 'MiniMaxH3ChainLoopEnd', storage_continuation.loop_end_inputs),
            (chain, 'MiniMaxH3ChainReview', storage_review_execution.review_inputs),
            (chain, 'MiniMaxH3ChainPlan', plan_asset_inputs),
            (chain, 'MiniMaxH3ChainPlanModern', plan_asset_inputs),
            (chain, 'MiniMaxH3ChainPlanStudio', plan_asset_inputs),
            (upscale, 'MiniMaxH3ChainUpscaleLoopEnd', storage_processing_continuation.loop_end_inputs),
            (upscale, 'MiniMaxH3ChainUpscaleHandoff', storage_processing_continuation.loop_inputs)):
        for function in _functions(module, (name,)):
            adapters[function] = adapter
    return dict(
        branch_writers=_functions(chain, ('MiniMaxH3ChainPlan', 'MiniMaxH3ChainPlanModern',
            'MiniMaxH3ChainPlanStudio', 'MiniMaxH3ChainScenePromptEditor',
            'MiniMaxH3ChainRichScenePromptEditor', 'MiniMaxH3ChainLoopStart', 'MiniMaxH3ChainRunManager',
            'MiniMaxH3ChainManifestLoad')),
        generation_writers=_functions(chain, ('MiniMaxH3ChainSegmentSave', 'MiniMaxH3ChainLoopEnd',
            'MiniMaxH3ChainManifestLoad', 'MiniMaxH3ChainReview')),
        processing_writers=_functions(upscale, ('MiniMaxH3ChainUpscaleSegmentSave',)),
        export_writers=_functions(chain, ('MiniMaxH3ChainExportPNG', 'MiniMaxH3ChainAssemble',
            'MiniMaxH3ChainChapterDelivery', 'MiniMaxH3ChainReview'))+
            _functions(upscale, ('MiniMaxH3ChainUpscaleMerge',)),
        handoff_writers=_functions(chain, ('MiniMaxH3ChainLoopStart', 'MiniMaxH3ChainLoopEnd',
            'MiniMaxH3ChainReview')),
        retention_writers=_functions(chain, ('MiniMaxH3ChainReview',)),
        history_writers=_functions(chain, ('MiniMaxH3ChainCurrent',)),
        asset_writers=_functions(chain, ('MiniMaxH3ProjectAssetManager',)), input_adapters=adapters)


_REQUEST_GRANTS = {
    '_working_branch_command': ('branch',), '_project_ownership_command': ('ownership',),
    '_save_run_assets': ('branch',),
    '_restore_checkpoint_revisions': ('branch',), '_attribute_checkpoint_revision': ('branch',),
    '_update_run_editorial': ('branch',), '_update_prompt_history': ('history',),
    '_delete_checkpoint_revision': ('retention',), '_checkpoint_retention_undo': ('retention',),
    '_processing_checkpoint_deletion': ('retention',), '_chapter_snapshot_retirement': ('retention',),
    '_claim_handoff': ('handoff',), '_transition_handoff': ('handoff',), '_release_handoff': ('handoff',),
    '_submit_deferred_review': ('generation', 'handoff', 'retention'),
    '_submit_review_decision': (),
    '_submit_candidate_batch_command': ('generation', 'retention'),
    **{name: ('asset',) for name in (
        '_project_asset_update', '_project_asset_duplicate', '_project_asset_folder',
        '_project_asset_reorder', '_project_asset_import', '_project_asset_derive',
        '_project_asset_capture_frame', '_project_asset_repair_inputs', '_project_asset_delete',
        '_project_asset_upload')},
    **{name: () for name in (
        '_list_saved_checkpoints', '_preview_checkpoint_revision_deletion', '_load_saved_run',
        '_get_prompt_history', '_list_handoffs', '_project_asset_catalog', '_project_asset_input_repair',
        '_plan_studio_presentation', '_plan_studio_checkpoint_thumbnail', '_project_asset_media',
        '_project_asset_sources', '_list_deferred_reviews')},
}


def request_grants(function):
    chain, _ = _modules()
    original = inspect.unwrap(function)
    name = original.__name__
    if (name not in _REQUEST_GRANTS or chain is None
            or inspect.unwrap(getattr(chain, name, None)) is not original):
        raise ValueError('No organized storage adapter for this HTTP action.')
    return {function: _REQUEST_GRANTS[name]}


@contextmanager
def hosted(run, *, request_function=None, request_values=None):
    if carriers._HOST.get() is not None or carriers.runtime._ACTIVE.get() is not None or not run:
        yield
        return
    import folder_paths
    project = activated_project(folder_paths.get_output_directory(), run)
    if project is None:
        yield
        return
    if request_function is not None:
        options = dict(request_grants=request_grants(request_function))
        needs_input = any('asset' in grants for grants in options['request_grants'].values())
    else:
        from comfy_execution.utils import get_executing_context
        context = get_executing_context()
        if context is None or not context.prompt_id:
            raise ValueError('Organized project node needs a ComfyUI execution context.')
        options = node_options()
        options['operation_namespace'] = uuid.uuid5(uuid.NAMESPACE_URL,
            json.dumps(['h3-job', context.prompt_id, context.list_index])).hex
        needs_input = bool(options['asset_writers'])
    if needs_input:
        options['asset_input_root'] = str(Path(folder_paths.get_input_directory()).resolve())
    with ExitStack() as stack:
        sources = []
        values = request_values or {}
        action = inspect.unwrap(request_function).__name__ if request_function else ''
        names = []
        if action == '_project_asset_import' and values.get('source_pin') is not None:
            names = [(values.get('source_project') or values.get('run_name'), values['source_pin'])]
        elif action == '_project_asset_sources' and values.get('source') in ('project', 'chains'):
            names = [(item.name, None) for item in project.parent.iterdir()
                     if _RUN.fullmatch(item.name) and item.name != run and item.is_dir()]
        if names:
            if __package__:
                from .storage_asset_sources import asset_source_access
            else:
                from storage_asset_sources import asset_source_access
            for name, pin in names:
                if name == run:
                    continue
                source = activated_project(folder_paths.get_output_directory(), name)
                if source is None:
                    continue
                with state.control_rehearsal_access(source):
                    store = ProjectStore(source)
                    if pin is None:
                        with carriers.runtime.runtime_access(store) as bound:
                            pin = bound.pin
                    sources.append(stack.enter_context(asset_source_access(store, pin=pin)))
                if len(sources) > 128:
                    raise ValueError('Too many source projects in one asset request.')
        options['asset_sources'] = sources
        stack.enter_context(state.control_rehearsal_access(project))
        stack.enter_context(carriers.node_host(ProjectStore(project), **options))
        yield


def input_project(values):
    runs = {str(item['run_name']) for item in carriers._inputs(values) if item.get('run_name')}
    assets = values.get('project_assets')
    if isinstance(assets, dict) and assets.get('project'):
        runs.add(str(assets['project']))
    elif not runs and values.get('run_name'):
        runs.add(str(values['run_name']))
    if len(runs) > 1:
        raise ValueError('H3 node inputs belong to different projects.')
    return next(iter(runs), None)


def storage_node(function):
    """Outer entry point; existing scoped_node still validates every carrier."""
    if getattr(function, '_h3_storage_hosted', False):
        return function
    signature = inspect.signature(function)
    def project(args, kwargs):
        values = signature.bind_partial(*args, **kwargs)
        values.apply_defaults()
        return input_project(values.arguments)
    @wraps(function)
    def wrapped(*args, **kwargs):
        with hosted(project(args, kwargs)):
            return function(*args, **kwargs)
    @wraps(function)
    async def async_wrapped(*args, **kwargs):
        with hosted(project(args, kwargs)):
            return await function(*args, **kwargs)
    result = async_wrapped if inspect.iscoroutinefunction(function) else wrapped
    result._h3_storage_hosted = True
    return result


def storage_http(function):
    async def dispatch(request):
        if carriers._HOST.get() is not None or carriers.runtime._ACTIVE.get() is not None:
            return await function(request)
        values = dict(request.query)
        if request.method == 'POST' and request.content_type == 'application/json':
            body = await request.json()
            if isinstance(body, dict):
                values = {**body, **values}
        elif request.method == 'POST' and inspect.unwrap(function).__name__ == '_project_asset_upload':
            # Read only the bounded control fields before the streamed file.
            # The original handler consumes the file exactly once.
            reader = await request.multipart()
            fields, upload = {}, None
            async for part in reader:
                if part.name == 'file':
                    upload = part
                    break
                if len(fields) >= 32 or part.name in fields:
                    raise ValueError('Invalid upload control fields.')
                raw = await part.read_chunk(size=8192)
                if not part.at_eof():
                    raise ValueError('Upload control field is too large.')
                fields[part.name] = raw.decode('utf-8').strip()
            request['h3_storage_upload'] = (fields, upload)
            values = {**fields, **values}
        run = values.get('project') or values.get('run_name')
        action = inspect.unwrap(function).__name__
        if action in ('_submit_review_decision', '_submit_candidate_batch_command'):
            chain, _ = _modules()
            registry = chain._PENDING_REVIEWS if action == '_submit_review_decision' else chain._ACTIVE_CANDIDATE_BATCHES
            plan = registry.get(str(values.get('token') or ''), {}).get('plan', {})
            if plan.get('run_name'):
                run = plan['run_name']
                request['h3_storage_review_identity'] = dict(run_name=run, branch_id=plan.get('_branch_id', 'main'))
        with hosted(run, request_function=function, request_values=values):
            return await function(request)
    @wraps(function)
    async def wrapped(request):
        try:
            action = inspect.unwrap(function).__name__
            background = request.method in ('GET', 'HEAD') and action in _BACKGROUND_READS
            body = None
            if (action == '_project_ownership_command' and request.method == 'POST'
                    and request.content_type == 'application/json'):
                # Consume the connection on its own event loop first. Only
                # status reads move; claims/takeovers/releases stay unchanged.
                body = await request.json()
                background = isinstance(body, dict) and str(body.get('action') or 'status').strip().lower() == 'status'
            if background:
                detached = _ReadRequest(request, body)
                return await _background_read(lambda: asyncio.run(dispatch(detached)))
            return await dispatch(request)
        except (ValueError, TypeError, OSError) as error:
            from aiohttp import web
            return web.json_response({'error':str(error), 'code':'h3_storage_request_conflict'}, status=409)
    return wrapped


def storage_fingerprint(function):
    if getattr(function, '_h3_storage_fingerprint', False):
        return function
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        if carriers.runtime._ACTIVE.get() is not None or carriers._HOST.get() is not None:
            return function(*args, **kwargs)
        values = signature.bind_partial(*args, **kwargs)
        values.apply_defaults()
        inputs = dict(values.arguments)
        for name in ('kwargs', '_kwargs'):
            inputs.update(inputs.pop(name, {}) or {})
        run = input_project(inputs)
        if not run:
            return function(*args, **kwargs)
        import folder_paths
        project = activated_project(folder_paths.get_output_directory(), run)
        if project is None:
            return function(*args, **kwargs)
        selected = inputs.get('working_branch_id', 'main')
        for item in carriers._inputs(inputs):
            if item.get('_branch_id'):
                selected = item['_branch_id']
                break
        with state.control_rehearsal_access(project), carriers.runtime.runtime_access(
                ProjectStore(project), selected=selected):
            return function(*args, **kwargs)
    wrapped._h3_storage_fingerprint = True
    return wrapped


async def storage_session(request):
    """Read-only handshake for existing editors before their first mutation."""
    return await _background_read(_storage_session_response, dict(request.query))


def _storage_session_response(query):
    import folder_paths
    from aiohttp import web
    try:
        run = query.get('run_name', '')
        project = activated_project(folder_paths.get_output_directory(), run)
        if project is None:
            return web.json_response({'organized': False})
        with state.control_rehearsal_access(project), carriers.runtime.runtime_access(
                ProjectStore(project), selected=query.get('branch_id', 'main')) as bound:
            return web.json_response({'organized': True, 'pin': bound.pin},
                headers={'Cache-Control':'no-store'})
    except (OSError, ValueError, TypeError) as error:
        return web.json_response({'error':str(error)}, status=409)
