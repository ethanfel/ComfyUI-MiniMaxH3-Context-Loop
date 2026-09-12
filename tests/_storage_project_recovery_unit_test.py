"""Reverse recovery includes post-join work and preserves prior immutable roots."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_project_migration_unit_test as fixture
import storage_project as project
import storage_state as state
import storage_recovery as recovery
import storage_project_recovery as combined
import storage_resolver as resolver


class ProjectRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.JoinTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.prepare()
        self.f.join()
        self.store, self.root = self.f.store, self.f.root
        self.lab = self.f.f.lab
        self.before = self.store.snapshot()
        media = self.lab/'new-video.mp4'
        media.write_bytes(b'new post-join video')
        self.address = 'segments/clip_0002.'+'d'*32+'.mp4'
        staged = self.store.stage_payload(self.address, media, 'media/generation/'+'d'*32+'/video.mp4',
            scope='branch:A', operation_id=uuid.uuid4().hex)
        self.new = b'{"prompt":"post migration edit","seed":18446744073709551613}\r\n'
        self.store.commit_artifacts(self.before, {'checkpoints/clip_0001.json': {
            'data': self.new, 'scope': 'branch:A', 'category': 'branches', 'immutable': False}},
            [staged], operation_id=uuid.uuid4().hex)
        self.current = self.store.snapshot()
        self.receipt = self.lab/'joined-copy-receipt.json'
        state.atomic_json(self.receipt, {'copy': str(self.root), 'source': str(self.f.f.root),
                                        'independent_copies': True})
        self.output, self.folder = self.lab/'recovered-output', self.lab/'recovery'
        self.restored = self.output/'h3_chains/demo'

    def prepare(self):
        return recovery.prepare_legacy_copy(self.receipt, self.output, self.folder, rehearsal_store=self.store)

    def test_latest_controls_media_and_unaccepted_work_are_preserved_independently(self):
        leftover = self.root/'project/jobs/unaccepted.part'
        leftover.parent.mkdir(exist_ok=True)
        leftover.write_bytes(b'incomplete new work retained as evidence')
        source_bytes = {k: p.read_bytes() for k, p in recovery._files(self.root).items()}
        journal = self.prepare()
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        self.assertFalse((self.restored/'storage.json').exists())
        self.assertEqual((self.restored/'checkpoints/clip_0001.json').read_bytes(), self.new)
        self.assertEqual((self.restored/self.address).read_bytes(), b'new post-join video')
        self.assertNotEqual((self.restored/self.address).stat().st_ino,
            self.store.payload_path(self.current, self.address).stat().st_ino)
        self.assertEqual((self.output/recovery.RECOVERY/'authority/project/jobs/unaccepted.part').read_bytes(), leftover.read_bytes())
        old_control = self.before.state['documents']['checkpoints/clip_0001.json']['file']['path']
        self.assertEqual((self.output/recovery.RECOVERY/'authority'/old_control).read_bytes(),
                         self.before.read('checkpoints/clip_0001.json'))
        self.assertEqual(source_bytes, {k: p.read_bytes() for k, p in recovery._files(self.root).items()})
        self.assertEqual(result, recovery.recover_legacy_copy(journal))
        self.assertIsNone(resolver.storage_state(self.restored))

    def test_interrupted_reverse_copy_remains_gated_and_resumes(self):
        journal = self.prepare()
        def interrupt(index):
            if index == 3:
                raise OSError('simulated reverse-copy interruption')
        with self.assertRaises(OSError):
            recovery.recover_legacy_copy(journal, after_copy=interrupt)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            resolver.storage_state(self.restored)
        recovery.recover_legacy_copy(journal)
        self.assertEqual((self.restored/'checkpoints/clip_0001.json').read_bytes(), self.new)
        self.assertEqual(self.current.reference, self.store.snapshot().reference)

    def test_new_combined_root_after_preparation_blocks_stale_recovery(self):
        journal = self.prepare()
        self.store.commit(self.current, {'checkpoints/clip_0001.json': {'data': b'{"seed":7}',
            'scope': 'branch:A', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError, 'Source namespace changed'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'), b'{"seed":7}')

    def test_corrupt_or_missing_accepted_payload_cannot_be_recovered_as_valid(self):
        path = self.store.payload_path(self.current, self.address)
        path.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'differs from its root'):
            self.prepare()
        self.assertFalse(self.folder.exists())
        path.rename(self.lab/'retained-corrupt-video')
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.prepare()

    def test_wrong_store_or_without_explicit_access_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'explicit ProjectStore'):
            recovery.prepare_legacy_copy(self.receipt, self.output, self.folder, rehearsal_store=object())
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            recovery.prepare_legacy_copy(self.receipt, self.output, self.folder)
        with state.control_rehearsal_access(self.f.f.root), self.assertRaisesRegex(ValueError, 'copy-only'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_foreign_gate_and_target_edit_are_not_overwritten(self):
        journal = self.prepare()
        def interrupt(_):
            raise OSError('interrupted')
        with self.assertRaises(OSError):
            recovery.recover_legacy_copy(journal, after_copy=interrupt)
        marker = self.restored/'storage.json'
        marker.write_bytes(b'{"format":"foreign"}')
        with self.assertRaisesRegex(ValueError, 'gate changed'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(marker.read_bytes(), b'{"format":"foreign"}')
        self.assertEqual(self.current.reference, self.store.snapshot().reference)

    def test_current_authority_changes_during_inventory_prevents_preparation(self):
        real = recovery._digest_stable
        changed = False
        def modify(path):
            nonlocal changed
            result = real(path)
            if not changed:
                changed = True
                self.store.advance_epoch(self.current, operation_id=uuid.uuid4().hex)
            return result
        with patch.object(recovery, '_digest_stable', side_effect=modify), self.assertRaisesRegex(ValueError, 'changed during recovery'):
            self.prepare()
        self.assertFalse(self.folder.exists())

    def claim_external_ownership(self):
        from storage_runtime import runtime_access
        from project_ownership import claim_project_ownership
        with runtime_access(self.store, ownership_writes=True):
            return claim_project_ownership(self.root.parent.parent, 'demo', 'recovery-owner-1234567890')

    def test_external_ownership_is_restored_outside_project_with_exact_history(self):
        from project_ownership import require_project_ownership, ownership_status, ProjectOwnershipError
        from storage_ownership import authority_directory
        owned = self.claim_external_ownership()
        directory = authority_directory(self.root.parent.parent, 'demo')
        old = {path.relative_to(directory): path.read_bytes() for path in directory.rglob('*') if path.is_file()}
        journal = self.prepare()
        result = recovery.recover_legacy_copy(journal)
        proof = {'owner_id': 'recovery-owner-1234567890', 'epoch': owned['epoch']}
        require_project_ownership(self.output, 'demo', proof)
        self.assertEqual(ownership_status(self.output, 'demo', proof['owner_id'])['epoch'], owned['epoch'])
        with self.assertRaises(ProjectOwnershipError):
            require_project_ownership(self.output, 'demo', None)
        self.assertTrue((self.output/'h3_chains/.project_ownership/demo.json').is_file())
        self.assertFalse(authority_directory(self.output, 'demo').exists())
        for relative, raw in old.items():
            self.assertEqual((self.output/recovery.RECOVERY/'ownership/log'/relative).read_bytes(), raw)
            self.assertEqual((directory/relative).read_bytes(), raw)
        self.assertEqual(result, recovery.recover_legacy_copy(journal))  # legitimate ownership lock is allowed

    def test_invalid_legacy_external_ownership_cannot_be_dropped(self):
        fence = self.root.parent/'.project_ownership/demo.json'
        fence.parent.mkdir()
        fence.write_bytes(b'{"unknown": "do not drop this fence"}')
        with self.assertRaisesRegex(ValueError, 'ownership metadata is invalid'):
            self.prepare()
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.output.exists())

    def test_claim_after_preparation_rejects_recovery_before_creating_target(self):
        journal = self.prepare()
        self.claim_external_ownership()
        with self.assertRaisesRegex(ValueError, 'ownership changed since recovery'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.output.exists())

    def test_late_external_claim_leaves_incomplete_recovery_gated(self):
        journal = self.prepare()
        def claim(index):
            if index == 1:
                self.claim_external_ownership()
        with self.assertRaisesRegex(ValueError, 'ownership changed since recovery'):
            recovery.recover_legacy_copy(journal, after_copy=claim)
        self.assertTrue((self.restored/'storage.json').is_file())
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            resolver.storage_state(self.restored)
        self.assertEqual(self.current.reference, self.store.snapshot().reference)


if __name__ == '__main__':
    unittest.main(verbosity=2)
