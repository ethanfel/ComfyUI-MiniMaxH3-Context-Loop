"""Durable Review records, caller fences, old snapshots and reverse recovery."""
import copy
import ast
import asyncio
import json
import logging
import os
from contextvars import copy_context
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch
import uuid

import _storage_carriers_unit_test as fixture
from branch_scope import scoped_node
from storage_carriers import node_host
from storage_runtime import runtime_access, current_runtime
from working_branches import WorkingBranches
from checkpoint_manager import _strict_run_name
import project_ownership as ownership
import review_inventory as review
import storage_recovery as recovery
import storage_state as state


def review_routes(output):
    """Run the real reconnect handlers; substitute only transport/environment."""
    source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
    names = {'_list_pending_reviews', '_retire_superseded_review_snapshots'}
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    namespace = dict(__package__='', Any=Any, os=os, time=time,
        _output_root=lambda: str(output), _strict_run_name=_strict_run_name,
        _load_review_snapshots=review.load_review_snapshots,
        _mark_review_snapshot_decided=review.mark_review_snapshot_decided,
        _review_candidate_batch_cleanup=lambda:None, _ACTIVE_CANDIDATE_BATCHES={}, _PENDING_REVIEWS={},
        WorkingBranches=WorkingBranches, _LOG=logging.getLogger('review_inventory_tests'),
        _run_dir=lambda plan:str(output/'h3_chains'/plan['run_name']/'branches'/plan['_branch_id']),
        web=SimpleNamespace(json_response=lambda value:value))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class ReviewInventoryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CarrierTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.root = self.store.project
        self.candidates = [dict(number=1, revision='a'*32, seed='18446744073709551615',
                                created_at='2026-09-11T12:00:00Z', has_audio=True, warning='',
                                scene_prompt='must not be copied', video={'private':'excluded'})]

        @scoped_node
        def save(carrier, token='review-one', server_now=10.0):
            directory = self.directory(carrier['_branch_id'])
            path = review.write_review_snapshot(str(directory), token, 'demo', 3,
                                                self.candidates, None, server_now)
            return (dict(carrier, review_path=path),)
        @scoped_node
        def decide(carrier, token='review-one', action='approve'):
            changed = review.mark_review_snapshot_decided(str(self.directory(carrier['_branch_id'])),
                                                         token, action, 20.0)
            return (dict(carrier, decided=changed),)
        @scoped_node
        def read(carrier):
            return review.load_review_snapshots(str(self.directory(carrier['_branch_id'])))
        self.save, self.decide, self.read = save, decide, read
        with node_host(self.store):
            self.input = self.f.named_start()

    def directory(self, branch):
        return self.root if branch == 'main' else self.root/'branches'/branch

    def saved(self, carrier=None):
        with node_host(self.store, handoff_writers=(self.save,)):
            return self.save(carrier or self.input)[0]

    def test_snapshot_and_decision_preserve_seed_without_archiving_prompt_or_proof(self):
        original = copy.deepcopy(self.input)
        before = self.store.snapshot()
        saved = self.saved()
        pending_root = self.store.snapshot()
        with node_host(self.store, handoff_writers=(self.decide,)):
            pending = self.read(saved)
            decided = self.decide(saved)[0]
            result = self.read(decided)
        self.assertEqual(self.input, original)
        self.assertEqual(pending[0]['status'], 'pending')
        self.assertEqual(result[0]['status'], 'decided')
        self.assertEqual(result[0]['decision_action'], 'approve')
        self.assertEqual(result[0]['candidates'][0]['seed'], '18446744073709551615')
        self.assertNotIn('scene_prompt', result[0]['candidates'][0])
        self.assertNotIn('video', result[0]['candidates'][0])
        self.assertNotIn('_project_ownership', result[0])
        latest = self.store.snapshot()
        for address, descriptor in pending_root.state['documents'].items():
            self.assertEqual(latest.state['documents'][address], descriptor)
        for address in before.state['documents']:
            self.assertEqual(before.read(address), latest.read(address))
        self.assertFalse((self.directory(self.named)/'orchestration').exists())

    def test_old_carriers_keep_old_inventory_not_current_disk_state(self):
        saved = self.saved()
        with node_host(self.store, handoff_writers=(self.decide,)):
            decided = self.decide(saved)[0]
            self.assertEqual(self.read(self.input), [])
            self.assertEqual(self.read(saved)[0]['status'], 'pending')
            self.assertEqual(self.read(decided)[0]['status'], 'decided')

    def test_exact_pending_retry_is_noop_and_different_token_payload_is_rejected(self):
        saved = self.saved()
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(self.save,)):
            self.save(saved)
            with self.assertRaisesRegex(state.StateConflict, 'different saved record'):
                self.save(saved, server_now=11.0)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_decision_is_terminal_and_stale_decision_cannot_win_twice(self):
        saved = self.saved()
        with node_host(self.store, handoff_writers=(self.decide,)):
            decided = self.decide(saved)[0]
            self.assertTrue(decided['decided'])
            repeated = self.decide(decided, action='stop')[0]
            self.assertFalse(repeated['decided'])
            with self.assertRaises(state.StateConflict):
                self.decide(saved, action='retry')
        self.assertEqual(self.store.snapshot().reference, decided['_storage_pin']['root'])

    def test_readonly_branch_grants_and_json_flags_do_not_authorize_review_write(self):
        before = self.store.snapshot().reference
        for grant in ({}, {'branch_writers':(self.save,)}, {'generation_writers':(self.save,)}):
            with node_host(self.store, **grant), self.assertRaisesRegex(ValueError, 'handoff writes'):
                self.save(dict(self.input, handoff_writes=True))
        self.assertEqual(self.store.snapshot().reference, before)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            review.write_review_snapshot(str(self.root), 'bad', 'demo', 1, [], None, 1)

    def test_runtime_grant_cannot_write_another_branch(self):
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                review.write_review_snapshot(str(self.root), 'bad', 'demo', 1, [], None, 1)

    def test_missing_and_conflicting_project_identity_fail_without_publication(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            with self.assertRaisesRegex(ValueError, 'identity'):
                review.write_review_snapshot(str(self.directory(self.named)), 'bad', 'other', 1, [], None, 1)
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                review.load_review_snapshots(str(self.directory('f'*32)))
        self.assertEqual(self.store.snapshot().reference, before)

    def claim(self, owner, force=False):
        with runtime_access(self.store, ownership_writes=True):
            return ownership.claim_project_ownership(self.output, 'demo', owner, force=force)

    def test_ownership_rechecked_after_review_wait_and_at_snapshot_commit(self):
        owner = 'review-owner-1234567890'
        claimed = self.claim(owner)
        carrier = copy.deepcopy(self.input)
        carrier['plan']['_project_ownership'] = dict(owner_id=owner, epoch=claimed['epoch'])
        with self.assertRaises(ownership.ProjectOwnershipError):
            self.saved()
        saved = self.saved(carrier)
        self.claim('review-other-1234567890', True)
        before = self.store.snapshot().reference
        with node_host(self.store, handoff_writers=(self.decide,)), self.assertRaises(ownership.ProjectOwnershipError):
            self.decide(saved)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_pending_and_decided_in_one_execution_use_its_acknowledged_root(self):
        @scoped_node
        def both(carrier):
            directory = str(self.directory(self.named))
            review.write_review_snapshot(directory, 'one-op', 'demo', 3, self.candidates, None, 10)
            self.assertTrue(review.mark_review_snapshot_decided(directory, 'one-op', 'interrupted', 20))
            return (carrier,)
        with node_host(self.store, handoff_writers=(both,)):
            result = both(self.input)[0]
            records = self.read(result)
        self.assertEqual(records[0]['decision_action'], 'interrupted')

    def test_write_failures_and_lost_ack_are_never_returned_as_success(self):
        before = self.store.snapshot().reference
        with patch.object(self.store, 'commit', side_effect=OSError('write failed')):
            with self.assertRaisesRegex(OSError, 'write failed'):
                self.saved()
        self.assertEqual(self.store.snapshot().reference, before)
        real = self.store.commit
        def fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('lost acknowledgement')
        with patch.object(self.store, 'commit', fail), self.assertRaisesRegex(OSError, 'acknowledgement'):
            self.saved()
        with node_host(self.store):
            fresh = self.f.named_start()
            self.assertEqual(len(self.read(fresh)), 1)
        accepted = self.store.snapshot().reference
        self.saved(fresh)
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_imported_immutable_snapshots_are_decided_without_rewriting_archive(self):
        directory = self.directory(self.named)
        address = (directory/'orchestration/review_imported.json').relative_to(self.root).as_posix()
        # Legacy named-branch snapshots did not embed their branch ID.
        value = dict(format=review.REVIEW_SNAPSHOT_FORMAT_VERSION, token='imported', run_name='demo',
                     scene=3, status='pending', candidates=[], deadline=None, server_now=1.0)
        # Keep original whitespace, missing optional fields and bytes exactly.
        raw = (json.dumps(value, indent=3)+'\r\n').encode()
        self.store.commit(self.store.snapshot(), {address:dict(data=raw, scope='archive:legacy',
            category='legacy', immutable=True)}, operation_id=uuid.uuid4().hex)
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            self.assertTrue(review.mark_review_snapshot_decided(str(directory), 'imported', 'superseded', 30))
        latest = self.store.snapshot()
        self.assertEqual(latest.read(address), raw)
        with runtime_access(self.store, selected=self.named):
            self.assertEqual(review.load_review_snapshots(str(directory))[0]['decision_action'], 'superseded')

    def test_accepted_blob_corruption_is_not_reported_as_empty_inventory(self):
        saved = self.saved()
        address = Path(saved['review_path']).relative_to(self.root).as_posix()
        descriptor = self.store.snapshot().state['documents'][address]
        blob = self.root/descriptor['file']['path']
        blob.write_bytes(b'corrupt accepted review')
        with node_host(self.store), self.assertRaises(ValueError):
            self.read(saved)

    def test_missing_accepted_decision_cannot_resurrect_pending_review(self):
        saved = self.saved()
        with node_host(self.store, handoff_writers=(self.decide,)):
            decided = self.decide(saved)[0]
        address = Path(review._decision_path(str(self.directory(self.named)), 'review-one')).relative_to(self.root).as_posix()
        descriptor = self.store.snapshot().state['documents'][address]
        blob = self.root/descriptor['file']['path']
        blob.rename(blob.with_suffix('.retained'))
        with node_host(self.store, handoff_writers=(self.decide,)):
            with self.assertRaisesRegex(state.StateConflict, 'bytes are missing'):
                self.read(decided)
            with self.assertRaisesRegex(state.StateConflict, 'bytes are missing'):
                self.decide(decided)

    def test_wrong_decision_digest_is_not_an_approval_or_a_pending_review(self):
        saved = self.saved()
        path = review._decision_path(str(self.directory(self.named)), 'review-one')
        address = Path(path).relative_to(self.root).as_posix()
        bad = dict(format=review.DECISION_FORMAT_VERSION, run_name='demo', _branch_id=self.named,
                   token='review-one', snapshot_sha256='0'*64, decision_action='approve', decided_at=20)
        self.store.commit(self.store.snapshot(), {address:dict(data=state._encode(bad), scope='archive:legacy',
            category='legacy', immutable=True)}, operation_id=uuid.uuid4().hex)
        with runtime_access(self.store, selected=self.named), self.assertRaisesRegex(ValueError, 'does not match'):
            review.load_review_snapshots(str(self.directory(self.named)))

    def test_epoch_changes_and_expired_worker_contexts_revoke_review_services(self):
        with runtime_access(self.store, selected=self.named, handoff_writes=True) as bound:
            service = bound.reviews
            captured = copy_context()
        with self.assertRaisesRegex(ValueError, 'escaped'):
            captured.run(lambda: service.working_root(self.directory(self.named)))
        self.store.advance_epoch(self.store.snapshot(), operation_id=uuid.uuid4().hex)
        with node_host(self.store, handoff_writers=(self.save,)), self.assertRaisesRegex(state.StateConflict, 'epoch'):
            self.save(self.input)

    def test_tokens_live_objects_and_nonfinite_values_fail(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            directory = str(self.directory(self.named))
            for token in ('../escape', 'x'*129, 'bad/slash'):
                with self.assertRaises(ValueError):
                    review.write_review_snapshot(directory, token, 'demo', 3, [], None, 1)
            with self.assertRaises(ValueError):
                review.write_review_snapshot(directory, 'object', 'demo', 3, [{'seed':object()}], None, 1)
            with self.assertRaises(ValueError):
                review.write_review_snapshot(directory, 'nan', 'demo', 3, [], float('nan'), 1)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_batch_inventory_reads_saved_handoffs_at_exact_pin(self):
        old = copy.deepcopy(self.input)
        from handoff_state import HandoffStore
        with runtime_access(self.store, handoff_writes=True):
            record = HandoffStore(self.output).create('demo', handoff_id='batch', action='next_candidate',
                scene=3, seed=18446744073709551615, working_branch_id='main')
        with runtime_access(self.store):
            self.assertEqual(review.load_batch_inventory(str(self.root)), [record])
        with runtime_access(self.store, selected=self.named, pin=old['_storage_pin']):
            self.assertEqual(review.load_batch_inventory(str(self.root)), [])

    def test_actual_reverse_recovery_keeps_decisions_and_pending_records(self):
        saved = self.saved()
        with node_host(self.store, handoff_writers=(self.decide, self.save)):
            decided = self.decide(saved)[0]
            another = self.save(decided, token='still-pending')[0]
            expected = self.read(another)
        before = self.store.snapshot()
        with tempfile.TemporaryDirectory(dir=self.f.f.f.f.lab) as raw:
            lab = Path(raw)
            receipt = self.f.f.f.f.lab/'review-copy.json'
            state.atomic_json(receipt, dict(copy=str(self.root), source=str(self.f.f.f.f.root), independent_copies=True))
            journal = recovery.prepare_legacy_copy(receipt, lab/'output', lab/'recovery', rehearsal_store=self.store)
            result = recovery.recover_legacy_copy(journal)
            recovered = lab/'output/h3_chains/demo/branches'/self.named
            self.assertEqual(review.load_review_snapshots(str(recovered)), expected)
            self.assertFalse(review.mark_review_snapshot_decided(str(recovered), 'review-one', 'stop', 40))
            self.assertTrue(result['source_unchanged'])
            self.assertEqual(result, recovery.recover_legacy_copy(journal))
        self.assertEqual(self.store.snapshot().reference, before.reference)

    def test_actual_reconnect_route_reads_both_branches_from_one_pin(self):
        with runtime_access(self.store, handoff_writes=True):
            review.write_review_snapshot(str(self.root), 'main-pending', 'demo', 1, [], None, 1)
        saved = self.saved()
        with node_host(self.store, handoff_writers=(self.decide, self.save)):
            decided = self.decide(saved)[0]
            latest = self.save(decided, token='named-pending')[0]
        routes = review_routes(self.output)
        with runtime_access(self.store, selected=self.named, pin=saved['_storage_pin']) as bound:
            old = asyncio.run(routes['_list_pending_reviews'](None))
            self.assertEqual(old['storage_pin'], bound.pin)
        with runtime_access(self.store, selected=self.named, pin=latest['_storage_pin']) as bound:
            new = asyncio.run(routes['_list_pending_reviews'](None))
            self.assertEqual(new['storage_pin'], bound.pin)
        self.assertEqual({item['token'] for item in old['reviews']}, {'main-pending', 'review-one'})
        self.assertEqual({item['token'] for item in new['reviews']}, {'main-pending', 'named-pending'})
        for item in new['reviews']:
            self.assertTrue(item['durable'])
            self.assertFalse(item['actionable'])
        self.assertEqual({item['_branch_id'] for item in new['reviews']}, {'main', self.named})
        self.assertFalse((self.root/'branches').exists())

    def test_reconnect_deduplicates_live_review_and_reports_unhosted_project(self):
        saved = self.saved()
        routes = review_routes(self.output)
        routes['_ACTIVE_CANDIDATE_BATCHES']['live'] = dict(public={
            'token':'live', 'run_name':'demo', '_branch_id':self.named, 'clip_index':3})
        routes['_ACTIVE_CANDIDATE_BATCHES']['other'] = dict(public={
            'token':'other', 'run_name':'other', '_branch_id':'main', 'clip_index':1})
        with runtime_access(self.store, selected=self.named, pin=saved['_storage_pin']):
            result = asyncio.run(routes['_list_pending_reviews'](None))
        self.assertEqual([item['token'] for item in result['reviews']], ['live'])
        routes['_ACTIVE_CANDIDATE_BATCHES'].clear()
        unhosted = asyncio.run(routes['_list_pending_reviews'](None))
        self.assertEqual(unhosted['reviews'], [])
        self.assertEqual(unhosted['unavailable_runs'][0]['run_name'], 'demo')
        self.assertIn('Unsupported', unhosted['unavailable_runs'][0]['error'])

    def test_superseding_review_retires_only_matching_scene_and_branch(self):
        with runtime_access(self.store, handoff_writes=True):
            review.write_review_snapshot(str(self.root), 'main-pending', 'demo', 3, [], None, 1)
        directory = str(self.directory(self.named))
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            for token, scene in (('old',3), ('chosen',3), ('another-scene',4)):
                review.write_review_snapshot(directory, token, 'demo', scene, [], None, 1)
        routes = review_routes(self.output)
        with runtime_access(self.store, selected=self.named, handoff_writes=True):
            routes['_retire_superseded_review_snapshots'](directory, 'demo', 3, 'chosen')
        with runtime_access(self.store, selected=self.named):
            results = {item['token']:item for item in review.load_review_snapshots(directory)}
            self.assertEqual(results['old']['decision_action'], 'superseded')
            self.assertEqual(results['chosen']['status'], 'pending')
            self.assertEqual(results['another-scene']['status'], 'pending')
            self.assertEqual(review.load_review_snapshots(str(self.root))[0]['status'], 'pending')


if __name__ == '__main__':
    unittest.main(verbosity=2)
