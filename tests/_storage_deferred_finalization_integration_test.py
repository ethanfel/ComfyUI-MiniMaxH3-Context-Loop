"""Actual deferred HTTP approval/restore/cleanup against independent CPU copies."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_deferred_review_integration_test as fixture

chain, module = fixture.chain, fixture.module
runtime, control = fixture.runtime, fixture.control
finalization = module('storage_deferred_finalization')


class FinalizationTests(fixture.DeferredReviewTests):
    # The prior module separately qualifies deferral. Only discover this class's
    # new end-to-end finalization cases in the standalone runner below.
    def batch(self):
        incoming, first, second, _ = self.two_candidates()
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, second, candidate_count=2)
        self.token = self.record()['token']
        return first, second

    def call(self, action, revision, kept, *, grant=True, retention=True, proof=True):
        async def body():
            return dict(action=action, run_name=self.run, token=self.token,
                        candidate_revision=revision, candidate_revisions=kept,
                        branch_id=self.plan.get('_branch_id', 'main'))
        request = SimpleNamespace(method='POST', query={}, json=body,
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'],
                     'X-H3-Ownership-Epoch':str(self.proof['epoch'])} if proof else {})
        from aiohttp import web
        with runtime.runtime_access(self.store, selected=self.plan.get('_branch_id', 'main'),
                generation_writes=grant, handoff_writes=grant, retention_writes=retention), \
                patch.object(chain, 'web', web):
            response = asyncio.run(chain._submit_deferred_review(request))
        return response.status, json.loads(response.body)

    def activate(self, revision, kept):
        status, prepared = self.call('prepare', revision, kept)
        self.assertEqual(status, 200, prepared)
        self.assertTrue(prepared['activation_required'])
        async def body():
            return dict(run_name=self.run, branch_id=self.plan.get('_branch_id', 'main'),
                scope_start_scene=1, scope_end_scene=prepared['clip_count'],
                resume_scene=prepared['resume_scene'], activate_only=True,
                revisions=prepared['resume_revisions'])
        request = SimpleNamespace(method='POST', query={}, json=body,
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'],
                     'X-H3-Ownership-Epoch':str(self.proof['epoch'])})
        from aiohttp import web
        with runtime.runtime_access(self.store, selected=self.plan.get('_branch_id', 'main'), branch_writes=True), \
                patch.object(chain, 'web', web):
            response = asyncio.run(chain._restore_checkpoint_revisions(request))
        self.assertEqual(response.status, 200, response.body)
        return prepared

    def test_finalize_requires_selected_active_lineage_without_publishing(self):
        first, second = self.batch()
        before = self.store.snapshot().reference
        status, value = self.call('finalize', first['revision'], [first['revision']])
        self.assertEqual(status, 409, value)
        self.assertIn('Activate', value['error'])
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(len(self.records()), 1)

    def test_selected_archive_seed_prompt_and_cleanup_are_exact_and_retryable(self):
        first, second = self.batch()
        revision = first['revision']
        kept = [revision]
        prepared = self.activate(revision, kept)
        before = self.store.snapshot()
        archive = before.read('recovery_archives/'+revision+'/plan.json')
        status, result = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['seed'], str(first['seed']))
        self.assertEqual(result['scene_prompt'], prepared['scene_prompt'])
        self.assertEqual(self.store.snapshot().read('plan.json'), archive)
        self.assertEqual(result['quarantined_candidate_count'], 1, result)
        self.assertEqual(result['deleted_candidate_count'], 0)
        self.assertEqual(result['reclaimed_bytes'], 0)
        self.assertFalse(self.records())
        after = self.store.snapshot()
        rejected = 'checkpoints/clip_0001.'+second['revision']+'.json'
        self.assertNotIn(rejected, after.state['documents'])
        self.assertTrue(before.read(rejected))  # Previous pinned read remains valid.
        self.assertEqual(self.call('finalize', revision, kept), (200, result))
        self.assertEqual(after.reference, self.store.snapshot().reference)
        self.assertEqual(self.source_files, {p.relative_to(self.source).as_posix():p.read_bytes()
                                            for p in self.source.rglob('*') if p.is_file()})

    def test_keep_all_needs_no_retention_grant_but_approval_still_needs_owner_and_writer(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision'], second['revision']]
        self.activate(revision, kept)
        before = self.store.snapshot().reference
        self.assertEqual(self.call('finalize', revision, kept, proof=False)[0], 423)
        self.assertEqual(self.call('finalize', revision, kept, grant=False)[0], 400)
        status, value = self.call('finalize', revision, [revision], retention=False)
        self.assertEqual(status, 400, value)
        self.assertIn('retention', value['error'])
        self.assertEqual(before, self.store.snapshot().reference)
        status, value = self.call('finalize', revision, kept, retention=False)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['quarantined_candidate_count'], 0)
        self.assertFalse(self.records())

    def test_lost_decision_ack_keeps_fixed_choice_and_resumes_without_reactivation(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        original = self.store.commit
        def lose(base, changes, **kwargs):
            result = original(base, changes, **kwargs)
            if any('/decisions/' in key for key in changes):
                raise OSError('approval lost ack')
            return result
        with patch.object(self.store, 'commit', lose):
            status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 503, value)
        self.assertEqual(self.record()['_finalization']['status'], 'cleanup_pending')
        status, value = self.call('prepare', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertFalse(value['activation_required'])
        before = self.store.snapshot().reference
        self.assertEqual(self.call('finalize', second['revision'], [second['revision']])[0], 409)
        self.assertEqual(self.call('finalize', revision, [revision, second['revision']])[0], 409)
        self.assertEqual(before, self.store.snapshot().reference)
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['quarantined_candidate_count'], 1)

    def test_failed_archive_publication_never_starts_cleanup(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        before, original = self.store.snapshot().reference, self.store.commit
        def fail(base, changes, **kwargs):
            if any('/decisions/' in key for key in changes):
                raise OSError('approval before publication')
            return original(base, changes, **kwargs)
        with patch.object(self.store, 'commit', fail):
            status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 503, value)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertNotIn('_finalization', self.record())
        self.assertEqual(self.call('finalize', revision, kept)[0], 200)

    def test_lost_quarantine_ack_resumes_exact_cleanup_with_retired_preview(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        retention = module('storage_retention').RuntimeRetention
        original = retention.delete_generation
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('quarantine lost ack')
        with patch.object(retention, 'delete_generation', lose):
            status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 503, value)
        document = self.record()
        self.assertEqual(document['_finalization']['status'], 'cleanup_pending')
        self.assertIsNone(document['candidates'][1]['video'])
        count = len(self.store.snapshot().state['operations'])
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(len(self.store.snapshot().state['operations']), count+1)
        self.assertFalse(self.records())

    def test_lost_completion_ack_then_undo_does_not_delete_again_on_retry(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        original = self.store.commit
        def lose(base, changes, **kwargs):
            result = original(base, changes, **kwargs)
            if any('/completed/' in key for key in changes):
                raise OSError('completion lost ack')
            return result
        with patch.object(self.store, 'commit', lose):
            status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 503, value)
        self.assertFalse(self.records())
        before = self.store.snapshot().reference
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(before, self.store.snapshot().reference)
        operation = value['undo_operations'][0]
        with runtime.runtime_access(self.store, retention_writes=True) as bound:
            preview = bound.retention.preview_undo(operation)
            self.assertTrue(preview['allowed'], preview)
            bound.retention.undo(operation, preview['snapshot'], proof=self.proof)
        after_undo = self.store.snapshot().reference
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(after_undo, self.store.snapshot().reference)
        self.assertTrue(self.store.snapshot().read('checkpoints/clip_0001.'+second['revision']+'.json'))

    def test_retry_cannot_restore_settings_over_later_branch_work(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision'], second['revision']]
        self.activate(revision, kept)
        self.assertEqual(self.call('finalize', revision, kept)[0], 200)
        snapshot = self.store.snapshot()
        plan = json.loads(snapshot.read('plan.json'))
        plan['private_later_edit'] = 'Must survive the old browser request'
        self.store.commit(snapshot, {'plan.json':dict(data=control._encode(plan),
            scope='branch:main', category='branches', immutable=False)}, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 409, value)
        self.assertIn('branch changed', value['error'])
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertEqual(json.loads(self.store.snapshot().read('plan.json')), plan)

    def test_named_branch_finalization_does_not_change_original_assignments_or_archives(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Deferred approval fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            self.plan = dict(self.plan, _branch_id=branch['id'], _storage_pin=bound.pin)
        before = self.store.snapshot()
        originals = {key:before.read(key) for key in before.state['documents']
                     if key in ('plan.json', 'workflow.json', 'api_prompt.json') or key.startswith('checkpoints/')}
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        after = self.store.snapshot()
        self.assertEqual(originals, {key:after.read(key) for key in originals})
        self.assertEqual(after.read('branches/'+branch['id']+'/plan.json'),
                         after.read('recovery_archives/'+revision+'/plan.json'))
        self.assertFalse(self.records(branch=branch['id']))
        self.assertFalse(self.records())

    def test_reverse_recovery_keeps_completion_and_never_resurrects_pending_batch(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        receipt = self.lab/'finalization-copy-receipt.json'
        control.atomic_json(receipt, dict(copy=str(self.store.project), source=str(self.source), independent_copies=True))
        recovery = module('storage_recovery')
        destination = self.lab/'finalization-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, destination, self.lab/'finalization-recovery', rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        from _upscale_chain_unit_test import folder_paths
        with patch.object(folder_paths, 'output_directory', str(destination)):
            self.assertFalse(chain._list_deferred_review_records(self.run))
            recovered, _ = chain._load_deferred_review(self.run, self.token)
            self.assertEqual(recovered['_finalization']['status'], 'complete')
            self.assertEqual(recovered['_finalization']['candidate_revision'], revision)
        restored = destination/'h3_chains'/self.run
        for key in [fixture.deferred.address('main', self.token),
                    finalization.decision_address('main', self.token),
                    finalization.decision_address('main', self.token, complete=True), 'plan.json']:
            self.assertEqual((restored/key).read_bytes(), self.store.snapshot().read(key))
        self.assertEqual(result, recovery.recover_legacy_copy(journal))

    def test_incomplete_cleanup_cannot_accept_tampered_prepared_deletion(self):
        first, second = self.batch()
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        retention = module('storage_retention').RuntimeRetention
        with patch.object(retention, 'delete_generation', side_effect=OSError('before quarantine')):
            status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 503, value)
        before = self.store.snapshot().reference
        paths = list((self.store.project/'project/jobs').glob('*/'+second['revision']+'.json'))
        self.assertEqual(len(paths), 1)
        raw = paths[0].read_bytes()
        changed = json.loads(raw)
        changed['snapshot'] = '0'*64
        paths[0].write_bytes(control._encode(changed))
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 409, value)
        self.assertEqual(before, self.store.snapshot().reference)
        paths[0].write_bytes(raw)
        self.assertEqual(self.call('finalize', revision, kept)[0], 200)

    def test_rejected_take_used_by_another_branch_is_kept_with_explicit_warning(self):
        first, second = self.batch()
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = branches(self.output, self.run).create('main', 'Keep second for another cut',
                {'plan_json':json.dumps({'shots':[{'id':s['id'], 'prompt':s['prompt']}
                                                 for s in self.plan['shots']]})}, through_scene=1)
        revision, kept = first['revision'], [first['revision']]
        self.activate(revision, kept)
        status, value = self.call('finalize', revision, kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['quarantined_candidate_count'], 0)
        self.assertTrue(value['cleanup_warnings'])
        self.assertTrue(self.store.snapshot().read('checkpoints/clip_0001.'+second['revision']+'.json'))
        pointer = json.loads(self.store.snapshot().read('branches/'+branch['id']+'/checkpoints/clip_0001.json'))
        self.assertEqual(pointer['segment']['revision'], second['revision'])
        self.assertFalse(self.records())

    def test_three_candidate_cleanup_continues_after_first_quarantine_ack_is_lost(self):
        incoming, saved = self.save_loop_scene()
        takes = [saved]
        execution = fixture.fixture
        for number in (2, 3):
            with self.host(uuid.uuid4().hex):
                transition = self.execute(incoming, saved, candidate_count=3)['result'][0]
                incoming = self.next_state(incoming, transition)
            chain._ACTIVE_CANDIDATE_BATCHES.pop(incoming['candidate_batch']['batch_token'], None)
            incoming['plan'] = chain._plan_with_review_revision(incoming['plan'], 1, 'Candidate '+str(number), number, 5)
            with execution.carriers.node_host(self.store, generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
                saved = chain.MiniMaxH3ChainSegmentSave().save(incoming, self.frames,
                    execution.av_latent(.2*number), self.audio(1))['result'][0]
            takes.append(saved)
        with self.host(uuid.uuid4().hex):
            self.defer(incoming, saved, candidate_count=3)
        self.token = self.record()['token']
        kept = [takes[0]['revision']]
        self.activate(kept[0], kept)
        retention = module('storage_retention').RuntimeRetention
        original = retention.delete_generation
        def lose(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('first of two cleanup acknowledgements lost')
        with patch.object(retention, 'delete_generation', lose):
            self.assertEqual(self.call('finalize', kept[0], kept)[0], 503)
        count = len(self.store.snapshot().state['operations'])
        status, value = self.call('finalize', kept[0], kept)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['quarantined_candidate_count'], 2)
        self.assertEqual(len(set(value['undo_operations'])), 2)
        self.assertEqual(len(self.store.snapshot().state['operations']), count+2)
        self.assertFalse(self.records())


if __name__ == '__main__':
    suite = unittest.TestSuite(FinalizationTests(name) for name in FinalizationTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
