"""Atomic base/ALT scene publication, independent media and writer grants."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_restore_routes_unit_test as fixture
from storage_runtime import runtime_access
from storage_carriers import node_host, PIN_KEY
from branch_scope import scoped_node, branch_scope
import storage_state as state
import project_ownership as ownership


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RestoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output = self.f.store, self.f.output
        self.proof, self.named = self.f.proof, self.f.named

    def candidate(self, scene=1, alternate=False):
        base = self.store.snapshot()
        metadata = state._decode(base.read('checkpoints/clip_%04d.json' % scene))
        metadata.update(format='h3_chain_segment_v3', run_name='demo')
        segment = metadata['segment']
        if scene == 1:
            segment.pop('predecessor_revision', None)  # old graph fixture uses a scene-zero sentinel
        token, operation = uuid.uuid4().hex, uuid.uuid4().hex
        if alternate:
            segment.update(take_kind='editorial_alternate', alternate_of_revision=segment['revision'])
        segment.update(revision=token, created_at='2026-09-11T00:00:00Z')
        segment['revision_metadata'] = 'h3_chains/demo/checkpoints/clip_%04d.%s.json' % (scene, token)
        files = {}
        with runtime_access(self.store) as runtime:
            for key, folder, ext in (('segment', 'segments', '.mp4'),
                    ('checkpoint', 'checkpoints', '.safetensors'),
                    ('generated_audio', 'generated_audio', '.wav'),
                    ('prompt_file', 'segments', '.prompt.txt')):
                source = runtime.reader.path(segment[key])
                files[key] = source
                segment[key] = 'h3_chains/demo/%s/clip_%04d.%s%s' % (folder, scene, token, ext)
        if alternate:
            metadata['archives'] = {}
            archives = None
        else:
            archives = {'plan': state._encode({'run_name': 'demo', 'shots': [{'id': segment['id'],
                'seed': segment.get('seed'), 'prompt': segment.get('scene_prompt_template', '')}]}),
                'workflow': b'{"nodes":[{"widgets_values":[Infinity,NaN]}]}'}
            metadata['archives'] = {key: 'h3_chains/demo/recovery_archives/'+token+'/'+key+'.json'
                                    for key in archives}
        return dict(metadata=metadata, files=files, archives=archives,
                    ownership_proof=self.proof, operation_id=operation)

    def test_base_accepts_all_files_archives_pointer_and_cut_together(self):
        cut = dict(replacements=[dict(scene=1, scene_id='scene_1', base_revision='1'*32,
            alternate_revision='e'*32)], locked_scene_ids=['scene_2'])
        self.store.commit(self.store.snapshot(), self.f.change('editorial.json', cut), operation_id=uuid.uuid4().hex)
        candidate = self.candidate()
        before = self.store.snapshot()
        observations = []
        with runtime_access(self.store, generation_writes=True) as runtime:
            runtime.generation.after_stage = lambda stage: observations.append(self.store.snapshot().reference)
            result = runtime.generation.publish(**candidate)
            self.assertEqual(result['storage_pin'], runtime.output_pin)
            self.assertEqual(runtime.reader.read(self.store.project/'checkpoints/clip_0001.json'),
                             state._decode(before.read('checkpoints/clip_0001.json')))
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertTrue(observations and all(ref == before.reference for ref in observations))
        self.assertEqual(state._decode(after.read('checkpoints/clip_0001.json')), candidate['metadata'])
        saved_cut = state._decode(after.read('editorial.json'))
        self.assertEqual(saved_cut['replacements'], [])
        self.assertEqual(saved_cut['locked_scene_ids'], ['scene_2'])
        self.assertEqual(after.read('workflow.json'), candidate['archives']['workflow'])
        self.assertEqual(after.read('checkpoints/clip_0002.json'), before.read('checkpoints/clip_0002.json'))
        self.assertEqual(self.store.verify_payloads(), 12)
        before.verify()

    def test_named_save_only_changes_its_own_assignments_and_authoring(self):
        candidate = self.candidate()
        before = self.store.snapshot()
        with runtime_access(self.store, selected=self.named, generation_writes=True) as runtime:
            runtime.generation.publish(**candidate)
        after = self.store.snapshot()
        for path in ('plan.json', 'workflow.json', 'checkpoints/clip_0001.json'):
            if path in before.state['documents']:
                self.assertEqual(after.read(path), before.read(path))
        self.assertEqual(state._decode(after.read('branches/'+self.named+'/checkpoints/clip_0001.json')),
                         candidate['metadata'])

    def test_alt_preserves_base_pointer_editorial_and_root_archives(self):
        candidate = self.candidate(alternate=True)
        before = self.store.snapshot()
        with runtime_access(self.store, generation_writes=True) as runtime:
            runtime.generation.publish(**candidate)
        after = self.store.snapshot()
        for name, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][name], descriptor)
        with runtime_access(self.store) as runtime:
            path = runtime.reader.path(candidate['metadata']['segment']['segment'])
            self.assertIn('/media/alternate/', str(path))
        self.assertEqual(self.store.verify_payloads(), 12)

    def test_alt_wrong_base_or_duration_is_rejected_before_staging(self):
        for key, value in (('alternate_of_revision', 'f'*32), ('raw_frames', 1)):
            candidate = self.candidate(alternate=True)
            candidate['metadata']['segment'][key] = value
            before = self.store.snapshot().reference
            with runtime_access(self.store, generation_writes=True) as runtime:
                with self.assertRaises((ValueError, state.StateConflict)):
                    runtime.generation.publish(**candidate)
            self.assertEqual(before, self.store.snapshot().reference)

    def test_grants_and_current_ownership_are_required(self):
        candidate = self.candidate()
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True) as runtime:
            with self.assertRaisesRegex(ValueError, 'generation writes'):
                runtime.generation.publish(**candidate)
        with runtime_access(self.store, generation_writes=True) as runtime:
            with self.assertRaises(ownership.ProjectOwnershipError):
                runtime.generation.publish(**dict(candidate, ownership_proof=None))
        self.assertEqual(before, self.store.snapshot().reference)

    def test_takeover_during_staging_prevents_acceptance(self):
        candidate = self.candidate()
        before = self.store.snapshot().reference
        with runtime_access(self.store, generation_writes=True, ownership_writes=True) as runtime:
            changed = []
            def takeover(stage):
                if stage == 'payload' and not changed:
                    changed.append(ownership.claim_project_ownership(self.output, 'demo',
                        'generation-new-owner-12345', force=True))
            runtime.generation.after_stage = takeover
            with self.assertRaises(ownership.ProjectOwnershipError):
                runtime.generation.publish(**candidate)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.store.verify_payloads(), 8)

    def test_scope_race_rejected_unrelated_branch_allowed(self):
        for selected, allowed in (('main', False), (self.named, True)):
            candidate = self.candidate()
            with runtime_access(self.store, generation_writes=True) as runtime:
                address = ('' if selected == 'main' else 'branches/'+selected+'/')+'plan.json'
                self.store.commit(self.store.snapshot(), self.f.change(address, {'separate': True}),
                                  operation_id=uuid.uuid4().hex)
                before = self.store.snapshot().reference
                if allowed:
                    runtime.generation.publish(**candidate)
                else:
                    with self.assertRaises(state.StateConflict):
                        runtime.generation.publish(**candidate)
                    self.assertEqual(before, self.store.snapshot().reference)

    def test_interruption_before_acceptance_can_retry_exact_staging(self):
        candidate = self.candidate()
        before = self.store.snapshot().reference
        with runtime_access(self.store, generation_writes=True) as runtime:
            pin = runtime.pin
            def stop(stage):
                if stage == 'document':
                    raise OSError('scene commit interrupted')
            runtime.generation.after_stage = stop
            with self.assertRaises(OSError):
                runtime.generation.publish(**candidate)
        self.assertEqual(before, self.store.snapshot().reference)
        with runtime_access(self.store, pin=pin, generation_writes=True) as runtime:
            result = runtime.generation.publish(**candidate)
        self.assertEqual(result['storage_pin']['root'], self.store.snapshot().reference)

    def test_lost_ack_retry_acknowledges_one_original_commit(self):
        candidate = self.candidate()
        real = self.store._publish
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost scene acknowledgement')
        with runtime_access(self.store, generation_writes=True) as runtime:
            pin = runtime.pin
            with patch.object(self.store, '_publish', lost), self.assertRaises(OSError):
                runtime.generation.publish(**candidate)
        accepted = self.store.snapshot().reference
        with runtime_access(self.store, generation_writes=True, pin=pin) as runtime:
            result = runtime.generation.publish(**candidate)
        self.assertEqual(result['storage_pin']['root'], accepted)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_wrong_payload_hash_and_foreign_address_do_not_publish(self):
        for key, value in (('segment_sha256', '0'*64), ('segment', 'h3_chains/other/video.mp4')):
            candidate = self.candidate()
            candidate['metadata']['segment'][key] = value
            before = self.store.snapshot().reference
            with runtime_access(self.store, generation_writes=True) as runtime:
                with self.assertRaises((ValueError, state.StateConflict)):
                    runtime.generation.publish(**candidate)
            self.assertEqual(before, self.store.snapshot().reference)

    def test_visual_and_audio_lead_hashes_use_the_saved_field_names(self):
        for kind in ('visual', 'audio'):
            candidate = self.candidate(scene=2)
            candidate['metadata']['segment'].update({
                kind+'_context_lead_source_scene': 1,
                kind+'_context_lead_source_revision': '1'*32,
                kind+'_context_lead_checkpoint_sha256': 'f'*64,
            })
            before = self.store.snapshot().reference
            with runtime_access(self.store, generation_writes=True) as runtime:
                with self.assertRaisesRegex(state.StateConflict, 'checkpoint hash'):
                    runtime.generation.publish(**candidate)
            self.assertEqual(before, self.store.snapshot().reference)

    def test_declared_reference_cannot_be_saved_without_checked_adoption(self):
        candidate = self.candidate()
        candidate['metadata']['segment']['reference_cache'] = {'metadata':'h3_chains/demo/reference_cache/missing.json'}
        before = self.store.snapshot().reference
        with runtime_access(self.store, generation_writes=True) as runtime:
            with self.assertRaisesRegex(ValueError, 'checked adoption'):
                runtime.generation.publish(**candidate)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_branch_switch_inside_runtime_cannot_redirect_save(self):
        candidate = self.candidate()
        with runtime_access(self.store, generation_writes=True) as runtime, branch_scope('demo', self.named):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                runtime.generation.publish(**candidate)

    def test_node_grant_stamps_result_and_is_not_inherited_by_helpers(self):
        candidate = self.candidate()
        @scoped_node
        def save(state):
            from storage_runtime import current_runtime
            current_runtime(self.output, 'demo').generation.publish(**candidate)
            return (dict(state, saved=True),)
        with runtime_access(self.store) as runtime:
            incoming = dict(run_name='demo', _branch_id='main', _project_ownership=self.proof,
                            **{PIN_KEY: runtime.pin})
        with node_host(self.store, branch_writers=(save,)):
            with self.assertRaisesRegex(ValueError, 'generation writes'):
                save(incoming)
        @scoped_node
        def helper(state):
            from storage_runtime import current_runtime
            current_runtime(self.output, 'demo').generation.publish(**candidate)
        @scoped_node
        def outer(state):
            helper(state)
        with node_host(self.store, generation_writers=(outer,)):
            with self.assertRaisesRegex(ValueError, 'generation writes'):
                outer(incoming)
        with node_host(self.store, generation_writers=(save,)):
            result = save(incoming)[0]
        self.assertEqual(result[PIN_KEY]['root'], self.store.snapshot().reference)
        self.assertNotEqual(incoming[PIN_KEY]['root'], result[PIN_KEY]['root'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
