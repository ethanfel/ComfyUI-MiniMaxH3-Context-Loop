"""Actual branch/ownership HTTP handlers on a copied combined project.

Only the HTTP transport and notifications are replaced. Domain validation,
ownership guards, prefix verification and branch transactions are real.
"""
import asyncio
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
import unittest
import uuid

import _storage_runtime_unit_test as fixture
import storage_state as state
from storage_runtime import runtime_access
from storage_resolver import resolve_output
from storage_branch_controls import BranchControlDocuments
from checkpoint_manager import CheckpointGraphManager, _strict_run_name
from working_branches import WorkingBranches
from branch_scope import branch_scope, branch_id
import project_ownership as ownership

A, B = 'workflow-owner-a-1234567890', 'workflow-owner-b-1234567890'


def routes(output):
    source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
    names = {'_working_branch_command', '_project_ownership_command',
        '_project_write_rejection', '_request_project_ownership', '_require_project_write',
        '_project_ownership_proof', '_load_checkpoint_revision', '_verify_segment_artifacts',
        '_file_sha256', '_read_json'}
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    events = []
    namespace = dict(vars(ownership), Any=Any, os=os, re=re, json=json, hashlib=hashlib, asyncio=asyncio,
        WorkingBranches=WorkingBranches, CheckpointGraphManager=CheckpointGraphManager,
        _output_root=lambda: str(output), _strict_run_name=_strict_run_name,
        _working_branch_id=branch_id, branch_scope=branch_scope, MAX_SHOTS=10000,
        _absolute_output_path=lambda value: str(resolve_output(output, value)),
        _publish_project_ownership=lambda value: events.append(('published', value)),
        _fence_inflight_project_work=lambda run: events.append(('fenced', run)),
        web=SimpleNamespace(json_response=lambda value, status=200, **kwargs:
                            {'status': status, 'body': value, **kwargs}))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace, events


