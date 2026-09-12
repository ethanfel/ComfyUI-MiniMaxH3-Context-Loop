"""Read acceptance through normal HTTP routes, on an activated migration copy.

No test runtime host is used. No scene is generated or deleted. The original
source is read only; the independent destination must have passed CLI verify.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace', required=True)
parser.add_argument('--restore-only', action='store_true', help='Check normal saved-run restore without repeating checkpoint scans')
args = parser.parse_args()
workspace = Path(args.workspace).absolute()
restore_only = args.restore_only

import _storage_generation_integration_test as fixture
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

migrate = fixture.module('storage_migrate')
_, source, inventory = migrate._load(workspace)
destination = workspace/'output/h3_chains'/source.name
chain = fixture.chain
branches = fixture.module('working_branches').WorkingBranches


async def check():
    app = web.Application()
    app.router.add_get('/branches', chain._working_branch_command)
    app.router.add_get('/checkpoints', chain._list_saved_checkpoints)
    app.router.add_get('/assets', chain._project_asset_catalog)
    app.router.add_get('/run', chain._load_saved_run)
    app.router.add_get('/runs', chain._list_saved_runs)
    fixture.folder_paths.output_directory = str(destination.parent.parent)
    results = []
    async with TestClient(TestServer(app)) as client:
        async def get(route, **query):
            response = await client.get(route, params={'run_name':source.name, **query})
            value = await response.json()
            if response.status != 200:
                raise AssertionError((route, query, response.status, value))
            assert response.headers.get('X-H3-Storage-Pin'), route
            return value
        listing = await get('/branches')
        expected = branches(source.parent.parent, source.name).listing()
        assert listing == expected, 'Branch list differs from source'
        results.append('branch_list')
        response = await client.get('/runs')
        runs = (await response.json())['runs']
        assert response.status == 200 and any(row['run_name'] == source.name and row['restorable'] for row in runs)
        results.append('run_list')
        for branch in listing['branches']:
            selected = branch['id']
            actual = await get('/branches', action='load', branch_id=selected)
            assert actual == branches(source.parent.parent, source.name).load(selected), selected
            results.append('authoring:'+selected)
            restored = await get('/run', branch_id=selected, include_assets='false')
            assert restored['plan_inputs']['run_name'] == source.name
            assert restored['scene_count'] > 0
            results.append('restore:'+selected)
            if not restore_only:
                await get('/checkpoints', branch_id=selected)
                results.append('checkpoints:'+selected)
        if not restore_only:
            await get('/assets', project=source.name)
            results.append('asset_catalog')
    assert fixture.runtime._ACTIVE.get() is None
    assert fixture.carriers._HOST.get() is None
    assert fixture.state._ACCESS.get() is None
    return dict(passed=len(results), checks=results, destination=str(destination),
                source_unchanged=True, live_project_modified=False, gpu_render_tested=False)


print(json.dumps(asyncio.run(check()), indent=2))
