"""Actual Pillow/ffmpeg cache generation and HTTP reads on isolated media."""
import ast
import asyncio
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
import uuid

from PIL import Image
import _storage_asset_edits_unit_test as fixture
from _storage_branch_routes_unit_test import request
from storage_runtime import runtime_access
from storage_project import payload_catalog
import storage_state as state


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AssetEditTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store,self.input = self.f.store,self.f.input
        self.asset = self.f.f.first['id']
        self.before,self.files = self.store.snapshot(),self.f.f.input_files()

    def call(self, method='ensure_thumbnail', *, asset=None, grant=True):
        with runtime_access(self.store,asset_previews=grant):
            return Path(getattr(self.f.f.assets(),method)('demo',asset or self.asset))

    def unchanged(self):
        self.assertEqual(self.store.snapshot().reference,self.before.reference)
        self.assertEqual(self.f.f.input_files(),self.files)
        self.assertEqual(payload_catalog(self.store.snapshot()),payload_catalog(self.before))

    def test_real_stills_share_content_cache_without_catalog_or_input_writes(self):
        thumb,poster = self.call(),self.call('ensure_poster')
        for path,group in ((thumb,'thumbnails'),(poster,'previews')):
            self.assertEqual(path.parent,self.store.project/'project/optional'/group)
            with Image.open(path) as image:
                self.assertEqual(image.format,'JPEG')
                self.assertEqual(image.size,(48,32))
        self.assertNotEqual(thumb,poster)
        self.assertEqual(self.call(grant=False),thumb)
        self.assertEqual(self.call(asset=self.f.f.clone['id']),thumb)
        self.assertEqual(self.call('ensure_browser_media',grant=False),
                         self.store.payload_path(self.before,'project_assets/'+self.f.f.first['relative_path']))
        self.assertEqual(self.call('ensure_browser_media',asset=self.f.f.sound['id'],grant=False),
                         self.store.payload_path(self.before,'project_assets/'+self.f.f.sound['relative_path']))
        self.unchanged()

    def test_cache_grant_is_separate_from_input_catalog_and_owner_authority(self):
        with self.assertRaisesRegex(ValueError,'preview-cache grant'):
            self.call(grant=False)
        with runtime_access(self.store,asset_writes=True,asset_input_root=self.input):
            with self.assertRaisesRegex(ValueError,'preview-cache grant'):
                self.f.f.assets().ensure_poster('demo',self.asset)
        with runtime_access(self.store,asset_previews=True):
            with self.assertRaisesRegex(ValueError,'read-only'):
                self.f.f.assets().update('demo',self.asset,{'tag':'no'},
                    storage_operation_id=uuid.uuid4().hex,ownership_proof=self.f.f.proof)
            self.f.f.assets().ensure_poster('demo',self.asset)  # No owner proof required for disposable pixels.
        self.unchanged()

    def test_corrupt_cache_rebuilds_only_with_grant_and_preserves_old_file(self):
        path = self.call()
        path.write_bytes(b'damaged preview')
        with self.assertRaisesRegex(ValueError,'preview-cache grant'):
            self.call(grant=False)
        repaired = self.call()
        self.assertNotEqual(repaired,path)
        self.assertEqual(path.read_bytes(),b'damaged preview')
        self.assertEqual(self.call(grant=False),repaired)
        self.unchanged()

    def test_unowned_or_mismatched_pointer_cannot_be_adopted(self):
        self.call()
        directory = self.store.project/'project/optional/thumbnails'
        reservation = next(directory.glob('*.request.json'))
        pointer = directory/(reservation.name.removesuffix('.request.json')+'.json')
        original = reservation.read_bytes()
        reservation.unlink()  # Simulate loss only on the test's disposable cache.
        with self.assertRaisesRegex(ValueError,'Unowned'):
            self.call()
        reservation.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'reservation is occupied'):
            self.call()
        reservation.write_bytes(original)
        pointer.write_bytes(b'broken json')
        repaired = self.call()
        self.assertEqual(self.call(grant=False),repaired)
        self.unchanged()

    def test_interrupted_pointer_publication_is_reproducible_and_does_not_delete_other_files(self):
        with patch('storage_asset_previews.atomic_json',side_effect=RuntimeError('lost pointer')):
            with self.assertRaisesRegex(RuntimeError,'lost pointer'):
                self.call()
        directory = self.store.project/'project/optional/thumbnails'
        orphan = list(directory.glob('*.thumb.jpg'))
        self.assertEqual(len(orphan),1)
        saved = orphan[0].read_bytes()
        regenerated = self.call()
        self.assertNotEqual(orphan[0],regenerated)
        self.assertEqual(orphan[0].read_bytes(),saved)
        self.assertFalse(list(directory.glob('r-*')))
        self.unchanged()

    def test_worker_threads_produce_one_verified_cache_and_closed_readers_cannot_escape(self):
        with runtime_access(self.store,asset_previews=True):
            assets = self.f.f.assets()
            async def work():
                return await asyncio.gather(*(asyncio.to_thread(assets.ensure_thumbnail,'demo',self.asset) for _ in range(4)))
            results = asyncio.run(work())
        self.assertEqual(len(set(results)),1)
        with self.assertRaisesRegex(ValueError,'current pinned runtime'):
            assets.ensure_thumbnail('demo',self.asset)
        self.unchanged()

    def test_actual_http_get_cannot_grant_cache_or_catalog_writes(self):
        namespace = self.f.asset_routes()
        namespace['web'].FileResponse = lambda path,headers:dict(path=Path(path),headers=headers)
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_project_asset_media']
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),namespace)
        query = dict(project='demo',asset=self.asset,variant='thumbnail',asset_previews=True,asset_writes=True)
        with runtime_access(self.store):
            denied = asyncio.run(namespace['_project_asset_media'](request(query,method='GET')))
        self.assertEqual(denied['status'],400)
        with runtime_access(self.store,asset_previews=True):
            allowed = asyncio.run(namespace['_project_asset_media'](request(query,method='GET')))
        self.assertEqual(allowed['path'],self.call(grant=False))
        self.assertEqual(allowed['headers']['X-H3-Asset-Kind'],'image')
        self.unchanged()

    def test_missing_source_never_uses_preview_as_asset_or_repairs_from_input(self):
        cached = self.call()
        source = self.store.payload_path(self.before,'project_assets/'+self.f.f.first['relative_path'])
        original = source.read_bytes()
        source.unlink()  # Damage only this isolated fixture's copied backup.
        try:
            with self.assertRaises((ValueError,OSError)):
                self.call()
            self.assertTrue(cached.is_file())
            self.assertFalse(source.exists())
            self.assertEqual(self.f.f.input_files(),self.files)
        finally:
            source.write_bytes(original)
        self.unchanged()

    def test_source_damage_during_render_never_publishes_pointer(self):
        source = self.store.payload_path(self.before,'project_assets/'+self.f.f.first['relative_path'])
        original,save = source.read_bytes(),Image.Image.save
        def corrupt(image,*args,**kwargs):
            result = save(image,*args,**kwargs)
            source.write_bytes(b'damaged while rendering')
            return result
        try:
            with patch.object(Image.Image,'save',corrupt),self.assertRaises((ValueError,OSError)):
                self.call()
            directory = self.store.project/'project/optional/thumbnails'
            self.assertFalse([p for p in directory.glob('*.json') if not p.name.endswith('.request.json')])
            self.assertFalse(list(directory.glob('r-*')))
        finally:
            source.write_bytes(original)
        self.unchanged()

    @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg not available')
    def test_real_video_poster_thumbnail_and_browser_transcode(self):
        video = self.input/'source.avi'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi',
            '-i','color=c=blue:s=64x48:r=5:d=0.4','-c:v','mpeg4',str(video)],check=True)
        result,_ = self.f.call('import_file',video)
        self.before,self.files = self.store.snapshot(),self.f.f.input_files()
        asset = result['asset']['id']
        for method in ('ensure_poster','ensure_thumbnail','ensure_browser_media'):
            path = self.call(method,asset=asset)
            self.assertEqual(self.call(method,asset=asset,grant=False),path)
            self.assertTrue(path.is_file())
            if method!='ensure_browser_media':
                with Image.open(path) as image:
                    self.assertEqual(image.size,(64,48))
            else:
                checked = subprocess.run(['ffprobe','-v','error','-select_streams','v:0',
                    '-show_entries','stream=codec_name,width,height','-of','csv=p=0',str(path)],
                    check=True,capture_output=True,text=True)
                self.assertEqual(checked.stdout.strip(),'h264,64,48')
        self.unchanged()


if __name__=='__main__':
    unittest.main()
