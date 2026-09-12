"""Actual Carousel + model pixel helper on independently imported CPU chains."""
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import uuid
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import _storage_asset_node_integration_test as fixture

chain, runtime, carriers = fixture.chain, fixture.runtime, fixture.carriers
assets_module, state = fixture.assets_module, fixture.state
torch = fixture.fixture.torch


class PixelModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.register_buffer('gain',torch.tensor(1.0))

    def forward(self, pixels):
        return torch.nn.functional.interpolate(pixels*self.gain,scale_factor=2,mode='nearest')


class Descriptor:
    scale = 2.0
    def __init__(self):
        self.model = PixelModel().eval()
        self.patcher = types.SimpleNamespace(load_device=torch.device('cpu'),patches={})
        self.calls = 0
        self.on_forward = None

    def __call__(self, pixels):
        self.calls += 1
        if self.on_forward:
            self.on_forward()
        return self.model(pixels)


class ModelAssetTests(unittest.TestCase):
    def setUp(self):
        fixture.AssetNodeTests.setUp(self)
        import comfy.model_management as management
        import comfy.utils as utils
        patch.object(management,'load_models_gpu',lambda *a,**k: None).start()
        patch.object(management,'intermediate_device',lambda: torch.device('cpu')).start()
        patch.object(utils,'tiled_scale',lambda tensor,fn,**kwargs: fn(tensor)).start()
        self.model = Descriptor()
        self.operation = dict(mode='model',project=self.g.run,operation_id='carousel-model-1',
            asset_id=self.hero['id'],tag='hero_model',crop=dict(x=2,y=3,width=20,height=16),
            target=dict(width=48,height=32))
        self.identity = uuid.uuid4().hex
        with runtime.runtime_access(self.g.store) as bound:
            self.pin = bound.pin
        self.incoming = json.dumps(dict(_storage_pin=self.pin))

    def bind(self, **kwargs):
        return runtime.runtime_access(self.g.store,pin=self.pin,asset_writes=True,
                                       asset_input_root=self.input,**kwargs)

    def execute(self, bound, model=None, operation=None):
        return chain._execute_project_asset_model_operation(
            assets_module.ProjectAssetStore(self.input,self.g.output),self.g.run,
            operation or self.operation,model,self.g.proof,storage_operation_id=self.identity,
            reference_templates=chain._project_asset_reference_templates(self.templates))

    def test_actual_carousel_compiles_new_asset_and_slots_from_one_commit_and_exact_retry(self):
        before = self.g.store.snapshot()
        namespace = uuid.uuid4().hex
        records = []
        for model in (self.model,None):
            with carriers.node_host(self.g.store,asset_writers=(self.node.build,),
                    asset_input_root=self.input,operation_namespace=namespace):
                records.append(self.node.build(self.g.run,catalog_json=self.incoming,
                    operation_json=json.dumps(self.operation),upscale_model=model,
                    tagged_references=self.templates,ownership_json=json.dumps(self.g.proof),
                    unique_id='carousel-1')[0])
        saved = self.g.store.snapshot()
        self.assertEqual(records[0],records[1])
        self.assertEqual(len(saved.state['operations'])-len(before.state['operations']),1)
        record = records[0]
        self.assertEqual(record['_storage_pin']['root'],saved.reference)
        self.assertEqual(record['catalog'],state._decode(saved.read('project_assets/catalog.json')))
        self.assertEqual(len(record['catalog']['assets']),2)
        self.assertEqual(record['catalog']['reference_slots'][0]['tag'],'unbound')
        self.assertEqual(self.model.calls,1)
        with runtime.runtime_access(self.g.store):
            store = assets_module.ProjectAssetStore(self.input,self.g.output)
            asset,path = store.asset(self.g.run,record['catalog']['assets'][-1]['id'])
            with Image.open(path) as opened:
                self.assertEqual(opened.size,(48,32))
            self.assertEqual(asset['parent_asset_id'],self.hero['id'])
        for address,descriptor in before.state['documents'].items():
            if address != 'project_assets/catalog.json':
                self.assertEqual(saved.state['documents'][address],descriptor)

    def test_each_publication_fault_resumes_without_another_model_forward(self):
        for stage in ('model_render_prepared','model_render_published','input_edit_prepared',
                      'input_media_published','input_edit_published','input_edit_committed'):
            with self.subTest(stage=stage):
                # Each case starts from the current committed root and adds its
                # own asset/operation, exercising all exact retry boundaries.
                with runtime.runtime_access(self.g.store) as bound:
                    self.pin = bound.pin
                self.identity = uuid.uuid4().hex
                self.operation.update(operation_id=stage,tag=stage)
                count = self.model.calls
                with self.bind() as bound:
                    def crash(current):
                        if current == stage:
                            raise RuntimeError('injected '+stage)
                    bound.assets.after_stage = crash
                    with self.assertRaisesRegex(RuntimeError,'injected'):
                        self.execute(bound,self.model)
                with self.bind() as bound:
                    result = self.execute(bound)
                accepted = self.g.store.snapshot().reference
                with self.bind() as bound:
                    again = self.execute(bound)
                self.assertEqual(result,again)
                self.assertEqual(accepted,self.g.store.snapshot().reference)
                self.assertEqual(self.model.calls,count+1)

    def test_changed_request_weights_or_templates_cannot_reuse_reservation(self):
        with self.bind() as bound:
            bound.assets.after_stage = lambda stage: (_ for _ in ()).throw(RuntimeError('stop')) if stage=='model_render_requested' else None
            with self.assertRaisesRegex(RuntimeError,'stop'):
                self.execute(bound,self.model)
        self.assertEqual(self.model.calls,0)
        with self.bind() as bound:
            with self.assertRaisesRegex(ValueError,'different inputs'):
                self.execute(bound,self.model,dict(self.operation,tag='changed'))
            self.model.model.gain.fill_(.5)
            with self.assertRaisesRegex(ValueError,'different weights'):
                self.execute(bound,self.model)
            self.model.model.gain.fill_(1)
            with self.assertRaisesRegex(ValueError,'not finished'):
                self.execute(bound)
            self.execute(bound,self.model)
        self.assertEqual(self.model.calls,1)

    def test_readonly_binding_cannot_stage_or_render(self):
        before = self.g.store.snapshot().reference
        with runtime.runtime_access(self.g.store) as bound:
            with self.assertRaisesRegex(ValueError,'read-only'):
                self.execute(bound,self.model)
        self.assertEqual(self.model.calls,0)
        self.assertEqual(before,self.g.store.snapshot().reference)
        self.assertFalse((self.g.store.project/('project/jobs/asset-model-'+self.identity+'.json')).exists())

    def test_successor_reader_is_exact_readonly_and_expires(self):
        with self.bind() as bound:
            old_store = assets_module.ProjectAssetStore(self.input,self.g.output)
            result = self.execute(bound,self.model)
            self.assertEqual(len(old_store.public_catalog(self.g.run)['assets']),1)
            with runtime.accepted_asset_read_access(bound) as child:
                store = assets_module.ProjectAssetStore(self.input,self.g.output)
                self.assertEqual(len(store.public_catalog(self.g.run)['assets']),2)
                with self.assertRaisesRegex(ValueError,'read-only'):
                    store.update(self.g.run,result['asset']['id'],dict(tag='bad'),
                        storage_operation_id=uuid.uuid4().hex,ownership_proof=self.g.proof)
                with self.assertRaisesRegex(ValueError,'operation-local'):
                    old_store.public_catalog(self.g.run)
            with self.assertRaisesRegex(ValueError,'operation-local'):
                child.check()

    def test_ownership_takeover_during_forward_blocks_publication(self):
        before = (self.input/'h3_projects'/self.g.run/'catalog.json').read_bytes()
        self.model.on_forward = lambda: fixture.fixture.ownership.claim_project_ownership(
            self.g.output,self.g.run,'model-takeover-owner',force=True)
        with self.bind(ownership_writes=True) as bound:
            with self.assertRaises(fixture.fixture.ownership.ProjectOwnershipError):
                self.execute(bound,self.model)
        self.assertEqual(before,(self.input/'h3_projects'/self.g.run/'catalog.json').read_bytes())
        self.assertFalse((self.g.store.project/('project/jobs/asset-render-'+self.identity+'.json')).exists())

    def test_source_corruption_during_forward_is_not_published(self):
        with self.bind() as bound:
            store = assets_module.ProjectAssetStore(self.input,self.g.output)
            _,path = store.asset(self.g.run,self.hero['id'])
            original = Path(path).read_bytes()
            self.model.on_forward = lambda: Path(path).write_bytes(b'corrupted test backup')
            with self.assertRaises(ValueError):
                self.execute(bound,self.model)
            Path(path).write_bytes(original)  # Repair only this isolated fixture.
        self.assertEqual(self.g.store.snapshot().reference,self.pin['root'])
        self.assertFalse((self.g.store.project/('project/jobs/asset-render-'+self.identity+'.json')).exists())

    def test_lost_root_reply_and_concurrent_retry_do_not_render_twice(self):
        publish = self.g.store._publish
        def lost(*args,**kwargs):
            publish(*args,**kwargs)
            raise RuntimeError('lost reply')
        with self.bind() as bound, patch.object(self.g.store,'_publish',side_effect=lost):
            with self.assertRaisesRegex(RuntimeError,'lost reply'):
                self.execute(bound,self.model)
        accepted = self.g.store.snapshot().reference
        # New thread contexts require the same explicit copy-access gate.
        def retry(_):
            with state.control_rehearsal_access(self.g.store.project), self.bind() as bound:
                return self.execute(bound)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(retry,range(3)))
        self.assertTrue(all(item == results[0] for item in results))
        self.assertEqual(self.model.calls,1)
        self.assertEqual(self.g.store.snapshot().reference,accepted)

    def test_corrupted_ready_receipt_or_render_is_retained_and_rejected(self):
        with self.bind() as bound:
            bound.assets.after_stage = lambda stage: (_ for _ in ()).throw(RuntimeError('stop')) if stage=='model_render_published' else None
            with self.assertRaisesRegex(RuntimeError,'stop'):
                self.execute(bound,self.model)
        receipt = self.g.store.project/('project/jobs/asset-render-'+self.identity+'.json')
        raw = receipt.read_bytes()
        modified = json.loads(raw)
        modified['value']['transform']['model_scale'] = 999
        receipt.write_text(json.dumps(modified))
        with self.bind() as bound, self.assertRaisesRegex(ValueError,'receipt is invalid'):
            self.execute(bound)
        receipt.write_bytes(raw)
        target = self.g.store.project/('project/assets/renders/'+self.identity+'.png')
        target.write_bytes(b'not the model render')
        with self.bind() as bound, self.assertRaisesRegex(ValueError,'occupied'):
            self.execute(bound)
        self.assertEqual(target.read_bytes(),b'not the model render')
        self.assertEqual(self.model.calls,1)
        self.assertEqual(self.g.store.snapshot().reference,self.pin['root'])

    def test_actual_alpha_crop_geometry_and_reference_descriptor_match_saved_pixels(self):
        source = self.input/'alpha.png'
        Image.new('RGBA',(32,32),(10,50,100,80)).save(source)
        with self.bind() as bound:
            store = assets_module.ProjectAssetStore(self.input,self.g.output)
            parent = store.import_file(self.g.run,source,tag='alpha',
                storage_operation_id=uuid.uuid4().hex,ownership_proof=self.g.proof)['asset']
            self.pin = bound.output_pin
        self.operation['asset_id'] = parent['id']
        with self.bind() as bound:
            result = self.execute(bound,self.model)
            with runtime.accepted_asset_read_access(bound):
                store = assets_module.ProjectAssetStore(self.input,self.g.output)
                _,path = store.asset(self.g.run,result['asset']['id'])
                with Image.open(path) as picture:
                    self.assertEqual(picture.mode,'RGBA')
                    self.assertEqual(picture.size,(48,32))
                    self.assertEqual(picture.getchannel('A').getextrema(),(80,80))
                compiled = self.node._compile_catalog(store,store.public_catalog(self.g.run),self.g.run,
                    '512','timestamped_video',None,result['asset']['id'])
                # The lazy reference output contains the committed backup path,
                # not the pending model staging or mutable input original.
                self.assertIn(str(path),repr(compiled[1]))


if __name__ == '__main__':
    unittest.main(argv=[__file__])
