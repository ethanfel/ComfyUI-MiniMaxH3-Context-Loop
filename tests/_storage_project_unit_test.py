"""Combined atomic payload/control contracts; fixtures are NOT a migrator."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_state_unit_test as fixture
import storage_state as state
import storage_project as project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.StateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # Test-only empty-payload bootstrap over an independently copied fixture.
        # Actual import/cutover needs the separately journaled migration service.
        marker = state._decode((self.f.root/'storage.json').read_bytes())
        marker.update(format=project.FORMAT, mode=project.ProjectStore.MODE)
        state.atomic_json(self.f.root/'storage.json', marker)
        self.store = project.ProjectStore(self.f.root)
        self.source = self.f.f.lab/'test-video.mp4'
        self.source.write_bytes(b'original media bytes')
        self.address = 'segments/clip_0001.'+'e'*32+'.mp4'
        self.target = 'media/generation/'+'e'*32+'/video.mp4'

    def stage(self, *, address=None, target=None, source=None, scope='branch:A', operation_id=None, immutable=True):
        return self.store.stage_payload(address or self.address, source or self.source, target or self.target,
            scope=scope, operation_id=operation_id or uuid.uuid4().hex, immutable=immutable)

    def controls(self, value=None, scope='branch:A'):
        address = self.f.a if scope == 'branch:A' else self.f.b
        return {address: {'data': state._encode(value or {'prompt': 'new', 'seed': '18446744073709551613'}),
                         'scope': scope, 'category': 'branches', 'immutable': False}}

    def commit(self, base, staged, *, controls=None, operation_id=None, **kwargs):
        return self.store.commit_artifacts(base, controls or self.controls(), staged,
            operation_id=operation_id or uuid.uuid4().hex, **kwargs)

    def test_staging_is_independent_durable_but_not_accepted(self):
        before = self.store.snapshot()
        receipt = self.stage()
        path = self.store.project/self.target
        self.assertEqual(path.read_bytes(), self.source.read_bytes())
        self.assertNotEqual((path.stat().st_dev, path.stat().st_ino),
                            (self.source.stat().st_dev, self.source.stat().st_ino))
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertEqual(project.payload_catalog(before), {})
        with self.assertRaises(KeyError):
            self.store.payload_path(before, self.address)
        self.assertEqual(receipt, self.stage(operation_id=receipt['operation_id']))

    def batch(self, count=3):
        return [dict(address='frames/test/frame_%08d.png'%i, source=self.source,
                     target='exports/png/'+'c'*32+'/frame_%08d.png'%i,
                     scope='exports:test', operation_id=uuid.uuid4().hex)
                for i in range(count)]

    def test_bulk_staging_revalidates_bounded_batches_not_whole_history_per_frame(self):
        before, requests = self.store.snapshot(), self.batch(65)
        with patch.object(self.store, '_marker', wraps=self.store._marker) as markers:
            receipts = self.store.stage_payloads(requests, batch_size=32)
        self.assertLessEqual(markers.call_count, 6)  # at most two checks per locked batch
        self.assertGreaterEqual(markers.call_count, 3)
        self.assertEqual(len(receipts), 65)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        for request, receipt in zip(requests, receipts):
            self.assertEqual(receipt['record']['address'], request['address'])
            self.assertEqual((self.store.project/request['target']).read_bytes(), self.source.read_bytes())
        self.commit(before, receipts)
        self.assertEqual(self.store.verify_payloads(), 65)

    def test_bulk_interruption_retains_exact_reservations_and_resumes_in_order(self):
        requests, observed = self.batch(), []
        before = self.store.snapshot()
        def stop(receipt):
            observed.append(receipt)
            if len(observed)==2:
                raise OSError('batch interrupted')
        with self.assertRaisesRegex(OSError, 'batch interrupted'):
            self.store.stage_payloads(requests, after_stage=stop)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertFalse((self.store.project/requests[-1]['target']).exists())
        resumed = self.store.stage_payloads(requests)
        self.assertEqual(resumed[:2], observed)
        self.commit(before, resumed)
        self.assertEqual(self.store.verify_payloads(), 3)

    def test_bulk_batch_rejects_duplicate_identity_escape_and_invalid_options_before_io(self):
        requests = self.batch()
        malformed = []
        for key in ('address','target','operation_id'):
            changed = copy.deepcopy(requests)
            changed[1][key] = changed[0][key]
            malformed.append(changed)
        changed = copy.deepcopy(requests)
        changed[-1]['target'] = '../escape.png'
        malformed.append(changed)
        changed = copy.deepcopy(requests)
        changed[-1]['immutable'] = 'yes'
        malformed.append(changed)
        before = {str(p):p.read_bytes() for p in self.store.project.rglob('*') if p.is_file()}
        for changed in malformed:
            with self.assertRaises(ValueError):
                self.store.stage_payloads(changed)
        for size in (0, True, 129):
            with self.assertRaisesRegex(ValueError, 'bounded batch size'):
                self.store.stage_payloads(requests, batch_size=size)
        after = {str(p):p.read_bytes() for p in self.store.project.rglob('*') if p.is_file()}
        self.assertEqual(after, before)

    def test_bulk_batch_rechecks_ready_gate_between_chunks(self):
        requests, count = self.batch(), 0
        original = (self.store.project/'storage.json').read_bytes()
        def gate(_receipt):
            nonlocal count
            count += 1
            if count == 2:
                marker = state._decode(original)
                marker['phase'] = 'maintenance'
                state.atomic_json(self.store.project/'storage.json', marker)
        with self.assertRaisesRegex(ValueError, 'incomplete control-state'):
            self.store.stage_payloads(requests, batch_size=2, after_stage=gate)
        self.assertFalse((self.store.project/requests[-1]['target']).exists())
        state.atomic_json(self.store.project/'storage.json', state._decode(original))

    def test_bulk_staging_still_rejects_changed_reserved_bytes(self):
        requests = self.batch()
        receipts = self.store.stage_payloads(requests)
        target = self.store.project/requests[1]['target']
        target.write_bytes(b'corrupt staged frame')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.stage_payloads(requests)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.commit(self.store.snapshot(), receipts)

    def test_unsupported_filesystem_refuses_staging_before_any_project_write(self):
        before = {str(path): path.read_bytes() for path in self.store.project.rglob('*') if path.is_file()}
        with patch.object(project, 'require_atomic_control_files', side_effect=ValueError('unsafe filesystem')):
            with self.assertRaisesRegex(ValueError, 'unsafe filesystem'):
                self.stage()
        after = {str(path): path.read_bytes() for path in self.store.project.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_unsupported_filesystem_refuses_control_commit_and_epoch_before_any_write(self):
        before = {str(path): path.read_bytes() for path in self.store.project.rglob('*') if path.is_file()}
        base = self.store.snapshot()
        with patch.object(state, 'require_atomic_control_files', side_effect=ValueError('unsafe filesystem')):
            with self.assertRaisesRegex(ValueError, 'unsafe filesystem'):
                self.store.commit(base, self.controls(), operation_id=uuid.uuid4().hex)
            with self.assertRaisesRegex(ValueError, 'unsafe filesystem'):
                self.store.advance_epoch(base, operation_id=uuid.uuid4().hex)
        after = {str(path): path.read_bytes() for path in self.store.project.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_existing_flat_reference_cache_payload_layout_is_supported(self):
        staged = self.stage(address='reference_cache/scene_0001.saved.safetensors',
                            target='project/reference_cache/scene_0001.saved.safetensors')
        self.commit(self.store.snapshot(), [staged])
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_media_and_settings_publish_in_one_root_without_mixed_reads(self):
        base = self.store.snapshot()
        staged = self.stage()
        audio = self.stage(address=self.address+'.wav', target=self.target+'.wav')
        seen = []
        def observe(_):
            now = self.store.snapshot()
            seen.append(now.reference == base.reference and not project.payload_catalog(now))
        self.commit(base, [staged, audio], after_stage=observe)
        self.assertTrue(seen and all(seen))
        view = self.store.snapshot()
        self.assertEqual(view.state['generation'], 1)
        self.assertEqual(json.loads(view.read(self.f.a))['seed'], '18446744073709551613')
        self.assertEqual(self.store.payload_path(view, self.address, verify=True).read_bytes(), self.source.read_bytes())
        self.assertEqual(self.store.verify_payloads(view), 2)
        self.assertEqual(project.payload_catalog(base), {})
        self.assertEqual(base.read(self.f.a), self.f.original[self.f.a])

    def test_interrupted_acceptance_retains_staging_and_can_retry(self):
        base, staged, op = self.store.snapshot(), self.stage(), uuid.uuid4().hex
        def fail(_):
            raise OSError('simulated acceptance interruption')
        with self.assertRaises(OSError):
            self.commit(base, [staged], operation_id=op, after_stage=fail)
        self.assertEqual(self.store.snapshot().reference, base.reference)
        self.assertTrue((self.store.project/self.target).is_file())
        receipt = self.commit(base, [staged], operation_id=op)
        self.assertEqual(self.commit(base, [staged], operation_id=op), receipt)
        self.assertEqual(self.store.snapshot().state['generation'], 1)

    def test_lost_pointer_acknowledgement_reconciles_media_and_controls(self):
        base, staged, op = self.store.snapshot(), self.stage(), uuid.uuid4().hex
        real = state.atomic_json
        def fail(path, value):
            real(path, value)
            raise OSError('lost acknowledgement')
        with patch.object(state, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
            self.commit(base, [staged], operation_id=op)
        published = self.store.snapshot()
        self.commit(base, [staged], operation_id=op)
        self.assertEqual(published.reference, self.store.snapshot().reference)
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_corrupt_staged_payload_blocks_publication(self):
        base, staged = self.store.snapshot(), self.stage()
        (self.store.project/self.target).write_bytes(b'edited')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.commit(base, [staged])
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_media_changed_after_candidate_root_is_caught(self):
        base, staged = self.store.snapshot(), self.stage()
        def change(phase):
            if phase == 'root':
                (self.store.project/self.target).write_bytes(b'changed during publication')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.commit(base, [staged], after_stage=change)
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_earlier_payload_changed_during_verification_is_caught(self):
        base, staged = self.store.snapshot(), self.stage()
        other = self.stage(address=self.address+'.wav', target=self.target+'.wav')
        real = project._verify_file
        count = 0
        def verify(root, record):
            nonlocal count
            result = real(root, record)
            count += 1
            # Two staging receipts verified, then both candidate payloads.
            if count == 4:
                first = self.store.project/(self.target if record['file']['path'] != self.target else self.target+'.wav')
                first.write_bytes(b'changed after its candidate hash')
            return result
        with patch.object(project, '_verify_file', side_effect=verify), self.assertRaisesRegex(ValueError, 'during control'):
            self.commit(base, [staged, other])
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_unowned_destination_is_not_adopted_even_if_bytes_match(self):
        path = self.store.project/self.target
        path.parent.mkdir(parents=True)
        path.write_bytes(self.source.read_bytes())
        with self.assertRaisesRegex(FileExistsError, 'Unowned'):
            self.stage()
        self.assertEqual(path.read_bytes(), self.source.read_bytes())

    def test_staging_fsync_failure_does_not_accept_media_and_retry_is_safe(self):
        op, before = uuid.uuid4().hex, self.store.snapshot()
        # Reserve intent successfully first, then fail the payload file flush.
        real = os.fsync
        count = 0
        def fsync(fd):
            nonlocal count
            count += 1
            if os.fstat(fd).st_size == len(self.source.read_bytes()):
                raise OSError('payload flush failure')
            real(fd)
        with patch.object(project.os, 'fsync', side_effect=fsync), self.assertRaises(OSError):
            self.stage(operation_id=op)
        self.assertEqual(before.reference, self.store.snapshot().reference)
        self.assertFalse((self.store.project/self.target).exists())
        staged = self.stage(operation_id=op)
        self.commit(before, [staged])
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_staging_receipt_and_operation_identity_cannot_be_reused_for_another_file(self):
        staged = self.stage()
        forged = copy.deepcopy(staged)
        forged['record']['address'] += '.other'
        with self.assertRaisesRegex(ValueError, 'reservation intent'):
            self.commit(self.store.snapshot(), [forged])
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.stage(address=self.address+'.other', operation_id=staged['operation_id'])

    def test_payload_cannot_hide_existing_control_identity(self):
        staged, base = self.stage(address=self.f.a), self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'control document|colliding payload'):
            self.commit(base, [staged])
        self.assertEqual(base.reference, self.store.snapshot().reference)

    def test_immutable_payload_cannot_be_replaced_mutable_version_keeps_old_root(self):
        staged, base = self.stage(), self.store.snapshot()
        self.commit(base, [staged])
        saved = self.store.snapshot()
        other = self.stage(target=self.target+'.new')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.commit(saved, [other])
        self.assertEqual(self.store.payload_path(saved, self.address, verify=True), self.store.project/self.target)
        mutable_address = 'frames/export_1/frame_0001.png'
        old = self.stage(address=mutable_address, target='exports/png/'+'1'*32+'/frame.png', immutable=False)
        self.commit(saved, [old])
        old_root = self.store.snapshot()
        self.source.write_bytes(b'edited pixels')
        new = self.stage(address=mutable_address, target='exports/png/'+'2'*32+'/frame.png', immutable=False)
        self.commit(old_root, [new])
        self.assertEqual(self.store.payload_path(old_root, mutable_address, verify=True).read_bytes(), b'original media bytes')
        self.assertEqual(self.store.payload_path(self.store.snapshot(), mutable_address, verify=True).read_bytes(), b'edited pixels')

    def test_independent_branches_merge_and_stale_source_or_epoch_is_rejected(self):
        base = self.store.snapshot()
        one = self.stage()
        two = self.stage(address=self.address+'.B', target=self.target+'.B', scope='branch:B')
        self.commit(base, [one])
        self.commit(base, [two], controls=self.controls(scope='branch:B'))
        self.assertEqual(self.store.verify_payloads(), 2)
        fresh = self.stage(address=self.address+'.third', target=self.target+'.third', scope='branch:B')
        with self.assertRaisesRegex(ValueError, 'scope changed'):
            self.commit(base, [fresh], controls=self.controls(scope='branch:B'), read_scopes=['branch:A'])
        base = self.store.snapshot()
        self.store.advance_epoch(base, operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError, 'epoch changed'):
            self.commit(base, [fresh], controls=self.controls(scope='branch:B'))

    def test_control_only_and_normal_resolvers_do_not_open_combined_fixture(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.f.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            fixture.fixture.resolver.storage_state(self.store.project)
        with self.assertRaisesRegex(ValueError, 'staging receipt'):
            self.store.commit(self.store.snapshot(), {project.payload_key(self.address): {
                'category': 'payloads', 'scope': 'branch:A', 'immutable': True, 'data': b'{}'}}, operation_id=uuid.uuid4().hex)

    def test_control_reads_are_pinned_and_root_cache_detects_changes(self):
        base = self.store.snapshot()
        before = base.state
        base.state['documents'].clear()
        self.assertEqual(base.state, before)
        self.store.commit(base, self.controls(), operation_id=uuid.uuid4().hex)
        self.assertEqual(base.read(self.f.a), self.f.original[self.f.a])
        (self.store.project/base.reference['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            base.read(self.f.a)


if __name__ == '__main__':
    unittest.main()
