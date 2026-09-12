"""Public chapter retirement, undo and recovery with no media deletion."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_chapters_integration_test as fixture

chain, runtime, state = fixture.chain, fixture.runtime, fixture.state
module = fixture.fixture.module
manager_type = module('chapter_snapshot_retirement').ChapterSnapshotManager


class ChapterRetentionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ChapterTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof = self.f.store, self.f.proof
        self.output, self.run = self.store.project.parent.parent, self.store.project.name
        self.manager = manager_type(self.output)
        self.first = self.f.seal()[0]
        self.changed = self.f.f.repin()
        self.changed.setdefault('editorial', {})['subtitles'] = {'mode':'off', 'offset_seconds':3}
        self.latest = self.f.seal(self.changed, unique_id='newer')[0]

    def preview(self, sealed=None):
        with runtime.runtime_access(self.store):
            return self.manager.retirement_preview(self.run, (sealed or self.latest)['chapter_manifest_path'])

    def retire(self, preview, proof=True):
        return self.manager.retire(self.run, preview['path'], preview['snapshot'],
                                   ownership_proof=self.proof if proof is True else proof)

    def test_retire_moves_only_metadata_releases_pin_and_undo_restores_exactly(self):
        before = self.store.snapshot()
        preview = self.preview()
        self.assertEqual(self.store.snapshot().reference, before.reference)
        observed = []
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            bound.retention.after_stage = lambda stage:observed.append(self.store.snapshot().reference)
            result = self.retire(preview)
        after = self.store.snapshot()
        self.assertTrue(all(ref in (before.reference, after.reference) for ref in observed))
        address = self.f.address(self.latest)
        archived = address.replace('/manifests/', '/retired_manifests/')
        self.assertNotIn(address, after.state['documents'])
        self.assertEqual(after.state['documents'][archived], before.state['documents'][address])
        self.assertEqual(after.read(archived), before.read(address))
        self.assertEqual(module('storage_project').payload_catalog(after), module('storage_project').payload_catalog(before))
        for key, descriptor in before.state['documents'].items():
            if key != address:
                self.assertEqual(after.state['documents'][key], descriptor)
        self.assertEqual((result['deleted_files'],result['reclaimed_bytes']), (0,0))
        self.assertTrue(result['undo_available'])
        self.assertEqual(self.f.load()[0]['chapter_manifest_id'], self.first['chapter_manifest_id'])
        with self.assertRaises((FileNotFoundError, ValueError)):
            self.f.load(self.latest['chapter_manifest_id'])
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.f.seal(self.changed, unique_id='newer')
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            undo = bound.retention.preview_undo(result['operation_id'])
            self.assertTrue(undo['allowed'], undo['blockers'])
            bound.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        restored = self.store.snapshot()
        self.assertNotIn(archived, restored.state['documents'])
        for key, descriptor in before.state['documents'].items():
            self.assertEqual(restored.state['documents'][key], descriptor)
        self.assertEqual(self.f.load()[0]['chapter_manifest_id'], self.latest['chapter_manifest_id'])
        self.store.verify_payloads()

    def test_separate_retention_grant_ownership_and_fresh_preview_are_required(self):
        preview = self.preview()
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, export_writes=True), self.assertRaisesRegex(ValueError, 'retention writes'):
            self.retire(preview)
        with runtime.runtime_access(self.store, retention_writes=True), self.assertRaises(module('project_ownership').ProjectOwnershipError):
            self.retire(preview, proof=None)
        self.assertEqual(self.store.snapshot().reference, before)
        self.f.seal(self.f.f.repin(), unique_id='after-preview')
        changed = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True), self.assertRaises(module('checkpoint_manager').CheckpointDeleteBlocked):
            self.retire(preview)
        self.assertEqual(self.store.snapshot().reference, changed)

    def test_interrupted_commit_retry_and_undone_retry_never_rollback(self):
        preview = self.preview()
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            def fail(stage):
                if stage == 'commit':
                    raise OSError('lost retirement ack')
            bound.retention.after_stage = fail
            with self.assertRaisesRegex(OSError, 'lost retirement ack'):
                self.retire(preview)
        after = self.store.snapshot().reference
        self.assertNotEqual(before, after)
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            result = self.retire(preview)
            self.assertEqual(self.store.snapshot().reference, after)
            undo = bound.retention.preview_undo(result['operation_id'])
            bound.retention.undo(result['operation_id'], undo['snapshot'], proof=self.proof)
        restored = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True):
            result = self.retire(preview)
        self.assertFalse(result['undo_available'])
        self.assertEqual(self.store.snapshot().reference, restored)

    def test_reverse_recovery_retains_tombstone_and_older_latest(self):
        preview = self.preview()
        with runtime.runtime_access(self.store, retention_writes=True):
            self.retire(preview)
        before = self.store.snapshot()
        f = self.f.f.f.f
        receipt = f.lab/'retired-chapter-receipt.json'
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(f.source), independent_copies=True))
        recovery = module('storage_recovery')
        output = f.lab/'retired-chapter-output'
        journal = recovery.prepare_legacy_copy(receipt, output, f.lab/'retired-chapter-recovery', rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        self.assertTrue((output/preview['retired_path']).is_file())
        self.assertFalse((output/preview['path']).exists())
        with patch.object(chain.folder_paths, 'output_directory', str(output)):
            loaded = chain.MiniMaxH3ChainChapterLoad().load(self.run, 1)[0]
            self.assertEqual(loaded['chapter_manifest_id'], self.first['chapter_manifest_id'])
            with self.assertRaisesRegex(ValueError, 'retired'):
                chain._persist_chapter_manifest(json.loads(before.read(preview['retired_path'].removeprefix('h3_chains/'+self.run+'/'))))
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_actual_http_retirement_and_common_undo_routes(self):
        from aiohttp import web
        def call(path, body, *, proof=True, undo=False):
            async def content():
                return body
            headers = {} if not proof else {'X-H3-Workflow-Owner':self.proof['owner_id'],
                                             'X-H3-Ownership-Epoch':str(self.proof['epoch'])}
            request = SimpleNamespace(path=path, json=content, headers=headers, query={}, method='POST')
            handler = chain._checkpoint_retention_undo if undo else chain._chapter_snapshot_retirement
            with patch.object(chain, 'web', web):
                response = asyncio.run(handler(request))
            return response.status, json.loads(response.body)
        body = dict(run_name=self.run, path=self.latest['chapter_manifest_path'])
        with runtime.runtime_access(self.store):
            code, preview = call('/retire-preview', body, proof=False)
            self.assertEqual(code, 200, preview)
        body['snapshot'] = preview['snapshot']
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            self.assertEqual(call('/retire', body, proof=False)[0], 423)
            self.assertEqual(self.store.snapshot().reference, before)
            def lost_ack(stage):
                if stage == 'commit':
                    raise OSError('uncertain chapter retirement publication')
            bound.retention.after_stage = lost_ack
            code, failure = call('/retire', body)
            self.assertEqual(code, 503, failure)
            self.assertFalse(failure['retry_automatically'])
        with runtime.runtime_access(self.store, retention_writes=True):
            code, retired = call('/retire', body)
            self.assertEqual(code, 200, retired)
        undo_body = dict(run_name=self.run, operation_id=retired['operation_id'])
        with runtime.runtime_access(self.store):
            code, preview = call('/undo-preview', undo_body, undo=True)
            self.assertEqual(code, 200, preview)
        undo_body['snapshot'] = preview['snapshot']
        with runtime.runtime_access(self.store, retention_writes=True):
            self.assertEqual(call('/undo', undo_body, undo=True, proof=False)[0], 423)
            code, undone = call('/undo', undo_body, undo=True)
            self.assertEqual(code, 200, undone)
        self.assertEqual(self.f.load()[0]['chapter_manifest_id'], self.latest['chapter_manifest_id'])

    def test_before_publication_failure_preserves_old_catalogue_and_retries(self):
        preview = self.preview()
        before = self.store.snapshot()
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            def fail(stage):
                if stage == 'root':
                    raise OSError('before chapter retirement publication')
            bound.retention.after_stage = fail
            with self.assertRaisesRegex(OSError, 'before chapter'):
                self.retire(preview)
        self.assertEqual(self.store.snapshot().reference, before.reference)
        with runtime.runtime_access(self.store, retention_writes=True):
            self.retire(preview)
        self.assertEqual(self.f.load()[0]['chapter_manifest_id'], self.first['chapter_manifest_id'])

    def test_removing_last_snapshot_releases_only_its_chapter_recovery_pins(self):
        revision = self.first['segments'][0]['revision']
        with runtime.runtime_access(self.store) as bound:
            graph = chain.CheckpointGraphManager(self.output, rehearsal_view=bound.reader)
            before = graph.deletion_preview(self.run, 1, revision)
        self.assertEqual(len(before['chapter_references']), 2)
        for sealed in (self.latest, self.first):
            preview = self.preview(sealed)
            with runtime.runtime_access(self.store, retention_writes=True):
                self.retire(preview)
        with runtime.runtime_access(self.store) as bound:
            graph = chain.CheckpointGraphManager(self.output, rehearsal_view=bound.reader)
            after = graph.deletion_preview(self.run, 1, revision)
        self.assertFalse(after['chapter_references'])
        with self.assertRaises(FileNotFoundError):
            self.f.load()
        self.store.verify_payloads()

    def test_retirement_rejects_cross_branch_target_without_changes(self):
        f = self.f.f.f.f
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = chain.WorkingBranches(self.output, self.run).create('main', 'Another chapter branch',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']} for s in f.f.plan['shots']]})},
                through_scene=2)['id']
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, selected=branch), self.assertRaisesRegex(ValueError, 'another branch'):
            self.manager.retirement_preview(self.run, self.latest['chapter_manifest_path'])
        self.assertEqual(self.store.snapshot().reference, before)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
