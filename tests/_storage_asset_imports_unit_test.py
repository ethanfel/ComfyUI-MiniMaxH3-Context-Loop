"""Media imports bind input originals, accepted backups and catalog atomically."""
from pathlib import Path
from types import SimpleNamespace
import ast
import asyncio
import unittest
import uuid
from unittest.mock import patch

from PIL import Image
import _storage_asset_edits_unit_test as fixture
from storage_runtime import runtime_access
from storage_project_assets import CATALOG
from storage_project import payload_catalog
import storage_state as state
from _storage_branch_routes_unit_test import request


class AssetImportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssetEditTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store,self.input,self.catalog = self.f.store,self.f.input,self.f.catalog
        self.source = self.input/'new-image.png'
        Image.new('RGB',(32,48),(11,33,99)).save(self.source)
        self.original = self.source.read_bytes()

    def call(self, method='import_file', *args, **kwargs):
        return self.f.call(method,*(args or (self.source,)),**kwargs)

    def test_import_copies_new_media_and_keeps_ids_paths_catalog_and_source(self):
        before = self.store.snapshot()
        result, _ = self.call(tag='new_asset')
        self.f.assert_synced(result)
        entry = result['asset']
        self.assertEqual(entry['metadata']['width'],32)
        self.assertEqual(entry['metadata']['height'],48)
        self.assertEqual((self.catalog.parent/entry['relative_path']).read_bytes(),self.original)
        with runtime_access(self.store):
            record,path = self.f.f.assets().asset('demo',entry['id'])
            self.assertEqual(record,entry)
            self.assertEqual(Path(path).read_bytes(),self.original)
            self.assertNotEqual(Path(path).stat().st_ino,self.source.stat().st_ino)
        self.assertEqual(len(payload_catalog(self.store.snapshot())),len(payload_catalog(before))+1)
        self.assertEqual(self.source.read_bytes(),self.original)
        for key,value in before.state['documents'].items():
            if key != CATALOG:
                self.assertEqual(self.store.snapshot().state['documents'][key],value)

    def test_same_imported_path_reuses_owned_backup_without_replacing_its_descriptor(self):
        first,_ = self.call()
        before = payload_catalog(self.store.snapshot())
        second,_ = self.call()
        self.assertNotEqual(first['asset']['id'],second['asset']['id'])
        self.assertEqual(first['asset']['relative_path'],second['asset']['relative_path'])
        self.assertEqual(payload_catalog(self.store.snapshot()),before)
        self.f.assert_synced(second)

    def test_interrupted_import_finishes_without_original_upload_and_exactly_once(self):
        for phase in ('input_edit_prepared','input_edit_pending','input_media_published',
                      'input_edit_published','asset_payloads','document','root','input_edit_committed'):
            with self.subTest(phase=phase):
                # Distinct filename each iteration exercises new payload staging.
                source = self.input/(phase+'.png')
                source.write_bytes(self.original)
                operation,pin = uuid.uuid4().hex,self.f.pin()
                before = self.store.snapshot().reference
                def fault(point):
                    if point == phase:
                        raise RuntimeError('injected '+phase)
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    self.call('import_file',source,operation=operation,pin=pin,fault=fault)
                intent = state._decode((self.store.project/('project/jobs/asset-edit-'+operation+'.json')).read_bytes())['value']
                expected = intent['result']
                if phase != 'input_edit_committed':
                    self.assertEqual(self.store.snapshot().reference,before)
                source.unlink()  # Only disposable upload. Its complete staged backup is durable.
                first,_ = self.call('import_file',source,operation=operation,pin=pin)
                second,_ = self.call('import_file',source,operation=operation,pin=pin)
                self.assertEqual(first,expected)
                self.assertEqual(second,expected)
                self.f.assert_synced(first)
                self.assertEqual((self.catalog.parent/first['asset']['relative_path']).read_bytes(),self.original)

    def test_reference_binding_and_derived_lineage_publish_only_final_catalog(self):
        slots,_ = self.call('sync_reference_slots',[dict(tag='requested',kind='image')])
        slot = slots['reference_slots'][0]['id']
        bound,_ = self.call('bind_reference_slot',slot,self.source)
        self.assertEqual(bound['bound_slot_id'],slot)
        self.assertEqual(bound['catalog']['reference_slots'],[])
        parent = bound['asset']['id']
        before = self.store.snapshot().state['generation']
        result,_ = self.call('register_derived_image',parent,self.source,
            transform=dict(kind='crop_resize',target=dict(width=32,height=48)),operation_id='render-job-1')
        self.f.assert_synced(result)
        self.assertEqual(self.store.snapshot().state['generation'],before+1)
        self.assertEqual(result['asset']['parent_asset_id'],parent)
        self.assertEqual(result['asset']['transform']['operation_id'],'render-job-1')
        self.assertEqual(result['asset']['transform']['target'],dict(width=32,height=48))

    def test_new_input_destination_collision_is_not_overwritten(self):
        operation,pin = uuid.uuid4().hex,self.f.pin()
        before = self.store.snapshot().reference
        raw = self.catalog.read_bytes()
        def fault(point):
            if point == 'input_edit_prepared':
                raise RuntimeError('prepared')
        with self.assertRaisesRegex(RuntimeError,'prepared'):
            self.call(operation=operation,pin=pin,fault=fault)
        intent = state._decode((self.store.project/('project/jobs/asset-edit-'+operation+'.json')).read_bytes())['value']
        target = self.catalog.parent/intent['result']['asset']['relative_path']
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(b'unrelated existing file')
        with self.assertRaises(state.StateConflict):
            self.call(operation=operation,pin=pin)
        self.assertEqual(target.read_bytes(),b'unrelated existing file')
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(self.catalog.read_bytes(),raw)
        self.assertFalse(self.f.pending.exists())

    def test_lost_root_reply_reuses_staged_binary_receipt(self):
        operation,pin = uuid.uuid4().hex,self.f.pin()
        publish = self.store._publish
        def lost(*args,**kwargs):
            publish(*args,**kwargs)
            raise RuntimeError('lost reply')
        with patch.object(self.store,'_publish',side_effect=lost), self.assertRaisesRegex(RuntimeError,'lost reply'):
            self.call(operation=operation,pin=pin)
        before = self.store.snapshot().reference
        self.source.unlink()
        result,_ = self.call(operation=operation,pin=pin)
        self.assertEqual(self.store.snapshot().reference,before)
        self.f.assert_synced(result)

    def test_actual_pillow_derive_keeps_geometry_lineage_and_only_one_commit(self):
        parent = self.f.f.first['id']
        before = self.store.snapshot().state['generation']
        result,_ = self.call('derive_image',parent,crop={},target=dict(width=24,height=16),
            operation_id='pillow-job',tag='variant')
        self.assertEqual(self.store.snapshot().state['generation'],before+1)
        self.assertEqual(result['asset']['metadata']['width'],24)
        self.assertEqual(result['asset']['metadata']['height'],16)
        self.assertEqual(result['asset']['parent_asset_id'],parent)
        self.assertEqual(result['asset']['transform']['kind'],'crop_resize')
        self.f.assert_synced(result)
        self.assertEqual(list((self.store.project/'project/assets/renders').iterdir()),[])

    def test_pillow_prepared_retry_does_not_render_again(self):
        operation,pin = uuid.uuid4().hex,self.f.pin()
        def fault(point):
            if point == 'input_edit_prepared':
                raise RuntimeError('prepared')
        args = dict(crop={},target=dict(width=24,height=16),operation_id='pillow-retry')
        with self.assertRaisesRegex(RuntimeError,'prepared'):
            self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,fault=fault,**args)
        with patch('project_assets.ProjectAssetStore.prepare_image_crop',side_effect=AssertionError('rendered again')):
            result,_ = self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,**args)
        self.f.assert_synced(result)

    def test_actual_json_import_and_derive_routes_publish_the_final_asset(self):
        namespace = self.f.asset_routes()
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        names = {'_project_asset_import','_project_asset_derive'}
        nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        def invoke(name, **values):
            body = dict(project='demo',storage_operation_id=uuid.uuid4().hex,**values)
            pin = self.f.pin()
            for _ in range(2):
                with runtime_access(self.store,pin=pin,asset_writes=True,asset_input_root=self.input):
                    response = asyncio.run(namespace['_project_asset_'+name](request(body,self.f.f.proof)))
                self.assertEqual(response['status'],200,response)
                self.f.assert_synced(response['body'])
            return response['body']
        imported = invoke('import',source='input',path=self.source.name,tag='http_import')
        invoke('derive',asset_id=imported['asset']['id'],crop={},target=dict(width=16,height=24),operation_id='http-render')

    def test_upload_staging_has_separate_input_and_owner_grants(self):
        for enabled, root, proof in ((False,None,self.f.f.proof),(True,None,self.f.f.proof),(True,self.input,None)):
            with runtime_access(self.store,asset_writes=enabled,asset_input_root=root), self.assertRaises(ValueError):
                self.f.f.assets().upload_path('demo','image.png',ownership_proof=proof)
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input):
            path = Path(self.f.f.assets().upload_path('demo','../external.png',ownership_proof=self.f.f.proof))
            self.assertEqual(path.parent,self.input/'h3_projects/demo/.uploads')
            self.assertFalse(path.exists())
            with self.assertRaises(ValueError):
                self.f.f.assets().upload_path('demo','bad.exe',ownership_proof=self.f.f.proof)

    def test_early_derived_retry_reuses_media_despite_new_render_filename(self):
        for phase in ('input_edit_requested','input_media_staged'):
            with self.subTest(phase=phase):
                operation,pin = uuid.uuid4().hex,self.f.pin()
                before,raw = self.store.snapshot().reference,self.catalog.read_bytes()
                args = dict(crop={},target=dict(width=24 if phase == 'input_edit_requested' else 32,height=16),operation_id=phase)
                def fault(point):
                    if point == phase:
                        raise RuntimeError('interrupted')
                with self.assertRaisesRegex(RuntimeError,'interrupted'):
                    self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,fault=fault,**args)
                self.assertEqual((self.store.snapshot().reference,self.catalog.read_bytes()),(before,raw))
                with self.assertRaises((ValueError,OSError)):
                    self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,
                              **{**args,'target':dict(width=16,height=24)})
                result,_ = self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,**args)
                self.f.assert_synced(result)

    def test_reserved_but_unpublished_media_retry_uses_same_bytes(self):
        operation,pin = uuid.uuid4().hex,self.f.pin()
        args = dict(crop={},target=dict(width=24,height=16),operation_id='partial-copy')
        # Fail the real payload publication after its reservation, before any
        # accepted catalog or input media. derive_image removes its own render.
        with patch('storage_project.publish_new_file',side_effect=RuntimeError('before media publication')):
            with self.assertRaisesRegex(RuntimeError,'before media publication'):
                self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,**args)
        result,_ = self.call('derive_image',self.f.f.first['id'],operation=operation,pin=pin,**args)
        self.f.assert_synced(result)

    def test_changed_file_during_early_request_is_rejected_before_publication(self):
        operation,pin = uuid.uuid4().hex,self.f.pin()
        before,raw = self.store.snapshot().reference,self.catalog.read_bytes()
        def replace(point):
            if point == 'input_edit_requested':
                Image.new('RGB',(32,48),(99,0,0)).save(self.source)
        with self.assertRaisesRegex(ValueError,'Source changed'):
            self.call(operation=operation,pin=pin,fault=replace)
        with self.assertRaises((ValueError,OSError)):
            self.call(operation=operation,pin=pin)
        self.assertEqual((self.store.snapshot().reference,self.catalog.read_bytes()),(before,raw))
        self.source.write_bytes(self.original)
        result,_ = self.call(operation=operation,pin=pin)
        self.f.assert_synced(result)

    def upload_routes(self):
        namespace = self.f.asset_routes()
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        names = {'_project_asset_upload','_safe_unlink'}
        nodes = [n for n in ast.parse(source.read_text()).body
                 if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        return namespace

    def upload_request(self, operation, *, raw=None, proof=None, after_read=None, **fields):
        async def parts():
            for key,value in dict(project='demo',storage_operation_id=operation,**fields).items():
                async def text(value=value):
                    return value
                yield SimpleNamespace(name=key,text=text)
            chunks = [self.original if raw is None else raw,b'']
            async def read_chunk(size):
                if len(chunks) == 1 and after_read:
                    after_read()
                return chunks.pop(0)
            yield SimpleNamespace(name='file',filename='uploaded.png',read_chunk=read_chunk)
        async def multipart():
            return parts()
        req = request({},self.f.f.proof if proof is None else proof)
        req.multipart = multipart
        return req

    def test_actual_multipart_upload_retry_keeps_asset_and_cleans_only_uploads(self):
        namespace = self.upload_routes()
        for phase in ('input_media_staged','input_edit_prepared','input_edit_published'):
            with self.subTest(phase=phase):
                operation,pin = uuid.uuid4().hex,self.f.pin()
                def fault(point):
                    if point == phase:
                        raise RuntimeError('upload interrupted')
                with runtime_access(self.store,pin=pin,asset_writes=True,asset_input_root=self.input) as runtime:
                    runtime.assets.after_stage = fault
                    with self.assertRaisesRegex(RuntimeError,'upload interrupted'):
                        asyncio.run(namespace['_project_asset_upload'](self.upload_request(operation,tag=phase)))
                self.assertEqual(list((self.catalog.parent/'.uploads').iterdir()),[])
                previous = None
                for _ in range(2):
                    with runtime_access(self.store,pin=pin,asset_writes=True,asset_input_root=self.input):
                        response = asyncio.run(namespace['_project_asset_upload'](self.upload_request(operation,tag=phase)))
                    self.assertEqual(response['status'],200,response)
                    self.f.assert_synced(response['body'])
                    if previous is not None:
                        self.assertEqual(response,previous)
                    previous = response
                self.assertEqual(list((self.catalog.parent/'.uploads').iterdir()),[])
                with runtime_access(self.store,pin=pin,asset_writes=True,asset_input_root=self.input):
                    denied = asyncio.run(namespace['_project_asset_upload'](self.upload_request(operation,tag=phase,raw=b'changed')))
                self.assertEqual(denied['status'],400,denied)
                self.assertEqual(list((self.catalog.parent/'.uploads').iterdir()),[])

    def test_upload_owner_takeover_during_stream_never_imports_catalog(self):
        import project_ownership as ownership
        from _storage_branch_routes_unit_test import B
        namespace = self.upload_routes()
        before,raw = self.store.snapshot().reference,self.catalog.read_bytes()
        def takeover():
            ownership.claim_project_ownership(self.f.f.output,'demo',B,force=True)
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input,ownership_writes=True):
            response = asyncio.run(namespace['_project_asset_upload'](
                self.upload_request(uuid.uuid4().hex,after_read=takeover)))
        self.assertEqual(response['status'],423,response)
        self.assertEqual((self.store.snapshot().reference,self.catalog.read_bytes()),(before,raw))
        self.assertEqual(list((self.catalog.parent/'.uploads').iterdir()),[])


if __name__ == '__main__':
    unittest.main()
