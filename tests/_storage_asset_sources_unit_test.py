"""Independent source pins, atomic cross-project imports and no source writes."""
import ast
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import _storage_asset_edits_unit_test as fixture
from _storage_handoff_routes_unit_test import routes
from _storage_branch_routes_unit_test import request
from project_assets import ProjectAssetStore, ProjectAssetConflictError
from project_ownership import claim_project_ownership
from storage_asset_sources import asset_source_access
from storage_runtime import runtime_access, current_runtime
from storage_project_assets import CATALOG
from storage_project import ProjectStore, FORMAT
import storage_state as state


class AssetSourceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssetEditTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.input = self.f.store, self.f.input
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lab = Path(self.temp.name)
        legacy_root = self.lab/'source/h3_chains/donor'
        legacy_root.mkdir(parents=True)
        raw = b'{"run_name":"donor","seed":18446744073709551615,"prompt":"kept"}\n'
        (legacy_root/'plan.json').write_bytes(raw)
        receipt = self.lab/'source.json'
        state.atomic_json(receipt, dict(copy=str(legacy_root), source=str(self.lab/'not-the-copy'), independent_copies=True))
        control = state.create_control_rehearsal(receipt, self.lab/'migrated', {
            'plan.json':dict(source='plan.json',sha256=state._hash(raw),scope='branch:main',category='branches',immutable=False)})
        marker = state._decode((control.project/'storage.json').read_bytes())
        marker.update(format=FORMAT, mode=ProjectStore.MODE)  # Empty-payload fixture only.
        state.atomic_json(control.project/'storage.json', marker)
        self.source = ProjectStore(control.project)
        self.source_output = control.project.parent.parent
        self.legacy = ProjectAssetStore(self.input, self.lab/'legacy-assets')
        self.picture = self.legacy.import_file('donor', self.f.f.image, role='semantic_anchor', tag='source_hero')['asset']
        self.mix = self.legacy.import_file('donor', self.f.f.audio, role='audio_reference', original_name='mix.wav', tag='mix')['asset']
        self.vocal = self.legacy.import_file('donor', self.f.f.audio, role='audio_reference', original_name='vocals.wav', tag='voice')['asset']
        self.legacy.update('donor', self.vocal['id'], dict(lyrics='Stem lyrics'))
        self.legacy.update('donor', self.mix['id'], dict(lyrics='Mix lyrics', options=dict(
            audio_tracks=dict(full_mix=self.mix['id'], vocals=self.vocal['id'], instrumental=''))))
        with state.control_rehearsal_access(self.source.project):
            with runtime_access(self.source, ownership_writes=True):
                owner = claim_project_ownership(self.source_output, 'donor', 'donor-owner-1234567890')
            self.proof = dict(owner_id='donor-owner-1234567890', epoch=owner['epoch'])
            with runtime_access(self.source, asset_writes=True) as bound:
                ProjectAssetStore(self.input, self.source_output).refresh_backup('donor',
                    operation_id=uuid.uuid4().hex, ownership_proof=self.proof)
                self.pin = bound.output_pin
            self.source_root = self.source.snapshot().reference
            self.grant_scope = asset_source_access(self.source, pin=self.pin)
            self.grant = self.grant_scope.__enter__()
            self.addCleanup(self.grant_scope.__exit__, None, None, None)
        self.source_files = {p.relative_to(self.source.project).as_posix():p.read_bytes()
            for p in self.source.project.rglob('*') if p.is_file()}

    def call(self, identity=None, *, operation=None, pin=None, fault=None, grants=None, **kwargs):
        with runtime_access(self.store, pin=pin, asset_writes=True, asset_input_root=self.input,
                            asset_sources=(self.grant,) if grants is None else grants) as bound:
            bound.assets.after_stage = fault
            result = self.f.f.assets().import_project_asset('demo', 'donor', identity or self.picture['id'],
                source_pin=self.pin, storage_operation_id=operation or uuid.uuid4().hex,
                ownership_proof=self.f.f.proof, **kwargs)
            self.assertIs(current_runtime(self.f.f.output), bound)
            return result, bound.output_pin

    def unchanged_source(self):
        self.assertEqual({p.relative_to(self.source.project).as_posix():p.read_bytes()
            for p in self.source.project.rglob('*') if p.is_file()}, self.source_files)

    def test_listing_uses_only_granted_catalogs_and_never_restores_missing_source_inputs(self):
        directory = self.input/'h3_projects/donor'
        directory.rename(self.input/'withheld-donor-input')
        with runtime_access(self.store, asset_sources=(self.grant,)):
            store = self.f.f.assets()
            rows = store.projects('source_hero', exclude_project='demo')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['source_pin'], self.pin)
            self.assertEqual(rows[0]['assets'][0]['id'], self.picture['id'])
            self.assertEqual({row['project'] for row in store.project_catalogs()}, {'demo','donor'})
            self.assertEqual(next(row['source_pin'] for row in store.backups() if row['run_name']=='donor'), self.pin)
        self.assertFalse(directory.exists())
        with runtime_access(self.store):
            self.assertEqual(self.f.f.assets().projects(exclude_project='demo'), [])
        self.unchanged_source()

    def test_import_is_one_commit_with_independent_bytes_and_source_provenance(self):
        before = self.store.snapshot()
        result, _ = self.call()
        self.assertEqual(self.store.snapshot().state['generation'], before.state['generation']+1)
        self.assertEqual(result['asset']['role'], 'semantic_anchor')
        self.assertEqual(result['asset']['imported_from']['source_pin'], self.pin)
        with runtime_access(self.store):
            _, target = self.f.f.assets().asset('demo', result['asset']['id'])
        with state.control_rehearsal_access(self.source.project):
            source = self.source.payload_path(self.source.snapshot(), 'project_assets/'+self.picture['relative_path'], verify=True)
        self.assertEqual(Path(target).read_bytes(), source.read_bytes())
        self.assertNotEqual(Path(target).stat().st_ino, source.stat().st_ino)
        self.unchanged_source()

    def test_listing_does_not_hash_media_but_import_still_verifies_same_size_corruption(self):
        with runtime_access(self.store, asset_sources=(self.grant,)):
            with patch('storage_project._hash_file', side_effect=AssertionError('Listing read media bytes')):
                self.assertTrue(self.f.f.assets().projects(exclude_project='demo'))
                self.assertTrue(self.f.f.assets().project_catalogs())
                self.assertTrue(self.f.f.assets().backups())
        with state.control_rehearsal_access(self.source.project):
            media = self.source.payload_path(self.source.snapshot(),
                'project_assets/'+self.picture['relative_path'], verify=True)
        original = media.read_bytes()
        media.write_bytes(bytes([original[0] ^ 1])+original[1:])
        with runtime_access(self.store, asset_sources=(self.grant,)):
            self.assertTrue(self.f.f.assets().projects(exclude_project='demo'))
        before, raw = self.store.snapshot().reference, self.f.catalog.read_bytes()
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.call()
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(self.f.catalog.read_bytes(), raw)

    def test_audio_dependencies_remap_ids_and_lyrics_in_one_catalog_commit(self):
        before = self.store.snapshot()
        result, _ = self.call(self.mix['id'])
        self.assertEqual(self.store.snapshot().state['generation'], before.state['generation']+1)
        imported = result['asset']
        bindings = imported['options']['audio_tracks']
        self.assertEqual(bindings['full_mix'], imported['id'])
        self.assertNotEqual(bindings['vocals'], self.vocal['id'])
        vocal = next(item for item in result['catalog']['assets'] if item['id']==bindings['vocals'])
        self.assertFalse(vocal['enabled'])
        self.assertEqual(vocal['lyrics'], 'Stem lyrics')
        self.assertEqual(imported['lyrics'], 'Mix lyrics')
        self.assertEqual(vocal['imported_from']['asset_id'], self.vocal['id'])
        self.assertEqual(self.f.catalog.read_bytes(), self.store.snapshot().read(CATALOG))
        self.unchanged_source()

    def test_exact_retry_after_preparation_uses_owned_copy_not_source_availability(self):
        for stage in ('input_media_staged','input_edit_requested','input_edit_prepared',
                      'input_edit_pending','input_edit_published','root','input_edit_committed'):
            with self.subTest(stage=stage):
                pin, op = self.f.pin(), uuid.uuid4().hex
                def fault(point):
                    if point==stage:
                        raise RuntimeError('interrupted '+stage)
                with self.assertRaisesRegex(RuntimeError,'interrupted'):
                    self.call(self.mix['id'], operation=op, pin=pin, fault=fault)
                grants = () if stage not in ('input_edit_requested','input_media_staged') else None
                result, after = self.call(self.mix['id'], operation=op, pin=pin, grants=grants)
                same, retry = self.call(self.mix['id'], operation=op, pin=pin, grants=())
                self.assertEqual((same,retry),(result,after))
                self.assertFalse(self.f.pending.exists())
        self.unchanged_source()

    def test_slot_import_preserves_slot_options_and_does_not_copy_audio_dependencies(self):
        catalog, _ = self.f.call('sync_reference_slots', [dict(kind='audio', tag='sound_slot', role='audio_reference')])
        slot = catalog['reference_slots'][0]['id']
        count = len(catalog['assets'])
        result, _ = self.call(self.mix['id'], slot_id=slot)
        self.assertEqual(result['bound_slot_id'], slot)
        self.assertEqual(result['asset']['tag'], 'sound_slot')
        self.assertEqual(result['asset']['lyrics'], 'Mix lyrics')
        self.assertEqual(len(result['catalog']['assets']), count+1)
        self.assertNotIn('audio_tracks', result['asset']['options'])

    def test_ungranted_forged_closed_and_raw_cross_project_reads_are_rejected(self):
        for grants in ((), ({'pin':self.pin},), (self.grant,self.grant)):
            with self.assertRaises(ValueError):
                self.call(grants=grants)
        with runtime_access(self.store, asset_sources=(self.grant,)):
            with self.assertRaisesRegex(ValueError,'different output/project'):
                self.f.f.assets().asset('donor',self.picture['id'])
        wrong = copy.deepcopy(self.pin)
        wrong['root']['sha256'] = 'a'*64
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input,asset_sources=(self.grant,)):
            with self.assertRaisesRegex(ValueError,'matching host'):
                self.f.f.assets().import_project_asset('demo','donor',self.picture['id'],source_pin=wrong,
                    storage_operation_id=uuid.uuid4().hex, ownership_proof=self.f.f.proof)
        self.grant_scope.__exit__(None,None,None)
        with self.assertRaisesRegex(ValueError,'closed'):
            self.call()
        self.unchanged_source()

    def test_source_snapshot_stays_historical_and_corrupt_media_is_not_hidden(self):
        self.legacy.update('donor', self.picture['id'], {'tag':'changed_later'})
        with state.control_rehearsal_access(self.source.project), runtime_access(self.source,asset_writes=True):
            ProjectAssetStore(self.input,self.source_output).refresh_backup('donor',operation_id=uuid.uuid4().hex,ownership_proof=self.proof)
        result, _ = self.call()
        self.assertEqual(result['asset']['tag'], 'source_hero')
        with state.control_rehearsal_access(self.source.project):
            media = self.source.payload_path(self.source.snapshot(), 'project_assets/'+self.picture['relative_path'], verify=True)
        media.write_bytes(b'broken copied source')  # Disposable source fixture only.
        raw = self.f.catalog.read_bytes()
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual(self.f.catalog.read_bytes(), raw)
        with runtime_access(self.store,asset_sources=(self.grant,)), self.assertRaises(ValueError):
            self.f.f.assets().projects(exclude_project='demo')

    def test_actual_import_http_route_propagates_independent_pin_and_retry_identity(self):
        namespace = routes(self.f.f.output)
        namespace['_project_asset_store'] = self.f.f.assets
        namespace['ProjectAssetConflictError'] = ProjectAssetConflictError
        path = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        nodes = [node for node in ast.parse(path.read_text()).body
                 if isinstance(node,ast.AsyncFunctionDef) and node.name=='_project_asset_import']
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
        pin = self.f.pin()
        body = dict(project='demo', source='project', source_project='donor',asset_id=self.mix['id'],
                    source_pin=self.pin,storage_operation_id=uuid.uuid4().hex)
        responses = []
        for _ in range(2):
            with runtime_access(self.store,pin=pin,asset_sources=(self.grant,),asset_writes=True,asset_input_root=self.input):
                response = asyncio.run(namespace['_project_asset_import'](request(body,self.f.f.proof)))
                self.assertEqual(response['status'],200,response)
                responses.append(response['body'])
        self.assertEqual(*responses)
        self.unchanged_source()

        # H3-backup imports keep their own requested options rather than the
        # live-catalog import's role/lyrics/audio-group inheritance semantics.
        body = dict(project='demo',source='chains',run_name='donor',asset_id=self.picture['id'],
            source_pin=self.pin,storage_operation_id=uuid.uuid4().hex,tag='backup_copy',role='picture')
        with runtime_access(self.store,asset_sources=(self.grant,),asset_writes=True,asset_input_root=self.input):
            response = asyncio.run(namespace['_project_asset_import'](request(body,self.f.f.proof)))
        self.assertEqual(response['status'],200,response)
        self.assertEqual(response['body']['asset']['tag'],'backup_copy')
        self.assertEqual(response['body']['asset']['role'],'picture')
        self.assertEqual(response['body']['asset']['source_kind'],'chains')
        self.assertEqual(response['body']['asset']['imported_from']['source_pin'],self.pin)

    def test_concurrent_reads_and_same_operation_imports_keep_separate_contexts(self):
        def browse():
            with runtime_access(self.store,asset_sources=(self.grant,)) as active:
                result = self.f.f.assets().projects(exclude_project='demo')
                self.assertIs(current_runtime(self.f.f.output),active)
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(copy_context().run,browse) for _ in range(2)]
            self.assertEqual(tasks[0].result(),tasks[1].result())
        op,pin = uuid.uuid4().hex,self.f.pin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(copy_context().run,self.call,operation=op,pin=pin) for _ in range(2)]
            self.assertEqual(tasks[0].result(),tasks[1].result())
        self.unchanged_source()

    def test_source_change_after_read_cannot_be_copied_under_the_old_fingerprint(self):
        original = self.grant.read
        def change(*args,**kwargs):
            result = original(*args,**kwargs)
            Path(result['media'][self.picture['id']]['path']).write_bytes(self.f.f.image.read_bytes()+b'changed')
            return result
        raw,root = self.f.catalog.read_bytes(),self.store.snapshot().reference
        with patch.object(self.grant,'read',side_effect=change), self.assertRaisesRegex(ValueError,'Source changed'):
            self.call()
        self.assertEqual(self.f.catalog.read_bytes(),raw)
        self.assertEqual(self.store.snapshot().reference,root)
        self.assertFalse(self.f.pending.exists())

    def test_read_grant_is_revoked_after_host_scope_and_never_grants_destination_writes(self):
        with runtime_access(self.store,asset_sources=(self.grant,)) as active:
            with self.assertRaisesRegex(ValueError,'read-only'):
                self.f.f.assets().import_project_asset('demo','donor',self.picture['id'],source_pin=self.pin,
                    storage_operation_id=uuid.uuid4().hex,ownership_proof=self.f.f.proof)
            with self.assertRaisesRegex(ValueError,'before the destination'):
                with asset_source_access(self.source,pin=self.pin):
                    pass
            self.grant_scope.__exit__(None,None,None)
            with self.assertRaisesRegex(ValueError,'closed'):
                self.f.f.assets().projects(exclude_project='demo')
        self.unchanged_source()


if __name__ == '__main__':
    unittest.main()
