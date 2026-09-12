"""Recovery includes new work without changing the organized source copy."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import _storage_resolver_unit_test as fixture

recovery = importlib.import_module('storage_recovery')
writes = importlib.import_module('storage_writes')
resolver = fixture.resolver


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.RelocationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.output = self.fixture.root, self.fixture.output
        journal = fixture.rehearsal.prepare(self.fixture.receipt, self.fixture.proposal,
            self.fixture.lab/'migration', organized_writers=True)
        fixture.rehearsal.relocate(journal)
        scope = resolver.rehearsal_access(self.root)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)
        self.new = 'segments/clip_0002.'+'d'*32+'.mp4'
        paths = writes.reserve_take(self.output, {'video':str(self.root/self.new)},
            stage='generation', identity='new take')
        path = Path(paths['video'])
        path.parent.mkdir(parents=True)
        path.write_bytes(b'new video')
        self.physical = path
        # These are accepted CURRENT controls, not the pre-migration receipt.
        self.control = self.root/'checkpoints/clip_0001.json'
        self.control.write_bytes(b'{"seed":18446744073709551613,"prompt":"new draft"}')
        png = resolver.resolve_output(self.output, self.root/self.fixture.png)
        (png/'frame_00000002.png').write_bytes(b'new PNG')
        (png/'export.json').write_bytes(b'{"frames":2}')
        self.target = self.fixture.lab/'legacy-output'
        self.restored = self.target/'h3_chains/demo'
        self.folder = self.fixture.lab/'recovery'

    def prepare(self):
        return recovery.prepare_legacy_copy(self.fixture.receipt, self.target, self.folder)

    def before(self):
        return {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_latest_controls_new_takes_and_png_append_survive_independently(self):
        before = self.before()
        journal = self.prepare()
        result = recovery.recover_legacy_copy(journal)
        self.assertEqual(before, self.before())
        self.assertTrue(result['source_unchanged'])
        self.assertFalse((self.restored/'storage.json').exists())
        self.assertEqual((self.restored/self.new).read_bytes(), b'new video')
        self.assertNotEqual((self.restored/self.new).stat().st_ino, self.physical.stat().st_ino)
        self.assertEqual((self.restored/'checkpoints/clip_0001.json').read_bytes(), self.control.read_bytes())
        self.assertEqual((self.restored/self.fixture.png/'frame_00000002.png').read_bytes(), b'new PNG')
        self.assertEqual((self.restored/self.fixture.png/'export.json').read_bytes(), b'{"frames":2}')
        self.assertEqual(resolver.resolve_output(self.target, 'h3_chains/demo/'+self.new), self.restored/self.new)
        self.assertEqual((self.target/recovery.RECOVERY/'authority/storage.json').read_bytes(), (self.root/'storage.json').read_bytes())
        self.assertEqual(result, recovery.recover_legacy_copy(journal))

    def test_interrupted_copy_stays_gated_and_resumes(self):
        before = self.before()
        journal = self.prepare()
        def fail(index):
            if index == 2:
                raise OSError('simulated interrupt')
        with self.assertRaisesRegex(OSError,'interrupt'):
            recovery.recover_legacy_copy(journal,after_copy=fail)
        with self.assertRaisesRegex(ValueError,'Unsupported'):
            resolver.storage_state(self.restored)
        recovery.recover_legacy_copy(journal)
        self.assertEqual(self.before(), before)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_stale_current_control_prevents_initial_copy(self):
        journal = self.prepare()
        self.control.write_bytes(b'{"prompt":"newer draft"}')
        with self.assertRaisesRegex(ValueError,'Source state changed'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.control.read_bytes(),b'{"prompt":"newer draft"}')

    def test_source_change_during_copy_blocks_publication_without_restoring_old_state(self):
        journal = self.prepare()
        def change(index):
            if index == 2:
                self.control.write_bytes(b'{"seed":42}')
        with self.assertRaisesRegex(ValueError,'Source state changed'):
            recovery.recover_legacy_copy(journal,after_copy=change)
        self.assertTrue((self.restored/'storage.json').is_file())
        self.assertEqual(self.control.read_bytes(),b'{"seed":42}')

    def test_untracked_source_addition_blocks_publication(self):
        journal = self.prepare()
        def add(index):
            if index == 1:
                (self.root/'new-control.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'Source namespace changed'):
            recovery.recover_legacy_copy(journal,after_copy=add)
        self.assertTrue((self.restored/'storage.json').exists())

    def test_target_edit_is_preserved_not_overwritten_on_resume(self):
        journal = self.prepare()
        def change(index):
            if index == 1:
                (self.restored/'checkpoints/clip_0001.json').write_bytes(b'user edit')
                raise OSError('interrupt')
        with self.assertRaises(OSError):
            recovery.recover_legacy_copy(journal,after_copy=change)
        with self.assertRaisesRegex(ValueError,'collision'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual((self.restored/'checkpoints/clip_0001.json').read_bytes(),b'user edit')
        self.assertTrue(self.physical.is_file())

    def test_unowned_existing_target_is_not_adopted(self):
        journal = self.prepare()
        self.target.mkdir()
        (self.target/'user.txt').write_text('keep me')
        with self.assertRaises(FileExistsError):
            recovery.recover_legacy_copy(journal)
        self.assertEqual((self.target/'user.txt').read_text(),'keep me')

    def test_plan_change_is_rejected_before_copy(self):
        journal = self.prepare()
        plan = self.folder/'plan.json'
        plan.write_bytes(plan.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'immutable recovery plan'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse(self.target.exists())

    def test_failed_publication_acknowledgement_is_resumable(self):
        journal = self.prepare()
        original = recovery.atomic_json
        def fail(path,value):
            if Path(path)==journal and value.get('phase')=='published':
                raise OSError('lost acknowledgement')
            return original(path,value)
        with patch.object(recovery,'atomic_json',fail),self.assertRaisesRegex(OSError,'acknowledgement'):
            recovery.recover_legacy_copy(journal)
        self.assertFalse((self.restored/'storage.json').exists())
        recovery.recover_legacy_copy(journal)
        self.assertEqual(json.loads(journal.read_text())['phase'],'published')

    def test_failed_copy_keeps_partial_and_source_bytes(self):
        journal = self.prepare()
        before = self.before()
        with patch.object(recovery,'publish_new_file',side_effect=OSError('publication unavailable')):
            with self.assertRaisesRegex(OSError,'unavailable'):
                recovery.recover_legacy_copy(journal)
        self.assertEqual(before,self.before())
        self.assertTrue(list((self.folder/'partials').glob('*.part')))
        recovery.recover_legacy_copy(journal)

    def test_insufficient_space_does_not_create_recovery_target(self):
        journal = self.prepare()
        before = self.before()
        with patch.object(recovery.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(OSError, 'Insufficient space'):
                recovery.recover_legacy_copy(journal)
        self.assertFalse(self.target.exists())
        self.assertEqual(before, self.before())

    def test_lost_file_publication_ack_keeps_bytes_and_resumes(self):
        journal = self.prepare()
        original = recovery.publish_new_file
        published = []
        def publish_then_fail(source, target):
            expected = Path(source).read_bytes()
            original(source, target)
            published.append((Path(target), expected))
            raise OSError('lost file acknowledgement')
        with patch.object(recovery, 'publish_new_file', publish_then_fail):
            with self.assertRaisesRegex(OSError, 'acknowledgement'):
                recovery.recover_legacy_copy(journal)
        self.assertTrue((self.restored/'storage.json').is_file())
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][0].read_bytes(), published[0][1])
        recovery.recover_legacy_copy(journal)
        self.assertEqual((self.restored/self.new).read_bytes(), b'new video')

    def test_lost_verified_journal_ack_keeps_gate_until_resume(self):
        journal = self.prepare()
        original = recovery.atomic_json
        def publish_then_fail(path, value):
            original(path, value)
            if Path(path) == journal and value.get('phase') == 'verified':
                raise OSError('lost verification acknowledgement')
        with patch.object(recovery, 'atomic_json', publish_then_fail):
            with self.assertRaisesRegex(OSError, 'acknowledgement'):
                recovery.recover_legacy_copy(journal)
        self.assertTrue((self.restored/'storage.json').is_file())
        self.assertEqual(json.loads(journal.read_text())['phase'], 'verified')
        recovery.recover_legacy_copy(journal)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_changed_verification_proof_is_never_overwritten(self):
        journal = self.prepare()
        def fail(index):
            raise OSError('interrupt')
        with self.assertRaises(OSError):
            recovery.recover_legacy_copy(journal, after_copy=fail)
        proof = self.target/recovery.RECOVERY/'verified.json'
        proof.write_bytes(b'{"user":"keep this"}')
        with self.assertRaisesRegex(ValueError, 'proof changed'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(proof.read_bytes(), b'{"user":"keep this"}')
        self.assertTrue((self.restored/'storage.json').is_file())

    def test_untracked_target_blocks_release_and_preserves_file(self):
        journal = self.prepare()
        def add(index):
            if index == 1:
                (self.target/'user.txt').write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'untracked files'):
            recovery.recover_legacy_copy(journal, after_copy=add)
        self.assertEqual((self.target/'user.txt').read_bytes(), b'keep')
        self.assertTrue((self.restored/'storage.json').is_file())

    def test_proof_created_during_copy_is_not_overwritten(self):
        journal = self.prepare()
        proof = self.target/recovery.RECOVERY/'verified.json'
        def add(index):
            if index == 1:
                proof.write_bytes(b'{"user":"new proof"}')
        with self.assertRaisesRegex(ValueError, 'proof changed before publication'):
            recovery.recover_legacy_copy(journal, after_copy=add)
        self.assertEqual(proof.read_bytes(), b'{"user":"new proof"}')
        self.assertTrue((self.restored/'storage.json').is_file())

    def test_owner_changed_during_copy_keeps_gate(self):
        journal = self.prepare()
        owner = self.target/recovery.RECOVERY/'owner.json'
        def change(index):
            if index == 1:
                owner.write_bytes(b'{"user":"different owner"}')
        with self.assertRaisesRegex(ValueError, 'ownership/gate/proof changed'):
            recovery.recover_legacy_copy(journal, after_copy=change)
        self.assertEqual(owner.read_bytes(), b'{"user":"different owner"}')
        self.assertTrue((self.restored/'storage.json').is_file())

    def test_published_copy_edits_are_not_replaced_or_regated(self):
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        changed = self.restored/'checkpoints/clip_0001.json'
        changed.write_bytes(b'{"prompt":"edited in recovered project"}')
        with self.assertRaisesRegex(ValueError, 'changed/corrupt'):
            recovery.recover_legacy_copy(journal)
        self.assertEqual(changed.read_bytes(), b'{"prompt":"edited in recovered project"}')
        self.assertFalse((self.restored/'storage.json').exists())

    def test_actual_checkpoint_reader_lock_does_not_break_completed_retry(self):
        from checkpoint_manager import checkpoint_run_lock
        path = self.prepare()
        result = recovery.recover_legacy_copy(path)
        with checkpoint_run_lock(str(self.target), self.root.name):
            pass
        self.assertEqual(recovery.recover_legacy_copy(path), result)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_another_run_lock_is_still_untracked(self):
        path = self.prepare()
        recovery.recover_legacy_copy(path)
        other = self.target/'h3_chains/.run_locks/other.lock'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'untracked'):
            recovery.recover_legacy_copy(path)
        self.assertTrue(other.exists())

    def test_data_under_expected_run_lock_name_is_not_ignored(self):
        path = self.prepare()
        recovery.recover_legacy_copy(path)
        other = self.target/'h3_chains/.run_locks'/f'{self.root.name}.lock'
        other.parent.mkdir(parents=True)
        other.write_bytes(b'user data, not a coordination lock')
        with self.assertRaisesRegex(ValueError, 'coordination lock'):
            recovery.recover_legacy_copy(path)
        self.assertEqual(other.read_bytes(), b'user data, not a coordination lock')

    def test_symlink_source_is_rejected_without_touching_outside_file(self):
        outside = self.fixture.lab/'outside.txt'
        outside.write_bytes(b'keep')
        link = self.root/'unexpected-link.txt'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit symbolic links')
        with self.assertRaisesRegex(ValueError, 'symlinks or junctions'):
            self.prepare()
        self.assertEqual(outside.read_bytes(), b'keep')
        self.assertFalse(self.target.exists())

    def test_source_change_after_its_hash_is_not_missed(self):
        journal = self.prepare()
        _, _, plan, source, _ = recovery._load(journal)
        original = recovery.sha256
        last = source/plan['rows'][-1]['source']
        def hash_then_change(path):
            value = original(path)
            if Path(path) == last:
                self.control.write_bytes(b'{"prompt":"concurrent draft"}')
            return value
        with patch.object(recovery, 'sha256', hash_then_change):
            with self.assertRaisesRegex(ValueError, 'changed during recovery verification'):
                recovery._verify_source(plan, source)
        self.assertEqual(self.control.read_bytes(), b'{"prompt":"concurrent draft"}')

    def test_target_change_after_its_hash_is_not_missed(self):
        journal = self.prepare()
        recovery.recover_legacy_copy(journal)
        _, _, plan, _, target = recovery._load(journal)
        original = recovery.sha256
        last = target/plan['rows'][-1]['target']
        changed = self.restored/'checkpoints/clip_0001.json'
        def hash_then_change(path):
            value = original(path)
            if Path(path) == last:
                changed.write_bytes(b'{"prompt":"concurrent recovered draft"}')
            return value
        with patch.object(recovery, 'sha256', hash_then_change):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                recovery._verify_target(plan, target, gate=False)
        self.assertFalse((self.restored/'storage.json').exists())

    def test_unmapped_organized_file_is_not_guessed(self):
        path = self.root/'media/unowned.mp4'
        path.write_bytes(b'user video')
        with self.assertRaisesRegex(ValueError,'explicit classification'):
            self.prepare()
        self.assertFalse(self.target.exists())

    def test_case_collision_is_rejected(self):
        path = self.root/'notes.txt'
        path.write_bytes(b'a')
        other = self.root/'NOTES.txt'
        other.write_bytes(b'b')
        if path.stat().st_ino == other.stat().st_ino:
            self.skipTest('Case-insensitive filesystem already prevents distinct names')
        with self.assertRaisesRegex(ValueError,'Case-colliding'):
            self.prepare()

    def test_declared_windows_budget_blocks_long_legacy_destinations(self):
        with self.assertRaisesRegex(ValueError,'budget'):
            recovery.prepare_legacy_copy(self.fixture.receipt,self.target,self.folder,path_budget=80)
        self.assertFalse(self.folder.exists())


if __name__=='__main__':
    unittest.main()
