"""Operation-local runtime binding for isolated combined-storage rehearsals.

This is not project activation: callers must already hold explicit copy-only
storage access. Normal service constructors share one accepted root inside the
binding. Outside it, legacy behavior and the combined-layout rejection remain.
No process-global output directory or filesystem function is replaced.
"""
import copy
import asyncio
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

if __package__:
    from .branch_scope import branch_id, branch_scope
    from .storage_branch_controls import BranchControlDocuments
    from .storage_project_reads import ProjectReadView
    from .storage_project import ProjectStore
    from .storage_state import Snapshot, StateConflict
    from .storage_handoff_runtime import RuntimeHandoffDocuments
    from .storage_generation import RuntimeGeneration
    from .storage_delivery import RuntimeDelivery
    from .storage_processing import RuntimeProcessing
    from .storage_exports import RuntimeExports
    from .storage_review_controls import RuntimeReviewDocuments
    from .storage_retention import RuntimeRetention
    from .storage_prompt_history import RuntimePromptHistory
    from .storage_project_assets import RuntimeProjectAssets
else:
    from branch_scope import branch_id, branch_scope
    from storage_branch_controls import BranchControlDocuments
    from storage_project_reads import ProjectReadView
    from storage_project import ProjectStore
    from storage_state import Snapshot, StateConflict
    from storage_handoff_runtime import RuntimeHandoffDocuments
    from storage_generation import RuntimeGeneration
    from storage_delivery import RuntimeDelivery
    from storage_processing import RuntimeProcessing
    from storage_exports import RuntimeExports
    from storage_review_controls import RuntimeReviewDocuments
    from storage_retention import RuntimeRetention
    from storage_prompt_history import RuntimePromptHistory
    from storage_project_assets import RuntimeProjectAssets


PIN_FORMAT = 'h3_storage_runtime_pin_v1'
_ACTIVE = ContextVar('h3_storage_runtime', default=None)
_NO_NODE_PROOF = object()


class _BranchDocuments(BranchControlDocuments):
    def __init__(self, runtime):
        super().__init__(runtime.store, base=runtime.base)
        self.runtime = runtime

    @contextmanager
    def operation(self):
        self.runtime.check()
        with super().operation():
            yield
            self.runtime.check()

    def write(self, path, value):
        self._require_write()
        self._require_branch(path)
        return super().write(path, value)

    def retire_pointer(self, path):
        self._require_write()
        self._require_branch(path)
        return super().retire_pointer(path)

    def _require_branch(self, path):
        scope = self._contract(self._address(path))[0]
        if self.runtime.has_node_proof and scope.startswith('branch:') and scope != 'branch:'+self.runtime.selected:
            raise ValueError('Node branch write belongs to a different runtime branch.')

    def _require_write(self):
        self.runtime.check()
        if not self.runtime.branch_writes or self.runtime.node_branch_write.get() is False:
            raise ValueError('Runtime binding is read-only; branch writes were not enabled.')

    @contextmanager
    def _commit_guard(self):
        self._require_write()
        if self.runtime.node_write_proof is _NO_NODE_PROOF:
            yield
            return
        if __package__:
            from .project_ownership import project_write_guard
        else:
            from project_ownership import project_write_guard
        # Only the short publication is locked, never the preceding node work.
        with project_write_guard(self.runtime.output, self.runtime.run,
                                 self.runtime.node_write_proof, 'save node branch settings'):
            yield

    def _commit(self, session):
        with self._commit_guard():
            return super()._commit(session)

    def retry_save(self, path, operation_id, request_hash):
        # Exact retries are still writer operations, not a way to bypass current
        # grants or recover a former owner's queued execution after takeover.
        with self._commit_guard():
            self._require_branch(path)
            return super().retry_save(path, operation_id, request_hash)

    def _committed(self, receipt):
        self.runtime.record_commit(receipt)


