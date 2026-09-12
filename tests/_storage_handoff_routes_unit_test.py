"""Actual handoff HTTP handlers, bound to isolated combined storage.

Transport is replaced, not ownership, threading, state machines or storage.
No test submits a generation to ComfyUI.
"""
import asyncio
import ast
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

import _storage_runtime_unit_test as fixture
from _storage_branch_routes_unit_test import request
from branch_scope import branch_scope, current_branch
from checkpoint_manager import _strict_run_name, checkpoint_revision_token
from handoff_state import (HandoffStore, HandoffError, HandoffNotFoundError,
    HandoffClaimError, IllegalHandoffTransitionError)
import project_ownership as ownership
from storage_branch_controls import BranchControlDocuments
from storage_runtime import runtime_access
import storage_state as state


def routes(output, *, source=None):
    source = Path(source) if source is not None else Path(__file__).resolve().parents[1]/'chain_nodes.py'
    names = {'_safe_name', '_handoff_store', '_handoff_scene_count', '_handoff_record_view',
        '_handoff_error_response', '_list_handoffs', '_claim_handoff', '_transition_handoff',
        '_release_handoff', '_owned_project_mutation', '_project_write_rejection',
        '_request_project_ownership', '_require_project_write', '_project_ownership_proof',
        '_project_asset_error_response', '_write_next_scene_handoff'}
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    namespace = dict(vars(ownership), __package__='', Any=Any, asyncio=asyncio,
        os=os, re=re, json=json, hashlib=hashlib, _output_root=lambda: str(output),
        checkpoint_revision_token=checkpoint_revision_token,
        _strict_run_name=_strict_run_name, branch_scope=branch_scope,
        _HandoffStore=HandoffStore, _HandoffError=HandoffError,
        _HandoffNotFoundError=HandoffNotFoundError, _HandoffClaimError=HandoffClaimError,
        _IllegalHandoffTransitionError=IllegalHandoffTransitionError,
        _HANDOFF_TRANSITION_STATUSES=('queued', 'consumed', 'cancelled', 'failed', 'uncertain'),
        web=SimpleNamespace(json_response=lambda value, status=200, **kwargs:
                            {'status': status, 'body': value, **kwargs}))
    def legacy_directory(plan):
        root = output/'h3_chains'/plan['run_name']
        selected = current_branch(plan['run_name'])
        return str(root if selected == 'main' else root/'branches'/selected)
    namespace['_run_dir'] = legacy_directory
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class HandoffRouteTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.routes = routes(self.output)
        self.plan_address = 'branches/'+self.named+'/plan.json'
        self.publish_plan(7)
        self.owner = 'handoff-route-owner-1234567890'
        with runtime_access(self.store, ownership_writes=True):
            result = ownership.claim_project_ownership(self.output, 'demo', self.owner)
        self.proof = {'owner_id': self.owner, 'epoch': result['epoch']}

    def publish_plan(self, count):
        scope, category, immutable = BranchControlDocuments._contract(self.plan_address)
        base = self.store.snapshot()
        plan = {'run_name': 'demo', 'shots': [{'id': 'scene_'+str(i)} for i in range(1, count+1)]}
        self.store.commit(base, {self.plan_address: dict(data=state._encode(plan),
            scope=scope, category=category, immutable=immutable)}, operation_id=uuid.uuid4().hex)

    def create(self, selected=None, identity='test-next', **kwargs):
        selected = self.named if selected is None else selected
        with runtime_access(self.store, selected=selected, handoff_writes=True) as runtime:
            record = HandoffStore(str(self.output)).create('demo', action='next_scene',
                scene=2, start_clip=2, end_clip=3, seed=18446744073709551613,
                handoff_id=identity, working_branch_id=selected, **kwargs)
            return record, runtime.output_pin

    def call(self, handler, proof=None, **body):
        payload = dict(run_name='demo', handoff_id='test-next', **body)
        return asyncio.run(self.routes[handler](request(payload, proof)))

    def test_list_uses_each_records_branch_plan_not_runtime_selected_branch(self):
        self.create('main', 'test-main')
        self.create()
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            result = self.call('_list_handoffs')
        self.assertEqual(result['status'], 200, result)
        by_branch = {r.get('working_branch_id', 'main'): r for r in result['body']['handoffs']}
        self.assertEqual(by_branch['main']['resume']['total_scenes'], 3)
        self.assertEqual(by_branch['main']['resume']['scene_range'], '')
        self.assertEqual(by_branch[self.named]['resume']['total_scenes'], 7)
        self.assertEqual(by_branch[self.named]['resume']['scene_range'], '2:3')
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertFalse((self.store.project/'branches').exists())

    def test_historical_list_keeps_old_plan_count_after_later_edit(self):
        _, pin = self.create()
        self.publish_plan(9)
        with runtime_access(self.store, selected=self.named, pin=pin):
            old = self.call('_list_handoffs')
        with runtime_access(self.store, selected=self.named):
            new = self.call('_list_handoffs')
        self.assertEqual(old['body']['handoffs'][0]['resume']['total_scenes'], 7)
        self.assertEqual(new['body']['handoffs'][0]['resume']['total_scenes'], 9)

    def test_real_handlers_claim_queue_consume_and_keep_exact_seed(self):
        self.create()
        for handler, status in (('_claim_handoff', 'claimed'),
                                ('_transition_handoff', 'queued'), ('_transition_handoff', 'consumed')):
            kwargs = {} if status == 'claimed' else {'status': status, 'accepted_prompt_id': 'test-prompt'}
            with runtime_access(self.store, selected=self.named, handoff_writes=True) as runtime:
                result = self.call(handler, self.proof, **kwargs)
                self.assertEqual(result['status'], 200, result)
                self.assertEqual(runtime.output_pin['root'], self.store.snapshot().reference)
            self.assertEqual(result['body']['handoff']['status'], status)
            self.assertEqual(result['body']['handoff']['seed'], 18446744073709551613)
            self.assertEqual(result['body']['handoff']['resume']['total_scenes'], 7)
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            rejected = self.call('_release_handoff', self.proof)
        self.assertEqual(rejected['status'], 409)

    def test_release_claim_then_retry_with_current_state(self):
        self.create()
        for handler, expected in (('_claim_handoff', 'claimed'), ('_release_handoff', 'pending'),
                                   ('_claim_handoff', 'claimed')):
            with runtime_access(self.store, selected=self.named, handoff_writes=True):
                result = self.call(handler, self.proof)
            self.assertEqual(result['status'], 200, result)
            self.assertEqual(result['body']['handoff']['status'], expected)
        self.assertEqual(result['body']['handoff']['attempt'], 2)

    def test_stale_duplicate_claim_returns_conflict_without_second_commit(self):
        _, pin = self.create()
        with runtime_access(self.store, selected=self.named, pin=pin, handoff_writes=True):
            self.assertEqual(self.call('_claim_handoff', self.proof)['status'], 200)
        accepted = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, pin=pin, handoff_writes=True):
            self.assertEqual(self.call('_claim_handoff', self.proof)['status'], 409)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_changed_source_branch_is_conflict_not_500(self):
        self.create()
        self.publish_plan(9)
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            result = self.call('_claim_handoff', self.proof)
        self.assertEqual(result['status'], 409, result)
        self.assertIn('source branch', result['body']['error'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_missing_handoff_is_404_and_readonly_binding_cannot_write(self):
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.assertEqual(self.call('_claim_handoff', self.proof)['status'], 404)
        self.create()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named):
            result = self.call('_claim_handoff', self.proof)
        self.assertEqual(result['status'], 400)
        self.assertIn('read-only', result['body']['error'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_ownership_headers_are_required_and_rechecked_in_worker(self):
        self.create()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.assertEqual(self.call('_claim_handoff')['status'], 423)
        real = self.routes['_owned_project_mutation']
        def takeover(*args, **kwargs):
            # The external authority is deliberately changed between the route's
            # precheck and its worker. Do not bypass the real commit guard.
            ownership.claim_project_ownership(self.output, 'demo',
                'handoff-route-new-owner-1234567890', force=True)
            return real(*args, **kwargs)
        with runtime_access(self.store, selected=self.named, handoff_writes=True, ownership_writes=True), \
                patch.dict(self.routes, _owned_project_mutation=takeover):
            result = self.call('_claim_handoff', self.proof)
        self.assertEqual(result['status'], 423, result)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_invalid_json_objects_and_ownership_headers_return_400(self):
        self.create()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            for handler in ('_claim_handoff', '_transition_handoff', '_release_handoff'):
                for body in ([], None, 2, 'no'):
                    result = asyncio.run(self.routes[handler](request(body, self.proof)))
                    self.assertEqual(result['status'], 400, result)
                result = self.call(handler, dict(self.proof, epoch='bad'), status='queued')
                self.assertEqual(result['status'], 400, result)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_lost_ack_returns_unavailable_and_never_duplicate_queue_authority(self):
        self.create()
        real = self.store.commit
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('test lost acknowledgement')
        with runtime_access(self.store, selected=self.named, handoff_writes=True), \
                patch.object(self.store, 'commit', lost):
            result = self.call('_claim_handoff', self.proof)
        self.assertEqual(result['status'], 503, result)
        self.assertFalse(result['body']['retry_automatically'])
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.assertEqual(self.call('_list_handoffs')['body']['handoffs'][0]['status'], 'claimed')
            self.assertEqual(self.call('_claim_handoff', self.proof)['status'], 409)

    def test_combined_plan_corruption_is_not_hidden_as_a_resume_hint(self):
        self.create()
        # Verified membership exists; simulate unreadable accepted bytes without
        # damaging a project fixture or teaching the reader to tolerate it.
        with runtime_access(self.store, selected=self.named) as runtime:
            real = runtime.reader.read
            def broken(path):
                if str(path).endswith('/plan.json'):
                    raise ValueError('test immutable digest mismatch')
                return real(path)
            with patch.object(runtime.reader, 'read', broken):
                result = self.call('_list_handoffs')
        self.assertEqual(result['status'], 400, result)
        self.assertNotIn('handoffs', result['body'])

    def test_legacy_resume_hint_still_reads_record_branch(self):
        legacy_routes = routes(self.f.f.f.output)
        with branch_scope('demo', self.named):
            self.assertEqual(legacy_routes['_handoff_scene_count']('demo'), 3)

    def loop_end_inputs(self):
        address = 'branches/'+self.named+'/checkpoints/clip_0001.json'
        raw = self.store.snapshot().read(address)
        plan = {'run_name': 'demo', '_branch_id': self.named, 'plan_hash': 'test-plan-hash',
                'shots': [{'id': 'one', 'seed': 2}, {'id': 'two', 'seed': 18446744073709551613}]}
        return plan, json.loads(raw)['segment'], raw

    def test_loop_end_creator_uses_accepted_checkpoint_and_exact_next_seed(self):
        plan, segment, raw = self.loop_end_inputs()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            record = self.routes['_write_next_scene_handoff'](plan, 1, 2, segment)
        self.assertEqual(record['source_checkpoint_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['seed'], 18446744073709551613)
        self.assertEqual(record['source_revision'], segment['revision'])
        self.assertEqual(record['working_branch_id'], self.named)
        accepted = self.store.snapshot().reference
        self.assertNotEqual(before, accepted)
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.assertEqual(self.routes['_write_next_scene_handoff'](plan, 1, 2, segment), record)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_loop_end_creator_rejects_wrong_branch_and_checkpoint_revision(self):
        plan, segment, _ = self.loop_end_inputs()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                self.routes['_write_next_scene_handoff'](dict(plan, _branch_id='main'), 1, 2, segment)
            with self.assertRaisesRegex(ValueError, 'accepted scene checkpoint'):
                self.routes['_write_next_scene_handoff'](plan, 1, 2, dict(segment, revision='d'*32))
        self.assertEqual(self.store.snapshot().reference, before)

    def test_loop_end_creator_recovers_persisted_record_after_lost_response(self):
        plan, segment, _ = self.loop_end_inputs()
        real = self.store.commit
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('test Loop End lost response')
        with runtime_access(self.store, selected=self.named, handoff_writes=True), \
                patch.object(self.store, 'commit', lost):
            with self.assertRaises(OSError):
                self.routes['_write_next_scene_handoff'](plan, 1, 2, segment)
        accepted = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            recovered = self.routes['_write_next_scene_handoff'](plan, 1, 2, segment)
        self.assertEqual(recovered['status'], 'pending')
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_loop_end_creator_uses_same_operation_acknowledged_checkpoint(self):
        plan, segment, _ = self.loop_end_inputs()
        address = 'branches/'+self.named+'/checkpoints/clip_0001.json'
        updated = dict(segment, revision='e'*32)
        raw = state._encode({'segment': updated})
        scope, category, immutable = BranchControlDocuments._contract(address)
        with runtime_access(self.store, selected=self.named, handoff_writes=True) as runtime:
            receipt = self.store.commit(runtime.accepted, {address: dict(data=raw, scope=scope,
                category=category, immutable=immutable)}, operation_id=uuid.uuid4().hex)
            runtime.record_commit(receipt)  # A fixture commit, not generation.
            record = self.routes['_write_next_scene_handoff'](plan, 1, 2, updated)
        self.assertEqual(record['source_checkpoint_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['source_revision'], updated['revision'])

    def test_loop_end_creator_rejects_missing_accepted_checkpoint(self):
        plan, segment, _ = self.loop_end_inputs()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            with self.assertRaisesRegex(ValueError, 'without its accepted scene checkpoint'):
                self.routes['_write_next_scene_handoff'](plan, 4, 5, segment)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_concurrent_branch_change_cannot_supply_loop_end_checkpoint(self):
        plan, segment, _ = self.loop_end_inputs()
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.publish_plan(9)  # A different writer; not this runtime's receipt.
            accepted = self.store.snapshot().reference
            with self.assertRaises(state.StateConflict):
                self.routes['_write_next_scene_handoff'](plan, 1, 2, segment)
        self.assertEqual(self.store.snapshot().reference, accepted)


if __name__ == '__main__':
    unittest.main()
