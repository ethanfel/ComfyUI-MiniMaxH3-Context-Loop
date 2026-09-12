"""Real stopped Review and partial video publication on independent CPU copies."""
import json
import math
import unittest
from unittest.mock import patch
import uuid

import _storage_review_execution_integration_test as fixture

chain, module = fixture.chain, fixture.module
runtime = module('storage_runtime')
assembly = module('storage_assembly')


class PartialReviewTests(unittest.TestCase):
    setUp = fixture.ReviewExecutionTests.setUp
    audio = fixture.ReviewExecutionTests.audio
    save_loop_scene = fixture.ReviewExecutionTests.save_loop_scene
    expand_loop = fixture.ReviewExecutionTests.expand_loop
    host = fixture.ReviewExecutionTests.host
    execute = fixture.ReviewExecutionTests.execute
    next_state = fixture.ReviewExecutionTests.next_state
    two_candidates = fixture.ReviewExecutionTests.two_candidates

    def stop(self, incoming, saved, job, *, decision=None, export=True, select=False, retention=False, **kwargs):
        with self.host(job, export=export, select=select, retention=retention):
            return self.execute(incoming, saved, decision or dict(action='stop'),
                                assemble_partial_on_stop=True, **kwargs)

    def assert_resolved_retry(self, result, token):
        self.assertEqual(len(self.events), 1, self.events)
        event, payload = self.events[0]
        self.assertEqual(event, 'minimax_h3_context_loop_review_resolved')
        self.assertEqual(payload['token'], token)
        self.assertEqual(payload['node_id'], 'review')
        self.assertEqual(payload['action'], 'stop')
        self.assertEqual(payload['status'], result['result'][1])
        self.assertEqual(payload['partial_video'], result['ui']['images'][0])

    def test_real_stop_publishes_partial_movie_manifest_and_receipt_together(self):
        incoming, saved = self.save_loop_scene()
        result = self.stop(incoming, saved, uuid.uuid4().hex, partial_audio_source='checkpointed')
        self.assertIsInstance(result['result'][0], chain.ExecutionBlocker)
        item = result['ui']['images'][0]
        video = self.output/item['subfolder']/item['filename']
        self.assertIn('/exports/video/', str(video))
        with chain.av.open(str(video)) as media:
            self.assertEqual(len(list(media.decode(video=0))), 5)
            self.assertEqual(len(media.streams.audio), 1)
        after = self.store.snapshot()
        manifest = json.loads(after.read('partial/through_clip_0001.manifest.json'))
        self.assertEqual(manifest['segments'][0]['seed'], saved['seed'])
        self.assertEqual(manifest['segments'][0]['revision'], saved['revision'])
        self.assertNotIn('_project_ownership', manifest)
        self.assertNotIn('_storage_pin', manifest)
        receipt_key = next(key for key in after.state['documents'] if key.startswith('partial_reviews/'))
        receipt = json.loads(after.read(receipt_key))
        self.assertEqual(receipt['audio_source'], 'generated')
        assembly_record = json.loads(after.read(receipt['assembly_witness']))
        self.assertEqual(assembly_record['logical'], receipt['outputs'])
        self.assertFalse((self.store.project/'partial').exists())
        self.assertTrue(any(event == 'minimax_h3_context_loop_review_resolved' and data.get('partial_video') == item
                            for event, data in self.events))
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})

    def test_selected_earlier_take_and_settings_drive_partial_not_latest_candidate(self):
        incoming, first, second, decision = self.two_candidates()
        decision['action'] = 'stop'
        result = self.stop(incoming, second, uuid.uuid4().hex, decision=decision, select=True, candidate_count=2)
        item = result['ui']['images'][0]
        with chain.av.open(str(self.output/item['subfolder']/item['filename'])) as media:
            rendered = list(media.decode(video=0))
        snapshot = self.store.snapshot()
        manifest = json.loads(snapshot.read('partial/through_clip_0001.manifest.json'))
        self.assertEqual(manifest['segments'][0]['revision'], first['revision'])
        self.assertEqual(manifest['segments'][0]['seed'], first['seed'])
        self.assertEqual(manifest['segments'][0]['scene_prompt'], first['scene_prompt'])
        with runtime.runtime_access(self.store) as bound:
            path = bound.reader.path(first['segment'])
        with chain.av.open(str(path)) as media:
            expected = list(media.decode(video=0))
        self.assertTrue(all((a.to_ndarray(format='rgb24') == b.to_ndarray(format='rgb24')).all()
                            for a,b in zip(rendered, expected)))

    def test_partial_publication_failure_keeps_stop_and_retry_does_not_reopen_gate_or_render(self):
        incoming, saved = self.save_loop_scene()
        job, original = uuid.uuid4().hex, self.store.commit_artifacts
        def fail(base, changes, *args, **kwargs):
            if any(key.startswith('partial_reviews/') for key in changes):
                raise OSError('partial before publication')
            return original(base, changes, *args, **kwargs)
        with patch.object(self.store, 'commit_artifacts', fail), self.assertRaisesRegex(OSError, 'before publication'):
            self.stop(incoming, saved, job)
        token = next(data['token'] for event,data in self.events if event == 'minimax_h3_context_loop_review')
        before = self.store.snapshot()
        self.assertNotIn('partial/through_clip_0001.manifest.json', before.state['documents'])
        review_key = next(key for key in before.state['documents'] if key.endswith('/review.json'))
        self.assertEqual(json.loads(before.read(review_key))['outcome'], 'stop')
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered twice')):
            result = self.stop(incoming, saved, job)
        self.assert_resolved_retry(result, token)
        self.assertTrue(result['ui']['images'])
        after = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered twice')):
            repeated = self.stop(incoming, saved, job)
        self.assertEqual(result['result'][1], repeated['result'][1])
        self.assert_resolved_retry(repeated, token)
        self.assertEqual(after, self.store.snapshot().reference)

    def test_no_export_grant_does_not_accept_stop_or_write_partial(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot().read('checkpoints/clip_0001.json')
        with self.assertRaisesRegex(ValueError, 'export writes'):
            self.stop(incoming, saved, uuid.uuid4().hex, export=False)
        # Publishing the Review inventory before the user decides is allowed
        # by the separate handoff grant. No Stop decision/export was accepted.
        after = self.store.snapshot()
        self.assertEqual(before, after.read('checkpoints/clip_0001.json'))
        self.assertFalse(any(key.endswith('/review.json') for key in after.state['documents']))
        self.assertNotIn('partial/through_clip_0001.manifest.json', after.state['documents'])

    def test_missing_optional_source_audio_keeps_explicit_silent_fallback_and_retry(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        result = self.stop(incoming, saved, job, partial_audio_source='source')
        self.assertIn('audio unavailable', result['result'][1])
        item = result['ui']['images'][0]
        with chain.av.open(str(self.output/item['subfolder']/item['filename'])) as media:
            self.assertFalse(media.streams.audio)
        after = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('retried failed audio')):
            repeated = self.stop(incoming, saved, job, partial_audio_source='source')
        self.assertEqual(repeated['result'][1], result['result'][1])
        self.assertEqual(after, self.store.snapshot().reference)

    def test_lost_export_acknowledgement_recovers_same_video_without_new_render_or_root(self):
        incoming, saved = self.save_loop_scene()
        job, original = uuid.uuid4().hex, self.store.commit_artifacts
        def lose(base, changes, *args, **kwargs):
            result = original(base, changes, *args, **kwargs)
            if any(key.startswith('partial_reviews/') for key in changes):
                raise OSError('lost partial acknowledgement')
            return result
        with patch.object(self.store, 'commit_artifacts', lose), self.assertRaisesRegex(OSError, 'lost partial'):
            self.stop(incoming, saved, job, partial_audio_source='checkpointed')
        token = next(data['token'] for event,data in self.events if event == 'minimax_h3_context_loop_review')
        after = self.store.snapshot()
        self.assertIn('partial/through_clip_0001.manifest.json', after.state['documents'])
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered twice')):
            result = self.stop(incoming, saved, job, partial_audio_source='checkpointed')
        self.assertTrue(result['ui']['images'])
        self.assert_resolved_retry(result, token)
        self.assertEqual(after.reference, self.store.snapshot().reference)
        self.assertEqual(len([key for key in after.state['documents'] if key.startswith('partial_reviews/')]), 1)

    def test_damaged_accepted_video_cannot_be_hidden_by_retry_or_silent_fallback(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        result = self.stop(incoming, saved, job, partial_audio_source='checkpointed')
        item = result['ui']['images'][0]
        video = self.output/item['subfolder']/item['filename']
        video.write_bytes(b'damaged disposable partial video')
        before = self.store.snapshot().reference
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            with self.assertRaises(ValueError):
                self.stop(incoming, saved, job, partial_audio_source='checkpointed')
        self.assertEqual(before, self.store.snapshot().reference)

    def test_lost_websocket_reply_retries_original_token_node_and_video_without_render(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        execution = module('storage_review_execution').ReviewExecution
        notify = execution._notify_stop
        def lose(bound, decision, result):
            server = bound.chain.PromptServer.instance
            send = server.send_sync
            def fail(event, payload, client):
                if event == 'minimax_h3_context_loop_review_resolved':
                    raise OSError('lost Stop websocket reply')
                return send(event, payload, client)
            with patch.object(server, 'send_sync', fail):
                return notify(bound, decision, result)
        with patch.object(execution, '_notify_stop', lose), self.assertRaisesRegex(OSError, 'websocket reply'):
            self.stop(incoming, saved, job)
        token = next(data['token'] for event,data in self.events if event == 'minimax_h3_context_loop_review')
        after = self.store.snapshot()
        self.assertIn('partial/through_clip_0001.manifest.json', after.state['documents'])
        with patch.object(chain, '_review_display_id', return_value='different-current-display'), \
                patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered twice')):
            result = self.stop(incoming, saved, job)
        self.assert_resolved_retry(result, token)  # Stored original "review", not the new display ID.
        self.assertEqual(self.store.snapshot().reference, after.reference)

    def test_prepared_stop_cannot_send_another_reviews_token(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        execution = module('storage_review_execution').ReviewExecution
        original = execution._publish
        def forged(bound, decision):
            decision = dict(decision, notification=dict(token='f'*32, node_id='other'))
            return original(bound, decision)
        with patch.object(execution, '_publish', forged), self.assertRaisesRegex(ValueError, 'notification differs'):
            self.stop(incoming, saved, job)
        after = self.store.snapshot()
        self.assertFalse(any(key.endswith('/review.json') for key in after.state['documents']))
        self.assertFalse(any(event == 'minimax_h3_context_loop_review_resolved' for event,_ in self.events))

    def test_named_branch_partial_keeps_main_plan_assignments_and_exports_unchanged(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Partial fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            self.plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        result = self.stop(incoming, saved, uuid.uuid4().hex)
        after = self.store.snapshot()
        prefix = 'branches/'+branch['id']+'/'
        partial = json.loads(after.read(prefix+'partial/through_clip_0001.manifest.json'))
        self.assertEqual(partial['_branch_id'], branch['id'])
        self.assertTrue(result['ui']['images'])
        self.assertNotIn('partial/through_clip_0001.manifest.json', after.state['documents'])
        self.assertEqual(before.state['scope_revisions']['branch:main'],
                         after.state['scope_revisions']['branch:main'])
        for key in ('plan.json', 'checkpoints/clip_0001.json', 'checkpoints/clip_0002.json'):
            self.assertEqual(before.read(key), after.read(key))

    def test_partial_keeps_selected_alt_picture_and_base_soundtrack_for_prior_scene(self):
        import _storage_alternate_delivery_integration_test as alternate_fixture
        alt = alternate_fixture.AlternateDeliveryTests()
        alt.f, alt.store = self, self.store
        original_frames = self.frames
        self.frames = fixture.torch.full_like(self.frames, .8)
        try:
            alt_incoming, alternate = alt.candidate()
            alt.end(alt_incoming, alternate)
        finally:
            self.frames = original_frames
        with fixture.carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(self.plan, 2)[1]
            saved = chain.MiniMaxH3ChainSegmentSave().save(
                incoming, self.frames[:incoming['plan']['shots'][1]['delivered_frames']],
                fixture.av_latent(.4), self.audio(2))['result'][0]
        result = self.stop(incoming, saved, uuid.uuid4().hex, partial_audio_source='checkpointed')
        snapshot = self.store.snapshot()
        manifest = json.loads(snapshot.read('partial/through_clip_0002.manifest.json'))
        self.assertEqual(manifest['segments'][0]['revision'], self.originals[0]['revision'])
        self.assertEqual(manifest['segments'][0]['generated_audio'], self.originals[0]['generated_audio'])
        self.assertEqual(manifest['editorial']['replacements'][0]['alternate_revision'], alternate['revision'])
        item = result['ui']['images'][0]
        with chain.av.open(str(self.output/item['subfolder']/item['filename'])) as media:
            frames = list(media.decode(video=0))
            self.assertEqual(len(media.streams.audio), 1)
        self.assertEqual(len(frames), self.originals[0]['delivered_frames']+saved['delivered_frames'])
        alternate_path = self.store.payload_path(snapshot, alternate['segment'].split('h3_chains/'+self.run+'/')[1])
        with chain.av.open(str(alternate_path)) as media:
            expected = list(media.decode(video=0))
        self.assertTrue(all((a.to_ndarray(format='rgb24') == b.to_ndarray(format='rgb24')).all()
                            for a,b in zip(frames, expected)))

    def test_partial_exports_selected_take_after_rejected_candidate_cleanup(self):
        incoming, first, second, decision = self.two_candidates()
        decision.update(action='stop', kept_candidate_revisions=[first['revision']])
        job = uuid.uuid4().hex
        result = self.stop(incoming, second, job, decision=decision, select=True,
                           retention=True, candidate_count=2)
        after = self.store.snapshot()
        self.assertEqual(json.loads(after.read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        self.assertNotIn('checkpoints/clip_0001.'+second['revision']+'.json', after.state['documents'])
        manifest = json.loads(after.read('partial/through_clip_0001.manifest.json'))
        self.assertEqual(manifest['segments'][0]['revision'], first['revision'])
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            repeated = self.stop(incoming, second, job, select=True, retention=True, candidate_count=2)
        self.assertEqual(result['result'][1], repeated['result'][1])
        self.assertEqual(after.reference, self.store.snapshot().reference)

    def test_older_partial_retry_preserves_later_partial_and_assignment(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        first = self.stop(incoming, saved, job)
        newer_incoming, newer = self.save_loop_scene()
        second = self.stop(newer_incoming, newer, uuid.uuid4().hex)
        latest = self.store.snapshot()
        self.assertNotEqual(first['ui']['images'], second['ui']['images'])
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            old = self.stop(incoming, saved, job)
        self.assertEqual(old['ui']['images'], first['ui']['images'])
        self.assertEqual(latest.reference, self.store.snapshot().reference)
        self.assertEqual(json.loads(latest.read('partial/through_clip_0001.manifest.json'))['segments'][0]['revision'],
                         newer['revision'])

    def test_actual_prompt_executor_stops_with_partial_and_does_not_run_scene_two(self):
        result = fixture.execute_graph(self, stop=True, partial=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([scene for scene, _seed in result['calls']], [1])
        self.assertFalse(result['outputs'])
        snapshot = self.store.snapshot()
        manifest = json.loads(snapshot.read('partial/through_clip_0001.manifest.json'))
        receipt_key = next(key for key in snapshot.state['documents'] if key.startswith('partial_reviews/'))
        record = json.loads(snapshot.read(receipt_key))
        video = self.store.payload_path(snapshot, record['outputs']['video'], verify=True)
        with chain.av.open(str(video)) as media:
            embedded = json.loads({key.lower():value for key,value in media.metadata.items()}['prompt'])
        archive = manifest['archives']['api_prompt'].split('h3_chains/'+self.run+'/')[1]
        expected = json.loads(snapshot.read(archive))
        self.assertEqual(json.dumps(embedded, sort_keys=True), json.dumps(expected, sort_keys=True))
        self.assertTrue(any(math.isnan(value) for node in embedded.values()
                            for value in node.get('is_changed', []) if isinstance(value, float)))

    def test_reverse_recovery_preserves_partial_video_and_ordinary_assembly(self):
        incoming, saved = self.save_loop_scene()
        self.stop(incoming, saved, uuid.uuid4().hex, partial_audio_source='checkpointed')
        before = self.store.snapshot()
        control = module('storage_state')
        receipt = self.lab/'partial-copy-receipt.json'
        control.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source),
                                         independent_copies=True))
        recovery = module('storage_recovery')
        output = self.lab/'partial-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, output, self.lab/'partial-recovery', rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/self.run
        key = 'partial/through_clip_0001.manifest.json'
        self.assertEqual((root/key).read_bytes(), before.read(key))
        record_key = next(key for key in before.state['documents'] if key.startswith('partial_reviews/'))
        record = json.loads(before.read(record_key))
        video = self.store.payload_path(before, record['outputs']['video'], verify=True)
        copied = root/record['outputs']['video']
        self.assertEqual(copied.read_bytes(), video.read_bytes())
        self.assertNotEqual((copied.stat().st_dev, copied.stat().st_ino), (video.stat().st_dev, video.stat().st_ino))
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))
        fixture.generation.folder_paths.output_directory = str(output)
        manifest = json.loads((root/key).read_bytes())
        manifest['_project_ownership'] = self.proof
        reassembled = chain.MiniMaxH3ChainAssemble().assemble(manifest, 'generated', 'recovered_partial', 192)
        with chain.av.open(reassembled['result'][0]) as media:
            self.assertEqual(len(list(media.decode(video=0))), saved['delivered_frames'])
            self.assertEqual(len(media.streams.audio), 1)
        self.assertEqual(before.reference, self.store.snapshot().reference)
        # A used recovery destination is no longer a pristine copy. Replaying
        # migration must not erase a new export merely to match its old receipt.
        with self.assertRaisesRegex(ValueError, 'untracked files'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(copied.read_bytes(), video.read_bytes())

    def test_mixed_resolution_partial_exports_only_current_chapter(self):
        control = module('storage_state')
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Chapter size fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        prefix = 'branches/'+branch['id']+'/'
        chapters = [dict(id='first', title='First', text='', start_scene=1, start_scene_id='first'),
                    dict(id='second', title='Second', text='', start_scene=2, start_scene_id='second',
                         resolution=dict(width=64, height=32))]
        self.store.commit(self.store.snapshot(), {prefix+'editorial.json':dict(
            data=control._encode(chain._normalize_run_editorial(dict(chapters=chapters), self.run)),
            scope='branch:'+branch['id'], category='branches', immutable=False)}, operation_id=uuid.uuid4().hex)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            raw = chain._effective_editor_plan(self.plan)
            raw.update(chapters=chapters, _branch_id=branch['id'])
            raw['shots'][1].update(context_length=0, audio_context_length=0)
            plan = chain._normalize_plan(json.dumps(raw), self.run, 32, 32, 1, 'video', 'head',
                                         'disabled', 'generated_audio', 1, 5/24, 2, 7, 18)
            plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
            self.plan = dict(plan, _branch_id=branch['id'], _storage_pin=bound.pin, _project_ownership=self.proof)
        with fixture.carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(self.plan, 2)[1]
            count = incoming['plan']['shots'][1]['delivered_frames']
            latent = fixture.av_latent(.4)
            latent['samples'][0] = fixture.torch.full((1,24,2,2,4), .4)
            saved = chain.MiniMaxH3ChainSegmentSave().save(incoming, fixture.torch.zeros(count,32,64,3),
                latent, self.audio(2))['result'][0]
        result = self.stop(incoming, saved, uuid.uuid4().hex, partial_audio_source='checkpointed')
        after = self.store.snapshot()
        manifest = json.loads(after.read(prefix+'partial/through_clip_0002.manifest.json'))
        self.assertEqual(manifest['chapter']['number'], 2)
        self.assertEqual([s['index'] for s in manifest['segments']], [2])
        self.assertEqual(manifest['compatibility']['width'], 64)
        item = result['ui']['images'][0]
        with chain.av.open(str(self.output/item['subfolder']/item['filename'])) as media:
            self.assertEqual((media.streams.video[0].width, media.streams.video[0].height), (64,32))
            self.assertEqual(len(list(media.decode(video=0))), count)
        self.assertEqual([s['index'] for s in incoming['segments']], [1])


if __name__ == '__main__':
    unittest.main(argv=[__file__], verbosity=2)
