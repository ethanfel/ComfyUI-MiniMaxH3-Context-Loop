"""Real migrated generation/ALT and processing reads, with no resolver patches."""
import copy
import importlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import uuid

import _upscale_alternate_unit_test as fixture

chain, upscale, torch = fixture.chain, fixture.upscale, fixture.torch
def module(name):
    return importlib.import_module(fixture.package.__name__+'.'+name)

state, project, migration, runtime, carriers = (module(name) for name in
    ('storage_state', 'storage_project', 'storage_project_migration',
     'storage_runtime', 'storage_carriers'))


class ProcessingReadTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AlternateUpscaleTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.run = self.f.run
        self.legacy_output = self.f.root
        self.source = self.legacy_output/'h3_chains'/self.run
        # Real legacy encoder saves provide the migration/resume fixture.
        _, incoming, self.source_manifest, _ = self.f.adapt(profile='pixels', backend='pixel')
        self.processed = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            incoming, self.f.frames)['result'][0]
        self.profile_paths = upscale._state_profile_paths(incoming, 1)
        self.expected_resume = self.f.adapt(profile='pixels', backend='pixel', start=2)[1]
        self.direct_alt = self.f.direct_alternate_manifest()
        self.derope = {}
        for name, joint in (('recovered-video', False), ('recovered-av', True)):
            _, derope_state, _, _ = self.f.adapt(profile=name, recipe='{"derope":true}', save_latent=True)
            latent = fixture.av_latent(.6) if joint else {'samples':fixture.av_latent(.6)['samples'][0]}
            audio = fixture.audio_for_frames(5)
            audio['waveform'].fill_(.5)
            child = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(derope_state, self.f.frames,
                upscaled_latent=latent, recovered_audio=audio if joint else None)['result'][0]
            metadata = chain._read_json(str(self.legacy_output/child['revision_metadata']))
            self.derope[name] = dict(stage='derope',
                profile_path=str(Path(child['revision_metadata']).parent.parent),
                branch=dict(kind='metadata', path=child['revision_metadata'], lineage=metadata['processing_lineage']))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.lab = Path(temporary.name)
        copied = self.lab/'copy-output'/'h3_chains'/self.run
        shutil.copytree(self.source, copied, copy_function=shutil.copy2)
        self.before_files = {p.relative_to(self.source).as_posix(): p.read_bytes()
                             for p in self.source.rglob('*') if p.is_file()}
        receipt = self.lab/'copy-receipt.json'
        state.atomic_json(receipt, dict(copy=str(copied), source=str(self.source), independent_copies=True,
            files=[dict(path=p, sha256=state._hash(raw)) for p, raw in self.before_files.items()]))
        self.output = self.lab/'combined-output'
        layout = module('storage_layout').OrganizedStorageLayout(str(self.output/'h3_chains'/self.run))
        contracts = module('storage_branch_controls').BranchControlDocuments
        documents, targets = {}, {}
        prefix = 'h3_chains/'+self.run+'/'
        for address, raw in self.before_files.items():
            if address.endswith(('.json', '.txt')):
                take = re.fullmatch(r'(?:checkpoints|segments)/clip_\d{4}\.([a-f0-9]{32})\.(?:json|prompt\.txt)', address)
                archive = re.fullmatch(r'recovery_archives/([a-f0-9]{32})/(plan|workflow|api_prompt)\.json', address)
                if address.startswith('upscaled/'):
                    scope, category = 'pass:'+state._hash(address.split('/')[1].encode())[:32], 'passes'
                    immutable = bool(re.search(r'clip_\d{4}\.[a-f0-9]{32}\.json$', address))
                elif address.startswith('alternates/'):
                    scope, category, immutable = 'exports:main', 'cuts', True
                elif take or archive:
                    scope, category, immutable = 'archive:'+(take or archive)[1], 'takes', True
                else:
                    scope, category, immutable = contracts._contract(address)
                documents[address] = dict(source=address, sha256=state._hash(raw), scope=scope,
                                          category=category, immutable=immutable)
            if address.endswith('.json'):
                document = json.loads(raw)
                segment = document.get('segment', {}) if isinstance(document, dict) else {}
                if not segment.get('revision'):
                    continue
                processing = document.get('format') == 'h3_chain_upscale_segment_v1'
                stage = module('storage_layout').storage_stage(
                    profile_config=document['profile_config']) if processing else module('storage_layout').storage_stage(
                    take_kind=segment.get('take_kind'))
                storage_id = uuid.uuid5(uuid.NAMESPACE_URL, segment['revision_metadata']).hex
                paths = layout.media(stage, storage_id, **({'pass_id':'a'*32} if processing else {}))
                paths['prompt'] = layout.take_prompt(storage_id)
                for key, role in (('segment','video'), ('checkpoint','checkpoint'),
                                  ('generated_audio','audio'), ('prompt_file','prompt')):
                    if segment.get(key):
                        logical = segment[key].removeprefix(prefix)
                        # Imported generation prompts are already exact controls.
                        if logical.endswith('.txt'):
                            continue
                        targets[logical] = dict(target=paths[role], scope='archive:'+segment['revision'], immutable=True)
        control = state.create_control_rehearsal(receipt, self.output, documents, commit_protocol='immutable_slots_v1')
        access = state.control_rehearsal_access(control.project)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        migration.join_payloads(migration.prepare_join(receipt, control, self.lab/'join', targets))
        self.store = project.ProjectStore(control.project)
        fixture.folder_paths.output_directory = str(self.output)
        self.before = self.store.snapshot()

    def adapt(self, source=None, **options):
        with carriers.node_host(self.store):
            return upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
                source or self.f.manifest, options.get('profile', 'new-pixels'),
                options.get('backend', 'pixel'), '{}', options.get('start', 1),
                options.get('stop', 0), False, 18, start_mode=options.get('mode', 'resume'))

    def unchanged(self):
        self.assertEqual(self.store.snapshot().reference, self.before.reference)
        self.assertEqual(self.before_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})
        self.assertFalse((self.store.project/'upscaled').exists())

    def test_actual_adapter_and_current_preserve_alt_picture_base_audio_seed_prompt(self):
        before = copy.deepcopy(self.f.manifest)
        _, incoming, source, status = self.adapt()
        with carriers.node_host(self.store):
            current = upscale.MiniMaxH3ChainUpscaleCurrent().current(incoming)
        self.assertIn('final-cut ALT pictures: 1/', status)
        self.assertEqual(source['segments'][0]['revision'], self.f.alt['revision'])
        self.assertTrue(torch.all(current[2]['samples'] == .9))
        self.assertTrue(torch.all(current[3]['samples'] == .3))
        self.assertTrue(torch.all(current[13]['waveform'] == .2))
        self.assertEqual((current[6], current[9]), (self.f.alt['prompt'], self.f.alt['seed']))
        self.assertEqual(self.f.manifest, before)
        self.unchanged()

    def test_actual_resume_loads_migrated_profile_not_a_legacy_folder(self):
        _, incoming, source, _ = self.adapt(profile='pixels', start=2)
        self.assertEqual(incoming['segments'], self.expected_resume['segments'])
        self.assertEqual(incoming['segments'][0]['revision'], self.processed['revision'])
        with carriers.node_host(self.store):
            current = upscale.MiniMaxH3ChainUpscaleCurrent().current(incoming)
        self.assertEqual(current[4], 2)
        self.assertEqual(current[6], self.f.bases[1]['prompt'])
        self.unchanged()

    def test_direct_alt_loop_end_manifest_uses_original_audio_after_migration(self):
        _, incoming, resolved, _ = self.adapt(self.direct_alt)
        with carriers.node_host(self.store):
            current = upscale.MiniMaxH3ChainUpscaleCurrent().current(incoming)
        self.assertEqual(resolved['segments'][0]['revision'], self.f.alt['revision'])
        self.assertTrue(torch.all(current[2]['samples'] == .9))
        self.assertTrue(torch.all(current[3]['samples'] == .3))
        self.assertTrue(torch.all(current[13]['waveform'] == .2))
        self.unchanged()

    def test_fresh_range_keeps_global_scene_numbers_without_requiring_prior_hq(self):
        _, incoming, source, _ = self.adapt(start=2, mode='fresh_range')
        self.assertEqual([s['index'] for s in source['segments']], [2])
        self.assertEqual(incoming['segments'], [])
        with carriers.node_host(self.store):
            current = upscale.MiniMaxH3ChainUpscaleCurrent().current(incoming)
        self.assertEqual((current[4], current[11]), (2, self.f.bases[1]['raw_frames']))
        with self.assertRaisesRegex(FileNotFoundError, 'fresh_range'):
            self.adapt(start=2)
        self.unchanged()

    def test_missing_changed_config_or_corrupt_prefix_fails_without_fallback(self):
        with self.assertRaisesRegex(ValueError, 'profile settings'):
            self.adapt(profile='pixels', start=2, backend='h3_latent')
        address = self.processed['checkpoint'].split('h3_chains/'+self.run+'/')[1]
        path = self.store.payload_path(self.before, address)
        path.write_bytes(b'corrupt copied checkpoint')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.adapt(profile='pixels', start=2)
        self.assertEqual(self.store.snapshot().reference, self.before.reference)

    def test_reader_does_not_enable_processing_save(self):
        _, incoming, _, _ = self.adapt()
        with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'processing writes'):
            upscale.MiniMaxH3ChainUpscaleSegmentSave().save(incoming, self.f.frames)
        self.unchanged()

    def test_continuation_rejects_an_empty_unwitnessed_prefix_before_writing(self):
        _, incoming, _, _ = self.adapt()
        empty = dict(incoming, index=1, end_clip=0, segments=[])
        with runtime.runtime_access(self.store), self.assertRaisesRegex(ValueError, 'verified processing save'):
            upscale.MiniMaxH3ChainUpscaleLoopEnd()._advance(None, empty)
        self.unchanged()

    def test_named_branch_cannot_borrow_main_profile_prefix(self):
        with runtime.runtime_access(self.store, branch_writes=True):
            named = chain.WorkingBranches(self.output, self.run).create('main', 'Named',
                {'plan_json':json.dumps({'shots':[{'id':s['id']} for s in self.f.plan['shots']]})},
                through_scene=2)['id']
        self.before = self.store.snapshot()
        source = dict(self.f.manifest, _branch_id=named)
        with self.assertRaisesRegex(FileNotFoundError, 'metadata is missing'):
            self.adapt(source, profile='pixels', start=2)
        _, incoming, _, _ = self.adapt(source, profile='pixels', start=2, mode='fresh_range')
        self.assertEqual(incoming['_storage_pin']['branch_id'], named)
        self.assertEqual(incoming['segments'], [])
        self.unchanged()

    def test_historical_pin_does_not_follow_changed_profile_metadata(self):
        _, old, pinned, _ = self.adapt(profile='pixels', start=2)
        address = self.processed['metadata'].split('h3_chains/'+self.run+'/')[1]
        changed = json.loads(self.before.read(address))
        changed['segment']['seed'] += 1
        contract = self.before.state['documents'][address]
        self.store.commit(self.before, {address:dict(data=state._encode(changed),
            scope=contract['scope'], category='passes', immutable=False)}, operation_id=uuid.uuid4().hex)
        self.before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'source timing or settings'):
            self.adapt(profile='pixels', start=2)
        _, replayed, _, _ = self.adapt(pinned, profile='pixels', start=2)
        self.assertEqual(old['segments'], replayed['segments'])
        self.unchanged()

    def test_pixel_decode_uses_same_alt_and_chapter_paths_stay_logical(self):
        chapter = dict(self.f.manifest, chapter={
            'number':1, 'id':'chapter', 'title':'Chapter', 'start_scene':1,
            'end_scene':2, 'planned_end_scene':2, 'source_start_frame':0},
            scene_start=1, scene_end=2)
        _, incoming, _, _ = self.adapt(chapter)
        test = self
        class VideoVAE:
            def decode(self, video):
                test.assertTrue(torch.all(video == .9))
                return test.f.frames
        with carriers.node_host(self.store):
            decoded = upscale.MiniMaxH3ChainUpscalePixelCurrent().current(incoming, VideoVAE())
        self.assertEqual(tuple(decoded[1].shape), (5,32,32,3))
        self.assertTrue(torch.all(decoded[2]['waveform'] == .2))
        self.assertFalse((self.store.project/'chapters').exists())
        self.unchanged()

    def test_migrated_derope_source_recovers_full_video_and_correct_audio_route(self):
        for name, joint in (('recovered-video', False), ('recovered-av', True)):
            with self.subTest(name=name):
                with runtime.runtime_access(self.store):
                    source = module('deferred_checkpoint_source').derope_source_manifest(
                        self.f.manifest, self.derope[name], chain, upscale)
                _, incoming, _, _ = self.adapt(source)
                with carriers.node_host(self.store):
                    current = upscale.MiniMaxH3ChainUpscaleCurrent().current(incoming)
                self.assertTrue(torch.all(current[2]['samples'] == .6))
                self.assertTrue(torch.all(current[3]['samples'] == (.6 if joint else .3)))
                self.assertTrue(torch.all(current[13]['waveform'] == (.5 if joint else .2)))
                self.assertEqual(current[6], self.f.alt['prompt'])
                # The absent second DeRoPE scene uses its selected original.
                self.assertEqual(source['segments'][1]['revision'], self.f.bases[1]['revision'])
                with self.assertRaisesRegex(ValueError, 'new output profile'):
                    self.adapt(source, profile=name)
        self.unchanged()

    def test_derope_branch_witness_or_profile_escape_fails_closed(self):
        changed = copy.deepcopy(self.derope['recovered-video'])
        changed['branch']['lineage'][0]['revision'] = '0'*32
        with runtime.runtime_access(self.store), self.assertRaisesRegex(ValueError, 'changed'):
            module('deferred_checkpoint_source').derope_source_manifest(self.f.manifest, changed, chain, upscale)
        changed = copy.deepcopy(self.derope['recovered-video'])
        changed['branch']['path'] = self.derope['recovered-av']['branch']['path']
        with runtime.runtime_access(self.store), self.assertRaisesRegex(ValueError, 'escapes'):
            module('deferred_checkpoint_source').derope_source_manifest(self.f.manifest, changed, chain, upscale)
        self.unchanged()


if __name__ == '__main__':
    unittest.main(argv=['storage-processing-reads'], verbosity=2)
