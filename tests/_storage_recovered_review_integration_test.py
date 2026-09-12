"""Real review HTTP cleanup/undo after independent ordinary-tree recovery."""
import asyncio
import copy
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_deferred_finalization_integration_test as fixture
from _upscale_chain_unit_test import folder_paths

chain, module, control = fixture.chain, fixture.module, fixture.control
recovered = module('storage_recovered_review')
scope = module('branch_scope').branch_scope


class RecoveredReviewTests(fixture.FinalizationTests):
    def accept_pending(self):
        first, second = self.batch()
        self.selected, self.rejected = first['revision'], second['revision']
        self.activate(self.selected,[self.selected])
        original = self.store.commit
        def lose(base, changes, **kwargs):
            result = original(base,changes,**kwargs)
            if any('/decisions/' in key for key in changes):
                raise OSError('accepted decision reply lost before cleanup')
            return result
        with patch.object(self.store,'commit',lose):
            status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,503,value)
        return first,second

    def recover(self):
        receipt = self.lab/'recovered-review-copy.json'
        control.atomic_json(receipt,dict(copy=str(self.store.project),source=str(self.source),independent_copies=True))
        self.destination = self.lab/'recovered-review-output'
        recovery = module('storage_recovery')
        journal = recovery.prepare_legacy_copy(receipt,self.destination,self.lab/'review-recovery',rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        self.root = self.destination/'h3_chains'/self.run
        self.before = self.files()
        self.original_root = self.store.snapshot().reference

    def files(self):
        return {p.relative_to(self.root).as_posix():p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}

    def legacy(self, action='finalize', *, revision=None, kept=None, proof=True,
               operation=None, snapshot=None, branch=None):
        selected = self.plan.get('_branch_id','main') if branch is None else branch
        async def body():
            return dict(action=action,run_name=self.run,token=self.token,
                candidate_revision=revision or self.selected,
                candidate_revisions=[self.selected] if kept is None else kept,
                branch_id=selected,operation_id=operation,snapshot=snapshot)
        request = SimpleNamespace(method='POST',query={},json=body,path='/checkpoint/'+action,
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'],
                     'X-H3-Ownership-Epoch':str(self.proof['epoch'])} if proof else {})
        from aiohttp import web
        handler = chain._checkpoint_retention_undo if action.startswith('undo') else chain._submit_deferred_review
        with patch.object(folder_paths,'output_directory',str(self.destination)), \
                scope(self.run,selected), patch.object(chain,'web',web):
            response = asyncio.run(handler(request))
        return response.status,json.loads(response.body)

    def assert_original_unchanged(self):
        self.assertEqual(self.store.snapshot().reference,self.original_root)
        self.assertEqual(self.source_files,{p.relative_to(self.source).as_posix():p.read_bytes()
                                          for p in self.source.rglob('*') if p.is_file()})

    def test_actual_recovered_cleanup_then_public_undo_preserves_approval_and_settings(self):
        first,second = self.accept_pending()
        self.recover()
        status,value = self.legacy('prepare')
        self.assertEqual(status,200,value)
        self.assertFalse(value['activation_required'])
        self.assertEqual(value['seed'],str(first['seed']))
        self.assertEqual(self.files(),self.before)
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1,value)
        self.assertEqual(value['reclaimed_bytes'],0)
        self.assertEqual(value['deleted_candidate_count'],0)
        self.assertEqual(len(value['undo_operations']),1)
        operation = value['undo_operations'][0]
        self.assertFalse((self.root/('checkpoints/clip_0001.'+second['revision']+'.json')).exists())
        for key in ('plan.json','workflow.json','api_prompt.json','checkpoints/clip_0001.json'):
            if key in self.before:
                self.assertEqual((self.root/key).read_bytes(),self.before[key])
        after = self.files()
        self.assertEqual(self.legacy(),(200,value))
        self.assertEqual(self.files(),after)
        status,preview = self.legacy('undo-preview',operation=operation,proof=False)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['allowed'])
        self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'],proof=False)[0],423)
        self.assertEqual(self.legacy('undo',operation=operation,snapshot='0'*64)[0],409)
        status,undone = self.legacy('undo',operation=operation,snapshot=preview['snapshot'])
        self.assertEqual(status,200,undone)
        for key,raw in self.before.items():
            self.assertEqual((self.root/key).read_bytes(),raw,key)
        restored = self.files()
        self.assertEqual(self.legacy()[0],200)
        self.assertEqual(self.files(),restored)  # Accepted old retry never deletes the restored take.
        self.assert_original_unchanged()

    def fault_retry(self, stage):
        self.accept_pending(); self.recover()
        def lose(service,name):
            if name == stage:
                raise OSError('lost '+stage)
        with patch.object(recovered.RecoveredReview,'stage',lose):
            status,value = self.legacy()
        self.assertEqual(status,503,value)
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1,value)
        self.assert_original_unchanged()

    def test_retry_prepared_before_any_move(self):
        self.fault_retry('prepared')

    def test_retry_after_one_file_moves(self):
        self.fault_retry('quarantine_file')

    def test_retry_after_quarantine_completion_reply_is_lost(self):
        self.fault_retry('committed')

    def test_retry_after_final_response_is_lost(self):
        self.fault_retry('finalized')

    def test_recovered_wrong_choice_owner_and_later_plan_change_do_not_move_files(self):
        self.accept_pending(); self.recover()
        self.assertEqual(self.legacy(proof=False)[0],423)
        self.assertEqual(self.legacy(revision=self.rejected,kept=[self.rejected])[0],409)
        self.assertEqual(self.legacy(kept=[self.selected,self.rejected])[0],409)
        self.assertEqual(self.files(),self.before)
        key = 'plan.json'
        changed = json.loads(self.before[key]); changed['shots'][0]['seed'] = 9
        (self.root/key).write_bytes(control._encode(changed))
        before = self.files()
        status,value = self.legacy()
        self.assertEqual(status,409,value)
        self.assertEqual(self.files(),before)

    def test_forged_retry_plan_cannot_move_an_unrelated_file(self):
        self.accept_pending(); self.recover()
        with patch.object(recovered.RecoveredReview,'stage',
                          side_effect=OSError('stop before first move')):
            self.assertEqual(self.legacy()[0],503)
        path = next(self.root.glob('retention/*/recovered_plan.json'))
        plan = json.loads(path.read_bytes())
        unrelated = self.root/'segments/unrelated.bin'
        unrelated.write_bytes(b'not part of any rejected take')
        key = unrelated.relative_to(self.root).as_posix()
        plan['files'].append(dict(path=key,target='retention/'+plan['operation_id']+'/files/'+control._hash(key.encode()),
                                 sha256=control._hash(unrelated.read_bytes()),size=unrelated.stat().st_size))
        path.write_bytes(control._encode(plan))
        before = self.files()
        status,value = self.legacy()
        self.assertEqual(status,409,value)
        self.assertIn('does not belong',value['error'])
        self.assertEqual(self.files(),before)

    def test_saved_processing_source_blocks_cleanup(self):
        self.accept_pending(); self.recover()
        path = self.root/'upscaled/kept-pass/checkpoints/clip_0001.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(control._encode(dict(format='h3_chain_upscale_segment_v1',run_name=self.run,
            segment=dict(source_revision=self.rejected,source_checkpoint='h3_chains/'+self.run+
                         '/checkpoints/clip_0001.'+self.rejected+'.safetensors'))))
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],0)
        self.assertTrue(value['cleanup_warnings'])
        self.assertTrue((self.root/('checkpoints/clip_0001.'+self.rejected+'.json')).exists())

    def test_recovery_after_organized_quarantine_keeps_completed_step(self):
        first,second = self.batch()
        self.selected,self.rejected = first['revision'],second['revision']
        self.activate(self.selected,[self.selected])
        retention = module('storage_retention').RuntimeRetention
        original = retention.delete_generation
        def lose(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('lost quarantine acknowledgement')
        with patch.object(retention,'delete_generation',lose):
            status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,503,value)
        self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1)
        operation = value['undo_operations'][0]
        status,preview = self.legacy('undo-preview',operation=operation,proof=False)
        self.assertEqual(status,200,preview)
        authority = self.destination/'.h3-storage-recovery/authority'
        originals = {p.relative_to(authority).as_posix():p.read_bytes() for p in authority.rglob('*') if p.is_file()}
        def lose(service,name):
            if name == 'organized_restore_file':
                raise OSError('lost first archived restore file reply')
        with patch.object(recovered.RecoveredReview,'stage',lose):
            self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],503)
        status,undone = self.legacy('undo',operation=operation,snapshot=preview['snapshot'])
        self.assertEqual(status,200,undone)
        self.assertTrue((self.root/('checkpoints/clip_0001.'+self.rejected+'.json')).is_file())
        self.assertEqual(originals,{p.relative_to(authority).as_posix():p.read_bytes() for p in authority.rglob('*') if p.is_file()})
        before = self.files()
        self.assertEqual(self.legacy()[0],200)
        self.assertEqual(self.files(),before)
        self.assert_original_unchanged()

    def test_named_branch_recovery_cleanup_and_undo_leave_original_assignments_unchanged(self):
        branches = module('working_branches').WorkingBranches
        with fixture.runtime.runtime_access(self.store,branch_writes=True):
            branch = branches(self.output,self.run).create('main','Recovered approval branch',
                {'plan_json':json.dumps({'shots':[{'id':s['id'],'prompt':s['prompt']} for s in self.plan['shots']]})},
                through_scene=1)
        with fixture.runtime.runtime_access(self.store,selected=branch['id']) as bound:
            self.plan = dict(self.plan,_branch_id=branch['id'],_storage_pin=bound.pin)
        self.accept_pending(); self.recover()
        self.assertEqual(self.legacy(branch='main')[0],404)
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        self.assertEqual(self.legacy('undo-preview',operation=operation,branch='main')[0],404)
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],200)
        for key,raw in self.before.items():
            self.assertEqual((self.root/key).read_bytes(),raw,key)

    def test_other_branch_keeps_rejected_take(self):
        self.accept_pending()
        branches = module('working_branches').WorkingBranches
        with fixture.runtime.runtime_access(self.store,branch_writes=True):
            branch = branches(self.output,self.run).create('main','Retained rejected take',
                {'plan_json':json.dumps({'shots':[{'id':s['id'],'prompt':s['prompt']} for s in self.plan['shots']]})},
                through_scene=1)
        snapshot = self.store.snapshot()
        self.store.commit(snapshot,{'branches/'+branch['id']+'/checkpoints/clip_0001.json':dict(
            data=snapshot.read('checkpoints/clip_0001.'+self.rejected+'.json'),
            scope='branch:'+branch['id'],category='branches',immutable=False)},operation_id=uuid.uuid4().hex)
        self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],0)
        self.assertTrue(any('branch' in warning for warning in value['cleanup_warnings']))
        for key,raw in self.before.items():
            self.assertEqual((self.root/key).read_bytes(),raw,key)

    def test_interrupted_undo_retry_and_occupied_destination_preserve_bytes(self):
        self.accept_pending(); self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        # An occupied late member must be discovered before any early move.
        conflict = self.root/preview['files'][-1]['path']
        conflict.write_bytes(b'new unrelated file occupying the requested destination')
        before = self.files()
        status,value = self.legacy('undo',operation=operation,snapshot=preview['snapshot'])
        self.assertEqual(status,409,value)
        self.assertEqual(before,self.files())
        conflict.unlink()  # Only this test's deliberately inserted temporary collision.
        def lose(service,name):
            if name == 'restore_file':
                raise OSError('lost first undo move reply')
        with patch.object(recovered.RecoveredReview,'stage',lose):
            status,value = self.legacy('undo',operation=operation,snapshot=preview['snapshot'])
        self.assertEqual(status,503,value)
        status,value = self.legacy('undo',operation=operation,snapshot=preview['snapshot'])
        self.assertEqual(status,200,value)
        for key,raw in self.before.items():
            self.assertEqual((self.root/key).read_bytes(),raw,key)

    def test_missing_or_damaged_rejected_metadata_stops_without_inventing_cleanup(self):
        self.accept_pending(); self.recover()
        path = self.root/('checkpoints/clip_0001.'+self.rejected+'.json')
        metadata = json.loads(path.read_bytes())
        metadata['segment']['seed'] = 1
        path.write_bytes(control._encode(metadata))
        before = self.files()
        status,value = self.legacy()
        self.assertEqual(status,409,value)
        self.assertEqual(before,self.files())
        path.unlink()  # Disposable fixture: emulate interrupted external file loss.
        before = self.files()
        status,value = self.legacy()
        self.assertEqual(status,409,value)
        self.assertIn('missing',value['error'])
        self.assertEqual(before,self.files())

    def test_rewritten_pending_and_matching_decision_cannot_replace_accepted_identity(self):
        self.accept_pending(); self.recover()
        document_key = fixture.fixture.deferred.address('main',self.token)
        document = json.loads((self.root/document_key).read_bytes())
        document['candidates'][1]['segment']['seed'] = 42
        (self.root/document_key).write_bytes(control._encode(document))
        decision_key = fixture.finalization.decision_address('main',self.token)
        decision = json.loads((self.root/decision_key).read_bytes())
        decision['request']['pending_sha256'] = control._hash(control._encode(document))
        (self.root/decision_key).write_bytes(control._encode(decision))
        before = self.files()
        status,value = self.legacy()
        self.assertEqual(status,409,value)
        self.assertIn('accepted storage',value['error'])
        self.assertEqual(before,self.files())

    def test_recovery_authority_io_error_is_retryable_without_cleanup(self):
        self.accept_pending(); self.recover()
        with patch.object(recovered.RecoveredReview,'_accepted_controls',side_effect=OSError('authority read unavailable')):
            status,value = self.legacy()
        self.assertEqual(status,503,value)
        self.assertFalse(value['retry_automatically'])
        self.assertEqual(before := self.before,self.files())
        self.assertEqual(self.legacy('prepare')[0],200)
        self.assertEqual(before,self.files())

    def test_portable_link_unlink_interruption_is_retryable_without_adopting_equal_copies(self):
        self.accept_pending(); self.recover()
        def lose(src,dst):
            os.link(src,dst)
            raise OSError('link succeeded but unlink was interrupted')
        with patch.object(recovered,'publish_new_file',lose):
            status,value = self.legacy()
        self.assertEqual(status,503,value)
        plan = json.loads(next(self.root.glob('retention/*/recovered_plan.json')).read_bytes())
        linked = next(item for item in plan['files'] if (self.root/item['target']).exists())
        self.assertTrue(os.path.samefile(self.root/linked['path'],self.root/linked['target']))
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        with patch.object(recovered,'publish_new_file',lose):
            self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],503)
        self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],200)
        for key,raw in self.before.items():
            self.assertEqual((self.root/key).read_bytes(),raw,key)

    def test_three_candidates_cross_recovery_and_restored_prior_take_is_not_deleted_again(self):
        incoming,saved = self.save_loop_scene()
        takes = [saved]
        execution = fixture.fixture.fixture
        for number in (2,3):
            with self.host(uuid.uuid4().hex):
                transition = self.execute(incoming,saved,candidate_count=3)['result'][0]
                incoming = self.next_state(incoming,transition)
            chain._ACTIVE_CANDIDATE_BATCHES.pop(incoming['candidate_batch']['batch_token'],None)
            incoming['plan'] = chain._plan_with_review_revision(incoming['plan'],1,'Candidate '+str(number),number,5)
            with execution.carriers.node_host(self.store,generation_writers=(chain.MiniMaxH3ChainSegmentSave.save,)):
                saved = chain.MiniMaxH3ChainSegmentSave().save(incoming,self.frames,
                    execution.av_latent(.2*number),self.audio(1))['result'][0]
            takes.append(saved)
        with self.host(uuid.uuid4().hex):
            self.defer(incoming,saved,candidate_count=3)
        self.token = self.record()['token']
        self.selected,self.rejected = takes[0]['revision'],takes[1]['revision']
        self.activate(self.selected,[self.selected])
        retention = module('storage_retention').RuntimeRetention
        original = retention.delete_generation
        def lose(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('lost first quarantine reply')
        with patch.object(retention,'delete_generation',lose):
            self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],503)
        self.recover()
        operation = next(path.parent.name for path in self.root.glob('retention/*/receipt.json'))
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],200)
        restored = self.root/('checkpoints/clip_0001.'+self.rejected+'.json')
        raw = restored.read_bytes()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],2)
        self.assertEqual(restored.read_bytes(),raw)
        self.assertFalse((self.root/('checkpoints/clip_0001.'+takes[2]['revision']+'.json')).exists())
        self.assertEqual(len(set(value['undo_operations'])),2)
        self.assert_original_unchanged()


if __name__ == '__main__':
    suite = unittest.TestSuite(RecoveredReviewTests(name) for name in RecoveredReviewTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
