"""Real control/media joining: independent copy, crash recovery, exact source."""
import json
from pathlib import Path
import shutil
import stat
import unittest
from unittest.mock import patch
import uuid

import _storage_resolver_unit_test as fixture
import storage_state as state
import storage_project as project
import storage_project_migration as migration


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RelocationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.activate()
        self.inventory, self.targets = {}, {}
        for address, raw in self.f.data.items():
            if address.endswith('.lock'):
                continue
            physical = fixture.resolver.resolve_output(self.f.output, self.f.address(address))
            if address.endswith('.json'):
                self.inventory[address] = {'source': physical.relative_to(self.f.root).as_posix(),
                    'sha256': state._hash(raw), 'scope': 'branch:A', 'category': 'branches', 'immutable': False}
            else:
                self.targets[address] = {'target': physical.relative_to(self.f.root).as_posix(),
                    'scope': 'archive:source', 'immutable': True}
        self.control = state.create_control_rehearsal(self.f.receipt, self.f.lab/'joined-output', self.inventory,
            commit_protocol=getattr(self, 'commit_protocol', None))
        self.root = self.control.project
        access = state.control_rehearsal_access(self.root)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        self.base = self.control.snapshot()
        self.store = project.ProjectStore(self.root)
        self.folder = self.f.lab/'join'

    def prepare(self, **kwargs):
        self.journal = migration.prepare_join(self.f.receipt, self.control, self.folder,
                                              kwargs.get('targets', self.targets))
        return self.journal

    def join(self, **kwargs):
        return migration.join_payloads(self.journal, **kwargs)

    def fail_copy(self, index):
        if index == 1:
            raise OSError('copy interrupted')

    def assert_ready(self):
        current = self.store.snapshot()
        self.assertEqual(current.state['generation'], 1)
        self.assertEqual(current.state['epoch'], 2)
        self.assertEqual(self.store.verify_payloads(), 2)
        for address, request in self.inventory.items():
            self.assertEqual(current.read(address), self.f.data[address])
        for address in self.targets:
            copied = self.store.payload_path(current, address, verify=True)
            original = fixture.resolver.resolve_output(self.f.output, self.f.address(address))
            self.assertEqual(copied.read_bytes(), original.read_bytes())
            self.assertNotEqual((copied.stat().st_dev, copied.stat().st_ino),
                                (original.stat().st_dev, original.stat().st_ino))
        return current

    def test_actual_join_preserves_controls_payloads_source_authorities_and_uint64(self):
        before = {name: path.read_bytes() for name, path in migration.recovery._files(self.f.root).items()}
        self.prepare()
        result = self.join()
        current = self.assert_ready()
        self.assertEqual(result['payloads'], 2)
        self.assertEqual(json.loads(current.read('checkpoints/clip_0001.json'))['seed'], 18446744073709551615)
        self.assertEqual(len([p for p in current.state['documents'] if p.startswith('__migration__/')]), 2)
        self.assertEqual(before, {name: path.read_bytes() for name, path in migration.recovery._files(self.f.root).items()})
        self.assertEqual(self.base.verify(), len(self.inventory))
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.control.snapshot()
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            fixture.resolver.storage_state(self.root)

    def test_join_uses_log_without_overwriting_immutable_bootstrap(self):
        marker = state._decode((self.root/'storage.json').read_bytes())
        marker['commit_protocol'] = 'immutable_slots_v1'
        state.atomic_json(self.root/'storage.json', marker)
        before = (self.root/'storage.json').read_bytes()
        with patch.object(migration, 'atomic_json', side_effect=AssertionError('overwrite')):
            self.prepare()
            self.join()
        self.assertEqual((self.root/'storage.json').read_bytes(), before)
        self.assert_ready()
        self.assertEqual(migration.progress.read(self.journal)['phase'], 'published')

    def test_exact_import_and_explicit_complete_destinations_required(self):
        for targets in ({}, {**self.targets, 'other': next(iter(self.targets.values()))}):
            with self.subTest(targets=list(targets)), self.assertRaisesRegex(ValueError, 'explicit|exactly'):
                self.prepare(targets=targets)
            self.assertFalse(self.folder.exists())
        self.control.commit(self.base, {'checkpoints/clip_0001.json': {'data': b'{"seed":1}',
            'scope': 'branch:A', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.prepare()

    def test_interrupted_copy_is_gated_then_resumes(self):
        self.prepare()
        with self.assertRaisesRegex(OSError, 'interrupted'):
            self.join(after_copy=self.fail_copy)
        for store in (self.control, self.store):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                store.snapshot()
        self.join()
        self.assert_ready()

    def test_post_publication_ack_failure_and_retry_keep_later_branch_save(self):
        self.prepare()
        real = state.atomic_json
        def publish_then_fail(path, value):
            real(path, value)
            if Path(path) == self.root/'storage.json' and value.get('phase') == 'ready':
                raise OSError('lost pointer acknowledgement')
        with patch.object(state, 'atomic_json', side_effect=publish_then_fail), self.assertRaisesRegex(OSError, 'acknowledgement'):
            self.join()
        current = self.assert_ready()
        self.store.commit(current, {'checkpoints/clip_0001.json': {'data': b'{"seed":2}',
            'scope': 'branch:A', 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        reference = self.store.snapshot().reference
        self.join()
        self.assertEqual(self.store.snapshot().reference, reference)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'), b'{"seed":2}')
        self.assertEqual(json.loads(self.journal.read_text())['phase'], 'published')

    def test_failed_final_pointer_can_resume_with_same_candidate(self):
        self.prepare()
        with patch.object(state, 'atomic_json', side_effect=OSError('pointer unavailable')), self.assertRaises(OSError):
            self.join()
        self.assertEqual(json.loads((self.root/'storage.json').read_text())['phase'], 'building')
        candidate = self.root/'project/roots'/f'{json.loads(self.journal.read_text())["operation_id"]}.json'
        original = candidate.read_bytes()
        self.join()
        self.assert_ready()
        self.assertEqual(candidate.read_bytes(), original)

    def test_metadata_write_interruption_retains_partials_only_in_journal_and_resumes(self):
        self.prepare()
        real = migration.os.fsync
        def fail(fd):
            # Payloads are 3/5 bytes, while control records are larger.
            info = migration.os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_size > 20:
                raise OSError('metadata flush failure')
            return real(fd)
        # Enter the gate/copy journal before injecting a metadata-specific fault.
        real_immutable = migration._join_immutable
        def interrupted(*args):
            with patch.object(migration.os, 'fsync', side_effect=fail):
                return real_immutable(*args)
        with patch.object(migration, '_join_immutable', side_effect=interrupted), self.assertRaisesRegex(OSError, 'flush'):
            self.join()
        self.assertTrue(list((self.folder/'partials').glob('*.part')))
        self.assertFalse(list(self.root.rglob('.tmp-*')))
        self.join()
        self.assert_ready()

    def test_untracked_metadata_shaped_file_blocks_publication(self):
        self.prepare()
        intruder = self.root/'project/payloads'/('f'*32+'.json')
        def create(index):
            if index == 1:
                intruder.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'Untracked'):
            self.join(after_copy=create)
        self.assertEqual(intruder.read_bytes(), b'{}')
        with self.assertRaisesRegex(ValueError, 'Untracked'):
            self.join()
        intruder.rename(self.f.lab/'preserved-intruder.json')
        self.join()
        self.assert_ready()

    def test_unowned_payload_appearing_after_preparation_is_not_adopted(self):
        self.prepare()
        target = self.root/self.f.new
        target.parent.mkdir(parents=True)
        target.write_bytes(b'VIDEO')
        with self.assertRaisesRegex(ValueError, 'Unowned'):
            self.join()
        self.assertEqual(self.control.snapshot().reference, self.base.reference)

    def test_copied_payload_corruption_blocks_resume_without_overwrite(self):
        self.prepare()
        with self.assertRaises(OSError):
            self.join(after_copy=self.fail_copy)
        copied = next(p for p in (self.root/entry['target'] for entry in self.targets.values()) if p.exists())
        copied.write_bytes(b'external bytes')
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.join()
        self.assertEqual(copied.read_bytes(), b'external bytes')
        copied.rename(self.f.lab/'preserved-collision')
        self.join()
        self.assert_ready()

    def test_source_changed_after_preparation_is_rejected_before_gate(self):
        self.prepare()
        source = self.f.root/'checkpoints/clip_0001.json'
        original = source.read_bytes()
        try:
            source.write_bytes(b'{"seed":999}')
            with self.assertRaisesRegex(ValueError, 'Source state changed'):
                self.join()
            self.assertEqual(self.control.snapshot().reference, self.base.reference)
        finally:
            source.write_bytes(original)

    def test_source_changed_during_final_payload_hash_is_rejected(self):
        self.prepare()
        source = self.f.root/'checkpoints/clip_0001.json'
        original, real = source.read_bytes(), project._verify_file
        def change(root, record):
            result = real(root, record)
            source.write_bytes(b'{"seed":3}')
            return result
        try:
            with patch.object(project, '_verify_file', side_effect=change), self.assertRaisesRegex(ValueError, 'Source changed during final'):
                self.join()
        finally:
            source.write_bytes(original)
        self.join()
        self.assert_ready()

    def test_disk_space_check_precedes_gate(self):
        self.prepare()
        usage = shutil.disk_usage(self.root)
        with patch.object(migration.shutil, 'disk_usage', return_value=usage._replace(free=0)), self.assertRaisesRegex(OSError, 'free space'):
            self.join()
        self.assertEqual(self.control.snapshot().reference, self.base.reference)

    def test_plan_tampering_never_changes_target(self):
        self.prepare()
        path = self.folder/'plan.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'immutable join plan'):
            self.join()
        self.assertEqual(self.control.snapshot().reference, self.base.reference)


if __name__ == '__main__':
    unittest.main(verbosity=2)
