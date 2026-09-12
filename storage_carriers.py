"""Pinned node carriers inside explicitly authorized, copy-only host sessions.

Serialized pins identify data, never permissions or filesystem locations. The
host supplies store access and exact branch-writer callables out of band. Normal
legacy nodes stay unchanged; a saved pin without host access fails closed.
"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import json
import inspect
import uuid
from pathlib import Path
from functools import wraps

if __package__:
    from . import storage_runtime as runtime
    from .branch_scope import branch_id
else:
    import storage_runtime as runtime
    from branch_scope import branch_id

PIN_KEY = '_storage_pin'
_HOST = ContextVar('h3_storage_node_host', default=None)
_NESTED = ('plan', 'state', 'source_manifest', 'manifest')


def _pin_identity(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid serialized H3 storage pin.')
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid serialized H3 storage pin.') from error


def _carriers(value, seen=None):
    # Only known serializable execution envelopes. Do not walk arbitrary model,
    # tensor, conditioning, prompt, image or reference-cache dictionaries.
    if not isinstance(value, dict):
        return
    seen = set() if seen is None else seen
    if id(value) in seen:
        raise ValueError('Cyclic H3 execution carrier.')
    seen.add(id(value))
    try:
        yield value
        for key in _NESTED:
            yield from _carriers(value.get(key), seen)
    finally:
        seen.remove(id(value))


def _inputs(values):
    found = []
    for key, value in values.items():
        if key in ('plan_json', 'plan_json_input', 'selection_json', 'catalog_json') and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue  # The existing node parser owns malformed legacy JSON.
        found.extend(_carriers(value))
    return found


class NodeHost:
    def __init__(self, store, branch_writers, handoff_writers, generation_writers=(), input_adapters=None,
                 generation_operations=None, processing_writers=(), processing_operations=None,
                 operation_namespace=None, export_writers=(), export_operations=None, handoff_operations=None,
                 retention_writers=(), history_writers=(), history_operations=None,
                 asset_writers=(), asset_operations=None, asset_input_root=None,
                 request_grants=None, asset_sources=()):
        if not isinstance(store, runtime.ProjectStore):
            raise TypeError('Node host requires an explicit copied ProjectStore.')
        store.snapshot()  # Existing copy-only access and ready-state validation.
        self.store, self.closed = store, False
        self.asset_sources = tuple(asset_sources)
        self.request_grants = {}
        for function, grants in (request_grants or {}).items():
            if not callable(function) or not isinstance(grants, (tuple, list, set, frozenset)):
                raise ValueError('HTTP storage grants require exact host-side callables and capabilities.')
            function = inspect.unwrap(function)
            allowed = {'branch', 'ownership', 'handoff', 'generation', 'processing',
                       'export', 'retention', 'history', 'asset'}
            if function in self.request_grants or any(grant not in allowed for grant in grants):
                raise ValueError('Invalid or duplicate HTTP storage grant.')
            self.request_grants[function] = frozenset(grants)
        if operation_namespace is not None:
            from_module = __package__+'.storage_state' if __package__ else 'storage_state'
            from importlib import import_module
            operation_namespace = import_module(from_module)._token(operation_namespace)
            if generation_operations or processing_operations or export_operations or handoff_operations or history_operations or asset_operations:
                raise ValueError('A job operation namespace cannot be mixed with explicit operation IDs.')
        self.operation_namespace = operation_namespace
        if any(not callable(item) for item in branch_writers + handoff_writers + generation_writers + processing_writers + export_writers + retention_writers + history_writers + asset_writers):
            raise ValueError('Node writer grants require host-side Python callables.')
        self.branch_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                        for item in branch_writers)
        self.handoff_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                         for item in handoff_writers)
        self.generation_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                            for item in generation_writers)
        self.processing_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                            for item in processing_writers)
        self.export_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                       for item in export_writers)
        self.retention_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                          for item in retention_writers)
        self.history_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                        for item in history_writers)
        self.asset_writers = frozenset(inspect.unwrap(getattr(item, '__func__', item))
                                      for item in asset_writers)
        has_asset_writers = self.asset_writers or any('asset' in grants for grants in self.request_grants.values())
        if asset_input_root is not None and (not has_asset_writers or not Path(asset_input_root).is_absolute()):
            raise ValueError('Asset input root needs exact node writer grants and an absolute host path.')
        if has_asset_writers and asset_input_root is None:
            raise ValueError('Asset node writes require a separate host input root.')
        self.asset_input_root = asset_input_root
        self.input_adapters = {}
        for function, adapter in (input_adapters or {}).items():
            if not callable(function) or not callable(adapter):
                raise ValueError('Node input adapters require host-side Python callables.')
            function = inspect.unwrap(getattr(function, '__func__', function))
            if function in self.input_adapters:
                raise ValueError('Duplicate node input adapter grant.')
            self.input_adapters[function] = adapter
        # Out-of-band IDs identify one exact node invocation, not an entire
        # workflow or all recursive scenes. They never grant write permission.
        operations_seen = set()
        for domain, requested in (('generation', generation_operations), ('processing', processing_operations),
                                  ('export', export_operations), ('handoff', handoff_operations),
                                  ('history', history_operations), ('asset', asset_operations)):
            registered = {}
            setattr(self, domain+'_operations', registered)
            for key, operation in (requested or {}).items():
                if (not isinstance(key, tuple) or len(key) != 2 or not callable(key[0])
                        or not isinstance(key[1], str) or not key[1]):
                    raise ValueError(domain.capitalize()+' operations require (callable, unique_id) host keys.')
                function = inspect.unwrap(getattr(key[0], '__func__', key[0]))
                if function not in getattr(self, domain+'_writers'):
                    raise ValueError(domain.capitalize()+' operation requires a separate exact writer grant.')
                if __package__:
                    from .storage_state import _token
                else:
                    from storage_state import _token
                operation = _token(operation)
                identity = (function, key[1])
                if identity in registered or operation in operations_seen:
                    raise ValueError('Duplicate '+domain+' operation identity.')
                registered[identity] = operation
                operations_seen.add(operation)

    def check(self):
        if self.closed or _HOST.get() is not self:
            raise ValueError('Storage node host escaped its copy-only session.')
        self.store.snapshot()


@contextmanager
def node_host(store, *, branch_writers=(), handoff_writers=(), generation_writers=(), input_adapters=None,
              generation_operations=None, processing_writers=(), processing_operations=None,
              operation_namespace=None, export_writers=(), export_operations=None, handoff_operations=None,
              retention_writers=(), history_writers=(), history_operations=None,
              asset_writers=(), asset_operations=None, asset_input_root=None,
              request_grants=None, asset_sources=()):
    """Host-only qualification access; JSON cannot create or prolong a host.

    Each independent node opens/closes its own runtime, from the incoming pin
    or the latest root for the first node. Default access is read-only. Granted
    branch writers receive exact accepted-commit pins and validate ownership at
    publication. A host-issued job operation_namespace supplies stable IDs to
    dynamically expanded generation/processing save and VIDEO PNG nodes (including cacheless
    reevaluation). It is not a writer grant and must not span unrelated jobs.
    VIDEO PNG export needs its separate exact export grant and operation ID.
    Checkpoint PNG/WAV uses the same explicit export grants and exact IDs.
    Review candidate quarantine requires a separate exact retention grant;
    its operation IDs derive from persisted, confirmed deletion previews.
    Carousel reference-slot synchronization needs an exact asset writer grant,
    an input-root grant and its own stable node ID. Project-global catalog
    handoff to another Plan branch uses the separately granted plan_asset_inputs
    adapter; its accepted root never changes during branch selection.
    Other unported writers remain fenced. Nothing activates production.
    """
    if _HOST.get() is not None or runtime._ACTIVE.get() is not None:
        raise ValueError('Cannot nest or switch storage node hosts.')
    host = NodeHost(store, tuple(branch_writers), tuple(handoff_writers), tuple(generation_writers),
                    input_adapters, generation_operations, tuple(processing_writers), processing_operations,
                    operation_namespace, tuple(export_writers), export_operations, handoff_operations,
                    tuple(retention_writers), tuple(history_writers), history_operations,
                    tuple(asset_writers), asset_operations, asset_input_root, request_grants, asset_sources)
    token = _HOST.set(host)
    try:
        yield host
    finally:
        host.closed = True
        _HOST.reset(token)


def storage_request(function):
    """Bind an actual H3 HTTP action to its server-issued project session.

    Outside a host the existing endpoint is unchanged. Inside a host, exact
    handler grants are mandatory; the client only supplies a data pin, never
    filesystem access. GET returns a pin for the next edit; POST must carry it
    in X-H3-Storage-Pin. Worker threads inherit this request's runtime, which is
    revoked when the response is produced. Ownership checks remain in the
    existing handler. This does not activate storage or register a project.
    """
    if getattr(function, '_h3_storage_request', False):
        return function

    @wraps(function)
    async def wrapped(request):
        host = _HOST.get()
        if host is None:
            return await function(request)
        from aiohttp import web
        try:
            host.check()
            original = inspect.unwrap(function)
            if original not in host.request_grants:
                raise ValueError('This HTTP action is not enabled for the hosted project.')
            values = dict(request.query)
            if request.method == 'POST':
                if original.__name__ == '_project_asset_upload' and 'h3_storage_upload' in request:
                    body = request['h3_storage_upload'][0]
                elif request.content_type == 'application/json':
                    body = await request.json()
                else:
                    raise ValueError('Hosted storage actions currently require JSON requests.')
                if not isinstance(body, dict):
                    raise ValueError('H3 storage request requires a JSON object.')
                for key in ('run_name', 'project', 'branch_id'):
                    if key in values and key in body and values[key] != body[key]:
                        raise ValueError('Conflicting H3 request project or branch identity.')
                values = {**body, **values}
            elif request.method not in ('GET', 'HEAD'):
                raise ValueError('Unsupported hosted storage HTTP method.')
            live_review = (request.get('h3_storage_review_identity')
                if original.__name__ in ('_submit_review_decision', '_submit_candidate_batch_command') else None)
            if live_review is not None:
                for key, value in live_review.items():
                    if key in values and values[key] != value:
                        raise ValueError('Review request differs from its running batch.')
                values = {**values, **live_review}
            identity_keys = ('project',) if (original.__name__ == '_project_asset_import'
                and values.get('source') == 'chains') else ('run_name', 'project')
            requested = {str(values[key]) for key in identity_keys if values.get(key)}
            if requested != {host.store.project.name}:
                raise ValueError('HTTP request must name the hosted project exactly.')
            selected = branch_id(values.get('branch_id', 'main'))
            encoded_pin = request.headers.get('X-H3-Storage-Pin')
            if encoded_pin is not None and len(encoded_pin) > 4096:
                raise ValueError('Invalid HTTP storage pin.')
            pin = json.loads(encoded_pin) if encoded_pin is not None else None
            if encoded_pin is not None and not isinstance(pin, dict):
                raise ValueError('Invalid HTTP storage pin.')
            if request.method == 'POST' and pin is None and live_review is None:
                raise ValueError('Load the project first; an edit requires its exact storage pin.')
            grants = host.request_grants[original] if request.method == 'POST' else ()
            options = {key+'_writes': True for key in grants}
            options['asset_sources'] = host.asset_sources
            if original.__name__ == '_project_asset_media':
                options['asset_previews'] = True
            if 'asset' in grants:
                options['asset_input_root'] = host.asset_input_root
            with runtime.runtime_access(host.store, pin=pin, selected=selected, **options) as bound:
                response = await function(request)
                host.check()
                bound.check()
                if response.status < 400:
                    response.headers['X-H3-Storage-Pin'] = json.dumps(
                        bound.output_pin, sort_keys=True, separators=(',', ':'))
                    response.headers['Cache-Control'] = 'no-store'
                return response
        except (ValueError, TypeError, KeyError) as error:
            return web.json_response({'error': str(error), 'code': 'h3_storage_request_conflict'}, status=409)
    wrapped._h3_storage_request = True
    return wrapped


def generation_operation(function, unique_id):
    """Get only this callable/node's explicitly issued host identity."""
    return _writer_operation('generation', function, unique_id)


