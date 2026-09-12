"""Actual CPU Review deferral, restart/retry and pinned batch inventory."""
import asyncio
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_review_execution_integration_test as fixture

chain, module = fixture.chain, fixture.module
runtime, control = module('storage_runtime'), module('storage_state')
deferred = module('storage_deferred_review')


class DeferredReviewTests(unittest.TestCase):
    setUp = fixture.ReviewExecutionTests.setUp
    audio = fixture.ReviewExecutionTests.audio
    save_loop_scene = fixture.ReviewExecutionTests.save_loop_scene
    expand_loop = fixture.ReviewExecutionTests.expand_loop
    host = fixture.ReviewExecutionTests.host
    next_state = fixture.ReviewExecutionTests.next_state
    two_candidates = fixture.ReviewExecutionTests.two_candidates
    execute = fixture.ReviewExecutionTests.execute

    def defer(self, incoming, saved, **options):
        self.events = []
        server = SimpleNamespace(instance=SimpleNamespace(client_id='private-deferred-test',
            send_sync=lambda event, payload, *args: self.events.append((event, copy.deepcopy(payload)))))
        with patch.object(chain, 'PromptServer', server), patch.object(chain, 'web', object()):
            return asyncio.run(chain.MiniMaxH3ChainReview().review(incoming, saved,
                True, False, 0, False, False, 'none', unique_id='deferred',
                pending_review={'version':1, 'defer_completed_batch':True}, **options))

    def records(self, pin=None, branch='main'):
        with runtime.runtime_access(self.store, pin=pin, selected=branch):
            return chain._list_deferred_review_records(self.run)

    def record(self):
        records = self.records(branch=self.plan.get('_branch_id', 'main'))
        self.assertEqual(len(records), 1)
        token = records[0]['token']
        with runtime.runtime_access(self.store, selected=self.plan.get('_branch_id', 'main')):
            return chain._load_deferred_review(self.run, token)[0]

    def test_actual_deferred_gate_publishes_batch_and_stop_together_without_legacy_writes(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        with self.host(uuid.uuid4().hex, export=False):
            result = self.defer(incoming, saved)
        self.assertIsInstance(result['result'][0], chain.ExecutionBlocker)
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        document = self.record()
        self.assertEqual(document['candidates'][0]['segment']['revision'], saved['revision'])
        self.assertEqual(document['public']['seed'], str(saved['seed']))
        self.assertEqual(document['plan']['shots'][0]['prompt'], incoming['plan']['shots'][0]['prompt'])
        self.assertNotIn('_project_ownership', document['plan'])
        self.assertNotIn('_storage_pin', document['plan'])
        self.assertNotIn('owner_id', json.dumps(document))
        key = 'jobs/'+document['token']+'/review.json'
        self.assertEqual(json.loads(after.read(key))['outcome'], 'deferred')
        archived = json.loads(after.read(deferred.address('main', document['token'])))
        self.assertEqual(json.loads(after.read(key))['deferred'], archived)
        self.assertNotIn('/media/', archived['public']['video']['subfolder'])
        self.assertFalse((self.store.project/'pending_reviews').exists())
        self.assertNotIn(document['token'], chain._PENDING_REVIEWS)
        self.assertEqual(self.records(saved['_storage_pin']), [])
        self.assertEqual(before.read('checkpoints/clip_0001.json'), after.read('checkpoints/clip_0001.json'))
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                           for p in self.source.rglob('*') if p.is_file()})

    def test_completed_batch_preserves_all_takes_and_synchronized_accepted_preview(self):
        incoming, first, second, _ = self.two_candidates()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, second, candidate_count=2, review_each_candidate=True, audio=self.audio(1))
        document = self.record()
        self.assertEqual([item['segment']['revision'] for item in document['candidates']],
                         [first['revision'], second['revision']])
        public = document['public']
        self.assertTrue(public['has_audio'])
        self.assertEqual(public['candidate_index'], 2)
        video = self.output/public['video']['subfolder']/public['video']['filename']
        with chain.av.open(str(video)) as media:
            self.assertEqual(len(media.streams.audio), 1)
        for take in (first, second):
            self.store.snapshot().read('checkpoints/clip_0001.'+take['revision']+'.json')
        self.assertFalse(any('retention' in key for key in self.store.snapshot().state['documents']))

    def test_retry_returns_existing_stop_without_new_batch_or_another_ui_gate(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        with self.host(job):
            first = self.defer(incoming, saved)
        before = self.store.snapshot().reference
        with self.host(job):
            repeated = self.defer(incoming, saved)
        self.assertEqual(repeated['result'][1], first['result'][1])
        self.assertFalse(self.events)
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(len(self.records()), 1)

    def test_prepublication_failure_never_exposes_pending_batch_and_resumes_prepared_result(self):
        incoming, saved = self.save_loop_scene()
        job, before = uuid.uuid4().hex, self.store.snapshot().reference
        original = self.store.commit
        def fail(base, changes, **kwargs):
            if any(key.startswith('pending_reviews/') for key in changes):
                raise OSError('pending before publication')
            return original(base, changes, **kwargs)
        with self.host(job), patch.object(self.store, 'commit', fail), self.assertRaisesRegex(OSError, 'before publication'):
            self.defer(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertFalse(self.records())
        self.assertFalse(self.events)
        with self.host(job):
            self.defer(incoming, saved)
        self.assertEqual(len(self.records()), 1)
        self.assertFalse(self.events)

    def test_lost_ack_keeps_exact_batch_and_retry_never_duplicates_it(self):
        incoming, saved = self.save_loop_scene()
        job, original = uuid.uuid4().hex, self.store.commit
        def lose(base, changes, **kwargs):
            result = original(base, changes, **kwargs)
            if any(key.startswith('pending_reviews/') for key in changes):
                raise OSError('pending lost ack')
            return result
        with self.host(job), patch.object(self.store, 'commit', lose), self.assertRaisesRegex(OSError, 'lost ack'):
            self.defer(incoming, saved)
        before, document = self.store.snapshot().reference, self.record()
        with self.host(job):
            self.defer(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(self.record(), document)
        self.assertFalse(self.events)

    def test_grant_and_ownership_are_rechecked_without_legacy_fallback(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot().reference
        with self.host(uuid.uuid4().hex, grant=False), self.assertRaisesRegex(ValueError, 'writer grant'):
            self.defer(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, before)
        ownership = module('project_ownership')
        with runtime.runtime_access(self.store, ownership_writes=True):
            ownership.claim_project_ownership(self.output, self.run, 'deferred-different-owner', force=True)
        with self.host(uuid.uuid4().hex), self.assertRaises(ownership.ProjectOwnershipError):
            self.defer(incoming, saved)
        self.assertFalse(self.records())

    def test_named_branch_inventory_is_isolated_and_original_plan_unchanged(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Deferred fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            self.plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        before = self.store.snapshot().read('plan.json')
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, saved)
        document = self.record()
        self.assertEqual(document['plan']['_branch_id'], branch['id'])
        self.assertEqual(self.records(), [])
        self.assertEqual(self.store.snapshot().read('plan.json'), before)

    def test_corrupt_accepted_batch_is_not_silently_hidden(self):
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, saved)
        token = self.record()['token']
        snapshot = self.store.snapshot()
        path = self.store.project/snapshot.state['documents'][deferred.address('main', token)]['file']['path']
        path.rename(path.with_suffix('.retained'))
        with self.assertRaisesRegex(control.StateConflict, 'bytes are missing'):
            self.records()

    def test_recomputed_prepared_checksum_cannot_change_seed_or_candidate_preview(self):
        incoming, saved = self.save_loop_scene()
        job, original = uuid.uuid4().hex, self.store.commit
        def fail(base, changes, **kwargs):
            if any(key.startswith('pending_reviews/') for key in changes):
                raise OSError('pending before publication')
            return original(base, changes, **kwargs)
        with self.host(job), patch.object(self.store, 'commit', fail), self.assertRaises(OSError):
            self.defer(incoming, saved)
        paths = list((self.store.project/'project/jobs').glob('*/review-result.json'))
        self.assertEqual(len(paths), 1)
        path = paths[0]
        original_bytes = path.read_bytes()
        before = self.store.snapshot().reference
        for change in ('seed', 'prompt', 'preview', 'candidate'):
            envelope = json.loads(original_bytes)
            document = envelope['value']['deferred']
            if change == 'seed':
                document['public']['seed'] = '3'
            elif change == 'prompt':
                document['plan']['shots'][0]['prompt'] = 'changed after preparation'
            elif change == 'preview':
                document['candidates'][0]['video']['filename'] = 'unaccepted.mp4'
                document['public']['video']['filename'] = 'unaccepted.mp4'
                document['public']['candidates'][0]['video']['filename'] = 'unaccepted.mp4'
            else:
                document['candidates'][0]['segment']['revision'] = self.originals[1]['revision']
            envelope['sha256'] = control._hash(control._encode(envelope['value']))
            path.write_bytes(control._encode(envelope))
            with self.subTest(change=change), self.host(job), self.assertRaises((ValueError, control.StateConflict)):
                self.defer(incoming, saved)
            self.assertEqual(self.store.snapshot().reference, before)
            self.assertFalse(self.records())
        path.write_bytes(original_bytes)
        with self.host(job):
            self.defer(incoming, saved)
        self.assertEqual(len(self.records()), 1)

    def test_full_reverse_recovery_keeps_batch_precise_and_previews_playable(self):
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, saved, audio=self.audio(1))
        document = self.record()
        receipt = self.lab/'deferred-copy-receipt.json'
        control.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source),
                                         independent_copies=True))
        recovery = module('storage_recovery')
        destination = self.lab/'deferred-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, destination, self.lab/'deferred-recovery',
                                              rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        from _upscale_chain_unit_test import folder_paths
        with patch.object(folder_paths, 'output_directory', str(destination)):
            restored, _ = chain._load_deferred_review(self.run, document['token'])
            self.assertEqual(restored['plan'], document['plan'])
            self.assertEqual(restored['public']['seed'], document['public']['seed'])
            self.assertEqual(len(chain._list_deferred_review_records(self.run)), 1)
        video = restored['public']['video']
        path = destination/video['subfolder']/video['filename']
        source_video = document['public']['video']
        previous = self.output/source_video['subfolder']/source_video['filename']
        self.assertEqual(path.read_bytes(), previous.read_bytes())
        self.assertNotEqual(path.stat().st_ino, previous.stat().st_ino)
        with chain.av.open(str(path)) as media:
            self.assertEqual(len(media.streams.audio), 1)
            self.assertEqual(len(list(media.decode(video=0))), 5)
        self.assertEqual(result, recovery.recover_legacy_copy(journal))

    def test_actual_prompt_executor_defers_without_running_the_next_scene(self):
        result = fixture.execute_graph(self, defer=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([scene for scene, _ in result['calls']], [1])
        self.assertEqual(len(result['gates']), 1)
        self.assertFalse(result['outputs'])
        self.assertEqual(len(self.records()), 1)

    def test_actual_list_route_reads_accepted_inventory_after_restart(self):
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, saved)
        request = SimpleNamespace(method='GET', query={'run_name':self.run, 'branch_id':'main'}, headers={})
        from aiohttp import web
        with runtime.runtime_access(self.store), patch.object(chain, 'web', web):
            response = asyncio.run(chain._list_deferred_reviews(request))
        self.assertEqual(response.status, 200)
        public = json.loads(response.body)
        self.assertEqual(len(public['reviews']), 1)
        self.assertEqual(public['reviews'][0]['seed'], str(saved['seed']))
        self.assertTrue(public['reviews'][0]['deferred_review'])

    def test_runtime_branch_cannot_be_overridden_by_request_or_used_after_close(self):
        with runtime.runtime_access(self.store) as bound:
            with module('branch_scope').branch_scope(self.run, 'f'*32), self.assertRaisesRegex(ValueError, 'another runtime branch'):
                deferred.listing(bound)
        with self.assertRaisesRegex(ValueError, 'escaped'):
            deferred.listing(bound)

    def test_prepare_route_returns_exact_candidate_lineage_without_promoting_or_pruning(self):
        incoming, first, second, _ = self.two_candidates()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, second, candidate_count=2)
        token = self.record()['token']
        async def body():
            return dict(action='prepare', run_name=self.run, token=token,
                        candidate_revision=first['revision'], branch_id='main')
        request = SimpleNamespace(method='POST', query={}, json=body,
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'],
                     'X-H3-Ownership-Epoch':str(self.proof['epoch'])})
        from aiohttp import web
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store) as bound, patch.object(chain, 'web', web):
            response = asyncio.run(chain._submit_deferred_review(request))
            self.assertEqual(response.status, 200, response.body)
            value = json.loads(response.body)
            self.assertEqual(value['storage_pin'], bound.pin)
        self.assertEqual(value['seed'], str(first['seed']))
        self.assertEqual(value['scene_prompt'], first.get('scene_prompt_template', first.get('scene_prompt')))
        self.assertEqual(value['resume_revisions'], [dict(scene=1, revision=first['revision'])])
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(json.loads(self.store.snapshot().read('checkpoints/clip_0001.json'))['segment']['revision'], second['revision'])


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)
