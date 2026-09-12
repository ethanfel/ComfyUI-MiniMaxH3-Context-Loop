"""Real processing/PNG and chapter undo after project-only recovery/re-import."""
import asyncio
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_processing_retention_integration_test as processing
import _storage_chapter_retention_integration_test as chapter

chain, runtime, state, module = processing.chain, processing.runtime, processing.state, processing.module


class Roundtrip:
    def initialize(self, fixture):
        self.f = fixture
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.store, self.proof = fixture.store, fixture.proof
        self.run = self.store.project.name
        source = fixture
        while not hasattr(source, 'lab'):
            source = source.f
        self.lab, self.original = source.lab, source.source

    def roundtrip(self):
        old = self.store
        suffix = uuid.uuid4().hex[:8]
        receipt = self.lab/('portable-source-'+suffix+'.json')
        state.atomic_json(receipt, dict(copy=str(old.project), source=str(self.original), independent_copies=True))
        output = self.lab/('portable-recovered-'+suffix)
        recovery = module('storage_recovery')
        journal = recovery.prepare_legacy_copy(receipt, output, self.lab/('portable-recovery-'+suffix),
                                               rehearsal_store=old)
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        recovered = output/'h3_chains'/self.run
        copied = self.lab/('portable-copy-'+suffix)/'h3_chains'/self.run
        shutil.copytree(recovered, copied, copy_function=shutil.copy2)
        self.assertFalse((copied.parent.parent/'.h3-storage-recovery').exists())
        receipt = self.lab/('portable-copy-'+suffix+'.json')
        state.atomic_json(receipt, dict(copy=str(copied), source=str(recovered), independent_copies=True))
        documents, targets = {}, {}
        contracts = old.snapshot().state['documents']
        for path in copied.rglob('*'):
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            key = path.relative_to(copied).as_posix()
            if path.suffix in ('.json', '.txt'):
                contract = ({k:contracts[key][k] for k in ('scope', 'category', 'immutable')}
                            if key in contracts else dict(scope='archive:portable', category='recovery', immutable=True))
                documents[key] = dict(source=key, sha256=state._hash(path.read_bytes()), **contract)
            else:
                targets[key] = dict(target='project/recovery/'+state._hash(key.encode())+'.bin',
                                    scope='archive:portable', immutable=True)
        imported = state.create_control_rehearsal(receipt, self.lab/('portable-import-'+suffix), documents,
                                                   commit_protocol='immutable_slots_v1')
        access = state.control_rehearsal_access(imported.project)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        migration = module('storage_project_migration')
        journal = migration.prepare_join(receipt, imported, self.lab/('portable-join-'+suffix), targets)
        migration.join_payloads(journal)
        self.store = module('storage_project').ProjectStore(imported.project)
        self.output = self.store.project.parent.parent
        folder = patch.object(chain.folder_paths, 'output_directory', str(self.output))
        folder.start()
        self.addCleanup(folder.stop)
        with runtime.runtime_access(self.store, ownership_writes=True):
            claimed = module('project_ownership').claim_project_ownership(self.output, self.run, self.proof['owner_id'])
        self.proof = dict(owner_id=self.proof['owner_id'], epoch=claimed['epoch'])

    def undo(self, operation, snapshot=None, *, proof=True, grant=True, branch='main'):
        async def body():
            return dict(run_name=self.run, operation_id=operation, snapshot=snapshot)
        request = SimpleNamespace(json=body, path='/checkpoint/'+('undo-preview' if snapshot is None else 'undo'),
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'], 'X-H3-Ownership-Epoch':str(self.proof['epoch'])}
                    if proof else {})
        from aiohttp import web
        with runtime.runtime_access(self.store, selected=branch, retention_writes=grant), patch.object(chain, 'web', web):
            response = asyncio.run(chain._checkpoint_retention_undo(request))
        return response.status, json.loads(response.body)

    def restore(self, operation):
        code, preview = self.undo(operation)
        self.assertEqual(code, 200, preview)
        self.assertTrue(preview['allowed'], preview)
        code, result = self.undo(operation, preview['snapshot'])
        self.assertEqual(code, 200, result)
        after = self.store.snapshot().reference
        self.assertEqual(self.undo(operation, preview['snapshot']), (code, result))
        self.assertEqual(self.store.snapshot().reference, after)
        return result


