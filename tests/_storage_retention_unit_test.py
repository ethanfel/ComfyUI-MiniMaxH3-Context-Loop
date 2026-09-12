"""Actual generation deletion manager and HTTP handlers, ownership and undo."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_restore_routes_unit_test as fixture
import storage_retention as retention
from storage_runtime import runtime_access
from checkpoint_manager import CheckpointGraphManager, CheckpointDeleteBlocked, checkpoint_run_lock
import storage_state as state
import project_ownership as ownership
import storage_carriers as carriers


class RetentionTests(unittest.TestCase):
    setUp = fixture.RestoreTests.setUp
    alternative = fixture.RestoreTests.alternative
    change = fixture.RestoreTests.change

    def preview(self, revision, scene=2):
        with runtime_access(self.store):
            return CheckpointGraphManager(self.output).deletion_preview('demo', scene, revision)

    def delete(self, revision, preview, *, proof=True, scene=2):
        return CheckpointGraphManager(self.output).delete('demo', scene, revision, preview['snapshot'],
            ownership_proof=self.proof if proof is True else proof)

    def graph(self):
        with runtime_access(self.store):
            return CheckpointGraphManager(self.output).graph('demo', adopt_legacy=False)

    def commit(self, address, value):
        base = self.store.snapshot()
        descriptor = base.state['documents'].get(address)
        contract = ({key:descriptor[key] for key in ('scope', 'category', 'immutable')} if descriptor else
                    dict(scope='archive:'+uuid.uuid4().hex, category='takes', immutable=True))
        self.store.commit(base, {address:dict(data=state._encode(value), **contract)}, operation_id=uuid.uuid4().hex)

    def test_owned_leaf_quarantine_and_undo_restore_graph_bytes_and_exact_descriptors(self):
        revision = self.alternative(2)
        before, graph = self.store.snapshot(), self.graph()
        preview = self.preview(revision)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertEqual(preview['reclaimed_bytes'], 0)
        self.assertGreater(preview['quarantined_bytes'], 0)
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(revision, preview)
        self.assertTrue(result['undo_available'])
        self.assertEqual(result['quarantined_files'], 5)
        self.assertNotIn(revision, {r['revision'] for r in self.graph()['revisions']})
        for item in preview['files']:
            if item['owned']:
                address = item['path'].removeprefix('h3_chains/demo/')
                key = address if address in before.state['documents'] else retention.ProjectQuarantine(self.store).preview(
                    before, [address], reason='read-only proof')['items'][0]['key']
                self.assertNotIn(key, self.store.snapshot().state['documents'])
                before.read(key)
        with runtime_access(self.store, retention_writes=True) as runtime:
            undo = runtime.retention.preview_undo(result['operation_id'])
            self.assertTrue(undo['allowed'], undo['blockers'])
            runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        self.assertEqual(self.graph(), graph)
        after = self.store.snapshot()
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
            self.assertEqual(after.read(address), before.read(address))

    def test_separate_permission_and_current_ownership_are_required(self):
        revision = self.alternative(2)
        preview, before = self.preview(revision), self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True, generation_writes=True), self.assertRaisesRegex(ValueError, 'retention writes'):
            self.delete(revision, preview)
        with runtime_access(self.store, retention_writes=True), self.assertRaises(ownership.ProjectOwnershipError):
            self.delete(revision, preview, proof=None)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_unrelated_commit_invalidates_confirmed_preview_without_rebase(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        with runtime_access(self.store, retention_writes=True):
            self.commit('branches/'+self.named+'/plan.json', {'new_reference': revision})
            before = self.store.snapshot().reference
            with self.assertRaises(state.StateConflict):
                self.delete(revision, preview)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_other_working_branch_and_required_context_prevent_deletion(self):
        preview = self.preview('1'*32, 1)
        self.assertFalse(preview['allowed'])
        self.assertTrue({'selected_assignment', 'required_input'} <= {r['kind'] for r in preview['references']})
        self.assertTrue(any(self.named in r['source'] for r in preview['references']))

    def test_selected_alternate_blocks_until_explicitly_unselected(self):
        revision = self.alternative(2, take_kind='editorial_alternate', alternate_of_revision='2'*32,
                                    alternate_media_mode='picture_only')
        self.commit('editorial.json', {'replacements':[dict(scene=2, base_revision='2'*32, alternate_revision=revision)]})
        preview = self.preview(revision)
        self.assertFalse(preview['allowed'])
        self.assertTrue(any('final-cut' in reason for reason in preview['blockers']))
        self.commit('editorial.json', {'replacements':[]})
        self.assertTrue(self.preview(revision)['allowed'])

    def test_processing_sources_block_but_opaque_prompts_and_uuid_collisions_do_not(self):
        revision = self.alternative(2)
        path = 'upscaled/pass/checkpoints/clip_0002.'+'a'*32+'.json'
        checkpoint = 'h3_chains/demo/checkpoints/clip_0002.'+revision+'.safetensors'
        self.commit(path, dict(format='h3_chain_upscale_segment_v1', run_name='demo',
            source_revision=revision, source_checkpoint=checkpoint, segment=dict(index=2, revision='a'*32)))
        self.assertFalse(self.preview(revision)['allowed'])
        self.assertTrue(any(r['kind'] == 'processing_dependency' for r in self.preview(revision)['references']))

    def test_history_and_unrelated_processing_are_not_permanent_false_blockers(self):
        revision = self.alternative(2)
        self.alternative(2, supersedes=revision)
        self.commit('upscaled/pass/checkpoints/clip_0002.'+revision+'.json',
            dict(format='h3_chain_upscale_segment_v1', run_name='demo',
                source_revision=revision, source_checkpoint='h3_chains/demo/upscaled/other/checkpoints/source.safetensors',
                segment=dict(index=2, revision=revision, checkpoint='h3_chains/demo/upscaled/pass/checkpoints/take.safetensors'),
                execution={'source_revision':revision}, prompt='h3_chains/demo/checkpoints/clip_0002.'+revision+'.json'))
        preview = self.preview(revision)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertTrue(any(r['kind'] == 'historical_only' and not r['blocking'] for r in preview['references']))

    def test_matching_processing_revision_with_invalid_source_address_is_protected(self):
        revision = self.alternative(2)
        for source in ('G:\\old-output\\clip.safetensors', '/old/output/clip.safetensors', {'path':'unknown'}):
            self.commit('upscaled/custom/checkpoints/clip_0002.'+uuid.uuid4().hex+'.json',
                dict(format='h3_chain_upscale_segment_v1', run_name='demo',
                     source_revision=revision, source_checkpoint=source, segment=dict(index=2)))
            preview = self.preview(revision)
            self.assertFalse(preview['allowed'])
            self.assertTrue(any(item['kind'] == 'unknown' for item in preview['references']))

    def test_windows_separators_keep_processing_dependency(self):
        revision = self.alternative(2)
        self.commit('upscaled/pass/checkpoints/clip_0002.'+uuid.uuid4().hex+'.json',
            dict(format='h3_chain_upscale_segment_v1', run_name='demo', source_revision=revision,
                 source_checkpoint='h3_chains\\demo\\checkpoints\\clip_0002.'+revision+'.safetensors',
                 segment=dict(index=2)))
        preview = self.preview(revision)
        self.assertFalse(preview['allowed'])
        self.assertTrue(any(item['kind'] == 'processing_dependency' for item in preview['references']))

    def test_attributed_alias_quarantines_only_owned_metadata_and_keeps_shared_media(self):
        parent = self.alternative(1)
        with runtime_access(self.store, branch_writes=True):
            alias = CheckpointGraphManager(self.output).attribute('demo', 1, parent, 2, '2'*32)['revision']
        before = self.store.snapshot()
        original = state._decode(before.read('checkpoints/clip_0002.json'))['segment']
        self.commit('upscaled/pass/checkpoints/clip_0002.'+uuid.uuid4().hex+'.json',
            dict(format='h3_chain_upscale_segment_v1', run_name='demo', source_revision='2'*32,
                 source_checkpoint=original['checkpoint'], segment=dict(index=2)))
        preview = self.preview(alias)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertEqual(preview['owned_file_count'], 1)
        self.assertTrue(any(item['kind'] == 'shared_artifact' for item in preview['references']))
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(alias, preview)
        self.assertEqual(result['quarantined_files'], 1)
        self.assertNotIn(alias, {row['revision'] for row in self.graph()['revisions']})
        for address, descriptor in before.state['documents'].items():
            if address != 'checkpoints/clip_0002.'+alias+'.json':
                self.assertEqual(self.store.snapshot().state['documents'][address], descriptor)

    def test_active_leaf_rolls_back_only_selected_branch_and_undo_restores_pointer(self):
        revision = self.alternative(2)
        document = state._decode(self.store.snapshot().read('checkpoints/clip_0002.'+revision+'.json'))
        self.commit('branches/'+self.named+'/checkpoints/clip_0002.json', document)
        preview = self.preview('2'*32)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertTrue(preview['rollback'])
        before = self.store.snapshot()
        with runtime_access(self.store, retention_writes=True):
            result = self.delete('2'*32, preview)
        after = self.store.snapshot()
        self.assertNotIn('checkpoints/clip_0002.json', after.state['documents'])
        self.assertEqual(after.read('checkpoints/clip_0003.json'), before.read('checkpoints/clip_0003.json'))
        self.assertEqual(after.read('branches/'+self.named+'/checkpoints/clip_0002.json'),
                         before.read('branches/'+self.named+'/checkpoints/clip_0002.json'))
        with runtime_access(self.store, retention_writes=True) as runtime:
            undo = runtime.retention.preview_undo(result['operation_id'])
            runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0002.json'), before.read('checkpoints/clip_0002.json'))

    def test_processing_alias_identity_is_protected_even_when_its_media_are_shared(self):
        parent = self.alternative(1)
        with runtime_access(self.store, branch_writes=True):
            alias = CheckpointGraphManager(self.output).attribute('demo', 1, parent, 2, '2'*32)['revision']
        original = state._decode(self.store.snapshot().read('checkpoints/clip_0002.json'))['segment']
        self.commit('upscaled/pass/checkpoints/clip_0002.'+uuid.uuid4().hex+'.json',
            dict(format='h3_chain_upscale_segment_v1', run_name='demo', source_revision=alias,
                 source_checkpoint=original['checkpoint'], segment=dict(index=2)))
        preview = self.preview(alias)
        self.assertFalse(preview['allowed'])
        self.assertTrue(any(item['kind'] == 'processing_dependency' for item in preview['references']))

    def test_lost_quarantine_ack_retries_without_new_publication_or_requarantine_after_undo(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        publish = self.store._publish
        def lose(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost acknowledgement')
        with runtime_access(self.store, retention_writes=True), patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.delete(revision, preview)
        before = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(revision, preview)
        self.assertEqual(self.store.snapshot().reference, before)
        with runtime_access(self.store, retention_writes=True) as runtime:
            undo = runtime.retention.preview_undo(result['operation_id'])
            runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        restored = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True):
            replayed = self.delete(revision, preview)
        self.assertFalse(replayed['undo_available'])
        self.assertEqual(replayed['storage_pin']['root'], restored)
        self.assertEqual(self.store.snapshot().reference, restored)

    def test_prepublication_failure_keeps_old_graph_and_retry_succeeds(self):
        revision = self.alternative(2)
        preview, before = self.preview(revision), self.store.snapshot().reference
        def fail(phase):
            if phase == 'root':
                raise OSError('interrupted quarantine')
        with runtime_access(self.store, retention_writes=True) as runtime:
            runtime.retention.after_stage = fail
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.delete(revision, preview)
        self.assertEqual(self.store.snapshot().reference, before)
        with runtime_access(self.store, retention_writes=True):
            self.delete(revision, preview)

    def test_undo_refuses_later_assignment_and_wrong_branch(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(revision, preview)
        with runtime_access(self.store, selected=self.named, retention_writes=True) as runtime, self.assertRaisesRegex(ValueError, 'another retention domain or branch'):
            runtime.retention.preview_undo(result['operation_id'])
        self.commit('checkpoints/clip_0002.'+revision+'.json', {'occupied':True})
        with runtime_access(self.store, retention_writes=True) as runtime:
            undo = runtime.retention.preview_undo(result['operation_id'])
            self.assertFalse(undo['allowed'])
            before = self.store.snapshot().reference
            with self.assertRaises(CheckpointDeleteBlocked):
                runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_undo_lost_ack_has_an_exact_retry_without_overwriting_later_work(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(revision, preview)
        publish = self.store._publish
        def lose(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost undo acknowledgement')
        with runtime_access(self.store, retention_writes=True) as runtime:
            undo = runtime.retention.preview_undo(result['operation_id'])
            with patch.object(self.store, '_publish', lose), self.assertRaisesRegex(OSError, 'lost undo'):
                runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        self.commit('branches/'+self.named+'/plan.json', {'later':True})
        before = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True) as runtime:
            repeated = runtime.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        self.assertEqual(repeated['storage_pin']['root'], before)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_takeover_after_preview_rejects_old_owner_before_any_retirement(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        with runtime_access(self.store, ownership_writes=True):
            ownership.claim_project_ownership(self.output, 'demo', fixture.fixture.B, force=True)
        before = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True), self.assertRaises(ownership.ProjectOwnershipError):
            self.delete(revision, preview)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_sealed_cut_is_a_typed_blocker_even_without_legacy_directories(self):
        revision = self.alternative(2)
        self.commit('chapters/01_first/manifests/'+uuid.uuid4().hex+'.json',
            dict(format='h3_chain_chapter_manifest_v1', run_name='demo', chapter=dict(number=1),
                 segments=[dict(index=2, revision=revision)]))
        preview = self.preview(revision)
        self.assertFalse(preview['allowed'])
        self.assertTrue(any(r['kind'] == 'frozen_cut_pin' for r in preview['references']))

    def test_unknown_processing_recovery_is_protected_not_declared_unreferenced(self):
        revision = self.alternative(2)
        self.commit('upscaled/custom/checkpoints/clip_0001.'+uuid.uuid4().hex+'.json',
                    dict(format='unknown_extension', run_name='demo'))
        preview = self.preview(revision)
        self.assertFalse(preview['allowed'])
        self.assertTrue(any(r['kind'] == 'unknown' for r in preview['references']))

    def test_undo_rechecks_payload_integrity_before_restoring_catalogue_entries(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        base = self.store.snapshot()
        with runtime_access(self.store, retention_writes=True):
            result = self.delete(revision, preview)
        path = self.store.payload_path(base, 'checkpoints/clip_0002.'+revision+'.safetensors')
        raw = path.read_bytes()
        path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        before = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True) as runtime, self.assertRaises(ValueError):
            runtime.retention.preview_undo(result['operation_id'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_nested_node_cannot_borrow_request_retention_grant(self):
        revision = self.alternative(2)
        preview = self.preview(revision)
        with carriers.node_host(self.store), runtime_access(self.store, retention_writes=True):
            with carriers.node_operation({'run_name':'demo'}, ('demo', 'main')):
                with self.assertRaisesRegex(ValueError, 'retention writes'):
                    self.delete(revision, preview)

    def test_actual_preview_delete_handlers_preserve_http_ownership_boundary(self):
        names = {'_preview_checkpoint_revision_deletion', '_delete_checkpoint_revision', '_checkpoint_retention_undo'}
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        methods = [item for item in ast.parse(source.read_text()).body
                   if isinstance(item, ast.AsyncFunctionDef) and item.name in names]
        self.assertEqual({item.name for item in methods}, names)
        ns = dict(self.ns, CheckpointDeleteBlocked=CheckpointDeleteBlocked, checkpoint_run_lock=checkpoint_run_lock)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), ns)
        revision = self.alternative(2)
        body = dict(run_name='demo', scene=2, revision=revision)
        def call(name, proof=True):
            request = fixture.fixture.request(body, self.proof if proof else None)
            request.path = body.get('path', '')
            return asyncio.run(ns[name](request))
        with runtime_access(self.store):
            result = call('_preview_checkpoint_revision_deletion')
            self.assertEqual(result['status'], 200, result)
        body['snapshot'] = result['body']['snapshot']
        before = self.store.snapshot().reference
        with runtime_access(self.store, retention_writes=True):
            self.assertEqual(call('_delete_checkpoint_revision', proof=False)['status'], 423)
            self.assertEqual(self.store.snapshot().reference, before)
            deleted = call('_delete_checkpoint_revision')
            self.assertEqual(deleted['status'], 200, deleted)
            self.assertTrue(deleted['body']['undo_available'])
        body = dict(run_name='demo', operation_id=deleted['body']['operation_id'], path='/undo-preview')
        with runtime_access(self.store):
            undo = call('_checkpoint_retention_undo')
            self.assertEqual(undo['status'], 200, undo)
        body.update(snapshot=undo['body']['snapshot'], path='/undo')
        with runtime_access(self.store, retention_writes=True):
            self.assertEqual(call('_checkpoint_retention_undo', proof=False)['status'], 423)
            restored = call('_checkpoint_retention_undo')
            self.assertEqual(restored['status'], 200, restored)
        self.assertIn(revision, {r['revision'] for r in self.graph()['revisions']})


if __name__ == '__main__':
    unittest.main(argv=[__file__])
