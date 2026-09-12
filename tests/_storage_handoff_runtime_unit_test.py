"""Normal handoff services and independent queued-node storage receipts."""
import copy
from contextvars import copy_context
import unittest
from unittest.mock import patch
import uuid

import _storage_carriers_unit_test as fixture
from branch_scope import scoped_node, current_branch
from storage_carriers import node_host, PIN_KEY
from storage_runtime import runtime_access, current_runtime
from handoff_state import HandoffStore, HandoffNotFoundError, HandoffClaimError
from working_branches import WorkingBranches
import project_ownership as ownership
import storage_state as state


class HandoffRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CarrierTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        @scoped_node
        def create(state):
            record = HandoffStore(str(self.output)).create('demo', action='next_scene',
                scene=5, start_clip=5, end_clip=7, seed=18446744073709551613,
                handoff_id='runtime-next', working_branch_id=current_branch('demo'))
            return (dict(state, handoff=record),)
        @scoped_node
        def step(state, status):
            handoffs = HandoffStore(str(self.output))
            if status == 'claimed':
                record = handoffs.claim('demo', 'runtime-next', claimant='test-worker')
            else:
                record = handoffs.transition('demo', 'runtime-next', status)
            return (dict(state, handoff=record),)
        @scoped_node
        def read(state):
            return (dict(state, handoff=HandoffStore(str(self.output)).load('demo', 'runtime-next')),)
        self.create, self.step, self.read = create, step, read
        with node_host(self.store):
            self.input = self.f.named_start()

    def test_normal_constructor_uses_operation_local_port(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named) as runtime:
            service = HandoffStore(str(self.output))
            self.assertIs(service.controls, runtime.handoffs)
            self.assertEqual(service.list('demo'), [])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_independent_nodes_keep_exact_state_machine_results_and_seed(self):
        with node_host(self.store, handoff_writers=(self.create, self.step)):
            value = self.create(self.input)[0]
            for status in ('claimed', 'queued', 'consumed'):
                value = self.step(value, status)[0]
                self.assertEqual(value['handoff']['status'], status)
                self.assertEqual(self.read(value)[0]['handoff'], value['handoff'])
        self.assertEqual(value['handoff']['seed'], 18446744073709551613)
        self.assertEqual(value[PIN_KEY]['root'], self.store.snapshot().reference)
        self.assertFalse((self.store.project/'orchestration').exists())

    def test_old_carrier_does_not_read_new_handoff(self):
        original = copy.deepcopy(self.input)
        with node_host(self.store, handoff_writers=(self.create,)):
            output = self.create(self.input)[0]
            with self.assertRaises(HandoffNotFoundError):
                self.read(self.input)
            self.assertEqual(self.read(output)[0]['handoff'], output['handoff'])
        self.assertEqual(self.input, original)

    def test_branch_write_grant_cannot_enable_handoff_write(self):
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.create,)):
            with self.assertRaisesRegex(ValueError, 'handoff writes'):
                self.create(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_handoff_write_grant_cannot_enable_branch_write(self):
        @scoped_node
        def wrong(state):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            branches.save(self.named, old['authoring'], old['revision'])
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(wrong,)):
            with self.assertRaisesRegex(ValueError, 'branch writes'):
                wrong(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def claim_owner(self, owner, force=False):
        with runtime_access(self.store, ownership_writes=True):
            return ownership.claim_project_ownership(self.output, 'demo', owner, force=force)

    def test_ownership_is_rechecked_at_handoff_commit(self):
        owner = 'handoff-owner-1234567890'
        claimed = self.claim_owner(owner)
        before = self.store.snapshot().reference
        carrier = dict(self.input, _project_ownership={'owner_id': owner, 'epoch': claimed['epoch']})
        with node_host(self.store, handoff_writers=(self.create, self.step)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.create(self.input)
            self.assertEqual(self.store.snapshot().reference, before)
            output = self.create(carrier)[0]
        self.claim_owner('handoff-new-owner-1234567890', True)
        with node_host(self.store, handoff_writers=(self.step,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.step(output, 'claimed')
        self.assertEqual(self.store.snapshot().reference, output[PIN_KEY]['root'])

    def test_stale_queued_claim_cannot_win_twice(self):
        with node_host(self.store, handoff_writers=(self.create, self.step)):
            pending = self.create(self.input)[0]
            claimed = self.step(pending, 'claimed')[0]
            with self.assertRaises(state.StateConflict):
                self.step(pending, 'claimed')
            with self.assertRaises(HandoffClaimError):
                self.step(claimed, 'claimed')
        self.assertEqual(self.store.snapshot().reference, claimed[PIN_KEY]['root'])

    def test_changed_source_branch_rejects_claim(self):
        with node_host(self.store, handoff_writers=(self.create,)):
            pending = self.create(self.input)[0]
        self.f.f.publish()
        with node_host(self.store, handoff_writers=(self.step,)):
            with self.assertRaises(state.StateConflict):
                self.step(pending, 'claimed')
            latest = self.f.named_start()
            with self.assertRaisesRegex(state.StateConflict, 'source branch'):
                self.step(latest, 'claimed')

    def test_services_and_worker_contexts_expire_with_runtime(self):
        with runtime_access(self.store) as runtime:
            service = HandoffStore(str(self.output))
            captured = copy_context()
        with self.assertRaisesRegex(ValueError, 'escaped'):
            service.list('demo')
        with self.assertRaisesRegex(ValueError, 'escaped'):
            captured.run(lambda: HandoffStore(str(self.output)).list('demo'))

    def test_grandchild_handoff_writer_cannot_borrow_grandparents_grant(self):
        @scoped_node
        def middle(state):
            return self.create(state)
        @scoped_node
        def parent(state):
            return middle(state)
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(parent, self.create)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                parent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_unscoped_utility_cannot_borrow_handoff_write_access(self):
        @scoped_node
        def utility():
            return HandoffStore(str(self.output)).create('demo', action='next_scene',
                scene=5, handoff_id='wrong', working_branch_id=self.named)
        @scoped_node
        def parent(state):
            return utility()
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(parent,)):
            with self.assertRaisesRegex(ValueError, 'handoff writes'):
                parent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_node_cannot_write_another_branchs_handoff(self):
        @scoped_node
        def wrong(state):
            return HandoffStore(str(self.output)).create('demo', action='next_scene',
                scene=5, handoff_id='wrong', working_branch_id='main')
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(wrong,)):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                wrong(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_lost_handoff_ack_never_returns_success_or_permits_duplicate_claim(self):
        with node_host(self.store, handoff_writers=(self.create,)):
            pending = self.create(self.input)[0]
        real = self.store.commit
        def fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost handoff claim ack')
        with node_host(self.store, handoff_writers=(self.step,)), patch.object(self.store, 'commit', fail):
            with self.assertRaisesRegex(OSError, 'claim ack'):
                self.step(pending, 'claimed')
        accepted = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(self.step,)):
            latest = self.f.named_start()
            self.assertEqual(self.read(latest)[0]['handoff']['status'], 'claimed')
            with self.assertRaises(HandoffClaimError):
                self.step(latest, 'claimed')
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_branch_save_then_handoff_uses_accepted_branch_revision(self):
        @scoped_node
        def save_branch(state):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            saved = branches.save(self.named, dict(old['authoring'], base_seed='18446744073709551603'),
                                  old['revision'], uuid.uuid4().hex)
            return (dict(state, saved=saved),)
        @scoped_node
        def combined(state):
            intermediate = save_branch(state)[0]
            bound = current_runtime(self.output, 'demo')
            branch_root = self.store.committed_snapshot(next(item for item in bound.accepted.state['operations'].values()
                if item['generation'] == bound.accepted.state['generation']))
            record = HandoffStore(str(self.output)).create('demo', action='next_scene', scene=5,
                handoff_id='runtime-next', working_branch_id=self.named)
            self.assertEqual(record['storage_dependency']['revision'], branch_root.state['scope_revisions']['branch:'+self.named])
            return (dict(intermediate, handoff=record),)
        with node_host(self.store, branch_writers=(combined, save_branch), handoff_writers=(combined, self.step)):
            pending = combined(self.input)[0]
            claimed = self.step(pending, 'claimed')[0]
        self.assertEqual(claimed['handoff']['status'], 'claimed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
