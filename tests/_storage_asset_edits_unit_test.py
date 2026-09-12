"""Real catalog editing methods: two-store publication and interrupted retries."""
import copy
import asyncio
import ast
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_project_assets_unit_test as fixture
from project_assets import ProjectAssetStore, ProjectAssetConflictError
from storage_runtime import runtime_access
from storage_project_assets import CATALOG, plan_asset_inputs
from storage_carriers import node_operation, node_host
from branch_scope import scoped_node, current_branch
from working_branches import WorkingBranches
import json
import storage_state as state
from _storage_handoff_routes_unit_test import routes
from _storage_branch_routes_unit_test import request


class AssetEditTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssetStorageTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.input, self.catalog = self.f.store, self.f.input, self.f.catalog
        self.f.refresh()
        self.pending = self.catalog.parent/'.h3-assets-pending.json'

    def pin(self):
        with runtime_access(self.store) as runtime:
            return runtime.pin

    def call(self, method, *args, pin=None, operation=None, fault=None, **kwargs):
        with runtime_access(self.store, pin=pin, asset_writes=True,
                            asset_input_root=self.input) as runtime:
            runtime.assets.after_stage = fault
            result = getattr(self.f.assets(), method)('demo', *args,
                storage_operation_id=operation or uuid.uuid4().hex,
                ownership_proof=self.f.proof, **kwargs)
            return result, runtime.output_pin

    def assert_synced(self, result):
        raw = self.catalog.read_bytes()
        self.assertEqual(self.store.snapshot().read(CATALOG), raw)
        self.assertEqual(state._decode(raw), result['catalog'])
        self.assertFalse(self.pending.exists())

    def test_all_eight_metadata_operations_preserve_media_and_other_controls(self):
        before = self.store.snapshot()
        files = self.f.input_files()
        result, _ = self.call('update', self.f.first['id'], {'tag':'renamed'})
        self.assert_synced(result)
        first, _ = self.call('create_folder', 'People', color='#123456')
        second, _ = self.call('create_folder', 'Other')
        folder = first['folder']['id']
        self.call('update_folder', folder, {'name':'Characters'})
        result, _ = self.call('reorder_folders', [second['folder']['id'], folder])
        self.assert_synced(result)
        clone, _ = self.call('duplicate', self.f.first['id'], folder_id=folder)
        order = [a['id'] for a in reversed(clone['catalog']['assets'])]
        result, _ = self.call('reorder', order)
        self.assertEqual([a['id'] for a in result['catalog']['assets']], order)
        result, _ = self.call('delete_folder', folder)
        self.assertEqual(result['assets_unfiled'],1)
        result, _ = self.call('sync_reference_slots', [dict(kind='image',tag='unresolved')])
        self.assertEqual(result['reference_slots'][0]['tag'],'unresolved')
        self.assert_synced(dict(catalog=result))
        for key, raw in files.items():
            if key != 'h3_projects/demo/catalog.json':
                self.assertEqual(self.f.input_files()[key],raw)
        after = self.store.snapshot()
        for key, descriptor in before.state['documents'].items():
            if key != CATALOG:
                self.assertEqual(after.state['documents'][key],descriptor)

    def test_delete_keeps_recovery_media_and_retries_after_commit(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        asset = self.f.sound
        path = self.catalog.parent/asset['relative_path']
        original = path.read_bytes()
        def interrupt(phase):
            if phase == 'input_edit_committed':
                raise OSError('test interrupted deletion acknowledgement')
        with self.assertRaisesRegex(OSError, 'interrupted deletion'):
            self.call('delete', asset['id'], pin=pin, operation=operation, fault=interrupt)
        self.assertEqual(path.read_bytes(), original)
        result, saved_pin = self.call('delete', asset['id'], pin=pin, operation=operation)
        self.assert_synced(result)
        self.assertFalse(path.exists())
        self.assertNotIn(asset['id'], [item['id'] for item in result['catalog']['assets']])
        self.assertEqual(self.store.payload_path(self.store.snapshot(),
            'project_assets/'+asset['relative_path'], verify=True).read_bytes(), original)
        again, again_pin = self.call('delete', asset['id'], pin=pin, operation=operation)
        self.assertEqual((again, again_pin), (result, saved_pin))

    def test_delete_shared_asset_does_not_remove_input_media(self):
        duplicated, _ = self.call('duplicate', self.f.first['id'])
        path = self.catalog.parent/self.f.first['relative_path']
        result, _ = self.call('delete', self.f.first['id'])
        self.assert_synced(result)
        self.assertTrue(path.is_file())

    def test_faults_keep_exact_duplicate_id_and_do_not_repeat_edits(self):
        for phase in ('input_edit_prepared','input_edit_pending','input_edit_published',
                      'asset_intent','asset_payloads','document','root','input_edit_committed'):
            with self.subTest(phase=phase):
                pin, operation = self.pin(), uuid.uuid4().hex
                before = self.store.snapshot()
                raw = self.catalog.read_bytes()
                def fault(point):
                    if point == phase:
                        raise RuntimeError('injected '+phase)
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    self.call('duplicate',self.f.first['id'],pin=pin,operation=operation,fault=fault)
                intent = state._decode((self.store.project/('project/jobs/asset-edit-'+operation+'.json')).read_bytes())['value']
                if phase != 'input_edit_committed':
                    self.assertEqual(self.store.snapshot().reference,before.reference)
                if phase in ('input_edit_prepared','input_edit_pending'):
                    self.assertEqual(self.catalog.read_bytes(),raw)
                result, accepted = self.call('duplicate',self.f.first['id'],pin=pin,operation=operation)
                self.assertEqual(result,intent['result'])
                self.assert_synced(result)
                same, retried = self.call('duplicate',self.f.first['id'],pin=pin,operation=operation)
                self.assertEqual((same,retried),(result,accepted))
                self.assertEqual(len(result['catalog']['assets']),len(state._decode(raw)['assets'])+1)

    def test_pending_edit_blocks_other_edit_and_independent_refresh(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        def fault(point):
            if point == 'input_edit_published':
                raise RuntimeError('stop')
        with self.assertRaises(RuntimeError):
            self.call('create_folder','Pending',pin=pin,operation=operation,fault=fault)
        before, raw = self.store.snapshot().reference, self.catalog.read_bytes()
        with self.assertRaisesRegex(state.StateConflict,'pending'):
            self.call('create_folder','Other')
        with self.assertRaisesRegex(state.StateConflict,'pending'):
            self.f.refresh()
        with self.assertRaisesRegex(ProjectAssetConflictError,'pending'):
            self.f.legacy.update('demo',self.f.first['id'],{'tag':'bypass'})
        with self.assertRaisesRegex(ProjectAssetConflictError,'pending'):
            self.f.legacy.load('demo')  # Must not auto-restore from a legacy mirror.
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(self.catalog.read_bytes(),raw)
        result, _ = self.call('create_folder','Pending',pin=pin,operation=operation)
        self.assert_synced(result)

    def test_lost_root_reply_retries_without_new_catalog_revision(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        publish = self.store._publish
        def lost(*args,**kwargs):
            publish(*args,**kwargs)
            raise RuntimeError('lost reply')
        with patch.object(self.store,'_publish',side_effect=lost), self.assertRaisesRegex(RuntimeError,'lost reply'):
            self.call('create_folder','One',pin=pin,operation=operation)
        before = self.store.snapshot().reference
        result, _ = self.call('create_folder','One',pin=pin,operation=operation)
        self.assertEqual(self.store.snapshot().reference,before)
        self.assert_synced(result)

    def test_stale_inputs_and_operation_reuse_never_overwrite(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        self.call('create_folder','One',pin=pin,operation=operation)
        self.call('create_folder','Two')
        before, raw = self.store.snapshot().reference, self.catalog.read_bytes()
        for name, op in (('One',operation),('Different',operation),('Old pin',uuid.uuid4().hex)):
            with self.assertRaises(ValueError):
                self.call('create_folder',name,pin=pin,operation=op)
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(self.catalog.read_bytes(),raw)
        self.assertFalse(self.pending.exists())

    def test_late_noop_retry_does_not_leave_a_blocking_marker(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        self.call('sync_reference_slots',[],pin=pin,operation=operation)
        # An independent same-catalog refresh advances project authority but
        # leaves identical input bytes. The old operation still must not write.
        self.f.refresh()
        with self.assertRaises(state.StateConflict):
            self.call('sync_reference_slots',[],pin=pin,operation=operation)
        self.assertFalse(self.pending.exists())

    def test_unrelated_branch_edit_does_not_block_exact_retry(self):
        pin, operation = self.pin(), uuid.uuid4().hex
        first, _ = self.call('create_folder','One',pin=pin,operation=operation)
        self.f.f.publish()
        result, _ = self.call('create_folder','One',pin=pin,operation=operation)
        self.assertEqual(first,result)
        self.assert_synced(result)

    def test_legacy_catalog_without_optional_fields_retains_edit_behavior(self):
        catalog = state._decode(self.catalog.read_bytes())
        for key in ('storage_revision','reference_slots','folders'):
            catalog.pop(key,None)
        self.catalog.write_bytes(state._encode(catalog))
        self.f.refresh()
        expected = ProjectAssetStore._normalize_catalog(copy.deepcopy(catalog),'demo')
        result, _ = self.call('sync_reference_slots',[])
        self.assertEqual(result,expected)
        self.assert_synced(dict(catalog=result))

    def test_new_empty_catalog_sync_uses_the_normal_creation_contract(self):
        fresh = fixture.AssetStorageTests()
        fresh.setUp()
        self.addCleanup(fresh.doCleanups)
        fresh.catalog.unlink()  # Only this new disposable fixture's input catalog.
        with runtime_access(fresh.store,asset_writes=True,asset_input_root=fresh.input):
            result = fresh.assets().sync_reference_slots('demo',[],
                storage_operation_id=uuid.uuid4().hex,ownership_proof=fresh.proof)
        self.assertRegex(result['storage_revision'],r'^[0-9a-f]{32}$')
        self.assertEqual(result['assets'],[])
        self.assertEqual(state._decode(fresh.store.snapshot().read(CATALOG)),result)
        self.assertEqual(fresh.store.snapshot().read(CATALOG),fresh.catalog.read_bytes())

    def test_input_catalog_drift_and_missing_media_are_rejected_before_publication(self):
        raw = self.catalog.read_bytes()
        self.catalog.write_bytes(b'broken')
        before = self.store.snapshot().reference
        with self.assertRaises(ValueError):
            self.call('create_folder','No')
        self.assertEqual(self.catalog.read_bytes(),b'broken')
        self.catalog.write_bytes(raw)
        media = self.catalog.parent/self.f.first['relative_path']
        media.unlink()  # Only a disposable test input; immutable backup remains.
        with self.assertRaises(OSError):
            self.call('create_folder','No')
        self.assertEqual(self.catalog.read_bytes(),raw)
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertFalse(self.pending.exists())

    def test_write_grant_input_root_owner_and_nested_node_fences(self):
        raw, before = self.catalog.read_bytes(), self.store.snapshot().reference
        for grant, root, proof in ((False,None,self.f.proof),(True,None,self.f.proof),
                                  (True,self.input,None),(True,self.input,dict(owner_id='wrong',epoch=1))):
            with runtime_access(self.store,asset_writes=grant,asset_input_root=root), self.assertRaises(ValueError):
                self.f.assets().create_folder('demo','No',storage_operation_id=uuid.uuid4().hex,ownership_proof=proof)
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input):
            with node_operation({},None), self.assertRaisesRegex(ValueError,'read-only'):
                self.f.assets().create_folder('demo','No',storage_operation_id=uuid.uuid4().hex,ownership_proof=self.f.proof)
        self.assertEqual(self.catalog.read_bytes(),raw)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_invalid_domain_operations_do_not_publish_or_leave_pending(self):
        raw, before = self.catalog.read_bytes(), self.store.snapshot().reference
        for method, args in (('update',(self.f.first['id'],{'tag':self.f.clone['tag']})),
                             ('reorder',([] ,)),('delete_folder',('missing',)),
                             ('sync_reference_slots',([dict(kind='bad')],))):
            with self.assertRaises((ValueError,OSError)):
                self.call(method,*args)
        self.assertEqual(self.catalog.read_bytes(),raw)
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertFalse(self.pending.exists())

    def asset_routes(self):
        namespace = routes(self.f.output)
        namespace['_project_asset_store'] = self.f.assets
        namespace['ProjectAssetConflictError'] = ProjectAssetConflictError
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        names = {'_project_asset_update','_project_asset_duplicate','_project_asset_folder','_project_asset_reorder'}
        nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        return namespace

    def test_actual_http_edit_routes_thread_owner_and_exact_identity(self):
        namespace = self.asset_routes()
        def invoke(route, **values):
            body = dict(project='demo',storage_operation_id=uuid.uuid4().hex,**values)
            pin = self.pin()
            for _ in range(2):
                with runtime_access(self.store,pin=pin,asset_writes=True,asset_input_root=self.input):
                    response = asyncio.run(namespace['_project_asset_'+route](request(body,self.f.proof)))
                self.assertEqual(response['status'],200,response)
                self.assert_synced(response['body'])
            return response['body']
        invoke('update',asset_id=self.f.first['id'],changes={'tag':'http'})
        clone = invoke('duplicate',asset_id=self.f.first['id'])
        folder = invoke('folder',action='create',name='HTTP')['folder']['id']
        invoke('folder',action='update',folder_id=folder,changes={'name':'Renamed'})
        invoke('folder',action='reorder',folder_ids=[folder])
        invoke('folder',action='delete',folder_id=folder)
        invoke('reorder',asset_ids=[a['id'] for a in reversed(clone['catalog']['assets'])])

    def test_http_body_cannot_supply_input_authority_or_ownership(self):
        namespace = self.asset_routes()
        body = dict(project='demo',action='create',name='Forbidden',asset_writes=True,
                    asset_input_root=str(self.input),ownership_proof=self.f.proof,
                    storage_operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            denied = asyncio.run(namespace['_project_asset_folder'](request(body,self.f.proof)))
        self.assertEqual(denied['status'],400,denied)
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input):
            denied = asyncio.run(namespace['_project_asset_folder'](request(body)))
        self.assertEqual(denied['status'],423,denied)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_shared_catalog_can_feed_named_plan_without_changing_root_or_inputs(self):
        @scoped_node
        def plan(run_name='ignored',project_assets=None,plan_json='{}'):
            return (dict(run_name='demo',saved=WorkingBranches(self.f.output,'demo').load(current_branch('demo'))),)
        with runtime_access(self.store) as bound:
            record = dict(format='h3_project_assets_v1',project='demo',catalog=self.f.assets().public_catalog('demo'),
                          _branch_id='main',_storage_pin=bound.pin)
        original = copy.deepcopy(record)
        authored = json.dumps(dict(_branch_id=self.f.f.named))
        with node_host(self.store,input_adapters={plan:plan_asset_inputs}):
            result = plan(project_assets=record,plan_json=authored)[0]
        self.assertEqual(result['_storage_pin']['root'],record['_storage_pin']['root'])
        self.assertEqual(result['_branch_id'],self.f.f.named)
        self.assertEqual(record,original)
        changed = copy.deepcopy(record)
        changed['catalog']['assets'][0]['tag'] = 'not-saved'
        with node_host(self.store,input_adapters={plan:plan_asset_inputs}), self.assertRaises(ValueError):
            plan(project_assets=changed,plan_json=authored)
        with node_host(self.store), self.assertRaises(ValueError):
            plan(project_assets=record,plan_json=authored)  # The JSON grants no adapter access.


if __name__ == '__main__':
    unittest.main()
