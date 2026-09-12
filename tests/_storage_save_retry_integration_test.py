"""Actual save retries with host-only operation IDs and exact input witnesses."""
import copy
import json
import math
import unittest
from unittest.mock import patch
import uuid

import _storage_generation_integration_test as fixture

chain, carriers, module, torch, av_latent = fixture.chain, fixture.carriers, fixture.module, fixture.torch, fixture.av_latent

retry = module('storage_save_retry')
state = module('storage_state')
digest = module('storage_execution_digest').execution_digest


class SaveRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.GenerationIntegrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.operation = uuid.uuid4().hex
        with carriers.node_host(self.store):
            self.incoming = chain.MiniMaxH3ChainLoopStart().start(self.f.plan, 1)[1]
        self.inputs = dict(state=self.incoming, images=self.f.frames, sampled_latent=av_latent(.4),
                           audio=self.f.audio(1), denoised_latent=av_latent(.6), unique_id='save')

    def save(self, inputs=None, operation=None):
        function = chain.MiniMaxH3ChainSegmentSave.save
        with carriers.node_host(self.store, generation_writers=(function,),
                generation_operations={(function, 'save'): operation or self.operation}):
            return chain.MiniMaxH3ChainSegmentSave().save(**(inputs or self.inputs))

    def no_encode(self):
        return patch.object(chain, '_write_segment_video', side_effect=AssertionError('retry encoded again'))

    def test_accepted_retry_returns_exact_take_and_preview_without_new_commit(self):
        before_plan = copy.deepcopy(self.incoming['plan'])
        result = self.save()
        original = self.store.snapshot()
        with self.no_encode():
            recovered = self.save()
        self.assertEqual(result['result'][0], recovered['result'][0])
        self.assertEqual(result['ui']['images'], recovered['ui']['images'])
        self.assertEqual(self.store.snapshot().reference, original.reference)
        self.assertEqual(result['result'][0]['revision'], self.operation)
        self.assertEqual(self.incoming['plan'], before_plan)

    def test_core_cache_and_widget_sentinels_are_preserved_in_exact_archive_retries(self):
        self.inputs['prompt'] = {'5':{'class_type':'MiniMaxH3ChainReview', 'inputs':{}, 'is_changed':[float('nan')]}}
        self.inputs['extra_pnginfo'] = {'workflow':{'nodes':[], 'ui_limit':float('inf')}}
        saved = self.save()['result'][0]
        snapshot = self.store.snapshot()
        for key, field, predicate in [('api_prompt', '5', lambda value: math.isnan(value['is_changed'][0])),
                                       ('workflow','ui_limit', math.isinf)]:
            address = saved['archives'][key].split('h3_chains/'+self.f.run+'/')[1]
            self.assertTrue(predicate(json.loads(snapshot.read(address))[field]))
        with self.no_encode():
            self.assertEqual(self.save()['result'][0], saved)
        self.inputs['prompt']['5']['inputs']['seed'] = 9
        with self.no_encode(), self.assertRaises(ValueError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, snapshot.reference)

    def test_nonfinite_plan_state_still_rejected_with_opaque_workflow(self):
        self.inputs['prompt'] = {'5':{'is_changed':[float('nan')]}}
        self.inputs['state']['plan']['invalid_setting'] = float('nan')
        before = self.store.snapshot().reference
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_lost_ack_replays_original_receipt_after_later_save_without_rollback(self):
        commit = self.store.commit_artifacts
        def lose(*args, **kwargs):
            commit(*args, **kwargs)
            raise OSError('lost save reply')
        with patch.object(self.store, 'commit_artifacts', side_effect=lose):
            with self.assertRaisesRegex(OSError, 'lost save reply'):
                self.save()
        accepted = self.store.snapshot()
        later_inputs = copy.deepcopy(self.inputs)
        later_inputs['state']['plan']['_storage_pin']['root'] = accepted.reference
        later = self.save(later_inputs, operation=uuid.uuid4().hex)['result'][0]
        latest = self.store.snapshot().reference
        with self.no_encode():
            recovered = self.save()['result'][0]
        self.assertEqual(recovered['_storage_pin']['root'], accepted.reference)
        self.assertEqual(recovered['revision'], self.operation)
        self.assertNotEqual(later['revision'], recovered['revision'])
        self.assertEqual(self.store.snapshot().reference, latest)
        self.assertEqual(state._decode(self.store.snapshot().read('checkpoints/clip_0001.json'))['segment']['revision'], later['revision'])

    def test_interrupted_publication_reuses_prepared_encoding(self):
        before = self.store.snapshot().reference
        publish = module('storage_generation').RuntimeGeneration.publish
        def fail(service, *args, **kwargs):
            def stop(stage):
                if stage == 'document':
                    raise OSError('interrupted publish')
            service.after_stage = stop
            return publish(service, *args, **kwargs)
        with patch.object(module('storage_generation').RuntimeGeneration, 'publish', fail):
            with self.assertRaisesRegex(OSError, 'interrupted publish'):
                self.save()
        self.assertEqual(self.store.snapshot().reference, before)
        with self.no_encode():
            recovered = self.save()['result'][0]
        self.assertEqual(recovered['revision'], self.operation)
        self.assertNotEqual(self.store.snapshot().reference, before)

    def test_changed_prompt_seed_tensor_audio_or_graph_cannot_reuse_identity(self):
        self.save()
        accepted = self.store.snapshot().reference
        changed = []
        for field, value in [('prompt', 'changed'), ('seed', 19)]:
            inputs = copy.deepcopy(self.inputs)
            inputs['state']['plan']['shots'][0][field] = value
            changed.append(inputs)
        for name in ('images', 'sampled_latent', 'denoised_latent', 'audio'):
            inputs = copy.deepcopy(self.inputs)
            if name == 'images':
                inputs[name].fill_(.7)
            elif name == 'audio':
                inputs[name]['waveform'].fill_(.7)
            else:
                inputs[name] = av_latent(.7)
            changed.append(inputs)
        changed.append(dict(self.inputs, prompt={'node': {'inputs': {'seed': 'different'}}}))
        changed.append(dict(self.inputs, extra_pnginfo={'workflow': {'nodes': []}}))
        with self.no_encode():
            for inputs in changed:
                with self.subTest(inputs=list(inputs)), self.assertRaisesRegex(ValueError, 'collision'):
                    self.save(inputs)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_saved_media_corruption_is_not_hidden_by_successful_receipt(self):
        saved = self.save()['result'][0]
        path = self.store.payload_path(self.store.snapshot(),
            saved['generated_audio'].split('/'+self.f.run+'/')[1])
        path.write_bytes(b'corrupted fixture audio')
        with self.no_encode(), self.assertRaises(ValueError):
            self.save()

    def test_owner_takeover_blocks_accepted_retry(self):
        self.save()
        with module('storage_runtime').runtime_access(self.store, ownership_writes=True):
            module('project_ownership').claim_project_ownership(self.f.output, self.f.run,
                'retry-other-owner', force=True)
        before = self.store.snapshot().reference
        with self.no_encode(), self.assertRaises(module('project_ownership').ProjectOwnershipError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_missing_input_pin_wrong_node_id_or_ungranted_id_reject(self):
        missing = copy.deepcopy(self.inputs)
        missing['state']['plan'].pop('_storage_pin')
        with self.assertRaisesRegex(ValueError, 'explicit input storage pin'):
            self.save(missing)
        with self.assertRaisesRegex(ValueError, 'unique_id'):
            self.save(dict(self.inputs, unique_id='other'))
        with self.assertRaisesRegex(ValueError, 'separate exact writer grant'):
            with carriers.node_host(self.store, generation_operations={
                    (chain.MiniMaxH3ChainSegmentSave.save, 'save'): self.operation}):
                pass
        with self.assertRaisesRegex(ValueError, 'callable'):
            with carriers.node_host(self.store, generation_operations={('save', 'save'): self.operation}):
                pass

    def test_unaccepted_prepared_file_corruption_keeps_prior_root(self):
        before = self.store.snapshot().reference
        with patch.object(self.store, 'commit_artifacts', side_effect=OSError('not accepted')):
            with self.assertRaises(OSError):
                self.save()
        prepared = self.store.project/'project/jobs'/self.operation/'prepared.json'
        saved = state._decode(prepared.read_bytes())['value']
        (self.store.project/saved['payloads']['video']).write_bytes(b'changed encoding')
        with self.no_encode(), self.assertRaises(ValueError):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_tensor_witness_is_bit_exact_bounded_and_layout_independent(self):
        tensor = torch.arange(1200000, dtype=torch.float32).reshape(1000, 1200).T
        self.assertEqual(digest(tensor), digest(tensor.contiguous()))
        self.assertNotEqual(digest(tensor), digest(tensor.to(torch.bfloat16)))
        self.assertNotEqual(digest(tensor), digest(tensor.reshape(-1)))
        self.assertNotEqual(digest({'a': True}), digest({'a': 1}))
        self.assertEqual(digest(torch.empty(0)), digest(torch.empty(0)))
        self.assertEqual(digest(torch.tensor(1., dtype=torch.bfloat16)),
                         digest(torch.tensor(1., dtype=torch.bfloat16)))
        cyclic = []; cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            digest(cyclic)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            digest(object())

    def test_prepared_record_checksum_and_mutated_encoder_inputs_reject(self):
        before = self.store.snapshot().reference
        encode = chain._write_segment_video
        def mutate(*args, **kwargs):
            encode(*args, **kwargs)
            self.inputs['images'].fill_(.9)
        with patch.object(chain, '_write_segment_video', mutate), self.assertRaisesRegex(ValueError, 'changed while encoding'):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)
        self.inputs['images'].zero_()
        with patch.object(self.store, 'commit_artifacts', side_effect=OSError('not accepted')):
            with self.assertRaises(OSError):
                self.save()
        path = self.store.project/'project/jobs'/self.operation/'prepared.json'
        record = state._decode(path.read_bytes())
        record['value']['metadata']['segment']['seed'] += 1
        path.write_bytes(state._encode(record))
        with self.no_encode(), self.assertRaisesRegex(ValueError, 'record failed its checksum'):
            self.save()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_prepared_reference_adoption_and_retry_use_verified_independent_cache(self):
        reference = self.f.reference()
        self.inputs['state']['plan']['compatibility']['generation_fingerprint'] = self.f.plan['compatibility']['generation_fingerprint']
        with patch.object(chain, '_find_reference_cache', return_value=reference), patch.object(
                self.store, 'commit_artifacts', side_effect=OSError('not accepted')):
            with self.assertRaises(OSError):
                self.save()
        with self.no_encode():
            saved = self.save()['result'][0]
        source = self.f.output/next(iter(reference['tensor_objects'].values()))['tensors']
        source.write_bytes(b'global cache changed after independent acceptance')
        with self.no_encode():
            self.assertEqual(self.save()['result'][0], saved)
        accepted = self.store.snapshot()
        manifest = state._decode(accepted.read(saved['reference_cache']['metadata'].split('/'+self.f.run+'/')[1]))
        local = next(iter(manifest['tensor_objects'].values()))['tensors'].split('/'+self.f.run+'/')[1]
        self.store.payload_path(accepted, local).write_bytes(b'accepted cache corrupted')
        with self.no_encode(), self.assertRaises(ValueError):
            self.save()

    def test_recovered_save_can_continue_actual_loop_with_unchanged_witness(self):
        self.save()
        recovered = self.save()['result'][0]
        with self.f.loop_host():
            expanded = self.f.expand_loop(self.incoming, recovered)
        self.assertIn('expand', expanded)


if __name__ == '__main__':
    unittest.main(argv=['save-retry-tests'], verbosity=2)
