"""Ordinary final renderer through atomic organized assembly publication."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
import wave
from types import SimpleNamespace
import numpy as np

import av
import _storage_latent_exports_integration_test as fixture

chain, upscale, carriers, runtime, state = (getattr(fixture, name) for name in
    ('chain','upscale','carriers','runtime','state'))
assembly = fixture.module('storage_assembly')
function = chain.MiniMaxH3ChainAssemble.assemble


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LatentExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof, self.manifest = self.f.store, self.f.proof, self.f.manifest
        self.namespace = uuid.uuid4().hex

    def assemble(self, manifest=None, *, unique_id='assembly', **settings):
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=self.namespace):
            return chain.MiniMaxH3ChainAssemble().assemble(manifest or self.manifest,
                **dict(dict(audio_source='none',filename='Final',audio_bitrate=96), **settings), unique_id=unique_id)

    def witness(self):
        snapshot = self.store.snapshot()
        keys = [p for p in snapshot.state['documents'] if p.startswith('export_records/')]
        return state._decode(snapshot.read(keys[-1]))

    def test_actual_renderer_short_paths_generated_wav_atomic_root_and_preview(self):
        before = self.store.snapshot()
        original = copy.deepcopy(self.manifest)
        result = self.assemble()
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.state['generation'], before.state['generation']+1)
        self.assertEqual(self.manifest, original)
        path = Path(result['result'][0])
        self.assertIn('/exports/video/', str(path))
        self.assertEqual(path.name, 'Final.mp4')
        with av.open(str(path)) as video:
            self.assertEqual(len(list(video.decode(video=0))), original['total_delivered_frames'])
            self.assertFalse(video.streams.audio)
        saved = self.witness()
        wav = self.store.payload_path(snapshot, saved['logical']['audio'], verify=True)
        self.assertEqual(wav.name, 'Final.generated.wav')
        with wave.open(str(wav),'rb') as audio:
            self.assertEqual(audio.getnframes(), round(original['total_delivered_frames']*audio.getframerate()/24))
            pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')/32768
            self.assertTrue(np.allclose(pcm, .2, atol=1e-4), 'ALT picture must retain base decoded audio')
        item = result['ui']['images'][0]
        self.assertEqual(self.store.project.parent.parent/item['subfolder']/item['filename'], path)
        self.assertFalse((self.store.project/'final').exists())
        self.assertNotIn('/project/jobs/', result['ui']['text'][0])
        after_documents = snapshot.state['documents']
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after_documents[address], descriptor)
        self.store.verify_payloads()

    def test_exact_retry_and_numbered_exports_preserve_prior_outputs(self):
        first = self.assemble()
        snapshot = self.store.snapshot()
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            self.assertEqual(self.assemble()['result'], first['result'])
        self.assertEqual(self.store.snapshot().reference, snapshot.reference)
        second = self.assemble(self.f.repin(), unique_id='second')
        self.assertEqual(Path(second['result'][0]).name, 'Final_2.mp4')
        self.assertNotEqual(first['result'], second['result'])
        self.assertIn('final/Final_001.mp4', fixture.module('storage_project').payload_catalog(self.store.snapshot()))
        self.assertTrue(Path(first['result'][0]).is_file())
        self.store.verify_payloads(snapshot)

    def test_embedded_metadata_preserves_archives_and_uses_saved_clip_geometry(self):
        source = copy.deepcopy(self.manifest)
        # A saved branch can have a different canvas from the current Plan.
        geometry = chain.common_saved_resolution(source['segments'], 'metadata test')
        self.assertTrue(geometry)
        source['compatibility'].update(width=geometry['width']+16, height=geometry['height']+16)
        original = copy.deepcopy(source)
        expected_manifest = copy.deepcopy(source)
        expected_manifest['compatibility'].update(geometry)
        with runtime.runtime_access(self.store, pin=source['_storage_pin']) as bound:
            tags = chain._manifest_media_metadata(expected_manifest, rehearsal_view=bound.reader)
        result = self.assemble(source, audio_source='generated')
        with av.open(result['result'][0]) as video:
            embedded = json.loads(video.metadata['h3_manifest'])
            # JSON object ordering is not a settings difference. Every value,
            # including the exact saved segments, still has to match.
            self.assertEqual(embedded, expected_manifest)
            self.assertEqual(embedded['segments'], original['segments'])
            for key, value in tags.items():
                if key != 'h3_manifest':
                    self.assertEqual(video.metadata.get(key), str(value), key)
            self.assertEqual((video.streams.video[0].width, video.streams.video[0].height),
                (geometry['width'], geometry['height']))
        self.assertEqual(source, original)
        self.assertEqual(self.witness()['manifest'], expected_manifest)

    def test_prepared_retry_after_publication_interruption_skips_render(self):
        operation = uuid.uuid4().hex
        def workspace(bound):
            return assembly.AssemblyExport(bound, upscale, self.manifest, operation=operation,
                audio_source='none', filename='retry', audio_bitrate=96)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=self.manifest['_storage_pin'], export_writes=True) as bound:
            work = workspace(bound)
            work.prepare(chain.MiniMaxH3ChainAssemble())
            def interrupt(stage):
                if stage == 'payload':
                    raise OSError('injected assembly publication interruption')
            bound.exports.after_stage = interrupt
            with self.assertRaisesRegex(OSError, 'injected'):
                work.publish()
        self.assertEqual(self.store.snapshot().reference, before)
        with runtime.runtime_access(self.store, pin=self.manifest['_storage_pin'], export_writes=True) as bound, \
             patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('rendered again')):
            work = workspace(bound)
            work.prepare(chain.MiniMaxH3ChainAssemble())
            result = work.publish()
        self.assertTrue(Path(result['result'][0]).is_file())
        self.store.verify_payloads()

    def test_missing_grant_operation_and_forged_seed_are_rejected(self):
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('renderer ran')):
            with carriers.node_host(self.store), self.assertRaisesRegex(ValueError,'export writes'):
                chain.MiniMaxH3ChainAssemble().assemble(self.manifest, 'none','bad',96)
            with carriers.node_host(self.store, export_writers=(function,)), self.assertRaisesRegex(ValueError,'operation ID'):
                chain.MiniMaxH3ChainAssemble().assemble(self.manifest, 'none','bad',96)
            source = copy.deepcopy(self.manifest)
            source['segments'][0]['seed'] += 1
            with self.assertRaisesRegex(ValueError,'immutable metadata'):
                self.assemble(source)

    def test_generated_audio_mux_and_pyav_fallback_keep_same_duration(self):
        with patch.object(chain, '_usable_ffmpeg', return_value=None):
            result = self.assemble(audio_source='generated')
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(video.streams.audio), 1)
            self.assertEqual(len(list(video.decode(video=0))), self.manifest['total_delivered_frames'])
        self.assertIn('PyAV', result['ui']['text'][0])

    def test_subtitles_use_pinned_asset_catalogue_and_publish_together(self):
        base = self.store.snapshot()
        catalog = dict(format='h3_project_assets_v1', version=1, project=self.store.project.name,
            assets=[dict(id='lyrics',kind='audio',tag='words',lyrics='[00:00.00]First line\n[00:00.20]Second line')])
        self.store.commit_artifacts(base, {'project_assets/catalog.json':dict(data=state._encode(catalog),
            scope='project',category='assets',immutable=False)}, [], operation_id=uuid.uuid4().hex)
        source = self.f.repin()
        source['editorial'] = dict(subtitles=dict(mode='preview_srt',asset_id='lyrics',offset_seconds=0))
        result = self.assemble(source)
        saved, snapshot = self.witness(), self.store.snapshot()
        self.assertEqual(set(saved['files']), {'video','audio','subtitles'})
        path = self.store.payload_path(snapshot, saved['logical']['subtitles'], verify=True)
        self.assertIn('First line', path.read_text())
        self.assertIn('00:00:00,000 -->', path.read_text())
        self.assertIn('project_assets/catalog.json', saved['dependencies'])
        self.assertTrue(Path(result['result'][0]).is_file())

    def test_partial_processing_manifest_keeps_hq_provenance_and_audio(self):
        source = copy.deepcopy(self.f.f.f.expected_resume)
        manifest = upscale._upscale_manifest(source, source['segments'], False)
        manifest.update(_storage_pin=self.manifest['_storage_pin'], _branch_id='main', _project_ownership=self.proof)
        result = self.assemble(manifest)
        saved = self.witness()
        self.assertEqual(set(saved['files']), {'video','audio','metadata'})
        record = state._decode(self.store.snapshot().read(saved['logical']['metadata']))
        self.assertFalse(record['complete'])
        self.assertEqual(record['profile'], 'pixels')
        self.assertEqual(record['completed_clip_count'], 1)
        self.assertEqual(record['planned_clip_count'], 2)
        self.assertEqual(record['processing_executions'][0]['revision'], source['segments'][0]['revision'])
        self.assertIn('/upscaled/pixels/final/', record['video'])
        self.assertIn('partial upscale 1/2', result['ui']['text'][0])
        self.store.verify_payloads()

    def test_partial_generation_keeps_generated_wav_independent_of_selected_mux_audio(self):
        source = copy.deepcopy(self.manifest)
        source.update(format='h3_chain_partial_manifest_v3',
            planned_clip_count=source['clip_count']+1, last_completed_clip=source['clip_count'])
        for mode in ('generated', 'none'):
            with self.subTest(audio_source=mode):
                incoming = self.f.repin(source)
                result = self.assemble(incoming, audio_source=mode, unique_id='partial_'+mode)
                snapshot = self.store.snapshot()
                keys = [p for p in snapshot.state['documents'] if p.startswith('export_records/')]
                saved = next(state._decode(snapshot.read(p)) for p in keys
                    if str(self.store.payload_path(snapshot, state._decode(snapshot.read(p))['logical']['video'])) == result['result'][0])
                wav_path = self.store.payload_path(snapshot, saved['logical']['audio'], verify=True)
                self.assertEqual(saved['manifest']['format'], 'h3_chain_partial_manifest_v3')
                self.assertEqual(saved['manifest']['segments'], source['segments'])
                with wave.open(str(wav_path), 'rb') as wav:
                    self.assertEqual(wav.getnframes(), round(source['total_delivered_frames']*wav.getframerate()/24))
                with av.open(result['result'][0]) as video:
                    self.assertEqual(len(video.streams.audio), int(mode == 'generated'))

    def test_overwrite_moves_only_logical_alias_and_old_retry_does_not_roll_back(self):
        first = self.assemble()
        before = self.store.snapshot()
        first_bytes = Path(first['result'][0]).read_bytes()
        second = self.assemble(self.f.repin(), unique_id='overwrite', overwrite_existing=True)
        after = self.store.snapshot().reference
        self.assertNotEqual(first['result'], second['result'])
        self.assertEqual(Path(first['result'][0]).read_bytes(), first_bytes)
        self.assertEqual(self.assemble()['result'], first['result'])
        self.assertEqual(self.store.snapshot().reference, after)
        catalog = fixture.module('storage_project').payload_catalog(self.store.snapshot())
        self.assertEqual(str(self.store.project/catalog['final/Final.mp4']['file']['path']), second['result'][0])
        self.store.verify_payloads(before)

    def test_source_audio_fingerprint_and_sampled_audio_sidecar_are_separate(self):
        source = copy.deepcopy(self.manifest)
        audio = fixture.torch.full((1,2,round(source['total_delivered_frames']*8000/24)), .4)
        audio = dict(waveform=audio, sample_rate=8000)
        source['compatibility']['source_audio_hash'] = chain._audio_fingerprint(audio)
        result = self.assemble(source, audio_source='source', source_audio=audio)
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(video.streams.audio), 1)
        wav = self.store.payload_path(self.store.snapshot(), self.witness()['logical']['audio'])
        with wave.open(str(wav), 'rb') as saved:
            pcm = np.frombuffer(saved.readframes(saved.getnframes()), dtype='<i2')/32768
        self.assertTrue(np.allclose(pcm, .2, atol=1e-4))

    def test_mutated_prepared_file_and_ownership_takeover_do_not_publish(self):
        operation = uuid.uuid4().hex
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = assembly.AssemblyExport(bound,upscale,self.manifest,operation=operation,
                filename='protected',audio_source='none',audio_bitrate=96)
            work.prepare(chain.MiniMaxH3ChainAssemble())
            saved = work._prepared()
            path = self.store.project/saved['files']['video']['path']
            raw = path.read_bytes()
            path.write_bytes(raw+b'injected-corruption')
            with self.assertRaisesRegex(ValueError,'bytes changed'):
                work.publish()
            self.assertEqual(self.store.snapshot().reference,before)
            path.write_bytes(raw)
        ownership = fixture.module('project_ownership')
        with runtime.runtime_access(self.store,ownership_writes=True):
            ownership.claim_project_ownership(self.store.project.parent.parent,self.store.project.name,'assembly-new-owner',force=True)
        after = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            with self.assertRaises(ownership.ProjectOwnershipError):
                assembly.AssemblyExport(bound,upscale,self.manifest,operation=operation,
                    filename='protected',audio_source='none',audio_bitrate=96)
        self.assertEqual(self.store.snapshot().reference,after)

    def test_stale_export_family_cannot_replace_newer_publication(self):
        self.assemble(unique_id='winner')
        after = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.assemble(unique_id='stale')
        self.assertEqual(self.store.snapshot().reference, after)

    def test_later_chapter_range_has_no_prior_scene_requirement(self):
        source = self.f.chapter()
        source['segments'] = source['segments'][1:]
        source.update(clip_count=1,scene_start=2,scene_end=2,
            total_delivered_frames=source['segments'][0]['delivered_frames'])
        source['chapter'].update(number=2,id='second',start_scene=2,end_scene=2)
        result = self.assemble(source)
        saved = self.witness()
        self.assertEqual(saved['logical']['video'], 'chapters/02_second/final/Final.mp4')
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(list(video.decode(video=0))), source['total_delivered_frames'])

    def test_reorder_gap_and_color_filter_use_existing_renderer(self):
        source = copy.deepcopy(self.manifest)
        source['editorial'] = dict(
            scene_order=[dict(scene=s['index'],scene_id=s['id']) for s in source['segments']],
            placements=[dict(scene=2,scene_id=source['segments'][1]['id'],start_frame=0),
                        dict(scene=1,scene_id=source['segments'][0]['id'],start_frame=10)])
        result = self.assemble(source, color_stabilization='scene_1_anchor')
        expected = 10+source['segments'][0]['delivered_frames']
        self.assertEqual(self.witness()['frame_count'], expected)
        self.assertIn('editorial scene order [2,1]', result['ui']['text'][0])
        self.assertIn('black editorial frames', result['ui']['text'][0])
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(list(video.decode(video=0))), expected)

    def test_midnight_retry_keeps_authored_filename_expansion(self):
        with patch.object(chain, '_expand_filename_date', return_value='2026-09-11'):
            result = self.assemble(filename='%date:yyyy-MM-dd%')
        with patch.object(chain, '_expand_filename_date', side_effect=AssertionError('date expanded again')):
            self.assertEqual(self.assemble(filename='%date:yyyy-MM-dd%')['result'], result['result'])

    def test_encoder_failure_leaves_no_export_and_retry_uses_new_private_attempt(self):
        operation = uuid.uuid4().hex
        def work(bound):
            return assembly.AssemblyExport(bound,upscale,self.manifest,operation=operation,
                filename='failed-render',audio_source='none',audio_bitrate=96)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound, \
             patch.object(chain,'_run_ffmpeg',side_effect=OSError('encoder interrupted')):
            with self.assertRaisesRegex(OSError,'encoder interrupted'):
                work(bound).prepare(chain.MiniMaxH3ChainAssemble())
        self.assertEqual(self.store.snapshot().reference,before)
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            retry = work(bound)
            retry.prepare(chain.MiniMaxH3ChainAssemble())
            self.assertTrue(retry.buffer.endswith('assemble0002'))
            result = retry.publish()
        self.assertTrue(Path(result['result'][0]).is_file())

    def test_scheduled_blend_redecodes_in_private_directory_and_keeps_duration(self):
        source = copy.deepcopy(self.f.f.f.f.manifest)
        source.update(_storage_pin=self.manifest['_storage_pin'], _branch_id='main',
                      _project_ownership=self.proof, editorial={})
        calls = []
        class BlendVAE:
            def decode(self, latent):
                calls.append(tuple(latent.shape))
                return fixture.torch.full((source['segments'][1]['raw_frames'],32,32,3), .3)
        result = self.assemble(source, blend_schedule='1',blend_video_vae=BlendVAE())
        self.assertEqual(len(calls),1)
        self.assertIn('scheduled visual blends [1]', result['ui']['text'][0])
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(list(video.decode(video=0))), source['total_delivered_frames'])
        self.assertFalse((self.store.project/'final').exists())
        self.assertFalse(list((self.store.project/'project/jobs').rglob('.blend_*.mkv')))
        self.store.verify_payloads()

    def test_forged_prepared_manifest_and_cross_branch_destination_reject(self):
        operation = uuid.uuid4().hex
        with runtime.runtime_access(self.store,pin=self.manifest['_storage_pin'],export_writes=True) as bound:
            work = assembly.AssemblyExport(bound,upscale,self.manifest,operation=operation,
                filename='forged',audio_source='none',audio_bitrate=96)
            work.prepare(chain.MiniMaxH3ChainAssemble())
            original = work._prepared()
            saved = copy.deepcopy(original)
            saved['manifest']['segments'][0]['seed'] += 1
            with self.assertRaisesRegex(ValueError,'provenance changed'):
                work._validate(saved)
            saved = copy.deepcopy(original)
            saved['base'] = 'branches/'+'a'*32+'/final/forged'
            saved['logical'] = {key:saved['base']+assembly.SUFFIXES[key] for key in saved['files']}
            with self.assertRaisesRegex(ValueError,'identity/selection changed'):
                work._validate(saved)

    def prelude_source(self):
        source = copy.deepcopy(self.manifest)
        segment = source['segments'][0]
        frames, rate = segment['delivered_frames'], 4800
        temporary = self.store.project.parent.parent/'prelude-audio.safetensors'
        chain._st_save({'waveform': fixture.torch.full((1,2,frames*200), .4)}, str(temporary))
        token = uuid.uuid4().hex
        address = 'prelude/audio.safetensors'
        base = self.store.snapshot()
        staged = self.store.stage_payload(address, temporary, 'project/assets/'+token+'/audio.safetensors',
            scope='archive:'+token, operation_id=token)
        self.store.commit_artifacts(base, {}, [staged], operation_id=uuid.uuid4().hex)
        source['prelude'] = dict(prepend=True, frame_count=frames, fps=24,
            width=source['compatibility']['width'], height=source['compatibility']['height'],
            video=segment['segment'], video_sha256=segment['segment_sha256'],
            audio='h3_chains/'+source['run_name']+'/'+address, audio_sha256=chain._file_sha256(str(temporary)),
            audio_sample_rate=rate)
        return self.f.repin(source)

    def test_existing_video_prelude_reads_accepted_picture_and_audio_without_legacy_paths(self):
        source = self.prelude_source()
        original = copy.deepcopy(source)
        result = self.assemble(source, audio_source='generated', blend_schedule='0')
        saved, snapshot = self.witness(), self.store.snapshot()
        frames = source['prelude']['frame_count']+source['total_delivered_frames']
        self.assertEqual(saved['frame_count'], frames)
        self.assertEqual(source, original)
        self.assertIn('existing-video prelude', result['ui']['text'][0])
        with av.open(result['result'][0]) as video:
            self.assertEqual(len(list(video.decode(video=0))), frames)
            self.assertEqual(len(video.streams.audio), 1)
        with wave.open(str(self.store.payload_path(snapshot, saved['logical']['audio'])), 'rb') as wav:
            self.assertEqual(wav.getnframes(), round(frames*wav.getframerate()/24))
            prefix = round(source['prelude']['frame_count']*wav.getframerate()/24)
            pcm = np.frombuffer(wav.readframes(prefix), dtype='<i2')/32768
            self.assertTrue(np.allclose(pcm, .4, atol=1e-4))
        key = fixture.module('storage_project').payload_key('prelude/audio.safetensors')
        self.assertIn(key, saved['dependencies'])
        self.store.verify_payloads()

    def test_prelude_corruption_rejects_before_any_renderer_or_publication(self):
        source = self.prelude_source()
        path = self.store.payload_path(self.store.snapshot(), 'prelude/audio.safetensors')
        path.write_bytes(b'corrupted independent test audio')
        root = self.store.snapshot().reference
        with patch.object(chain, '_run_ffmpeg', side_effect=AssertionError('renderer ran')):
            with self.assertRaises(ValueError):
                self.assemble(source, audio_source='generated', blend_schedule='0')
        self.assertEqual(self.store.snapshot().reference, root)

    def test_source_timeline_recovers_accepted_audio_and_keeps_identity_and_base_soundtrack(self):
        source = copy.deepcopy(self.manifest)
        temporary = self.store.project.parent.parent/'timeline-source.wav'
        count, rate = source['total_delivered_frames'], 4800
        chain._write_wav(dict(waveform=fixture.torch.full((1,2,count*200), .35), sample_rate=rate), str(temporary))
        timeline = chain.MiniMaxH3SourceTimeline().build(audio_path=str(temporary))[0]
        record = chain._source_timeline_recovery_record(timeline)
        record['audio']['path'] = '/old-host/input/timeline-source.wav'
        source['source_timeline'] = record
        source['compatibility'].update(source_timeline_fingerprint=timeline['fingerprints']['timeline'],
            source_audio_hash=timeline['fingerprints']['audio'])
        token, address = uuid.uuid4().hex, 'project_assets/audio/timeline-source.wav'
        base = self.store.snapshot()
        staged = self.store.stage_payload(address, temporary, 'project/assets/'+token+'/source.wav',
            scope='archive:'+token, operation_id=token)
        catalog = dict(format='h3_project_assets_v1', project=source['run_name'], assets=[dict(
            relative_path='audio/timeline-source.wav', sha256=chain._file_sha256(str(temporary)),
            size=temporary.stat().st_size)])
        self.store.commit_artifacts(base, {'project_assets/catalog.json':dict(data=state._encode(catalog),
            scope='project', category='assets', immutable=False)}, [staged], operation_id=uuid.uuid4().hex)
        source = self.f.repin(source)
        original = copy.deepcopy(source)
        result = self.assemble(source, audio_source='source')
        saved, snapshot = self.witness(), self.store.snapshot()
        self.assertEqual(source, original)
        self.assertEqual(saved['manifest']['source_timeline'], record)
        self.assertIn('project_assets/catalog.json', saved['dependencies'])
        self.assertIn(fixture.module('storage_project').payload_key(address), saved['dependencies'])
        with av.open(result['result'][0]) as media:
            decoded = np.concatenate([frame.to_ndarray() for frame in media.decode(audio=0)], axis=-1)
            self.assertAlmostEqual(float(np.median(decoded)), .35, delta=.03)
        # The archival generated WAV remains the base clip audio, even when
        # the MP4 mux uses this separately restored Source Timeline.
        with wave.open(str(self.store.payload_path(snapshot, saved['logical']['audio'])), 'rb') as wav:
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')/32768
            self.assertTrue(np.allclose(pcm, .2, atol=1e-4))
        self.store.verify_payloads()

    def merge(self, manifest, unique_id='merge', *, granted=True, **host_options):
        writers = (upscale.MiniMaxH3ChainUpscaleMerge.merge,) if granted else ()
        with carriers.node_host(self.store, export_writers=writers,
                **dict(dict(operation_namespace=self.namespace), **host_options)):
            return upscale.MiniMaxH3ChainUpscaleMerge().merge(manifest, 'generated', 'Legacy_merge', 96,
                unique_id=unique_id)

    def merge_source(self):
        source = copy.deepcopy(self.f.f.f.expected_resume)
        manifest = upscale._upscale_manifest(source, source['segments'], False)
        manifest.update(_storage_pin=self.manifest['_storage_pin'], _branch_id='main', _project_ownership=self.proof)
        return manifest

    def test_legacy_merge_node_keeps_hq_contract_with_its_own_export_grant_and_id(self):
        manifest = self.merge_source()
        original = copy.deepcopy(manifest)
        result = self.merge(manifest)
        snapshot, saved = self.store.snapshot(), self.witness()
        self.assertEqual(set(saved['files']), {'video', 'audio', 'metadata'})
        metadata = state._decode(snapshot.read(saved['logical']['metadata']))
        self.assertEqual(metadata['profile'], 'pixels')
        self.assertFalse(metadata['complete'])
        self.assertEqual(manifest, original)
        with av.open(result['result'][0]) as media:
            self.assertEqual(len(list(media.decode(video=0))), manifest['total_delivered_frames'])
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('encoded retry')):
            self.assertEqual(self.merge(manifest), result)
        self.assertEqual(self.store.snapshot().reference, snapshot.reference)
        self.store.verify_payloads()

    def test_legacy_merge_cannot_borrow_assemble_grant_or_missing_node_identity(self):
        source = self.merge_source()
        root = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError, 'export writes'):
            self.merge(source, granted=False)
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=self.namespace):
            with self.assertRaisesRegex(ValueError, 'export writes'):
                upscale.MiniMaxH3ChainUpscaleMerge().merge(source, 'none', 'bad', 96, unique_id='merge')
        with self.assertRaisesRegex(ValueError, 'unique_id'):
            self.merge(source, unique_id=None)
        with self.assertRaisesRegex(ValueError, 'operation ID'):
            self.merge(source, operation_namespace=None)
        self.assertEqual(self.store.snapshot().reference, root)

    def test_legacy_merge_prepared_retry_keeps_the_exact_dynamic_node_operation(self):
        source = self.merge_source()
        original = assembly.AssemblyExport.publish
        def interrupt(work):
            def fail(stage):
                if stage == 'payload':
                    raise OSError('injected legacy Merge publication failure')
            work.runtime.exports.after_stage = fail
            return original(work)
        root = self.store.snapshot().reference
        with patch.object(assembly.AssemblyExport, 'publish', interrupt):
            with self.assertRaisesRegex(OSError, 'injected legacy Merge'):
                self.merge(source, unique_id='parent.scene5.merge')
        self.assertEqual(self.store.snapshot().reference, root)
        with patch.object(chain.MiniMaxH3ChainAssemble, '_assemble', side_effect=AssertionError('encoded retry')):
            result = self.merge(source, unique_id='parent.scene5.merge')
        self.assertTrue(Path(result['result'][0]).is_file())
        self.store.verify_payloads()

    def test_final_review_notification_uses_the_new_accepted_path_not_the_old_input_snapshot(self):
        events = []
        server = SimpleNamespace(send_sync=lambda *args: events.append(args))
        for copied in (False, True):
            source = self.f.repin()
            key = chain._final_review_preview_key(source)
            pending = {key: dict(token='review-token', node_id='review-node', client_id='test-client')}
            with patch.dict(chain._PENDING_FINAL_REVIEW_PREVIEWS, pending, clear=True), \
                    patch.object(chain, 'PromptServer', SimpleNamespace(instance=server)), \
                    patch.object(chain, '_video_output_item', side_effect=AssertionError('Resolved new output through old pin')):
                result = self.assemble(source, unique_id='review_'+str(copied), copy_to_output=copied,
                    output_subfolder='review-copies')
                self.assertNotIn(key, chain._PENDING_FINAL_REVIEW_PREVIEWS)
            event, payload, client = events[-1]
            self.assertEqual(event, 'minimax_h3_context_loop_review_resolved')
            self.assertEqual(client, 'test-client')
            self.assertEqual(payload['final_video'], result['ui']['images'][0])
            item = payload['final_video']
            path = self.store.project.parent.parent/item['subfolder']/item['filename']
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), Path(result['result'][0]).read_bytes())
        self.assertEqual(len(events), 2)


class AlternateAssemblyTests(unittest.TestCase):
    def setUp(self):
        import _storage_alternate_delivery_integration_test as alternate_fixture
        self.f = alternate_fixture.AlternateDeliveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store

    def test_readonly_manifest_load_can_feed_export_without_granting_a_new_owner(self):
        before = self.store.snapshot().reference
        with carriers.node_host(self.store):
            manifest = chain.MiniMaxH3ChainManifestLoad().load(self.f.f.plan)[0]
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(manifest['_project_ownership'], self.f.f.proof)
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=uuid.uuid4().hex):
            result = chain.MiniMaxH3ChainAssemble().assemble(manifest, 'none', 'Loaded', 96,
                blend_schedule='0', unique_id='loaded')
        self.assertTrue(Path(result['result'][0]).is_file())
        before = self.store.snapshot().reference
        with carriers.node_host(self.store):
            denied = chain.MiniMaxH3ChainManifestLoad().load(dict(self.f.f.plan,_project_ownership=None))[0]
        self.assertIsNone(denied['_project_ownership'])
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=uuid.uuid4().hex):
            with self.assertRaises(fixture.module('project_ownership').ProjectOwnershipError):
                chain.MiniMaxH3ChainAssemble().assemble(denied, 'none', 'Denied', 96,
                    blend_schedule='0', unique_id='denied')
        self.assertEqual(self.store.snapshot().reference, before)

    def test_actual_alt_loop_end_connects_directly_to_assembly_with_original_audio(self):
        # Make the candidate picture visibly different from the saved base.
        self.f.f.frames.fill_(.7)
        audio = self.f.f.audio
        def candidate_audio(index):
            value = audio(index)
            value['waveform'].fill_(.6)
            return value
        with patch.object(self.f.f, 'audio', candidate_audio):
            incoming, saved = self.f.candidate()
        manifest = self.f.end(incoming, saved)[0]
        self.assertEqual(manifest['_project_ownership'], incoming['plan']['_project_ownership'])
        original = copy.deepcopy(manifest)
        namespace = uuid.uuid4().hex
        for mode in ('none', 'generated'):
            with self.subTest(audio_source=mode):
                source = copy.deepcopy(manifest)
                source['_storage_pin']['root'] = self.store.snapshot().reference
                with carriers.node_host(self.store, export_writers=(function,), operation_namespace=namespace):
                    result = chain.MiniMaxH3ChainAssemble().assemble(source, mode, 'ALT', 96,
                        blend_schedule='0', unique_id='alt_'+mode)
                snapshot = self.store.snapshot()
                docs = snapshot.state['documents']
                exports = [state._decode(snapshot.read(p)) for p in docs if p.startswith('export_records/')]
                record = next(r for r in exports if str(self.store.payload_path(snapshot,r['logical']['video'])) == result['result'][0])
                self.assertIn('audio', record['logical'])
                with av.open(result['result'][0]) as video:
                    pictures = list(video.decode(video=0))
                    self.assertEqual(len(pictures), saved['delivered_frames'])
                    self.assertGreater(pictures[0].to_ndarray(format='rgb24').mean(), 150)
                    self.assertEqual(len(video.streams.audio), int(mode == 'generated'))
                with runtime.runtime_access(self.store, pin=source['_storage_pin']) as bound:
                    expected = chain._generated_audio(dict(source,segments=[self.f.f.originals[0]]),
                        rehearsal_view=bound.reader)
                    direct = chain._generated_audio(source, rehearsal_view=bound.reader)
                self.assertTrue(fixture.torch.equal(direct['waveform'], expected['waveform']))
                waveform, rate = chain._validate_audio(expected, 'original ALT audio')
                wav_path = self.store.payload_path(snapshot, record['logical']['audio'], verify=True)
                with wave.open(str(wav_path), 'rb') as wav:
                    actual = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
                    pcm = waveform[0].transpose(0,1).clamp(-1,1).mul(32767).round().to(fixture.torch.int16).numpy().reshape(-1)
                    self.assertEqual(wav.getframerate(), rate)
                    self.assertTrue(np.array_equal(actual, pcm))
                delivery = state._decode(snapshot.read(state._decode(snapshot.read(
                    'deliveries/main/alternates/scene_0001/latest.json'))['manifest']))
                self.assertNotIn('_project_ownership', delivery)
        self.assertEqual(manifest, original)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
