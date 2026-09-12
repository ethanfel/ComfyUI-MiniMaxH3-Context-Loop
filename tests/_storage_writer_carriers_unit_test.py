"""Exact writer output pins, host-only grants and commit-time ownership checks."""
import copy
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_carriers_unit_test as fixture
from branch_scope import scoped_node
from storage_carriers import node_host, PIN_KEY
from storage_runtime import runtime_access, current_runtime
from working_branches import WorkingBranches
import project_ownership as ownership
import storage_state as state


class WriterCarrierTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CarrierTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        @scoped_node
        def save(state, prompt='Node writer é 雪', seed='18446744073709551609', operation_id=''):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            authored = copy.deepcopy(old['authoring'])
            plan = json.loads(authored['plan_json'])
            plan['shots'][0].update(prompt=prompt, seed=seed)
            authored['plan_json'] = json.dumps(plan, ensure_ascii=False)
            saved = branches.save(self.named, authored, old['revision'], operation_id or uuid.uuid4().hex)
            return (dict(state, saved=saved, plan=dict(state['plan'], saved=saved)),)
        self.save = save
        with node_host(self.store):
            self.input = self.f.named_start()

    def test_saved_output_uses_its_commit_and_next_node_reads_new_settings(self):
        original = copy.deepcopy(self.input)
        with node_host(self.store, branch_writers=(self.save,)):
            output = self.save(self.input)[0]
        self.assertEqual(self.input, original)
        self.assertNotEqual(output[PIN_KEY]['root'], self.input[PIN_KEY]['root'])
        self.assertEqual(output[PIN_KEY], output['plan'][PIN_KEY])
        with node_host(self.store):
            delivered = self.f.read(output)[0]
        self.assertEqual(delivered['saved'], output['saved'])
        shot = json.loads(delivered['saved']['authoring']['plan_json'])['shots'][0]
        self.assertEqual(shot['seed'], '18446744073709551609')
        self.assertEqual(shot['prompt'], 'Node writer é 雪')
        self.assertEqual(delivered['saved']['authoring']['width'], 960)

    def test_later_writer_does_not_replace_this_nodes_output_pin(self):
        @scoped_node
        def racing(state):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            saved = branches.save(self.named, dict(old['authoring'], base_seed='111'), old['revision'])
            runtime = current_runtime(self.output, 'demo')
            accepted = runtime.output_pin
            # Simulate another cooperating operation between this node's save
            # and return; a different control scope commits a newer root.
            self.store.commit(self.store.snapshot(), {'branches/default.json': {
                'data': state_module._encode({'format': 'unrelated-test', 'id': self.named}),
                'scope': 'project', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
            self.assertEqual(runtime.output_pin, accepted)
            return (dict(state, saved=saved),)
        state_module = state
        with node_host(self.store, branch_writers=(racing,)):
            output = racing(self.input)[0]
        self.assertNotEqual(output[PIN_KEY]['root'], self.store.snapshot().reference)
        with node_host(self.store):
            self.assertEqual(self.f.read(output)[0]['saved'], output['saved'])

    def test_exact_receipt_resolves_original_commit_after_later_commits(self):
        def commit(value):
            return self.store.commit(self.store.snapshot(), {'plan.json': {
                'data': state._encode({'value': value}), 'scope': 'branch:main',
                'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        receipt = commit('one')
        exact = self.store.snapshot()
        commit('two')
        self.assertEqual(self.store.committed_snapshot(receipt).reference, exact.reference)
        for changes in ({'generation': float(receipt['generation'])}, {'generation': receipt['generation']+1},
                        {'operation_id': uuid.uuid4().hex}, {'request_sha256': '0'*64}, {'extra': True}):
            with self.assertRaises(ValueError):
                self.store.committed_snapshot(dict(receipt, **changes))

    def test_writer_grant_does_not_leak_to_another_node_or_json(self):
        @scoped_node
        def other(state):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            branches.save(self.named, old['authoring'], old['revision'])
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                other(dict(self.input, branch_writers=True))
        with self.assertRaisesRegex(ValueError, 'callables'):
            with node_host(self.store, branch_writers=('save',)):
                self.fail('Serialized writer grant was accepted.')

    def claim(self, owner, force=False):
        with runtime_access(self.store, ownership_writes=True):
            return ownership.claim_project_ownership(self.output, 'demo', owner, force=force)

    def test_protected_project_requires_current_proof_at_commit(self):
        owner = 'writer-carrier-owner-12345'
        owned = self.claim(owner)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.save(self.input)
            self.assertEqual(before, self.store.snapshot().reference)
            carrier = dict(self.input, _project_ownership={'owner_id': owner, 'epoch': owned['epoch']})
            output = self.save(carrier)[0]
        self.assertNotEqual(output[PIN_KEY]['root'], before)

    def test_forced_takeover_fences_queued_writer_but_keeps_saved_work(self):
        owner = 'writer-carrier-owner-12345'
        owned = self.claim(owner)
        carrier = dict(self.input, _project_ownership={'owner_id': owner, 'epoch': owned['epoch']})
        self.claim('writer-carrier-other-12345', True)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.save(carrier)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_failed_ack_never_returns_a_success_output_pin(self):
        # Replacement-mode test fixture: simulate a failure after publication,
        # just before the branch port receives the successful storage receipt.
        real = self.store.commit
        def fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost receipt acknowledgement')
        with node_host(self.store, branch_writers=(self.save,)), patch.object(self.store, 'commit', fail):
            with self.assertRaisesRegex(OSError, 'lost receipt'):
                self.save(self.input)
        self.assertIsNone(current_runtime(self.output))
        with node_host(self.store):
            latest = self.f.named_start()
        self.assertNotEqual(latest[PIN_KEY], self.input[PIN_KEY])

    def test_stale_queued_write_cannot_rebase_onto_someone_elses_settings(self):
        with node_host(self.store, branch_writers=(self.save,)):
            first = self.save(self.input)[0]
            with self.assertRaises(state.StateConflict):
                self.save(self.input, prompt='stale replacement')
        with node_host(self.store):
            self.assertEqual(self.f.read(first)[0]['saved'], first['saved'])

    def lose_ack(self, carrier, operation_id):
        real = self.store.commit
        def fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost saved result')
        with node_host(self.store, branch_writers=(self.save,)), patch.object(self.store, 'commit', fail):
            with self.assertRaisesRegex(OSError, 'lost saved result'):
                self.save(carrier, operation_id=operation_id)
        return self.store.snapshot()

    def test_lost_ack_retry_reuses_exact_saved_result_without_second_commit(self):
        operation = uuid.uuid4().hex
        original = copy.deepcopy(self.input)
        saved = self.lose_ack(self.input, operation)
        with node_host(self.store, branch_writers=(self.save,)):
            recovered = self.save(self.input, operation_id=operation)[0]
        self.assertEqual(self.store.snapshot().reference, saved.reference)
        self.assertEqual(recovered[PIN_KEY]['root'], saved.reference)
        self.assertEqual(self.input, original)
        with node_host(self.store):
            self.assertEqual(self.f.read(recovered)[0]['saved'], recovered['saved'])
        self.assertEqual(json.loads(recovered['saved']['authoring']['plan_json'])['shots'][0]['seed'],
                         '18446744073709551609')

    def test_retry_keeps_original_receipt_after_unrelated_branch_commit(self):
        operation = uuid.uuid4().hex
        saved = self.lose_ack(self.input, operation)
        self.store.commit(self.store.snapshot(), {'plan.json': {
            'data': state._encode({'unrelated': True}), 'scope': 'branch:main',
            'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        latest = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.save,)):
            recovered = self.save(self.input, operation_id=operation)[0]
        self.assertEqual(recovered[PIN_KEY]['root'], saved.reference)
        self.assertEqual(self.store.snapshot().reference, latest)

    def test_retry_operation_id_does_not_acknowledge_different_settings(self):
        operation = uuid.uuid4().hex
        saved = self.lose_ack(self.input, operation)
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaisesRegex(state.StateConflict, 'reused'):
                self.save(self.input, prompt='different', operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, saved.reference)

    def test_newer_branch_save_invalidates_old_retry(self):
        operation = uuid.uuid4().hex
        self.lose_ack(self.input, operation)
        with node_host(self.store, branch_writers=(self.save,)):
            newer = self.f.named_start()
            output = self.save(newer, prompt='Newer intentional edit')[0]
            with self.assertRaises(state.StateConflict):
                self.save(self.input, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, output[PIN_KEY]['root'])

    def test_assignment_change_invalidates_retry_even_when_save_receipt_remains(self):
        operation = uuid.uuid4().hex
        saved = self.lose_ack(self.input, operation)
        address = 'branches/'+self.named+'/checkpoints/clip_0001.json'
        pointer = json.loads(saved.read(address))
        pointer['_authoring_assignment'] = uuid.uuid4().hex
        self.store.commit(saved, {address: {'data': state._encode(pointer), 'scope': 'branch:'+self.named,
            'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        latest = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaisesRegex(state.StateConflict, 'Branch changed'):
                self.save(self.input, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, latest)

    def test_former_owner_cannot_recover_queued_writer_after_takeover(self):
        owner = 'retry-owner-1234567890'
        claimed = self.claim(owner)
        carrier = dict(self.input, _project_ownership={'owner_id': owner, 'epoch': claimed['epoch']})
        operation = uuid.uuid4().hex
        saved = self.lose_ack(carrier, operation)
        self.claim('new-retry-owner-1234567890', True)
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.save(carrier, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, saved.reference)

    def test_retry_ack_failure_keeps_result_recoverable_without_false_output(self):
        operation = uuid.uuid4().hex
        saved = self.lose_ack(self.input, operation)
        with node_host(self.store, branch_writers=(self.save,)), patch.object(
                self.store, '_acknowledge_commit', side_effect=OSError('ack still unavailable')):
            with self.assertRaisesRegex(OSError, 'still unavailable'):
                self.save(self.input, operation_id=operation)
        with node_host(self.store, branch_writers=(self.save,)):
            recovered = self.save(self.input, operation_id=operation)[0]
        self.assertEqual(recovered[PIN_KEY]['root'], saved.reference)

    def test_granted_writer_can_delegate_and_keep_exact_child_receipt(self):
        @scoped_node
        def parent(state):
            # Reader helper sees the same input snapshot, not ambient latest.
            self.assertEqual(self.f.read(state)[0]['saved'], state['plan']['saved'])
            return self.save(state)
        with node_host(self.store, branch_writers=(parent, self.save)):
            output = parent(self.input)[0]
        self.assertNotEqual(output[PIN_KEY], self.input[PIN_KEY])
        with node_host(self.store):
            self.assertEqual(self.f.read(output)[0]['saved'], output['saved'])

    def test_nested_helper_does_not_inherit_callers_write_grant(self):
        @scoped_node
        def parent(state):
            return self.save(state)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(parent,)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                parent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_grandchild_writer_cannot_borrow_grandparents_grant(self):
        @scoped_node
        def reader_middle(state):
            return self.save(state)
        @scoped_node
        def grandparent(state):
            return reader_middle(state)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(grandparent, self.save)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                grandparent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_utility_without_run_input_cannot_borrow_active_writer_access(self):
        @scoped_node
        def utility():
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load(self.named)
            branches.save(self.named, old['authoring'], old['revision'])
        @scoped_node
        def parent(state):
            return utility()
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(parent,)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                parent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_node_cannot_save_another_branchs_authoring(self):
        @scoped_node
        def wrong(state):
            branches = WorkingBranches(self.output, 'demo')
            old = branches.load('main')
            branches.save('main', old['authoring'], old['revision'])
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(wrong,)):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                wrong(self.input)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_nested_grant_cannot_elevate_a_read_only_parent(self):
        owner = 'read-only-caller-owner-12345'
        claimed = self.claim(owner)
        carrier = dict(self.input, _project_ownership={'owner_id': owner, 'epoch': claimed['epoch']})
        @scoped_node
        def reader_parent(state):
            return self.save(state)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(self.save,)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                reader_parent(carrier)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_nested_writer_cannot_substitute_ownership_proof(self):
        owner = 'nested-writer-owner-12345'
        claimed = self.claim(owner)
        @scoped_node
        def parent(state):
            carrier = dict(state, _project_ownership={'owner_id': owner, 'epoch': claimed['epoch']})
            return self.save(carrier)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(parent, self.save)):
            with self.assertRaisesRegex(ValueError, 'ownership proof'):
                parent(self.input)
        self.assertEqual(self.store.snapshot().reference, before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
