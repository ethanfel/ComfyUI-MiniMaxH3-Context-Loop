"""Real Carousel build on imported CPU-generated chain + independent input media."""
import json
import unittest
from unittest.mock import patch
import uuid

from PIL import Image
import _storage_generation_integration_test as fixture

chain, runtime, carriers = fixture.chain, fixture.runtime, fixture.carriers
assets_module = fixture.module('project_assets')
state = fixture.state


class AssetNodeTests(unittest.TestCase):
    def setUp(self):
        self.g = fixture.GenerationIntegrationTests()
        self.g.setUp()
        self.addCleanup(self.g.doCleanups)
        self.input = self.g.lab/'input'
        self.input.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(fixture.folder_paths,'input_directory',str(self.input)).start()
        picture = self.input/'hero.png'
        Image.new('RGB',(32,32),(40,80,120)).save(picture)
        legacy = assets_module.ProjectAssetStore(self.input,self.g.lab/'legacy-asset-output')
        self.hero = legacy.import_file(self.g.run,picture,tag='hero')['asset']
        with runtime.runtime_access(self.g.store,asset_writes=True):
            assets_module.ProjectAssetStore(self.input,self.g.output).refresh_backup(
                self.g.run,operation_id=uuid.uuid4().hex,ownership_proof=self.g.proof)
        self.node = chain.MiniMaxH3ProjectAssetManager()
        self.templates = chain._append_tagged_reference(chain._make_tagged_references([]),
            kind='picture',tag='unbound',value=fixture.torch.zeros(1,32,32,3),content_hash='a'*64)

    def test_real_build_syncs_templates_and_stamps_accepted_catalog_without_decoding(self):
        before = self.g.store.snapshot()
        with carriers.node_host(self.g.store,asset_writers=(self.node.build,),
                                asset_input_root=self.input,operation_namespace=uuid.uuid4().hex):
            output = self.node.build(self.g.run,tagged_references=self.templates,
                ownership_json=json.dumps(self.g.proof),unique_id='carousel-1')
        record, references, _, _, status = output
        saved = self.g.store.snapshot()
        catalog = state._decode(saved.read('project_assets/catalog.json'))
        self.assertEqual(record['catalog'],catalog)
        self.assertEqual(record['_storage_pin']['root'],saved.reference)
        self.assertEqual(record['_branch_id'],'main')
        self.assertNotEqual(before.reference,saved.reference)
        self.assertEqual(catalog['reference_slots'][0]['tag'],'unbound')
        self.assertIn('1 unassigned',status)
        self.assertEqual(len(catalog['assets']),1)
        self.assertEqual(self.node.INPUT_TYPES()['hidden']['unique_id'],'UNIQUE_ID')
        with carriers.node_host(self.g.store):
            planned = chain.MiniMaxH3ChainPlan().build(json.dumps({'shots':[
                {'id':'first','prompt':'@hero Exact prompt','length':5,'steps':2,'seed':'17'}]}),
                'ignored', '', 32, 32, 1, 'video', 'head', 'disabled', 'generated_audio',
                1, 5/24, 2, 7, 18, 0, 'guide', project_assets=record)[0]
        self.assertEqual(planned['_storage_pin']['root'],saved.reference)
        self.assertEqual(planned['run_name'],self.g.run)
        # Source generation prompts, plans and every take remain unchanged.
        for address, descriptor in before.state['documents'].items():
            if address != 'project_assets/catalog.json':
                self.assertEqual(saved.state['documents'][address],descriptor)

    def test_exact_host_pinned_retry_keeps_template_identity(self):
        with runtime.runtime_access(self.g.store) as bound:
            incoming = json.dumps(dict(_storage_pin=bound.pin))
        namespace = uuid.uuid4().hex
        outputs = []
        for _ in range(2):
            with carriers.node_host(self.g.store,asset_writers=(self.node.build,),
                                    asset_input_root=self.input,operation_namespace=namespace):
                outputs.append(self.node.build(self.g.run,catalog_json=incoming,
                    tagged_references=self.templates,ownership_json=json.dumps(self.g.proof),unique_id='carousel-1')[0])
        self.assertEqual(outputs[0],outputs[1])

    def test_read_only_and_foreign_owner_build_do_not_mutate(self):
        before = self.g.store.snapshot().reference
        with carriers.node_host(self.g.store):
            record = self.node.build(self.g.run)[0]
            self.assertEqual(record['_storage_pin']['root'],before)
            with self.assertRaisesRegex(ValueError,'read-only'):
                self.node.build(self.g.run,tagged_references=self.templates,
                    ownership_json=json.dumps(self.g.proof),unique_id='ungranted')
        with carriers.node_host(self.g.store,asset_writers=(self.node.build,),
                                asset_input_root=self.input,operation_namespace=uuid.uuid4().hex):
            record = self.node.build(self.g.run,tagged_references=self.templates,
                ownership_json=json.dumps(dict(owner_id='wrong-owner',epoch=1)),unique_id='read-only')[0]
            self.assertEqual(record['catalog']['reference_slots'],[])
        self.assertEqual(self.g.store.snapshot().reference,before)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
