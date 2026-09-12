"""Ordinary package nodes discover an activated project without a test host."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch

import _storage_generation_integration_test as fixture

host = fixture.module('storage_host')
state = fixture.state
chain = fixture.chain


class HostTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.GenerationIntegrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        migrate = fixture.module('storage_migrate')
        fixture.ownership.claim_project_ownership(
            self.f.source.parent.parent, self.f.run, 'cpu-generation-test-owner')
        self.workspace = self.f.lab/'migration'
        migrate.prepare(self.f.source, self.workspace)
        migrate.copy_project(self.workspace)
        migrate.verify(self.workspace)
        migrate.activate(self.workspace)
        self.f.output = self.workspace/'output'
        self.f.store = fixture.project.ProjectStore(self.f.output/'h3_chains'/self.f.run)
        fixture.folder_paths.output_directory = str(self.f.output)
        token = state._ACCESS.set(None)
        self.addCleanup(state._ACCESS.reset, token)

    def test_normal_save_and_retry_with_comfy_execution_identity(self):
        from comfy_execution.utils import CurrentNodeContext
        job = uuid.uuid4().hex
        with CurrentNodeContext(job, 'start'):
            value = chain.MiniMaxH3ChainLoopStart().start(copy.deepcopy(self.f.plan), 1)[1]
        with CurrentNodeContext(job, 'save'):
            first = chain.MiniMaxH3ChainSegmentSave().save(value, self.f.frames,
                fixture.av_latent(.4), self.f.audio(1), unique_id='save')
            retried = chain.MiniMaxH3ChainSegmentSave().save(value, self.f.frames,
                fixture.av_latent(.4), self.f.audio(1), unique_id='save')
        self.assertEqual(first['result'][0]['revision'], retried['result'][0]['revision'])
        self.assertEqual(first['result'][0]['seed'], 18446744073709551601)
        self.assertIsNone(fixture.carriers._HOST.get())
        self.assertIsNone(fixture.runtime._ACTIVE.get())
        self.assertIsNone(state._ACCESS.get())
        self.assertFalse((self.f.store.project/'segments').exists())

    def test_normal_plan_studio_and_fingerprint(self):
        from comfy_execution.utils import CurrentNodeContext
        with CurrentNodeContext(uuid.uuid4().hex, 'studio'):
            result = chain.MiniMaxH3ChainPlanStudio().passthrough(
                plan=copy.deepcopy(self.f.plan), verify_resume_history=False)
        self.assertIsInstance(result, dict)
        self.assertFalse((self.f.store.project/'plan_studio_presentation.json').exists())
        with state.control_rehearsal_access(self.f.store.project):
            saved = self.f.store.snapshot().read('plan_studio_presentation.json')
        self.assertEqual(json.loads(saved)['run_name'], self.f.run)
        chain.MiniMaxH3ChainManifestLoad.IS_CHANGED(self.f.plan)
        self.assertIsNone(fixture.runtime._ACTIVE.get())

    def test_normal_checkpoint_http_without_test_context(self):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        from aiohttp import web
        async def run():
            app = web.Application()
            app.router.add_get('/checkpoints', chain._list_saved_checkpoints)
            app.router.add_get('/run', chain._load_saved_run)
            app.router.add_get('/runs', chain._list_saved_runs)
            async with TestClient(TestServer(app)) as client:
                response = await client.get('/checkpoints', params={'run_name':self.f.run})
                value = await response.json()
                self.assertEqual(response.status, 200, value)
                pin = json.loads(response.headers['X-H3-Storage-Pin'])
                self.assertEqual(pin['run_name'], self.f.run)
                self.assertIsNone(state._ACCESS.get())
                response = await client.get('/run', params={'run_name':self.f.run})
                value = await response.json()
                self.assertEqual(response.status, 200, value)
                self.assertEqual(value['scene_count'], 2)
                response = await client.get('/runs')
                value = await response.json()
                self.assertEqual(response.status, 200, value)
                self.assertTrue(value['runs'][0]['restorable'])
                self.assertEqual(value['runs'][0]['checkpoint_count'], 2)
        asyncio.run(run())

    def test_normal_streamed_asset_upload_and_delete(self):
        import asyncio
        import io
        from PIL import Image
        from aiohttp import FormData, web
        from aiohttp.test_utils import TestClient, TestServer
        data = io.BytesIO()
        Image.new('RGB', (8, 8), (15, 22, 33)).save(data, format='PNG')
        async def run():
            app = web.Application()
            app.router.add_get('/session', host.storage_session)
            app.router.add_post('/upload', chain._project_asset_upload)
            app.router.add_post('/delete', chain._project_asset_delete)
            async with TestClient(TestServer(app)) as client:
                response = await client.get('/session', params={'run_name':self.f.run})
                pin = (await response.json())['pin']
                headers = {'X-H3-Storage-Pin':json.dumps(pin),
                    'X-H3-Workflow-Owner':self.f.proof['owner_id'],
                    'X-H3-Ownership-Epoch':str(self.f.proof['epoch'])}
                form = FormData()
                form.add_field('project', self.f.run)
                form.add_field('storage_operation_id', uuid.uuid4().hex)
                form.add_field('file', data.getvalue(), filename='small.png', content_type='image/png')
                response = await client.post('/upload', data=form, headers=headers)
                value = await response.json()
                self.assertEqual(response.status, 200, value)
                headers['X-H3-Storage-Pin'] = response.headers['X-H3-Storage-Pin']
                response = await client.post('/delete', headers=headers, json={
                    'project':self.f.run, 'asset_id':value['asset']['id'],
                    'storage_operation_id':uuid.uuid4().hex})
                value = await response.json()
                self.assertEqual(response.status, 200, value)
                self.assertEqual(value['catalog']['assets'], [])
        with patch.object(fixture.folder_paths, 'get_input_directory', return_value=str(self.f.lab/'input')):
            asyncio.run(run())

    def test_normal_run_manager_archives_loader_and_source_audio(self):
        from comfy_execution.utils import CurrentNodeContext
        from PIL import Image
        inputs = self.f.lab/'run-manager-input'
        inputs.mkdir()
        picture = inputs/'picture.png'
        Image.new('RGB', (8, 8), (44, 55, 66)).save(picture)
        bindings = [dict(binding_id='picture', role='picture', original_value='picture.png')]
        timeline = chain.MiniMaxH3SourceTimeline().build(
            video_path='', source_audio=self.f.audio(1))[0]
        with patch.object(fixture.folder_paths, 'get_input_directory', return_value=str(inputs)):
            with CurrentNodeContext(uuid.uuid4().hex, 'run-manager'):
                result = chain.MiniMaxH3ChainRunManager().passthrough(
                    self.f.plan, True, True, False, json.dumps(bindings), source_timeline=timeline)
        self.assertTrue(result[1]['audio']['materialized_from_tensor'])
        self.assertFalse((self.f.store.project/'references').exists())
        self.assertFalse((self.f.store.project/'source_timeline').exists())
        with state.control_rehearsal_access(self.f.store.project):
            snapshot = self.f.store.snapshot()
            document = json.loads(snapshot.read('references/manifest.json'))
            audio = json.loads(snapshot.read('source_timeline.json'))
            self.assertEqual(len(document['bindings']), 1)
            self.assertTrue(document['bindings'][0].get('archive'))
            self.assertEqual(audio['audio']['kind'], 'external_path')
            # Recovery resolves the accepted archive, not an old references/
            # directory, when the original loader input is unavailable.
            restore_inputs = self.f.lab/'restored-input'
            with fixture.runtime.runtime_access(self.f.store):
                restored = fixture.module('asset_store').RunAssetStore(
                    str(self.f.output), str(restore_inputs)).prepare_restore(self.f.run)
            self.assertFalse(restored['warnings'], restored)
            restored_files = list(restore_inputs.iterdir())
            self.assertEqual(len(restored_files), 1)
            self.assertEqual(restored_files[0].read_bytes(), picture.read_bytes())

    def test_activation_rejects_changed_bootstrap_and_unscoped_execution(self):
        with self.assertRaisesRegex(ValueError, 'execution context'):
            with host.hosted(self.f.run):
                self.fail('Non-Comfy call acquired a normal node host.')
        with patch.object(host.state, '_hash', return_value='different'):
            with self.assertRaisesRegex(ValueError, 'activation'):
                host.activated_project(self.f.output, self.f.run)

    def test_public_migration_then_normal_recursive_pixel_png_and_assembly(self):
        from comfy_execution.utils import CurrentNodeContext
        from _storage_processing_graph_integration_test import execute_graph
        with CurrentNodeContext(uuid.uuid4().hex, 'manifest'):
            manifest = chain.MiniMaxH3ChainManifestLoad().load(self.f.plan)[0]
        result = execute_graph(self.f.store, manifest, normal_host=True,
            export_png=True, assembly_node='assemble')
        self.assertTrue(result['success'], result['errors'])
        self.assertIsNone(state._ACCESS.get())
        self.assertIsNone(fixture.carriers._HOST.get())


if __name__ == '__main__':
    unittest.main(argv=['storage-host-test'], verbosity=2)
