"""Actual restore handler: lineage, ownership and one combined publication."""
import ast
import asyncio
from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch
import uuid

import _storage_branch_routes_unit_test as fixture
from storage_branch_controls import BranchControlDocuments
from storage_runtime import runtime_access
from checkpoint_manager import CheckpointGraphManager, _strict_run_name
from storage_resolver import resolve_output
from run_manager import archive_policy_inputs
from branch_scope import branch_scope
import project_ownership as ownership
import storage_state as state


def routes(output, *, source=None):
    source = Path(source) if source else Path(__file__).resolve().parents[1]/'chain_nodes.py'
    names = {'_attribute_checkpoint_revision', '_restore_checkpoint_revisions', '_publish_runtime_checkpoint_restore',
             '_checkpoint_plan_revision', '_video_output_item', '_load_checkpoint_revision',
             '_verify_segment_artifacts', '_file_sha256', '_read_json',
             '_project_write_rejection', '_request_project_ownership',
             '_require_project_write', '_project_ownership_proof'}
    nodes = [item for item in ast.parse(source.read_text()).body
             if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in names]
    assert {item.name for item in nodes} == names
    namespace = dict(vars(ownership), Any=Any, os=os, re=re, json=json, hashlib=hashlib,
        uuid=uuid, asyncio=asyncio, nullcontext=nullcontext, MAX_SHOTS=128, archive_policy_inputs=archive_policy_inputs,
        CheckpointGraphManager=CheckpointGraphManager, _strict_run_name=_strict_run_name,
        _output_root=lambda: str(output),
        _absolute_output_path=lambda value: str(resolve_output(output, value)),
        _canonical_json=lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')),
        web=SimpleNamespace(json_response=lambda value, status=200, **kwargs:
                            {'status': status, 'body': value, **kwargs}))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RouteTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.proof = self.f.proof
        self.ns = routes(self.output)
        self.body = dict(run_name='demo', scope_start_scene=1, scope_end_scene=2,
                         resume_scene=2, activate_only=True,
                         revisions=[dict(scene=1, revision='1'*32)])

    def call(self, body=None, proof=True):
        return asyncio.run(self.ns['_restore_checkpoint_revisions'](
            fixture.request(self.body if body is None else body, self.proof if proof is True else proof)))

    def change(self, address, value):
        scope, category, immutable = BranchControlDocuments._contract(address)
        return {address: dict(data=state._encode(value), scope=scope, category=category, immutable=immutable)}

    def alternative(self, scene, *, archives=None, **updates):
        base = self.store.snapshot()
        original = state._decode(base.read('checkpoints/clip_%04d.%s.json' % (scene, str(scene)*32)))
        revision = uuid.uuid4().hex
        original['segment'].update(revision=revision, **updates)
        if archives is not None:
            original['archives'] = archives
        address = 'checkpoints/clip_%04d.%s.json' % (scene, revision)
        staged = []
        with runtime_access(self.store) as runtime:
            for key in ('segment', 'checkpoint', 'generated_audio', 'prompt_file'):
                source = runtime.reader.path(original['segment'][key])
                target = runtime.reader.address(original['segment'][key]).replace(str(scene)*32, revision)
                staged.append(self.store.stage_payload(target, source,
                    'media/generation/'+revision+'/'+source.name,
                    scope='archive:'+revision, operation_id=uuid.uuid4().hex))
                original['segment'][key] = 'h3_chains/demo/'+target
        self.store.commit_artifacts(base, {address: dict(data=state._encode(original), scope='archive:'+revision,
            category='takes', immutable=True)}, staged, operation_id=uuid.uuid4().hex)
        return revision

    def test_restore_is_atomic_and_preserves_exact_prompt_seed_media_and_old_pin(self):
        before = self.store.snapshot()
        original = state._decode(before.read('checkpoints/clip_0001.json'))
        observations = []
        with runtime_access(self.store, branch_writes=True) as runtime:
            runtime.branches.after_stage = lambda _: observations.append(self.store.snapshot().reference)
            result = self.call()
            self.assertEqual(result['status'], 200, result)
            self.assertEqual(result['body']['storage_pin'], runtime.output_pin)
            self.assertNotEqual(runtime.output_pin, runtime.pin)
            self.assertIn(2, CheckpointGraphManager(self.output).active_selection('demo')[0])
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertTrue(observations and all(root == before.reference for root in observations))
        assigned = state._decode(after.read('checkpoints/clip_0001.json'))
        self.assertEqual(assigned['segment'], original['segment'])
        self.assertIn('_authoring_assignment', assigned)
        self.assertNotIn('_authoring_assignment', original)
        self.assertNotIn('checkpoints/clip_0002.json', after.state['documents'])
        self.assertEqual(result['body']['retired_scope_pointers'], 1)
        scene = result['body']['restored'][0]
        self.assertEqual(scene['seed'], str(original['segment']['seed']))
        self.assertEqual(scene['scene_prompt'], original['segment']['scene_prompt_template'])
        self.assertTrue((self.output/scene['video']['subfolder']/scene['video']['filename']).is_file())
        self.assertEqual(after.read('checkpoints/clip_0003.json'), before.read('checkpoints/clip_0003.json'))
        before.verify()
        self.assertFalse((self.store.project/'checkpoints').exists())

    def test_named_branch_only_and_retired_assignment_can_be_restored(self):
        before = self.store.snapshot()
        with runtime_access(self.store, selected=self.named, branch_writes=True):
            result = self.call()
            self.assertEqual(result['status'], 200, result)
        after = self.store.snapshot()
        self.assertEqual(after.read('checkpoints/clip_0002.json'), before.read('checkpoints/clip_0002.json'))
        prefix = 'branches/'+self.named+'/checkpoints/'
        self.assertNotIn(prefix+'clip_0002.json', after.state['documents'])
        body = dict(self.body, resume_scene=3, revisions=[dict(scene=i, revision=str(i)*32) for i in (1, 2)])
        with runtime_access(self.store, selected=self.named, branch_writes=True):
            result = self.call(body)
            self.assertEqual(result['status'], 200, result)
        self.assertEqual(state._decode(self.store.snapshot().read(prefix+'clip_0002.json'))['segment']['revision'], '2'*32)

    def test_alt_cannot_replace_generation_lineage(self):
        revision = self.alternative(1, take_kind='editorial_alternate')
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            result = self.call(dict(self.body, revisions=[dict(scene=1, revision=revision)]))
        self.assertEqual(result['status'], 400, result)
        self.assertIn('alternate', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_mismatched_predecessor_rejected(self):
        revision = self.alternative(2, predecessor_revision='e'*32)
        body = dict(self.body, resume_scene=3, revisions=[dict(scene=1, revision='1'*32), dict(scene=2, revision=revision)])
        with runtime_access(self.store, branch_writes=True):
            result = self.call(body)
        self.assertEqual(result['status'], 400, result)
        self.assertIn('different scene 1', result['body']['error'])

    def test_strict_shared_prompts_still_checked_but_activation_allows_them(self):
        revision = self.alternative(2, prompt_prefix='Different shared prompt')
        body = dict(self.body, resume_scene=3, activate_only=False,
                    revisions=[dict(scene=1, revision='1'*32), dict(scene=2, revision=revision)])
        with runtime_access(self.store, branch_writes=True):
            result = self.call(body)
            self.assertEqual(result['status'], 400, result)
            self.assertIn('different shared prompts', result['body']['error'])
            accepted = self.call(dict(body, activate_only=True))
            self.assertEqual(accepted['status'], 200, accepted)

    def test_concurrent_branch_write_rejected_without_rebase(self):
        with runtime_access(self.store, branch_writes=True):
            base = self.store.snapshot()
            self.store.commit(base, self.change('plan.json', {'concurrent': True}), operation_id=uuid.uuid4().hex)
            current = self.store.snapshot().reference
            result = self.call()
            self.assertEqual(result['status'], 409, result)
        self.assertEqual(current, self.store.snapshot().reference)

    def test_unrelated_branch_write_is_preserved(self):
        address = 'branches/'+self.named+'/plan.json'
        with runtime_access(self.store, branch_writes=True):
            self.store.commit(self.store.snapshot(), self.change(address, {'unrelated': True}), operation_id=uuid.uuid4().hex)
            result = self.call()
            self.assertEqual(result['status'], 200, result)
        self.assertEqual(state._decode(self.store.snapshot().read(address)), {'unrelated': True})

    def test_read_only_and_missing_proof_reject(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            self.assertEqual(self.call(proof=None)['status'], 423)
            result = self.call()
            self.assertEqual(result['status'], 400, result)
            self.assertIn('read-only', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_takeover_after_validation_rejects_publication(self):
        real = self.ns['_publish_runtime_checkpoint_restore']
        def takeover(*args):
            ownership.claim_project_ownership(self.output, 'demo', fixture.B, force=True)
            return real(*args)
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True, ownership_writes=True):
            with patch.dict(self.ns, _publish_runtime_checkpoint_restore=takeover):
                result = self.call()
        self.assertEqual(result['status'], 423, result)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_failed_prepublication_retains_entire_old_assignment(self):
        before = self.store.snapshot().reference
        def fail(stage):
            if stage == 'retired_pointer':
                raise OSError('test restore interrupted before publication')
        with runtime_access(self.store, branch_writes=True) as runtime:
            runtime.branches.after_stage = fail
            result = self.call()
        self.assertEqual(result['status'], 503, result)
        self.assertFalse(result['body']['retry_automatically'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_lost_ack_is_uncertain_not_false_success_or_automatic_retry(self):
        before = self.store.snapshot().reference
        real = self.store._publish
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('test restore lost acknowledgement')
        with runtime_access(self.store, branch_writes=True):
            with patch.object(self.store, '_publish', lost):
                result = self.call()
        self.assertEqual(result['status'], 503, result)
        self.assertFalse(result['body']['retry_automatically'])
        self.assertNotEqual(before, self.store.snapshot().reference)
        self.assertNotIn('checkpoints/clip_0002.json', self.store.snapshot().state['documents'])

    def test_corrupt_payload_cannot_be_assigned(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True) as runtime:
            metadata = runtime.reader.read(self.store.project/'checkpoints/clip_0001.json')
            path = runtime.reader.path(metadata['segment']['checkpoint'])
            raw = path.read_bytes()
            try:
                path.write_bytes(b'corrupt')
                result = self.call()
                self.assertEqual(result['status'], 400, result)
                self.assertIn('integrity', result['body']['error'])
            finally:
                path.write_bytes(raw)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_malformed_requests_and_scope_rejected_without_writes(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            for body in ([], None, dict(self.body, scope_end_scene=129),
                         dict(self.body, revisions=[]), dict(self.body, revisions=self.body['revisions']*2)):
                if body is None:
                    body = 'invalid'
                result = self.call(body)
                self.assertEqual(result['status'], 400, result)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_scope_cannot_switch_away_from_runtime_branch(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True), branch_scope('demo', self.named):
            result = self.call()
        self.assertEqual(result['status'], 400, result)
        self.assertIn('different runtime branch', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)


if __name__ == '__main__':
    unittest.main(verbosity=2)
