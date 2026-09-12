"""Exact reversible catalogue retirement; not domain deletion permission tests."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_project_unit_test as fixture
import storage_project as project
import storage_state as state
import storage_quarantine as quarantine


class QuarantineTests(unittest.TestCase):
    def test_immutable_control_archive_retains_bytes_and_undo_cannot_overwrite(self):
        base = self.store.snapshot()
        target = 'chapters/01_first/retired_manifests/'+'f'*32+'.json'
        preview = self.service.preview(base, [self.metadata], reason='Retire one saved chapter snapshot',
                                       archive_controls={self.metadata:target})
        receipt = self.retire(preview)
        after = self.store.snapshot()
        self.assertNotIn(self.metadata, after.state['documents'])
        self.assertEqual(after.state['documents'][target], base.state['documents'][self.metadata])
        self.assertEqual(after.read(target), base.read(self.metadata))
        self.service.undo(after, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        restored = self.store.snapshot()
        self.assertNotIn(target, restored.state['documents'])
        self.assertEqual(restored.state['documents'][self.metadata], base.state['documents'][self.metadata])

    def test_archive_preview_rejects_payloads_occupied_destinations_and_forgery(self):
        base = self.store.snapshot()
        for archive in ({self.f.address:'new.json'}, {self.metadata:self.pointer},
                        {self.metadata:'retention/'+'f'*32+'/receipt.json'}, {'missing.json':'new.json'}):
            with self.subTest(archive=archive), self.assertRaises(ValueError):
                self.service.preview(base, [self.metadata, self.f.address], reason='Invalid archive', archive_controls=archive)
        preview = self.service.preview(base, [self.metadata], reason='Exact control archive',
                                       archive_controls={self.metadata:'archived.json'})
        forged = copy.deepcopy(preview)
        forged['archives'][0]['descriptor']['scope'] = 'branch:foreign'
        with self.assertRaises(ValueError):
            self.retire(forged)
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def make_fixture(self):
        return fixture.ProjectTests()

    def setUp(self):
        self.f = self.make_fixture()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.metadata = 'checkpoints/clip_0001.'+'e'*32+'.json'
        self.pointer = self.f.f.pointer
        controls = {self.metadata:dict(data=state._encode({'segment':{'revision':'e'*32}}),
            scope='archive:'+'e'*32, category='takes', immutable=True)}
        self.f.commit(self.store.snapshot(), [self.f.stage(scope='archive:'+'e'*32)], controls=controls)
        self.service = quarantine.ProjectQuarantine(self.store)
        self.addresses = [self.metadata, self.pointer, self.f.address]

    def preview(self, base=None, addresses=None):
        return self.service.preview(base or self.store.snapshot(), addresses or self.addresses,
                                    reason='Explicit disposable-fixture retirement')

    def retire(self, preview=None, **kwargs):
        return self.service.quarantine(preview or self.preview(),
            operation_id=kwargs.pop('operation_id', uuid.uuid4().hex), **kwargs)

    def test_preview_is_read_only_and_exact_without_authorizing_deletion(self):
        before = self.store.snapshot()
        files = {p.relative_to(self.store.project).as_posix():p.read_bytes()
                 for p in self.store.project.rglob('*') if p.is_file()}
        preview = self.preview()
        self.assertEqual(preview['base'], before.reference)
        self.assertEqual([item['address'] for item in preview['items']], sorted(self.addresses))
        self.assertNotIn('allowed', preview)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertEqual(files, {p.relative_to(self.store.project).as_posix():p.read_bytes()
                               for p in self.store.project.rglob('*') if p.is_file()})

    def updated_preview(self):
        base = self.store.snapshot()
        return self.service.preview(base, [self.metadata, self.f.address], reason='Atomic PNG ownership tombstone',
                                    updates={self.pointer:b'{"deleted_scenes":[1],"clips":[]}'})

    def test_control_update_and_retirement_are_atomic_and_undo_restores_exact_versions(self):
        before = self.store.snapshot()
        preview = self.updated_preview()
        self.assertEqual(preview['format'], quarantine.PREVIEW_UPDATES)
        observed = []
        def observe(phase):
            current = self.store.snapshot()
            observed.append((self.metadata in current.state['documents'], current.read(self.pointer)))
        receipt = self.retire(preview, after_stage=observe)
        changed = bytes.fromhex(preview['updates'][0]['data_hex'])
        self.assertTrue(observed)
        self.assertTrue(all(item in ((True, before.read(self.pointer)), (False, changed)) for item in observed))
        self.assertEqual(self.store.snapshot().read(self.pointer), changed)
        self.service.undo(self.store.snapshot(), receipt['operation_id'], operation_id=uuid.uuid4().hex)
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(self.store.snapshot().state['documents'][address], descriptor)
            self.assertEqual(self.store.snapshot().read(address), before.read(address))

    def test_updated_control_edit_blocks_undo_without_restoring_deleted_files(self):
        receipt = self.retire(self.updated_preview())
        base = self.store.snapshot()
        contract = base.state['documents'][self.pointer]
        self.store.commit(base, {self.pointer:dict(data=b'{"later":true}',
            **{key:contract[key] for key in ('scope', 'category', 'immutable')})}, operation_id=uuid.uuid4().hex)
        current = self.store.snapshot()
        with self.assertRaisesRegex(state.StateConflict, 'later edits'):
            self.service.undo(current, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().reference, current.reference)
        self.assertNotIn(self.metadata, current.state['documents'])

    def test_update_retirement_failure_and_lost_ack_retries_preserve_one_transaction(self):
        preview, before = self.updated_preview(), self.store.snapshot().reference
        def fail(phase):
            if phase == 'root':
                raise OSError('updated quarantine before acceptance')
        operation = uuid.uuid4().hex
        with self.assertRaises(OSError):
            self.retire(preview, operation_id=operation, after_stage=fail)
        self.assertEqual(self.store.snapshot().reference, before)
        original = self.store._publish
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('updated quarantine lost ack')
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.retire(preview, operation_id=operation)
        accepted = self.store.snapshot().reference
        receipt = self.retire(preview, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, accepted)
        base = self.store.snapshot()
        undo = uuid.uuid4().hex
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.service.undo(base, operation, operation_id=undo)
        restored = self.store.snapshot().reference
        self.service.undo(base, operation, operation_id=undo)
        self.assertEqual(self.store.snapshot().reference, restored)
        self.assertEqual(self.retire(preview, operation_id=operation), receipt)
        self.assertEqual(self.store.snapshot().reference, restored)

    def test_control_update_rejects_immutable_missing_internal_duplicate_and_forged_preview(self):
        before = self.store.snapshot()
        for updates in ({self.metadata:b'{}'}, {'unknown.json':b'{}'}, {self.f.address:b'{}'},
                        {'retention/'+uuid.uuid4().hex+'/state.json':b'{}'}, {self.pointer:'not bytes'}):
            with self.subTest(updates=list(updates)), self.assertRaises(ValueError):
                self.service.preview(before, [self.metadata], reason='Invalid control update', updates=updates)
        valid = self.updated_preview()
        for change in ('descriptor', 'data_hex', 'duplicate'):
            bad = copy.deepcopy(valid)
            if change == 'descriptor':
                bad['updates'][0]['descriptor']['scope'] = 'branch:other'
            elif change == 'data_hex':
                bad['updates'][0]['data_hex'] = b'{}'.hex()
            else:
                bad['updates'].append(bad['updates'][0])
            with self.subTest(change=change), self.assertRaises(state.StateConflict):
                self.retire(bad)
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_witnessed_immutable_index_replacement_keeps_old_version_and_exact_undo(self):
        before = self.store.snapshot()
        raw = b'{"prefix":null,"deleted_scenes":[1]}'
        preview = self.service.preview(before, [self.f.address], reason='Explicit imported PNG recipe retirement',
            updates={self.metadata:raw}, replace_immutable=[self.metadata])
        receipt = self.retire(preview)
        accepted = self.store.snapshot()
        self.assertEqual(accepted.read(self.metadata), raw)
        self.assertNotEqual(accepted.state['documents'][self.metadata]['file'], before.state['documents'][self.metadata]['file'])
        self.assertEqual(before.read(self.metadata), state._encode({'segment':{'revision':'e'*32}}))
        self.service.undo(accepted, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().state['documents'][self.metadata], before.state['documents'][self.metadata])

    def custody_preview(self, status, base=None):
        return self.service.preview(base or self.store.snapshot(), self.addresses,
            reason='Explicit damaged owned payload retirement', payload_exceptions={self.f.address:[status]})

    def test_edited_payload_custody_keeps_recorded_hash_and_restores_exact_predeletion_state(self):
        before = self.store.snapshot()
        key = project.payload_key(self.f.address)
        path = self.store.payload_path(before, self.f.address)
        original = path.read_bytes()
        path.write_bytes(original+b'edited pixels')
        preview = self.custody_preview('edited')
        item = next(item for item in preview['items'] if item['address'] == self.f.address)
        self.assertEqual(item['custody']['status'], 'edited')
        self.assertEqual(item['payload'], project._indexed_record(before, key))
        self.assertNotEqual(item['custody']['file']['sha256'], item['payload']['file']['sha256'])
        deleted = self.retire(preview)
        self.assertNotIn(key, self.store.snapshot().state['documents'])
        self.assertEqual(path.read_bytes(), original+b'edited pixels')
        self.assertEqual(self.store.verify_payloads(), 0)
        self.service.undo(self.store.snapshot(), deleted['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().state['documents'][key], before.state['documents'][key])
        self.assertEqual(path.read_bytes(), original+b'edited pixels')
        with self.assertRaises(state.StateConflict):
            self.store.verify_payloads()  # Never conceal the existing edit.

    def test_missing_payload_custody_does_not_invent_bytes_on_undo(self):
        before = self.store.snapshot()
        key = project.payload_key(self.f.address)
        path = self.store.payload_path(before, self.f.address)
        path.unlink()  # Only this disposable fixture simulates a lost file.
        preview = self.custody_preview('missing')
        deleted = self.retire(preview)
        self.assertNotIn(key, self.store.snapshot().state['documents'])
        self.assertFalse(path.exists())
        self.service.undo(self.store.snapshot(), deleted['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().state['documents'][key], before.state['documents'][key])
        self.assertFalse(path.exists())
        with self.assertRaises(FileNotFoundError):
            self.store.verify_payloads()

    def test_edited_or_missing_custody_never_bypasses_control_or_permission_errors(self):
        base = self.store.snapshot()
        for exceptions in ({self.metadata:['missing']}, {'not-owned.png':['edited']}, {self.f.address:['anything']}):
            with self.subTest(exceptions=exceptions), self.assertRaises(ValueError):
                self.service.preview(base, self.addresses, reason='Invalid custody', payload_exceptions=exceptions)
        with patch.object(project, '_hash_file', side_effect=PermissionError('fixture permission denied')):
            with self.assertRaises(PermissionError):
                self.custody_preview('missing')
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_custody_edit_race_and_missing_file_reappearance_fence_publication(self):
        path = self.store.payload_path(self.store.snapshot(), self.f.address)
        raw = path.read_bytes()
        for status in ('edited', 'missing'):
            if status == 'edited':
                path.write_bytes(raw+b'first edit')
            else:
                path.unlink()
            preview, before = self.custody_preview(status), self.store.snapshot().reference
            def change(phase):
                if phase == 'root':
                    path.write_bytes(raw+b'changed after preview')
            with self.subTest(status=status), self.assertRaises(state.StateConflict):
                self.retire(preview, after_stage=change)
            self.assertEqual(self.store.snapshot().reference, before)
            self.assertEqual(path.read_bytes(), raw+b'changed after preview')

    def test_custody_undo_rejects_later_edits_and_recovered_missing_bytes(self):
        before = self.store.snapshot()
        path = self.store.payload_path(before, self.f.address)
        original = path.read_bytes()
        path.write_bytes(original+b'first edit')
        receipt = self.retire(self.custody_preview('edited'))
        accepted = self.store.snapshot()
        path.write_bytes(original+b'later edit')
        with self.assertRaises(state.StateConflict):
            self.service.undo(accepted, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().reference, accepted.reference)
        self.assertEqual(path.read_bytes(), original+b'later edit')

    def test_custody_failures_and_lost_ack_retry_keep_exact_operation_and_observation(self):
        path = self.store.payload_path(self.store.snapshot(), self.f.address)
        path.write_bytes(path.read_bytes()+b'edited')
        preview, operation = self.custody_preview('edited'), uuid.uuid4().hex
        before = self.store.snapshot().reference
        def stop(phase):
            if phase == 'root':
                raise OSError('custody interrupted')
        with self.assertRaises(OSError):
            self.retire(preview, operation_id=operation, after_stage=stop)
        self.assertEqual(self.store.snapshot().reference, before)
        publish = self.store._publish
        def lose(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('custody lost ack')
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.retire(preview, operation_id=operation)
        accepted = self.store.snapshot()
        self.retire(preview, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, accepted.reference)
        undo = uuid.uuid4().hex
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.service.undo(accepted, operation, operation_id=undo)
        restored = self.store.snapshot().reference
        self.service.undo(accepted, operation, operation_id=undo)
        self.assertEqual(self.store.snapshot().reference, restored)

    def test_immutable_replacement_does_not_grant_public_writes_or_accept_another_version(self):
        before = self.store.snapshot()
        descriptor = before.state['documents'][self.metadata]
        change = {self.metadata:dict(data=b'{}', **{key:descriptor[key] for key in ('scope', 'category', 'immutable')})}
        with self.assertRaises(state.StateConflict):
            self.store.commit(before, change, operation_id=uuid.uuid4().hex)
        for addresses in ([self.pointer], [self.metadata, self.metadata], ['missing.json']):
            with self.subTest(addresses=addresses), self.assertRaises(ValueError):
                self.service.preview(before, [self.f.address], reason='Invalid explicit replacement',
                    updates={self.metadata:b'{}'}, replace_immutable=addresses)
        wrong = copy.deepcopy(descriptor)
        wrong['file']['sha256'] = '0'*64
        with self.assertRaises(state.StateConflict):
            self.store._commit_changes(before, change, operation_id=uuid.uuid4().hex,
                                       replace_documents={self.metadata:wrong})
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_quarantine_atomically_hides_metadata_pointer_and_media_but_retains_all_bytes(self):
        before = self.store.snapshot()
        observed = []
        def observe(phase):
            current = self.store.snapshot()
            observed.append((phase, current.reference, set(current.state['documents'])))
        receipt = self.retire(after_stage=observe)
        after = self.store.snapshot()
        removed = {self.metadata, self.pointer, project.payload_key(self.f.address)}
        self.assertTrue(observed)
        self.assertTrue(all(ref == before.reference for phase,ref,keys in observed
                            if phase not in ('pointer', 'commit', 'acknowledged')))
        self.assertTrue(all(removed.isdisjoint(keys) for phase,ref,keys in observed
                            if phase in ('pointer', 'commit', 'acknowledged')))
        self.assertTrue(removed.isdisjoint(after.state['documents']))
        self.assertEqual(project.payload_catalog(after), {})
        self.assertEqual(before.read(self.metadata), state._encode({'segment':{'revision':'e'*32}}))
        path = self.store.payload_path(before, self.f.address, verify=True)
        self.assertEqual(path.read_bytes(), self.f.source.read_bytes())
        self.assertEqual(after.read(self.f.f.a), before.read(self.f.f.a))
        record = json.loads(after.read(quarantine._address(receipt['operation_id'])))
        self.assertEqual(record['reclaimed_bytes'], 0)
        self.assertEqual(record['physical_policy'], 'retain_immutable_slots')
        self.assertEqual(self.store.committed_snapshot(receipt).reference, after.reference)

    def test_undo_restores_original_descriptors_bytes_and_seed_without_overwrite(self):
        before = self.store.snapshot()
        receipt = self.retire()
        base = self.store.snapshot()
        result = self.service.undo(base, receipt['operation_id'], operation_id=uuid.uuid4().hex)
        after = self.store.snapshot()
        for address in (self.metadata, self.pointer, project.payload_key(self.f.address), self.f.f.a):
            self.assertEqual(after.read(address), before.read(address))
            self.assertEqual(after.state['documents'][address], before.state['documents'][address])
        self.assertEqual(self.store.verify_payloads(), 1)
        self.assertEqual(self.store.committed_snapshot(result).reference, after.reference)
        self.assertEqual(json.loads(after.read(quarantine._address(receipt['operation_id'], 'state')))['status'], 'restored')

    def test_prepublication_failures_preserve_active_root_and_can_resume(self):
        for phase in ('document', 'retired_document', 'root'):
            before = self.store.snapshot()
            preview, operation = self.preview(before), uuid.uuid4().hex
            def fail(point):
                if point == phase:
                    raise OSError('injected quarantine failure')
            with self.subTest(phase=phase), self.assertRaisesRegex(OSError, 'injected'):
                self.retire(preview, operation_id=operation, after_stage=fail)
            self.assertEqual(self.store.snapshot().reference, before.reference)
            self.assertEqual(self.store.verify_payloads(), 1)
            self.retire(preview, operation_id=operation)
            self.service.undo(self.store.snapshot(), operation, operation_id=uuid.uuid4().hex)

    def test_lost_ack_retry_does_not_publish_twice_or_requarantine_after_undo(self):
        preview, operation = self.preview(), uuid.uuid4().hex
        publish = self.store._publish
        def lose(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost acknowledgement')
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.retire(preview, operation_id=operation)
        after = self.store.snapshot().reference
        receipt = self.retire(preview, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, after)
        self.service.undo(self.store.snapshot(), operation, operation_id=uuid.uuid4().hex)
        restored = self.store.snapshot().reference
        self.assertEqual(self.retire(preview, operation_id=operation), receipt)
        self.assertEqual(self.store.snapshot().reference, restored)
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_undo_lost_ack_is_retryable_only_with_same_input_root(self):
        operation = self.retire()['operation_id']
        base, undo = self.store.snapshot(), uuid.uuid4().hex
        publish = self.store._publish
        def lose(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost undo acknowledgement')
        with patch.object(self.store, '_publish', lose), self.assertRaises(OSError):
            self.service.undo(base, operation, operation_id=undo)
        after = self.store.snapshot().reference
        self.service.undo(base, operation, operation_id=undo)
        self.assertEqual(self.store.snapshot().reference, after)
        with self.assertRaises(ValueError):
            self.service.undo(self.store.snapshot(), operation, operation_id=uuid.uuid4().hex)

    def test_new_reference_in_unrelated_scope_invalidates_stale_quarantine(self):
        preview = self.preview()
        self.store.commit(self.store.snapshot(), {self.f.f.b:dict(data=b'{"new_reference":true}',
            scope='branch:B', category='branches', immutable=False)}, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict, 'Project changed'):
            self.retire(preview)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_undo_cannot_overwrite_reassigned_checkpoint_or_new_scope(self):
        operation = self.retire()['operation_id']
        self.store.commit(self.store.snapshot(), {self.pointer:dict(data=b'{"revision":"later"}',
            scope='branch:A', category='branches', immutable=False)}, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict, 'occupied'):
            self.service.undo(self.store.snapshot(), operation, operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_undo_rejects_changed_payload_bytes_or_missing_control_bytes(self):
        operation = self.retire()['operation_id']
        base = self.store.snapshot()
        path = self.store.project/self.f.target
        original = path.read_bytes()
        path.write_bytes(original[:-1]+bytes([original[-1]^1]))
        with self.assertRaises(ValueError):
            self.service.undo(base, operation, operation_id=uuid.uuid4().hex)
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_changed_preview_cannot_hide_descriptors_payloads_or_scope(self):
        preview = self.preview()
        for mutate in ('hash', 'descriptor', 'payload', 'address'):
            bad = copy.deepcopy(preview)
            if mutate == 'hash':
                bad['sha256'] = '0'*64
            elif mutate == 'descriptor':
                bad['items'][0]['descriptor']['immutable'] = False
            elif mutate == 'payload':
                next(item for item in bad['items'] if 'payload' in item)['payload']['file']['sha256'] = '0'*64
            else:
                bad['items'][0]['address'] = '../escape'
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, FileNotFoundError)):
                self.retire(bad)

    def test_public_control_writers_cannot_forge_retention_receipts_or_retire_payload_index(self):
        base = self.store.snapshot()
        fake = {quarantine._address(uuid.uuid4().hex):dict(data=b'{}', category='recovery',
                                                        scope='project', immutable=True)}
        with self.assertRaisesRegex(ValueError, 'quarantine transaction'):
            self.store.commit(base, fake, operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError, 'quarantine transaction'):
            self.store.commit_artifacts(base, fake, [], operation_id=uuid.uuid4().hex)
        with self.assertRaises(TypeError):
            self.store.commit(base, {}, operation_id=uuid.uuid4().hex,
                              retire_documents={project.payload_key(self.f.address):{}})
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_empty_duplicate_missing_reserved_and_escape_addresses_reject(self):
        base = self.store.snapshot()
        for addresses in ([], self.addresses*2, ['missing.mp4'], ['../bad'], ['C:/file.mp4'],
                          ['jobs/request.json'], ['retention/x.json'], [project.payload_key(self.f.address)]):
            with self.subTest(addresses=addresses), self.assertRaises((ValueError, FileNotFoundError)):
                self.service.preview(base, addresses, reason='test')
        self.assertEqual(self.store.snapshot().reference, base.reference)

    def test_epoch_change_and_cross_project_snapshot_reject_retirement(self):
        preview, base = self.preview(), self.store.snapshot()
        self.store.advance_epoch(base, operation_id=uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            self.retire(preview)
        other = state.Snapshot(self.f.f.f.root, base.reference)
        with self.assertRaises(ValueError):
            self.preview(other)

    def test_changed_operation_request_cannot_reuse_accepted_quarantine(self):
        preview, operation = self.preview(), uuid.uuid4().hex
        self.retire(preview, operation_id=operation)
        different = self.service.preview(state.Snapshot(self.store.project, preview['base']),
            self.addresses[:-1], reason=preview['reason'])
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict, 'reused'):
            self.retire(different, operation_id=operation)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_failed_undo_retains_quarantine_then_restores_all_identities(self):
        operation = self.retire()['operation_id']
        base, undo = self.store.snapshot(), uuid.uuid4().hex
        def fail(phase):
            if phase == 'document':
                raise OSError('interrupted undo')
        with self.assertRaises(OSError):
            self.service.undo(base, operation, operation_id=undo, after_stage=fail)
        self.assertEqual(self.store.snapshot().reference, base.reference)
        self.assertEqual(project.payload_catalog(self.store.snapshot()), {})
        self.service.undo(base, operation, operation_id=undo)
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_reverse_recovery_keeps_quarantine_evidence_without_resurrecting_active_files(self):
        import storage_recovery as recovery
        receipt = self.retire()
        before = self.store.snapshot()
        lab = self.f.f.f.lab
        proof = lab/'quarantined-copy.json'
        state.atomic_json(proof, dict(copy=str(self.store.project), source=str(self.f.f.f.root),
                                     independent_copies=True))
        output = lab/'quarantined-recovered-output'
        journal = recovery.prepare_legacy_copy(proof, output, lab/'quarantined-recovery', rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        recovered = output/'h3_chains'/self.store.project.name
        for address in self.addresses:
            self.assertFalse((recovered/address).exists())
        self.assertEqual((recovered/quarantine._address(receipt['operation_id'])).read_bytes(),
                         before.read(quarantine._address(receipt['operation_id'])))
        evidence = output/recovery.RECOVERY/'authority'/self.f.target
        self.assertEqual(evidence.read_bytes(), self.f.source.read_bytes())
        self.assertNotEqual(evidence.stat().st_ino, (self.store.project/self.f.target).stat().st_ino)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        self.assertTrue(result['source_unchanged'])
        self.assertEqual(result, recovery.recover_legacy_copy(journal))

    def test_payload_changed_after_preview_cannot_be_hidden_by_retiring_its_index(self):
        before, preview = self.store.snapshot(), self.preview()
        path = self.store.project/self.f.target
        original = path.read_bytes()
        def change(phase):
            if phase == 'root':
                path.write_bytes(original[:-1]+bytes([original[-1]^1]))
        with self.assertRaisesRegex(state.StateConflict, 'checksum'):
            self.retire(preview, after_stage=change)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        path.write_bytes(original)
        self.assertEqual(self.store.verify_payloads(), 1)


class ImmutableSlotQuarantineTests(QuarantineTests):
    def make_fixture(self):
        from _storage_commit_log_unit_test import ProjectLogTests
        return ProjectLogTests()


if __name__ == '__main__':
    unittest.main()
