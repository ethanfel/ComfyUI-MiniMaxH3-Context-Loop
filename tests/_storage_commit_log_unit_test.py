"""No-overwrite authority, real crash/retry, corruption and concurrent readers."""
import errno
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import uuid

import _storage_state_unit_test as fixture
import storage_state as state
import storage_commit_log as log
import storage_project as project
import _storage_project_unit_test as media_fixture


class CommitLogTests(unittest.TestCase):
    change = fixture.StateTests.change
    commit = fixture.StateTests.commit
    marker = fixture.StateTests.marker

    def setUp(self):
        create = state.create_control_rehearsal
        def immutable(*args, **kwargs):
            return create(*args, **kwargs, commit_protocol=log.PROTOCOL)
        with patch.object(state, 'create_control_rehearsal', immutable):
            fixture.StateTests.setUp(self)

    def test_import_and_updates_never_replace_or_link_an_existing_pointer(self):
        before = self.marker()
        with patch.object(state, 'atomic_json', side_effect=AssertionError('overwrite called')):
            with patch.object(state, 'require_atomic_control_files', side_effect=AssertionError('old protocol')):
                for _ in range(4):
                    self.commit(self.store.snapshot(), self.change(self.a))
        self.assertEqual(self.marker(), before)
        self.assertEqual(self.store.snapshot().state['generation'], 4)
        self.assertEqual(len(list((self.root/log.DIRECTORY).glob('*.json'))), 10)

    def test_atomic_visibility_and_historical_pin(self):
        base = self.store.snapshot()
        changes = {**self.change(self.a), **self.change(self.pointer)}
        seen = []
        def observer(stage):
            view = self.store.snapshot()
            seen.append((stage, view.read(self.a), view.read(self.pointer)))
        self.commit(base, changes, after_stage=observer)
        for stage, a, pointer in seen:
            expected = changes if stage in ('commit', 'acknowledged') else None
            self.assertEqual(a, expected[self.a]['data'] if expected else self.original[self.a])
            self.assertEqual(pointer, expected[self.pointer]['data'] if expected else self.original[self.pointer])
        self.assertEqual(base.read(self.a), self.original[self.a])

    def test_prepublication_failure_retains_old_head_and_retry(self):
        base, op = self.store.snapshot(), uuid.uuid4().hex
        publish = state.publish_new_file
        def denied(staging, destination):
            if str(destination).endswith('/000000000002.json'):
                raise PermissionError(errno.EACCES, 'simulated disconnected publication')
            publish(staging, destination)
        with patch.object(state, 'publish_new_file', denied), self.assertRaises(PermissionError):
            self.commit(base, self.change(self.a), operation_id=op)
        self.assertEqual(self.store.snapshot().reference, base.reference)
        self.assertTrue(list((self.root/log.DIRECTORY).glob('.tmp-*')))
        self.commit(base, self.change(self.a), operation_id=op)
        self.assertEqual(self.store.snapshot().state['generation'], 1)

    def test_lost_ack_retries_exact_operation_even_after_another_branch_commit(self):
        base, op = self.store.snapshot(), uuid.uuid4().hex
        def fail(stage):
            if stage == 'commit':
                raise OSError('lost acknowledgement')
        with self.assertRaisesRegex(OSError, 'acknowledgement'):
            self.commit(base, self.change(self.a), operation_id=op, after_stage=fail)
        snapshot = self.store.snapshot()
        receipt = snapshot.state['operations'][op]
        head = log.CommitLog(self.root, self.marker()).read()
        self.assertFalse(head.acknowledged)
        # Reading is non-mutating, even when a writer lost its acknowledgement.
        self.assertFalse((self.root/log.CommitLog._address(2, ack=True)).exists())
        self.commit(base, self.change(self.b))
        self.assertTrue((self.root/log.CommitLog._address(2, ack=True)).exists())
        self.assertEqual(self.commit(base, self.change(self.a), operation_id=op), receipt)
        self.assertEqual(self.store.snapshot().state['generation'], 2)
        with self.assertRaisesRegex(state.StateConflict, 'reused'):
            self.commit(base, self.change(self.a, b'other request'), operation_id=op)

    def test_failed_ack_flush_is_not_reported_as_success_on_retry(self):
        base, op = self.store.snapshot(), uuid.uuid4().hex
        with patch.object(log, 'sync_file', side_effect=OSError('flush offline')):
            for _ in range(2):
                with self.assertRaisesRegex(OSError, 'flush offline'):
                    self.commit(base, self.change(self.a), operation_id=op)
        self.assertEqual(self.store.snapshot().state['generation'], 1)
        self.commit(base, self.change(self.a), operation_id=op)
        self.assertEqual(self.store.snapshot().state['generation'], 1)

    def test_real_process_crash_at_each_boundary(self):
        script = '''import os, sys
from pathlib import Path
import storage_state as s
root=Path(sys.argv[1]); address=sys.argv[2]; stage=sys.argv[3]; op=sys.argv[4]
with s.control_rehearsal_access(root):
    store=s.ControlStore(root); base=store.snapshot()
    def stop(point):
        if point == stage: os._exit(19)
    store.commit(base,{address:{'data':b'crash-test','scope':'branch:A','category':'branches','immutable':False}},operation_id=op,after_stage=stop)
'''
        for stage in ('document', 'root', 'commit', 'acknowledged'):
            with self.subTest(stage=stage):
                base, op = self.store.snapshot(), uuid.uuid4().hex
                result = subprocess.run([sys.executable, '-B', '-S', '-c', script,
                    str(self.root), self.a, stage, op], cwd=Path(state.__file__).parent,
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 19, result.stderr)
                after = self.store.snapshot()
                self.assertEqual(after.state['generation'], base.state['generation']+
                                 (1 if stage in ('commit', 'acknowledged') else 0))
                self.commit(base, self.change(self.a, b'crash-test'), operation_id=op)
                self.assertEqual(self.store.snapshot().state['generation'], base.state['generation']+1)

    def test_missing_latest_acknowledged_commit_cannot_revert_to_old_root(self):
        self.commit(self.store.snapshot(), self.change(self.a))
        (self.root/log.CommitLog._address(2)).unlink()  # injected loss on private fixture
        with self.assertRaisesRegex(ValueError, 'Missing immutable commit'):
            self.store.snapshot()

    def test_corrupt_commit_witness_and_unknown_namespace_fail_closed(self):
        self.commit(self.store.snapshot(), self.change(self.a))
        path = self.root/log.CommitLog._address(2)
        raw = path.read_bytes()
        path.write_bytes(raw+b' ')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.store.snapshot()
        path.write_bytes(raw)
        unknown = self.root/log.DIRECTORY/'unexpected.json'
        unknown.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Unknown immutable commit'):
            self.store.snapshot()

    def test_corrupt_unacknowledged_commit_cannot_satisfy_retry(self):
        base, op = self.store.snapshot(), uuid.uuid4().hex
        with patch.object(log, 'sync_file', side_effect=OSError('offline')), self.assertRaises(OSError):
            self.commit(base, self.change(self.a), operation_id=op)
        current = self.store.snapshot()
        (self.root/current.state['documents'][self.a]['file']['path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.commit(base, self.change(self.a), operation_id=op)
        self.assertFalse((self.root/log.CommitLog._address(2, ack=True)).exists())

    def test_sequence_gap_and_forged_previous_head_fail_closed(self):
        self.commit(self.store.snapshot(), self.change(self.a))
        self.commit(self.store.snapshot(), self.change(self.b))
        path = self.root/log.CommitLog._address(3)
        record = json.loads(path.read_bytes())
        record['previous'] = None
        path.write_bytes(state._encode(record))
        with self.assertRaisesRegex(ValueError, 'Invalid immutable commit chain'):
            self.store.snapshot()

    def test_unknown_protocol_never_falls_back_to_replace(self):
        value = json.loads(self.marker())
        value['commit_protocol'] = 'future-version'
        (self.root/'storage.json').write_bytes(state._encode(value))
        with self.assertRaisesRegex(ValueError, 'Unsupported immutable commit protocol'):
            self.store.snapshot()

    def test_failed_initial_import_retains_an_immutable_gate(self):
        output = self.f.lab/'failed-log-import'
        publish = state._immutable
        def fail(project, address, raw, budget):
            if address.startswith('project/roots/'):
                raise OSError('import interrupted')
            return publish(project, address, raw, budget)
        with patch.object(state, '_immutable', fail), self.assertRaisesRegex(OSError, 'interrupted'):
            state.create_control_rehearsal(self.f.receipt, output, self.inventory, commit_protocol=log.PROTOCOL)
        project = output/'h3_chains'/self.root.name
        self.assertEqual(json.loads((project/'storage.json').read_bytes())['phase'], 'building')
        with state.control_rehearsal_access(project), self.assertRaisesRegex(ValueError, 'incomplete'):
            state.ControlStore(project).snapshot()

    def test_initial_import_lost_ack_is_reconciled_without_overwriting_bootstrap(self):
        output = self.f.lab/'lost-import-ack'
        with patch.object(log, 'sync_file', side_effect=OSError('ack offline')), self.assertRaisesRegex(OSError, 'offline'):
            state.create_control_rehearsal(self.f.receipt, output, self.inventory, commit_protocol=log.PROTOCOL)
        project = output/'h3_chains'/self.root.name
        raw = (project/'storage.json').read_bytes()
        with state.control_rehearsal_access(project):
            store = state.ControlStore(project)
            self.assertEqual(store.snapshot().state['generation'], 0)
            store.advance_epoch(store.snapshot(), operation_id=uuid.uuid4().hex)
            self.assertEqual(store.snapshot().state['generation'], 1)
        self.assertEqual((project/'storage.json').read_bytes(), raw)

    def test_concurrent_reader_never_sees_missing_pointer_or_mixed_controls(self):
        done, started = threading.Event(), threading.Event()
        seen, errors = [], []
        def reader():
            try:
                with state.control_rehearsal_access(self.root):
                    started.set()
                    while not done.is_set():
                        view = self.store.snapshot()
                        if view.state['generation']:
                            self.assertEqual(view.read(self.a), view.read(self.pointer))
                        seen.append(view.state['generation'])
            except BaseException as error:
                errors.append(str(error))
        thread = threading.Thread(target=reader)
        thread.start()
        self.assertTrue(started.wait(5))
        try:
            for index in range(20):
                raw = str(index).encode()
                self.commit(self.store.snapshot(), {**self.change(self.a, raw), **self.change(self.pointer, raw)})
        finally:
            done.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertTrue(seen)
        self.assertEqual(self.store.snapshot().state['generation'], 20)

    def test_competing_processes_merge_independent_scopes_and_reject_shared_scope(self):
        script = '''import json, sys
from pathlib import Path
import storage_state as s
root=Path(sys.argv[1]); address=sys.argv[2]; scope=sys.argv[3]; op=sys.argv[4]
with s.control_rehearsal_access(root):
    store=s.ControlStore(root); base=s.Snapshot(root,json.loads(sys.argv[5]))
    print('ready',flush=True)
    sys.stdin.readline()
    try:
        store.commit(base,{address:{'data':op.encode(),'scope':scope,'category':'branches','immutable':False}},operation_id=op)
    except s.StateConflict:
        sys.exit(23)
'''
        for shared in (False, True):
            base = self.store.snapshot()
            children = []
            with state._lock(self.root):
                for address, scope in ((self.a, 'branch:A'),
                        (self.a if shared else self.b, 'branch:A' if shared else 'branch:B')):
                    child = subprocess.Popen([sys.executable, '-B', '-S', '-c', script,
                        str(self.root), address, scope, uuid.uuid4().hex, json.dumps(base.reference)],
                        cwd=Path(state.__file__).parent, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    children.append(child)
                    self.addCleanup(lambda p=child: p.kill() if p.poll() is None else None)
                for child in children:
                    self.assertEqual(child.stdout.readline().strip(), 'ready')
                    child.stdin.write('start\n')
                    child.stdin.flush()
                    self.assertIsNone(child.poll())
            for child in children:
                _, err = child.communicate(timeout=15)
                self.assertIn(child.returncode, (0, 23), err)
            self.assertEqual(sorted(p.returncode for p in children), [0, 23] if shared else [0, 0])
            self.assertEqual(self.store.snapshot().state['generation'], base.state['generation']+(1 if shared else 2))


# These engine invariants must also hold with a different publication backend.
for name in (
        'test_import_preserves_all_bytes_and_source_without_live_activation',
        'test_snapshot_pin_cannot_be_changed_through_returned_dicts',
        'test_same_scope_stale_writer_is_rejected',
        'test_independent_branch_edits_merge_without_changing_epoch',
        'test_declared_read_dependency_blocks_stale_source',
        'test_maintenance_epoch_fences_queued_jobs_not_branch_settings',
        'test_immutable_work_and_scope_reassignment_are_rejected',
        'test_corrupt_blob_never_falls_back_to_retained_legacy_file',
        'test_missing_or_changed_root_never_falls_back',
        'test_pointer_changed_outside_lock_is_not_overwritten',
        'test_corruption_of_staged_document_blocks_root_publication',
        'test_change_after_earlier_hash_is_detected_during_full_verification',
        'test_two_threads_keep_independent_branch_updates',
        'test_case_collision_and_traversal_never_publish',
        'test_linked_control_file_cannot_escape'):
    setattr(CommitLogTests, name, getattr(fixture.StateTests, name))


class ProjectLogTests(unittest.TestCase):
    stage = media_fixture.ProjectTests.stage
    controls = media_fixture.ProjectTests.controls
    commit = media_fixture.ProjectTests.commit

    def setUp(self):
        media_fixture.ProjectTests.setUp(self)
        # Private empty-payload fixture only, before its first transaction.
        marker = state._decode((self.store.project/'storage.json').read_bytes())
        marker['commit_protocol'] = log.PROTOCOL
        state.atomic_json(self.store.project/'storage.json', marker)

    def test_corrupt_media_after_lost_ack_cannot_be_acknowledged_or_extended(self):
        base, staged, op = self.store.snapshot(), self.stage(), uuid.uuid4().hex
        with patch.object(log, 'sync_file', side_effect=OSError('offline')), self.assertRaises(OSError):
            self.commit(base, [staged], operation_id=op)
        (self.store.project/self.target).write_bytes(b'corrupt media')
        for action in (
                lambda: self.commit(base, [staged], operation_id=op),
                lambda: self.store.commit(self.store.snapshot(), self.controls(), operation_id=uuid.uuid4().hex)):
            with self.assertRaisesRegex(ValueError, 'checksum/size'):
                action()
        self.assertFalse((self.store.project/log.CommitLog._address(1, ack=True)).exists())

    def test_payload_and_controls_share_commit_visibility_point(self):
        base, staged = self.store.snapshot(), self.stage()
        seen = []
        def observe(stage):
            snapshot = self.store.snapshot()
            seen.append((stage, bool(project.payload_catalog(snapshot)), snapshot.reference != base.reference))
        with patch.object(state, 'atomic_json', side_effect=AssertionError('overwrite')):
            self.commit(base, [staged], after_stage=observe)
        for stage, payload, changed in seen:
            self.assertEqual(payload, changed)
            self.assertEqual(changed, stage in ('commit', 'acknowledged'))
        self.assertEqual(self.store.verify_payloads(), 1)

    def test_hash_binds_opened_file_not_stale_preopen_cifs_attributes(self):
        opened = []
        original_open, signature = Path.open, state.resolver._signature
        def track(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            if path == self.source:
                opened.append(True)
            return handle
        def cached(path):
            actual = signature(path)
            return actual if opened else actual[:3]+(actual[3]-60_000_000_000, actual[4]-60_000_000_000)
        with patch.object(Path, 'open', track), patch.object(state.resolver, '_signature', cached):
            digest, _ = project._hash_file(self.source)
        self.assertEqual(digest, state._hash(self.source.read_bytes()))

    def test_hash_still_rejects_changes_to_open_file_or_replaced_path(self):
        original = project.hashlib.file_digest
        def changed(handle, algorithm):
            result = original(handle, algorithm)
            self.source.write_bytes(b'changed during hashing')
            return result
        with patch.object(project.hashlib, 'file_digest', changed), self.assertRaisesRegex(ValueError, 'changed while hashing'):
            project._hash_file(self.source)
        def replaced(handle, algorithm):
            result = original(handle, algorithm)
            path = self.source.with_name('replacement.bin')
            path.write_bytes(self.source.read_bytes())
            path.replace(self.source)
            return result
        with patch.object(project.hashlib, 'file_digest', replaced), self.assertRaisesRegex(ValueError, 'changed while hashing'):
            project._hash_file(self.source)


for name in (
        'test_staging_is_independent_durable_but_not_accepted',
        'test_existing_flat_reference_cache_payload_layout_is_supported',
        'test_interrupted_acceptance_retains_staging_and_can_retry'):
    setattr(ProjectLogTests, name, getattr(media_fixture.ProjectTests, name))


if __name__ == '__main__':
    unittest.main()
