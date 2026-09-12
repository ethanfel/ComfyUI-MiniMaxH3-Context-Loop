"""No-overwrite journal, media-join and reverse-copy failure/retry coverage."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import _storage_project_migration_unit_test as joining
import _storage_recovery_unit_test as reversing
import storage_journal as journal
import storage_commit_log as commits
import storage_state as state
import storage_recovery as recovery


class JournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder/'journal.json'
        self.initial = {'format': 'test_journal', 'operation_id': uuid.uuid4().hex,
                        'plan_sha256': 'a'*64, 'phase': 'prepared'}
        journal.create(self.path, self.initial)
        self.bootstrap = self.path.read_bytes()
        self.base = journal.read(self.path)

    def test_phases_never_replace_bootstrap_and_duplicate_adds_no_record(self):
        with patch.object(state, 'atomic_json', side_effect=AssertionError('replacement')):
            current = self.base
            for phase in ('copying', 'verified', 'published'):
                current = journal.advance(self.path, current, dict(current, phase=phase))
            before = commits.CommitLog(self.folder, self.bootstrap).read()
            journal.advance(self.path, current, current)
        self.assertEqual(self.path.read_bytes(), self.bootstrap)
        self.assertEqual(commits.CommitLog(self.folder, self.bootstrap).read(), before)
        self.assertEqual(before.sequence, 3)
        self.assertEqual(journal.read(self.path)['phase'], 'published')

    def test_stale_phase_cannot_replace_new_progress(self):
        journal.advance(self.path, self.base, dict(self.base, phase='copying'))
        with self.assertRaisesRegex(state.StateConflict, 'phase changed'):
            journal.advance(self.path, self.base, dict(self.base, phase='published'))
        self.assertEqual(journal.read(self.path)['phase'], 'copying')

    def test_operation_identity_cannot_change(self):
        for key in ('format', 'operation_id', 'plan_sha256'):
            with self.subTest(key=key), self.assertRaisesRegex(state.StateConflict, 'identity'):
                journal.advance(self.path, self.base, dict(self.base, **{key: 'changed'}))
        self.assertEqual(journal.read(self.path), self.base)

    def test_lost_acknowledgement_resumes_same_phase_without_extra_record(self):
        proposed = dict(self.base, phase='copying')
        with patch.object(commits.CommitLog, 'acknowledge', side_effect=OSError('lost ack')):
            with self.assertRaisesRegex(OSError, 'lost ack'):
                journal.advance(self.path, self.base, proposed)
        head = commits.CommitLog(self.folder, self.bootstrap).read()
        self.assertFalse(head.acknowledged)
        self.assertEqual(head.value, proposed)
        journal.advance(self.path, self.base, proposed)
        retried = commits.CommitLog(self.folder, self.bootstrap).read()
        self.assertTrue(retried.acknowledged)
        self.assertEqual(retried.reference, head.reference)

    def test_missing_acknowledged_record_fails_without_older_phase_fallback(self):
        journal.advance(self.path, self.base, dict(self.base, phase='copying'))
        record = self.folder/commits.CommitLog._address(1)
        record.rename(self.folder/'retained-commit.json')
        with self.assertRaisesRegex(ValueError, 'Missing immutable commit'):
            journal.read(self.path)
        with self.assertRaisesRegex(ValueError, 'Missing immutable commit'):
            journal.advance(self.path, self.base, dict(self.base, phase='verified'))

    def test_read_only_bootstrap_does_not_create_coordination_files(self):
        unknown = self.folder/'untrusted'
        unknown.mkdir()
        path = unknown/'journal.json'
        path.write_bytes(self.bootstrap)
        journal.bootstrap(path)
        self.assertEqual([p.name for p in unknown.iterdir()], ['journal.json'])

    def test_plan_or_gate_collision_preserves_existing_bytes(self):
        path = self.folder/'gate.json'
        journal.publish_once(path, {'owner': 'first'})
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'collision'):
            journal.publish_once(path, {'owner': 'second'})
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_protocol_and_recreating_journal_are_rejected(self):
        with self.assertRaises(ValueError):
            journal.protocol('unknown')
        with self.assertRaises(ValueError):
            journal.create(self.path, self.initial)
        self.assertEqual(self.path.read_bytes(), self.bootstrap)


class LogJoinTests(joining.JoinTests):
    commit_protocol = commits.PROTOCOL

    def test_join_uses_log_without_overwriting_immutable_bootstrap(self):
        before = (self.root/'storage.json').read_bytes()
        with patch.object(joining.migration, 'atomic_json', side_effect=AssertionError('replacement')):
            self.prepare()
            self.join()
        self.assertEqual((self.root/'storage.json').read_bytes(), before)
        self.assert_ready()
        self.assertEqual(journal.read(self.journal)['phase'], 'published')

    def test_failed_final_pointer_can_resume_with_same_candidate(self):
        self.prepare()
        original = commits.CommitLog.append
        def fail_ready(log, value, *args, **kwargs):
            if log.project == self.root and value.get('phase') == 'ready':
                raise OSError('final commit unavailable')
            return original(log, value, *args, **kwargs)
        with patch.object(commits.CommitLog, 'append', fail_ready), self.assertRaises(OSError):
            self.join()
        self.assertEqual(journal.authority(self.root)[0]['phase'], 'building')
        candidate = self.root/'project/roots'/f'{journal.read(self.journal)["operation_id"]}.json'
        before = candidate.read_bytes()
        self.join()
        self.assert_ready()
        self.assertEqual(candidate.read_bytes(), before)

    def test_post_publication_ack_failure_and_retry_keep_later_branch_save(self):
        self.prepare()
        original = commits.CommitLog.acknowledge
        def fail_ready(log, head):
            if log.project == self.root and head.value.get('phase') == 'ready':
                raise OSError('lost commit acknowledgement')
            return original(log, head)
        with patch.object(commits.CommitLog, 'acknowledge', fail_ready), self.assertRaises(OSError):
            self.join()
        current = self.assert_ready()
        self.store.commit(current, {'checkpoints/clip_0001.json': {'data': b'{"seed":2}',
            'scope': 'branch:A', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        reference = self.store.snapshot().reference
        self.join()
        self.assertEqual(self.store.snapshot().reference, reference)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'), b'{"seed":2}')
        self.assertEqual(journal.read(self.journal)['phase'], 'published')

    def test_gate_ack_failure_stays_blocked_then_resumes(self):
        self.prepare()
        original = commits.CommitLog.acknowledge
        def fail_gate(log, head):
            if log.project == self.root and head.value.get('phase') == 'building':
                raise OSError('lost gate acknowledgement')
            return original(log, head)
        with patch.object(commits.CommitLog, 'acknowledge', fail_gate), self.assertRaises(OSError):
            self.join()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.store.snapshot()
        self.join()
        self.assert_ready()

    def test_join_progress_ack_failure_resumes(self):
        self.prepare()
        original = commits.CommitLog.acknowledge
        def fail_progress(log, head):
            if log.project == self.folder and head.value.get('phase') == 'copying':
                raise OSError('lost progress acknowledgement')
            return original(log, head)
        with patch.object(commits.CommitLog, 'acknowledge', fail_progress), self.assertRaises(OSError):
            self.join()
        self.assertEqual(journal.read(self.journal)['phase'], 'copying')
        self.join()
        self.assert_ready()


class LogRecoveryTests(reversing.RecoveryTests):
    def prepare(self):
        return recovery.prepare_legacy_copy(self.fixture.receipt, self.target, self.folder,
                                            commit_protocol=commits.PROTOCOL)

    def test_failed_publication_acknowledgement_is_resumable(self):
        path = self.prepare()
        original = commits.CommitLog.acknowledge
        def fail_published(log, head):
            if log.project == self.folder and head.value.get('phase') == 'published':
                raise OSError('lost acknowledgement')
            return original(log, head)
        with patch.object(commits.CommitLog, 'acknowledge', fail_published), self.assertRaises(OSError):
            recovery.recover_legacy_copy(path)
        self.assertFalse((self.restored/'storage.json').exists())
        before = journal._log(path).read().reference
        recovery.recover_legacy_copy(path)
        self.assertEqual(journal.read(path)['phase'], 'published')
        self.assertEqual(journal._log(path).read().reference, before)

    def test_lost_verified_journal_ack_keeps_gate_until_resume(self):
        path = self.prepare()
        original = commits.CommitLog.acknowledge
        def fail_verified(log, head):
            if log.project == self.folder and head.value.get('phase') == 'verified':
                raise OSError('lost verification acknowledgement')
            return original(log, head)
        with patch.object(commits.CommitLog, 'acknowledge', fail_verified), self.assertRaises(OSError):
            recovery.recover_legacy_copy(path)
        self.assertTrue((self.restored/'storage.json').is_file())
        self.assertEqual(journal.read(path)['phase'], 'verified')
        recovery.recover_legacy_copy(path)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_copy_uses_no_replacement_for_plan_gate_proof_or_journal(self):
        with patch.object(recovery, 'atomic_json', side_effect=AssertionError('replacement')):
            path = self.prepare()
            before = path.read_bytes()
            recovery.recover_legacy_copy(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(journal.read(path)['phase'], 'published')

    def test_failed_proof_publication_keeps_partials_outside_target_and_resumes(self):
        path = self.prepare()
        original = state.publish_new_file
        def fail_proof(source, target):
            if Path(target).name == 'verified.json':
                raise OSError('proof publication unavailable')
            return original(source, target)
        with patch.object(state, 'publish_new_file', fail_proof), self.assertRaises(OSError):
            recovery.recover_legacy_copy(path)
        self.assertTrue((self.restored/'storage.json').exists())
        self.assertFalse(list(self.target.rglob('.tmp-*')))
        recovery.recover_legacy_copy(path)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_failed_initial_owner_publication_resumes_without_adopting_target(self):
        path = self.prepare()
        original = state.publish_new_file
        def fail_owner(source, target):
            if Path(target).name == 'owner.json':
                raise OSError('owner publication unavailable')
            return original(source, target)
        with patch.object(state, 'publish_new_file', fail_owner), self.assertRaises(OSError):
            recovery.recover_legacy_copy(path)
        self.assertFalse(self.target.exists())
        recovery.recover_legacy_copy(path)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_lost_target_directory_ack_reflushes_before_progress(self):
        path = self.prepare()
        original = recovery.sync_directory
        def fail_parent(directory):
            if Path(directory) == self.target.parent:
                raise OSError('target directory flush unavailable')
            return original(directory)
        with patch.object(recovery, 'sync_directory', fail_parent):
            for attempt in range(2):
                with self.subTest(attempt=attempt), self.assertRaisesRegex(OSError, 'flush'):
                    recovery.recover_legacy_copy(path)
                self.assertTrue((self.restored/'storage.json').is_file())
                self.assertEqual(journal.read(path)['phase'], 'prepared')
        recovery.recover_legacy_copy(path)
        self.assertEqual(journal.read(path)['phase'], 'published')

    def test_empty_foreign_target_is_not_adopted(self):
        path = self.prepare()
        self.target.mkdir()
        with self.assertRaisesRegex(FileExistsError, 'not owned'):
            recovery.recover_legacy_copy(path)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_explicit_long_path_budget_applies_to_recovery_authorities(self):
        self.target = self.fixture.lab/('long-'+'x'*150)/'legacy-output'
        self.restored = self.target/'h3_chains/demo'
        path = recovery.prepare_legacy_copy(self.fixture.receipt, self.target, self.folder,
            path_budget=512, commit_protocol=commits.PROTOCOL)
        recovery.recover_legacy_copy(path)
        self.assertEqual(journal.read(path)['path_budget'], 512)
        self.assertEqual((self.restored/self.new).read_bytes(), b'new video')

    def test_invalid_scope_creates_no_journal_lock(self):
        path = self.prepare()
        outside = self.fixture.lab/'outside-source-output'
        outside.mkdir()
        # Valid checksums cannot turn an invalid receipted scope into authority.
        raw_plan = json.loads((self.folder/'plan.json').read_text())
        raw_plan['destination'] = str(self.output/'forbidden-target')
        (outside/'plan.json').write_bytes(state._encode(raw_plan))
        initial = journal.bootstrap(path)[0]
        initial['plan_sha256'] = state._hash((outside/'plan.json').read_bytes())
        (outside/'journal.json').write_bytes(state._encode(initial))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            recovery._load(outside/'journal.json')
        self.assertFalse((outside/'.journal.lock').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
