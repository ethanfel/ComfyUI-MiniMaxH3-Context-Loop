"""Real CPU Review selection, post-acceptance typed quarantine and Loop resume."""
import copy
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_quarantine_integration_test as fixture

generation, chain, module = fixture.generation, fixture.chain, fixture.module
review_fixture = fixture.fixture
review = review_fixture.review
cleanup = module('storage_review_cleanup')
retention = module('storage_retention')
runtime = module('storage_runtime')


class ReviewCleanupTests(unittest.TestCase):
    setUp = fixture.QuarantineIntegrationTests.setUp
    audio = fixture.QuarantineIntegrationTests.audio
    save_loop_scene = fixture.QuarantineIntegrationTests.save_loop_scene
    expand_loop = fixture.QuarantineIntegrationTests.expand_loop
    host = fixture.QuarantineIntegrationTests.host
    execute = fixture.QuarantineIntegrationTests.execute
    next_state = fixture.QuarantineIntegrationTests.next_state
    two_candidates = fixture.QuarantineIntegrationTests.two_candidates
    graph = fixture.QuarantineIntegrationTests.graph
    assert_source_unchanged = fixture.QuarantineIntegrationTests.assert_source_unchanged

    def candidates(self, *, latest=False, stop=False):
        incoming, first, second, decision = self.two_candidates()
        decision['kept_candidate_revisions'] = [second['revision'] if latest else first['revision']]
        decision['candidate_revision'] = decision['kept_candidate_revisions'][0]
        if stop:
            decision['action'] = 'stop'
        return incoming, first, second, decision, uuid.uuid4().hex

    def rows(self):
        return {row['revision']:row for row in self.graph()['revisions']}

    def test_selection_is_accepted_before_quarantine_and_exact_context_continues(self):
        incoming, first, second, decision, job = self.candidates()
        original = retention.RuntimeRetention.delete_generation
        calls = []
        def checked(service, scene, revision, snapshot, **kwargs):
            before = self.store.snapshot()
            self.assertEqual(json.loads(before.read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
            self.assertTrue(any(key.endswith('/review.json') for key in before.state['documents']))
            calls.append(revision)
            return original(service, scene, revision, snapshot, **kwargs)
        with self.host(job, select=True, retention=True), patch.object(retention.RuntimeRetention, 'delete_generation', checked):
            result = self.execute(incoming, second, decision, candidate_count=2)
            accepted = result['result'][0]
            advanced = self.next_state(incoming, accepted)
        self.assertEqual(calls, [second['revision']])
        self.assertNotIn(second['revision'], self.rows())
        self.assertTrue(self.rows()[first['revision']]['ready'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], first['seed'])
        self.assertEqual(advanced['plan']['shots'][0]['prompt'], first['prompt'])
        self.assertTrue(review_fixture.torch.equal(advanced['previous_latent']['samples'][0], review_fixture.av_latent(.4)['samples'][0]))
        self.assertEqual(accepted['_storage_pin']['root'], self.store.snapshot().reference)
        self.assertIn('quarantined 1 candidate', result['result'][1])
        self.assertIn('no disk space reclaimed', result['result'][1])
        self.assert_source_unchanged()

    def test_keep_latest_retires_earlier_candidate_without_fabricating_selection(self):
        incoming, first, second, decision, job = self.candidates(latest=True)
        with self.host(job, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            result = self.expand_loop(incoming, accepted, sampled_latent=review_fixture.av_latent(.8),
                                      images=review_fixture.torch.full_like(self.frames, .7))
            advanced = next(value['inputs']['initial_state'] for value in result['expand'].values()
                            if value['class_type'] == 'MiniMaxH3ChainLoopStart')
        self.assertNotIn(first['revision'], self.rows())
        self.assertNotIn('_h3_review_decision', accepted)
        self.assertEqual(advanced['segments'][0]['revision'], second['revision'])
        self.assertTrue(review_fixture.torch.equal(advanced['previous_latent']['samples'][0], review_fixture.av_latent(.8)['samples'][0]))

    def test_missing_retention_grant_does_not_publish_selection_or_delete(self):
        incoming, first, second, decision, job = self.candidates()
        before = self.store.snapshot().read('checkpoints/clip_0001.json')
        with self.host(job, select=True), self.assertRaisesRegex(ValueError, 'retention writes'):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'), before)
        self.assertTrue({first['revision'], second['revision']} <= self.rows().keys())

    def test_failed_selection_publication_never_enters_cleanup(self):
        incoming, first, second, decision, job = self.candidates()
        original = self.store.commit
        def fail(base, changes, **kwargs):
            if any(key.endswith('/review.json') for key in changes):
                raise OSError('selection not published')
            return original(base, changes, **kwargs)
        with self.host(job, select=True, retention=True), patch.object(self.store, 'commit', fail), \
                patch.object(retention.RuntimeRetention, 'delete_generation') as deletion, \
                self.assertRaisesRegex(OSError, 'selection not published'):
            self.execute(incoming, second, decision, candidate_count=2)
        deletion.assert_not_called()
        self.assertTrue({first['revision'], second['revision']} <= self.rows().keys())

    def test_lost_quarantine_ack_retries_exact_request_without_another_gate(self):
        incoming, first, second, decision, job = self.candidates()
        original = retention.RuntimeRetention.delete_generation
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('lost candidate quarantine ack')
        with self.host(job, select=True, retention=True), patch.object(retention.RuntimeRetention, 'delete_generation', lose), \
                self.assertRaisesRegex(OSError, 'lost candidate'):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertNotIn(second['revision'], self.rows())
        count = len(self.store.snapshot().state['operations'])
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            advanced = self.next_state(incoming, accepted)
        self.assertFalse(self.events)
        self.assertEqual(len(self.store.snapshot().state['operations']), count+1)  # cleanup witness only
        self.assertEqual(advanced['segments'][0]['revision'], first['revision'])

    def test_cleanup_commit_lost_ack_and_later_undo_do_not_requarantine_on_retry(self):
        incoming, first, second, decision, job = self.candidates()
        original = self.store.commit
        def lose(base, changes, **kwargs):
            result = original(base, changes, **kwargs)
            if any(key.endswith('/review-cleanup.json') for key in changes):
                raise OSError('lost cleanup completion ack')
            return result
        with self.host(job, select=True, retention=True), patch.object(self.store, 'commit', lose), \
                self.assertRaisesRegex(OSError, 'lost cleanup'):
            self.execute(incoming, second, decision, candidate_count=2)
        before = self.store.snapshot().reference
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
        self.assertFalse(self.events)
        self.assertEqual(before, self.store.snapshot().reference)
        _, record = cleanup.verify_cleanup(self.store, accepted[cleanup.KEY], accepted[review.KEY]['receipt'])
        operation = record['steps'][0]['receipt']['operation_id']
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            preview = bound.retention.preview_undo(operation)
            bound.retention.undo(operation, preview['snapshot'], proof=incoming['plan']['_project_ownership'])
        restored = self.store.snapshot().reference
        with self.host(job, select=True, retention=True):
            repeated = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            self.next_state(incoming, repeated)
        self.assertFalse(self.events)
        self.assertEqual(restored, self.store.snapshot().reference)
        self.assertIn(second['revision'], self.rows())

    def test_missing_forged_or_different_cleanup_proof_cannot_advance_pin(self):
        incoming, first, second, decision, job = self.candidates()
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            for change in ('missing', 'receipt', 'witness', 'seed', 'old_pin'):
                bad = copy.deepcopy(accepted)
                if change == 'missing':
                    bad.pop(cleanup.KEY)
                elif change == 'receipt':
                    bad[cleanup.KEY]['receipt'] = bad[review.KEY]['receipt']
                elif change == 'witness':
                    bad[cleanup.KEY]['witness'] = bad[review.KEY]['witness']
                elif change == 'seed':
                    bad['_h3_review_decision']['plan']['shots'][0]['seed'] = 3
                else:
                    bad['_storage_pin'] = second['_storage_pin']
                with self.subTest(change=change), self.assertRaises((ValueError, generation.state.StateConflict)):
                    self.next_state(incoming, bad)

    def test_stop_publishes_choice_then_quarantines_without_continuing(self):
        incoming, first, second, decision, job = self.candidates(stop=True)
        with self.host(job, select=True, retention=True):
            result = self.execute(incoming, second, decision, candidate_count=2)
        self.assertIsInstance(result['result'][0], chain.ExecutionBlocker)
        self.assertNotIn(second['revision'], self.rows())
        self.assertIn('quarantined 1 candidate', result['result'][1])

    def test_actual_prompt_executor_prunes_rejected_takes_then_generates_next_scene(self):
        result = review_fixture.execute_graph(self, select=True, prune=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([index for index,seed in result['calls']], [1,1,2,2])
        self.assertEqual(len(result['gates']), 2)
        segments = result['outputs'][0]['segments']
        self.assertEqual([segment['seed'] for segment in segments], [18446744073709551601, 8])
        self.assertTrue(all(segment['revision'] in self.rows() for segment in segments))
        records = [key for key in self.store.snapshot().state['documents'] if key.endswith('/review-cleanup.json')]
        self.assertEqual(len(records), 2)
        self.store.verify_payloads()

    def test_actual_uncached_executor_retries_nodes_without_duplicate_retirement(self):
        result = review_fixture.execute_graph(self, select=True, prune=True, cache='none')
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual(len(result['gates']), 2)
        self.assertEqual(len(result['outputs'][0]['segments']), 2)
        records = [key for key in self.store.snapshot().state['documents'] if key.endswith('/review-cleanup.json')]
        self.assertEqual(len(records), 2)
        self.store.verify_payloads()

    def test_named_branch_cleanup_does_not_change_original_assignments_or_archives(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Cleanup fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            self.plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        before = self.store.snapshot()
        incoming, first, second, decision, job = self.candidates()
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            advanced = self.next_state(incoming, accepted)
        after = self.store.snapshot()
        for address in ('plan.json', 'checkpoints/clip_0001.json', 'checkpoints/clip_0002.json'):
            self.assertEqual(before.read(address), after.read(address))
        self.assertNotIn(second['revision'], self.rows())
        self.assertEqual(advanced['plan']['_branch_id'], branch['id'])

    def test_prepublication_quarantine_failure_preserves_files_and_resumes_after_selection(self):
        incoming, first, second, decision, job = self.candidates()
        kernel = retention.ProjectQuarantine.quarantine
        def fail(service, preview, **kwargs):
            def stop(phase):
                if phase == 'root':
                    raise OSError('candidate quarantine before publication')
            return kernel(service, preview, **dict(kwargs, after_stage=stop))
        with self.host(job, select=True, retention=True), patch.object(retention.ProjectQuarantine, 'quarantine', fail), \
                self.assertRaisesRegex(OSError, 'before publication'):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertTrue({first['revision'], second['revision']} <= self.rows().keys())
        self.assertEqual(json.loads(self.store.snapshot().read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            self.next_state(incoming, accepted)
        self.assertFalse(self.events)
        self.assertNotIn(second['revision'], self.rows())

    def test_new_reference_after_acceptance_cannot_be_adopted_into_cleanup_preview(self):
        incoming, first, second, decision, job = self.candidates()
        original = cleanup.ReviewCleanup.run
        def change(service):
            address = 'project_notes/'+uuid.uuid4().hex+'.json'
            self.store.commit(self.store.snapshot(), {address:dict(data=b'{}', scope='archive:separate',
                category='legacy', immutable=True)}, operation_id=uuid.uuid4().hex)
            return original(service)
        with self.host(job, select=True, retention=True), patch.object(cleanup.ReviewCleanup, 'run', change), \
                self.assertRaisesRegex(generation.state.StateConflict, 'before Review cleanup'):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertTrue({first['revision'], second['revision']} <= self.rows().keys())
        self.assertEqual(json.loads(self.store.snapshot().read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])

    def test_sealed_candidate_is_kept_with_explanation_and_not_retried_after_dependency_changes(self):
        incoming, first, second, decision, job = self.candidates()
        address = 'chapters/01_first/manifests/'+uuid.uuid4().hex+'.json'
        self.store.commit(self.store.snapshot(), {address:dict(data=generation.state._encode(
            dict(format='h3_chain_chapter_manifest_v1', run_name=self.run, chapter=dict(number=1),
                 segments=[dict(index=1, revision=second['revision'])])), scope='archive:sealed',
                 category='legacy', immutable=True)}, operation_id=uuid.uuid4().hex)
        with self.host(job, select=True, retention=True):
            result = self.execute(incoming, second, decision, candidate_count=2)
            self.next_state(incoming, result['result'][0])
        self.assertIn(second['revision'], self.rows())
        self.assertIn('quarantined 0 candidates', result['result'][1])
        self.assertIn('Sealed chapter', result['result'][1])
        before = self.store.snapshot().reference
        with self.host(job, select=True, retention=True):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertFalse(self.events)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_reverse_recovery_keeps_cleanup_retired_and_exact_selected_context(self):
        incoming, first, second, decision, job = self.candidates()
        with self.host(job, select=True, retention=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
        before = self.store.snapshot().reference
        copy_receipt = self.lab/'review-cleanup-copy.json'
        generation.state.atomic_json(copy_receipt, dict(copy=str(self.store.project), source=str(self.source),
                                                       independent_copies=True))
        recovery = module('storage_recovery')
        output = self.lab/'cleanup-recovered-output'
        journal = recovery.prepare_legacy_copy(copy_receipt, output, self.lab/'cleanup-recovery', rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/self.run
        self.assertFalse((root/('checkpoints/clip_0001.'+second['revision']+'.json')).exists())
        plan = dict(accepted['_h3_review_decision']['plan'])
        plan.pop('_storage_pin')
        generation.folder_paths.output_directory = str(output)
        resumed = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        self.assertEqual(resumed['segments'][0]['revision'], first['revision'])
        self.assertEqual(resumed['plan']['shots'][0]['seed'], first['seed'])
        self.assertEqual(resumed['plan']['shots'][0]['prompt'], first['prompt'])
        self.assertTrue(review_fixture.torch.equal(resumed['previous_latent']['samples'][0], review_fixture.av_latent(.4)['samples'][0]))
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))
        self.assert_source_unchanged()


if __name__ == '__main__':
    unittest.main(argv=[__file__])
