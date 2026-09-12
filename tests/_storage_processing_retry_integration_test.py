"""Actual processing saver retries: accepted, prepared, interrupted and stale."""
import copy
import json
import math
import unittest
from unittest.mock import patch
import uuid

import _storage_processing_saves_integration_test as fixture

chain, upscale, carriers, runtime, torch, state = (
    fixture.chain, fixture.upscale, fixture.carriers, fixture.runtime, fixture.torch, fixture.state)
module = fixture.fixture.module


class ProcessingRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProcessingSaveTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.operation = uuid.uuid4().hex
        self.inputs = dict(state=self.f.incoming(), images=self.f.f.f.frames, unique_id='save')

    def save(self, inputs=None, operation=None):
        function = upscale.MiniMaxH3ChainUpscaleSegmentSave.save
        with carriers.node_host(self.store, processing_writers=(function,),
                processing_operations={(function,'save'):operation or self.operation}):
            return upscale.MiniMaxH3ChainUpscaleSegmentSave().save(**(inputs or self.inputs))

    def no_encode(self):
        return patch.object(chain, '_write_segment_video', side_effect=AssertionError('retry encoded again'))

    def test_accepted_retry_returns_exact_take_preview_and_usable_handoff(self):
        before_input = copy.deepcopy(self.inputs['state'])
        original = self.save()
        accepted = self.store.snapshot().reference
        with self.no_encode():
            recovered = self.save()
        self.assertEqual(recovered['result'][0], original['result'][0])
        self.assertEqual(recovered['ui']['images'], original['ui']['images'])
        self.assertEqual(self.store.snapshot().reference, accepted)
        next_state = self.f.handoff(self.inputs['state'], recovered['result'][0])
        self.assertEqual(next_state['index'], 2)
        self.assertEqual(self.inputs['state'], before_input)

    def test_lost_ack_recovers_old_save_without_rolling_back_later_take(self):
        commit = self.store.commit_artifacts
        def lose(*args, **kwargs):
            commit(*args, **kwargs)
            raise OSError('lost processing reply')
        with patch.object(self.store, 'commit_artifacts', side_effect=lose), self.assertRaisesRegex(OSError, 'lost processing reply'):
            self.save()
        accepted = self.store.snapshot().reference
        later_inputs = dict(self.inputs, state=self.f.incoming())
        later = self.save(later_inputs, uuid.uuid4().hex)['result'][0]
        latest = self.store.snapshot()
        with self.no_encode():
            recovered = self.save()['result'][0]
        self.assertEqual(recovered['_storage_pin']['root'], accepted)
        self.assertEqual(recovered['revision'], self.operation)
        self.assertEqual(self.store.snapshot().reference, latest.reference)
        canonical = state._decode(latest.read('upscaled/new-pixels/checkpoints/clip_0001.json'))
        self.assertEqual(canonical['segment']['revision'], later['revision'])

    def test_interrupted_commit_reuses_prepared_files_without_encoder(self):
        publisher = module('storage_processing').RuntimeProcessing
        publish = publisher.publish
        before = self.store.snapshot().reference
        def fail(service, *args):
            def stop(stage):
                if stage == 'document':
                    raise OSError('interrupted processing commit')
            service.after_stage = stop
            return publish(service, *args)
        with patch.object(publisher, 'publish', fail), self.assertRaisesRegex(OSError,'interrupted'):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)
        with self.no_encode():
            recovered = self.save()['result'][0]
        self.assertEqual(recovered['revision'], self.operation)
        self.assertNotEqual(self.store.snapshot().reference, before)
        self.store.verify_payloads()

    def test_failed_encoder_can_retry_same_operation_in_another_private_workspace(self):
        before = self.store.snapshot().reference
        with patch.object(chain, '_write_segment_video', side_effect=OSError('failed encoder')), self.assertRaises(OSError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)
        saved = self.save()['result'][0]
        self.assertEqual(saved['revision'], self.operation)
        self.assertEqual(len(list((self.store.project/'project/jobs'/self.operation).glob('encode-*'))), 2)

    def test_changed_settings_tensors_audio_or_workflow_cannot_borrow_operation(self):
        self.save()
        before = self.store.snapshot().reference
        modified = []
        for key,value in (('seed',42),('prompt','forged')):
            inputs = copy.deepcopy(self.inputs)
            inputs['state']['source_manifest']['segments'][0][key] = value
            modified.append(inputs)
        modified += [dict(self.inputs, images=self.inputs['images']+.1),
            dict(self.inputs, upscaled_latent={'samples':torch.zeros(1)}),
            dict(self.inputs, recovered_audio={'waveform':torch.zeros(1,2,300),'sample_rate':32000}),
            dict(self.inputs, prompt={'42':{'inputs':{'seed':14}}}),
            dict(self.inputs, extra_pnginfo={'workflow':{'nodes':[]}})]
        with self.no_encode():
            for inputs in modified:
                with self.subTest(keys=list(inputs)), self.assertRaisesRegex(ValueError, 'collision'):
                    self.save(inputs)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_corrupted_prepared_files_never_reencode_or_accept(self):
        before = self.store.snapshot().reference
        with patch.object(self.store,'commit_artifacts',side_effect=OSError('not accepted')), self.assertRaises(OSError):
            self.save()
        path = self.store.project/'project/jobs'/self.operation/'prepared.json'
        prepared = json.loads(path.read_text())['value']
        (self.store.project/prepared['payloads']['checkpoint']).write_bytes(b'changed private checkpoint')
        with self.no_encode(), self.assertRaisesRegex(ValueError, 'changed|differs|different|match'):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_owner_takeover_blocks_recovery_of_accepted_save(self):
        self.save()
        with runtime.runtime_access(self.store, ownership_writes=True):
            module('project_ownership').claim_project_ownership(self.f.output,self.f.run,'next-processing-owner',force=True)
        before = self.store.snapshot().reference
        with self.no_encode(), self.assertRaises(module('project_ownership').ProjectOwnershipError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_accepted_payload_corruption_is_not_hidden_by_receipt(self):
        saved = self.save()['result'][0]
        with runtime.runtime_access(self.store) as bound:
            path = bound.reader.path(saved['generated_audio'])
        path.write_bytes(b'corrupted copied audio')
        with self.no_encode(), self.assertRaises(ValueError):
            self.save()

    def test_invalid_prepared_checksum_or_foreign_encoder_path_rejected(self):
        with patch.object(self.store,'commit_artifacts',side_effect=OSError('not accepted')), self.assertRaises(OSError):
            self.save()
        path = self.store.project/'project/jobs'/self.operation/'prepared.json'
        envelope = json.loads(path.read_text())
        original = copy.deepcopy(envelope)
        envelope['value']['payloads']['video'] = 'elsewhere/video.mp4'
        state.atomic_json(path,envelope)
        with self.no_encode(), self.assertRaisesRegex(ValueError,'checksum'):
            self.save()
        envelope['sha256'] = state._hash(state._encode(envelope['value']))
        state.atomic_json(path,envelope)
        with self.no_encode(), self.assertRaisesRegex(ValueError,'escapes'):
            self.save()
        state.atomic_json(path,original)
        with self.no_encode():
            self.save()

    def test_missing_pin_wrong_node_id_or_cross_domain_identity_rejected(self):
        missing = copy.deepcopy(self.inputs)
        missing['state'].pop('_storage_pin')
        with self.assertRaisesRegex(ValueError,'explicit input storage pin'):
            self.save(missing)
        with self.assertRaisesRegex(ValueError,'unique_id'):
            self.save(dict(self.inputs,unique_id='other'))
        function = upscale.MiniMaxH3ChainUpscaleSegmentSave.save
        with self.assertRaisesRegex(ValueError,'separate exact writer grant'):
            with carriers.node_host(self.store, processing_operations={(function,'save'):self.operation}):
                pass
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            with carriers.node_host(self.store,generation_writers=(function,),processing_writers=(function,),
                    generation_operations={(function,'generation'):self.operation},
                    processing_operations={(function,'save'):self.operation}):
                pass

    def test_nonfinite_workflow_extensions_survive_publication_and_retry(self):
        ui = {'nodes':[{'id':42,'widgets_values':[float('inf'),float('-inf'),float('nan'),2**64-1]}]}
        inputs = dict(self.inputs, extra_pnginfo={'workflow':ui})
        saved = self.save(inputs)['result'][0]
        with self.no_encode():
            recovered = self.save(inputs)['result'][0]
        self.assertEqual(saved,recovered)
        with runtime.runtime_access(self.store) as bound:
            metadata = bound.reader.read(saved['revision_metadata'])
            self.assertNotIn('execution',metadata)
            execution = module('processing_execution').from_metadata(metadata)
            values = execution['workflow']['nodes'][0]['widgets_values']
            self.assertEqual(values[:2],[float('inf'),float('-inf')])
            self.assertTrue(math.isnan(values[2]))
            self.assertEqual(values[3],2**64-1)
            self.assertEqual(chain._fingerprint(execution),saved['execution_hash'])
            control_bytes = bound.base.read(bound.reader.address(saved['revision_metadata']))
            json.loads(control_bytes,parse_constant=lambda value:self.fail('non-finite control authority'))

    def test_hq_witness_normalizes_without_cloning_and_matches_actual_handoff(self):
        latent = fixture.fixture.fixture.av_latent(.6)
        proof = module('storage_processing_continuation')
        with patch.object(chain,'_tensor_cpu_clone',side_effect=AssertionError('witness cloned latent')):
            normalized = proof.context_latent(latent,upscale)
            expected = module('storage_execution_digest').execution_digest(normalized)
        actual = module('storage_execution_digest').execution_digest(upscale._cpu_latent(latent))
        self.assertEqual(expected,actual)

    def test_inputs_modified_during_encoder_are_not_accepted(self):
        before = self.store.snapshot().reference
        real = chain._write_segment_video
        def changed(*args,**kwargs):
            result = real(*args,**kwargs)
            self.inputs['images'].fill_(.3)
            return result
        with patch.object(chain,'_write_segment_video',changed), self.assertRaisesRegex(ValueError,'changed while encoding'):
            self.save()
        self.assertEqual(self.store.snapshot().reference,before)


if __name__ == '__main__':
    unittest.main(argv=['processing-retry'])
