"""Actual CPU encoder saves into a joined copy, with no resolver monkeypatches."""
import copy
import importlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from _upscale_chain_unit_test import load_package, folder_paths, torch, av_latent, audio_for_frames, legacy_cache_fixture

package, chain, _ = load_package()
def module(name):
    return importlib.import_module(package.__name__+'.'+name)

state = module('storage_state')
project = module('storage_project')
migration = module('storage_project_migration')
runtime = module('storage_runtime')
carriers = module('storage_carriers')
ownership = module('project_ownership')
contracts = module('storage_branch_controls').BranchControlDocuments
layout_type = module('storage_layout').OrganizedStorageLayout


class GenerationIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.lab = Path(temporary.name)
        self.run = 'generation_test'
        source_output = self.lab/'source-output'
        folder_paths.output_directory = str(source_output)
        self.plan = chain.MiniMaxH3ChainPlan().build(json.dumps({'shots':[
            {'id':'first', 'prompt':'Original precise prompt é\n雪', 'length':5, 'steps':2,
             'seed':'18446744073709551601'},
            {'id':'second', 'prompt':'Second prompt', 'length':5, 'steps':2, 'seed':'8'}]}),
            self.run, '', 32, 32, 1, 'video', 'head', 'disabled', 'generated_audio',
            1, 5/24, 2, 7, 18, 0, 'guide')[0]
        self.plan = chain._plan_with_source_audio(chain._plan_with_external_context(self.plan, None), None)
        self.frames = torch.zeros(5, 32, 32, 3)
        self.states = {}
        self.originals = []
        for index in (1, 2):
            self.states[index] = chain._initial_state(self.plan, index)
            self.originals.append(chain.MiniMaxH3ChainSegmentSave().save(
                self.states[index], self.frames[:self.plan['shots'][index-1]['delivered_frames']],
                av_latent(.1), self.audio(index), denoised_latent=av_latent(.3))['result'][0])
        # A real independent source copy, then the real importer/joiner; no
        # hand-built combined marker or bypass of migration/access validation.
        source = source_output/'h3_chains'/self.run
        copied = self.lab/'copy-output'/'h3_chains'/self.run
        shutil.copytree(source, copied, copy_function=shutil.copy2)
        self.source_files = {p.relative_to(source).as_posix(): p.read_bytes()
                             for p in source.rglob('*') if p.is_file()}
        self.source = source
        receipt = self.lab/'copy-receipt.json'
        state.atomic_json(receipt, {'copy':str(copied), 'source':str(source), 'independent_copies':True,
            'files':[dict(path=k, sha256=state._hash(v)) for k,v in self.source_files.items()]})
        self.output = self.lab/'combined-output'
        layout = layout_type(str(self.output/'h3_chains'/self.run))
        documents, targets = {}, {}
        for address, raw in self.source_files.items():
            if address.endswith(('.json', '.prompt.txt')):
                take = re.fullmatch(r'(?:checkpoints|segments)/clip_\d{4}\.([a-f0-9]{32})\.(?:json|prompt\.txt)', address)
                archive = re.fullmatch(r'recovery_archives/([a-f0-9]{32})/(plan|workflow|api_prompt)\.json', address)
                if take or archive:
                    scope, category, immutable = 'archive:'+(take or archive)[1], 'takes', True
                else:
                    scope, category, immutable = contracts._contract(address)
                documents[address] = dict(source=address, sha256=state._hash(raw), scope=scope,
                                          category=category, immutable=immutable)
        for segment in self.originals:
            paths = layout.media('generation', segment['revision'])
            paths['prompt'] = layout.take_prompt(segment['revision'])
            for key, role in [('segment','video'), ('checkpoint','checkpoint'),
                              ('generated_audio','audio')]:
                address = segment[key].split('h3_chains/'+self.run+'/')[1]
                targets[address] = dict(target=paths[role], scope='archive:'+segment['revision'], immutable=True)
        control = state.create_control_rehearsal(receipt, self.output, documents,
                                                 commit_protocol='immutable_slots_v1')
        self.access = state.control_rehearsal_access(control.project)
        self.access.__enter__()
        self.addCleanup(self.access.__exit__, None, None, None)
        journal = migration.prepare_join(receipt, control, self.lab/'join', targets)
        migration.join_payloads(journal)
        self.store = project.ProjectStore(control.project)
        folder_paths.output_directory = str(self.output)
        with runtime.runtime_access(self.store, ownership_writes=True):
            claimed = ownership.claim_project_ownership(self.output, self.run, 'cpu-generation-test-owner')
        self.proof = dict(owner_id='cpu-generation-test-owner', epoch=claimed['epoch'])
        self.plan['_project_ownership'] = self.proof

    def audio(self, index):
        value = audio_for_frames(self.plan['shots'][index-1]['delivered_frames'])
        value['waveform'].fill_(.2)
        return value

    def save(self, index=1, plan=None, sampled=None):
        plan = plan or self.plan
        value = {**self.states[index], 'plan':plan}
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            return chain.MiniMaxH3ChainSegmentSave().save(value,
                self.frames[:plan['shots'][index-1]['delivered_frames']],
                av_latent(.4) if sampled is None else sampled, self.audio(index),
                denoised_latent=av_latent(.6))

    def test_actual_encoder_preserves_precise_prompt_seed_audio_latents_and_preview(self):
        before = self.store.snapshot()
        original_state = copy.deepcopy(self.states[1]['plan'])
        result = self.save()
        segment = result['result'][0]
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertEqual(segment['_storage_pin']['root'], after.reference)
        self.assertEqual(self.states[1]['plan'], original_state)
        self.assertEqual(segment['seed'], 18446744073709551601)
        prefix = 'h3_chains/'+self.run+'/'
        def payload(key):
            return self.store.payload_path(after, segment[key].removeprefix(prefix), verify=True)
        self.assertEqual(payload('prompt_file').read_text(), self.plan['shots'][0]['prompt'])
        tensors = chain._st_load(str(payload('checkpoint')))
        self.assertTrue(torch.equal(tensors['video'], av_latent(.4)['samples'][0]))
        self.assertTrue(torch.equal(tensors['denoised_video'], av_latent(.6)['samples'][0]))
        self.assertTrue(torch.equal(tensors['delivered_audio'], self.audio(1)['waveform']))
        with chain.av.open(str(payload('segment'))) as video:
            self.assertEqual(len(list(video.decode(video=0))), 5)
        with chain.av.open(str(payload('generated_audio'))) as audio:
            self.assertGreater(sum(f.samples for f in audio.decode(audio=0)), 0)
        canonical = json.loads(after.read('checkpoints/clip_0001.json'))
        self.assertNotIn('_storage_pin', canonical['segment'])
        self.assertEqual(canonical['segment']['seed'], segment['seed'])
        self.assertEqual(before.read('checkpoints/clip_0002.json'), after.read('checkpoints/clip_0002.json'))
        preview = result['ui']['images'][0]
        self.assertEqual(self.output/preview['subfolder']/preview['filename'], payload('segment'))
        self.assertEqual(self.store.verify_payloads(), 10)
        self.assertFalse((self.store.project/'checkpoints').exists())
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})

    def test_actual_loop_start_resumes_exact_newly_saved_predecessor(self):
        saved = self.save()['result'][0]
        plan = dict(self.plan, _storage_pin=saved['_storage_pin'], _branch_id='main')
        before = self.store.snapshot().reference
        with carriers.node_host(self.store):
            flow, resumed, status = chain.MiniMaxH3ChainLoopStart().start(plan, 2)
        self.assertEqual(resumed['index'], 2)
        self.assertEqual(resumed['resumed_from'], 1)
        self.assertEqual(resumed['segments'][0]['revision'], saved['revision'])
        self.assertEqual(resumed['segments'][0]['seed'], 18446744073709551601)
        self.assertEqual(resumed['plan']['_storage_pin'], saved['_storage_pin'])
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertIn('resumed from clip 1', status)

    def save_loop_scene(self, scene_range=''):
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(self.plan, 1, scene_range=scene_range)[1]
            saved = chain.MiniMaxH3ChainSegmentSave().save(
                incoming, self.frames, av_latent(.4), self.audio(1))['result'][0]
        return incoming, saved

    def loop_host(self, *, write=False, handoff=False):
        return carriers.node_host(self.store,
            generation_writers=(chain.MiniMaxH3ChainLoopEnd.end,) if write else (),
            handoff_writers=(chain.MiniMaxH3ChainLoopEnd.end,) if handoff else (), input_adapters={
            chain.MiniMaxH3ChainLoopEnd.end: module('storage_continuation').loop_end_inputs})

    def expand_loop(self, incoming, saved, execution_mode='recursive', *, sampled_latent=None, images=None):
        from comfy_execution.graph import DynamicPrompt
        prompt = {
            'plan': {'class_type':'MiniMaxH3ChainPlan', 'inputs':{}},
            'start': {'class_type':'MiniMaxH3ChainLoopStart', 'inputs':{
                'plan':['plan', 0], 'start_clip':1}},
            'current': {'class_type':'MiniMaxH3ChainCurrent', 'inputs':{'state':['start', 1]}},
            'save': {'class_type':'MiniMaxH3ChainSegmentSave', 'inputs':{'state':['current', 0]}},
            'end': {'class_type':'MiniMaxH3ChainLoopEnd', 'inputs':{
                'flow':['start', 0], 'state':['current', 0], 'segment':['save', 0]}},
        }
        return chain.MiniMaxH3ChainLoopEnd().end(['start', 0], incoming, self.frames if images is None else images,
            av_latent(.4) if sampled_latent is None else sampled_latent, saved, dynprompt=DynamicPrompt(prompt), unique_id='end',
            execution_mode=execution_mode)

    def test_actual_recursive_end_advances_exact_save_and_binds_expanded_start_plan(self):
        incoming, saved = self.save_loop_scene()
        original_plan = copy.deepcopy(incoming['plan'])
        original_segment = copy.deepcopy(saved)
        original_frames = incoming['previous_frames']
        with self.loop_host():
            result = self.expand_loop(incoming, saved)
            start = next(v['inputs'] for v in result['expand'].values()
                         if v['class_type'] == 'MiniMaxH3ChainLoopStart')
            self.assertIsInstance(start['plan'], dict)
            next_state = chain.MiniMaxH3ChainLoopStart().start(**start)[1]
        self.assertEqual(next_state['index'], 2)
        self.assertEqual(next_state['plan']['_storage_pin'], saved['_storage_pin'])
        self.assertEqual(next_state['segments'][0]['revision'], saved['revision'])
        self.assertEqual(next_state['plan']['shots'][0]['seed'], 18446744073709551601)
        self.assertTrue(torch.equal(next_state['previous_latent']['samples'][0], av_latent(.4)['samples'][0]))
        self.assertTrue(torch.equal(next_state['previous_latent']['samples'][1], av_latent(.4)['samples'][1]))
        self.assertEqual(incoming['plan'], original_plan)
        self.assertIs(incoming['previous_frames'], original_frames)
        self.assertEqual(saved, original_segment)
        self.assertEqual(self.store.snapshot().reference, saved['_storage_pin']['root'])

    def test_loop_end_does_not_pick_later_unrelated_generation(self):
        incoming, saved = self.save_loop_scene()
        newer = self.save()['result'][0]
        with self.loop_host():
            expanded = self.expand_loop(incoming, saved)
        next_state = next(v['inputs']['initial_state'] for v in expanded['expand'].values()
                          if v['class_type'] == 'MiniMaxH3ChainLoopStart')
        self.assertEqual(next_state['segments'][0]['revision'], saved['revision'])
        self.assertEqual(next_state['plan']['_storage_pin'], saved['_storage_pin'])
        self.assertEqual(self.store.snapshot().reference, newer['_storage_pin']['root'])

    def test_loop_end_rejects_forged_receipt_and_changed_plan_range_or_lineage(self):
        incoming, saved = self.save_loop_scene()
        variants = []
        for field, value in [('end_clip', 1), ('range_start', 2), ('segments', self.originals[:1])]:
            variants.append((dict(incoming, **{field:value}), saved))
        for field, value in [('seed', 9), ('prompt', 'Changed after save')]:
            changed = dict(incoming, plan=copy.deepcopy(incoming['plan']))
            changed['plan']['shots'][0][field] = value
            variants.append((changed, saved))
        for field, value in [('seed', 9), ('revision', self.originals[0]['revision']),
                             ('_storage_pin', incoming['plan']['_storage_pin'])]:
            variants.append((incoming, dict(saved, **{field:value})))
        bad_receipt = copy.deepcopy(saved)
        bad_receipt['_storage_save']['receipt']['operation_id'] = uuid.uuid4().hex
        variants.append((incoming, bad_receipt))
        bad_witness = copy.deepcopy(saved)
        bad_witness['_storage_save']['witness'] = '../../other.json'
        variants.append((incoming, bad_witness))
        before = self.store.snapshot().reference
        with self.loop_host():
            for candidate_state, candidate_segment in variants:
                with self.subTest(state=candidate_state is incoming, revision=candidate_segment['revision']):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.expand_loop(candidate_state, candidate_segment)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_loop_end_needs_explicit_adapter_and_witness(self):
        incoming, saved = self.save_loop_scene()
        with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'mixed-root'):
            self.expand_loop(incoming, saved)
        incomplete = dict(saved)
        incomplete.pop('_storage_save')
        with self.loop_host(), self.assertRaisesRegex(ValueError, 'transition receipt'):
            self.expand_loop(incoming, incomplete)

    def test_loop_end_rejects_corrupt_saved_media_before_expanding(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        address = saved['checkpoint'].split('h3_chains/'+self.run+'/')[1]
        artifact = self.store.payload_path(before, address, verify=True)
        with artifact.open('r+b') as handle:
            handle.write(b'corrupt!')
        with self.loop_host(), self.assertRaisesRegex(ValueError, 'checksum|SHA-256'):
            self.expand_loop(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_loop_end_after_force_ownership_cannot_continue_the_old_job(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, ownership_writes=True):
            ownership.claim_project_ownership(self.output, self.run, 'private-new-loop-owner', force=True)
        with self.loop_host(), self.assertRaises(ValueError):
            self.expand_loop(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_loop_end_advances_matching_outer_state_pin_without_mutating_it(self):
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            incoming = chain.MiniMaxH3ChainLoopStart().start(self.plan, 1)[1]
            incoming['_storage_pin'] = copy.deepcopy(incoming['plan']['_storage_pin'])
            saved = chain.MiniMaxH3ChainSegmentSave().save(
                incoming, self.frames, av_latent(.4), self.audio(1))['result'][0]
        before = copy.deepcopy(incoming['_storage_pin'])
        with self.loop_host():
            self.expand_loop(incoming, saved)
        self.assertEqual(incoming['_storage_pin'], before)
        corrupted = dict(incoming, _storage_pin=saved['_storage_pin'])
        with self.loop_host(), self.assertRaisesRegex(ValueError, 'envelope'):
            self.expand_loop(corrupted, saved)

    def test_recursive_start_rejects_same_pin_but_different_prepared_plan(self):
        incoming, saved = self.save_loop_scene()
        with self.loop_host():
            expanded = self.expand_loop(incoming, saved)
            start = next(v['inputs'] for v in expanded['expand'].values()
                         if v['class_type'] == 'MiniMaxH3ChainLoopStart')
            start['plan'] = copy.deepcopy(start['plan'])
            start['plan']['shots'][0]['seed'] = 99
            with self.assertRaisesRegex(ValueError, 'differs from its continuation'):
                chain.MiniMaxH3ChainLoopStart().start(**start)

    def test_actual_partial_loop_end_publishes_frozen_delivery_without_legacy_paths(self):
        incoming, saved = self.save_loop_scene(scene_range='1')
        before = self.store.snapshot()
        with self.loop_host(write=True):
            manifest, raw, _, _ = self.expand_loop(incoming, saved)
        after = self.store.snapshot()
        pointer = json.loads(after.read('deliveries/main/latest.json'))
        stored = json.loads(after.read(pointer['manifest']))
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertEqual(stored['segments'][0]['revision'], saved['revision'])
        self.assertEqual(stored['_storage_pin'], saved['_storage_pin'])
        self.assertEqual(manifest['_storage_pin']['root'], after.reference)
        self.assertEqual(json.loads(raw), manifest)
        self.assertFalse(pointer['complete'])
        self.assertEqual((manifest['clip_count'], manifest['planned_clip_count']), (1, 2))
        self.assertEqual(pointer['through_scene'], 1)
        old = before.state['documents']
        new = after.state['documents']
        self.assertTrue(all(new[k] == v for k,v in old.items()))
        self.assertFalse((self.store.project/'partial').exists())
        self.assertFalse((self.store.project/'manifest.json').exists())
        self.assertEqual(self.store.verify_payloads(), 10)

    def test_actual_partial_end_retry_returns_same_accepted_delivery(self):
        incoming, saved = self.save_loop_scene(scene_range='1')
        with self.loop_host(write=True):
            first = self.expand_loop(incoming, saved)[0]
            before = self.store.snapshot().reference
            second = self.expand_loop(incoming, saved)[0]
        self.assertEqual(first, second)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_actual_complete_end_uses_both_new_recursive_checkpoints(self):
        incoming, first = self.save_loop_scene()
        with self.loop_host():
            expanded = self.expand_loop(incoming, first)
            start = next(v['inputs'] for v in expanded['expand'].values()
                         if v['class_type'] == 'MiniMaxH3ChainLoopStart')
            current = chain.MiniMaxH3ChainLoopStart().start(**start)[1]
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
            second = chain.MiniMaxH3ChainSegmentSave().save(current,
                self.frames[:self.plan['shots'][1]['delivered_frames']], av_latent(.4), self.audio(2))['result'][0]
        before = self.store.snapshot()
        with self.loop_host(write=True):
            manifest = self.expand_loop(current, second)[0]
        after = self.store.snapshot()
        pointer = json.loads(after.read('deliveries/main/latest.json'))
        self.assertTrue(pointer['complete'])
        self.assertEqual(pointer['through_scene'], 2)
        self.assertEqual(manifest['format'], 'h3_chain_manifest_v3')
        self.assertEqual([s['revision'] for s in manifest['segments']], [first['revision'], second['revision']])
        self.assertEqual(manifest['total_delivered_frames'], self.plan['total_delivered_frames'])
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertEqual(after.read('checkpoints/clip_0001.json'), before.read('checkpoints/clip_0001.json'))
        self.assertEqual(after.read('checkpoints/clip_0002.json'), before.read('checkpoints/clip_0002.json'))

    def test_delivery_requires_own_node_grant_and_rejects_later_branch_changes(self):
        incoming, saved = self.save_loop_scene(scene_range='1')
        with self.loop_host(), self.assertRaisesRegex(ValueError, 'generation writes'):
            self.expand_loop(incoming, saved)
        newer = self.save()['result'][0]
        with self.loop_host(write=True), self.assertRaises(ValueError):
            self.expand_loop(incoming, saved)
        self.assertEqual(self.store.snapshot().reference, newer['_storage_pin']['root'])

    def test_manifest_load_can_inspect_readonly_and_publish_owned_copy(self):
        before = self.store.snapshot()
        with carriers.node_host(self.store):
            manifest, raw, status = chain.MiniMaxH3ChainManifestLoad().load(self.plan)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertIn('read-only storage session', status)
        self.assertEqual(manifest['clip_count'], 2)
        self.assertEqual(json.loads(raw), manifest)
        with carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainManifestLoad.load,)):
            written, raw, status = chain.MiniMaxH3ChainManifestLoad().load(self.plan)
        self.assertNotIn('read-only', status)
        self.assertEqual(json.loads(raw), written)
        self.assertEqual(written['segments'], manifest['segments'])
        self.assertEqual(self.store.snapshot().state['generation'], before.state['generation']+1)

    def test_requeue_manifest_and_handoff_are_one_publication_and_retry_does_not_reset_claim(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        with self.loop_host(write=True, handoff=True):
            result = self.expand_loop(incoming, saved, 'top_level_requeue')
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        completion = result['ui']['h3_chain_top_level_requeue'][0]
        path = 'orchestration/'+completion['handoff_id']+'.json'
        handoff = json.loads(after.read(path))
        self.assertEqual(handoff['status'], 'pending')
        self.assertEqual(handoff['source_revision'], saved['revision'])
        self.assertEqual(handoff['scene'], 2)
        self.assertEqual(handoff['seed'], self.plan['shots'][1]['seed'])
        self.assertEqual(handoff['storage_dependency']['revision'], after.state['scope_revisions']['branch:main'])
        self.assertEqual(json.loads(result['result'][1]), result['result'][0])
        self.assertEqual(result['result'][0]['_storage_pin']['root'], after.reference)
        self.assertIn('deliveries/main/latest.json', after.state['documents'])
        with runtime.runtime_access(self.store, handoff_writes=True):
            module('handoff_state').HandoffStore(self.output).claim(self.run, handoff['handoff_id'])
        claimed = self.store.snapshot()
        with self.loop_host(write=True, handoff=True):
            repeated = self.expand_loop(incoming, saved, 'top_level_requeue')
        self.assertEqual(repeated['ui'], result['ui'])
        self.assertEqual(self.store.snapshot().reference, claimed.reference)
        self.assertEqual(json.loads(claimed.read(path))['status'], 'claimed')

    def test_requeue_requires_both_grants_before_any_publication(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot().reference
        for grants in ({'write':True}, {'handoff':True}):
            with self.loop_host(**grants), self.assertRaisesRegex(ValueError, 'writes'):
                self.expand_loop(incoming, saved, 'top_level_requeue')
        self.assertEqual(self.store.snapshot().reference, before)

    def test_requeue_failure_does_not_leave_a_pending_handoff_or_partial_delivery(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        delivery_type = module('storage_delivery').RuntimeDelivery
        publish = delivery_type.publish
        def failing(service, *args, **kwargs):
            def stop(stage):
                if stage == 'document':
                    raise OSError('interrupted combined delivery')
            service.after_stage = stop
            return publish(service, *args, **kwargs)
        with self.loop_host(write=True, handoff=True), patch.object(delivery_type, 'publish', failing):
            with self.assertRaisesRegex(OSError, 'interrupted combined delivery'):
                self.expand_loop(incoming, saved, 'top_level_requeue')
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertFalse(any(k.startswith(('deliveries/', 'orchestration/')) for k in before.state['documents']))
        with self.loop_host(write=True, handoff=True):
            self.expand_loop(incoming, saved, 'top_level_requeue')
        self.assertEqual(self.store.snapshot().state['generation'], before.state['generation']+1)

    def test_requeue_lost_ack_retry_acknowledges_the_original_batch(self):
        incoming, saved = self.save_loop_scene()
        before = self.store.snapshot()
        publish = self.store._publish
        def lost(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost combined delivery acknowledgement')
        with self.loop_host(write=True, handoff=True), patch.object(self.store, '_publish', lost):
            with self.assertRaisesRegex(OSError, 'lost combined delivery acknowledgement'):
                self.expand_loop(incoming, saved, 'top_level_requeue')
        accepted = self.store.snapshot()
        self.assertEqual(accepted.state['generation'], before.state['generation']+1)
        with self.loop_host(write=True, handoff=True):
            result = self.expand_loop(incoming, saved, 'top_level_requeue')
        self.assertEqual(self.store.snapshot().reference, accepted.reference)
        self.assertEqual(result['result'][0]['_storage_pin']['root'], accepted.reference)

    def continuation_plan(self, pin):
        plan = copy.deepcopy(self.plan)
        plan.update(_storage_pin=pin, _branch_id='main')
        shot = plan['shots'][1]
        shot.pop('visual_context_blocks', None)
        shot.update(visual_context_source=1, context_length=1,
                    audio_context_unlocked=True, audio_context_source=1,
                    generated_continuity='on', audio_context_length=1)
        return plan

    def test_resume_context_uses_pinned_saved_tensors_not_latest_take(self):
        first = self.save()['result'][0]
        plan = self.continuation_plan(first['_storage_pin'])
        with carriers.node_host(self.store):
            original = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        before = copy.deepcopy(plan)
        self.frames.fill_(.25)
        later = self.save()['result'][0]
        with carriers.node_host(self.store):
            historical = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        self.assertEqual(historical['segments'][0]['revision'], first['revision'])
        self.assertNotEqual(first['revision'], later['revision'])
        self.assertTrue(torch.equal(historical['previous_frames'], original['previous_frames']))
        self.assertTrue(torch.equal(historical['previous_latent']['samples'][0], av_latent(.4)['samples'][0]))
        self.assertTrue(torch.equal(historical['previous_latent']['samples'][1], av_latent(.4)['samples'][1]))
        self.assertEqual(plan, before)
        self.assertEqual(self.store.snapshot().reference, later['_storage_pin']['root'])

    def test_resume_changed_prompt_or_seed_is_not_silently_accepted(self):
        saved = self.save()['result'][0]
        for key, value in [('prompt', 'Wrong prompt'), ('seed', 9)]:
            plan = self.continuation_plan(saved['_storage_pin'])
            plan = chain._plan_with_review_revision(plan, 1,
                value if key == 'prompt' else plan['shots'][0]['scene_prompt'],
                value if key == 'seed' else plan['shots'][0]['seed'])
            with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'mismatch'):
                chain.MiniMaxH3ChainLoopStart().start(plan, 2)
        self.assertEqual(saved['_storage_pin']['root'], self.store.snapshot().reference)

    def test_resume_corrupt_checkpoint_still_fails_with_history_check_disabled(self):
        saved = self.save()['result'][0]
        plan = self.continuation_plan(saved['_storage_pin'])
        address = saved['checkpoint'].split('h3_chains/'+self.run+'/')[1]
        artifact = self.store.payload_path(self.store.snapshot(), address, verify=True)
        with artifact.open('r+b') as handle:
            handle.write(b'corrupt!')
        with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'SHA-256'):
            chain.MiniMaxH3ChainLoopStart().start(plan, 2, verify_resume_history=False)

    def test_named_branch_resume_cannot_pick_a_new_main_predecessor(self):
        first = self.save()['result'][0]
        branches_type = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True) as bound:
            branch = branches_type(self.output, self.run).create('main', 'Resume fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        later = self.save()['result'][0]
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            plan = self.continuation_plan(bound.pin)
        plan['_branch_id'] = branch['id']
        with carriers.node_host(self.store):
            resumed = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        self.assertEqual(resumed['segments'][0]['revision'], first['revision'])
        self.assertNotEqual(resumed['segments'][0]['revision'], later['revision'])
        self.assertEqual(resumed['plan']['_branch_id'], branch['id'])
        self.assertEqual(self.store.snapshot().reference, later['_storage_pin']['root'])

    def test_visual_and_independent_audio_context_read_the_accepted_checkpoint(self):
        sampled = av_latent(.4)
        sampled['samples'][1] = sampled['samples'][1][...,:8]
        first = self.save(sampled=sampled)['result'][0]
        plan = self.continuation_plan(first['_storage_pin'])
        plan['shots'][1].pop('visual_context_source', None)
        plan['shots'][1]['visual_context_blocks'] = [{'source':1, 'frames':1, 'start_frame':4}]
        with carriers.node_host(self.store):
            resumed = chain.MiniMaxH3ChainLoopStart().start(plan, 2)[1]
        with runtime.runtime_access(self.store, pin=first['_storage_pin']):
            composed = chain._selected_context_state(resumed)
        self.assertEqual(composed['_visual_context_source'], 1)
        self.assertEqual(composed['_audio_context_source'], 1)
        self.assertTrue(torch.equal(composed['previous_latent']['samples'][0], av_latent(.4)['samples'][0][:,:,-1:]))
        self.assertEqual(tuple(composed['previous_frames'].shape), (1,32,32,3))

    def test_actual_source_timeline_recovers_mirrored_video_and_audio_without_identity_change(self):
        saved = self.save()['result'][0]
        base = self.store.snapshot()
        sources, stages, assets = {}, [], []
        for field, group, filename in (('segment','videos','source.mp4'),
                                       ('generated_audio','audio','source.wav')):
            source = self.store.payload_path(base,saved[field].split('h3_chains/'+self.run+'/')[1],verify=True)
            sources[field] = str(source)
            token = uuid.uuid4().hex
            address = 'project_assets/'+group+'/'+filename
            stages.append(self.store.stage_payload(address,source,'project/assets/'+token+'/'+filename,
                scope='archive:'+token,operation_id=token))
            assets.append(dict(relative_path=group+'/'+filename,sha256=saved[field+'_sha256'],size=source.stat().st_size))
        catalogue = dict(format='h3_project_assets_v1',project=self.run,assets=assets)
        self.store.commit_artifacts(base,{'project_assets/catalog.json':dict(data=state._encode(catalogue),
            scope='project',category='assets',immutable=False)},stages,operation_id=uuid.uuid4().hex)
        original = chain.MiniMaxH3SourceTimeline().build(sources['segment'],sources['generated_audio'],'ignore',0)[0]
        expected_audio = chain._source_timeline_source_audio(original)
        expected_video = chain._source_timeline_scene_video(original,0,5)
        record = chain._source_timeline_recovery_record(original)
        record['video']['path'] = '/old-host/input/source.mp4'
        record['audio']['path'] = '/old-host/input/source.wav'
        before = copy.deepcopy(record)
        root = self.store.snapshot().reference
        with runtime.runtime_access(self.store):
            recovered = chain._source_timeline_from_recovery(record)
            audio = chain._source_timeline_source_audio(recovered)
            video = chain._source_timeline_scene_video(recovered,0,5)
        self.assertEqual(record,before)
        self.assertEqual(original['fingerprints'],recovered['fingerprints'])
        self.assertIn('/project/assets/',recovered['audio']['path'])
        self.assertIn('/project/assets/',recovered['video']['path'])
        self.assertEqual(tuple(video.shape),(5,32,32,3))
        self.assertTrue(torch.equal(video,expected_video))
        self.assertTrue(torch.equal(audio['waveform'],expected_audio['waveform']))
        self.assertEqual(root,self.store.snapshot().reference)

    def test_actual_alt_encoder_does_not_replace_base_or_authored_settings(self):
        base = self.originals[0]
        with runtime.runtime_access(self.store):
            alternate = chain._alternate_take_plan(self.plan, {'alternate_draft':{
                'enabled':True, 'scene':1, 'scene_id':'first', 'base_revision':base['revision'],
                'prompt':'Exact alternate é 雪', 'seed':18446744073709551603}})
        before = self.store.snapshot()
        saved = self.save(plan=alternate)['result'][0]
        after = self.store.snapshot()
        for address in before.state['documents']:
            self.assertEqual(after.read(address), before.read(address), address)
        self.assertEqual(saved['seed'], 18446744073709551603)
        self.assertEqual(saved['alternate_of_revision'], base['revision'])
        self.assertIn('/media/alternate/', str(self.store.payload_path(after,
            saved['segment'].split('h3_chains/'+self.run+'/')[1], verify=True)))

    def test_encoder_failure_and_invalid_reference_cannot_publish_incomplete_scene(self):
        before = self.store.snapshot().reference
        with patch.object(chain, '_st_save', side_effect=OSError('encoder failed')):
            with self.assertRaisesRegex(OSError, 'encoder failed'):
                self.save()
        self.assertEqual(before, self.store.snapshot().reference)
        with patch.object(chain, '_find_reference_cache', return_value={'found':'reference'}):
            with self.assertRaisesRegex(ValueError, 'reference cache format'):
                self.save()
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.store.verify_payloads(), 6)

    def reference(self, version=None):
        class VAE:
            def encode(self, images):
                return torch.ones((1, 24, 1, 2, 2))
        fingerprint = 'reference-integration-fixture'
        self.plan['compatibility']['generation_fingerprint'] = fingerprint
        chain._cache_reference_scene(fingerprint=fingerprint, scene=1, scene_count=2,
            prompt=self.plan['shots'][0]['prompt'], compiled_prompt='<Picture 1> precise',
            width=32, height=32, length=5, ref_image_size='match', vae=VAE(), audio_vae=None,
            pictures=[torch.ones((1,64,64,3))], videos=[], audios=[])
        metadata = chain._find_reference_cache(fingerprint, 1, 2, self.plan['shots'][0]['prompt'], 32,32,5)
        return legacy_cache_fixture(chain, metadata, version=version) if version else metadata

    def test_v3_v2_v1_references_are_independent_exact_readable_and_reused(self):
        for version in (None, 'h3_reference_cache_v2', 'h3_reference_cache_v1'):
            with self.subTest(version=version):
                source = self.reference(version)
                exact = chain._reference_cache_tensors(source)
                with patch.object(chain, '_find_reference_cache', return_value=source):
                    saved = self.save()['result'][0]
                accepted = self.store.snapshot()
                with runtime.runtime_access(self.store):
                    local = chain._load_reference_cache_descriptor(saved['reference_cache'])
                    actual = chain._reference_cache_tensors(local)
                    self.assertEqual(set(actual), set(exact))
                    for key in exact:
                        self.assertTrue(torch.equal(exact[key], actual[key]), key)
                    self.assertEqual(chain._load_run_reference_cache_descriptor(
                        self.run, 1, chain._reference_cache_descriptor(source)), local)
                    source_paths = ([row['tensors'] for row in source['tensor_objects'].values()]
                                    if version is None else [source['tensors']])
                    local_paths = ([row['tensors'] for row in local['tensor_objects'].values()]
                                   if version is None else [local['tensors']])
                    for src, dst in zip(source_paths, local_paths):
                        original = self.output/src
                        copied = self.store.payload_path(accepted,
                            dst.split('h3_chains/'+self.run+'/')[1], verify=True)
                        self.assertNotEqual(original.stat().st_ino, copied.stat().st_ino)
                        self.assertEqual(original.read_bytes(), copied.read_bytes())
                before_count = self.store.verify_payloads()
                with patch.object(chain, '_find_reference_cache', return_value=source):
                    repeated = self.save()['result'][0]
                self.assertEqual(repeated['reference_cache'], saved['reference_cache'])
                self.assertEqual(self.store.verify_payloads(), before_count+4)

    def test_corrupt_reference_prevents_scene_and_cache_acceptance_together(self):
        source = self.reference()
        before = self.store.snapshot().reference
        object_path = self.output/next(iter(source['tensor_objects'].values()))['tensors']
        object_path.write_bytes(b'corrupted test-only reference')
        with patch.object(chain, '_find_reference_cache', return_value=source):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                self.save()
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.store.verify_payloads(), 6)

    def test_execution_pin_is_not_persisted_as_recovery_authoring(self):
        with runtime.runtime_access(self.store) as bound:
            self.plan['_storage_pin'] = bound.pin
        incoming_pin = copy.deepcopy(self.plan['_storage_pin'])
        saved = self.save()['result'][0]
        accepted = self.store.snapshot()
        archive = json.loads(accepted.read(saved['archives']['plan'].split('h3_chains/'+self.run+'/')[1]))
        self.assertNotIn('_storage_pin', archive)
        self.assertNotIn('_project_ownership', archive)
        self.assertEqual(self.plan['_storage_pin'], incoming_pin)

    def test_next_scene_can_save_against_the_new_predecessor(self):
        first = self.save()['result'][0]
        self.states[2]['segments'] = [{key:value for key,value in first.items()
                                      if key not in ('_storage_pin','run_name','_branch_id')}]
        second = self.save(index=2)['result'][0]
        self.assertEqual(second['predecessor_revision'], first['revision'])
        self.assertEqual(second['predecessor_checkpoint_sha256'], first['checkpoint_sha256'])
        self.assertEqual(self.store.verify_payloads(), 14)


if __name__ == '__main__':
    unittest.main(argv=['generation-integration-tests'], verbosity=2)
