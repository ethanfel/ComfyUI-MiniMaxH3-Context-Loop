"""Actual CPU saves after activating organized payload writers on tiny fixtures."""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _upscale_chain_unit_test import load_package, folder_paths, torch, av_latent, audio_for_frames

package, chain, upscale = load_package()
resolver = importlib.import_module(package.__name__+'.storage_resolver')
writes = importlib.import_module(package.__name__+'.storage_writes')
persistence = importlib.import_module(package.__name__+'.processing_persistence')


class OrganizedWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder_paths.output_directory=self.temp.name
        self.output=Path(self.temp.name)
        self.run='organized_writer_test'
        self.root=self.output/'h3_chains'/self.run
        self.root.mkdir(parents=True)
        aliases={'format':resolver.ALIASES,'files':{},'directories':{}}
        address='project/aliases/'+'a'*32+'.json'
        persistence.atomic_json(self.root/address,aliases)
        persistence.atomic_json(self.root/'storage.json',{'format':resolver.FORMAT,'version':1,
            'mode':'rehearsal','phase':'ready','aliases':address,
            'aliases_sha256':hashlib.sha256((self.root/address).read_bytes()).hexdigest(),
            'writer_policy':writes.POLICY,'writer_generation':0})
        self.scope=resolver.rehearsal_access(self.root)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__,None,None,None)
        self.plan=chain.MiniMaxH3ChainPlan().build(json.dumps({'shots':[
            {'id':'first','prompt':'Original precise prompt','length':5,'steps':2,'seed':'18446744073709551601'},
            {'id':'second','prompt':'Second prompt','length':5,'steps':2,'seed':'8'}]}),
            self.run,'unit-test',32,32,1,'video','head','disabled','generated_audio',
            1,5/24,2,7,18,0,'guide')[0]
        self.plan=chain._plan_with_source_audio(chain._plan_with_external_context(self.plan,None),None)
        self.frames=torch.zeros(5,32,32,3)

    def save(self,index=1,plan=None):
        plan=plan or self.plan
        audio=audio_for_frames(plan['shots'][index-1]['delivered_frames'])
        audio['waveform'].fill_(0.2)
        return chain.MiniMaxH3ChainSegmentSave().save(chain._initial_state(plan,index),
            self.frames[:plan['shots'][index-1]['delivered_frames']],av_latent(.1),audio,
            denoised_latent=av_latent(.3))['result'][0]

    def manifest(self,segments):
        selection={'run_name':self.run,'output_mode':'workflow_local','lineage':[
            {'scene':s['index'],'revision':s['revision']} for s in segments]}
        return chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps(selection))[0]

    def assert_payload(self,segment,stage):
        for key in ('segment','checkpoint','generated_audio'):
            path=Path(chain._absolute_output_path(segment[key]))
            self.assertTrue(path.is_file(),path)
            self.assertIn('/media/'+stage+'/',str(path))
            self.assertFalse((self.output/segment[key]).exists())
        self.assertIn('/project/takes/',chain._absolute_output_path(segment['prompt_file']))

    def test_generation_alt_deferred_pixel_latent_resume_and_final_export(self):
        originals=[self.save(1),self.save(2)]
        original_bytes={p:p.read_bytes() for s in originals for key in ('segment','checkpoint','revision_metadata')
                        for p in [Path(chain._absolute_output_path(s[key]))]}
        self.assert_payload(originals[0],'generation')
        self.assertEqual(originals[0]['seed'],18446744073709551601)
        alt_plan=chain._alternate_take_plan(self.plan,{'alternate_draft':{'enabled':True,'scene':1,
            'scene_id':'first','base_revision':originals[0]['revision'],
            'prompt':'Exact ALT prompt','seed':18446744073709551603}})
        alt=self.save(1,alt_plan)
        chain._select_editorial_alternate(alt_plan,alt)
        self.assert_payload(alt,'alternate')
        manifest=self.manifest(originals)
        for profile,backend,save_latent,stage,recipe in [('pixels','pixel',False,'pixel_upscale','{}'),
                ('latents','h3_latent',True,'latent_upscale','{}'),
                ('derope','h3_latent',True,'derope','{"derope":true}'),
                ('refine','ltx_2_5',False,'video_refine','{}'),
                ('custom','custom',False,'custom','{}')]:
            adapted=upscale.MiniMaxH3ChainUpscaleAdapter().adapt(manifest,profile,backend,recipe,1,0,save_latent,18)
            state=adapted[1]
            self.assertEqual(state['source_manifest']['segments'][0]['revision'],alt['revision'])
            self.assertEqual(state['source_manifest']['segments'][0]['seed'],18446744073709551603)
            saved=upscale.MiniMaxH3ChainUpscaleSegmentSave().save(state,self.frames,
                upscaled_latent=av_latent(.8) if save_latent else None)['result'][0]
            self.assert_payload(saved,stage)
            upscale._verify_upscale_segment(saved,1)
            resumed=upscale.MiniMaxH3ChainUpscaleAdapter().adapt(manifest,profile,backend,recipe,2,0,save_latent,18)[1]
            self.assertEqual(resumed['segments'][0]['revision'],saved['revision'])
            partial=upscale._upscale_manifest(state,[saved],complete=False)
            final=chain.MiniMaxH3ChainAssemble().assemble(partial,'none','organized',96)
            self.assertIn('/exports/video/',final['result'][0])
            self.assertTrue(Path(final['result'][0]).is_file())
            logical_video=chain._relative_output_path(final['result'][0])
            logical_stem=str(Path(logical_video).with_suffix(''))
            metadata=Path(chain._absolute_output_path(logical_stem+'.json'))
            self.assertEqual(metadata,Path(final['result'][0]).with_suffix('.json'))
            self.assertEqual(chain._relative_output_path(str(metadata)),logical_stem.replace('\\','/')+'.json')
            record=json.loads(metadata.read_text())
            self.assertEqual(record['video'],logical_video)
            audio=Path(chain._absolute_output_path(record['generated_audio']))
            self.assertEqual(audio.name,'audio.wav')
            self.assertEqual(record['generated_audio_sha256'],chain._file_sha256(str(audio)))
        graph=chain.CheckpointGraphManager(str(self.output)).graph(self.run,adopt_legacy=False)
        self.assertTrue(all(not row.get('broken') for row in graph['revisions']))
        self.assertEqual(original_bytes,{p:p.read_bytes() for p in original_bytes})

    def test_generation_metadata_lost_ack_retains_referenced_media(self):
        original=chain._atomic_json
        written=[]
        def uncertain(path,value):
            original(path,value)
            if value.get('format')=='h3_chain_segment_v3':
                written.append(value)
                raise OSError('simulated saved metadata acknowledgement lost')
        with patch.object(chain,'_atomic_json',uncertain),self.assertRaisesRegex(OSError,'acknowledgement'):
            self.save()
        self.assertEqual(len(written),1)
        segment=written[0]['segment']
        chain._verify_segment_artifacts(segment,1)
        for path in segment['archives'].values():
            self.assertTrue(Path(chain._absolute_output_path(path)).is_file())

    def test_media_flush_failure_cannot_publish_a_new_generation_pointer(self):
        original=self.save()
        pointer=self.output/original['metadata']
        before=pointer.read_bytes()
        with (patch.object(persistence,'sync_file',side_effect=OSError('simulated media flush error')),
              self.assertRaisesRegex(OSError,'media flush')):
            self.save()
        self.assertEqual(pointer.read_bytes(),before)
        chain._verify_segment_artifacts(original,1)

    def test_new_asset_backup_is_organized_and_recovery_reads_it(self):
        from PIL import Image
        assets=importlib.import_module(package.__name__+'.project_assets')
        image=self.output/'source.png'
        Image.new('RGB',(32,32),(10,20,30)).save(image)
        store=assets.ProjectAssetStore(str(self.output/'input'),str(self.output))
        result=store.import_file(self.run,str(image),role='picture',tag='test')
        _,path=store.backup_asset_path(self.run,result['asset']['id'])
        self.assertIn('/project/assets/',Path(path).as_posix())
        self.assertEqual(Path(path).read_bytes(),image.read_bytes())
        self.assertEqual(store.backups()[0]['run_name'],self.run)

    def test_complete_png_export_new_destination_and_incremental_lookup(self):
        first=self.save()
        manifest=self.manifest([first])
        manifest,_=chain._chapter_manifest_from_manifest(manifest,1)
        class VAE:
            def decode(self,latent):
                return torch.zeros(5,32,32,3)
        # The folder allocator and incremental discovery are used by the real
        # latent exporter; decoding quality is outside this CPU storage test.
        directory=chain._new_export_directory(manifest,'test')
        self.assertIn('/exports/png/',directory)
        self.assertTrue(Path(directory).is_dir())
        second=chain._new_export_directory(manifest,'test')
        self.assertNotEqual(directory,second)
        logical=resolver.logical_output(self.output,second)
        self.assertTrue(logical.endswith('/test_0002'))
        actual=chain.MiniMaxH3ChainExportPNG().export(manifest,VAE(),'actual',
            checkpoint_verification='strict',reuse_existing=True)
        self.assertIn('/exports/png/',actual['result'][0])
        repeated=chain.MiniMaxH3ChainExportPNG().export(manifest,VAE(),'actual',
            checkpoint_verification='strict',reuse_existing=True)
        self.assertEqual(repeated['result'][0],actual['result'][0])


if __name__=='__main__':
    unittest.main(argv=['organized-writer-tests'])
