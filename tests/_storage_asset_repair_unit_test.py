"""Explicit input repair never changes the accepted catalog or normal reads."""
import copy
import asyncio
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_asset_edits_unit_test as fixture
from storage_runtime import runtime_access
from storage_project_assets import CATALOG
from storage_project import payload_catalog, payload_key
from project_assets import ProjectAssetConflictError
from _storage_handoff_routes_unit_test import routes
from _storage_branch_routes_unit_test import request
import storage_state as state


class AssetRepairTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssetEditTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.catalog, self.input = self.f.store, self.f.catalog, self.f.input
        self.assets = self.f.f.assets
        self.media = self.catalog.parent/self.f.f.first['relative_path']
        self.original_media = self.media.read_bytes()
        self.raw = self.catalog.read_bytes()

    def inspect(self, pin=None):
        with runtime_access(self.store, pin=pin):
            return self.assets().inspect_input_repair('demo')

    def repair(self, inspection, operation=None, fault=None, **kwargs):
        with runtime_access(self.store, pin=inspection['input_pin'], asset_writes=True,
                            asset_input_root=self.input, **kwargs) as runtime:
            runtime.assets.after_stage = fault
            result = self.assets().repair_inputs('demo', inspection,
                storage_operation_id=operation or uuid.uuid4().hex,
                ownership_proof=self.f.f.proof)
            return result, runtime.output_pin

    def damaged(self):
        self.catalog.write_bytes(b'broken catalog \xff')
        self.media.write_bytes(b'damaged picture')

    def assert_restored(self):
        self.assertEqual(self.catalog.read_bytes(), self.raw)
        self.assertEqual(self.media.read_bytes(), self.original_media)
        self.assertEqual(self.store.snapshot().read(CATALOG), self.raw)
        self.assertFalse(self.f.pending.exists())

    def test_readonly_inspection_and_browsing_do_not_repair_or_create_files(self):
        self.damaged()
        before = self.f.f.input_files()
        root = self.store.snapshot().reference
        plan = self.inspect()
        self.assertTrue(plan['repairable'])
        self.assertEqual(sum(row['action'] == 'preserve_restore' for row in plan['files']), 2)
        with runtime_access(self.store):
            self.assets().public_catalog('demo')
        self.assertEqual(self.f.f.input_files(), before)
        self.assertEqual(self.store.snapshot().reference, root)

    def test_restores_missing_catalog_and_media_keeps_unknown_files_and_controls(self):
        self.catalog.unlink()  # Disposable fixture input only; accepted backup stays intact.
        self.media.unlink()
        extra = self.catalog.parent/'user-notes.txt'
        extra.write_text('keep this')
        before = self.store.snapshot()
        result, _ = self.repair(self.inspect())
        self.assertEqual(set(result['repaired_files']), {'catalog.json', self.f.f.first['relative_path']})
        self.assertEqual(result['preserved_files'], [])
        self.assertEqual(extra.read_text(), 'keep this')
        self.assert_restored()
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(self.store.snapshot().state['documents'][address], descriptor)
        # The repair actually unlocks normal edits, not just backup browsing.
        result, _ = self.f.call('create_folder', 'After repair')
        self.assertEqual(result['folder']['name'], 'After repair')

    def test_damaged_bytes_retained_independently_in_accepted_recovery_payloads(self):
        self.damaged()
        op = uuid.uuid4().hex
        result, _ = self.repair(self.inspect(), op)
        self.assertEqual(len(result['preserved_files']), 2)
        self.assert_restored()
        for relative, raw in [('catalog.json', b'broken catalog \xff'),
                              (self.f.f.first['relative_path'], b'damaged picture')]:
            address = 'project_assets/.repairs/'+op+'/'+relative
            path = self.store.payload_path(self.store.snapshot(), address, verify=True)
            self.assertEqual(path.read_bytes(), raw)
            self.assertIn('/project/assets/recovery/', str(path))
            self.assertNotEqual(path.stat().st_ino, (self.catalog.parent/relative).stat().st_ino)

    def test_faults_resume_exactly_without_changing_catalog_or_duplicate_recovery(self):
        for stage in ('input_repair_requested', 'input_repair_prepared', 'input_repair_pending',
                      'input_repair_file', 'document', 'root', 'input_repair_committed'):
            with self.subTest(stage=stage):
                self.damaged()
                plan, op = self.inspect(), uuid.uuid4().hex
                def fault(point):
                    if point == stage:
                        raise RuntimeError('injected '+stage)
                with self.assertRaisesRegex(RuntimeError, 'injected'):
                    self.repair(plan, op, fault)
                result, pin = self.repair(plan, op)
                same, again = self.repair(plan, op)
                self.assertEqual((same, again), (result, pin))
                self.assert_restored()

    def test_pending_repair_fences_other_edits_refresh_and_legacy_reads(self):
        self.damaged()
        plan, op = self.inspect(), uuid.uuid4().hex
        def fault(stage):
            if stage == 'input_repair_pending':
                raise RuntimeError('interrupted')
        with self.assertRaises(RuntimeError):
            self.repair(plan, op, fault)
        for call in (lambda: self.f.call('create_folder', 'Blocked'), self.f.f.refresh):
            with self.assertRaisesRegex(state.StateConflict, 'pending'):
                call()
        with self.assertRaisesRegex(ProjectAssetConflictError, 'pending'):
            self.f.f.legacy.load('demo')
        with self.assertRaisesRegex(state.StateConflict, 'pending'):
            self.repair(plan)
        self.repair(plan, op)
        self.assert_restored()

    def test_different_valid_or_unknown_catalog_is_never_overwritten(self):
        for changed in (dict(state._decode(self.raw), assets=[]),
                        dict(state._decode(self.raw), project='another'),
                        dict(format='future_format', version=99), []):
            self.catalog.write_bytes(state._encode(changed) if isinstance(changed, dict) else b'[]')
            raw = self.catalog.read_bytes()
            plan = self.inspect()
            self.assertFalse(plan['repairable'])
            root = self.store.snapshot().reference
            with self.assertRaisesRegex(state.StateConflict, 'readable catalog'):
                self.repair(plan)
            self.assertEqual(self.catalog.read_bytes(), raw)
            self.assertEqual(self.store.snapshot().reference, root)
            self.assertFalse(self.f.pending.exists())

    def test_changed_input_or_project_or_request_fails_without_overwrite(self):
        self.damaged()
        plan = self.inspect()
        self.media.write_bytes(b'new user bytes')
        with self.assertRaisesRegex(state.StateConflict, 'changed since inspection'):
            self.repair(plan)
        self.assertEqual(self.media.read_bytes(), b'new user bytes')
        self.damaged()
        plan, op = self.inspect(), uuid.uuid4().hex
        def fault(stage):
            if stage == 'input_repair_prepared':
                raise RuntimeError('stop')
        with self.assertRaises(RuntimeError):
            self.repair(plan, op, fault)
        altered = copy.deepcopy(plan)
        altered['files'][0]['path'] = '../escape'
        with self.assertRaisesRegex(state.StateConflict, 'identity'):
            self.repair(altered, op)
        self.repair(plan, op)
        self.f.call('create_folder', 'New authoring')
        raw = self.catalog.read_bytes()
        with self.assertRaisesRegex(state.StateConflict, 'changed after repair'):
            self.repair(plan, op)
        self.assertEqual(self.catalog.read_bytes(), raw)
        self.assertFalse(self.f.pending.exists())

    def test_lost_commit_reply_has_one_root_and_one_preserved_copy(self):
        self.damaged()
        plan, op = self.inspect(), uuid.uuid4().hex
        original = self.store._publish
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('lost reply')
        with patch.object(self.store, '_publish', side_effect=lost), self.assertRaisesRegex(RuntimeError, 'lost reply'):
            self.repair(plan, op)
        root = self.store.snapshot().reference
        self.repair(plan, op)
        self.assertEqual(self.store.snapshot().reference, root)
        self.assert_restored()

    def test_missing_or_damaged_backup_cannot_replace_input(self):
        self.damaged()
        snapshot = self.store.snapshot()
        source = self.store.payload_path(snapshot, 'project_assets/'+self.f.f.first['relative_path'], verify=True)
        before = self.f.f.input_files()
        source.write_bytes(b'broken accepted backup')  # Independent test payload, never the user's chain.
        with self.assertRaises((ValueError, OSError)):
            self.inspect()
        self.assertEqual(self.f.f.input_files(), before)

    def test_owner_write_grant_input_grant_and_symlink_fences(self):
        self.damaged()
        plan = self.inspect()
        for enabled, root, proof in ((False, None, self.f.f.proof),
                                     (True, None, self.f.f.proof),
                                     (True, self.input, None)):
            with runtime_access(self.store, asset_writes=enabled, asset_input_root=root):
                with self.assertRaises(ValueError):
                    self.assets().repair_inputs('demo', plan, storage_operation_id=uuid.uuid4().hex,
                                                ownership_proof=proof)
        self.media.unlink()  # Disposable damaged fixture only.
        self.media.symlink_to(self.f.f.image)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.inspect()
        self.assertEqual(self.f.f.image.read_bytes(), self.original_media)

    def test_actual_http_inspect_repair_retry_and_foreign_owner(self):
        namespace = routes(self.f.f.output)
        namespace['_project_asset_store'] = self.assets
        namespace['ProjectAssetConflictError'] = ProjectAssetConflictError
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        names = {'_project_asset_input_repair', '_project_asset_repair_inputs'}
        nodes = [n for n in ast.parse(source.read_text()).body
                 if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
        self.damaged()
        before = self.f.f.input_files()
        with runtime_access(self.store):
            response = asyncio.run(namespace['_project_asset_input_repair'](request({'project':'demo'})))
        self.assertEqual(response['status'], 200, response)
        self.assertEqual(self.f.f.input_files(), before)
        plan = response['body']['inspection']
        body = dict(project='demo', inspection=plan, storage_operation_id=uuid.uuid4().hex)
        for proof, wanted in ((None, 423), (self.f.f.proof, 200), (self.f.f.proof, 200)):
            with runtime_access(self.store, pin=plan['input_pin'], asset_writes=True, asset_input_root=self.input):
                response = asyncio.run(namespace['_project_asset_repair_inputs'](request(body, proof)))
            self.assertEqual(response['status'], wanted, response)
        self.assert_restored()

    def test_missing_whole_project_input_directory_inspects_without_creating_it(self):
        # Recoverable rename of this disposable input subtree; no media deletion.
        directory = self.catalog.parent
        moved = directory.with_name('fixture-input-before-repair')
        directory.rename(moved)
        plan = self.inspect()
        self.assertFalse(directory.exists())
        self.assertTrue(all(item['action'] == 'restore' for item in plan['files']))
        self.repair(plan)
        self.assert_restored()
        self.assertEqual((moved/'catalog.json').read_bytes(), self.raw)

    def test_changed_preserved_payload_and_newer_input_stop_pending_retry(self):
        self.damaged()
        plan, op = self.inspect(), uuid.uuid4().hex
        def fault(stage):
            if stage == 'input_repair_prepared':
                raise RuntimeError('stop')
        with self.assertRaises(RuntimeError):
            self.repair(plan, op, fault)
        self.catalog.write_bytes(state._encode(dict(state._decode(self.raw), revision='newer')))
        newer = self.catalog.read_bytes()
        with self.assertRaisesRegex(state.StateConflict, 'changed after repair inspection'):
            self.repair(plan, op)
        self.assertEqual(self.catalog.read_bytes(), newer)
        self.catalog.write_bytes(b'broken catalog \xff')
        job = self.store.project/('project/jobs/asset-repair-'+op+'.json')
        intent = state._decode(job.read_bytes())['value']
        record = self.store._staged_record(intent['preserved'][0]['receipt'])
        (self.store.project/record['file']['path']).write_bytes(b'corrupted recovery copy')
        with self.assertRaises(ValueError):
            self.repair(plan, op)
        self.assertEqual(self.catalog.read_bytes(), b'broken catalog \xff')


if __name__ == '__main__':
    unittest.main()