class _ReadView(ProjectReadView):
    def __init__(self, runtime):
        super().__init__(runtime.store, base=runtime.base)
        self.runtime = runtime

    @contextmanager
    def operation(self):
        self.runtime.check()
        with super().operation():
            yield
            self.runtime.check()

    @asynccontextmanager
    async def async_operation(self):
        await asyncio.to_thread(self.runtime.check)
        async with super().async_operation():
            yield
            await asyncio.to_thread(self.runtime.check)


class ProjectRuntime:
    def __init__(self, store, *, pin=None, selected='main', branch_writes=False, ownership_writes=False,
                 handoff_writes=False, generation_writes=False, processing_writes=False, export_writes=False,
                 retention_writes=False, history_writes=False, asset_writes=False, asset_input_root=None,
                 asset_previews=False, asset_sources=()):
        if not isinstance(store, ProjectStore):
            raise TypeError('Runtime binding requires an explicit combined ProjectStore.')
        if type(branch_writes) is not bool:
            raise ValueError('Branch write capability must be an explicit boolean.')
        if type(ownership_writes) is not bool:
            raise ValueError('Ownership write capability must be an explicit boolean.')
        if type(handoff_writes) is not bool:
            raise ValueError('Handoff write capability must be an explicit boolean.')
        if type(generation_writes) is not bool:
            raise ValueError('Generation write capability must be an explicit boolean.')
        if type(processing_writes) is not bool:
            raise ValueError('Processing write capability must be an explicit boolean.')
        if type(export_writes) is not bool:
            raise ValueError('Export write capability must be an explicit boolean.')
        if type(retention_writes) is not bool:
            raise ValueError('Retention write capability must be an explicit boolean.')
        if type(history_writes) is not bool:
            raise ValueError('History write capability must be an explicit boolean.')
        if type(asset_writes) is not bool:
            raise ValueError('Asset backup write capability must be an explicit boolean.')
        if type(asset_previews) is not bool:
            raise ValueError('Asset preview cache capability must be an explicit boolean.')
        self.store, self.project = store, store.project
        self.output = self.project.parent.parent
        self.run, self.selected = self.project.name, branch_id(selected)
        if __package__:
            from .storage_asset_sources import validate_sources
        else:
            from storage_asset_sources import validate_sources
        self.asset_sources = validate_sources(asset_sources, self.run)
        current = store.snapshot()  # Checks copy-only access and current ready gate.
        self.base = current
        if pin is not None:
            if (not isinstance(pin, dict)
                    or set(pin) != {'format', 'run_name', 'branch_id', 'epoch', 'root'}
                    or pin['format'] != PIN_FORMAT or pin['run_name'] != self.run
                    or pin['branch_id'] != self.selected or type(pin['epoch']) is not int):
                raise ValueError('Runtime pin does not match the requested project and branch.')
            self.base = Snapshot(self.project, pin['root'])
            store.validate_snapshot(self.base, current=current)
            if pin['epoch'] != self.base.state['epoch']:
                raise StateConflict('Runtime pin epoch does not match its saved root.')
        self.epoch = self.base.state['epoch']
        if self.epoch != current.state['epoch']:
            raise StateConflict('Runtime pin was fenced by a storage epoch change.')
        self.branch_writes = branch_writes
        self.ownership_writes = ownership_writes
        self.handoff_writes = handoff_writes
        self.generation_writes = generation_writes
        self.processing_writes = processing_writes
        self.export_writes = export_writes
        self.retention_writes = retention_writes
        self.history_writes = history_writes
        self.asset_writes = asset_writes
        self.asset_previews = asset_previews
        if asset_input_root is not None:
            if not asset_writes or not Path(asset_input_root).is_absolute():
                raise ValueError('Input asset edits need an explicit absolute input root and asset write grant.')
            if __package__:
                from .storage_resolver import confined
            else:
                from storage_resolver import confined
            confined(asset_input_root, 'h3_projects/'+self.run)
        self.asset_input_root = Path(asset_input_root).absolute() if asset_input_root is not None else None
        self.closed = False
        self.accepted = self.base
        self._result_roots = [self.base.reference]
        self.node_write_proof = _NO_NODE_PROOF
        # Nested scoped helpers must not inherit a caller's node write grant.
        # None is direct host/API access, outside the node-host boundary.
        self.node_branch_write = ContextVar('h3_runtime_node_branch_write', default=None)
        self.node_handoff_write = ContextVar('h3_runtime_node_handoff_write', default=None)
        self.node_generation_write = ContextVar('h3_runtime_node_generation_write', default=None)
        self.node_processing_write = ContextVar('h3_runtime_node_processing_write', default=None)
        self.node_export_write = ContextVar('h3_runtime_node_export_write', default=None)
        self.node_retention_write = ContextVar('h3_runtime_node_retention_write', default=None)
        self.node_history_write = ContextVar('h3_runtime_node_history_write', default=None)
        self.node_asset_write = ContextVar('h3_runtime_node_asset_write', default=None)
        self.branches, self.reader = _BranchDocuments(self), _ReadView(self)
        self.handoffs = RuntimeHandoffDocuments(self)
        self.reviews = RuntimeReviewDocuments(self)
        self.generation = RuntimeGeneration(self)
        self.delivery = RuntimeDelivery(self)
        self.processing = RuntimeProcessing(self)
        self.exports = RuntimeExports(self)
        self.retention = RuntimeRetention(self)
        self.history = RuntimePromptHistory(self)
        self.assets = RuntimeProjectAssets(self)
        if __package__:
            from .storage_ownership import OwnershipLog
        else:
            from storage_ownership import OwnershipLog
        self.ownership = OwnershipLog(self)

    @property
    def has_node_proof(self):
        return self.node_write_proof is not _NO_NODE_PROOF

    @property
    def pin(self):
        # No absolute paths, prompts or mutable dictionaries escape in the pin.
        return {'format': PIN_FORMAT, 'run_name': self.run, 'branch_id': self.selected,
                'epoch': self.epoch, 'root': self.base.reference}

    @property
    def output_pin(self):
        return dict(self.pin, root=self.accepted.reference)

    @property
    def output_pins(self):
        # Only this operation's input and acknowledged intermediate results.
        # An unrelated concurrent commit is never added to this list.
        return [dict(self.pin, root=dict(root)) for root in self._result_roots]

    def record_commit(self, receipt):
        self.check()
        saved = self.store.committed_snapshot(receipt)
        if saved.state['epoch'] != self.epoch:
            raise StateConflict('Node commit was fenced by a storage epoch change.')
        self.store.validate_snapshot(self.accepted, current=saved)
        self.accepted = saved
        if saved.reference not in self._result_roots:
            self._result_roots.append(saved.reference)

    def check(self):
        if self.closed or _ACTIVE.get() is not self:
            raise ValueError('Runtime service escaped its operation-local binding.')
        current = self.store.snapshot()
        if current._validated_root()['epoch'] != self.epoch:
            raise StateConflict('Runtime operation was fenced by a storage epoch change.')
        return current


