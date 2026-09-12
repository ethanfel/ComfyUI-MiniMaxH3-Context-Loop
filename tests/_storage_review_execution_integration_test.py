"""Real CPU Save -> asynchronous Review -> Loop End, on independent copies."""
import asyncio
import copy
import contextvars
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_generation_integration_test as generation

chain, carriers, module = generation.chain, generation.carriers, generation.module
torch, av_latent = generation.torch, generation.av_latent
review = module('storage_review_execution')


def execute_graph(fixture, *, retry=False, select=False, cache='classic', prune=False, defer=False,
                  stop=False, partial=False):
    """Only model generation and UI transport are fakes; execute real nodes."""
    import execution
    import nodes
    calls, outputs, gates = [], [], []
    plan = copy.deepcopy(fixture.plan)

    class Source:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required':{}}
        RETURN_TYPES = (chain.PLAN_TYPE,)
        FUNCTION = 'read'
        def read(self):
            return (copy.deepcopy(plan),)

    class Pixels:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required':{'state':(chain.STATE_TYPE,)}}
        RETURN_TYPES = ('IMAGE', 'LATENT', 'AUDIO')
        FUNCTION = 'render'
        def render(self, state):
            index = state['index']
            calls.append((index, state['plan']['shots'][index-1]['seed']))
            count = state['plan']['shots'][index-1]['delivered_frames']
            return torch.full((count,32,32,3), .3), av_latent(.4), fixture.audio(index)

    class Sink:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required':{'manifest':(chain.MANIFEST_TYPE,)}}
        RETURN_TYPES = ()
        FUNCTION = 'read'
        OUTPUT_NODE = True
        def read(self, manifest):
            outputs.append(manifest)
            return ()

    class Server:
        client_id = None
        last_node_id = None
        def send_sync(self, event, data, *args):
            if event == 'minimax_h3_context_loop_review' and data.get('pending_decision'):
                if data.get('deferred_review'):
                    gates.append(data['token'])
                    return
                pending = chain._PENDING_REVIEWS[data['token']]
                if pending['future'].done():
                    return
                decision = dict(action='stop' if stop else 'approve', kept_candidate_revisions=data['kept_candidate_revisions'])
                if select:
                    decision['candidate_revision'] = data['candidates'][0]['revision']
                    if prune:
                        decision['kept_candidate_revisions'] = [decision['candidate_revision']]
                if retry and not gates:
                    decision = dict(action='retry', scene_prompt='Executor retry é\n雪', seed=13, raw_frames=5)
                gates.append(data['token'])
                pending['future'].set_result(decision)

    mapping = dict(H3ReviewSource=Source, H3ReviewPixels=Pixels, H3ReviewSink=Sink)
    for cls in (chain.MiniMaxH3ChainLoopStart, chain.MiniMaxH3ChainSegmentSave,
                chain.MiniMaxH3ChainReview, chain.MiniMaxH3ChainLoopEnd):
        mapping[cls.__name__] = cls
    prompt = {
        '1':dict(class_type='H3ReviewSource', inputs={}),
        '2':dict(class_type='MiniMaxH3ChainLoopStart', inputs=dict(plan=['1',0], start_clip=1)),
        '3':dict(class_type='H3ReviewPixels', inputs=dict(state=['2',1])),
        '4':dict(class_type='MiniMaxH3ChainSegmentSave', inputs=dict(state=['2',1], images=['3',0],
                                                                 sampled_latent=['3',1], audio=['3',2])),
        '5':dict(class_type='MiniMaxH3ChainReview', inputs=dict(state=['2',1], segment=['4',0], enabled=True,
            play_notification_sound=False, auto_continue_timeout_minutes=0, unload_models_while_waiting=False,
            assemble_partial_on_stop=partial, partial_audio_source='none', audio=['3',2])),
        '6':dict(class_type='MiniMaxH3ChainLoopEnd', inputs=dict(flow=['2',0], state=['2',1], images=['3',0],
                                                             sampled_latent=['3',1], segment=['5',0])),
        '7':dict(class_type='H3ReviewSink', inputs=dict(manifest=['6',0])),
    }
    server = Server()
    if select:
        prompt['5']['inputs']['candidate_count'] = 2
    if defer:
        prompt['5']['inputs']['pending_review'] = {'version':1, 'defer_completed_batch':True}
    with patch.dict(nodes.NODE_CLASS_MAPPINGS, mapping), \
            patch.object(chain, 'PromptServer', SimpleNamespace(instance=server)), \
            patch.object(chain, 'web', object()), carriers.node_host(fixture.store, operation_namespace=uuid.uuid4().hex,
                generation_writers=(chain.MiniMaxH3ChainSegmentSave.save, chain.MiniMaxH3ChainLoopEnd.end,
                                    chain.MiniMaxH3ChainReview.review),
                handoff_writers=(chain.MiniMaxH3ChainReview.review,), export_writers=(chain.MiniMaxH3ChainReview.review,),
                retention_writers=(chain.MiniMaxH3ChainReview.review,) if prune else (),
                input_adapters={chain.MiniMaxH3ChainReview.review: review.review_inputs,
                    chain.MiniMaxH3ChainLoopEnd.end: module('storage_continuation').loop_end_inputs}):
        executor = execution.PromptExecutor(server, cache_type={
            'classic':execution.CacheType.CLASSIC, 'none':execution.CacheType.NONE}[cache],
            cache_args={'ram':0, 'ram_inactive':0})
        executor.execute(prompt, uuid.uuid4().hex, execute_outputs=['7'])
    errors = [data['exception_message'] for event,data in executor.status_messages if event == 'execution_error']
    return dict(success=executor.success, errors=errors, calls=calls, outputs=outputs, gates=gates)