def processing_operation(function, unique_id):
    return _writer_operation('processing', function, unique_id)


def export_operation(function, unique_id):
    return _writer_operation('export', function, unique_id)


def handoff_operation(function, unique_id):
    return _writer_operation('handoff', function, unique_id)


def history_operation(function, unique_id):
    return _writer_operation('history', function, unique_id)


def asset_operation(function, unique_id):
    return _writer_operation('asset', function, unique_id)


def _writer_operation(domain, function, unique_id):
    host = _HOST.get()
    if host is None:
        return None
    host.check()
    function = inspect.unwrap(getattr(function, '__func__', function))
    if host.operation_namespace is not None:
        if function not in getattr(host, domain+'_writers'):
            raise ValueError('Job operation identity requires a separate exact writer grant.')
        if not isinstance(unique_id, str) or not unique_id:
            raise ValueError('Retryable '+domain+' node requires its exact execution unique_id.')
        identity = json.dumps([domain, function.__module__, function.__qualname__, unique_id],
                              separators=(',', ':'))
        return uuid.uuid5(uuid.UUID(host.operation_namespace), identity).hex
    operations = getattr(host, domain+'_operations')
    if any(item[0] is function for item in operations) and (function, unique_id) not in operations:
        raise ValueError('Retryable '+domain+' node requires its exact host-registered unique_id.')
    return operations.get((function, unique_id))


