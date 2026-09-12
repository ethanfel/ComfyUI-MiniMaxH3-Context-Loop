"""Real ffmpeg, HTTP worker threads and pinned copied-chain frame sources."""
import asyncio
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import uuid

from aiohttp import web
from PIL import Image
import _storage_asset_node_integration_test as fixture

chain,runtime = fixture.chain,fixture.runtime
assets_module,state = fixture.assets_module,fixture.state


class CaptureIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixture.AssetNodeTests.setUp(self)
        self.temp = self.g.lab/'temp'
        self.temp.mkdir()
        patch.object(fixture.fixture.folder_paths,'get_temp_directory',lambda: str(self.temp)).start()
        patch.object(chain,'web',web).start()
        self.video = self.g.output/'external.mkv'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'color=c=red:s=64x48:r=24:d=1','-c:v','ffv1',str(self.video)],check=True,timeout=20)
        with runtime.runtime_access(self.g.store) as bound:
            self.pin = bound.pin
        self.identity = uuid.uuid4().hex
        self.body = dict(project=self.g.run,filename=self.video.name,subfolder='',type='output',
                         time_seconds=.5,tag='frame',role='',folder_id=None,storage_operation_id=self.identity)

    def bind(self, **kwargs):
        return runtime.runtime_access(self.g.store,pin=self.pin,asset_writes=True,
            asset_input_root=self.input,**kwargs)

    def capture(self, **changes):
        body = dict(self.body,**changes)
        return chain._project_asset_capture_frame_sync(body['project'],body['filename'],body['subfolder'],
            body['type'],body['time_seconds'],body['tag'],body['role'],body['folder_id'],self.g.proof,
            storage_operation_id=body['storage_operation_id'])

    def test_real_ffmpeg_pixels_folder_family_names_and_source_provenance(self):
        with self.bind() as bound:
            store = assets_module.ProjectAssetStore(self.input,self.g.output)
            folder = store.create_folder(self.g.run,'Frames',storage_operation_id=uuid.uuid4().hex,
                ownership_proof=self.g.proof)['folder']['id']
            self.pin = bound.output_pin
        with self.bind() as bound:
            result = self.capture(folder_id=folder)
            accepted = bound.output_pin
        with self.bind():
            self.assertEqual(self.capture(folder_id=folder),result)
        self.pin = accepted
        with self.bind() as bound:
            second = self.capture(storage_operation_id=uuid.uuid4().hex,folder_id=folder)
        self.assertEqual((result['asset']['tag'],second['asset']['tag']),('frame','frame1'))
        self.assertEqual(result['asset']['folder_id'],folder)
        self.assertEqual(result['asset']['transform']['source']['view']['filename'],self.video.name)
        self.assertEqual(result['asset']['transform']['time_seconds'],.5)
        with runtime.runtime_access(self.g.store):
            _,path = assets_module.ProjectAssetStore(self.input,self.g.output).asset(self.g.run,result['asset']['id'])
            with Image.open(path) as image:
                self.assertEqual(image.size,(64,48))
                r,g,b = image.convert('RGB').getpixel((20,20))
                self.assertGreater(r,240)
                self.assertLess(g+b,10)

    def test_each_interruption_resumes_without_extracting_again(self):
        for phase in ('frame_capture_prepared','frame_capture_published','input_edit_prepared',
                      'input_media_published','input_edit_published','input_edit_committed'):
            with self.subTest(phase=phase):
                with runtime.runtime_access(self.g.store) as bound:
                    self.pin = bound.pin
                self.body['storage_operation_id'] = uuid.uuid4().hex
                with self.bind() as bound:
                    def fault(point):
                        if point==phase:
                            raise RuntimeError('injected '+phase)
                    bound.assets.after_stage = fault
                    with self.assertRaisesRegex(RuntimeError,'injected'):
                        self.capture()
                with patch.object(chain,'_capture_video_frame',side_effect=AssertionError('extracted twice')):
                    with self.bind():
                        result = self.capture()
                    accepted = self.g.store.snapshot().reference
                    with self.bind():
                        self.assertEqual(self.capture(),result)
                self.assertEqual(accepted,self.g.store.snapshot().reference)

    def test_saved_logical_and_physical_video_use_exact_pinned_payload(self):
        logical = self.g.originals[0]['segment']
        self.assertFalse((self.g.output/logical).exists())
        with self.bind() as bound:
            result = self.capture(filename=Path(logical).name,subfolder=str(Path(logical).parent),time_seconds=.04)
            source = result['asset']['transform']['source']
            self.assertTrue(source['indexed'])
            self.assertEqual(source['pin'],self.pin)
            physical = bound.store.payload_path(bound.base,bound.reader.address(logical),verify=True)
        with runtime.runtime_access(self.g.store) as bound:
            self.pin = bound.pin
        relative = physical.relative_to(self.g.output)
        with self.bind():
            again = self.capture(filename=relative.name,subfolder=str(relative.parent),time_seconds=.04,
                                 storage_operation_id=uuid.uuid4().hex)
        self.assertEqual(again['asset']['sha256'],result['asset']['sha256'])

    def test_input_project_asset_uses_accepted_backup_even_if_input_is_missing(self):
        with self.bind() as bound:
            store = assets_module.ProjectAssetStore(self.input,self.g.output)
            asset = store.import_file(self.g.run,self.video,role='video',tag='source_video',
                storage_operation_id=uuid.uuid4().hex,ownership_proof=self.g.proof)['asset']
            self.pin = bound.output_pin
        relative = Path('h3_projects')/self.g.run/asset['relative_path']
        (self.input/relative).unlink()  # Only this test's disposable input copy.
        with self.bind():
            # The source can be resolved from its pin, but publication must not
            # step past unrelated input/backup inconsistency.
            from_module = fixture.fixture.module('storage_asset_capture')
            source,indexed = from_module.source_path(runtime.current_runtime(self.g.output),
                dict(input=self.input,output=self.g.output,temp=self.temp),relative.name,str(relative.parent),'input')
            self.assertTrue(indexed)
            self.assertTrue(source.is_file())
            with self.assertRaises(FileNotFoundError):
                self.capture(filename=relative.name,subfolder=str(relative.parent),type='input')

    def test_external_source_may_disappear_after_durable_extraction_but_cannot_change(self):
        with self.bind() as bound:
            def fault(point):
                if point=='frame_capture_prepared':
                    raise RuntimeError('prepared')
            bound.assets.after_stage = fault
            with self.assertRaisesRegex(RuntimeError,'prepared'):
                self.capture()
        original = self.video.read_bytes()
        self.video.write_bytes(b'different source')
        with self.bind(), self.assertRaisesRegex(ValueError,'source bytes changed'):
            self.capture()
        self.video.unlink()
        with self.bind(), patch.object(chain,'_capture_video_frame',side_effect=AssertionError('extracted')):
            result = self.capture()
        self.assertEqual(result['asset']['transform']['source']['sha256'],state._hash(original))

    def test_ownership_takeover_or_source_change_during_extraction_blocks_publish(self):
        extract = chain._capture_video_frame
        before = (self.input/'h3_projects'/self.g.run/'catalog.json').read_bytes()
        def takeover(source,seconds,target):
            extract(source,seconds,target)
            fixture.fixture.ownership.claim_project_ownership(self.g.output,self.g.run,
                'capture-takeover-owner',force=True)
        with self.bind(ownership_writes=True), patch.object(chain,'_capture_video_frame',takeover):
            with self.assertRaises(fixture.fixture.ownership.ProjectOwnershipError):
                self.capture()
        self.assertEqual(before,(self.input/'h3_projects'/self.g.run/'catalog.json').read_bytes())
        self.assertFalse(list((self.g.store.project/'project/assets/renders').glob('*.png')))

    def test_actual_http_worker_thread_has_stable_retry_and_no_implicit_write_grant(self):
        body,proof = self.body,self.g.proof
        class Request:
            headers = {'X-H3-Workflow-Owner':proof['owner_id'],'X-H3-Ownership-Epoch':str(proof['epoch'])}
            async def json(self):
                return body
        with runtime.runtime_access(self.g.store):
            response = asyncio.run(chain._project_asset_capture_frame(Request()))
            self.assertEqual(response.status,400)
        replies = []
        for _ in range(2):
            with self.bind():
                response = asyncio.run(chain._project_asset_capture_frame(Request()))
                self.assertEqual(response.status,200,response.text)
                replies.append(json.loads(response.text))
        self.assertEqual(replies[0],replies[1])

    def test_invalid_paths_times_and_cross_project_sources_do_not_extract(self):
        cases = [dict(filename=''),dict(filename=str(self.video)),dict(filename='C:\\other.mp4'),
            dict(subfolder='../'),dict(filename='missing.m3u8'),dict(type='other'),
            dict(subfolder='h3_chains/another',filename='video.mp4'),
            dict(type='input',subfolder='h3_projects/another/videos',filename='video.mp4'),
            dict(time_seconds=float('nan')),dict(time_seconds=True),dict(time_seconds=-1)]
        with self.bind(), patch.object(chain,'_capture_video_frame') as extract:
            for params in cases:
                with self.subTest(params=params),self.assertRaises((ValueError,OSError)):
                    self.capture(storage_operation_id=uuid.uuid4().hex,**params)
            extract.assert_not_called()

    def test_changed_timestamp_tag_folder_and_source_cannot_reuse_request(self):
        with self.bind() as bound:
            def stop(point):
                if point=='frame_capture_requested':
                    raise RuntimeError('requested')
            bound.assets.after_stage = stop
            with self.assertRaisesRegex(RuntimeError,'requested'):
                self.capture()
        with self.bind(),patch.object(chain,'_capture_video_frame') as extract:
            for changes in (dict(time_seconds=.6),dict(tag='changed'),dict(folder_id='elsewhere'),
                            dict(filename='another.mkv'),dict(role='semantic_anchor')):
                with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'different inputs'):
                    self.capture(**changes)
            extract.assert_not_called()

    def test_lost_commit_reply_reuses_same_catalog_asset(self):
        publish = self.g.store._publish
        def lose(*args,**kwargs):
            publish(*args,**kwargs)
            raise RuntimeError('lost reply')
        with self.bind(),patch.object(self.g.store,'_publish',side_effect=lose):
            with self.assertRaisesRegex(RuntimeError,'lost reply'):
                self.capture()
        accepted = self.g.store.snapshot().reference
        with self.bind(),patch.object(chain,'_capture_video_frame',side_effect=AssertionError('extracted twice')):
            result = self.capture()
        self.assertEqual(accepted,self.g.store.snapshot().reference)
        self.assertEqual(len(result['catalog']['assets']),2)

    def test_damaged_capture_and_missing_indexed_source_never_fall_back(self):
        logical = self.g.originals[0]['segment']
        params = dict(filename=Path(logical).name,subfolder=str(Path(logical).parent),time_seconds=.04)
        with self.bind() as bound:
            source = bound.store.payload_path(bound.base,bound.reader.address(logical),verify=True)
            def stop(point):
                if point=='frame_capture_prepared':
                    raise RuntimeError('prepared')
            bound.assets.after_stage = stop
            with self.assertRaisesRegex(RuntimeError,'prepared'):
                self.capture(**params)
        original = source.read_bytes()
        source.unlink()
        with self.bind(),self.assertRaises(FileNotFoundError):
            self.capture(**params)
        source.write_bytes(original)  # Restore only the generated fixture payload.
        ready = self.g.store.project/('project/jobs/frame-'+self.identity+'.json')
        value = json.loads(ready.read_bytes())
        value['value']['size'] += 1
        ready.write_text(json.dumps(value))
        with self.bind(),self.assertRaisesRegex(ValueError,'receipt is damaged'):
            self.capture(**params)
        self.assertEqual(self.g.store.snapshot().reference,self.pin['root'])

    def test_source_change_or_failed_decode_does_not_publish_partial_image(self):
        extract = chain._capture_video_frame
        original = self.video.read_bytes()
        def changed(source,seconds,target):
            extract(source,seconds,target)
            self.video.write_bytes(b'changed during capture')
        with self.bind(),patch.object(chain,'_capture_video_frame',changed),self.assertRaisesRegex(ValueError,'source bytes changed'):
            self.capture()
        self.video.write_bytes(original)
        def failed(source,seconds,target):
            Path(target).write_bytes(b'partial png')
            raise RuntimeError('decode failed')
        with self.bind(),patch.object(chain,'_capture_video_frame',failed),self.assertRaisesRegex(RuntimeError,'decode failed'):
            self.capture()
        self.assertFalse(list((self.g.store.project/'project/assets/renders').glob('*.png')))
        self.assertEqual(self.g.store.snapshot().reference,self.pin['root'])


if __name__ == '__main__':
    unittest.main(argv=[__file__])