class ReviewExecutionTests(unittest.TestCase):
    setUp = generation.GenerationIntegrationTests.setUp
    audio = generation.GenerationIntegrationTests.audio
    save = generation.GenerationIntegrationTests.save
    save_loop_scene = generation.GenerationIntegrationTests.save_loop_scene
    expand_loop = generation.GenerationIntegrationTests.expand_loop

    def host(self, job, *, grant=True, export=True, adapter=True, select=False, retention=False):
        return carriers.node_host(self.store, operation_namespace=job,
            handoff_writers=(chain.MiniMaxH3ChainReview.review,) if grant else (),
            generation_writers=(chain.MiniMaxH3ChainReview.review,) if select else (),
            export_writers=(chain.MiniMaxH3ChainReview.review,) if export else (),
            retention_writers=(chain.MiniMaxH3ChainReview.review,) if retention else (),
            input_adapters={chain.MiniMaxH3ChainReview.review: review.review_inputs,
                chain.MiniMaxH3ChainLoopEnd.end: module('storage_continuation').loop_end_inputs} if adapter else {})

    def execute(self, incoming, saved, decision=None, **options):
        self.events = []
        def send(event, payload, client):
            self.events.append((event, copy.deepcopy(payload)))
            if event == 'minimax_h3_context_loop_review' and payload.get('pending_decision'):
                pending = chain._PENDING_REVIEWS[payload['token']]
                if not pending['future'].done():
                    value = decision(payload) if callable(decision) else decision
                    pending['future'].set_result(value or {'action':'approve'})
        server = SimpleNamespace(instance=SimpleNamespace(send_sync=send, client_id='copy-only-test'))
        with patch.object(chain, 'PromptServer', server), patch.object(chain, 'web', object()):
            return asyncio.run(chain.MiniMaxH3ChainReview().review(incoming, saved,
                options.pop('enabled', True), False, 0, False,
                options.pop('assemble_partial_on_stop', False), options.pop('partial_audio_source', 'none'),
                unique_id='review', **options))

    def next_state(self, incoming, saved):
        result = self.expand_loop(incoming, saved)
        return next(value['inputs']['initial_state'] for value in result['expand'].values()
                    if value['class_type'] == 'MiniMaxH3ChainLoopStart')

    def two_candidates(self):
        incoming, first = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            retry = self.execute(incoming, first, candidate_count=2)['result'][0]
            current = self.next_state(incoming, retry)
        chain._ACTIVE_CANDIDATE_BATCHES.pop(current['candidate_batch']['batch_token'], None)
        current['plan'] = chain._plan_with_review_revision(current['plan'], 1, 'Second candidate edit', 19, 5)
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            second = chain.MiniMaxH3ChainSegmentSave().save(current, torch.full_like(self.frames, .7),
                av_latent(.8), self.audio(1))['result'][0]
        decision = dict(action='approve', candidate_revision=first['revision'],
                        kept_candidate_revisions=[first['revision'], second['revision']])
        return current, first, second, decision

    def test_select_earlier_candidate_restores_exact_prompt_seed_context_and_recovery(self):
        incoming, first, second, decision = self.two_candidates()
        before = self.store.snapshot()
        job = uuid.uuid4().hex
        with self.host(job, select=True):
            result = self.execute(incoming, second, decision, candidate_count=2)
            accepted = result['result'][0]
            advanced = self.next_state(incoming, accepted)
        self.assertEqual(accepted['revision'], first['revision'])
        self.assertEqual(advanced['segments'][0]['revision'], first['revision'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], first['seed'])
        self.assertEqual(advanced['plan']['shots'][0]['prompt'], first['prompt'])
        self.assertTrue(torch.equal(advanced['previous_latent']['samples'][0], av_latent(.4)['samples'][0]))
        self.assertFalse(torch.equal(advanced['previous_latent']['samples'][0], av_latent(.8)['samples'][0]))
        self.assertEqual(advanced['plan']['_storage_pin'], accepted['_storage_pin'])
        after = self.store.snapshot()
        self.assertEqual(json.loads(after.read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        archive = 'recovery_archives/'+first['revision']+'/plan.json'
        self.assertEqual(after.read('plan.json'), before.read(archive))
        self.assertEqual(before.read('checkpoints/clip_0002.json'), after.read('checkpoints/clip_0002.json'))
        self.assertEqual(len(generation.project.payload_catalog(before)), len(generation.project.payload_catalog(after)))
        witness = json.loads(after.read(accepted[review.KEY]['witness']))
        self.assertNotIn('owner_id', json.dumps(witness))
        self.assertNotIn('context_frames', witness['output_segment']['_h3_review_decision'])
        with self.host(job, select=True):
            repeated = self.execute(incoming, second, decision, candidate_count=2)
        self.assertEqual(review.output_digest(repeated['result'][0], second['_storage_pin']),
                         review.output_digest(accepted, second['_storage_pin']))
        self.assertFalse(self.events)
        self.assertEqual(self.store.snapshot().reference, after.reference)

    def test_selected_candidate_continuation_rejects_forged_plan_context_or_media(self):
        incoming, first, second, decision = self.two_candidates()
        with self.host(uuid.uuid4().hex, select=True):
            accepted = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
            for change in ('seed', 'prompt', 'video', 'audio', 'context', 'path', 'pin'):
                bad = copy.deepcopy(accepted)
                selected = bad['_h3_review_decision']
                if change in ('seed', 'prompt'):
                    selected['plan']['shots'][0][change] = 7 if change == 'seed' else 'forged'
                elif change in ('video', 'audio'):
                    selected['sampled_latent']['samples'][0 if change == 'video' else 1].add_(1)
                elif change == 'context':
                    selected['context_frames'].add_(1)
                elif change == 'pin':
                    selected['plan']['_storage_pin'] = second['_storage_pin']
                else:
                    bad['segment'] = second['segment']
                with self.subTest(change=change), self.assertRaises((ValueError, generation.state.StateConflict)):
                    self.next_state(incoming, bad)

    def test_candidate_assignment_requires_separate_generation_grant(self):
        incoming, first, second, decision = self.two_candidates()
        before = self.store.snapshot().read('checkpoints/clip_0001.json')
        with self.host(uuid.uuid4().hex), self.assertRaisesRegex(ValueError, 'generation writes'):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'), before)

    def test_selecting_take_outside_witnessed_batch_is_rejected(self):
        incoming, first, second, decision = self.two_candidates()
        decision['candidate_revision'] = self.originals[0]['revision']
        with self.host(uuid.uuid4().hex, select=True), self.assertRaisesRegex(ValueError, 'witnessed Review batch'):
            self.execute(incoming, second, decision, candidate_count=2)

    def test_real_prompt_executor_selects_earlier_candidates_then_finishes(self):
        result = execute_graph(self, select=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([index for index,seed in result['calls']], [1,1,2,2])
        self.assertEqual(len(result['gates']), 2)
        self.assertEqual([s['seed'] for s in result['outputs'][0]['segments']], [18446744073709551601, 8])
        self.store.verify_payloads()

    def test_selection_publication_failure_keeps_current_take_then_resumes_without_new_gate(self):
        incoming, first, second, decision = self.two_candidates()
        job = uuid.uuid4().hex
        before = self.store.snapshot()
        commit = self.store.commit
        with self.host(job, select=True):
            operation = carriers.handoff_operation(chain.MiniMaxH3ChainReview.review, 'review')
            def fail(base, changes, **kwargs):
                if kwargs.get('operation_id') == operation:
                    self.assertIn('plan.json', changes)
                    self.assertIn('checkpoints/clip_0001.json', changes)
                    self.assertIn(review._address(operation), changes)
                    raise OSError('injected candidate publication failure')
                return commit(base, changes, **kwargs)
            with patch.object(self.store, 'commit', fail), self.assertRaisesRegex(OSError, 'injected candidate'):
                self.execute(incoming, second, decision, candidate_count=2)
        failed = self.store.snapshot()
        self.assertEqual(before.read('plan.json'), failed.read('plan.json'))
        self.assertEqual(before.read('checkpoints/clip_0001.json'), failed.read('checkpoints/clip_0001.json'))
        self.assertNotIn(operation, failed.state['operations'])
        with self.host(job, select=True):
            result = self.execute(incoming, second, {'action':'retry'}, candidate_count=2)
            advanced = self.next_state(incoming, result['result'][0])
        self.assertFalse(self.events)
        self.assertEqual(advanced['segments'][0]['revision'], first['revision'])
        self.assertEqual(json.loads(self.store.snapshot().read('plan.json'))['shots'][0]['seed'], first['seed'])

    def test_selection_lost_ack_recovers_exact_assignment_without_second_publication(self):
        incoming, first, second, decision = self.two_candidates()
        job = uuid.uuid4().hex
        runtime = module('storage_runtime')
        record = runtime.ProjectRuntime.record_commit
        with self.host(job, select=True):
            operation = carriers.handoff_operation(chain.MiniMaxH3ChainReview.review, 'review')
            def lose(bound, receipt):
                if receipt['operation_id'] == operation:
                    raise OSError('injected candidate lost acknowledgement')
                return record(bound, receipt)
            with patch.object(runtime.ProjectRuntime, 'record_commit', lose), self.assertRaisesRegex(OSError, 'lost acknowledgement'):
                self.execute(incoming, second, decision, candidate_count=2)
        after = self.store.snapshot()
        self.assertEqual(json.loads(after.read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        with self.host(job, select=True):
            result = self.execute(incoming, second, candidate_count=2)
            self.next_state(incoming, result['result'][0])
        self.assertFalse(self.events)
        self.assertEqual(self.store.snapshot().reference, after.reference)

    def test_stop_can_select_earlier_candidate_without_reentering_loop_on_retry(self):
        incoming, first, second, decision = self.two_candidates()
        decision['action'] = 'stop'
        job = uuid.uuid4().hex
        with self.host(job, select=True):
            stopped = self.execute(incoming, second, decision, candidate_count=2)
        self.assertIsInstance(stopped['result'][0], chain.ExecutionBlocker)
        after = self.store.snapshot()
        self.assertEqual(json.loads(after.read('checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        with self.host(job, select=True):
            repeated = self.execute(incoming, second, candidate_count=2)
        self.assertIsInstance(repeated['result'][0], chain.ExecutionBlocker)
        self.assertEqual([event for event,_ in self.events], ['minimax_h3_context_loop_review_resolved'])
        self.assertEqual(self.events[0][1]['action'], 'stop')
        self.assertEqual(self.store.snapshot().reference, after.reference)

    def test_named_branch_candidate_selection_preserves_other_branches(self):
        branches = module('working_branches').WorkingBranches
        with generation.runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Candidate fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with generation.runtime.runtime_access(self.store, selected=branch['id']) as bound:
            self.plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        before = self.store.snapshot()
        incoming, first, second, decision = self.two_candidates()
        with self.host(uuid.uuid4().hex, select=True):
            result = self.execute(incoming, second, decision, candidate_count=2)
            advanced = self.next_state(incoming, result['result'][0])
        after = self.store.snapshot()
        for address in ('plan.json', 'checkpoints/clip_0001.json', 'checkpoints/clip_0002.json'):
            self.assertEqual(before.read(address), after.read(address))
        prefix = 'branches/'+branch['id']+'/'
        self.assertEqual(json.loads(after.read(prefix+'checkpoints/clip_0001.json'))['segment']['revision'], first['revision'])
        self.assertEqual(advanced['plan']['_branch_id'], branch['id'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], first['seed'])

    def test_imported_candidate_with_control_prompt_sidecar_can_be_selected(self):
        first = self.originals[0]
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(self.plan, 1)[1]
            incoming['candidate_batch'] = dict(scene=1, target=2, kept_revisions=[first['revision']],
                candidates=[chain._review_candidate_record(first, {}, False, '')])
            second = chain.MiniMaxH3ChainSegmentSave().save(incoming, self.frames, av_latent(.8), self.audio(1))['result'][0]
        decision = dict(action='approve', candidate_revision=first['revision'],
                        kept_candidate_revisions=[first['revision'], second['revision']])
        with self.host(uuid.uuid4().hex, select=True):
            result = self.execute(incoming, second, decision, candidate_count=2)
            advanced = self.next_state(incoming, result['result'][0])
        self.assertEqual(advanced['segments'][0]['revision'], first['revision'])
        self.assertTrue(torch.equal(advanced['previous_latent']['samples'][0], av_latent(.1)['samples'][0]))

    def test_selected_checkpoint_damage_blocks_selection_and_accepted_replay(self):
        incoming, first, second, decision = self.two_candidates()
        job = uuid.uuid4().hex
        with self.host(job, select=True):
            self.execute(incoming, second, decision, candidate_count=2)
        path = self.store.payload_path(self.store.snapshot(), first['checkpoint'].split('h3_chains/'+self.run+'/')[1])
        raw = path.read_bytes()
        path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        after = self.store.snapshot().reference
        with self.host(job, select=True), self.assertRaises((ValueError, OSError)):
            self.execute(incoming, second, decision, candidate_count=2)
        self.assertEqual(self.store.snapshot().reference, after)

    def test_selected_take_survives_independent_reverse_recovery_and_ordinary_resume(self):
        incoming, first, second, decision = self.two_candidates()
        with self.host(uuid.uuid4().hex, select=True):
            selected = self.execute(incoming, second, decision, candidate_count=2)['result'][0]
        before = self.store.snapshot()
        receipt = self.lab/'selected-copy-receipt.json'
        generation.state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source),
                                                   independent_copies=True))
        recovery = module('storage_recovery')
        output = self.lab/'selected-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, output, self.lab/'selected-recovery', rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/self.run
        self.assertEqual((root/'plan.json').read_bytes(), before.read('plan.json'))
        self.assertEqual(json.loads((root/'checkpoints/clip_0001.json').read_bytes())['segment']['revision'], first['revision'])
        for candidate in (first, second):
            for key in ('checkpoint', 'segment', 'generated_audio'):
                address = candidate[key].split('h3_chains/'+self.run+'/')[1]
                original = self.store.payload_path(before, address, verify=True)
                copied = root/address
                self.assertEqual(copied.read_bytes(), original.read_bytes())
                self.assertNotEqual((copied.stat().st_dev, copied.stat().st_ino),
                                    (original.stat().st_dev, original.stat().st_ino))
        restored_plan = dict(selected['_h3_review_decision']['plan'])
        restored_plan.pop('_storage_pin')
        generation.folder_paths.output_directory = str(output)
        resumed = chain.MiniMaxH3ChainLoopStart().start(restored_plan, 2)[1]
        self.assertEqual(resumed['segments'][0]['revision'], first['revision'])
        self.assertEqual(resumed['plan']['shots'][0]['seed'], first['seed'])
        self.assertTrue(torch.equal(resumed['previous_latent']['samples'][0], av_latent(.4)['samples'][0]))
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))

    def test_actual_approve_then_loop_preserves_saved_scene_and_exact_input_context(self):
        incoming, saved = self.save_loop_scene()
        digest = module('storage_execution_digest').execution_digest(incoming)
        with self.host(uuid.uuid4().hex):
            result = self.execute(incoming, saved)
            accepted = result['result'][0]
            advanced = self.next_state(incoming, accepted)
        self.assertEqual(advanced['index'], 2)
        self.assertEqual(advanced['segments'][0]['revision'], saved['revision'])
        self.assertEqual(advanced['plan']['_storage_pin'], accepted['_storage_pin'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], 18446744073709551601)
        self.assertTrue(torch.equal(advanced['previous_latent']['samples'][0], av_latent(.4)['samples'][0]))
        self.assertEqual(module('storage_execution_digest').execution_digest(incoming), digest)
        self.assertNotIn(review.KEY, saved)
        self.assertFalse(chain._PENDING_REVIEWS)
        root = self.store.snapshot()
        witness = json.loads(root.read(accepted[review.KEY]['witness']))
        self.assertNotIn('owner_id', json.dumps(witness))
        preview = next(payload['video'] for event,payload in self.events
                       if event == 'minimax_h3_context_loop_review')
        self.assertTrue((self.output/preview['subfolder']/preview['filename']).is_file())
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})

    def test_retry_uses_edited_prompt_seed_duration_without_advancing_predecessor(self):
        incoming, saved = self.save_loop_scene()
        decision = dict(action='retry', scene_prompt='Edited é\n雪', seed=18446744073709551600, raw_frames=5)
        with self.host(uuid.uuid4().hex):
            result = self.execute(incoming, saved, decision)
            next_state = self.next_state(incoming, result['result'][0])
        self.assertEqual(next_state['index'], incoming['index'])
        self.assertEqual(next_state['segments'], incoming['segments'])
        self.assertIs(next_state['previous_frames'], incoming['previous_frames'])
        self.assertIs(next_state['previous_latent'], incoming['previous_latent'])
        self.assertEqual(next_state['plan']['shots'][0]['seed'], decision['seed'])
        self.assertIn(decision['scene_prompt'], next_state['plan']['shots'][0]['prompt'])
        self.assertEqual(next_state['plan']['shots'][0]['raw_frames'], 5)
        self.assertNotIn('candidate_batch', next_state)
        self.assertEqual(incoming['plan']['shots'][0]['seed'], 18446744073709551601)

    def test_automatic_batch_retry_retains_current_saved_take_and_keep_marks(self):
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            result = self.execute(incoming, saved, candidate_count=2)
            next_state = self.next_state(incoming, result['result'][0])
        batch = next_state['candidate_batch']
        self.assertEqual(batch['target'], 2)
        self.assertEqual(batch['candidates'][0]['segment']['revision'], saved['revision'])
        self.assertEqual(batch['kept_revisions'], [saved['revision']])
        self.assertNotEqual(next_state['plan']['shots'][0]['seed'], saved['seed'])
        chain._ACTIVE_CANDIDATE_BATCHES.pop(batch['batch_token'], None)

    def test_disabled_review_passes_through_without_new_writes_or_handoff_grant(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot().reference
        with self.host(uuid.uuid4().hex, grant=False, export=False):
            result = self.execute(incoming, saved, enabled=False)
            self.next_state(incoming, result['result'][0])
        self.assertEqual(result['result'][0], saved)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_replay_recovers_exact_decision_without_new_gate_or_new_root(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        with self.host(job):
            first = self.execute(incoming, saved, dict(action='retry', scene_prompt='Saved edit', seed=11, raw_frames=5))
        before = self.store.snapshot().reference
        with self.host(job):
            second = self.execute(incoming, saved, dict(action='approve'))
            self.next_state(incoming, second['result'][0])
        self.assertEqual(first, second)
        self.assertFalse(self.events)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_loop_rejects_changed_review_prompt_seed_receipt_state_or_context(self):
        incoming, saved = self.save_loop_scene()
        with self.host(uuid.uuid4().hex):
            result = self.execute(incoming, saved, dict(action='retry', scene_prompt='Saved edit', seed=11, raw_frames=5))
            accepted = result['result'][0]
            variants = []
            for key,value in [('scene_prompt','Forged'), ('seed',12), ('raw_frames',22)]:
                changed = copy.deepcopy(accepted)
                changed['_h3_review_decision'][key] = value
                variants.append((incoming, changed))
            changed = copy.deepcopy(accepted)
            changed[review.KEY]['receipt']['operation_id'] = uuid.uuid4().hex
            variants.append((incoming, changed))
            changed = copy.deepcopy(incoming)
            changed['plan']['shots'][0]['seed'] = 15
            variants.append((changed, accepted))
            variants.append((dict(incoming, candidate_batch={'scene':1}), accepted))
            variants.append((dict(incoming, previous_frames=torch.ones(1,32,32,3)), accepted))
            variants.append((dict(incoming, previous_latent=av_latent(.9)), accepted))
            before = self.store.snapshot().reference
            for value,segment in variants:
                with self.subTest(state=value is incoming):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.next_state(value, segment)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_review_requires_adapter_exact_grant_and_host_job_identity(self):
        incoming, saved = self.save_loop_scene()
        for options, pattern in [({'adapter':False}, 'mixed-root'), ({'grant':False}, 'writer grant')]:
            with self.host(uuid.uuid4().hex, **options), self.assertRaisesRegex(ValueError, pattern):
                self.execute(incoming, saved)
        with carriers.node_host(self.store, handoff_writers=(chain.MiniMaxH3ChainReview.review,),
                input_adapters={chain.MiniMaxH3ChainReview.review:review.review_inputs}), self.assertRaises(ValueError):
            self.execute(incoming, saved)

    def test_request_rejects_changed_inputs_before_reopening_gate(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        with self.host(job):
            self.execute(incoming, saved)
        before = self.store.snapshot().reference
        with self.host(job), self.assertRaises(ValueError):
            self.execute(incoming, saved, candidate_count=2)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_real_audio_preview_is_indexed_optional_media_with_exact_video_frames(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        with self.host(job):
            result = self.execute(incoming, saved, audio=self.audio(1))
            self.next_state(incoming, result['result'][0])
        previews = [payload for event,payload in self.events if payload.get('has_audio')]
        self.assertTrue(previews)
        item = previews[-1]['video']
        path = self.output/item['subfolder']/item['filename']
        self.assertIn('project/optional/previews/', str(path))
        with chain.av.open(str(path)) as container:
            self.assertEqual(len(container.streams.audio), 1)
            frames = [frame.to_ndarray(format='rgb24') for frame in container.decode(video=0)]
        source = self.store.payload_path(self.store.snapshot(), saved['segment'].split('h3_chains/'+self.run+'/')[1])
        with chain.av.open(str(source)) as container:
            original = [frame.to_ndarray(format='rgb24') for frame in container.decode(video=0)]
        self.assertEqual(len(frames), 5)
        self.assertTrue(all((a == b).all() for a,b in zip(frames, original)))
        self.assertGreater(self.store.verify_payloads(), 10)

    def test_stop_remains_blocked_on_exact_retry_and_cannot_be_forged_into_continue(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        with self.host(job):
            first = self.execute(incoming, saved, dict(action='stop'))
            self.assertIsInstance(first['result'][0], chain.ExecutionBlocker)
        before = self.store.snapshot().reference
        with self.host(job):
            second = self.execute(incoming, saved)
            self.assertIsInstance(second['result'][0], chain.ExecutionBlocker)
            operation = carriers.handoff_operation(chain.MiniMaxH3ChainReview.review, 'review')
            receipt = self.store.snapshot().state['operations'][operation]
            forged = dict(saved, _storage_pin=dict(saved['_storage_pin'], root=before),
                **{review.KEY:dict(receipt=receipt, witness=review._address(operation))})
            with self.assertRaises(ValueError):
                self.next_state(incoming, forged)
        self.assertEqual(first['result'][1], second['result'][1])
        self.assertEqual([event for event,_ in self.events], ['minimax_h3_context_loop_review_resolved'])
        self.assertEqual(self.events[0][1]['action'], 'stop')
        self.assertEqual(self.store.snapshot().reference, before)

    def test_failed_acceptance_replays_prepared_retry_without_rerolling_seed_or_reopening_gate(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        original_commit = self.store.commit
        with self.host(job):
            operation = carriers.handoff_operation(chain.MiniMaxH3ChainReview.review, 'review')
            def fail(base, changes, **kwargs):
                if kwargs.get('operation_id') == operation:
                    raise OSError('injected transition publication failure')
                return original_commit(base, changes, **kwargs)
            with patch.object(self.store, 'commit', fail), self.assertRaisesRegex(OSError, 'injected'):
                self.execute(incoming, saved, candidate_count=2)
        self.assertNotIn(operation, self.store.snapshot().state['operations'])
        prepared = json.loads((self.store.project/'project/jobs'/operation/'review-result.json').read_text())['value']
        selected_seed = prepared['output_segment']['_h3_review_decision']['seed']
        with self.host(job):
            recovered = self.execute(incoming, saved, candidate_count=2)
            next_state = self.next_state(incoming, recovered['result'][0])
        self.assertFalse(self.events)
        self.assertEqual(next_state['plan']['shots'][0]['seed'], selected_seed)
        chain._ACTIVE_CANDIDATE_BATCHES.pop(next_state['candidate_batch']['batch_token'], None)

    def test_lost_acknowledgement_reuses_only_the_accepted_transition(self):
        incoming, saved = self.save_loop_scene()
        job = uuid.uuid4().hex
        runtime = module('storage_runtime')
        original_record = runtime.ProjectRuntime.record_commit
        with self.host(job):
            operation = carriers.handoff_operation(chain.MiniMaxH3ChainReview.review, 'review')
            def lose(bound, receipt):
                if receipt['operation_id'] == operation:
                    raise OSError('lost Review acknowledgement')
                return original_record(bound, receipt)
            with patch.object(runtime.ProjectRuntime, 'record_commit', lose), self.assertRaisesRegex(OSError, 'lost Review'):
                self.execute(incoming, saved)
        accepted = self.store.snapshot()
        self.assertIn(operation, accepted.state['operations'])
        with self.host(job):
            result = self.execute(incoming, saved)
            self.next_state(incoming, result['result'][0])
        self.assertFalse(self.events)
        self.assertEqual(self.store.snapshot().reference, accepted.reference)

    def test_interruption_keeps_checkpoint_and_a_new_job_can_review_the_same_pin(self):
        incoming, saved = self.save_loop_scene()
        async def interrupted(*args):
            raise asyncio.CancelledError()
        with self.host(uuid.uuid4().hex), patch.object(chain, '_await_review_decision', interrupted):
            with self.assertRaises(asyncio.CancelledError):
                self.execute(incoming, saved)
        self.assertFalse(chain._PENDING_REVIEWS)
        self.assertEqual(json.loads(self.store.snapshot().read('checkpoints/clip_0001.json'))['segment']['revision'], saved['revision'])
        with self.host(uuid.uuid4().hex):
            completed = self.execute(incoming, saved)
            self.next_state(incoming, completed['result'][0])

    def test_ownership_transfer_while_waiting_rejects_the_old_review(self):
        incoming, saved = self.save_loop_scene()
        outside = contextvars.copy_context()
        def transfer():
            with generation.runtime.runtime_access(self.store, ownership_writes=True):
                generation.ownership.claim_project_ownership(self.output, self.run, 'new-review-owner', force=True)
        def decide(payload):
            outside.run(transfer)
            return dict(action='approve')
        with self.host(uuid.uuid4().hex), self.assertRaises(ValueError):
            self.execute(incoming, saved, decide)
        self.assertFalse(chain._PENDING_REVIEWS)

    def test_branch_change_while_waiting_cannot_be_absorbed_by_inventory_commits(self):
        incoming, saved = self.save_loop_scene()
        outside = contextvars.copy_context()
        def branch_change():
            root = self.store.snapshot()
            self.store.commit(root, {'review_test_branch_change.json':dict(data=b'{}', scope='branch:main',
                category='branches', immutable=False)}, operation_id=uuid.uuid4().hex)
        def decide(payload):
            outside.run(branch_change)
            return dict(action='approve')
        with self.host(uuid.uuid4().hex), self.assertRaisesRegex(ValueError, 'branch changed|Changed watched'):
            self.execute(incoming, saved, decide)
        self.assertFalse(chain._PENDING_REVIEWS)

    def test_real_prompt_executor_two_scene_review_and_final_delivery(self):
        result = execute_graph(self)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([index for index,seed in result['calls']], [1,2])
        self.assertEqual(len(result['gates']), 2)
        manifest = result['outputs'][0]
        self.assertEqual([s['index'] for s in manifest['segments']], [1,2])
        self.assertEqual(manifest['_project_ownership'], self.proof)
        self.assertEqual(manifest['_storage_pin']['root'], self.store.snapshot().reference)
        self.store.verify_payloads()

    def test_real_prompt_executor_retries_edited_scene_before_continuing(self):
        result = execute_graph(self, retry=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual(result['calls'], [(1,18446744073709551601), (1,13), (2,8)])
        self.assertEqual(len(result['gates']), 3)
        manifest = result['outputs'][0]
        self.assertEqual(manifest['segments'][0]['seed'], 13)
        self.assertIn('Executor retry é\n雪', manifest['segments'][0]['prompt'])
        self.assertEqual([s['index'] for s in manifest['segments']], [1,2])
        self.store.verify_payloads()

    def test_uncached_prompt_executor_does_not_repeat_reviews_or_saves(self):
        result = execute_graph(self, cache='none')
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual(len(result['gates']), 2)
        self.assertEqual([s['index'] for s in result['outputs'][0]['segments']], [1,2])
        self.store.verify_payloads()

    def test_named_branch_review_and_retry_do_not_change_main_or_its_inventory(self):
        branches = module('working_branches').WorkingBranches
        with generation.runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Review fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with generation.runtime.runtime_access(self.store, selected=branch['id']) as bound:
            plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        before = self.store.snapshot()
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(plan, 1)[1]
            saved = chain.MiniMaxH3ChainSegmentSave().save(incoming, self.frames,
                av_latent(.4), self.audio(1))['result'][0]
        with self.host(uuid.uuid4().hex):
            result = self.execute(incoming, saved, dict(action='retry', scene_prompt='Fork edit', seed=21, raw_frames=5))
            advanced = self.next_state(incoming, result['result'][0])
        self.assertEqual(advanced['plan']['_branch_id'], branch['id'])
        self.assertEqual(advanced['plan']['shots'][0]['seed'], 21)
        after = self.store.snapshot()
        for address in ('plan.json', 'checkpoints/clip_0001.json', 'checkpoints/clip_0002.json'):
            self.assertEqual(before.read(address), after.read(address))
        named = 'branches/'+branch['id']+'/orchestration/'
        self.assertTrue(any(address.startswith(named) for address in after.state['documents']))
        self.assertFalse(any(address.startswith('orchestration/review_') for address in after.state['documents']))


if __name__ == '__main__':
    unittest.main(argv=[__file__])