class ProcessingTests(Roundtrip, unittest.TestCase):
    def setUp(self):
        self.initialize(processing.ProcessingRetentionTests())

    def retired(self):
        _, segment, exported = self.f.saved()
        self.before = self.store.snapshot()
        self.export_address, _ = self.f.f.record(exported)
        preview = self.f.preview(segment)
        self.assertTrue(preview['allowed'], preview)
        self.files = preview['files']
        prefix = 'h3_chains/'+self.run+'/'
        self.expected_controls, self.expected_payloads = {}, {}
        for item in self.files:
            if not item['exists']:
                continue
            logical = item['path'].removeprefix(prefix)
            if logical in self.before.state['documents']:
                self.expected_controls[logical] = self.before.read(logical)
            else:
                path = self.store.payload_path(self.before, logical, verify=True)
                self.expected_payloads[logical] = (path.read_bytes(), (path.stat().st_dev, path.stat().st_ino))
        self.expected_export = self.before.read(self.export_address)
        with runtime.runtime_access(self.store, retention_writes=True):
            result = self.f.delete(segment, preview)
        self.roundtrip()
        return result['operation_id']

    def test_processing_take_png_pointer_and_indexes_restore_after_project_only_import(self):
        operation = self.retired()
        imported = self.store.snapshot()
        self.restore(operation)
        after = self.store.snapshot()
        for logical, raw in self.expected_controls.items():
            self.assertEqual(after.read(logical), raw)
        for logical, (raw, identity) in self.expected_payloads.items():
            restored = self.store.payload_path(after, logical, verify=True)
            self.assertEqual(restored.read_bytes(), raw, logical)
            self.assertNotEqual((restored.stat().st_dev, restored.stat().st_ino), identity)
        self.assertEqual(after.read(self.export_address), self.expected_export)
        self.assertEqual(imported.read('plan.json'), after.read('plan.json'))
        self.store.verify_payloads()
        # A second recovery must preserve restored PNG/media and the closed undo.
        self.roundtrip()
        self.assertEqual(self.store.snapshot().read(self.export_address), self.expected_export)
        code, preview = self.undo(operation)
        self.assertEqual(code, 200, preview)
        self.assertFalse(preview['allowed'])

    def test_later_png_index_edit_is_preserved_and_blocks_imported_undo(self):
        operation = self.retired()
        current = self.store.snapshot()
        key = self.export_address
        value = state._decode(current.read(key))
        value['later_edit'] = 'Keep this post-import work'
        descriptor = current.state['documents'][key]
        self.store._commit_changes(current, {key:dict(data=state._encode(value),
            **{k:descriptor[k] for k in ('scope', 'category', 'immutable')})},
            operation_id=uuid.uuid4().hex, replace_documents={key:descriptor} if descriptor['immutable'] else {})
        before = self.store.snapshot()
        code, response = self.undo(operation)
        self.assertEqual(code, 409, response)
        self.assertEqual(before.reference, self.store.snapshot().reference)
        self.assertEqual(value, state._decode(self.store.snapshot().read(key)))

    def test_missing_grant_owner_and_interrupted_staging_never_partially_restore(self):
        operation = self.retired()
        code, preview = self.undo(operation)
        self.assertEqual(code, 200, preview)
        before = self.store.snapshot().reference
        self.assertEqual(self.undo(operation, preview['snapshot'], proof=False)[0], 423)
        self.assertEqual(self.undo(operation, preview['snapshot'], grant=False)[0], 400)
        original = self.store.stage_payload
        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('portable processing staging interrupted')
        with patch.object(self.store, 'stage_payload', interrupt):
            self.assertEqual(self.undo(operation, preview['snapshot'])[0], 503)
        self.assertEqual(before, self.store.snapshot().reference)
        self.restore(operation)


class ChapterTests(Roundtrip, unittest.TestCase):
    def setUp(self):
        self.initialize(chapter.ChapterRetentionTests())

    def retired(self, *, undone=False):
        self.before = self.store.snapshot()
        preview = self.f.preview()
        prefix = 'h3_chains/'+self.run+'/'
        self.address = preview['path'].removeprefix(prefix)
        self.archived = preview['retired_path'].removeprefix(prefix)
        self.expected_chapter = self.before.read(self.address)
        with runtime.runtime_access(self.store, retention_writes=True):
            result = self.f.retire(preview)
        if undone:
            self.restore(result['operation_id'])
        self.roundtrip()
        return result['operation_id']

    def load(self):
        with processing.carriers.node_host(self.store):
            return chain.MiniMaxH3ChainChapterLoad().load(self.run, 1)[0]

    def test_chapter_retirement_archive_and_latest_load_survive_portable_undo(self):
        operation = self.retired()
        imported = self.store.snapshot()
        self.assertEqual(self.load()['chapter_manifest_id'], self.f.first['chapter_manifest_id'])
        self.restore(operation)
        after = self.store.snapshot()
        self.assertNotIn(self.archived, after.state['documents'])
        self.assertEqual(after.read(self.address), self.expected_chapter)
        self.assertEqual(after.read('plan.json'), imported.read('plan.json'))
        self.assertEqual(self.load()['chapter_manifest_id'], self.f.latest['chapter_manifest_id'])
        self.store.verify_payloads()

    def test_already_restored_native_chapter_reports_closed_after_import(self):
        operation = self.retired(undone=True)
        before = self.store.snapshot().reference
        code, preview = self.undo(operation)
        self.assertEqual(code, 200, preview)
        self.assertFalse(preview['allowed'])
        self.assertEqual(self.undo(operation, preview['snapshot'])[0], 409)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.load()['chapter_manifest_id'], self.f.latest['chapter_manifest_id'])

    def test_modified_chapter_archive_cannot_overwrite_later_work(self):
        operation = self.retired()
        current = self.store.snapshot()
        descriptor = current.state['documents'][self.archived]
        changed = current.read(self.archived)+b'\n'
        self.store._commit_changes(current, {self.archived:dict(data=changed,
            **{k:descriptor[k] for k in ('scope', 'category', 'immutable')})},
            operation_id=uuid.uuid4().hex, replace_documents={self.archived:descriptor})
        before = self.store.snapshot().reference
        code, response = self.undo(operation)
        self.assertEqual(code, 409, response)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(changed, self.store.snapshot().read(self.archived))

    def test_chapter_undo_lost_reply_is_idempotent(self):
        operation = self.retired()
        code, preview = self.undo(operation)
        self.assertEqual(code, 200, preview)
        original = self.store._commit_changes
        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('portable chapter undo accepted before lost reply')
        with patch.object(self.store, '_commit_changes', interrupt):
            code, response = self.undo(operation, preview['snapshot'])
        self.assertEqual(code, 503, response)
        before = self.store.snapshot().reference
        code, response = self.undo(operation, preview['snapshot'])
        self.assertEqual(code, 200, response)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(self.load()['chapter_manifest_id'], self.f.latest['chapter_manifest_id'])


if __name__ == '__main__':
    unittest.main(argv=[__file__])
