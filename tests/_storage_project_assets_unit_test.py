"""Atomic asset mirrors, ordinary consumers and failure recovery on CPU copies."""
import asyncio
import ast
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import wave

from PIL import Image
import _storage_runtime_unit_test as fixture
from project_assets import ProjectAssetStore
from project_ownership import claim_project_ownership, ProjectOwnershipError
from storage_runtime import runtime_access
from storage_project_assets import CATALOG, media_entries
from storage_project import payload_catalog, payload_key
from storage_project_reads import ProjectReadView
from storage_carriers import node_operation
import storage_state as state


class AssetStorageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output = self.f.store, self.f.output
        self.input = self.output/'input'
        self.input.mkdir()
        self.legacy = ProjectAssetStore(self.input, self.output/'legacy-assets-output')
        self.image = self.input/'source.png'
        Image.new('RGB', (48, 32), (20, 60, 90)).save(self.image)
        self.first = self.legacy.import_file('demo', self.image, tag='hero')['asset']
        self.clone = self.legacy.duplicate('demo', self.first['id'])['asset']
        self.audio = self.input/'sound.wav'
        with wave.open(str(self.audio), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b'\0'*1600)
        self.sound = self.legacy.import_file('demo', self.audio, role='source_track')['asset']
        self.catalog = self.input/'h3_projects/demo/catalog.json'
        self.original_input = self.input_files()
        with runtime_access(self.store, ownership_writes=True):
            owner = claim_project_ownership(self.output, 'demo', 'asset-owner-1234567890')
        self.proof = dict(owner_id='asset-owner-1234567890', epoch=owner['epoch'])

    def input_files(self):
        return {p.relative_to(self.input).as_posix():p.read_bytes()
                for p in self.input.rglob('*') if p.is_file()}

    def assets(self, **kwargs):
        return ProjectAssetStore(self.input, self.output, **kwargs)

    def refresh(self, *, pin=None, operation=None, fault=None, selected='main'):
        with runtime_access(self.store, pin=pin, selected=selected, asset_writes=True) as bound:
            bound.assets.after_stage = fault
            result = self.assets().refresh_backup('demo', operation_id=operation or uuid.uuid4().hex,
                                                  ownership_proof=self.proof)
            return result, bound.output_pin

    def test_refresh_publishes_exact_catalog_deduplicated_media_and_no_input_writes(self):
        before = self.store.snapshot()
        result, _ = self.refresh()
        after = self.store.snapshot()
        self.assertEqual(after.read(CATALOG), self.catalog.read_bytes())
        self.assertEqual((result['asset_count'],result['media_file_count'],result['copied_media_files']), (3,2,2))
        self.assertEqual(self.input_files(), self.original_input)
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertFalse((self.store.project/'project_assets').exists())
        for name, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][name],descriptor)
        expected_catalog = self.legacy.load('demo')
        with runtime_access(self.store):
            assets = self.assets()
            self.assertEqual(assets.public_catalog('demo'), expected_catalog)
            for entry in result['catalog']['assets']:
                copied, path = assets.backup_asset_path('demo',entry['id'])
                source = self.input/'h3_projects/demo'/entry['relative_path']
                self.assertEqual(copied,entry)
                self.assertEqual(Path(path).read_bytes(),source.read_bytes())
                self.assertNotEqual(Path(path).stat().st_ino,source.stat().st_ino)
                self.assertIn('/project/assets/media/',str(path))
            self.assertEqual(assets.backups(),[dict(run_name='demo',revision=result['catalog']['revision'],
                                                  assets=result['catalog']['assets'])])

    def test_accepted_read_ignores_missing_corrupt_or_newer_input_without_repair(self):
        result, _ = self.refresh()
        raw = self.catalog.read_bytes()
        for changed in (b'broken-json', state._encode(dict(result['catalog'],assets=[])), None):
            if changed is None:
                self.catalog.unlink()  # Only this disposable input fixture.
            else:
                self.catalog.write_bytes(changed)
            with runtime_access(self.store):
                actual = self.assets().load('demo')
                entry, path = self.assets().asset('demo',self.first['id'])
                self.assertEqual(actual,result['catalog'])
                self.assertEqual(Path(path).read_bytes(),self.image.read_bytes())
            self.assertEqual(self.catalog.read_bytes() if self.catalog.exists() else None,changed)
        self.catalog.write_bytes(raw)

    def test_old_pin_keeps_catalog_and_shared_media_after_new_input_edit(self):
        first, pin = self.refresh()
        self.legacy.update('demo',self.first['id'],{'tag':'renamed'})
        second, _ = self.refresh()
        self.assertEqual(second['copied_media_files'],0)
        with runtime_access(self.store,pin=pin):
            self.assertEqual(self.assets().load('demo'),first['catalog'])
        with runtime_access(self.store):
            self.assertEqual(self.assets().load('demo'),second['catalog'])

    def test_append_and_remove_cards_keep_old_backups_available_at_their_pin(self):
        first, pin = self.refresh()
        old = self.store.snapshot()
        old_raw = old.read(CATALOG)
        self.legacy.delete('demo',self.clone['id'])
        self.legacy.delete('demo',self.first['id'])
        image = self.input/'new.png'
        Image.new('RGB',(16,16),(3,4,5)).save(image)
        added = self.legacy.import_file('demo',image,tag='new')['asset']
        latest, _ = self.refresh()
        self.assertEqual(latest['copied_media_files'],1)
        self.assertEqual(len(payload_catalog(self.store.snapshot())),3)
        self.assertEqual(old.read(CATALOG),old_raw)
        self.assertEqual(state._decode(old_raw),first['catalog'])
        with runtime_access(self.store,pin=pin):
            _, path = self.assets().asset('demo',self.first['id'])
            self.assertEqual(Path(path).read_bytes(),self.image.read_bytes())
        with runtime_access(self.store,selected=self.f.named):
            self.assertEqual(self.assets().load('demo'),latest['catalog'])
            self.assertEqual(Path(self.assets().asset('demo',added['id'])[1]).read_bytes(),image.read_bytes())
            with self.assertRaises(FileNotFoundError):
                self.assets().asset('demo',self.first['id'])

    def test_empty_catalog_and_missing_primary_are_not_confused(self):
        empty = copy.deepcopy(state._decode(self.catalog.read_bytes()))
        empty['assets'] = []
        self.catalog.write_bytes(state._encode(empty))
        result, _ = self.refresh()
        self.assertEqual(result['asset_count'],0)
        self.assertEqual(result['media_file_count'],0)
        with runtime_access(self.store):
            self.assertEqual(self.assets().public_catalog('demo')['assets'],[])
        before = self.store.snapshot().reference
        self.catalog.unlink()
        with self.assertRaises(FileNotFoundError):
            self.refresh()
        self.assertEqual(self.store.snapshot().reference,before)

    def test_late_retry_after_another_branch_change_does_not_recopy_assets(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        first, _ = self.refresh(pin=pin,operation=operation)
        self.f.publish()
        before = self.store.snapshot().reference
        same, _ = self.refresh(pin=pin,operation=operation)
        self.assertEqual(first,same)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_imported_immutable_legacy_catalog_is_versioned_without_reclassifying(self):
        original_raw = self.catalog.read_bytes()
        contract = dict(category='legacy',scope='archive:legacy',immutable=True)
        self.store.commit(self.store.snapshot(),{CATALOG:dict(data=original_raw,**contract)},
                          operation_id=uuid.uuid4().hex)
        before = self.store.snapshot()
        with runtime_access(self.store):
            self.assertEqual(self.assets().load('demo'),state._decode(original_raw))
        changed = self.legacy.update('demo',self.first['id'],{'tag':'new'})
        result, _ = self.refresh()
        after = self.store.snapshot()
        self.assertEqual({k:after.state['documents'][CATALOG][k] for k in contract},contract)
        self.assertEqual(before.read(CATALOG),original_raw)
        self.assertEqual(result['catalog'],changed['catalog'])
        self.assertEqual(after.read(CATALOG),self.catalog.read_bytes())
        with runtime_access(self.store):
            self.assertEqual(self.assets().load('demo'),changed['catalog'])
        witness_address = next(key for key in after.state['documents'] if key.startswith('project_assets/.operations/'))
        self.assertEqual(state._decode(after.read(witness_address))['replaced'],{CATALOG:before.state['documents'][CATALOG]})

    def test_wrong_catalog_contract_cannot_be_reclassified_by_refresh(self):
        self.store.commit(self.store.snapshot(),{CATALOG:dict(data=self.catalog.read_bytes(),
            category='history',scope='history:main',immutable=True)},operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError,'import contract'):
            self.refresh()
        self.assertEqual(before,self.store.snapshot().reference)

    def test_failure_before_publication_and_exact_retry(self):
        for phase in ('asset_intent','asset_payloads','document','root'):
            with self.subTest(phase=phase):
                with runtime_access(self.store) as runtime:
                    pin = runtime.pin
                before, operation = self.store.snapshot().reference, uuid.uuid4().hex
                def fault(point):
                    if point == phase:
                        raise RuntimeError('injected '+phase)
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    self.refresh(pin=pin,operation=operation,fault=fault)
                self.assertEqual(self.store.snapshot().reference,before)
                expected, accepted = self.refresh(pin=pin,operation=operation)
                retried, same = self.refresh(pin=pin,operation=operation)
                self.assertEqual(retried,expected)
                self.assertEqual(same,accepted)
                self.assertEqual(self.input_files(),self.original_input)

    def test_lost_reply_after_real_publication_recovers_the_accepted_result(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        publish = self.store._publish
        def lost(*args,**kwargs):
            publish(*args,**kwargs)
            raise RuntimeError('lost reply')
        with patch.object(self.store,'_publish',side_effect=lost), self.assertRaisesRegex(RuntimeError,'lost reply'):
            self.refresh(pin=pin,operation=operation)
        before = self.store.snapshot().reference
        result, _ = self.refresh(pin=pin,operation=operation)
        self.assertEqual(result['asset_count'],3)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_source_catalog_or_media_changes_do_not_publish_partial_backup(self):
        before = self.store.snapshot().reference
        source = self.input/'h3_projects/demo'/self.first['relative_path']
        original = source.read_bytes()
        source.write_bytes(b'changed')
        with self.assertRaisesRegex(state.StateConflict,'Input asset differs'):
            self.refresh()
        self.assertEqual(self.store.snapshot().reference,before)
        source.write_bytes(original)
        def fault(point):
            if point == 'asset_payloads':
                self.catalog.write_bytes(b'{}')
        with self.assertRaisesRegex(state.StateConflict,'catalog changed'):
            self.refresh(fault=fault)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_missing_or_damaged_accepted_backup_never_uses_input_or_legacy_leftovers(self):
        self.refresh()
        record = payload_catalog(self.store.snapshot())['project_assets/'+self.first['relative_path']]
        path = self.store.project/record['file']['path']
        path.write_bytes(b'damaged accepted copy')
        leftover = self.store.project/'project_assets'/self.first['relative_path']
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(self.image.read_bytes())
        with runtime_access(self.store), self.assertRaisesRegex(ValueError,'checksum'):
            self.assets().asset('demo',self.first['id'])
        path.unlink()  # Deliberately damage only this disposable test artifact.
        with runtime_access(self.store), self.assertRaises(OSError):
            self.assets().backup_asset_path('demo',self.first['id'])

    def test_missing_catalog_cannot_adopt_input_or_unindexed_backup(self):
        leftover = self.store.project/CATALOG
        leftover.parent.mkdir(parents=True)
        leftover.write_bytes(self.catalog.read_bytes())
        with runtime_access(self.store), self.assertRaisesRegex(ValueError,'No accepted'):
            self.assets().public_catalog('demo')
        with runtime_access(self.store):
            self.assertEqual(self.assets().backups(),[])
        self.assertEqual(self.input_files(),self.original_input)

    def test_read_binding_does_not_allow_edit_preview_upload_or_clone_writes(self):
        self.refresh()
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            assets = self.assets()
            actions = [lambda:assets.update('demo',self.first['id'],{'tag':'no'}),
                lambda:assets._save_catalog(assets.load('demo')),
                lambda:assets.delete('demo',self.first['id']),
                lambda:assets.create_folder('demo','No'),
                lambda:assets.upload_path('demo','new.png'),
                lambda:assets.ensure_thumbnail('demo',self.first['id']),
                lambda:assets.duplicate_project('demo','other')]
            for action in actions:
                with self.assertRaises(ValueError):
                    action()
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(self.input_files(),self.original_input)
        self.assertFalse((self.input/'h3_projects/other').exists())

    def test_separate_grants_ownership_and_binding_lifetime(self):
        for enabled, proof in ((False,self.proof),(True,None),(True,dict(owner_id='wrong',epoch=1))):
            with runtime_access(self.store,asset_writes=enabled), self.assertRaises(ValueError):
                self.assets().refresh_backup('demo',operation_id=uuid.uuid4().hex,ownership_proof=proof)
        with runtime_access(self.store,asset_writes=True) as runtime:
            with node_operation({}, None), self.assertRaisesRegex(ValueError,'read-only'):
                self.assets().refresh_backup('demo',operation_id=uuid.uuid4().hex,ownership_proof=self.proof)
            service = runtime.assets
        with self.assertRaisesRegex(ValueError,'escaped'):
            service.refresh(self.input,uuid.uuid4().hex,self.proof)
        self.assertEqual(self.input_files(),self.original_input)

    def test_other_project_and_old_reader_cannot_refresh(self):
        with runtime_access(self.store,asset_writes=True):
            with self.assertRaisesRegex(ValueError,'different output/project'):
                self.assets().load('other')
            reader = self.assets(rehearsal_view=ProjectReadView(self.store))
            with self.assertRaisesRegex(ValueError,'current explicit'):
                reader.refresh_backup('demo',operation_id=uuid.uuid4().hex,ownership_proof=self.proof)

    def test_asset_gate_checks_do_not_copy_full_index_or_expose_mutable_authority(self):
        self.refresh()
        original = self.store.snapshot()
        # Entering a read session builds its isolated index once. Resolving one
        # asset must not copy the full index again at every nested gate check.
        with runtime_access(self.store):
            assets = self.assets()
            with patch.object(state.Snapshot,'_root',side_effect=AssertionError('unnecessary full-index copy')):
                entry,path = assets.backup_asset_path('demo',self.first['id'])
                self.assertEqual(entry,self.first)
                self.assertEqual(Path(path).read_bytes(),self.image.read_bytes())
        exposed = original.state
        exposed['epoch'] += 1
        exposed['documents'].clear()
        actual = self.store.snapshot()
        self.assertEqual(actual.reference,original.reference)
        self.assertIn(CATALOG,actual.state['documents'])
        self.assertNotEqual(actual.state['epoch'],exposed['epoch'])

    def test_path_escape_case_collision_and_conflicting_duplicates_rejected(self):
        original = state._decode(self.catalog.read_bytes())
        bad = []
        for path in ('../outside.png','images/../../outside.png','images/sub/file.png','C:\\source.png'):
            doc = copy.deepcopy(original)
            doc['assets'][0]['relative_path'] = path
            bad.append(doc)
        doc = copy.deepcopy(original)
        doc['assets'][1]['size'] += 1
        bad.append(doc)
        doc = copy.deepcopy(original)
        doc['assets'][1]['relative_path'] = doc['assets'][0]['relative_path'].upper()
        bad.append(doc)
        for doc in bad:
            with self.assertRaises(ValueError):
                media_entries(doc,'demo')
        source = self.input/'h3_projects/demo'/self.first['relative_path']
        source.unlink()
        source.symlink_to(self.image)
        with self.assertRaisesRegex(ValueError,'symlinks'):
            self.refresh()

    def test_stale_request_and_changed_identity_are_rejected(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        self.refresh(pin=pin,operation=operation)
        self.legacy.update('demo',self.first['id'],{'tag':'new'})
        before = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.refresh(pin=pin)
        with self.assertRaises(state.StateConflict):
            self.refresh(pin=pin,operation=operation)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_actual_catalog_and_chain_source_http_handlers_read_accepted_assets(self):
        result, _ = self.refresh()
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        names = ('_project_asset_catalog','_project_asset_sources')
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node,ast.AsyncFunctionDef) and node.name in names]
        namespace = dict(asyncio=asyncio,_project_asset_store=self.assets,
            _project_asset_error_response=lambda exc:(400,str(exc)),
            web=SimpleNamespace(json_response=lambda body:(200,body)))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        with runtime_access(self.store):
            code, catalog = asyncio.run(namespace['_project_asset_catalog'](SimpleNamespace(query={'project':'demo'})))
            self.assertEqual(code,200,catalog)
            self.assertEqual(catalog,result['catalog'])
            code, body = asyncio.run(namespace['_project_asset_sources'](SimpleNamespace(query={'source':'chains'})))
            self.assertEqual(code,200,body)
            self.assertEqual(body['items'][0]['assets'],catalog['assets'])

    def test_ordinary_recovery_preserves_catalog_and_can_restore_input_without_source(self):
        import storage_recovery as recovery
        self.refresh()
        lab = self.output.parent
        receipt = lab/('assets-'+uuid.uuid4().hex+'.json')
        state.atomic_json(receipt,dict(copy=str(self.store.project),source=str(self.f.f.f.root),independent_copies=True))
        destination = lab/('recovered-assets-'+uuid.uuid4().hex)
        journal = recovery.prepare_legacy_copy(receipt,destination,lab/('asset-recovery-'+uuid.uuid4().hex),
                                                rehearsal_store=self.store)
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        ordinary = ProjectAssetStore(destination/'fresh-input',destination)
        actual = ordinary.load('demo')
        self.assertEqual(actual,state._decode(self.catalog.read_bytes()))
        _, path = ordinary.backup_asset_path('demo',self.first['id'])
        self.assertEqual(Path(path).read_bytes(),self.image.read_bytes())
        self.assertEqual(self.input_files(),self.original_input)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
