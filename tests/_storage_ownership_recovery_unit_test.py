"""Ownership authority, exact history and failure/retry behavior in recovery."""
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import _storage_project_recovery_unit_test as fixture
import storage_state as state
import storage_recovery as recovery
import storage_ownership_recovery as external
from storage_runtime import runtime_access
from storage_ownership import authority_directory
from storage_commit_log import PROTOCOL, CommitLog
import project_ownership as ownership

A, B = 'recovery-owner-1234567890', 'recovery-other-1234567890'


class OwnershipRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProjectRecoveryTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.root = self.f.store, self.f.root
        self.output, self.folder, self.lab = self.f.output, self.f.folder, self.f.lab
        self.source_output = self.root.parent.parent
        self.directory = authority_directory(self.source_output, 'demo')

    def claim(self, owner=A, force=False):
        with runtime_access(self.store, ownership_writes=True):
            return ownership.claim_project_ownership(self.source_output, 'demo', owner, force=force)

    def prepare(self):
        return recovery.prepare_legacy_copy(self.f.receipt, self.output, self.folder,
            rehearsal_store=self.store, commit_protocol=PROTOCOL)

    def test_legacy_record_bytes_and_epoch_are_preserved_exactly(self):
        legacy = self.root.parent/'.project_ownership/demo.json'
        legacy.parent.mkdir()
        record = ownership._empty_record('demo')
        record.update(epoch=42, owner_digest=ownership._owner_digest(A), lease_expires_at=0.0)
        raw = (json.dumps(record, indent=3)+'\r\n').encode()
        legacy.write_bytes(raw)
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        self.assertEqual((self.output/'h3_chains/.project_ownership/demo.json').read_bytes(), raw)
        self.assertEqual((self.output/recovery.RECOVERY/'ownership/legacy.json').read_bytes(), raw)
        self.assertEqual(legacy.read_bytes(), raw)
        status = ownership.ownership_status(self.output, 'demo', A)
        self.assertEqual(status['epoch'], 42)
        self.assertTrue(status['expired'])
        self.assertFalse(ownership.claim_project_ownership(self.output, 'demo', B)['owned_by_requester'])

    def test_effective_forced_owner_is_restored_not_imported_legacy_owner(self):
        legacy = self.root.parent/'.project_ownership/demo.json'
        legacy.parent.mkdir()
        record = ownership._empty_record('demo')
        record.update(epoch=8, owner_digest=ownership._owner_digest(A))
        raw = state._encode(record)
        legacy.write_bytes(raw)
        self.claim(B, True)
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        status = ownership.ownership_status(self.output, 'demo', B)
        self.assertTrue(status['owned_by_requester'])
        self.assertEqual(status['epoch'], 9)
        self.assertEqual((self.output/recovery.RECOVERY/'ownership/legacy.json').read_bytes(), raw)
        with self.assertRaises(ownership.ProjectOwnershipError):
            ownership.require_project_ownership(self.output, 'demo', dict(owner_id=A, epoch=8))

    def test_lost_active_record_publication_resumes_without_overwrite(self):
        owned = self.claim()
        journal = self.prepare()
        real = recovery.publish_new_file
        def lost_ack(source, target):
            result = real(source, target)
            if Path(target) == self.output/'h3_chains/.project_ownership/demo.json':
                raise OSError('active ownership ack lost')
            return result
        with patch.object(recovery, 'publish_new_file', lost_ack), self.assertRaisesRegex(OSError, 'ack lost'):
            recovery.recover_legacy_copy(journal)
        active = self.output/'h3_chains/.project_ownership/demo.json'
        before = active.read_bytes()
        self.assertTrue((self.f.restored/'storage.json').is_file())
        recovery.recover_legacy_copy(journal)
        self.assertEqual(before, active.read_bytes())
        ownership.require_project_ownership(self.output, 'demo', dict(owner_id=A, epoch=owned['epoch']))

    def test_changed_target_ownership_is_not_overwritten_by_retry(self):
        self.claim()
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        changed = ownership.claim_project_ownership(self.output, 'demo', B, force=True)
        with self.assertRaisesRegex(ValueError, 'changed/corrupt'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(ownership.ownership_status(self.output, 'demo', B)['epoch'], changed['epoch'])

    def test_source_takeover_after_inventory_rejects_before_target_creation(self):
        self.claim()
        journal = self.prepare()
        self.claim(B, True)
        with self.assertRaisesRegex(state.StateConflict, 'ownership changed since recovery'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.output.exists())

    def test_source_takeover_waits_until_recovery_is_published(self):
        owned = self.claim()
        journal = self.prepare()
        entered, finished = threading.Event(), threading.Event()
        errors, workers = [], []
        def takeover():
            entered.set()
            try:
                # Copy-only access is deliberately granted in this worker;
                # no inherited ownership session is required by a fresh job.
                with state.control_rehearsal_access(self.root):
                    self.claim(B, True)
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()
        def pause(index):
            if index == 1:
                worker = threading.Thread(target=takeover)
                workers.append(worker)
                worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(finished.wait(.05))
        recovery.recover_legacy_copy(journal, after_copy=pause)
        workers[0].join(3)
        self.assertFalse(workers[0].is_alive())
        self.assertEqual(errors, [])
        ownership.require_project_ownership(self.output, 'demo', dict(owner_id=A, epoch=owned['epoch']))

    def test_missing_bootstrap_and_acknowledged_record_are_never_downgraded(self):
        self.claim()
        path = self.directory/CommitLog._address(1)
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing immutable commit'):
            self.prepare()
        path.write_bytes(raw)
        (self.directory/'storage.json').rename(self.lab/'retained-ownership-bootstrap.json')
        with self.assertRaisesRegex(ValueError, 'Missing external ownership bootstrap'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_new_journal_cannot_be_read_by_older_tree_only_recovery(self):
        self.claim()
        journal = self.prepare()
        plan = json.loads((journal.parent/'plan.json').read_text())
        self.assertNotEqual(plan['format'], recovery.LEGACY_PLAN)
        self.assertEqual(plan['format'], recovery.PLAN)
        self.assertTrue(plan['external_ownership']['files'])

    def test_released_project_stays_protected_with_no_owner(self):
        owned = self.claim()
        with runtime_access(self.store, ownership_writes=True):
            released = ownership.release_project_ownership(self.source_output, 'demo', A, owned['epoch'])
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        status = ownership.ownership_status(self.output, 'demo')
        self.assertTrue(status['enabled'])
        self.assertTrue(status['available'])
        self.assertEqual(status['epoch'], released['epoch'])
        with self.assertRaises(ownership.ProjectOwnershipError):
            ownership.require_project_ownership(self.output, 'demo', None)

    def old_journal_fixture(self, journal):
        # Construct the exact older on-disk schema in this disposable fixture.
        # This is test data, never a downgrade operation offered by recovery.
        plan_path = journal.parent/'plan.json'
        plan = json.loads(plan_path.read_text())
        plan['format'] = recovery.LEGACY_PLAN
        plan.pop('external_ownership')
        raw = state._encode(plan)
        bootstrap = json.loads(journal.read_text())
        bootstrap['plan_sha256'] = state._hash(raw)
        plan_path.write_bytes(raw)
        journal.write_bytes(state._encode(bootstrap))

    def test_older_unowned_journal_remains_usable(self):
        journal = self.prepare()
        self.old_journal_fixture(journal)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        self.assertFalse(ownership.ownership_status(self.output, 'demo')['enabled'])

    def test_unowned_status_reader_lock_does_not_break_completed_retry(self):
        journal = self.prepare()
        result = recovery.recover_legacy_copy(journal)
        self.assertFalse(ownership.ownership_status(self.output, 'demo')['enabled'])
        self.assertEqual(recovery.recover_legacy_copy(journal), result)
        self.assertFalse((self.output/'h3_chains/.project_ownership/demo.json').exists())

    def test_older_journal_cannot_omit_a_new_external_fence(self):
        journal = self.prepare()
        self.old_journal_fixture(journal)
        self.claim()
        with self.assertRaisesRegex(ValueError, 'external ownership inventory'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.output.exists())

    def test_edited_ownership_archive_blocks_resume_and_keeps_both_sources(self):
        self.claim()
        journal = self.prepare()
        plan = json.loads((journal.parent/'plan.json').read_text())
        first = external.entries(plan)[0]
        def stop(index):
            if index == len(plan['rows'])+1:
                raise OSError('stop after first ownership archive')
        with self.assertRaises(OSError):
            recovery.recover_legacy_copy(journal, after_copy=stop)
        archived = self.output/first['target']
        archived.write_bytes(b'keep modified archive')
        with self.assertRaisesRegex(ValueError, 'collision'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(archived.read_bytes(), b'keep modified archive')
        self.assertTrue((self.f.restored/'storage.json').is_file())
        self.assertEqual(self.store.snapshot().reference, self.f.current.reference)

    def test_external_ownership_symlink_cannot_reach_another_project(self):
        self.claim()
        path = self.directory/'storage.json'
        raw = path.read_bytes()
        outside = self.lab/'another-owner.json'
        outside.write_bytes(raw)
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            if os.name == 'nt':
                self.skipTest('Windows fixture needs symlink permission: '+str(exc))
            raise
        with self.assertRaisesRegex(ValueError, 'symlinks or junctions'):
            self.prepare()
        self.assertEqual(outside.read_bytes(), raw)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
