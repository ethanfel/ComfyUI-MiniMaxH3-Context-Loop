"""Actual checkpoint PNG/WAV renderer through combined-store publication."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
import wave

import av
import numpy as np
import _storage_processing_saves_integration_test as fixture

chain, upscale, torch = fixture.chain, fixture.upscale, fixture.torch
runtime, carriers, state = fixture.runtime, fixture.carriers, fixture.state
module = fixture.fixture.module
exports = module('storage_latent_export')
function = chain.MiniMaxH3ChainExportPNG.export


class VideoVAE:
    def __init__(self, counts):
        self.counts, self.calls = list(counts), []

    def decode(self, latent):
        value = float(latent.flatten()[0])
        count = self.counts[len(self.calls) % len(self.counts)]
        self.calls.append(value)
        frames = torch.full((count, 4, 4, 3), value)
        for frame in range(count):
            frames[frame, ..., 1] = frame/100
        return frames


class AudioVAE:
    audio_sample_rate = 2400

    def __init__(self, counts):
        self.counts, self.calls = list(counts), []

    def decode(self, latent):
        count = self.counts[len(self.calls) % len(self.counts)]
        value = float(latent.flatten()[0])
        self.calls.append(value)
        return torch.full((1,2,count*100), value)


class LatentExportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProcessingSaveTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof = self.f.store, self.f.proof
        incoming = self.f.incoming()
        self.manifest = dict(incoming['source_manifest'], _storage_pin=incoming['_storage_pin'],
            _branch_id='main', _project_ownership=self.proof)
        self.counts = [s['raw_frames'] for s in self.manifest['segments']]
        self.video, self.audio = VideoVAE(self.counts), AudioVAE(self.counts)
        self.namespace = uuid.uuid4().hex

    def export(self, manifest=None, *, video=True, audio=True, unique_id='png', **options):
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=self.namespace):
            return chain.MiniMaxH3ChainExportPNG().export(manifest=manifest or self.manifest,
                video_vae=self.video if video else None, audio_vae=self.audio if audio else None,
                unique_id=unique_id, **dict(dict(export_name='Checkpoint', png_bit_depth='16',
                    first_frame_number=101, embed_workflow=True), **options))

    def repin(self, manifest=None):
        source = copy.deepcopy(manifest or self.manifest)
        for item in carriers._carriers(source):
            if '_storage_pin' in item:
                item['_storage_pin']['root'] = self.store.snapshot().reference
        return source

    def chapter(self, count=None):
        source = self.repin()
        if count is not None:
            source['segments'] = source['segments'][:count]
        source['format'] = chain.CHAPTER_MANIFEST_FORMAT
        source['chapter'] = dict(number=1, id='first', title='First', start_scene=1,
                                end_scene=len(source['segments']), planned_end_scene=2, complete=count != 1)
        source['clip_count'] = len(source['segments'])
        source['scene_start'], source['scene_end'] = 1, len(source['segments'])
        source['total_delivered_frames'] = sum(s['delivered_frames'] for s in source['segments'])
        return source

    def record(self, result):
        snapshot = self.store.snapshot()
        for address in snapshot.state['documents']:
            if address.endswith('/export.json'):
                value = state._decode(snapshot.read(address))
                name = value['frame_files'][0]['file'] if value['frame_files'] else 'audio.wav'
                physical = self.store.payload_path(snapshot, address.removesuffix('export.json')+name)
                match = physical.parent == Path(result['result'][0]) if value['frame_files'] else str(physical) == result['result'][3]
                if match:
                    return address, value
        self.fail('No accepted checkpoint export index')

    def test_real_renderer_preserves_alt_picture_base_audio_trim_metadata_and_archives(self):
        before, source = self.store.snapshot(), copy.deepcopy(self.manifest)
        result = self.export()
        self.assertEqual(Path(result['result'][0]).name, 'Checkpoint')
        self.assertEqual(Path(result['result'][3]).name, 'Checkpoint.wav')
        address, record = self.record(result)
        self.assertEqual(self.manifest, source)
        self.assertAlmostEqual(self.video.calls[0], .7)
        self.assertTrue(all(abs(value-.1)<1e-6 for value in self.audio.calls))
        count = sum(s['delivered_frames'] for s in source['segments'])
        self.assertEqual((result['result'][1],record['timeline_frame_count']), (count,count))
        self.assertIsNone(result['result'][4])
        self.assertEqual([s['seed'] for s in record['clips']], [s['seed'] for s in source['segments']])
        cursor = 101
        for clip, segment, value in zip(record['clips'], source['segments'], self.video.calls):
            trim = segment['raw_frames']-segment['delivered_frames']
            for frame in range(segment['delivered_frames']):
                with av.open(str(Path(result['result'][0])/('frame_%08d.png' % cursor))) as container:
                    pixels = next(container.decode(video=0)).to_ndarray(format='rgb48le')
                self.assertTrue(np.all(pixels[...,0] == round(value*65535)))
                self.assertTrue(np.all(pixels[...,1] == round((trim+frame)/100*65535)))
                cursor += 1
        with wave.open(result['result'][3], 'rb') as saved:
            self.assertEqual((saved.getframerate(),saved.getnframes(),saved.getnchannels()),(2400,count*100,2))
        from PIL import Image
        with Image.open(Path(result['result'][0])/'frame_00000101.png') as frame:
            self.assertEqual(frame.info['h3_prompt'], source['segments'][0]['prompt'])
            self.assertIn('h3_manifest', frame.info)
            self.assertIn('h3_plan', frame.info)
        after = self.store.snapshot()
        for path, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][path],descriptor)
        self.assertFalse((self.store.project/'frames').exists())
        self.store.verify_payloads()

    def test_video_only_audio_only_and_no_vae_error(self):
        first = self.export(audio=False)
        self.assertEqual(first['result'][3], '')
        self.assertFalse(self.audio.calls)
        second = self.export(self.repin(), video=False, unique_id='audio')
        self.assertEqual(second['result'][1], 0)
        self.assertTrue(Path(second['result'][3]).is_file())
        self.assertFalse(self.record(second)[1]['frame_files'])
        with self.assertRaisesRegex(ValueError, 'needs video_vae'):
            self.export(self.repin(), video=False, audio=False, unique_id='none')

    def test_exact_retry_skips_both_vaes_and_keeps_root(self):
        result = self.export()
        before = self.store.snapshot().reference
        with patch.object(self.video,'decode',side_effect=AssertionError('decoded video again')), \
             patch.object(self.audio,'decode',side_effect=AssertionError('decoded audio again')):
            self.assertEqual(self.export()['result'],result['result'])
        self.assertEqual(self.store.snapshot().reference,before)

    def test_chapter_append_reuses_frames_and_keeps_old_wav_and_history(self):
        first_source = self.chapter(1)
        first = self.export(first_source, unique_id='chapter1')
        old = self.store.snapshot()
        first_path = Path(first['result'][0])/'frame_00000101.png'
        inode, raw, old_audio = first_path.stat().st_ino, first_path.read_bytes(), Path(first['result'][3]).read_bytes()
        second = self.export(self.chapter(), unique_id='chapter2')
        self.assertEqual(first['result'][0],second['result'][0])
        self.assertEqual((first_path.stat().st_ino,first_path.read_bytes()),(inode,raw))
        self.assertEqual(Path(first['result'][3]).read_bytes(),old_audio)
        self.assertNotEqual(first['result'][3],second['result'][3])
        self.assertEqual(len(self.video.calls),2)
        self.assertEqual(len(self.audio.calls),3)
        address, record = self.record(second)
        self.assertEqual(record['reused_clip_count'],1)
        self.assertEqual(state._decode(old.read(address))['timeline_frame_count'],first_source['total_delivered_frames'])
        before_retry = self.store.snapshot().reference
        self.assertEqual(self.export(first_source, unique_id='chapter1')['result'],first['result'])
        self.assertEqual(self.store.snapshot().reference,before_retry)
        self.store.verify_payloads(old)
        self.store.verify_payloads()

    def test_chapter_unchanged_reuses_wav_and_no_decode(self):
        self.export(self.chapter(), unique_id='full')
        with patch.object(self.video,'decode',side_effect=AssertionError('video decoded')), \
             patch.object(self.audio,'decode',side_effect=AssertionError('audio decoded')):
            result = self.export(self.chapter(), unique_id='repeat')
        self.assertEqual(self.record(result)[1]['reused_clip_count'],2)

    def test_changed_settings_or_disabled_reuse_preserve_numbered_variants(self):
        first = self.export(self.chapter(), unique_id='first')
        second = self.export(self.chapter(), unique_id='changed', png_bit_depth='8')
        third = self.export(self.chapter(), unique_id='fresh', reuse_existing=False)
        self.assertEqual(len({r['result'][0] for r in (first,second,third)}),3)
        self.assertTrue(self.record(second)[0].endswith('Checkpoint_0002/export.json'))
        self.assertTrue(self.record(third)[0].endswith('Checkpoint_0003/export.json'))
        self.store.verify_payloads()

    def test_private_progress_and_prepared_interruption_retry(self):
        operation = uuid.uuid4().hex
        def workspace(bound):
            return exports.LatentPNGExport(bound,upscale,self.manifest,operation=operation,
                export_name='private',video_vae=self.video,audio_vae=self.audio)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = workspace(bound)
            work.prepare(chain.MiniMaxH3ChainExportPNG(),workers=2)
            self.assertEqual(self.store.snapshot().reference,before)
            events = list((self.store.project/work.buffer/'progress').glob('*.json'))
            self.assertGreater(len(events),3)
            self.assertFalse((Path(work.render_directory)/'export.partial.json').exists())
            def interrupt(stage):
                if stage == 'payload':
                    raise OSError('injected publication interruption')
            bound.exports.after_stage = interrupt
            with self.assertRaisesRegex(OSError,'injected'):
                work.publish()
        self.assertEqual(self.store.snapshot().reference,before)
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound, \
             patch.object(self.video,'decode',side_effect=AssertionError('video decoded')), \
             patch.object(self.audio,'decode',side_effect=AssertionError('audio decoded')):
            work = workspace(bound)
            work.prepare(chain.MiniMaxH3ChainExportPNG())
            work.publish()
        self.store.verify_payloads()

    def test_readonly_missing_operation_and_forged_seed_fail_before_decode(self):
        before = self.store.snapshot().reference
        with carriers.node_host(self.store), self.assertRaisesRegex(ValueError,'export writes'):
            chain.MiniMaxH3ChainExportPNG().export(manifest=self.manifest,video_vae=self.video)
        with carriers.node_host(self.store,export_writers=(function,)), self.assertRaisesRegex(ValueError,'operation ID'):
            chain.MiniMaxH3ChainExportPNG().export(manifest=self.manifest,video_vae=self.video)
        forged = copy.deepcopy(self.manifest)
        forged['segments'][0]['seed'] += 1
        with self.assertRaisesRegex(ValueError,'immutable metadata'):
            self.export(forged)
        self.assertFalse(self.video.calls or self.audio.calls)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_direct_alt_and_unresolved_base_manifest_keep_the_original_audio(self):
        for name, source in (('base',self.f.f.f.manifest), ('alt',self.f.f.direct_alt)):
            with self.subTest(route=name):
                source = copy.deepcopy(source)
                source.update(_storage_pin=self.repin()['_storage_pin'], _branch_id='main', _project_ownership=self.proof)
                counts = [s['raw_frames'] for s in source['segments']]
                self.video, self.audio = VideoVAE(counts), AudioVAE(counts)
                result = self.export(source,unique_id=name)
                self.assertAlmostEqual(self.video.calls[0],.7)
                self.assertAlmostEqual(self.audio.calls[0],.1)
                self.assertEqual(self.record(result)[1]['clips'][0]['seed'],self.f.f.f.alt['seed'])

    def test_editorial_tail_trim_keeps_technical_head_cut_and_shortens_wav(self):
        # Save a real longer checkpoint on the migrated copy. Five-frame test
        # scenes have no shorter boundary shared by both video and audio grids.
        with runtime.runtime_access(self.store) as bound:
            plan = chain.MiniMaxH3ChainPlan().build(json.dumps({'shots':[
                {'id':'longer','prompt':'A longer migration fixture','length':22,'steps':2,'seed':'9'}]}),
                self.f.run,'',32,32,1,'video','head','disabled','generated_audio',
                1,5/24,2,7,18,0,'guide')[0]
            plan.update(_storage_pin=bound.pin,_branch_id='main',_project_ownership=self.proof)
            incoming = chain._initial_state(plan,1)
        shot = plan['shots'][0]
        raw = shot['raw_frames']
        latent = {'samples':[torch.full((1,24,chain._h3_prefix_frame_boundary_step(raw),2,2),.4),
                              torch.full((1,32,2,round(raw*40/24)),.4)]}
        sound = dict(waveform=torch.full((1,2,round(shot['delivered_frames']*8000/24)),.2),sample_rate=8000)
        saver = chain.MiniMaxH3ChainSegmentSave.save
        with carriers.node_host(self.store,generation_writers=(saver,),operation_namespace=self.namespace):
            saved = chain.MiniMaxH3ChainSegmentSave().save(incoming,
                torch.zeros(shot['delivered_frames'],32,32,3),latent,sound,unique_id='longer')['result'][0]
        source = dict(self.manifest,segments=[saved],clip_count=1,scene_start=1,scene_end=1,
                      total_delivered_frames=shot['delivered_frames'],editorial={})
        source = self.repin(source)
        self.video,self.audio = VideoVAE([raw]),AudioVAE([raw])
        last = source['segments'][-1]
        out = 9  # Exact 3-step video prefix and 15-step audio prefix.
        removed = last['delivered_frames']-out
        editorial = copy.deepcopy(source.get('editorial') or {})
        editorial['trims'] = [dict(scene=last['index'],scene_id=last['id'],out_frame=out)]
        source['editorial'] = chain._normalize_run_editorial(editorial,source['run_name'])
        result = self.export(source)
        record = self.record(result)[1]
        self.assertEqual(record['timeline_frame_count'],source['total_delivered_frames']-removed)
        self.assertEqual(record['clips'][-1]['delivered_frames'],out)
        first = record['clips'][-1]['first_frame_number']
        with av.open(str(Path(result['result'][0])/('frame_%08d.png' % first))) as container:
            pixels = next(container.decode(video=0)).to_ndarray(format='rgb48le')
        self.assertTrue(np.all(pixels[...,1] == round((last['raw_frames']-last['delivered_frames'])/100*65535)))
        with wave.open(result['result'][3], 'rb') as saved:
            self.assertEqual(saved.getnframes(),(source['total_delivered_frames']-removed)*100)

    def test_audio_only_chapter_append_returns_real_directory_and_retains_old_audio(self):
        first = self.export(self.chapter(1),video=False,unique_id='audio1')
        before = Path(first['result'][3]).read_bytes()
        self.assertTrue(Path(first['result'][0]).is_dir())
        second = self.export(self.chapter(),video=False,unique_id='audio2')
        self.assertEqual(first['result'][0],second['result'][0])
        self.assertNotEqual(first['result'][3],second['result'][3])
        self.assertEqual(Path(first['result'][3]).read_bytes(),before)
        self.assertFalse(self.video.calls)
        self.assertEqual(self.record(second)[1]['timeline_frame_count'],self.manifest['total_delivered_frames'])

    def test_later_range_starts_without_prior_exports(self):
        source = copy.deepcopy(self.manifest)
        source['segments'] = source['segments'][1:]
        source.update(clip_count=1,scene_start=2,scene_end=2,
            total_delivered_frames=source['segments'][0]['delivered_frames'])
        self.video, self.audio = VideoVAE(self.counts[1:]), AudioVAE(self.counts[1:])
        result = self.export(source)
        record = self.record(result)[1]
        self.assertEqual([c['index'] for c in record['clips']],[2])
        self.assertEqual(record['clips'][0]['first_frame_number'],101)

    def test_ownership_takeover_and_changed_prepared_bytes_cannot_publish(self):
        operation = uuid.uuid4().hex
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = exports.LatentPNGExport(bound,upscale,self.manifest,operation=operation,
                export_name='fenced',video_vae=self.video)
            work.prepare(chain.MiniMaxH3ChainExportPNG())
            before = self.store.snapshot().reference
            prepared = work._prepared()
            path = self.store.project/next(iter(prepared['files'].values()))['path']
            original = path.read_bytes()
            path.write_bytes(original[:-1]+bytes([original[-1]^1]))
            with self.assertRaisesRegex(ValueError,'bytes changed'):
                work.publish()
            self.assertEqual(self.store.snapshot().reference,before)
            path.write_bytes(original)
        with runtime.runtime_access(self.store,ownership_writes=True):
            fixture.ownership.claim_project_ownership(self.f.output,self.f.run,'new-latent-export-owner',force=True)
        after = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            with self.assertRaises(fixture.ownership.ProjectOwnershipError):
                exports.LatentPNGExport(bound,upscale,self.manifest,operation=operation,
                    export_name='fenced',video_vae=self.video)
        self.assertEqual(self.store.snapshot().reference,after)

    def test_stale_family_publisher_cannot_replace_a_newer_export(self):
        source = self.chapter()
        first = self.export(source,unique_id='winner')
        before = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.export(source,unique_id='stale')
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(self.record(first)[1]['timeline_frame_count'],source['total_delivered_frames'])

    def test_reverse_recovery_can_reuse_checkpoint_pngs_and_original_wav(self):
        recovery = module('storage_recovery')
        source = self.chapter()
        exported = self.export(source)
        address, record = self.record(exported)
        lab = self.f.f.lab
        receipt = lab/'latent-recovery-receipt.json'
        state.atomic_json(receipt,dict(copy=str(self.store.project),source=str(self.f.f.source),independent_copies=True))
        destination = lab/'latent-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt,destination,lab/'latent-recovery',rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        restored = destination/'h3_chains'/self.f.run
        self.assertEqual(json.loads((restored/address).read_text()),record)
        for carrier in carriers._carriers(source):
            carrier.pop('_storage_pin',None)
        with patch.object(fixture.fixture.fixture.folder_paths,'output_directory',str(destination)), \
             patch.object(self.video,'decode',side_effect=AssertionError('recovered PNGs must be reused')), \
             patch.object(self.audio,'decode',side_effect=AssertionError('recovered audio must be reused')):
            audio, pictures, _, _ = chain._checkpoint_export_views(source)
            identity = chain._png_export_incremental_identity(source,audio,pictures,self.video,self.audio,101,1,True,16)
            self.assertEqual(identity['settings'],record['incremental']['settings'])
            self.assertTrue(chain._png_export_sources_match(identity['sources'],record['incremental']['sources']))
            result = chain.MiniMaxH3ChainExportPNG().export(manifest=source,video_vae=self.video,audio_vae=self.audio,
                export_name='Checkpoint',first_frame_number=101,png_bit_depth='16',embed_workflow=True)
        self.assertEqual(Path(result['result'][0]),restored/address.removesuffix('/export.json'))
        self.assertEqual(Path(result['result'][3]).read_bytes(),Path(exported['result'][3]).read_bytes())

    def test_derope_video_and_joint_audio_use_their_saved_streams(self):
        for name, expected_audio in (('recovered-video',.3),('recovered-av',.6)):
            with self.subTest(profile=name):
                with runtime.runtime_access(self.store) as bound:
                    source = module('deferred_checkpoint_source').derope_source_manifest(
                        self.f.f.f.manifest,self.f.f.derope[name],chain,upscale)
                    source.update(_storage_pin=bound.pin,_branch_id='main',_project_ownership=self.proof)
                self.video,self.audio = VideoVAE(self.counts),AudioVAE(self.counts)
                self.export(source,unique_id=name)
                self.assertAlmostEqual(self.video.calls[0],.6)
                self.assertAlmostEqual(self.audio.calls[0],expected_audio)
                self.assertAlmostEqual(self.audio.calls[1],.1)

    def test_source_comparison_ignores_only_checkpoint_timestamp_hints(self):
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = exports.LatentPNGExport(bound,upscale,self.manifest,operation=uuid.uuid4().hex,
                                          export_name='identity',video_vae=self.video,audio_vae=self.audio)
        sources = work.identity['sources']
        changed = copy.deepcopy(sources)
        changed[0]['picture']['checkpoint_mtime_ns'] += 1
        self.assertTrue(chain._png_export_sources_match(sources,changed))
        for key,value in (('checkpoint_sha256','a'*64),('checkpoint_size',1)):
            forged = copy.deepcopy(changed)
            forged[0]['picture'][key] = value
            self.assertFalse(chain._png_export_sources_match(sources,forged))
        changed[0]['audio']['segment']['seed'] += 1
        self.assertFalse(chain._png_export_sources_match(sources,changed))

    def import_legacy_chapter(self, name, *, immutable):
        """Import bytes produced by the ordinary pre-migration exporter.

        This exercises the legacy index contract, not a new-format index with
        its storage ID removed. All source/output roots are temporary fixtures.
        """
        source = self.chapter(1)
        for carrier in carriers._carriers(source):
            carrier.pop('_storage_pin', None)
            carrier.pop('_project_ownership', None)
        with patch.object(fixture.fixture.fixture.folder_paths, 'output_directory', str(self.f.f.legacy_output)):
            result = chain.MiniMaxH3ChainExportPNG().export(manifest=source,
                video_vae=self.video, audio_vae=self.audio, export_name=name,
                first_frame_number=101, png_bit_depth='16', embed_workflow=True)
        folder = Path(result['result'][0])
        index = json.loads((folder/'export.json').read_text())
        self.assertNotIn('_storage_export_id', index)
        logical = folder.relative_to(self.f.f.source).as_posix()
        imported_id = uuid.uuid4().hex
        base, receipts = self.store.snapshot(), []
        for item in index['frame_files']+[index['audio']]:
            is_audio = item['file'] == 'audio.wav'
            target = ('exports/audio/' if is_audio else 'exports/png/')+imported_id+'/'+item['file']
            receipts.append(self.store.stage_payload(logical+'/'+item['file'], folder/item['file'],
                target, scope='archive:legacy_checkpoint_export', immutable=immutable or not is_audio,
                operation_id=uuid.uuid4().hex))
        self.store.commit_artifacts(base, {logical+'/export.json': dict(data=state._encode(index),
            scope='archive:legacy_checkpoint_export', category='legacy', immutable=immutable)}, receipts,
            operation_id=uuid.uuid4().hex)
        return logical, index

    def test_actual_legacy_index_prefix_is_copied_without_decode_or_archive_changes(self):
        for immutable in (False, True):
            with self.subTest(immutable=immutable):
                name = 'Legacy_archived' if immutable else 'Legacy_current'
                logical, old_index = self.import_legacy_chapter(name, immutable=immutable)
                before = self.store.snapshot()
                old_files = {item['file']:self.store.payload_path(before,logical+'/'+item['file'])
                             for item in old_index['frame_files']+[old_index['audio']]}
                old_bytes = {name:path.read_bytes() for name,path in old_files.items()}
                self.video,self.audio = VideoVAE(self.counts[1:]),AudioVAE(self.counts)
                result = self.export(self.chapter(), unique_id=name, export_name=name)
                address, record = self.record(result)
                self.assertEqual(address,logical+'_0002/export.json')
                self.assertEqual(record['reused_clip_count'],1)
                self.assertEqual(len(self.video.calls),1)
                self.assertEqual(len(self.audio.calls),2)
                for name, old in old_files.items():
                    self.assertEqual(old.read_bytes(),old_bytes[name])
                    if name != 'audio.wav':
                        current = Path(result['result'][0])/name
                        self.assertEqual(current.read_bytes(),old_bytes[name])
                        self.assertNotEqual(current.stat().st_ino,old.stat().st_ino)
                after = self.store.snapshot()
                self.assertEqual(before.state['documents'][logical+'/export.json'],
                                 after.state['documents'][logical+'/export.json'])
                self.assertEqual(state._decode(after.read(logical+'/export.json')),old_index)
                self.store.verify_payloads(before)
                self.store.verify_payloads()

    def test_actual_legacy_complete_chapter_can_fork_without_either_vae(self):
        logical, original = self.import_legacy_chapter('Legacy_complete',immutable=True)
        before = self.store.snapshot()
        with patch.object(self.video,'decode',side_effect=AssertionError('Imported PNG prefix decoded')), \
             patch.object(self.audio,'decode',side_effect=AssertionError('Imported complete WAV decoded')):
            result = self.export(self.chapter(1),export_name='Legacy_complete')
        address, record = self.record(result)
        self.assertEqual(address,logical+'_0002/export.json')
        self.assertEqual(record['new_frame_count'],0)
        self.assertEqual(record['reused_frame_count'],original['frame_count'])
        self.assertEqual(Path(result['result'][3]).read_bytes(),
                         self.store.payload_path(before,logical+'/audio.wav').read_bytes())
        self.store.verify_payloads()

    def test_decode_interruption_keeps_private_progress_and_restarts_in_a_new_buffer(self):
        operation = uuid.uuid4().hex
        def workspace(bound):
            return exports.LatentPNGExport(bound,upscale,self.manifest,operation=operation,
                                          export_name='decode-retry',video_vae=self.video)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = workspace(bound)
            decode = self.video.decode
            def interrupt(latent):
                if self.video.calls:
                    raise OSError('injected decode interruption')
                return decode(latent)
            with patch.object(self.video,'decode',side_effect=interrupt), self.assertRaisesRegex(OSError,'injected'):
                work.prepare(chain.MiniMaxH3ChainExportPNG())
            previous_buffer = Path(work.render_directory)
            preserved = {p:p.read_bytes() for p in previous_buffer.rglob('*') if p.is_file()}
            self.assertTrue(preserved)
            self.assertIsNone(work._prepared())
        self.assertEqual(self.store.snapshot().reference,before)
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = workspace(bound)
            work.prepare(chain.MiniMaxH3ChainExportPNG())
            self.assertNotEqual(Path(work.render_directory),previous_buffer)
            work.publish()
        self.assertEqual({p:p.read_bytes() for p in preserved},preserved)
        self.store.verify_payloads()


if __name__ == '__main__':
    unittest.main(argv=[__file__])
