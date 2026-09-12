"""Review choices and undo must survive another import, not just one recovery."""
import json
import re
import shutil
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_recovered_review_integration_test as fixture
from _upscale_chain_unit_test import folder_paths

chain, module, control = fixture.chain, fixture.module, fixture.control
runtime = module('storage_runtime')


class RoundtripTests(fixture.RecoveredReviewTests):
    def imported_undo(self, operation, snapshot=None, *, proof=True, grant=True, branch=None):
        async def body():
            return dict(operation_id=operation, snapshot=snapshot, run_name=self.run)
        request = SimpleNamespace(json=body, path='/checkpoint/'+('undo-preview' if snapshot is None else 'undo'),
            headers={'X-H3-Workflow-Owner':self.proof['owner_id'],
                     'X-H3-Ownership-Epoch':str(self.proof['epoch'])} if proof else {})
        from aiohttp import web
        with runtime.runtime_access(self.store, selected=branch or self.plan.get('_branch_id','main'),
                                    retention_writes=grant), patch.object(chain,'web',web):
            response = asyncio.run(chain._checkpoint_retention_undo(request))
        return response.status,json.loads(response.body)

    def recovered_quarantine(self):
        self.accept_pending(); self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        self.quarantine = value['undo_operations'][0]
        self.quarantine_plan = json.loads((self.root/('retention/'+self.quarantine+'/recovered_plan.json')).read_bytes())
        self.reimport()
        return self.quarantine

    def reimport(self):
        self.first_store = self.store
        self.first_destination = self.destination
        self.reimport_source = self.root
        copied = self.lab/'reimport-copy-output'/'h3_chains'/self.run
        shutil.copytree(self.root,copied,copy_function=shutil.copy2)
        receipt = self.lab/'reimport-receipt.json'
        control.atomic_json(receipt,dict(source=str(self.root),copy=str(copied),independent_copies=True))
        documents,targets = {},{}
        old = self.store.snapshot().state['documents']
        for path in copied.rglob('*'):
            if not path.is_file() or path.name.endswith('.lock'):
                continue
            key = path.relative_to(copied).as_posix()
            if path.suffix in ('.json','.txt'):
                contract = {k:old[key][k] for k in ('scope','category','immutable')} if key in old else dict(
                    scope='archive:recovered_review',category='recovery',immutable=True)
                documents[key] = dict(source=key,sha256=control._hash(path.read_bytes()),**contract)
            else:
                targets[key] = dict(target='project/optional/reimport/'+control._hash(key.encode())+'.bin',
                                    scope='archive:reimport',immutable=True)
        self.output = self.lab/'reimport-output'
        imported = control.create_control_rehearsal(receipt,self.output,documents,commit_protocol='immutable_slots_v1')
        access = control.control_rehearsal_access(imported.project)
        access.__enter__()
        self.addCleanup(access.__exit__,None,None,None)
        migration = module('storage_project_migration')
        journal = migration.prepare_join(receipt,imported,self.lab/'reimport-join',targets)
        migration.join_payloads(journal)
        self.store = module('storage_project').ProjectStore(imported.project)
        folder_paths.output_directory = str(self.output)
        with runtime.runtime_access(self.store,ownership_writes=True):
            claimed = module('project_ownership').claim_project_ownership(self.output,self.run,self.proof['owner_id'])
        self.proof = dict(owner_id=self.proof['owner_id'],epoch=claimed['epoch'])

    def test_pending_accepted_choice_survives_reimport_without_second_activation(self):
        self.accept_pending(); self.recover(); self.reimport()
        before = self.store.snapshot()
        status,value = self.call('prepare',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertFalse(value['activation_required'])
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1)
        after = self.store.snapshot()
        for key in ('plan.json','checkpoints/clip_0001.json'):
            self.assertEqual(before.read(key),after.read(key))
        self.assertEqual(self.call('finalize',self.selected,[self.selected]),(status,value))
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_completed_organized_cleanup_reimport_does_not_republish_approval(self):
        self.accept_pending()
        self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],200)
        self.recover(); self.reimport()
        before = self.store.snapshot().reference
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1)
        self.assertEqual(before,self.store.snapshot().reference)
        self.assertFalse(self.records())

    def test_completed_recovered_cleanup_reimport_does_not_delete_or_republish(self):
        self.accept_pending(); self.recover()
        self.assertEqual(self.legacy()[0],200)
        self.reimport()
        before = self.store.snapshot().reference
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1)
        self.assertEqual(before,self.store.snapshot().reference)
        self.assertFalse(self.records())

    def test_imported_approval_lost_ack_retry_does_not_reactivate_or_rewrite(self):
        self.accept_pending(); self.recover(); self.reimport()
        original = self.store.commit
        def lose(base,changes,**kwargs):
            result = original(base,changes,**kwargs)
            if any('/imported-decisions/' in key for key in changes):
                raise OSError('imported approval binding lost reply')
            return result
        with patch.object(self.store,'commit',lose):
            status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,503,value)
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['quarantined_candidate_count'],1)

    def test_imported_choice_cannot_replace_later_branch_settings(self):
        self.accept_pending(); self.recover(); self.reimport()
        current = self.store.snapshot()
        plan = json.loads(current.read('plan.json'))
        plan['shots'][0]['seed'] = 9
        self.store.commit(current,{'plan.json':dict(data=control._encode(plan),scope='branch:main',
            category='branches',immutable=False)},operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,409,value)
        self.assertEqual(before,self.store.snapshot().reference)
        self.assertEqual(json.loads(self.store.snapshot().read('plan.json')),plan)

    def test_recovered_quarantine_public_undo_after_reimport(self):
        operation = self.recovered_quarantine()
        before = self.store.snapshot()
        status,preview = self.imported_undo(operation,proof=False,grant=False)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['allowed'],preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'],proof=False)[0],423)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'],grant=False)[0],400)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        after = self.store.snapshot()
        self.assertEqual(before.read('plan.json'),after.read('plan.json'))
        self.assertEqual(before.read('checkpoints/clip_0001.json'),after.read('checkpoints/clip_0001.json'))
        view = module('storage_project_reads').ProjectReadView(self.store,base=after)
        with view.operation():
            for item in self.quarantine_plan['files']:
                self.assertEqual(view.path('h3_chains/'+self.run+'/'+item['path']).read_bytes(),
                                 self.before[item['path']],item['path'])
        self.assertEqual(self.imported_undo(operation,preview['snapshot']),(status,value))
        self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],200)
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_imported_undo_staging_interruption_is_retryable(self):
        operation = self.recovered_quarantine()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        before = self.store.snapshot().reference
        retention = module('storage_retention').RuntimeRetention
        original = retention.undo
        def interrupted(service,*args,**kwargs):
            def fail(stage):
                if stage == 'restore_staged':
                    raise OSError('interrupted after independent staging')
            service.after_stage = fail
            return original(service,*args,**kwargs)
        with patch.object(retention,'undo',interrupted):
            status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,503,value)
        self.assertEqual(before,self.store.snapshot().reference)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)

    def test_imported_undo_lost_publication_reply_is_idempotent(self):
        operation = self.recovered_quarantine()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        original = self.store._commit_changes
        def lose(base,changes,**kwargs):
            result = original(base,changes,**kwargs)
            if any(key.endswith('/recovered_undo.json') for key in changes):
                raise OSError('restore accepted but acknowledgement lost')
            return result
        with patch.object(self.store,'_commit_changes',lose):
            status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,503,value)
        after = self.store.snapshot().reference
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        self.assertEqual(after,self.store.snapshot().reference)

    def test_imported_undo_stale_preview_preserves_later_branch_edit(self):
        operation = self.recovered_quarantine()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        before = self.store.snapshot()
        plan = json.loads(before.read('plan.json')); plan['later_note'] = 'keep this change'
        self.store.commit(before,{'plan.json':dict(data=control._encode(plan),scope='branch:main',
            category='branches',immutable=False)},operation_id=uuid.uuid4().hex)
        changed = self.store.snapshot()
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],409)
        self.assertEqual(changed.reference,self.store.snapshot().reference)
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        self.assertEqual(changed.read('plan.json'),self.store.snapshot().read('plan.json'))

    def test_imported_undo_refuses_occupied_destination_before_staging(self):
        operation = self.recovered_quarantine()
        key = 'checkpoints/clip_0001.'+self.rejected+'.json'
        self.store.commit(self.store.snapshot(),{key:dict(data=self.before[key],scope='archive:'+self.rejected,
            category='takes',immutable=True)},operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        with patch.object(self.store,'stage_payload',side_effect=AssertionError('must not start staging')):
            status,value = self.imported_undo(operation)
        self.assertEqual(status,409,value)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_imported_undo_rejects_changed_quarantine_payload(self):
        operation = self.recovered_quarantine()
        before = self.store.snapshot()
        key = self.quarantine_plan['files'][-1]['target']
        path = self.store.payload_path(before,key,verify=True)
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0]^1])+raw[1:])
        status,value = self.imported_undo(operation)
        self.assertNotEqual(status,200,value)
        self.assertEqual(before.reference,self.store.snapshot().reference)

    def test_importing_an_already_restored_quarantine_does_not_restore_twice(self):
        self.accept_pending(); self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.legacy('undo',operation=operation,snapshot=preview['snapshot'])[0],200)
        self.reimport()
        before = self.store.snapshot().reference
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['restored'])
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],200)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_imported_undo_survives_another_ordinary_recovery(self):
        operation = self.recovered_quarantine()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],200)
        accepted = self.store.snapshot().reference
        receipt = self.lab/'second-recovery-receipt.json'
        control.atomic_json(receipt,dict(copy=str(self.store.project),source=str(self.source),independent_copies=True))
        self.destination = self.lab/'second-recovery-output'
        recovery = module('storage_recovery')
        journal = recovery.prepare_legacy_copy(receipt,self.destination,self.lab/'second-recovery-journal',
                                               rehearsal_store=self.store)
        result = recovery.recover_legacy_copy(journal)
        self.assertTrue(result['source_unchanged'])
        self.root = self.destination/'h3_chains'/self.run
        for item in self.quarantine_plan['files']:
            self.assertEqual((self.root/item['path']).read_bytes(),self.before[item['path']])
            self.assertFalse((self.root/item['target']).exists())
        status,preview = self.legacy('undo-preview',operation=operation)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['restored'])
        self.assertEqual(self.legacy()[0],200)
        self.assertEqual(accepted,self.store.snapshot().reference)

    def unfinished_recovered_finalization(self):
        self.accept_pending(); self.recover()
        def lose(service,stage):
            if stage == 'committed':
                raise OSError('quarantine completed before final reply')
        with patch.object(fixture.recovered.RecoveredReview,'stage',lose):
            self.assertEqual(self.legacy()[0],503)
        operation = json.loads(next(self.root.glob('retention/*/recovered_plan.json')).read_bytes())['operation_id']
        self.reimport()
        return operation

    def test_reimport_resumes_finalization_after_completed_ordinary_quarantine(self):
        operation = self.unfinished_recovered_finalization()
        before = self.store.snapshot()
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['undo_operations'],[operation])
        after = self.store.snapshot()
        self.assertEqual(before.read('plan.json'),after.read('plan.json'))
        self.assertEqual(self.call('finalize',self.selected,[self.selected]),(status,value))
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_reimported_pending_completion_keeps_an_undone_rejection(self):
        operation = self.unfinished_recovered_finalization()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],200)
        before = self.store.snapshot()
        key = 'checkpoints/clip_0001.'+self.rejected+'.json'
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        after = self.store.snapshot()
        self.assertEqual(before.read(key),after.read(key))
        self.assertEqual(value['undo_operations'],[operation])
        self.assertEqual(self.call('finalize',self.selected,[self.selected]),(status,value))
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_named_branch_imported_undo_cannot_change_original_plan_or_pointers(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store,branch_writes=True):
            branch = branches(self.output,self.run).create('main','Recovered undo fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'],'prompt':s['prompt']} for s in self.plan['shots']]})},
                through_scene=1)
        with runtime.runtime_access(self.store,selected=branch['id']) as bound:
            self.plan = dict(self.plan,_branch_id=branch['id'],_storage_pin=bound.pin)
        operation = self.recovered_quarantine()
        before = self.store.snapshot()
        selected = {key:before.read(key) for key in before.state['documents']
                    if key == 'plan.json' or re.fullmatch(r'checkpoints/clip_\d{4}\.json',key)}
        status,value = self.imported_undo(operation,branch='main')
        self.assertNotEqual(status,200,value)
        self.assertEqual(before.reference,self.store.snapshot().reference)
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],200)
        after = self.store.snapshot()
        self.assertEqual(selected,{key:after.read(key) for key in selected})
        key = 'branches/'+branch['id']+'/plan.json'
        self.assertEqual(before.read(key),after.read(key))

    def test_imported_forged_quarantine_cannot_restore_unrelated_file(self):
        self.accept_pending(); self.recover()
        status,value = self.legacy()
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        key = 'retention/'+operation+'/recovered_plan.json'
        plan = json.loads((self.root/key).read_bytes())
        logical,raw = 'segments/unrelated.bin',b'not an artifact of the rejected take'
        target = 'retention/'+operation+'/files/'+control._hash(logical.encode())
        (self.root/target).write_bytes(raw)
        plan['files'].append(dict(path=logical,target=target,sha256=control._hash(raw),size=len(raw)))
        (self.root/key).write_bytes(control._encode(plan))
        (self.root/('retention/'+operation+'/recovered_complete.json')).write_bytes(control._encode(
            dict(format=fixture.recovered.FORMAT,plan_sha256=control._hash(control._encode(plan)))))
        self.reimport()
        before = self.store.snapshot().reference
        status,value = self.imported_undo(operation)
        self.assertEqual(status,409,value)
        self.assertIn('does not belong',value['error'])
        self.assertEqual(before,self.store.snapshot().reference)

    def organized_quarantine_import(self):
        self.accept_pending()
        original = self.store.snapshot()
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        operation = value['undo_operations'][0]
        key = 'checkpoints/clip_0001.'+self.rejected+'.json'
        metadata = original.read(key)
        self.recover(); self.reimport()
        return operation,key,metadata

    def test_organized_quarantine_public_undo_after_project_only_reimport(self):
        operation,key,metadata = self.organized_quarantine_import()
        before = self.store.snapshot()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['allowed'],preview)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        after = self.store.snapshot()
        self.assertEqual(after.read(key),metadata)
        self.assertEqual(after.read('plan.json'),before.read('plan.json'))
        self.assertEqual(after.read('checkpoints/clip_0001.json'),before.read('checkpoints/clip_0001.json'))
        self.assertEqual(self.imported_undo(operation,preview['snapshot']),(status,value))
        self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],200)
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_portable_undo_lost_ack_does_not_restore_twice(self):
        operation,_,_ = self.organized_quarantine_import()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        original = self.store._commit_changes
        def lose(base,changes,**kwargs):
            result = original(base,changes,**kwargs)
            if any(key.endswith('/portable_undo.json') for key in changes):
                raise OSError('portable undo accepted before lost reply')
            return result
        with patch.object(self.store,'_commit_changes',lose):
            status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,503,value)
        before = self.store.snapshot().reference
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_portable_undo_occupied_destination_and_missing_grant_are_safe(self):
        operation,key,metadata = self.organized_quarantine_import()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'],proof=False)[0],423)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'],grant=False)[0],400)
        self.store.commit(self.store.snapshot(),{key:dict(data=metadata,scope='archive:'+self.rejected,
            category='takes',immutable=True)},operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,409,value)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_portable_undo_staging_interruption_keeps_current_catalogue(self):
        operation,_,_ = self.organized_quarantine_import()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        before = self.store.snapshot().reference
        original = self.store.stage_payload
        def lose(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('portable independent staging interrupted')
        with patch.object(self.store,'stage_payload',lose):
            self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],503)
        self.assertEqual(before,self.store.snapshot().reference)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)

    def unfinished_organized_import(self):
        self.accept_pending()
        retention = module('storage_retention').RuntimeRetention
        original = retention.delete_generation
        def lose(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('organized quarantine completed before review reply')
        with patch.object(retention,'delete_generation',lose):
            self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],503)
        self.recover(); self.reimport()
        return next(key.split('/')[1] for key in self.store.snapshot().state['documents']
                    if key.startswith('retention/') and key.endswith('/portable.json'))

    def test_portable_import_resumes_completed_quarantine_before_final_ack(self):
        operation = self.unfinished_organized_import()
        before = self.store.snapshot()
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(value['undo_operations'],[operation])
        after = self.store.snapshot()
        self.assertEqual(before.read('plan.json'),after.read('plan.json'))
        self.assertEqual(self.call('finalize',self.selected,[self.selected]),(status,value))
        self.assertEqual(after.reference,self.store.snapshot().reference)

    def test_portable_import_finalization_preserves_an_undone_rejection(self):
        operation = self.unfinished_organized_import()
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertEqual(self.imported_undo(operation,preview['snapshot'])[0],200)
        before = self.store.snapshot()
        key = 'checkpoints/clip_0001.'+self.rejected+'.json'
        status,value = self.call('finalize',self.selected,[self.selected])
        self.assertEqual(status,200,value)
        self.assertEqual(before.read(key),self.store.snapshot().read(key))
        self.assertEqual(value['undo_operations'],[operation])

    def test_portable_active_leaf_undo_restores_its_assignment_after_reimport(self):
        self.accept_pending()
        self.assertEqual(self.call('finalize',self.selected,[self.selected])[0],200)
        before = self.store.snapshot()
        pointer = before.read('checkpoints/clip_0001.json')
        with runtime.runtime_access(self.store,retention_writes=True) as bound:
            preview = bound.retention.preview_generation(1,self.selected)
            self.assertTrue(preview['allowed'],preview)
            self.assertTrue(preview['rollback'])
            deleted = bound.retention.delete_generation(1,self.selected,preview['snapshot'],proof=self.proof)
        operation = deleted['operation_id']
        self.assertNotIn('checkpoints/clip_0001.json',self.store.snapshot().state['documents'])
        self.recover(); self.reimport()
        imported_plan = self.store.snapshot().read('plan.json')
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        self.assertTrue(preview['allowed'],preview)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        self.assertEqual(self.store.snapshot().read('checkpoints/clip_0001.json'),pointer)
        self.assertEqual(self.store.snapshot().read('plan.json'),imported_plan)

    def test_named_branch_portable_undo_preserves_main_and_rejects_wrong_branch(self):
        branches = module('working_branches').WorkingBranches
        with runtime.runtime_access(self.store,branch_writes=True):
            branch = branches(self.output,self.run).create('main','Portable undo fork',
                {'plan_json':json.dumps({'shots':[{'id':s['id'],'prompt':s['prompt']} for s in self.plan['shots']]})},
                through_scene=1)
        with runtime.runtime_access(self.store,selected=branch['id']) as bound:
            self.plan = dict(self.plan,_branch_id=branch['id'],_storage_pin=bound.pin)
        operation,_,_ = self.organized_quarantine_import()
        before = self.store.snapshot()
        status,value = self.imported_undo(operation,branch='main')
        self.assertNotEqual(status,200,value)
        self.assertEqual(before.reference,self.store.snapshot().reference)
        status,preview = self.imported_undo(operation)
        self.assertEqual(status,200,preview)
        status,value = self.imported_undo(operation,preview['snapshot'])
        self.assertEqual(status,200,value)
        for key in ('plan.json','checkpoints/clip_0001.json','branches/'+branch['id']+'/plan.json'):
            self.assertEqual(before.read(key),self.store.snapshot().read(key))


if __name__ == '__main__':
    suite = unittest.TestSuite(RoundtripTests(name) for name in RoundtripTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
