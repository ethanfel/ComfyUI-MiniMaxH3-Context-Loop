"""Delivery-only control commits, historical pins, failures and exact retry."""
import copy
import unittest
from unittest.mock import patch
import uuid

import _storage_generation_unit_test as fixture
from storage_runtime import runtime_access
import storage_state as state
import project_ownership as ownership


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.GenerationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof = self.f.store, self.f.proof
        saved = [state._decode(self.store.snapshot().read('checkpoints/clip_%04d.json' % i))['segment']
                 for i in (1,2)]
        for segment in saved:
            segment.setdefault('delivered_frames', 1)  # Minimal legacy fixture lacks timing fields.
        self.plan = dict(run_name='demo', _project_ownership=self.proof,
                         shots=saved, plan_hash='exact-plan', compatibility={})
        self.manifest = dict(format='h3_chain_partial_manifest_v3', run_name='demo',
            plan_hash='exact-plan', compatibility={}, clip_count=1, planned_clip_count=2,
            last_completed_clip=1, segments=saved[:1], total_delivered_frames=saved[0]['delivered_frames'],
            duration_seconds=saved[0]['delivered_frames']/24.0)

    def publish(self, *, pin=None, selected='main', plan=None, manifest=None):
        with runtime_access(self.store, pin=pin, selected=selected, generation_writes=True) as runtime:
            result = runtime.delivery.publish(plan or self.plan, manifest or self.manifest)
            return result, runtime.output_pin

    def test_only_delivery_controls_change_and_old_root_remains_readable(self):
        before = self.store.snapshot()
        result, pin = self.publish()
        after = self.store.snapshot()
        old, new = before.state['documents'], after.state['documents']
        self.assertTrue(all(new[k] == d for k,d in old.items()))
        self.assertEqual(set(new)-set(old), {result['address'],'deliveries/main/latest.json'})
        self.assertEqual(pin['root'], after.reference)
        self.assertEqual(after.state['scope_revisions']['branch:main'], before.state['scope_revisions']['branch:main'])
        before.verify()

    def test_caller_ownership_is_returned_but_not_archived_or_replaced(self):
        result, _pin = self.publish()
        self.assertEqual(result['manifest']['_project_ownership'], self.proof)
        self.assertIsNot(result['manifest']['_project_ownership'], self.proof)
        saved = state._decode(self.store.snapshot().read(result['address']))
        self.assertNotIn('_project_ownership', saved)
        self.assertNotIn('_project_ownership', self.manifest)
        with self.assertRaisesRegex(ValueError, 'caller ownership'):
            self.publish(manifest=dict(self.manifest, _project_ownership=None))
        from storage_delivery import delivery_carrier
        self.assertNotIn('_project_ownership', delivery_carrier(saved, {}))
        self.assertNotIn('_project_ownership', delivery_carrier(dict(saved,_project_ownership=self.proof), {}))
        self.assertIsNone(delivery_carrier(saved, {'_project_ownership':None})['_project_ownership'])
        stale = dict(self.proof, epoch=0)
        self.assertEqual(delivery_carrier(saved, {'_project_ownership':stale})['_project_ownership'], stale)

    def test_wrong_seed_count_range_revision_or_branch_rejected_without_publication(self):
        candidates = []
        for key, value in [('clip_count',2),('last_completed_clip',2),('planned_clip_count',4),
                           ('total_delivered_frames',999),('_branch_id',self.f.named)]:
            candidates.append(dict(self.manifest, **{key:value}))
        for key, value in [('seed',99),('index',2),('revision','f'*32)]:
            bad = copy.deepcopy(self.manifest)
            bad['segments'][0][key] = value
            candidates.append(bad)
        before = self.store.snapshot().reference
        for value in candidates:
            with self.assertRaises((ValueError,KeyError)):
                self.publish(manifest=value)
        self.assertEqual(self.store.snapshot().reference,before)

    def test_interruption_can_retry_without_replacing_older_manifest(self):
        self.publish()
        before = self.store.snapshot()
        with runtime_access(self.store,generation_writes=True) as runtime:
            pin = runtime.pin
            def stop(stage):
                if stage == 'root':
                    raise OSError('interrupted delivery')
            runtime.delivery.after_stage = stop
            with self.assertRaises(OSError):
                runtime.delivery.publish(self.plan,self.manifest)
        self.assertEqual(self.store.snapshot().reference,before.reference)
        self.publish(pin=pin)
        self.assertEqual(self.store.snapshot().state['generation'],before.state['generation']+1)

    def test_lost_ack_returns_original_receipt_without_duplicate_delivery(self):
        real = self.store._publish
        def lost(*args,**kwargs):
            real(*args,**kwargs)
            raise OSError('lost delivery acknowledgement')
        with runtime_access(self.store,generation_writes=True) as runtime:
            pin = runtime.pin
            with patch.object(self.store,'_publish',lost), self.assertRaises(OSError):
                runtime.delivery.publish(self.plan,self.manifest)
        accepted = self.store.snapshot().reference
        result, output_pin = self.publish(pin=pin)
        self.assertEqual(output_pin['root'],accepted)
        self.assertEqual(self.store.snapshot().reference,accepted)
        self.assertEqual(self.store.committed_snapshot(result['receipt']).reference,accepted)

    def test_unrelated_branch_change_allowed_selected_branch_change_rejected(self):
        for selected,allowed in [(self.f.named,True),('main',False)]:
            with runtime_access(self.store,generation_writes=True) as runtime:
                address = ('' if selected == 'main' else 'branches/'+selected+'/')+'plan.json'
                self.store.commit(self.store.snapshot(),self.f.f.change(address,{'new':True}),
                                  operation_id=uuid.uuid4().hex)
                before = self.store.snapshot().reference
                if allowed:
                    runtime.delivery.publish(self.plan,self.manifest)
                else:
                    with self.assertRaises(state.StateConflict):
                        runtime.delivery.publish(self.plan,self.manifest)
                    self.assertEqual(self.store.snapshot().reference,before)

    def test_missing_grant_and_wrong_owner_cannot_publish(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store,branch_writes=True) as runtime:
            with self.assertRaisesRegex(ValueError,'generation writes'):
                runtime.delivery.publish(self.plan,self.manifest)
        with self.assertRaises(ownership.ProjectOwnershipError):
            self.publish(plan=dict(self.plan,_project_ownership=None))
        self.assertEqual(self.store.snapshot().reference,before)

    def test_saved_checksum_must_match_the_accepted_file_index_before_delivery(self):
        # Simulate imported metadata that disagrees with independently hashed
        # media. Accepted catalogue bytes alone do not validate that relationship.
        revision = self.f.f.alternative(1,checkpoint_sha256='0'*64)
        metadata = state._decode(self.store.snapshot().read('checkpoints/clip_0001.'+revision+'.json'))
        self.store.commit(self.store.snapshot(),self.f.f.change('checkpoints/clip_0001.json',metadata),
                          operation_id=uuid.uuid4().hex)
        segment = metadata['segment']
        segment.setdefault('delivered_frames',1)
        manifest = dict(self.manifest,segments=[segment])
        before = self.store.snapshot().reference
        with self.assertRaisesRegex(state.StateConflict,'saved checksum'):
            self.publish(manifest=manifest)
        self.assertEqual(self.store.snapshot().reference,before)


if __name__ == '__main__':
    unittest.main()
