"""Real processing saves and PNG exports through typed quarantine and undo."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from PIL import Image

import _storage_png_sequence_integration_test as fixture

chain, upscale, runtime, state = fixture.chain, fixture.upscale, fixture.runtime, fixture.state
module = fixture.fixture.fixture.fixture.module
carriers = fixture.carriers
manager_type = module('processing_checkpoint_delete').ProcessingCheckpointManager
retention = module('storage_retention')


class ProcessingRetentionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SequenceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.incoming = self.f.store, self.f.incoming
        self.output, self.run, self.proof = self.f.f.f.output, self.f.f.f.run, self.f.f.f.proof
        self.manager = manager_type(self.output)

    def saved(self, index=1, *, export=True):
        incoming = self.f.next_state(index)
        result = self.f.export(incoming, unique_id='png-'+uuid.uuid4().hex) if export else None
        images = fixture.fixture.fixture.torch.from_numpy(
            self.f.f.pixels[index].astype(fixture.fixture.np.float32)/65535)
        with carriers.node_host(self.store, processing_writers=(upscale.MiniMaxH3ChainUpscaleSegmentSave.save,)):
            segment = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(incoming, images)['result'][0]
        return incoming, segment, result

    def preview(self, segment):
        with runtime.runtime_access(self.store):
            return self.manager.deletion_preview(self.run, segment['revision_metadata'])

    def delete(self, segment, preview):
        return self.manager.delete(self.run, segment['revision_metadata'], preview['snapshot'], ownership_proof=self.proof)

    def test_take_png_frames_pointer_and_indexes_quarantine_together_and_undo_is_exact(self):
        incoming, segment, result = self.saved()
        address, index = self.f.record(result)
        before = self.store.snapshot()
        preview = self.preview(segment)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertIn(address, preview['control_updates'])
        self.assertTrue(any(item['path'].endswith('.png') for item in preview['files']))
        observed = []
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            bound.retention.after_stage = lambda phase: observed.append(self.store.snapshot().reference)
            deleted = self.delete(segment, preview)
        after = self.store.snapshot()
        self.assertEqual(deleted['reclaimed_bytes'], 0)
        self.assertTrue(all(root in (before.reference, after.reference) for root in observed))
        tombstone = state._decode(after.read(address))
        self.assertEqual(tombstone['deleted_scenes'], [1])
        self.assertEqual(tombstone['clips'], [])
        self.assertFalse(tombstone['complete'])
        prefix = 'h3_chains/'+self.run+'/'
        self.assertNotIn(segment['revision_metadata'].removeprefix(prefix), after.state['documents'])
        self.assertNotIn(segment['metadata'].removeprefix(prefix), after.state['documents'])
        for item in preview['files']:
            if item['exists']:
                logical = item['path'].removeprefix(prefix)
                key = logical if logical in before.state['documents'] else module('storage_project').payload_key(logical)
                self.assertNotIn(key, after.state['documents'])
                before.read(key)
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            undo = bound.retention.preview_undo(deleted['operation_id'])
            self.assertTrue(undo['allowed'], undo['blockers'])
            bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        restored = self.store.snapshot()
        for key, descriptor in before.state['documents'].items():
            self.assertEqual(restored.state['documents'][key], descriptor)
            self.assertEqual(restored.read(key), before.read(key))
        self.store.verify_payloads()

    def test_separate_permission_ownership_and_fresh_preview_are_required(self):
        incoming, segment, result = self.saved()
        preview = self.preview(segment)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, processing_writes=True), self.assertRaisesRegex(ValueError, 'retention writes'):
            self.delete(segment, preview)
        with runtime.runtime_access(self.store, retention_writes=True), self.assertRaises(module('project_ownership').ProjectOwnershipError):
            self.manager.delete(self.run, segment['revision_metadata'], preview['snapshot'])
        self.assertEqual(self.store.snapshot().reference, before)
        with runtime.runtime_access(self.store, retention_writes=True):
            self.store.commit(self.store.snapshot(), {'project_notes/'+uuid.uuid4().hex+'.json':dict(
                data=b'{}', scope='archive:other', category='legacy', immutable=True)}, operation_id=uuid.uuid4().hex)
            changed = self.store.snapshot().reference
            with self.assertRaises(state.StateConflict):
                self.delete(segment, preview)
        self.assertEqual(self.store.snapshot().reference, changed)

    def test_png_index_edits_block_undo_without_overwriting_later_work(self):
        incoming, segment, result = self.saved()
        address, _ = self.f.record(result)
        preview = self.preview(segment)
        with runtime.runtime_access(self.store, retention_writes=True):
            deleted = self.delete(segment, preview)
        base = self.store.snapshot()
        index = state._decode(base.read(address))
        index['later_note'] = 'Later user edit'
        descriptor = base.state['documents'][address]
        self.store.commit(base, {address:dict(data=state._encode(index),
            **{key:descriptor[key] for key in ('scope', 'category', 'immutable')})}, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            undo = bound.retention.preview_undo(deleted['operation_id'])
            self.assertFalse(undo['allowed'])
            with self.assertRaises(module('checkpoint_manager').CheckpointDeleteBlocked):
                bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_failed_quarantine_and_lost_ack_retry_keep_exact_files_and_tombstones(self):
        incoming, segment, result = self.saved()
        preview, before = self.preview(segment), self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            def fail(phase):
                if phase == 'root':
                    raise OSError('processing quarantine interrupted')
            bound.retention.after_stage = fail
            with self.assertRaisesRegex(OSError, 'interrupted'):
                self.delete(segment, preview)
        self.assertEqual(self.store.snapshot().reference, before)
        original = self.store._publish
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('lost processing quarantine ack')
        with runtime.runtime_access(self.store, retention_writes=True), patch.object(self.store, '_publish', lose), \
                self.assertRaisesRegex(OSError, 'lost processing'):
            self.delete(segment, preview)
        accepted = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True):
            repeated = self.delete(segment, preview)
        self.assertEqual(repeated['storage_pin']['root'], accepted)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_repeated_take_shares_png_owner_until_last_revision_is_quarantined(self):
        incoming, first, result = self.saved()
        incoming, second, reused = self.saved()
        self.assertEqual(first['png_export_owner'], second['png_export_owner'])
        address, original = self.f.record(result)
        preview = self.preview(first)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertFalse(any(item['path'].endswith('.png') for item in preview['files']))
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(first, preview)
        self.assertEqual(state._decode(self.store.snapshot().read(address)), original)
        preview = self.preview(second)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertTrue(any(item['path'].endswith('.png') for item in preview['files']))
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(second, preview)
        self.assertEqual(state._decode(self.store.snapshot().read(address))['clips'], [])

    def test_new_export_after_cleanup_forks_without_resurrecting_old_frames(self):
        incoming, segment, result = self.saved()
        address, _ = self.f.record(result)
        preview = self.preview(segment)
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(segment, preview)
        new = self.f.export(self.f.next_state(1), unique_id='after-cleanup')
        new_address, new_index = self.f.record(new)
        self.assertNotEqual(new_address, address)
        self.assertEqual(state._decode(self.store.snapshot().read(address))['clips'], [])
        self.assertEqual(new_index['clips'][0]['index'], 1)
        self.assertEqual(new_index['clips'][0]['first_frame_number'], 101)

    def test_imported_immutable_index_and_variant_recipe_are_replaced_and_undo_restores_bytes(self):
        incoming, segment, result = self.saved()
        _, record = self.f.record(result)
        # Import exact exporter bytes under the historical immutable-control
        # contract. Payload staging makes independent copies, never hardlinks.
        directory = 'upscaled/new-pixels/frames/imported_2'
        export, marker = directory+'/export.json', directory+'/.png_variant.json'
        marker_value = dict(format=module('png_export_variants').FORMAT, settings=record['settings'],
                            prefix=copy.deepcopy(record), first_changed_scene=1, reason='Imported test variant')
        raw = {export:state._encode(record), marker:state._encode(marker_value)}
        staged = []
        for item in record['clips'][0]['files']:
            staged.append(self.store.stage_payload(directory+'/'+item['file'],
                Path(result['result'][0])/item['file'], 'exports/png/'+uuid.uuid4().hex+'/'+item['file'],
                scope='exports:main', operation_id=uuid.uuid4().hex))
        self.store.commit_artifacts(self.store.snapshot(), {address:dict(data=data,
            scope='exports:main', category='passes', immutable=True) for address,data in raw.items()},
            staged, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot()
        preview = self.preview(segment)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertTrue({export, marker} <= set(preview['control_updates']))
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            deleted = self.delete(segment, preview)
        after = self.store.snapshot()
        self.assertEqual(state._decode(after.read(export))['clips'], [])
        self.assertIsNone(state._decode(after.read(marker))['prefix'])
        for address, data in raw.items():
            self.assertEqual(before.read(address), data)
            self.assertTrue(after.state['documents'][address]['immutable'])
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            undo = bound.retention.preview_undo(deleted['operation_id'])
            self.assertTrue(undo['allowed'], undo['blockers'])
            bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        restored = self.store.snapshot()
        for address in before.state['documents']:
            self.assertEqual(restored.state['documents'][address], before.state['documents'][address])
        self.store.verify_payloads()

    def test_distinct_png_owners_keep_shared_frames_until_both_takes_are_removed(self):
        incoming, first, result = self.saved()
        self.f.incoming = self.f.next_state(1)
        self.f.incoming['png_export_session'] = uuid.uuid4().hex
        incoming, second, reused = self.saved()
        self.assertNotEqual(first['png_export_owner'], second['png_export_owner'])
        address, original = self.f.record(result)
        self.assertEqual(len(original['clips'][0]['processing_owners']), 2)
        preview = self.preview(first)
        self.assertFalse(any(item['path'].endswith('.png') for item in preview['files']))
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(first, preview)
        updated = state._decode(self.store.snapshot().read(address))
        self.assertEqual(updated['clips'][0]['processing_owners'], [second['png_export_owner']])
        self.assertEqual(updated['clips'][0]['files'], original['clips'][0]['files'])
        self.store.verify_payloads()
        preview = self.preview(second)
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(second, preview)
        self.assertEqual(state._decode(self.store.snapshot().read(address))['clips'], [])

    def test_independent_pixel_successor_and_its_numbered_pngs_survive_prefix_deletion(self):
        incoming, first, result = self.saved()
        images = fixture.fixture.fixture.torch.from_numpy(
            self.f.f.pixels[1].astype(fixture.fixture.np.float32)/65535)
        self.f.incoming = self.f.f.f.handoff(incoming, first, images=images)
        incoming, second, result = self.saved(2)
        address, original = self.f.record(result)
        survivor = copy.deepcopy(original['clips'][1])
        before = self.store.snapshot()
        preview = self.preview(first)
        self.assertTrue(preview['allowed'], preview['blockers'])
        self.assertIn(second['revision'], {item['revision'] for item in preview['retained_independent_takes']})
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(first, preview)
        after = self.store.snapshot()
        self.assertEqual(state._decode(after.read(address))['clips'], [survivor])
        prefix = 'h3_chains/'+self.run+'/'
        second_metadata = second['revision_metadata'].removeprefix(prefix)
        self.assertEqual(after.read(second_metadata), before.read(second_metadata))
        for item in survivor['files']:
            logical = address.removesuffix('/export.json')+'/'+item['file']
            self.assertEqual(self.store.payload_path(after, logical, verify=True).read_bytes(),
                             self.store.payload_path(before, logical, verify=True).read_bytes())
        self.store.verify_payloads()

    def test_named_branch_deletion_and_undo_cannot_touch_main_or_borrow_its_grant(self):
        incoming, main, result = self.saved()
        with runtime.runtime_access(self.store, branch_writes=True):
            named = chain.WorkingBranches(self.output, self.run).create('main', 'Separate processing',
                {'plan_json':json.dumps({'shots':[{'id':s['id']} for s in self.f.f.f.f.f.plan['shots']]})},
                through_scene=2)['id']
        self.f.incoming = self.f.f.f.incoming(source=dict(self.f.f.f.f.f.manifest, _branch_id=named))
        incoming, segment, result = self.saved()
        before = self.store.snapshot()
        with runtime.runtime_access(self.store), self.assertRaisesRegex(ValueError, 'another working branch'):
            self.manager.deletion_preview(self.run, segment['revision_metadata'])
        with runtime.runtime_access(self.store, selected=named, retention_writes=True) as bound:
            preview = self.manager.deletion_preview(self.run, segment['revision_metadata'])
            self.assertTrue(preview['allowed'], preview['blockers'])
            deleted = self.delete(segment, preview)
        after = self.store.snapshot()
        for address, descriptor in before.state['documents'].items():
            if not address.startswith(('branches/'+named+'/', '__storage__/')):
                self.assertEqual(after.state['documents'].get(address), descriptor)
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            with self.assertRaisesRegex(ValueError, 'another retention domain or branch'):
                bound.retention.preview_undo(deleted['operation_id'])
        with runtime.runtime_access(self.store, selected=named, retention_writes=True) as bound:
            undo = bound.retention.preview_undo(deleted['operation_id'])
            bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        self.store.verify_payloads()

    def test_derope_take_is_protected_when_a_later_pixel_pass_uses_it_as_source(self):
        source = self.f.f.f.f
        selection = source.derope['recovered-av']
        with runtime.runtime_access(self.store):
            manifest = module('deferred_checkpoint_source').derope_source_manifest(
                source.f.manifest, selection, chain, upscale)
        incoming = self.f.f.f.incoming(source=manifest, profile='from-derope')
        child = self.f.f.f.save(incoming)['result'][0]
        with runtime.runtime_access(self.store):
            preview = self.manager.deletion_preview(self.run, selection['branch']['path'])
        self.assertFalse(preview['allowed'])
        self.assertIn(child['revision_metadata'], {item['metadata_path'] for item in preview['dependents']})
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True):
            with self.assertRaises(module('checkpoint_manager').CheckpointDeleteBlocked):
                self.manager.delete(self.run, selection['branch']['path'], preview['snapshot'], ownership_proof=self.proof)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_actual_http_preview_delete_and_undo_keep_ownership_and_conflict_status(self):
        incoming, segment, result = self.saved()
        body = dict(run_name=self.run, metadata_path=segment['revision_metadata'])
        def call(path, payload=body, *, proof=True, undo=False):
            async def content():
                return payload
            headers = {} if not proof else {'X-H3-Workflow-Owner':self.proof['owner_id'],
                                           'X-H3-Ownership-Epoch':str(self.proof['epoch'])}
            request = SimpleNamespace(path=path, json=content, headers=headers, query={}, method='POST')
            handler = chain._checkpoint_retention_undo if undo else chain._processing_checkpoint_deletion
            response = asyncio.run(handler(request))
            return response.status, json.loads(response.body)
        with runtime.runtime_access(self.store):
            status, preview = call('/delete-preview')
        self.assertEqual(status, 200, preview)
        body['snapshot'] = preview['snapshot']
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True):
            self.assertEqual(call('/delete', proof=False)[0], 423)
            self.assertEqual(self.store.snapshot().reference, before)
            status, deleted = call('/delete')
            self.assertEqual(status, 200, deleted)
        undo_body = dict(run_name=self.run, operation_id=deleted['operation_id'])
        with runtime.runtime_access(self.store):
            status, preview = call('/undo-preview', undo_body, undo=True)
            self.assertEqual(status, 200, preview)
        undo_body['snapshot'] = preview['snapshot']
        with runtime.runtime_access(self.store, retention_writes=True):
            self.assertEqual(call('/undo', undo_body, proof=False, undo=True)[0], 423)
            status, restored = call('/undo', undo_body, undo=True)
            self.assertEqual(status, 200, restored)
        self.store.verify_payloads()

    def test_owned_edited_png_deletes_and_undo_preserves_edit_without_rewriting_output_hash(self):
        incoming, segment, result = self.saved()
        address, record = self.f.record(result)
        frame = record['clips'][0]['files'][0]['file']
        logical = address.removesuffix('/export.json')+'/'+frame
        before = self.store.snapshot()
        path = Path(result['result'][0])/frame
        # Actual decodable replacement PNG, not a bypass of decoder/integrity checks.
        Image.new('RGB', (32,32), (230,80,15)).save(path)
        edited = path.read_bytes()
        preview = self.preview(segment)
        self.assertEqual(preview['payload_conditions'][0]['status'], 'edited')
        self.assertTrue(preview['allowed'])
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            deleted = self.delete(segment, preview)
            undo = bound.retention.preview_undo(deleted['operation_id'])
            self.assertIn('does not recover', undo['warning'])
            bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        after = self.store.snapshot()
        self.assertEqual(after.read(address), before.read(address))
        key = module('storage_project').payload_key(logical)
        self.assertEqual(after.read(key), before.read(key))
        self.assertEqual(path.read_bytes(), edited)
        with self.assertRaises(state.StateConflict):
            self.store.verify_payloads()

    def test_missing_owned_png_and_checkpoint_are_retired_but_undo_does_not_recover_lost_bytes(self):
        incoming, segment, result = self.saved()
        address, record = self.f.record(result)
        frame = record['clips'][0]['files'][0]['file']
        before = self.store.snapshot()
        paths = [Path(result['result'][0])/frame, self.store.payload_path(before,
            segment['checkpoint'].removeprefix('h3_chains/'+self.run+'/'))]
        for path in paths:
            path.unlink()  # Simulated damage to disposable owned fixtures only.
        preview = self.preview(segment)
        self.assertTrue(preview['allowed'])
        self.assertEqual([item['status'] for item in preview['payload_conditions']], ['missing', 'missing'])
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            deleted = self.delete(segment, preview)
            self.store.verify_payloads()  # Active catalogue contains no stale missing entries.
            undo = bound.retention.preview_undo(deleted['operation_id'])
            self.assertEqual(len(undo['payload_conditions']), 2)
            bound.retention.undo(deleted['operation_id'], undo['snapshot'], proof=self.proof)
        after = self.store.snapshot()
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_changed_png_after_preview_requires_reconfirmation_and_never_touches_bytes(self):
        incoming, segment, result = self.saved()
        path = Path(result['result'][0])/'frame_00000101.png'
        path.write_bytes(path.read_bytes()+b'first user edit')
        preview, before = self.preview(segment), self.store.snapshot().reference
        path.write_bytes(path.read_bytes()+b'later user edit')
        changed = path.read_bytes()
        with runtime.runtime_access(self.store, retention_writes=True):
            with self.assertRaises(module('checkpoint_manager').CheckpointDeleteBlocked):
                self.delete(segment, preview)
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(path.read_bytes(), changed)
        refreshed = self.preview(segment)
        self.assertNotEqual(refreshed['snapshot'], preview['snapshot'])
        with runtime.runtime_access(self.store, retention_writes=True):
            self.delete(segment, refreshed)
        self.assertEqual(path.read_bytes(), changed)

    def test_corrupt_non_png_payload_is_not_silently_treated_as_deliberate_pixel_edit(self):
        incoming, segment, result = self.saved()
        before = self.store.snapshot()
        path = self.store.payload_path(before, segment['checkpoint'].removeprefix('h3_chains/'+self.run+'/'))
        path.write_bytes(path.read_bytes()+b'corrupt checkpoint')
        with self.assertRaises(state.StateConflict):
            self.preview(segment)
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_reverse_recovery_preserves_png_tombstone_and_never_resurrects_deleted_take(self):
        incoming, segment, result = self.saved()
        address, record = self.f.record(result)
        preview = self.preview(segment)
        with runtime.runtime_access(self.store, retention_writes=True):
            deleted = self.delete(segment, preview)
        source = self.f.f.f.f
        receipt = source.lab/'retired-processing-copy.json'
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(source.source), independent_copies=True))
        recovery = module('storage_recovery')
        output = source.lab/'retired-processing-recovered'
        journal = recovery.prepare_legacy_copy(receipt, output, source.lab/'retired-processing-recovery',
                                               rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/self.run
        prefix = 'h3_chains/'+self.run+'/'
        for item in preview['files']:
            self.assertFalse((root/item['path'].removeprefix(prefix)).exists())
        self.assertEqual(json.loads((root/address).read_bytes())['deleted_scenes'], [1])
        self.assertEqual(json.loads((root/address).read_bytes())['clips'], [])
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))

    def test_frame_inventory_and_undo_do_not_copy_entire_catalogue_per_item(self):
        incoming, segment, result = self.saved()
        getter = state.Snapshot.state.fget
        base = self.store.snapshot()
        calls = []
        def counted(snapshot):
            if snapshot is base:
                calls.append(snapshot.reference)
            return getter(snapshot)
        with runtime.runtime_access(self.store) as bound, patch.object(state.Snapshot, 'state', property(counted)):
            preview = bound.retention._build_processing(segment['revision_metadata'], base)[0]
        self.assertLessEqual(len(calls), 6, 'Preview copied the whole catalogue once per owned file')
        with runtime.runtime_access(self.store, retention_writes=True):
            deleted = self.delete(segment, preview)
        base = self.store.snapshot()
        calls.clear()
        with runtime.runtime_access(self.store) as bound, patch.object(state.Snapshot, 'state', property(counted)):
            undo = bound.retention._undo_preview(deleted['operation_id'], base)
        self.assertTrue(undo['allowed'])
        self.assertLessEqual(len(calls), 2, 'Undo copied the whole catalogue once per retired file')


if __name__ == '__main__':
    unittest.main(argv=[__file__])
