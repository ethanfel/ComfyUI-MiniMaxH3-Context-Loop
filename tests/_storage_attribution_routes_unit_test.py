"""Actual attribution service/API: immutable aliases, no media or pointer edits."""
import asyncio
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_restore_routes_unit_test as fixture
from _storage_branch_routes_unit_test import request
from storage_runtime import runtime_access
from storage_carriers import node_host, PIN_KEY
from checkpoint_manager import CheckpointGraphManager
from branch_scope import branch_scope, scoped_node
import project_ownership as ownership
import storage_state as state


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RestoreTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.parent = self.f.alternative(1)
        self.body = dict(run_name='demo', parent_scene=1, parent_revision=self.parent,
                         candidate_scene=2, candidate_revision='2'*32)

    def call(self, body=None, proof=True):
        return asyncio.run(self.f.ns['_attribute_checkpoint_revision'](request(
            body or self.body, self.f.proof if proof is True else proof)))

    def test_api_creates_one_alias_and_keeps_shared_source_files_and_assignments(self):
        before = self.store.snapshot()
        with runtime_access(self.store, branch_writes=True) as runtime:
            result = self.call()
            self.assertEqual(result['status'], 200, result)
            self.assertTrue(result['body']['created'])
            self.assertEqual(result['body']['storage_pin'], runtime.output_pin)
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        address = 'checkpoints/clip_0002.'+result['body']['revision']+'.json'
        saved = state._decode(after.read(address))
        original = state._decode(before.read('checkpoints/clip_0002.json'))
        for key in ('segment', 'checkpoint', 'generated_audio', 'prompt_file', 'seed'):
            self.assertEqual(saved['segment'][key], original['segment'][key])
        self.assertEqual(saved['adoption']['source_revision'], '2'*32)
        self.assertEqual(saved['segment']['predecessor_revision'], self.parent)
        self.assertTrue(saved['adoption']['shared_artifacts'])
        self.assertEqual(set(after.state['documents'])-set(before.state['documents']), {address})
        self.assertTrue(all(after.read(name) == before.read(name) for name in before.state['documents']))
        self.assertFalse((self.store.project/'checkpoints').exists())
        with runtime_access(self.store):
            graph = CheckpointGraphManager(self.output).graph('demo', adopt_legacy=False)
            alias = next(row for row in graph['revisions'] if row['revision'] == result['body']['revision'])
            self.assertEqual(alias['parent']['revision'], self.parent)
            self.assertTrue(alias['ready'])

    def test_fresh_retry_deduplicates_and_old_pin_rejects_duplicate_publication(self):
        with runtime_access(self.store, branch_writes=True) as runtime:
            pin = runtime.pin
            first = self.call()
            self.assertEqual(first['status'], 200, first)
        accepted = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True, pin=pin):
            stale = self.call()
            self.assertEqual(stale['status'], 409, stale)
        with runtime_access(self.store, branch_writes=True):
            repeated = self.call()
            self.assertEqual(repeated['status'], 200, repeated)
            self.assertFalse(repeated['body']['created'])
            self.assertEqual(repeated['body']['revision'], first['body']['revision'])
        self.assertEqual(self.store.snapshot().reference, accepted)

    def test_source_context_prevents_reparenting(self):
        revision = self.f.alternative(2, resolved_context_length=17, resolved_audio_context_length=0)
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            result = self.call(dict(self.body, candidate_revision=revision))
        self.assertEqual(result['status'], 400, result)
        self.assertIn('consumes predecessor', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_archived_workflow_is_verified_as_opaque_bytes_not_authority(self):
        # A legacy workflow can include Infinity/NaN in widget values. These
        # bytes were accepted by migration and must stay byte-identical.
        raw = b'{"nodes":[{"widgets_values":[Infinity,NaN,-Infinity]}]}'
        self.store.commit(self.store.snapshot(), {'workflow.json': dict(
            data=raw, scope='branch:main', category='branches', immutable=False)},
            operation_id=uuid.uuid4().hex)
        parent = self.f.alternative(1, archives={'workflow': 'h3_chains/demo/workflow.json'})
        with runtime_access(self.store, branch_writes=True):
            result = self.call(dict(self.body, parent_revision=parent))
        self.assertEqual(result['status'], 200, result)
        self.assertTrue(result['body']['created'])
        self.assertEqual(self.store.snapshot().read('workflow.json'), raw)

    def test_alt_and_non_adjacent_scene_cannot_be_attributed(self):
        revision = self.f.alternative(2, take_kind='editorial_alternate', alternate_of_revision='2'*32)
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            alt = self.call(dict(self.body, candidate_revision=revision))
            wrong_scene = self.call(dict(self.body, candidate_scene=3))
        self.assertEqual(alt['status'], 400, alt)
        self.assertIn('alternates', alt['body']['error'])
        self.assertEqual(wrong_scene['status'], 400)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_anonymous_and_read_only_requests_cannot_create_aliases(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            self.assertEqual(self.call(proof=None)['status'], 423)
            result = self.call()
        self.assertEqual(result['status'], 400, result)
        self.assertIn('read-only', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_changed_branch_or_source_scope_cannot_rebase_request(self):
        for scope in ('branch:main', 'archive:'+'2'*32):
            with runtime_access(self.store, branch_writes=True):
                # A valid separate document advancing the same explicit scope.
                self.store.commit(self.store.snapshot(), {'project_notes/'+uuid.uuid4().hex+'.json': {
                    'data': b'{}', 'scope': scope, 'category': 'legacy', 'immutable': True}},
                    operation_id=uuid.uuid4().hex)
                current = self.store.snapshot().reference
                result = self.call()
                self.assertEqual(result['status'], 409, result)
                self.assertEqual(current, self.store.snapshot().reference)

    def test_unrelated_branch_change_does_not_block_attribution(self):
        with runtime_access(self.store, branch_writes=True):
            self.store.commit(self.store.snapshot(), self.f.change('branches/'+self.named+'/plan.json',
                {'separate': True}), operation_id=uuid.uuid4().hex)
            result = self.call()
            self.assertEqual(result['status'], 200, result)

    def test_corrupt_shared_payload_prevents_alias(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True) as runtime:
            metadata = runtime.reader.read(self.store.project/'checkpoints/clip_0002.json')
            path = runtime.reader.path(metadata['segment']['checkpoint'])
            raw = path.read_bytes()
            try:
                path.write_bytes(b'bad')
                result = self.call()
                self.assertEqual(result['status'], 409, result)
            finally:
                path.write_bytes(raw)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_failure_before_publication_and_lost_ack_are_reported_safely(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True) as runtime:
            def stop(stage):
                if stage == 'document':
                    raise OSError('attribution before publication')
            runtime.branches.after_stage = stop
            result = self.call()
        self.assertEqual(result['status'], 503, result)
        self.assertEqual(before, self.store.snapshot().reference)
        real = self.store._publish
        def lost(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('attribution lost acknowledgement')
        with runtime_access(self.store, branch_writes=True), patch.object(self.store, '_publish', lost):
            result = self.call()
        self.assertEqual(result['status'], 503, result)
        self.assertFalse(result['body']['retry_automatically'])
        accepted = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            repeated = self.call()
            self.assertEqual(repeated['status'], 200, repeated)
            self.assertFalse(repeated['body']['created'])
        self.assertEqual(accepted, self.store.snapshot().reference)

    def test_direct_node_requires_grant_and_rechecks_ownership_at_publication(self):
        @scoped_node
        def assign(state):
            result = CheckpointGraphManager(self.output).attribute('demo', 1, self.parent, 2, '2'*32)
            return (dict(state, attributed=result),)
        with runtime_access(self.store) as runtime:
            incoming = dict(run_name='demo', _branch_id='main', _project_ownership=self.f.proof,
                            **{PIN_KEY: runtime.pin})
        before = self.store.snapshot().reference
        with node_host(self.store):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                assign(incoming)
        with runtime_access(self.store, ownership_writes=True):
            ownership.claim_project_ownership(self.output, 'demo', 'new-owner-attribution-1234', force=True)
        with node_host(self.store, branch_writers=(assign,)):
            with self.assertRaises(ownership.ProjectOwnershipError):
                assign(incoming)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_runtime_branch_cannot_be_switched_during_attribution(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True), branch_scope('demo', self.named):
            result = self.call()
        self.assertEqual(result['status'], 400, result)
        self.assertIn('different runtime branch', result['body']['error'])
        self.assertEqual(before, self.store.snapshot().reference)


if __name__ == '__main__':
    unittest.main(verbosity=2)
