"""Atomic control-only roots: exact bytes, scoped CAS, lost acknowledgements."""
import json
from pathlib import Path
import sys
import subprocess
import threading
import unittest
from unittest.mock import patch
import uuid

import _storage_resolver_unit_test as fixture
import storage_state as state


class StateTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RelocationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.a, self.b = 'branches/main.json', 'branches/'+'b'*32+'/branch.json'
        self.pointer = 'checkpoints/clip_0001.json'
        self.original = {
            self.a: b'{"prompt":"old A","seed":18446744073709551613}\r\n',
            self.b: b'{"prompt":"old B","seed":18446744073709551611}\n',
            self.pointer: b'{"revision":"old take"}\n',
            'checkpoints/clip_0001.'+'c'*32+'.json': b'{"saved":"immutable"}',
        }
        self.inventory = {}
        for address, raw in self.original.items():
            path = self.f.root/address
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.inventory[address] = {'source': address, 'sha256': state._hash(raw),
                'scope': 'branch:B' if address == self.b else 'branch:A',
                'category': 'branches', 'immutable': address not in (self.a, self.b, self.pointer)}
        self.output = self.f.lab/'control-output'
        self.store = state.create_control_rehearsal(self.f.receipt, self.output, self.inventory)
        self.root = self.store.project
        scope = state.control_rehearsal_access(self.root)
        scope.__enter__()
        self.addCleanup(scope.__exit__, None, None, None)

    def change(self, address, data=b'{"prompt":"new","seed":18446744073709551615}'):
        spec = self.inventory[address]
        return {address: {**{k: spec[k] for k in ('scope', 'category', 'immutable')}, 'data': data}}

    def commit(self, base, changes, **kwargs):
        return self.store.commit(base, changes, operation_id=kwargs.pop('operation_id', uuid.uuid4().hex), **kwargs)

    def marker(self):
        return (self.root/'storage.json').read_bytes()

    def test_import_preserves_all_bytes_and_source_without_live_activation(self):
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.verify(), 4)
        for address, raw in self.original.items():
            self.assertEqual(snapshot.read(address), raw)
            self.assertEqual((self.f.root/address).read_bytes(), raw)
        self.assertEqual(json.loads(snapshot.read(self.a))['seed'], 18446744073709551613)
        self.assertFalse((self.root/self.a).exists())
        with fixture.resolver.rehearsal_access(self.root), self.assertRaisesRegex(ValueError, 'Unsupported'):
            fixture.resolver.storage_state(self.root)
        with state.control_rehearsal_access(self.f.root), self.assertRaisesRegex(ValueError, 'copy-only'):
            self.store.snapshot()

    def test_multi_document_publication_has_no_mixed_generation(self):
        base = self.store.snapshot()
        changes = {**self.change(self.a), **self.change(self.pointer, b'{"revision":"new take"}')}
        observed = []
        def staged(_):
            view = self.store.snapshot()
            observed.append((view.read(self.a), view.read(self.pointer)))
        result = self.commit(base, changes, after_stage=staged)
        self.assertEqual(result['generation'], 1)
        self.assertTrue(observed)
        self.assertTrue(all(a == self.original[self.a] and b == self.original[self.pointer] for a, b in observed))
        view = self.store.snapshot()
        self.assertEqual(view.read(self.a), changes[self.a]['data'])
        self.assertEqual(view.read(self.pointer), changes[self.pointer]['data'])
        self.assertEqual(base.read(self.a), self.original[self.a])
        self.assertEqual(base.read(self.pointer), self.original[self.pointer])

    def test_snapshot_pin_cannot_be_changed_through_returned_dicts(self):
        base = self.store.snapshot()
        reference = base.reference
        extra = state.Snapshot(self.root, reference)
        reference['sha256'] = '0'*64
        base.reference['path'] = 'invalid'
        view = base.state
        view['documents'].clear()
        self.assertEqual(base.read(self.a), self.original[self.a])
        self.assertEqual(extra.read(self.a), self.original[self.a])
        with state.control_rehearsal_access(self.f.root), self.assertRaisesRegex(ValueError, 'copy-only'):
            base.read(self.a)

    def test_same_scope_stale_writer_is_rejected(self):
        base = self.store.snapshot()
        self.commit(base, self.change(self.a))
        before = self.marker()
        with self.assertRaisesRegex(state.StateConflict, 'scope changed'):
            self.commit(base, self.change(self.pointer))
        self.assertEqual(self.marker(), before)

    def test_independent_branch_edits_merge_without_changing_epoch(self):
        base = self.store.snapshot()
        self.commit(base, self.change(self.a))
        self.commit(base, self.change(self.b))
        current = self.store.snapshot()
        self.assertEqual(current.state['generation'], 2)
        self.assertEqual(current.state['epoch'], 1)
        for address in (self.a, self.b):
            self.assertEqual(current.read(address), self.change(address)[address]['data'])

    def test_declared_read_dependency_blocks_stale_source(self):
        base = self.store.snapshot()
        self.commit(base, self.change(self.a))
        with self.assertRaisesRegex(state.StateConflict, 'branch:A'):
            self.commit(base, self.change(self.b), read_scopes=['branch:A'])

    def test_maintenance_epoch_fences_queued_jobs_not_branch_settings(self):
        base = self.store.snapshot()
        op = uuid.uuid4().hex
        receipt = self.store.advance_epoch(base, operation_id=op)
        current = self.store.snapshot()
        self.assertEqual(current.state['epoch'], 2)
        self.assertEqual(current.state['scope_revisions'], base.state['scope_revisions'])
        self.assertEqual(current.read(self.a), base.read(self.a))
        self.assertEqual(self.store.advance_epoch(base, operation_id=op), receipt)
        with self.assertRaisesRegex(state.StateConflict, 'epoch changed'):
            self.commit(base, self.change(self.b))

    def test_immutable_work_and_scope_reassignment_are_rejected(self):
        base = self.store.snapshot()
        immutable = next(k for k in self.original if k not in (self.a, self.b, self.pointer))
        for changes in (self.change(immutable), {self.a: dict(self.change(self.a)[self.a], scope='branch:B')}):
            with self.assertRaisesRegex(state.StateConflict, 'immutable work'):
                self.commit(base, changes)
        self.assertEqual(self.store.snapshot().state['generation'], 0)

    def test_interrupted_staging_and_retry_leave_old_snapshot_readable(self):
        base = self.store.snapshot()
        op = uuid.uuid4().hex
        changes = {**self.change(self.a), **self.change(self.pointer)}
        before = self.marker()
        def fail(_):
            raise OSError('simulated disconnect')
        with self.assertRaisesRegex(OSError, 'disconnect'):
            self.commit(base, changes, operation_id=op, after_stage=fail)
        self.assertEqual(self.marker(), before)
        self.assertEqual(self.store.snapshot().read(self.a), self.original[self.a])
        self.assertTrue((self.root/'project/jobs'/f'{op}.json').exists())
        self.commit(base, changes, operation_id=op)
        self.assertEqual(self.store.snapshot().state['generation'], 1)

    def test_failed_pointer_publication_retains_prior_root_and_staging(self):
        base = self.store.snapshot()
        before = self.marker()
        op = uuid.uuid4().hex
        with patch.object(state, 'atomic_json', side_effect=OSError('pointer offline')):
            with self.assertRaisesRegex(OSError, 'offline'):
                self.commit(base, self.change(self.a), operation_id=op)
        self.assertEqual(self.marker(), before)
        self.assertGreater(len(list((self.root/'project/roots').glob('*.json'))), 1)
        self.commit(base, self.change(self.a), operation_id=op)

    def test_lost_ack_is_idempotent_even_after_another_branch_save(self):
        base = self.store.snapshot()
        op = uuid.uuid4().hex
        original = state.atomic_json
        def publish_then_fail(path, value):
            original(path, value)
            raise OSError('lost acknowledgement')
        with patch.object(state, 'atomic_json', publish_then_fail), self.assertRaisesRegex(OSError, 'acknowledgement'):
            self.commit(base, self.change(self.a), operation_id=op)
        receipt = self.store.snapshot().state['operations'][op]
        self.commit(base, self.change(self.b))
        before = self.marker()
        self.assertEqual(self.commit(base, self.change(self.a), operation_id=op), receipt)
        self.assertEqual(self.marker(), before)
        self.assertEqual(self.store.snapshot().state['generation'], 2)
        with self.assertRaisesRegex(state.StateConflict, 'reused'):
            self.commit(base, self.change(self.a, b'other'), operation_id=op)

    def test_corrupt_blob_never_falls_back_to_retained_legacy_file(self):
        view = self.store.snapshot()
        ref = view.state['documents'][self.a]['file']
        (self.root/ref['path']).write_bytes(b'corrupt')
        (self.root/self.a).parent.mkdir(parents=True, exist_ok=True)
        (self.root/self.a).write_bytes(self.original[self.a])
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            view.read(self.a)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.commit(view, self.change(self.b))

    def test_missing_or_changed_root_never_falls_back(self):
        view = self.store.snapshot()
        path = self.root/view.reference['path']
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.store.snapshot()

    def test_pointer_changed_outside_lock_is_not_overwritten(self):
        base = self.store.snapshot()
        def change(stage):
            if stage == 'root':
                (self.root/'storage.json').write_bytes(b'{"other":"owner"}')
        with self.assertRaisesRegex(state.StateConflict, 'outside the writer lock'):
            self.commit(base, self.change(self.a), after_stage=change)
        self.assertEqual(self.marker(), b'{"other":"owner"}')

    def test_corruption_of_staged_document_blocks_root_publication(self):
        base = self.store.snapshot()
        before = self.marker()
        def corrupt(stage):
            if stage == 'root':
                candidates = [p for p in (self.root/'project/roots').glob('*.json')
                              if p != self.root/base.reference['path']]
                candidate = json.loads(candidates[0].read_text())
                ref = candidate['documents'][self.a]['file']
                (self.root/ref['path']).write_bytes(b'bad staged bytes')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.commit(base, self.change(self.a), after_stage=corrupt)
        self.assertEqual(self.marker(), before)

    def test_change_after_earlier_hash_is_detected_during_full_verification(self):
        view = self.store.snapshot()
        documents = list(view.state['documents'].values())
        first, last = documents[0]['file'], documents[-1]['file']
        original = state._reference
        def read_then_change(project, ref, pattern):
            raw = original(project, ref, pattern)
            if ref == last:
                (project/first['path']).write_bytes(b'changed after hashing')
            return raw
        with patch.object(state, '_reference', read_then_change), self.assertRaisesRegex(ValueError, 'changed during snapshot'):
            view.verify()

    def test_unpublished_root_cannot_be_used_as_a_transaction_base(self):
        base = self.store.snapshot()
        with patch.object(state, 'atomic_json', side_effect=OSError('offline')), self.assertRaises(OSError):
            self.commit(base, self.change(self.a))
        path = next(p for p in (self.root/'project/roots').glob('*.json')
                    if p != self.root/base.reference['path'])
        raw = path.read_bytes()
        candidate = state.Snapshot(self.root, {'path': path.relative_to(self.root).as_posix(),
                                               'sha256': state._hash(raw), 'size': len(raw)})
        with self.assertRaisesRegex(state.StateConflict, 'committed ancestor'):
            self.commit(candidate, self.change(self.b))

    def test_two_threads_keep_independent_branch_updates(self):
        base = self.store.snapshot()
        errors = []
        def worker(address):
            try:
                with state.control_rehearsal_access(self.root):
                    self.commit(base, self.change(address))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(a,)) for a in (self.a, self.b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.store.snapshot().state['generation'], 2)

    def test_separate_process_rechecks_after_waiting_for_project_lock(self):
        script = '''import sys, uuid
from pathlib import Path
from storage_state import ControlStore, control_rehearsal_access
root=Path(sys.argv[1]); address=sys.argv[2]
with control_rehearsal_access(root):
    store=ControlStore(root); base=store.snapshot()
    print('ready',flush=True)
    store.commit(base,{address:{'data':b'new B','scope':'branch:B','category':'branches','immutable':False}},operation_id=uuid.uuid4().hex)
    print('committed',flush=True)
'''
        with state._lock(self.root):
            child = subprocess.Popen([sys.executable, '-c', script, str(self.root), self.b],
                cwd=str(Path(state.__file__).parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: child.kill() if child.poll() is None else None)
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assertIsNone(child.poll())
            self.commit(self.store.snapshot(), self.change(self.a))
        out, err = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, err)
        self.assertEqual(out.strip(), 'committed')
        self.assertEqual(self.store.snapshot().state['generation'], 2)
        self.assertEqual(self.store.snapshot().read(self.b), b'new B')

    def test_failed_import_is_gated_and_keeps_source(self):
        output = self.f.lab/'failed-import'
        original = state.atomic_json
        def fail(path, value):
            if value.get('phase') == 'ready':
                raise OSError('lost connection')
            return original(path, value)
        with patch.object(state, 'atomic_json', fail), self.assertRaisesRegex(OSError, 'connection'):
            state.create_control_rehearsal(self.f.receipt, output, self.inventory)
        root = output/'h3_chains/demo'
        self.assertEqual(json.loads((root/'storage.json').read_text())['phase'], 'building')
        with state.control_rehearsal_access(root), self.assertRaisesRegex(ValueError, 'incomplete'):
            state.ControlStore(root).snapshot()
        for address, raw in self.original.items():
            self.assertEqual((self.f.root/address).read_bytes(), raw)

    def test_windows_budget_and_invalid_reference_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            state.create_control_rehearsal(self.f.receipt, self.f.lab/'short', self.inventory, path_budget=20)
        marker = json.loads(self.marker())
        marker['epoch'] = True
        (self.root/'storage.json').write_bytes(state._encode(marker))
        with self.assertRaisesRegex(ValueError, 'pointer disagree'):
            self.store.snapshot()

    def test_case_collision_and_traversal_never_publish(self):
        base = self.store.snapshot()
        for address in (self.a.upper(), '../outside.json', 'storage.json', 'thing.lock'):
            with self.assertRaises(ValueError):
                self.commit(base, {address: self.change(self.a)[self.a]})
            self.assertEqual(self.store.snapshot().state['generation'], 0)

    def test_import_rejects_occupied_target_and_changed_source(self):
        with self.assertRaisesRegex(ValueError, 'new empty destination'):
            state.create_control_rehearsal(self.f.receipt, self.output, self.inventory)
        target = self.f.lab/'another-output'
        (self.f.root/self.a).write_bytes(b'new user settings')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            state.create_control_rehearsal(self.f.receipt, target, self.inventory)
        self.assertFalse(target.exists())
        self.assertEqual((self.f.root/self.a).read_bytes(), b'new user settings')

    def test_linked_control_file_cannot_escape(self):
        base = self.store.snapshot()
        ref = base.state['documents'][self.a]['file']
        path = self.root/ref['path']
        outside = self.f.lab/'outside.json'
        outside.write_bytes(self.original[self.a])
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Host does not allow symlinks')
        with self.assertRaisesRegex(ValueError, 'symlinks or junctions'):
            base.read(self.a)
        self.assertEqual(outside.read_bytes(), self.original[self.a])


if __name__ == '__main__':
    unittest.main()