def prepare_inputs(values, function):
    """Run an exact host-granted adapter before the strict mixed-pin gate.

    Adapters verify persisted successor receipts. They grant no write access,
    cannot replace an active runtime, and never come from workflow JSON.
    """
    host = _HOST.get()
    if host is None:
        return values
    host.check()
    adapter = host.input_adapters.get(inspect.unwrap(function))
    if adapter is None:
        return values
    if runtime._ACTIVE.get() is not None:
        if adapter.__name__ == 'plan_asset_inputs' and adapter.__module__ == (
                (__package__+'.' if __package__ else '')+'storage_project_assets'):
            # Studio builds a Plan internally. Its outer adapter has already
            # normalized the asset envelope; nested node_operation still
            # checks exact pins and branch, without rebinding the runtime.
            return values
        raise ValueError('A node input adapter cannot rebind an active runtime.')
    prepared = adapter(host.store, dict(values))
    host.check()
    if not isinstance(prepared, dict) or set(prepared) != set(values):
        raise ValueError('Node input adapter changed the function argument contract.')
    return prepared


@contextmanager
def node_operation(values, selected, *, function=None):
    carriers = _inputs(values)
    pins = [item[PIN_KEY] for item in carriers if PIN_KEY in item]
    pin = pins[0] if pins else None
    if pins:
        # Python considers True == 1 and 1.0 == 1. Wire identities must not:
        # malformed secondary pins cannot borrow validation from the first.
        identities = {_pin_identity(item) for item in pins}
        if len(identities) != 1:
            raise ValueError('Conflicting or invalid H3 storage pins; no mixed-root execution.')
    active, host = runtime._ACTIVE.get(), _HOST.get()
    if active is None and host is None:
        if pins:
            raise ValueError('H3 storage pin requires explicit copy-only host access.')
        yield None
        return
    if host is not None:
        host.check()
    if active is not None:
        active.check()
    store = active.store if active is not None else host.store
    run = store.project.name
    explicit_runs = {str(item['run_name']) for item in carriers if item.get('run_name')}
    assets = values.get('project_assets')
    if isinstance(assets, dict) and assets.get('project'):
        # Plan.build explicitly uses the connected project's name instead of
        # its legacy run_name widget. Match that existing routing contract.
        explicit_runs.add(str(assets['project']))
    elif values.get('run_name') and not (
            isinstance(values.get('plan'), dict) and function is not None
            and inspect.unwrap(function).__qualname__ == 'MiniMaxH3ChainPlanStudio.passthrough'):
        explicit_runs.add(str(values['run_name']))
    if selected:
        explicit_runs.add(str(selected[0]))
    if pins:
        explicit_runs.add(str(pin.get('run_name') or ''))
    if not explicit_runs:
        # Unrelated utility/model nodes do not acquire project storage access.
        if active is None:
            yield None
        else:
            branch_token = active.node_branch_write.set(False)
            handoff_token = active.node_handoff_write.set(False)
            generation_token = active.node_generation_write.set(False)
            processing_token = active.node_processing_write.set(False)
            export_token = active.node_export_write.set(False)
            retention_token = active.node_retention_write.set(False)
            history_token = active.node_history_write.set(False)
            asset_token = active.node_asset_write.set(False)
            try:
                yield None
                active.check()
            finally:
                active.node_asset_write.reset(asset_token)
                active.node_history_write.reset(history_token)
                active.node_retention_write.reset(retention_token)
                active.node_export_write.reset(export_token)
                active.node_processing_write.reset(processing_token)
                active.node_generation_write.reset(generation_token)
                active.node_handoff_write.reset(handoff_token)
                active.node_branch_write.reset(branch_token)
        return
    if explicit_runs != {run}:
        raise ValueError('H3 node carrier belongs to a different project.')
    branches = {branch_id(item['_branch_id']) for item in carriers if '_branch_id' in item}
    if selected:
        branches.add(branch_id(selected[1]))
    if pins:
        branches.add(branch_id(pin.get('branch_id')))
    if len(branches) > 1:
        raise ValueError('H3 node carriers select different branches.')
    branch = next(iter(branches), 'main')
    branch_write = host is not None and function is not None and inspect.unwrap(function) in host.branch_writers
    handoff_write = host is not None and function is not None and inspect.unwrap(function) in host.handoff_writers
    generation_write = host is not None and function is not None and inspect.unwrap(function) in host.generation_writers
    processing_write = host is not None and function is not None and inspect.unwrap(function) in host.processing_writers
    export_write = host is not None and function is not None and inspect.unwrap(function) in host.export_writers
    retention_write = host is not None and function is not None and inspect.unwrap(function) in host.retention_writers
    history_write = host is not None and function is not None and inspect.unwrap(function) in host.history_writers
    asset_write = host is not None and function is not None and inspect.unwrap(function) in host.asset_writers
    write = branch_write or handoff_write or generation_write or processing_write or export_write or retention_write or history_write or asset_write
    proofs = [item['_project_ownership'] for item in carriers if '_project_ownership' in item]
    if asset_write and values.get('ownership_json'):
        supplied = values['ownership_json']
        supplied = json.loads(supplied) if isinstance(supplied, str) else supplied
        if not isinstance(supplied, dict):
            raise ValueError('Invalid asset node ownership proof.')
        proofs.append(dict(owner_id=str(supplied.get('owner_id') or '').strip(), epoch=int(supplied.get('epoch'))))
    proof = proofs[0] if proofs else None
    if write and any(item != proof for item in proofs):
        raise ValueError('Conflicting node ownership proofs.')
    if active is not None:
        if active.ownership_writes or ((active.branch_writes or active.handoff_writes or active.generation_writes or active.processing_writes or active.export_writes or active.retention_writes or active.history_writes or active.asset_writes) and host is None):
            raise ValueError('Writable nested node calls require explicit node-host grants.')
        if active.selected != branch or (pins and _pin_identity(active.pin) != _pin_identity(pin)):
            raise ValueError('H3 node carrier differs from its active runtime pin.')
        if branch_write and (not active.branch_writes or active.node_branch_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested writer cannot elevate its caller.')
        if handoff_write and (not active.handoff_writes or active.node_handoff_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested handoff writer cannot elevate its caller.')
        if generation_write and (not active.generation_writes or active.node_generation_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested generation writer cannot elevate its caller.')
        if processing_write and (not active.processing_writes or active.node_processing_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested processing writer cannot elevate its caller.')
        if export_write and (not active.export_writes or active.node_export_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested export writer cannot elevate its caller.')
        if retention_write and (not active.retention_writes or active.node_retention_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested retention writer cannot elevate its caller.')
        if history_write and (not active.history_writes or active.node_history_write.get() is False):
            raise ValueError('Runtime binding is read-only; nested history writer cannot elevate its caller.')
        if asset_write and (not active.asset_writes or active.node_asset_write.get() is False
                            or active.asset_input_root != Path(host.asset_input_root)):
            raise ValueError('Runtime binding is read-only; nested asset writer cannot elevate its caller.')
        if write and proofs and proof != active.node_write_proof:
            raise ValueError('Nested node cannot change its operation ownership proof.')
        token = active.node_branch_write.set(bool(branch_write and active.branch_writes))
        handoff_token = active.node_handoff_write.set(bool(handoff_write and active.handoff_writes))
        generation_token = active.node_generation_write.set(bool(generation_write and active.generation_writes))
        processing_token = active.node_processing_write.set(bool(processing_write and active.processing_writes))
        export_token = active.node_export_write.set(bool(export_write and active.export_writes))
        retention_token = active.node_retention_write.set(bool(retention_write and active.retention_writes))
        history_token = active.node_history_write.set(bool(history_write and active.history_writes))
        asset_token = active.node_asset_write.set(bool(asset_write and active.asset_writes))
        try:
            yield active
            active.check()
        finally:
            active.node_asset_write.reset(asset_token)
            active.node_history_write.reset(history_token)
            active.node_retention_write.reset(retention_token)
            active.node_export_write.reset(export_token)
            active.node_processing_write.reset(processing_token)
            active.node_generation_write.reset(generation_token)
            active.node_handoff_write.reset(handoff_token)
            active.node_branch_write.reset(token)
    else:
        with runtime.runtime_access(store, pin=pin, selected=branch, branch_writes=branch_write,
                                    handoff_writes=handoff_write, generation_writes=generation_write,
                                    processing_writes=processing_write, export_writes=export_write,
                                    retention_writes=retention_write, history_writes=history_write,
                                    asset_writes=asset_write,
                                    asset_input_root=host.asset_input_root if asset_write else None) as bound:
            if write:
                bound.node_write_proof = copy.deepcopy(proof)
            yield bound
            host.check()
            bound.check()


def stamp(value, bound):
    """Copy only envelopes; never mutate ComfyUI's cached upstream inputs."""
    if bound is None:
        return value
    bound.check()
    if isinstance(value, tuple):
        return tuple(stamp(item, bound) for item in value)
    if not isinstance(value, dict):
        return value
    result = value
    asset_envelope = (value.get('format') == 'h3_project_assets_v1'
                      and isinstance(value.get('catalog'), dict) and value.get('project'))
    if value.get('run_name') or asset_envelope:
        if str(value.get('run_name') or value['project']) != bound.run:
            raise ValueError('Node output belongs to a different project.')
        if '_branch_id' in value and branch_id(value['_branch_id']) != bound.selected:
            raise ValueError('Node output belongs to a different branch.')
        # A returned input envelope can contain this operation's original pin.
        # Only a verified successful receipt may advance it to the saved root.
        accepted = bound.output_pin
        if PIN_KEY in value and _pin_identity(value[PIN_KEY]) not in {
                _pin_identity(pin) for pin in bound.output_pins}:
            raise ValueError('Node output contains a conflicting storage pin.')
        result = dict(result, _branch_id=bound.selected, **{PIN_KEY: copy.deepcopy(accepted)})
    for key in _NESTED + ('result',):
        if key in value:
            nested = value[key]
            changed = ([stamp(item, bound) for item in nested] if key == 'result' and isinstance(nested, list)
                       else stamp(nested, bound))
            if changed is not nested:
                result = dict(result, **{key: changed})
    return result
