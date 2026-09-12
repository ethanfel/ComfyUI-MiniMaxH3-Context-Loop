"""Actual processing encoder + atomic combined-store save on independent copies."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_processing_reads_integration_test as fixture

chain, upscale, torch = fixture.chain, fixture.upscale, fixture.torch
runtime, carriers, state = fixture.runtime, fixture.carriers, fixture.state
ownership = fixture.module('project_ownership')
continuation = fixture.module('storage_processing_continuation')


class ProcessingSaveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProcessingReadTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.run = self.f.store, self.f.output, self.f.run
        with runtime.runtime_access(self.store, ownership_writes=True):
            saved = ownership.claim_project_ownership(self.output, self.run, 'cpu-processing-owner')
        self.proof = dict(owner_id='cpu-processing-owner', epoch=saved['epoch'])

    def incoming(self, **options):
        _, incoming, source, _ = self.f.adapt(**options)
        incoming['_project_ownership'] = self.proof
        return incoming

    def save(self, incoming, **options):
        with carriers.node_host(self.store, processing_writers=(upscale.MiniMaxH3ChainUpscaleSegmentSave.save,)):
            return upscale.MiniMaxH3ChainUpscaleSegmentSave().save(incoming, self.f.f.frames, **options)

    def handoff(self, incoming, segment, **options):
        function = upscale.MiniMaxH3ChainUpscaleHandoff.prepare
        with carriers.node_host(self.store, input_adapters={function:continuation.loop_inputs}):
            return upscale.MiniMaxH3ChainUpscaleHandoff().prepare(incoming,
                options.get('images', self.f.f.frames), segment,
                options.get('upscaled_latent'))[0]

    def test_pixel_save_atomically_accepts_media_metadata_resume_and_execution(self):
        incoming = self.incoming()
        before = self.store.snapshot()
        original = copy.deepcopy(incoming)
        api = {'7':{'class_type':'PixelUpscaler','inputs':{'scale':2}}}
        ui = {'nodes':[{'id':7,'widgets_values':[2]}]}
        result = self.save(incoming, prompt=api, extra_pnginfo={'workflow':ui})
        segment = result['result'][0]
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertEqual(incoming, original)
        prefix = 'h3_chains/'+self.run+'/'
        metadata = state._decode(after.read(segment['revision_metadata'].removeprefix(prefix)))
        self.assertEqual(metadata['execution'], dict(api_prompt=api, workflow=ui))
        self.assertEqual(metadata, state._decode(after.read(segment['metadata'].removeprefix(prefix))))
        manifest = state._decode(after.read(segment['_processing_manifest'].removeprefix(prefix)))
        self.assertEqual(manifest['segments'], [upscale._public_upscale_segment(segment)])
        self.assertEqual(segment['source_revision'], self.f.f.alt['revision'])
        self.assertEqual(segment['seed'], self.f.f.alt['seed'])
        with runtime.runtime_access(self.store) as bound:
            path = bound.reader.path(segment['checkpoint'])
            self.assertIn('/media/pixel_upscale/', str(path))
            tensors = chain._st_load(str(path))
            self.assertTrue(torch.all(tensors['delivered_audio'] == .2))
            self.assertEqual(tensors['upscale_marker'].item(), 1)
        resumed = self.f.adapt(profile='new-pixels', start=2)[1]
        self.assertEqual(resumed['segments'][0]['revision'], segment['revision'])
        self.assertTrue((self.output/result['ui']['images'][0]['subfolder']/result['ui']['images'][0]['filename']).is_file())
        self.assertFalse((self.store.project/'upscaled').exists())
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
        self.store.verify_payloads()

    def test_range_start_two_does_not_create_scene_one_and_can_resume(self):
        incoming = self.incoming(start=2, mode='fresh_range')
        result = self.save(incoming)['result'][0]
        self.assertEqual(result['index'], 2)
        after = self.store.snapshot()
        self.assertNotIn('upscaled/new-pixels/checkpoints/clip_0001.json', after.state['documents'])
        saved = state._decode(after.read('upscaled/new-pixels/upscale_manifest.json'))
        self.assertEqual((saved['scene_start'], saved['scene_end']), (2,2))
        self.assertEqual(saved['segments'][0]['revision'], result['revision'])

    def test_readonly_wrong_owner_and_forged_seed_reject_before_encoder(self):
        incoming = self.incoming()
        before = self.store.snapshot().reference
        with patch.object(chain, '_write_segment_video', side_effect=AssertionError('encoder ran')):
            with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'processing writes'):
                upscale.MiniMaxH3ChainUpscaleSegmentSave().save(incoming, self.f.f.frames)
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.save(dict(incoming, _project_ownership=None))
            forged = copy.deepcopy(incoming)
            forged['source_manifest']['segments'][0]['seed'] += 1
            with runtime.runtime_access(self.store):
                forged['source_manifest_hash'] = upscale._source_hash(forged['source_manifest'])
            with self.assertRaisesRegex(ValueError, 'immutable metadata'):
                self.save(forged)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_fault_before_root_keeps_previous_resume_and_all_original_files(self):
        incoming = self.incoming(profile='pixels')
        before = self.store.snapshot()
        publisher = fixture.module('storage_processing').RuntimeProcessing
        real = publisher.publish
        observed = []
        def fail(publisher, *args):
            def interrupt(stage):
                observed.append(self.store.snapshot().reference)
                if stage == 'document':
                    raise OSError('interrupted processing acceptance')
            publisher.after_stage = interrupt
            return real(publisher, *args)
        with patch.object(publisher, 'publish', fail), self.assertRaisesRegex(OSError, 'interrupted'):
            self.save(incoming)
        self.assertTrue(observed and all(item == before.reference for item in observed))
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertEqual(self.f.adapt(profile='pixels', start=2)[1]['segments'][0]['revision'], self.f.processed['revision'])
        self.store.verify_payloads()

    def test_real_two_scene_handoff_adapter_resume_and_final_delivery(self):
        incoming = self.incoming()
        first = self.save(incoming)['result'][0]
        handed = self.handoff(incoming, first)
        with carriers.node_host(self.store):
            next_state = upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
                handed['source_manifest'], 'new-pixels', 'pixel', '{}', 1, 0, False, 18,
                initial_state=handed)[1]
            current = upscale.MiniMaxH3ChainUpscaleCurrent().current(next_state)
        self.assertEqual(current[4], 2)
        second = self.save(next_state)['result'][0]
        function = upscale.MiniMaxH3ChainUpscaleLoopEnd.end
        before = self.store.snapshot().reference
        with carriers.node_host(self.store, input_adapters={function:continuation.loop_inputs}):
            result = upscale.MiniMaxH3ChainUpscaleLoopEnd().end(
                'h3_upscale', next_state, self.f.f.frames, second)
        self.assertEqual([s['revision'] for s in result[0]['segments']], [first['revision'],second['revision']])
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(result[0]['_storage_pin']['root'], before)
        self.store.verify_payloads()

    def test_missing_receipt_changed_source_images_or_latent_cannot_continue(self):
        incoming = self.incoming()
        segment = self.save(incoming)['result'][0]
        with carriers.node_host(self.store), self.assertRaisesRegex(ValueError, 'mixed-root'):
            upscale.MiniMaxH3ChainUpscaleHandoff().prepare(incoming, self.f.f.frames, segment)
        for key, value in (('seed', 99), ('prompt', 'forged')):
            changed = copy.deepcopy(incoming)
            changed['source_manifest']['segments'][0][key] = value
            with self.assertRaisesRegex(ValueError, 'state differs'):
                self.handoff(changed, segment)
        with self.assertRaisesRegex(ValueError, 'tensors differ'):
            self.handoff(incoming, segment, images=self.f.f.frames+.1)
        with self.assertRaisesRegex(ValueError, 'tensors differ'):
            self.handoff(incoming, segment, upscaled_latent={'samples':torch.zeros(1)})
        changed = dict(segment, _processing_save=None)
        with self.assertRaisesRegex(ValueError, 'receipt and witness'):
            self.handoff(incoming, changed)

    def test_delivery_reattaches_only_the_witnessed_proof_and_rejects_changed_or_nested_denials(self):
        incoming = self.incoming()
        saved = self.save(incoming)['result'][0]
        handed = self.handoff(incoming, saved)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=handed['_storage_pin']) as bound:
            delivered = continuation.delivery(bound, handed, upscale)
            self.assertEqual(delivered['_project_ownership'], self.proof)
            for replacement in (None, dict(self.proof, epoch=self.proof['epoch']+1)):
                changed = dict(handed, _project_ownership=replacement)
                with self.assertRaisesRegex(ValueError, 'ownership proof'):
                    continuation.delivery(bound, changed, upscale)
            changed = dict(handed, _project_ownership=self.proof,
                source_manifest=dict(handed['source_manifest'], _project_ownership=None))
            with self.assertRaisesRegex(ValueError, 'ownership proof'):
                continuation.delivery(bound, changed, upscale)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_advance_cannot_accept_changed_hq_context_or_prefix(self):
        incoming = self.incoming(start=2, mode='fresh_range')
        segment = self.save(incoming)['result'][0]
        handed = self.handoff(incoming, segment)
        with runtime.runtime_access(self.store, pin=handed['_storage_pin']):
            changed = dict(handed, previous_latent={'samples':torch.zeros(1)})
            with self.assertRaisesRegex(ValueError, 'HQ context'):
                upscale.MiniMaxH3ChainUpscaleLoopEnd()._advance(None, changed)
            changed = dict(handed, segments=[])
            with self.assertRaisesRegex(ValueError, 'manifest'):
                upscale.MiniMaxH3ChainUpscaleLoopEnd()._advance(None, changed)

    def test_saved_joint_latent_and_recovered_audio_survive_derope_chained_source(self):
        with carriers.node_host(self.store):
            incoming = upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
                self.f.f.manifest, 'new-derope', 'h3_latent', '{"derope":true}',
                1, 0, True, 18)[1]
        incoming['_project_ownership'] = self.proof
        latent = fixture.fixture.av_latent(.6)
        audio = fixture.fixture.audio_for_frames(5)
        audio['waveform'].fill_(.5)
        segment = self.save(incoming, upscaled_latent=latent, recovered_audio=audio)['result'][0]
        with runtime.runtime_access(self.store) as bound:
            metadata = bound.reader.read(segment['revision_metadata'])
            tensors = chain._st_load(str(bound.reader.path(segment['checkpoint'])))
            self.assertTrue(torch.all(tensors['upscaled_video'] == .6))
            self.assertTrue(torch.all(tensors['upscaled_audio'] == .6))
            self.assertTrue(torch.all(tensors['delivered_audio'] == .5))
            selection = dict(stage='derope', profile_path=str(Path(segment['revision_metadata']).parent.parent),
                branch=dict(kind='metadata', path=segment['revision_metadata'], lineage=metadata['processing_lineage']))
            source = fixture.module('deferred_checkpoint_source').derope_source_manifest(
                self.f.f.manifest, selection, chain, upscale)
        second_state = self.f.adapt(source, profile='derope-then-pixel')[1]
        second_state['_project_ownership'] = self.proof
        child = self.save(second_state)['result'][0]
        with runtime.runtime_access(self.store) as bound:
            tensors = chain._st_load(str(bound.reader.path(child['checkpoint'])))
            self.assertTrue(torch.all(tensors['delivered_audio'] == .5))

    def test_takeover_during_encoding_fences_commit_and_keeps_previous_take(self):
        incoming = self.incoming(profile='pixels')
        before = self.store.snapshot().reference
        real = chain._write_segment_video
        def encode(*args, **kwargs):
            result = real(*args, **kwargs)
            # Ownership lives outside accepted project roots, as in production.
            bound = runtime.current_runtime(chain._output_root(), self.run)
            bound.ownership_writes = True
            ownership.claim_project_ownership(self.output, self.run, 'new-processing-owner', force=True)
            return result
        with patch.object(chain, '_write_segment_video', encode), self.assertRaises(ownership.ProjectOwnershipError):
            self.save(incoming)
        self.assertEqual(self.store.snapshot().reference, before)
        self.store.verify_payloads()


if __name__ == '__main__':
    unittest.main(argv=['processing-saves'])
