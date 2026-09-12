"""Normal VIDEO ExportPNG node: pinned append, reuse, forks and exact retries."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_png_exports_integration_test as fixture

chain, upscale, runtime, state = fixture.chain, fixture.upscale, fixture.runtime, fixture.state
carriers = fixture.fixture.carriers
sequence = fixture.fixture.fixture.module('storage_png_sequence')
function = chain.MiniMaxH3ChainExportPNG.export


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PNGExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.incoming = self.f.f.incoming()
        self.videos = self.f.videos
        self.namespace = uuid.uuid4().hex

    def next_state(self, index):
        incoming = copy.deepcopy(self.incoming)
        incoming['index'] = index
        for carrier in carriers._carriers(incoming):
            if '_storage_pin' in carrier:
                carrier['_storage_pin']['root'] = self.store.snapshot().reference
        return incoming

    def export(self, incoming=None, video=None, unique_id='png1', **options):
        incoming = incoming or self.incoming
        settings = dict(export_name='DLSS', first_frame_number=101, png_bit_depth='16', embed_workflow=False)
        settings.update(options)
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=self.namespace):
            return chain.MiniMaxH3ChainExportPNG().export(state=incoming, video=video or self.videos[incoming['index']],
                                                        unique_id=unique_id, **settings)

    def record(self, result):
        root = self.store.snapshot()
        addresses = [p for p in root.state['documents'] if p.endswith('/export.json')]
        for address in addresses:
            record = state._decode(root.read(address))
            if record.get('_storage_export_id') == Path(result['result'][0]).name:
                return address, record
        self.fail('Accepted sequence index was not found')

    def test_normal_node_appends_in_same_directory_without_copying_prior_frames(self):
        before_input = copy.deepcopy(self.incoming)
        first = self.export()
        old_root = self.store.snapshot()
        address, record = self.record(first)
        paths = {p:p.stat().st_ino for p in Path(first['result'][0]).glob('frame_*.png')}
        second = self.export(self.next_state(2), unique_id='png2')
        self.assertEqual(first['result'][0], second['result'][0])
        self.assertEqual(self.incoming, before_input)
        self.assertIs(first['result'][4], self.videos[1])
        self.assertIs(second['result'][4], self.videos[2])
        self.assertEqual(paths, {p:p.stat().st_ino for p in paths})
        after = self.store.snapshot()
        updated = state._decode(after.read(address))
        self.assertEqual([c['index'] for c in updated['clips']], [1,2])
        self.assertEqual(updated['clips'][1]['first_frame_number'], 101+record['frame_count'])
        self.assertEqual(second['result'][1], sum(s['delivered_frames'] for s in self.incoming['source_manifest']['segments']))
        for path, descriptor in old_root.state['documents'].items():
            if path.startswith('__storage__/'):
                self.assertEqual(after.state['documents'][path], descriptor)
        self.assertEqual(state._decode(old_root.read(address)), record)
        self.store.verify_payloads()

    def test_exact_node_retry_does_not_encode_or_advance_root(self):
        first = self.export()
        before = self.store.snapshot().reference
        with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('No reencode')):
            again = self.export()
        self.assertEqual(again['result'], first['result'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_changed_scene_creates_numbered_variant_with_independent_prefix(self):
        first = self.export()
        self.export(self.next_state(2), unique_id='png2')
        changed, _ = fixture.video_file(self.f.f.output/'changed-two.mkv', self.f.source['segments'][1]['raw_frames'], 19)
        result = self.export(self.next_state(2), video=changed, unique_id='changed2')
        address, record = self.record(result)
        self.assertTrue(address.endswith('/DLSS_2/export.json'))
        self.assertEqual([c['index'] for c in record['clips']], [1,2])
        self.assertNotEqual(first['result'][0], result['result'][0])
        left = Path(first['result'][0])/'frame_00000101.png'
        right = Path(result['result'][0])/'frame_00000101.png'
        self.assertEqual(left.read_bytes(), right.read_bytes())
        self.assertNotEqual(left.stat().st_ino, right.stat().st_ino)
        self.store.verify_payloads()

    def test_fresh_range_starts_at_two_without_exporting_one(self):
        incoming = self.f.f.incoming(start=2, mode='fresh_range')
        result = self.export(incoming)
        _, record = self.record(result)
        self.assertEqual([c['index'] for c in record['clips']], [2])
        self.assertEqual(record['clips'][0]['first_frame_number'], 101)

    def test_reuse_disabled_starts_variant_once_then_keeps_that_session(self):
        first = self.export()
        self.export(self.next_state(2), unique_id='png2')
        incoming = self.next_state(1)
        incoming['png_export_session'] = uuid.uuid4().hex
        self.incoming = incoming
        new = self.export(incoming, unique_id='new1', reuse_existing=False)
        final = self.export(self.next_state(2), unique_id='new2', reuse_existing=False)
        self.assertNotEqual(first['result'][0], new['result'][0])
        self.assertEqual(new['result'][0], final['result'][0])
        self.assertTrue(self.record(final)[0].endswith('/DLSS_2/export.json'))

    def test_prepared_interruption_and_restart_skip_encoder(self):
        operation = uuid.uuid4().hex
        before = self.store.snapshot().reference
        def workspace(bound):
            return sequence.PNGSequenceExport(bound, upscale, self.incoming, self.videos[1],
                operation=operation, export_name='direct', png_bit_depth=16)
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            w = workspace(bound)
            w.prepare()
            bound.exports.after_stage = lambda stage: (_ for _ in ()).throw(OSError('stop PNG'))
            with self.assertRaisesRegex(OSError, 'stop PNG'):
                w.publish()
        self.assertEqual(self.store.snapshot().reference, before)
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('No reencode')):
                w = workspace(bound)
                w.prepare()
                result = w.publish()
        self.assertTrue(Path(result['result'][0]).is_dir())
        self.store.verify_payloads()

    def test_ungranted_or_unidentified_video_and_checkpoint_exports_fail_closed(self):
        before = self.store.snapshot().reference
        with carriers.node_host(self.store):
            with self.assertRaisesRegex(ValueError, 'export writes'):
                chain.MiniMaxH3ChainExportPNG().export(state=self.incoming, video=self.videos[1])
        with carriers.node_host(self.store, export_writers=(function,)):
            with self.assertRaisesRegex(ValueError, 'operation ID'):
                chain.MiniMaxH3ChainExportPNG().export(state=self.incoming, video=self.videos[1])
            with self.assertRaisesRegex(ValueError, 'operation ID'):
                chain.MiniMaxH3ChainExportPNG().export(manifest=self.incoming['source_manifest'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_exact_reuse_attaches_owner_without_encoding_another_sequence(self):
        first = self.export()
        incoming = self.next_state(1)
        incoming['png_export_session'] = uuid.uuid4().hex
        with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('No reencode')):
            reused = self.export(incoming, unique_id='reuse')
        self.assertEqual(first['result'][0], reused['result'][0])
        record = self.record(reused)[1]
        self.assertEqual(len(record['clips'][0]['processing_owners']), 2)
        catalogue = state._decode(self.store.snapshot().read(sequence.owners.RUNTIME_CATALOG))
        self.assertEqual(catalogue['directories'], ['h3_chains/'+self.f.f.run+'/'+self.record(reused)[0].removesuffix('/export.json')])

    def test_source_manifest_ownership_is_accepted_but_explicit_denial_or_conflict_is_not(self):
        incoming = copy.deepcopy(self.incoming)
        proof = incoming.pop('_project_ownership')
        incoming['source_manifest']['_project_ownership'] = proof
        before = self.store.snapshot().reference
        denied = dict(incoming,_project_ownership=None)
        conflict = dict(incoming,_project_ownership=dict(owner_id='other',epoch=proof['epoch']))
        for candidate in (denied, conflict):
            with self.assertRaisesRegex(ValueError,'Conflicting node ownership proofs'):
                self.export(candidate,unique_id='denied')
            self.assertEqual(self.store.snapshot().reference,before)
        missing = copy.deepcopy(incoming)
        missing['source_manifest'].pop('_project_ownership')
        with self.assertRaises(fixture.ownership.ProjectOwnershipError):
            self.export(missing,unique_id='missing')
        self.assertEqual(self.store.snapshot().reference,before)
        original = copy.deepcopy(incoming)
        result = self.export(incoming,unique_id='nested-owner')
        self.assertEqual(incoming,original)
        self.assertEqual(self.record(result)[1]['clips'][0]['index'],1)

    def test_old_retry_after_append_keeps_current_index_and_returns_old_count(self):
        first = self.export()
        second = self.export(self.next_state(2), unique_id='png2')
        before = self.store.snapshot().reference
        with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('No reencode')):
            repeated = self.export()
        self.assertEqual(first['result'], repeated['result'])
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(self.record(second)[1]['last_scene'], 2)

    def test_concurrent_old_pin_cannot_replace_an_accepted_sequence(self):
        first = self.export()
        before = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.export(unique_id='competing-old-pin')
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(len(self.record(first)[1]['clips']), 1)

    def test_new_owner_fences_prepared_export_before_frame_staging(self):
        operation = uuid.uuid4().hex
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            w = sequence.PNGSequenceExport(bound, upscale, self.incoming, self.videos[1], operation=operation, export_name='owned')
            w.prepare()
            target = self.store.project/'exports/png'/w._prepared()['export_id']
        with runtime.runtime_access(self.store, ownership_writes=True):
            fixture.ownership.claim_project_ownership(self.f.f.output, self.f.f.run, 'new-sequence-png-owner', force=True)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            with self.assertRaises(fixture.ownership.ProjectOwnershipError):
                sequence.PNGSequenceExport(bound, upscale, self.incoming, self.videos[1], operation=operation, export_name='owned').publish()
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertFalse(target.exists())

    def test_modified_prepared_index_cannot_omit_payload_or_drop_dependency(self):
        operation = uuid.uuid4().hex
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            w = sequence.PNGSequenceExport(bound, upscale, self.incoming, self.videos[1], operation=operation, export_name='tamper')
            w.prepare()
            original = w._prepared()
            path = self.store.project/w.job/'sequence-prepared.json'
            for key in ('files', 'scopes'):
                changed = copy.deepcopy(original)
                if key == 'files':
                    changed['files'].pop(next(iter(changed['files'])))
                else:
                    changed['scopes'] = []
                state.atomic_json(path, dict(value=changed, sha256=state._hash(state._encode(changed))))
                with self.assertRaises(state.StateConflict):
                    w.publish()
            self.assertFalse((self.store.project/'exports/png'/original['export_id']).exists())

    def test_prefix_edit_during_append_is_detected_before_acceptance(self):
        first = self.export()
        incoming = self.next_state(2)
        before = self.store.snapshot().reference
        path = Path(first['result'][0])/'frame_00000101.png'
        original = path.read_bytes()
        def mutate(stage):
            if stage == 'payload':
                path.write_bytes(bytes([original[0]^1])+original[1:])
        with runtime.runtime_access(self.store, pin=incoming['_storage_pin'], export_writes=True) as bound:
            w = sequence.PNGSequenceExport(bound, upscale, incoming, self.videos[2], operation=uuid.uuid4().hex,
                export_name='DLSS', first_frame_number=101, png_bit_depth=16, embed_workflow=False)
            w.prepare()
            bound.exports.after_stage = mutate
            with self.assertRaises(state.StateConflict):
                w.publish()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_frame_changes_between_precheck_and_staging_cannot_enter_index(self):
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=self.incoming['_storage_pin'], export_writes=True) as bound:
            w = sequence.PNGSequenceExport(bound, upscale, self.incoming, self.videos[1], operation=uuid.uuid4().hex, export_name='race')
            w.prepare()
            stage_payloads = self.store.stage_payloads
            def mutate(requests, **options):
                path = requests[0]['source']
                original = path.read_bytes()
                path.write_bytes(bytes([original[0]^1])+original[1:])
                return stage_payloads(requests, **options)
            with patch.object(self.store, 'stage_payloads', side_effect=mutate):
                with self.assertRaisesRegex(state.StateConflict, 'changed during staging'):
                    w.publish()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_pixel_save_and_handoff_keep_png_acceptance_and_owner_on_next_scene(self):
        first = self.export()
        images = fixture.fixture.torch.from_numpy(self.f.pixels[1].astype(fixture.np.float32)/65535)
        with carriers.node_host(self.store, processing_writers=(upscale.MiniMaxH3ChainUpscaleSegmentSave.save,)):
            saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(self.incoming, images)['result'][0]
        self.assertEqual(saved['png_export_owner'], self.record(first)[1]['clips'][0]['processing_owners'][0])
        handoff = upscale.MiniMaxH3ChainUpscaleHandoff.prepare
        with carriers.node_host(self.store, input_adapters={handoff:fixture.fixture.continuation.loop_inputs}):
            incoming = upscale.MiniMaxH3ChainUpscaleHandoff().prepare(self.incoming, images, saved)[0]
        self.assertEqual(incoming['index'], 2)
        second = self.export(incoming, unique_id='png2')
        self.assertEqual(first['result'][0], second['result'][0])
        self.assertEqual(self.record(second)[1]['last_scene'], 2)
        self.store.verify_payloads()

    def test_changed_settings_allocate_next_number_without_touching_first(self):
        first = self.export()
        second = self.export(self.next_state(1), unique_id='rgb8', png_bit_depth='8')
        third = self.export(self.next_state(1), unique_id='new-numbering', first_frame_number=10)
        self.assertTrue(self.record(second)[0].endswith('/DLSS_2/export.json'))
        self.assertTrue(self.record(third)[0].endswith('/DLSS_3/export.json'))
        self.assertEqual(self.record(first)[1]['settings']['png_bit_depth'], 16)

    def test_recovery_controls_keep_legacy_variant_and_session_schema(self):
        self.export()
        incoming = self.next_state(1)
        result = self.export(incoming, unique_id='new-settings', first_frame_number=10)
        directory, record = self.record(result)
        directory = directory.removesuffix('/export.json')
        snapshot = self.store.snapshot()
        marker = state._decode(snapshot.read(directory+'/.png_variant.json'))
        self.assertEqual(marker['format'], fixture.png.variants.FORMAT)
        self.assertEqual(marker['settings'], record['settings'])
        self.assertIsNone(marker['prefix'])
        binding_hash = state._hash(fixture.json.dumps([incoming['png_export_session'],record['settings']], sort_keys=True).encode())
        binding = directory.removesuffix('_2')+'/.png_variants/'+binding_hash+'.json'
        self.assertEqual(state._decode(snapshot.read(binding)), {'directory':'DLSS_2'})

    def test_reverse_recovery_can_reuse_variant_through_normal_legacy_node(self):
        recovery = fixture.fixture.fixture.module('storage_recovery')
        manager_module = fixture.fixture.fixture.module('processing_checkpoint_delete')
        cleanup = fixture.fixture.fixture.module('png_export_cleanup')
        self.export()
        incoming = self.next_state(1)
        exported = self.export(incoming, unique_id='variant8', png_bit_depth='8')
        address, record = self.record(exported)
        before = self.store.snapshot()
        lab = self.f.f.f.lab
        receipt = lab/'png-recovery-receipt.json'
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.f.f.f.source), independent_copies=True))
        recovered_output = lab/'png-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, recovered_output, lab/'png-recovery', rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        restored = recovered_output/'h3_chains'/self.f.f.run
        self.assertEqual(fixture.json.loads((restored/address).read_text()), record)
        self.assertNotEqual((restored/address).stat().st_ino,
                            (self.store.project/before.state['documents'][address]['file']['path']).stat().st_ino)
        # Pins are execution-host envelopes, not a grant to a recovered legacy
        # project. Normal legacy export uses the same saved recipe/source IDs.
        for carrier in carriers._carriers(incoming):
            carrier.pop('_storage_pin', None)
        folder_paths = fixture.fixture.fixture.fixture.folder_paths
        with patch.object(folder_paths, 'output_directory', str(recovered_output)):
            with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('Recovered variant should be reused')):
                reused = chain.MiniMaxH3ChainExportPNG().export(state=incoming, video=self.videos[1],
                    export_name='DLSS', first_frame_number=101, png_bit_depth='8', embed_workflow=False)
            self.assertEqual(Path(reused['result'][0]), restored/address.removesuffix('/export.json'))
            self.assertEqual(reused['result'][1], exported['result'][1])
            manager = manager_module.ProcessingCheckpointManager(recovered_output)
            with cleanup.locked_exports(manager, self.f.f.run) as found:
                self.assertEqual(len(found), 2)
                self.assertTrue(any(value['settings']['png_bit_depth']==8 for value in found.values()))
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.store.verify_payloads()

    def test_imported_indexes_reuse_mutable_contract_or_fork_immutable_archive(self):
        self.export()
        current = self.store.snapshot()
        address = 'upscaled/new-pixels/frames/DLSS/export.json'
        original = state._decode(current.read(address))
        for immutable in (False, True):
            with self.subTest(immutable=immutable):
                name = 'imported-immutable' if immutable else 'imported-mutable'
                logical = 'upscaled/new-pixels/frames/'+name
                export_id = uuid.uuid4().hex
                base = self.store.snapshot()
                imported = copy.deepcopy(original)
                imported.pop('_storage_export_id')
                receipts = []
                for item in imported['clips'][0]['files']:
                    source = self.store.payload_path(current, address.removesuffix('/export.json')+'/'+item['file'])
                    receipts.append(self.store.stage_payload(logical+'/'+item['file'], source,
                        'exports/png/'+export_id+'/'+item['file'], scope='archive:imported_png', operation_id=uuid.uuid4().hex))
                self.store.commit_artifacts(base, {logical+'/export.json': dict(data=state._encode(imported),
                    scope='archive:imported_png', category='legacy', immutable=immutable)}, receipts,
                    operation_id=uuid.uuid4().hex)
                before = self.store.snapshot()
                incoming = self.next_state(2)
                result = self.export(incoming, unique_id=name, export_name=name)
                after = self.store.snapshot()
                expected = logical+('_2' if immutable else '')+'/export.json'
                record = state._decode(after.read(expected))
                self.assertEqual([clip['index'] for clip in record['clips']], [1,2])
                if immutable:
                    self.assertEqual(before.state['documents'][logical+'/export.json'], after.state['documents'][logical+'/export.json'])
                    self.assertNotEqual(Path(result['result'][0]).name, export_id)
                else:
                    self.assertEqual(Path(result['result'][0]).name, export_id)
                    self.assertEqual(after.state['documents'][expected]['scope'], 'archive:imported_png')
                    self.assertEqual(after.state['documents'][expected]['category'], 'legacy')
                self.assertEqual(state._decode(before.read(logical+'/export.json')), imported)
        self.store.verify_payloads()

    def test_unported_external_output_and_changed_source_reject_before_encoder(self):
        before = self.store.snapshot().reference
        with patch.object(fixture.png, 'encode_scene', side_effect=AssertionError('No encoding')):
            with self.assertRaisesRegex(ValueError, 'external-export port'):
                self.export(output_folder='not-this-project/custom-pngs')
            incoming = copy.deepcopy(self.incoming)
            incoming['source_manifest']['segments'][0]['seed'] += 1
            with self.assertRaisesRegex(ValueError, 'immutable metadata'):
                self.export(incoming)
        self.assertEqual(self.store.snapshot().reference, before)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
