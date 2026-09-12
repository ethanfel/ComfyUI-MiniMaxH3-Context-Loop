"""Operation-local handoff port; grants never imply queue submission authority."""
from contextlib import contextmanager

if __package__:
    from .storage_handoff_controls import HandoffControlDocuments
else:
    from storage_handoff_controls import HandoffControlDocuments


class RuntimeHandoffDocuments(HandoffControlDocuments):
    def __init__(self, runtime):
        super().__init__(runtime.store, base=runtime.base)
        self.runtime = runtime

    @contextmanager
    def operation(self, *, write=False):
        self.runtime.check()
        if write:
            self._require_write()
        with super().operation(write=write):
            yield
            self.runtime.check()

    def _snapshot_for_operation(self, current, *, write):
        # A Loop End may publish its branch checkpoint before creating a handoff.
        # The handoff must watch that accepted branch revision, not its old input.
        # Read-only operations elsewhere still retain the original runtime pin.
        self.runtime.check()
        return self.runtime.accepted if write else self.runtime.base

    def _require_write(self):
        self.runtime.check()
        if not self.runtime.handoff_writes or self.runtime.node_handoff_write.get() is False:
            raise ValueError('Runtime binding is read-only; handoff writes were not enabled.')

    @contextmanager
    def staged(self, base):
        """Collect a delivery's handoff writes without independently committing.

        The delivery publisher owns the single acceptance boundary. Ordinary
        handoff operations retain their existing automatic commit behavior.
        """
        self._require_write()
        if self._operation.get() is not None or base.reference != self.runtime.base.reference:
            raise ValueError('Handoff staging needs a fresh matching runtime operation.')
        self.runtime.store.validate_snapshot(base, current=self.runtime.check())
        session = {'base':base, 'changes':{}, 'reads':{'jobs:handoffs'}, 'error':None}
        token = self._operation.set(session)
        try:
            yield session
            self._require_write()
            if session['error'] is not None:
                raise session['error']
        finally:
            self._operation.reset(token)

    def write(self, path, value):
        self._require_write()
        if value.get('working_branch_id', 'main') != self.runtime.selected:
            raise ValueError('Handoff write belongs to a different runtime branch.')
        return super().write(path, value)

    def _commit(self, session):
        self._require_write()
        if self.runtime.has_node_proof:
            if __package__:
                from .project_ownership import project_write_guard
            else:
                from project_ownership import project_write_guard
            with project_write_guard(self.runtime.output, self.runtime.run,
                                     self.runtime.node_write_proof, 'save node handoff'):
                return super()._commit(session)
        return super()._commit(session)

    def _committed(self, receipt):
        self.runtime.record_commit(receipt)