def current_runtime(output, run=None):
    """Return only this operation's binding; never fall through across projects."""
    runtime = _ACTIVE.get()
    if runtime is None:
        return None
    if (Path(output).resolve() != runtime.output
            or (run is not None and str(run) != runtime.run)):
        raise ValueError('Runtime service belongs to a different output/project.')
    runtime.check()
    return runtime


@contextmanager
def accepted_asset_read_access(parent):
    """Read this operation's acknowledged asset result; grant no follow-on writes."""
    parent.check()
    child = ProjectRuntime(parent.store, pin=parent.output_pin, selected=parent.selected)
    child.node_write_proof = (copy.deepcopy(parent.node_write_proof)
                              if parent.has_node_proof else _NO_NODE_PROOF)
    for name in ('branch', 'handoff', 'generation', 'processing', 'export',
                 'retention', 'history', 'asset'):
        getattr(child, 'node_'+name+'_write').set(False)
    token = _ACTIVE.set(child)
    try:
        with child.reader.operation():
            yield child
    finally:
        child.closed = True
        _ACTIVE.reset(token)
        parent.check()


@contextmanager
def accepted_export_access(parent):
    """Export only from this operation's acknowledged successor, never latest.

    This internal follow-on deliberately suspends the parent's readers. It does
    not enable arbitrary nested runtime_access, accept a caller-supplied pin,
    change branches, or inherit permission to mutate authoring/generation.
    """
    parent.check()
    parent.exports.require_write()
    child = ProjectRuntime(parent.store, pin=parent.output_pin,
                           selected=parent.selected, export_writes=True)
    child.node_write_proof = (copy.deepcopy(parent.node_write_proof)
                              if parent.has_node_proof else _NO_NODE_PROOF)
    for name in ('branch', 'handoff', 'generation', 'processing', 'retention', 'history', 'asset'):
        getattr(child, 'node_'+name+'_write').set(False)
    child.node_export_write.set(parent.node_export_write.get())
    child.exports.after_stage = parent.exports.after_stage
    token = _ACTIVE.set(child)
    try:
        with child.reader.operation():
            yield child
    finally:
        child.closed = True
        _ACTIVE.reset(token)
        parent.check()
        parent.store.validate_snapshot(parent.accepted, current=child.accepted)
        # Include only receipts acknowledged by the export, including a commit
        # accepted before a later optional output/notification failed.
        parent.accepted = child.accepted
        for root in child._result_roots:
            if root not in parent._result_roots:
                parent._result_roots.append(copy.deepcopy(root))