def request(body, proof=None, method='POST'):
    async def content():
        return body
    headers = {} if proof is None else {'X-H3-Workflow-Owner': proof['owner_id'],
                                       'X-H3-Ownership-Epoch': str(proof['epoch'])}
    return SimpleNamespace(method=method, query=body, json=content, headers=headers)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.namespace, self.events = routes(self.output)
        # Small opaque files exercise the real SHA-256 verifier, no GPU/mock
        # checkpoint decoder. The immutable take agrees with its source pointer.
        controls, staged = {}, []
        base = self.store.snapshot()
        for scene in (1, 2):
            pointer = 'checkpoints/clip_%04d.json' % scene
            metadata = state._decode(base.read(pointer))
            segment = metadata['segment']
            stem = 'clip_%04d.%s' % (scene, segment['revision'])
            for key, folder, ext in (('segment', 'segments', '.mp4'),
                    ('checkpoint', 'checkpoints', '.safetensors'),
                    ('generated_audio', 'generated_audio', '.wav'),
                    ('prompt_file', 'checkpoints', '.txt')):
                raw = ('Exact '+key+' é\r\n'+str(scene)).encode()
                source = self.output/(stem+ext)
                source.write_bytes(raw)
                address = folder+'/'+stem+ext
                segment[key] = 'h3_chains/demo/'+address
                segment[key+'_sha256'] = hashlib.sha256(raw).hexdigest()
                staged.append(self.store.stage_payload(address, source,
                    'media/generation/'+segment['revision']+'/'+key+ext,
                    scope='archive:'+segment['revision'], operation_id=uuid.uuid4().hex))
            scope, category, immutable = BranchControlDocuments._contract(pointer)
            controls[pointer] = dict(data=state._encode(metadata), scope=scope,
                                     category=category, immutable=immutable)
            controls['checkpoints/'+stem+'.json'] = dict(data=state._encode(metadata),
                scope='archive:'+segment['revision'], category='takes', immutable=True)
        self.store.commit_artifacts(base, controls, staged, operation_id=uuid.uuid4().hex)
        with runtime_access(self.store, ownership_writes=True):
            result = self.call('_project_ownership_command', {'action': 'claim', 'owner_id': A})
            self.assertEqual(result['status'], 200, result)
        self.proof = dict(owner_id=A, epoch=result['body']['epoch'])

    def call(self, handler, body, proof=None, method='POST'):
        return asyncio.run(self.namespace[handler](request(dict(run_name='demo', **body), proof, method)))

    def branch(self, body, proof=None):
        return self.call('_working_branch_command', body, proof or self.proof)

    def test_save_and_default_response_include_exact_new_authoring_and_selection(self):
        with runtime_access(self.store, branch_writes=True):
            before = WorkingBranches(self.output, 'demo').load(self.named)
            authored = copy.deepcopy(before['authoring'])
            plan = json.loads(authored['plan_json'])
            plan['shots'][0].update(prompt='Chosen prompt 雪', seed='18446744073709551610')
            authored['plan_json'] = json.dumps(plan, ensure_ascii=False)
            saved = self.branch(dict(action='save', branch_id=self.named, authoring=authored,
                revision=before['revision'], operation_id=uuid.uuid4().hex))
            self.assertEqual(saved['status'], 200, saved)
            self.assertEqual(WorkingBranches(self.output, 'demo').load(self.named), before)
        with runtime_access(self.store, branch_writes=True):
            loaded = WorkingBranches(self.output, 'demo').load(self.named)
            self.assertEqual(loaded, saved['body'])
            shot = json.loads(loaded['authoring']['plan_json'])['shots'][0]
            self.assertEqual(shot['prompt'], 'Chosen prompt 雪')
            self.assertEqual(shot['seed'], '18446744073709551610')
            self.assertEqual((loaded['authoring']['width'], loaded['authoring']['height']), (960, 544))
            result = self.branch(dict(action='default', branch_id=self.named))
            self.assertEqual(result['status'], 200, result)
            self.assertEqual(result['body']['default_branch'], self.named)
        with runtime_access(self.store):
            self.assertEqual(WorkingBranches(self.output, 'demo').listing(), result['body'])

    def test_fork_preserves_verified_prefix_and_empty_branch_keeps_no_clips(self):
        before = self.store.snapshot()
        with runtime_access(self.store, branch_writes=True):
            authored = WorkingBranches(self.output, 'demo').load()['authoring']
            body = dict(action='create', name='Fork', authoring=authored,
                        through_scene=2, operation_id=uuid.uuid4().hex)
            result = self.branch(body)
            self.assertEqual(result['status'], 200, result)
        fork = result['body']['id']
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        for scene in (1, 2):
            relative = 'checkpoints/clip_%04d.json' % scene
            self.assertEqual(state._decode(after.read('branches/'+fork+'/'+relative)),
                             state._decode(before.read(relative)))
        self.assertNotIn('branches/'+fork+'/checkpoints/clip_0003.json', after.state['documents'])
        with runtime_access(self.store, branch_writes=True):
            replay = self.branch(body)
            self.assertEqual(replay, result)
            empty = self.branch(dict(body, name='Empty', through_scene=0, operation_id=uuid.uuid4().hex))
            self.assertEqual(empty['status'], 200, empty)
        documents = self.store.snapshot().state['documents']
        self.assertFalse(any(key.startswith('branches/'+empty['body']['id']+'/checkpoints/') for key in documents))
        self.assertEqual(empty['body']['authoring']['base_seed'], authored['base_seed'])
        self.assertFalse((self.store.project/'checkpoints').exists())

    def test_missing_or_corrupt_media_prevents_fork_without_branch_publication(self):
        for key in ('segment', 'checkpoint', 'generated_audio', 'prompt_file'):
            with self.subTest(key=key), runtime_access(self.store, branch_writes=True) as runtime:
                metadata = runtime.reader.read(self.store.project/'checkpoints/clip_0001.json')
                path = runtime.reader.path(metadata['segment'][key])
                raw = path.read_bytes()
                before = self.store.snapshot().reference
                try:
                    path.write_bytes(b'bad')
                    authored = WorkingBranches(self.output, 'demo').load()['authoring']
                    result = self.branch(dict(action='create', name='Broken', authoring=authored,
                        through_scene=2, operation_id=uuid.uuid4().hex))
                    self.assertEqual(result['status'], 400, result)
                    self.assertIn('integrity', result['body']['error'])
                    self.assertEqual(before, self.store.snapshot().reference)
                finally:
                    path.write_bytes(raw)

    def test_force_ownership_rejects_stale_post_and_emits_fence_once(self):
        with runtime_access(self.store, ownership_writes=True, branch_writes=True):
            before = self.store.snapshot().reference
            loaded = WorkingBranches(self.output, 'demo').load()
            forced = self.call('_project_ownership_command', dict(action='force', owner_id=B))
            self.assertEqual(forced['status'], 200)
            result = self.branch(dict(action='save', authoring=loaded['authoring'], revision=loaded['revision']))
            self.assertEqual(result['status'], 423, result)
            self.assertEqual(result['body']['code'], 'h3_project_read_only')
            self.assertEqual(before, self.store.snapshot().reference)
            self.call('_project_ownership_command', dict(action='status', owner_id=B))
            self.call('_project_ownership_command', dict(action='heartbeat', owner_id=B,
                epoch=forced['body']['epoch']))
        self.assertEqual([event for event in self.events if event[0] == 'fenced'], [('fenced', 'demo')])
        self.assertEqual(sum(event[0] == 'published' for event in self.events), 2)

    def test_no_proof_and_read_only_capabilities_reject_mutations(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            authored = WorkingBranches(self.output, 'demo').load()['authoring']
            body = dict(action='create', name='Empty', authoring=authored, through_scene=0)
            self.assertEqual(self.call('_working_branch_command', body)['status'], 423)
            denied = self.branch(body)
            self.assertEqual(denied['status'], 400, denied)
            self.assertIn('read-only', denied['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)


if __name__ == '__main__':
    unittest.main(verbosity=2)
