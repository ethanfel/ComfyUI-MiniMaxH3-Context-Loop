"""Real aiohttp requests through the storage wrapper and existing H3 handlers.

Only fixture media are used. No running ComfyUI, GPU, or user project is touched.
"""
import asyncio
import copy
import json
import unittest
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import _storage_branch_routes_unit_test as branches
import _storage_asset_edits_unit_test as assets
from storage_carriers import node_host, storage_request
from storage_runtime import runtime_access


PIN = 'X-H3-Storage-Pin'


def headers(pin, proof=None):
    value = {PIN: json.dumps(pin)}
    if proof:
        value.update({'X-H3-Workflow-Owner': proof['owner_id'],
                      'X-H3-Ownership-Epoch': str(proof['epoch'])})
    return value


class HTTPTests(unittest.TestCase):
    def branch_fixture(self):
        f = branches.RouteTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.namespace['web'] = web
        return f

    def test_branch_load_save_retry_and_stale_edit_use_exact_successor(self):
        f = self.branch_fixture()
        handler = f.namespace['_working_branch_command']

        async def check():
            app = web.Application()
            app.router.add_route('*', '/branches', storage_request(handler))
            with node_host(f.store, request_grants={handler: ('branch',)}):
                async with TestClient(TestServer(app)) as client:
                    selection = dict(run_name='demo', action='load', branch_id=f.named)
                    response = await client.get('/branches', params=selection)
                    self.assertEqual(response.status, 200, await response.text())
                    original_pin = json.loads(response.headers[PIN])
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    loaded = await response.json()
                    authoring = copy.deepcopy(loaded['authoring'])
                    plan = json.loads(authoring['plan_json'])
                    plan['shots'][0].update(prompt='Exact HTTP prompt 雪', seed='18446744073709551610')
                    authoring['plan_json'] = json.dumps(plan, ensure_ascii=False)
                    body = dict(selection, action='save', authoring=authoring,
                                revision=loaded['revision'], operation_id=uuid.uuid4().hex)
                    response = await client.post('/branches', json=body, headers=headers(original_pin, f.proof))
                    self.assertEqual(response.status, 200, await response.text())
                    saved, accepted_pin = await response.json(), json.loads(response.headers[PIN])
                    self.assertNotEqual(original_pin, accepted_pin)
                    # A lost reply must be repeatable from the original request pin.
                    retry = await client.post('/branches', json=body, headers=headers(original_pin, f.proof))
                    self.assertEqual(retry.status, 200, await retry.text())
                    self.assertEqual(await retry.json(), saved)
                    self.assertEqual(json.loads(retry.headers[PIN]), accepted_pin)
                    # An unrelated edit from the old view cannot overwrite it.
                    stale = await client.post('/branches', json=dict(body, operation_id=uuid.uuid4().hex),
                                              headers=headers(original_pin, f.proof))
                    self.assertIn(stale.status, (400, 409), await stale.text())
                    self.assertNotIn(PIN, stale.headers)
                    response = await client.get('/branches', params=selection, headers=headers(accepted_pin))
                    self.assertEqual(response.status, 200, await response.text())
                    self.assertEqual(await response.json(), saved)
                    shot = json.loads(saved['authoring']['plan_json'])['shots'][0]
                    self.assertEqual(shot['prompt'], 'Exact HTTP prompt 雪')
                    self.assertEqual(shot['seed'], '18446744073709551610')
                    self.assertEqual((saved['authoring']['width'], saved['authoring']['height']), (960, 544))
        asyncio.run(check())

    def test_request_cannot_grant_access_or_mix_project_branch_and_pin(self):
        f = self.branch_fixture()
        handler = f.namespace['_working_branch_command']
        before = f.store.snapshot().reference
        with runtime_access(f.store, selected=f.named) as bound:
            pin = bound.pin

        async def check():
            app = web.Application()
            app.router.add_route('*', '/branches', storage_request(handler))
            with node_host(f.store, request_grants={handler: ()}):
                async with TestClient(TestServer(app)) as client:
                    for query, supplied in [
                            ({'run_name': 'other'}, headers(pin)),
                            ({'run_name': 'demo', 'branch_id': 'main'}, headers(pin)),
                            ({'run_name': 'demo'}, {PIN: 'null'}),
                            ({'run_name': 'demo'}, {PIN: '['}),
                            ({'run_name': 'demo'}, {PIN: 'x' * 4097})]:
                        response = await client.get('/branches', params=query, headers=supplied)
                        self.assertEqual(response.status, 409, await response.text())
                    body = dict(run_name='demo', branch_id=f.named, action='default', branch_writes=True)
                    response = await client.post('/branches', json=body)
                    self.assertEqual(response.status, 409, await response.text())
                    response = await client.post('/branches', json=body, headers=headers(pin, f.proof))
                    self.assertIn(response.status, (400, 409), await response.text())
                    response = await client.post('/branches?run_name=other', json=body,
                                                 headers=headers(pin, f.proof))
                    self.assertEqual(response.status, 409, await response.text())
        asyncio.run(check())
        self.assertEqual(f.store.snapshot().reference, before)

    def test_real_asset_worker_checks_owner_and_returns_committed_catalog(self):
        f = assets.AssetEditTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        namespace = f.asset_routes()
        namespace['web'] = web
        handler = namespace['_project_asset_folder']
        original_pin = f.pin()
        body = dict(project='demo', action='create', name='HTTP folder',
                    storage_operation_id=uuid.uuid4().hex)

        async def check():
            app = web.Application()
            app.router.add_post('/folder', storage_request(handler))
            with node_host(f.store, asset_input_root=f.input, request_grants={handler: ('asset',)}):
                async with TestClient(TestServer(app)) as client:
                    denied = await client.post('/folder', json=body, headers=headers(original_pin))
                    self.assertEqual(denied.status, 423, await denied.text())
                    response = await client.post('/folder', json=body, headers=headers(original_pin, f.f.proof))
                    self.assertEqual(response.status, 200, await response.text())
                    result, accepted = await response.json(), json.loads(response.headers[PIN])
                    self.assertNotEqual(accepted, original_pin)
                    f.assert_synced(result)
                    retry = await client.post('/folder', json=body, headers=headers(original_pin, f.f.proof))
                    self.assertEqual(retry.status, 200, await retry.text())
                    self.assertEqual(await retry.json(), result)
                    self.assertEqual(json.loads(retry.headers[PIN]), accepted)
                    f.assert_synced(result)
        asyncio.run(check())

    def test_ungranted_and_expired_hosts_fail_while_legacy_remains_unchanged(self):
        f = self.branch_fixture()
        calls = []

        async def handler(request):
            calls.append(request.method)
            return web.json_response({'legacy': True})

        async def check():
            wrapped = storage_request(handler)
            self.assertIs(storage_request(wrapped), wrapped)
            app = web.Application()
            app.router.add_get('/read', wrapped)
            with node_host(f.store):
                async with TestClient(TestServer(app)) as client:
                    response = await client.get('/read', params={'run_name': 'demo'})
                    self.assertEqual(response.status, 409, await response.text())
            self.assertFalse(calls)
            app = web.Application()
            app.router.add_get('/read', wrapped)
            with node_host(f.store, request_grants={handler: ()}):
                client = TestClient(TestServer(app))
                await client.start_server()
                response = await client.get('/read', params={'run_name': 'demo'})
                self.assertEqual(response.status, 200)
            try:
                # The server task retained its context, but its host is revoked.
                response = await client.get('/read', params={'run_name': 'demo'})
                self.assertEqual(response.status, 409, await response.text())
            finally:
                await client.close()
            self.assertEqual(calls, ['GET'])
            app = web.Application()
            app.router.add_get('/read', wrapped)
            async with TestClient(TestServer(app)) as client:
                response = await client.get('/read')
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {'legacy': True})
                self.assertNotIn(PIN, response.headers)
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