@contextmanager
def runtime_access(store, *, pin=None, selected='main', branch_writes=False, ownership_writes=False,
                   handoff_writes=False, generation_writes=False, processing_writes=False, export_writes=False,
                   retention_writes=False, history_writes=False, asset_writes=False, asset_input_root=None,
                   asset_previews=False, asset_sources=()):
    """One pinned request/job operation on an explicitly permitted test copy.

Branch documents, external ownership, checkpoint reads and scene publication are ported here. Other writers keep
rejecting combined storage. A pin identifies saved data, not write authority.
The host must opt into branch/ownership writes separately; browser JSON cannot grant them.
"""
    if _ACTIVE.get() is not None:
        raise ValueError('A runtime operation cannot switch bindings while active.')
    runtime = ProjectRuntime(store, pin=pin, selected=selected, branch_writes=branch_writes,
                             ownership_writes=ownership_writes, handoff_writes=handoff_writes,
                             generation_writes=generation_writes, processing_writes=processing_writes,
                             export_writes=export_writes, retention_writes=retention_writes,
                             history_writes=history_writes, asset_writes=asset_writes,
                             asset_input_root=asset_input_root, asset_previews=asset_previews,
                             asset_sources=asset_sources)
    token = _ACTIVE.set(runtime)
    try:
        with branch_scope(runtime.run, runtime.selected), runtime.reader.operation():
            yield runtime
    finally:
        # Child tasks/threads inherit ContextVars. Resetting the parent's token
        # alone does not revoke their captured operation after the request ends.
        runtime.closed = True
        _ACTIVE.reset(token)


@asynccontextmanager
async def async_runtime_access(store, **options):
    """HTTP binding without blocking the server on index/verification reads."""
    if _ACTIVE.get() is not None:
        raise ValueError('A runtime operation cannot switch bindings while active.')
    runtime = await asyncio.to_thread(ProjectRuntime, store, **options)
    token = _ACTIVE.set(runtime)
    try:
        with branch_scope(runtime.run, runtime.selected):
            async with runtime.reader.async_operation():
                yield runtime
    finally:
        runtime.closed = True
        _ACTIVE.reset(token)
