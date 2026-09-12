"""Actual handoff state machine on combined storage; no ComfyUI queue writes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import unittest
from unittest.mock import patch
import uuid

import _storage_branch_controls_unit_test as fixture
import storage_project as project
import storage_state as state
from storage_handoff_controls import HandoffControlDocuments
from handoff_state import HandoffStore, HandoffClaimError, HandoffExistsError


class HandoffControlTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.BranchControlTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.store.project
        marker = state._decode((self.root/'storage.json').read_bytes())
        marker.update(format=project.FORMAT, mode=project.ProjectStore.MODE)
        state.atomic_json(self.root/'storage.json', marker)
        self.store = project.ProjectStore(self.root)
        self.handoffs = self.manager()
        self.legacy = HandoffStore(str(self.f.f.output), now=lambda: '2026-09-11T00:00:00+00:00')

    def manager(self, **kwargs):
        return HandoffStore(str(self.root.parent.parent), now=lambda: '2026-09-11T00:00:00+00:00',
                            rehearsal_controls=HandoffControlDocuments(self.store, **kwargs))

    def create(self, store=None, **kwargs):
        return (store or self.handoffs).create('demo', action='next_scene', scene=5,
            start_clip=5, end_clip=7, seed=18446744073709551613, handoff_id='test_next',
            working_branch_id=self.f.named, **kwargs)

    @staticmethod
    def without_pin(record):
        result = copy.deepcopy(record)
        result.pop('storage_dependency', None)
        return result

    def mutate_source(self, branch=None):
        branch = branch or self.f.named
        address = 'branches/main.json' if branch == 'main' else 'branches/'+branch+'/branch.json'
        base = self.store.snapshot()
        descriptor = base.state['documents'][address]
        value = state._decode(base.read(address))
        value['name'] += ' edited'
        self.store.commit(base, {address: {'data': state._encode(value),
            **{key: descriptor[key] for key in ('scope', 'category', 'immutable')}}},
            operation_id=uuid.uuid4().hex)

    def test_actual_state_machine_matches_legacy_with_exact_seed(self):
        old = self.create(self.legacy)
        new = self.create()
        self.assertEqual(self.without_pin(new), old)
        for method, args, kwargs in (
            ('claim', ('demo', 'test_next'), {'claimant': 'worker', 'source_prompt_id': 'prompt-A'}),
            ('release', ('demo', 'test_next'), {'reason': 'queue unavailable'}),
            ('claim', ('demo', 'test_next'), {'claimant': 'worker'}),
            ('transition', ('demo', 'test_next', 'queued'), {'accepted_prompt_id': 'prompt-B'}),
            ('transition', ('demo', 'test_next', 'consumed'), {}),
        ):
            self.assertEqual(self.without_pin(getattr(self.handoffs, method)(*args, **kwargs)),
                             getattr(self.legacy, method)(*args, **kwargs))
        self.assertEqual(self.handoffs.load('demo', 'test_next')['seed'], 18446744073709551613)
        self.assertFalse((self.root/'orchestration').exists())

    def test_create_is_atomic_and_read_only_operations_do_not_publish(self):
        base = self.store.snapshot()
        observed = []
        def observe(_):
            observed.append(self.handoffs.list('demo') == [] and self.store.snapshot().reference == base.reference)
        self.create(self.manager(after_stage=observe))
        self.assertTrue(observed and all(observed))
        latest = self.store.snapshot().reference
        self.assertEqual(len(self.handoffs.list('demo')), 1)
        self.handoffs.load('demo', 'test_next')
        self.assertEqual(self.store.snapshot().reference, latest)
        self.assertEqual(base.state['generation']+1, self.store.snapshot().state['generation'])

    def test_duplicate_create_and_claim_do_not_republish(self):
        self.create()
        with self.assertRaises(HandoffExistsError):
            self.create()
        self.handoffs.claim('demo', 'test_next')
        before = self.store.snapshot().reference
        with self.assertRaises(HandoffClaimError):
            self.handoffs.claim('demo', 'test_next')
        self.assertEqual(before, self.store.snapshot().reference)

    def test_stale_branch_pin_blocks_claim_but_allows_cancellation(self):
        self.create()
        self.mutate_source()
        with self.assertRaisesRegex(state.StateConflict, 'source branch'):
            self.handoffs.claim('demo', 'test_next')
        self.assertEqual(self.handoffs.load('demo', 'test_next')['status'], 'pending')
        self.assertEqual(self.handoffs.transition('demo', 'test_next', 'cancelled')['status'], 'cancelled')

    def test_other_branch_change_does_not_block_named_handoff(self):
        self.create()
        self.mutate_source('main')
        self.assertEqual(self.handoffs.claim('demo', 'test_next')['status'], 'claimed')

    def test_maintenance_epoch_blocks_old_pending_handoff(self):
        self.create()
        self.store.advance_epoch(self.store.snapshot(), operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(state.StateConflict, 'epoch'):
            self.handoffs.claim('demo', 'test_next')

    def test_explicit_queued_base_is_fenced_after_branch_change(self):
        queued = self.manager(base=self.store.snapshot())
        self.mutate_source()
        with self.assertRaisesRegex(state.StateConflict, 'scope changed'):
            self.create(queued)
        self.assertEqual(self.handoffs.list('demo'), [])

    def test_unpinned_imported_handoff_requires_manual_resume(self):
        record = self.create(self.legacy)
        path = self.root/'orchestration/test_next.json'
        with self.handoffs.controls.operation():
            self.handoffs.controls.write(path, record)
        self.assertEqual(self.handoffs.load('demo', 'test_next'), record)
        with self.assertRaisesRegex(state.StateConflict, 'manual resume'):
            self.handoffs.claim('demo', 'test_next')
        self.handoffs.transition('demo', 'test_next', 'cancelled')

    def test_exhausted_pending_budget_is_saved_failed_before_error(self):
        record = self.create(max_attempts=1)
        record['attempt'] = 1
        with self.handoffs.controls.operation():
            self.handoffs.controls.write(self.root/'orchestration/test_next.json', record)
        with self.assertRaisesRegex(HandoffClaimError, 'budget'):
            self.handoffs.claim('demo', 'test_next')
        self.assertEqual(self.handoffs.load('demo', 'test_next')['status'], 'failed')

    def test_interruption_before_claim_publication_keeps_pending(self):
        self.create()
        before = self.store.snapshot().reference
        def interrupt(_):
            raise OSError('simulated claim interruption')
        with self.assertRaises(OSError):
            self.manager(after_stage=interrupt).claim('demo', 'test_next')
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.handoffs.load('demo', 'test_next')['status'], 'pending')

    def test_lost_claim_ack_is_not_claimed_again(self):
        self.create()
        real = state.atomic_json
        def fail(path, value):
            real(path, value)
            raise OSError('lost acknowledgement')
        with patch.object(state, 'atomic_json', fail), self.assertRaises(OSError):
            self.handoffs.claim('demo', 'test_next')
        self.assertEqual(self.handoffs.load('demo', 'test_next')['status'], 'claimed')
        with self.assertRaises(HandoffClaimError):
            self.handoffs.claim('demo', 'test_next')
        self.handoffs.transition('demo', 'test_next', 'uncertain')

    def test_concurrent_claims_have_one_committed_winner(self):
        self.create()
        barrier = threading.Barrier(2)
        def worker(number):
            with state.control_rehearsal_access(self.root):
                manager = self.manager()
                original = manager.controls.claim_dependencies
                def check(record):
                    original(record)
                    barrier.wait(timeout=10)
                manager.controls.claim_dependencies = check
                try:
                    return manager.claim('demo', 'test_next', claimant=str(number))['claimant']
                except state.StateConflict:
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(results.count('conflict'), 1)
        self.assertIn(self.handoffs.load('demo', 'test_next')['claimant'], results)
        self.assertEqual(self.handoffs.load('demo', 'test_next')['attempt'], 1)

    def test_wrong_output_and_missing_source_branch_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'different output'):
            HandoffStore(str(self.f.f.output), rehearsal_controls=HandoffControlDocuments(self.store))
        with self.assertRaisesRegex(ValueError, 'saved source branch'):
            self.handoffs.create('demo', action='next_scene', working_branch_id='d'*32)

    def test_corrupt_control_cannot_be_hidden_by_legacy_list_error_tolerance(self):
        self.create()
        base = self.store.snapshot()
        descriptor = base.state['documents']['orchestration/test_next.json']
        (self.root/descriptor['file']['path']).write_bytes(b'{broken')
        with self.assertRaises(ValueError):
            self.handoffs.list('demo')

    def test_unpublished_candidate_is_not_a_valid_handoff_read_pin(self):
        roots = []
        original = self.store._write_root
        def write(root, operation, budget):
            reference = original(root, operation, budget)
            roots.append(reference)
            return reference
        def interrupt(phase):
            if phase == 'root':
                raise OSError('staged but never published')
        with patch.object(self.store, '_write_root', write), self.assertRaises(OSError):
            self.create(self.manager(after_stage=interrupt))
        candidate = state.Snapshot(self.root, roots[0])
        with self.assertRaisesRegex(state.StateConflict, 'committed ancestor'):
            self.manager(base=candidate).list('demo')


if __name__ == '__main__':
    unittest.main()
