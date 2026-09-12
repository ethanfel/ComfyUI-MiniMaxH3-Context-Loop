"""Atomic checkpoint assignment retirement; no filesystem deletion."""
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_runtime_unit_test as fixture
from storage_branch_controls import BranchControlDocuments
from storage_runtime import runtime_access, current_runtime
from storage_carriers import node_host, PIN_KEY
from branch_scope import scoped_node
import project_ownership as ownership
from checkpoint_manager import CheckpointGraphManager
import storage_state as state


class PointerRetirementTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.named = self.f.store, self.f.named
        self.pointer = 'branches/'+self.named+'/checkpoints/clip_0002.json'
        self.first = 'branches/'+self.named+'/checkpoints/clip_0001.json'

    def commit(self, base, changes=None, **kwargs):
        return self.store.commit(base, changes or {},
            operation_id=kwargs.pop('operation_id', uuid.uuid4().hex), **kwargs)

    def change(self, address, value):
        scope, category, immutable = BranchControlDocuments._contract(address)
        return {address: dict(data=state._encode(value), scope=scope, category=category, immutable=immutable)}

    def test_retirement_keeps_all_prior_bytes_and_other_branch_assignments(self):
        before = self.store.snapshot()
        descriptors = before.state['documents']
        raw = before.read(self.pointer)
        receipt = self.commit(before, retire_pointers=[self.pointer])
        accepted = self.store.snapshot()
        self.assertEqual(receipt['generation'], before.state['generation']+1)
        self.assertNotIn(self.pointer, accepted.state['documents'])
        self.assertEqual(before.read(self.pointer), raw)
        self.assertTrue((self.store.project/descriptors[self.pointer]['file']['path']).is_file())
        self.assertEqual(accepted.read('checkpoints/clip_0002.json'), before.read('checkpoints/clip_0002.json'))
        self.assertEqual(set(accepted.state['documents']), set(descriptors)-{self.pointer})
        self.assertEqual(self.store.committed_snapshot(receipt).reference, accepted.reference)

    def test_replace_and_retire_are_one_publication_for_graph_readers(self):
        before = self.store.snapshot()
        metadata = json.loads(before.read(self.first))
        metadata['_authoring_assignment'] = uuid.uuid4().hex
        observations = []
        def observe(_):
            current = self.store.snapshot()
            observations.append((current.read(self.first), self.pointer in current.state['documents']))
        self.commit(before, self.change(self.first, metadata), retire_pointers=[self.pointer], after_stage=observe)
        self.assertTrue(observations)
        self.assertTrue(all(raw == before.read(self.first) and exists for raw, exists in observations))
        with runtime_access(self.store, selected=self.named):
            active, stale = CheckpointGraphManager(self.f.output).active_selection('demo')
        self.assertNotIn(2, active)
        self.assertEqual(json.loads(self.store.snapshot().read(self.first)), metadata)

    def test_empty_retirement_does_not_allow_empty_commit(self):
        with self.assertRaisesRegex(ValueError, 'nonempty changes'):
            self.commit(self.store.snapshot())

    def test_arbitrary_or_immutable_controls_cannot_be_retired(self):
        base = self.store.snapshot()
        for address in ('branches/main.json', 'plan.json', '__storage__/payloads/x.json',
                        'orchestration/job.json', 'checkpoints/clip_0002.'+'a'*32+'.json',
                        'checkpoints/clip_0000.json', '../checkpoints/clip_0002.json'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.commit(base, retire_pointers=[address])
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_duplicates_simultaneous_replacement_and_invalid_lists_reject(self):
        base = self.store.snapshot()
        for addresses in ([self.pointer, self.pointer], self.pointer, {self.pointer}, [2]):
            with self.subTest(addresses=addresses), self.assertRaises((ValueError, TypeError)):
                self.commit(base, retire_pointers=addresses)
        with self.assertRaisesRegex(ValueError, 'simultaneous replacement'):
            self.commit(base, self.change(self.pointer, {'revision': 'x'}), retire_pointers=[self.pointer])
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_retirement_and_replacement_cannot_hide_concurrent_branch_change(self):
        base = self.store.snapshot()
        self.commit(base, self.change(self.first, {'segment': {'index': 1, 'revision': 'e'*32}}))
        accepted = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict, 'scope changed'):
            self.commit(base, retire_pointers=[self.pointer])
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_unrelated_branch_commit_can_be_preserved(self):
        base = self.store.snapshot()
        self.commit(base, self.change('checkpoints/clip_0001.json', {'other': True}))
        self.commit(base, retire_pointers=[self.pointer])
        self.assertEqual(json.loads(self.store.snapshot().read('checkpoints/clip_0001.json')), {'other': True})

    def test_lost_ack_retry_reuses_exact_result(self):
        base, operation = self.store.snapshot(), uuid.uuid4().hex
        real = self.store._publish
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('test lost retirement response')
        with patch.object(self.store, '_publish', lost), self.assertRaises(OSError):
            self.commit(base, retire_pointers=[self.pointer], operation_id=operation)
        accepted = self.store.snapshot().reference
        receipt = self.commit(base, retire_pointers=[self.pointer], operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, accepted)
        self.assertEqual(self.store.committed_snapshot(receipt).reference, accepted)
        with self.assertRaises(state.StateConflict):
            self.commit(base, retire_pointers=[self.first], operation_id=operation)

    def test_prepublication_failure_leaves_old_assignment_and_can_resume(self):
        for phase in ('document', 'retired_pointer', 'root'):
            base, operation = self.store.snapshot(), uuid.uuid4().hex
            changed = self.change(self.first, {'phase': phase})
            def fail(point):
                if point == phase:
                    raise OSError('test prepublication interruption')
            with self.assertRaises(OSError):
                self.commit(base, changed, retire_pointers=[self.pointer], operation_id=operation, after_stage=fail)
            self.assertEqual(self.store.snapshot().reference, base.reference)
            self.commit(base, changed, retire_pointers=[self.pointer], operation_id=operation)
            self.assertNotIn(self.pointer, self.store.snapshot().state['documents'])
            # Reassign explicitly for the next independent interruption test.
            original = json.loads(base.read(self.pointer))
            self.commit(self.store.snapshot(), self.change(self.pointer, original))

    def test_missing_pointer_or_wrong_import_contract_rejects(self):
        base = self.store.snapshot()
        with self.assertRaisesRegex(state.StateConflict, 'absent'):
            self.commit(base, retire_pointers=['checkpoints/clip_9999.json'])
        # An imported immutable document at a canonical-looking path must not
        # be mistaken for a removable assignment, even with valid storage bytes.
        wrong = 'checkpoints/clip_0004.json'
        self.commit(base, {wrong: dict(data=b'{}', scope='branch:main', category='branches', immutable=True)})
        before = self.store.snapshot()
        with self.assertRaisesRegex(state.StateConflict, 'ownership contract'):
            self.commit(before, retire_pointers=[wrong])
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_branch_port_stages_retirement_but_other_readers_keep_input_root(self):
        before = self.store.snapshot()
        path = self.store.project/self.pointer
        with runtime_access(self.store, selected=self.named, branch_writes=True) as runtime:
            with runtime.branches.operation():
                self.assertTrue(runtime.branches.exists(path))
                runtime.branches.retire_pointer(path)
                self.assertFalse(runtime.branches.exists(path))
                self.assertNotIn(path, runtime.branches.matching(path.parent, 'clip_*.json'))
                with self.assertRaises(FileNotFoundError):
                    runtime.branches.read(path)
                self.assertEqual(runtime.reader.read(path), json.loads(before.read(self.pointer)))
            self.assertNotEqual(runtime.output_pin['root'], runtime.pin['root'])
            self.assertEqual(runtime.reader.read(path), json.loads(before.read(self.pointer)))
        self.assertNotIn(self.pointer, self.store.snapshot().state['documents'])

    def node_writer(self, address):
        @scoped_node
        def retire(state):
            runtime = current_runtime(self.f.output, 'demo')
            with runtime.branches.operation():
                runtime.branches.retire_pointer(self.store.project/address)
            return (dict(state, retired=True),)
        return retire

    def test_node_retirement_needs_own_grant_and_cannot_retire_another_branch(self):
        incoming = {'run_name': 'demo', '_branch_id': self.named}
        retire = self.node_writer(self.pointer)
        before = self.store.snapshot().reference
        with node_host(self.store), self.assertRaisesRegex(ValueError, 'read-only'):
            retire(incoming)
        wrong = self.node_writer('checkpoints/clip_0002.json')
        with node_host(self.store, branch_writers=(wrong,)), self.assertRaisesRegex(ValueError, 'different runtime branch'):
            wrong(incoming)
        self.assertEqual(self.store.snapshot().reference, before)
        with node_host(self.store, branch_writers=(retire,)):
            result = retire(incoming)[0]
        self.assertEqual(result[PIN_KEY]['root'], self.store.snapshot().reference)
        self.assertNotIn(self.pointer, self.store.snapshot().state['documents'])

    def test_retirement_node_still_needs_current_workflow_owner(self):
        with runtime_access(self.store, ownership_writes=True):
            owned = ownership.claim_project_ownership(self.f.output, 'demo', 'retirement-owner-1234567890')
        incoming = {'run_name': 'demo', '_branch_id': self.named}
        retire = self.node_writer(self.pointer)
        before = self.store.snapshot().reference
        with node_host(self.store, branch_writers=(retire,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                retire(incoming)
            self.assertEqual(self.store.snapshot().reference, before)
            result = retire(dict(incoming, _project_ownership={
                'owner_id': 'retirement-owner-1234567890', 'epoch': owned['epoch']}))[0]
        self.assertEqual(result[PIN_KEY]['root'], self.store.snapshot().reference)


if __name__ == '__main__':
    unittest.main()
