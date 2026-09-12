"""Existing ownership API against external no-overwrite runtime fences."""
import asyncio
from contextvars import copy_context
import json
import threading
import unittest
from unittest.mock import patch

import _storage_runtime_unit_test as fixture
import storage_state as state
import storage_commit_log as ledger
from storage_runtime import runtime_access
from storage_ownership import authority_directory
from working_branches import WorkingBranches
import project_ownership as ownership

A, B = 'workflow-owner-a-1234567890', 'workflow-owner-b-1234567890'


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output = self.f.store, self.f.output
        self.directory = authority_directory(self.output, 'demo')

    def claim(self, owner=A, force=False):
        result = ownership.claim_project_ownership(self.output, 'demo', owner, owner, force=force)
        return {'owner_id': owner, 'epoch': result['epoch']}

    def test_claim_takeover_heartbeat_release_keep_public_semantics(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, ownership_writes=True):
            self.assertFalse(ownership.ownership_status(self.output, 'demo')['enabled'])
            a = self.claim()
            self.assertEqual(a['epoch'], 1)
            self.assertEqual(ownership.require_project_ownership(self.output, 'demo', a), dict(a, run_name='demo'))
            rejected = ownership.claim_project_ownership(self.output, 'demo', B)
            self.assertFalse(rejected['owned_by_requester'])
            b = self.claim(B, force=True)
            self.assertEqual(b['epoch'], 2)
            with self.assertRaisesRegex(ownership.ProjectOwnershipError, 'stale workflow'):
                ownership.require_project_ownership(self.output, 'demo', a)
            self.assertFalse(ownership.heartbeat_project_ownership(self.output, 'demo', A, 1)['owned_by_requester'])
            released = ownership.release_project_ownership(self.output, 'demo', B, b['epoch'])
            self.assertEqual(released['epoch'], 3)
            self.assertTrue(released['available'])
            with self.assertRaises(ownership.ProjectOwnershipError):
                ownership.require_project_ownership(self.output, 'demo', b)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertTrue(self.directory.is_dir())
        self.assertFalse(self.directory.is_relative_to(self.store.project))
        for path in self.directory.rglob('*.json'):
            # Labels are not credentials; use different labels for this check.
            self.assertNotIn('"owner_id"', path.read_text())

    def test_copy_read_access_cannot_claim_ownership(self):
        with runtime_access(self.store):
            with self.assertRaisesRegex(ValueError, 'ownership changes'):
                self.claim()
        self.assertFalse(self.directory.exists())

    def test_import_existing_fence_keeps_epoch_and_exact_legacy_bytes(self):
        legacy = self.output/'h3_chains/.project_ownership/demo.json'
        legacy.parent.mkdir(parents=True)
        record = ownership._empty_record('demo')
        record.update(epoch=42, owner_digest=ownership._owner_digest(A), owner_label='Existing owner', lease_expires_at=0)
        raw = (json.dumps(record, indent=3)+'\r\n').encode()
        legacy.write_bytes(raw)
        with runtime_access(self.store, ownership_writes=True):
            self.assertTrue(ownership.ownership_status(self.output, 'demo', A)['expired'])
            rejected = ownership.claim_project_ownership(self.output, 'demo', B)
            self.assertFalse(rejected['owned_by_requester'])  # expiry never transfers
            b = self.claim(B, force=True)
            self.assertEqual(b['epoch'], 43)
        self.assertEqual(legacy.read_bytes(), raw)
        legacy.write_bytes(b'{}')
        with runtime_access(self.store), self.assertRaisesRegex(ValueError, 'authority'):
            ownership.ownership_status(self.output, 'demo')

    def test_existing_external_fence_cannot_fall_back_after_project_is_moved(self):
        with runtime_access(self.store, ownership_writes=True):
            self.claim()
        self.store.project.rename(self.output/'retained-project')
        with self.assertRaisesRegex(ValueError, 'no legacy fallback'):
            ownership.require_project_ownership(self.output, 'demo', None)
        self.assertTrue((self.directory/'storage.json').is_file())

    def test_missing_acknowledged_record_or_bootstrap_cannot_make_run_unowned(self):
        with runtime_access(self.store, ownership_writes=True):
            self.claim()
        path = self.directory/ledger.CommitLog._address(1)
        raw = path.read_bytes()
        path.unlink()
        with runtime_access(self.store), self.assertRaisesRegex(ValueError, 'Missing immutable commit'):
            ownership.ownership_status(self.output, 'demo')
        path.write_bytes(raw)
        (self.directory/'storage.json').rename(self.output/'retained-bootstrap.json')
        with runtime_access(self.store), self.assertRaisesRegex(ValueError, 'no legacy fallback'):
            ownership.ownership_status(self.output, 'demo')

    def test_lost_acknowledgement_retry_never_reverts_owner_or_epoch(self):
        with runtime_access(self.store, ownership_writes=True):
            with patch.object(ledger.CommitLog, 'acknowledge', side_effect=OSError('lost ack')):
                with self.assertRaisesRegex(OSError, 'lost ack'):
                    self.claim()
            self.assertEqual(ownership.ownership_status(self.output, 'demo', A)['epoch'], 1)
            proof = self.claim()
            self.assertEqual(proof['epoch'], 1)
            self.assertTrue(ownership.ownership_status(self.output, 'demo', A)['owned_by_requester'])
        log = ledger.CommitLog(self.directory, (self.directory/'storage.json').read_bytes())
        self.assertTrue(log.read().acknowledged)

    def test_force_cannot_split_branch_commit_even_with_inherited_context(self):
        entered, finished = threading.Event(), threading.Event()
        errors = []
        with runtime_access(self.store, ownership_writes=True, branch_writes=True):
            a = self.claim()
            with ownership.project_write_guard(self.output, 'demo', a):
                context = copy_context()
                def takeover():
                    entered.set()
                    try:
                        context.run(self.claim, B, True)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        finished.set()
                worker = threading.Thread(target=takeover)
                worker.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(finished.wait(.05))
                branches = WorkingBranches(self.output, 'demo')
                loaded = branches.load()
                branches.save('main', loaded['authoring'], loaded['revision'])
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            with self.assertRaises(ownership.ProjectOwnershipError):
                ownership.require_project_ownership(self.output, 'demo', a)

    def test_old_runtime_pin_uses_current_ownership_not_historical_owner(self):
        with runtime_access(self.store, ownership_writes=True) as runtime:
            pin, a = runtime.pin, self.claim()
        with runtime_access(self.store, ownership_writes=True):
            self.claim(B, True)
        with runtime_access(self.store, pin=pin):
            with self.assertRaises(ownership.ProjectOwnershipError):
                ownership.require_project_ownership(self.output, 'demo', a)

    def test_inherited_async_task_cannot_reenter_a_suspended_guard(self):
        async def run():
            with runtime_access(self.store, ownership_writes=True):
                a = self.claim()
                with ownership.project_write_guard(self.output, 'demo', a):
                    async def takeover():
                        with self.assertRaisesRegex(state.StateConflict, 'async tasks'):
                            self.claim(B, True)
                    await asyncio.create_task(takeover())
                    ownership.require_project_ownership(self.output, 'demo', a)
                self.assertEqual(self.claim(B, True)['epoch'], 2)
        asyncio.run(run())

    def test_external_authority_preserves_every_earlier_version(self):
        with runtime_access(self.store, ownership_writes=True):
            self.claim()
            old = {path: path.read_bytes() for path in self.directory.rglob('*') if path.is_file()}
            self.claim(B, True)
            self.claim(A, True)
        self.assertEqual(old, {path: path.read_bytes() for path in old})


if __name__ == '__main__':
    unittest.main(verbosity=2)
