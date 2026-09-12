"""Real history mutations with atomic publication, ownership and retry faults."""
import copy
import asyncio
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_runtime_unit_test as fixture
from branch_scope import branch_scope
from prompt_history import PromptHistoryStore, FORMAT
from project_ownership import claim_project_ownership, ProjectOwnershipError
from storage_runtime import runtime_access
from storage_carriers import node_host, history_operation, PIN_KEY
from branch_scope import scoped_node
from _storage_handoff_routes_unit_test import routes
from _storage_branch_routes_unit_test import request
import storage_state as state


class HistoryWriteTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        with runtime_access(self.store, ownership_writes=True):
            owner = claim_project_ownership(self.output, 'demo', 'history-owner-1234567890')
        self.proof = dict(owner_id='history-owner-1234567890', epoch=owner['epoch'])

    def call(self, action, *args, selected='main', pin=None, operation=None, fault=None):
        with runtime_access(self.store, selected=selected, pin=pin, history_writes=True) as runtime:
            runtime.history.after_stage = fault
            result = getattr(PromptHistoryStore(self.output), action)(
                'demo', 'scene_1', *args, operation_id=operation or uuid.uuid4().hex,
                ownership_proof=self.proof)
            return result, runtime.output_pin

    def read(self, selected='main', pin=None):
        with runtime_access(self.store, selected=selected, pin=pin):
            history = PromptHistoryStore(self.output)
            index = history.list('demo','scene_1')
            return index, {item['id']:history.get('demo','scene_1',item['id']) for item in index['revisions']}

    def test_draft_execution_child_label_archive_and_activation_keep_prompt(self):
        original = self.store.snapshot()
        draft, _ = self.call('save_draft', ' exact é 雪\r\ntext ')
        first = draft['revision']['id']
        revised, _ = self.call('save_draft', 'edited', first)
        self.assertEqual(revised['revision']['id'], first)
        executed, _ = self.call('mark_executed', 'edited')
        self.assertEqual(executed['revision']['execution_count'], 1)
        saved_executed = self.store.snapshot()
        child, _ = self.call('save_draft', 'new draft', first)
        self.assertNotEqual(child['revision']['id'], first)
        self.assertEqual(child['revision']['parent_id'], first)
        self.call('set_label', first, ' Original  shot ')
        self.call('set_archived', first, True)
        self.assertTrue(self.read()[1][first]['archived_at'])
        active, _ = self.call('activate', first)
        self.assertEqual(active['revision']['prompt'], 'edited')
        self.assertEqual(active['revision']['label'], 'Original shot')
        self.assertIsNone(active['revision']['archived_at'])
        self.assertEqual(state._decode(saved_executed.read('prompt_history/scene_1/'+first+'.json')), executed['revision'])
        after = self.store.snapshot()
        for address, descriptor in original.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
        self.assertFalse((self.store.project/'prompt_history').exists())

    def test_delete_only_inactive_leaf_draft_and_keep_old_root_bytes(self):
        executed, _ = self.call('mark_executed', 'original')
        first = executed['revision']['id']
        draft, _ = self.call('save_draft', 'discard', first)
        token = draft['revision']['id']
        with self.assertRaisesRegex(ValueError, 'active'):
            self.call('delete_draft', token)
        self.call('activate', first)
        before = self.store.snapshot()
        self.call('delete_draft', token)
        address = 'prompt_history/scene_1/'+token+'.json'
        self.assertNotIn(address, self.store.snapshot().state['documents'])
        self.assertEqual(state._decode(before.read(address)), draft['revision'])
        with self.assertRaises(ValueError):
            self.call('delete_draft', first)

    def test_named_branch_isolation_and_old_reader_pin(self):
        main, main_pin = self.call('mark_executed', 'main original')
        named, _ = self.call('mark_executed', 'named original', selected=self.named)
        self.call('set_label', main['revision']['id'], 'Later')
        self.assertEqual(self.read(self.named)[1][named['revision']['id']]['prompt'], 'named original')
        self.assertEqual(self.read(pin=main_pin)[1][main['revision']['id']]['label'], '')
        with runtime_access(self.store, history_writes=True), branch_scope('demo', self.named):
            with self.assertRaisesRegex(ValueError, 'different runtime branch'):
                PromptHistoryStore(self.output).save_draft('demo','scene_1','wrong branch',
                    operation_id=uuid.uuid4().hex, ownership_proof=self.proof)

    def test_exact_execution_retry_does_not_increment_count_again(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        result, _ = self.call('mark_executed','executed',pin=pin,operation=operation)
        before = self.store.snapshot().reference
        retried, _ = self.call('mark_executed','executed',pin=pin,operation=operation)
        self.assertEqual(retried,result)
        self.assertEqual(self.store.snapshot().reference,before)
        self.assertEqual(result['revision']['execution_count'],1)
        with self.assertRaises(ValueError):
            self.call('mark_executed','different',pin=pin,operation=operation)

    def test_staging_interruptions_never_publish_half_a_revision(self):
        for phase in ('document','root'):
            with self.subTest(phase=phase):
                with runtime_access(self.store) as runtime:
                    pin = runtime.pin
                before = self.store.snapshot().reference
                operation = uuid.uuid4().hex
                def interrupt(actual):
                    if actual == phase:
                        raise OSError('interrupted '+phase)
                with self.assertRaisesRegex(OSError,'interrupted'):
                    self.call('mark_executed',phase,pin=pin,operation=operation,fault=interrupt)
                self.assertEqual(self.store.snapshot().reference,before)
                result, _ = self.call('mark_executed',phase,pin=pin,operation=operation)
                self.assertEqual(result['revision']['execution_count'],1)
                self.assertEqual(self.read()[0],result['history'])

    def test_lost_publication_acknowledgement_replays_exact_result(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        publish = self.store._publish
        def interrupt(*args, **kwargs):
            publish(*args, **kwargs)
            raise OSError('lost reply')
        with patch.object(self.store,'_publish',side_effect=interrupt), self.assertRaisesRegex(OSError,'lost reply'):
            self.call('mark_executed','once',pin=pin,operation=operation)
        before = self.store.snapshot().reference
        result, _ = self.call('mark_executed','once',pin=pin,operation=operation)
        self.assertEqual(result['revision']['execution_count'],1)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_old_pin_and_delayed_retry_do_not_overwrite_newer_edits(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        result, _ = self.call('mark_executed','original',pin=pin,operation=operation)
        self.call('set_label',result['revision']['id'],'new label')
        before = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.call('save_draft','stale',pin=pin)
        with self.assertRaises(state.StateConflict):
            self.call('mark_executed','original',pin=pin,operation=operation)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_missing_grant_owner_operation_and_escaped_service_reject(self):
        before = self.store.snapshot().reference
        for options in ({},dict(branch_writes=True),dict(generation_writes=True)):
            with runtime_access(self.store, **options):
                with self.assertRaisesRegex(ValueError,'read-only'):
                    PromptHistoryStore(self.output).save_draft('demo','scene_1','no grant',
                        operation_id=uuid.uuid4().hex,ownership_proof=self.proof)
        with runtime_access(self.store,history_writes=True):
            history = PromptHistoryStore(self.output)
            with self.assertRaises(ProjectOwnershipError):
                history.save_draft('demo','scene_1','no owner',operation_id=uuid.uuid4().hex)
            with self.assertRaises(ValueError):
                history.save_draft('demo','scene_1','no operation',ownership_proof=self.proof)
        with self.assertRaises(ValueError):
            history.save_draft('demo','scene_1','escaped',operation_id=uuid.uuid4().hex,ownership_proof=self.proof)
        self.assertEqual(before,self.store.snapshot().reference)

    def history_routes(self):
        namespace = routes(self.output)
        namespace['PromptHistoryStore'] = PromptHistoryStore
        source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        nodes = [n for n in ast.parse(source.read_text()).body
                 if isinstance(n,ast.AsyncFunctionDef) and n.name in ('_get_prompt_history','_update_prompt_history')]
        exec(compile(ast.Module(body=nodes,type_ignores=[]), str(source),'exec'),namespace)
        return namespace

    def test_actual_http_mutations_use_owner_and_operation_without_changing_plan(self):
        namespace = self.history_routes()
        def invoke(action, **values):
            with runtime_access(self.store,history_writes=True,selected=self.named):
                response = asyncio.run(namespace['_update_prompt_history'](request(dict(
                    run_name='demo',scene_id='scene_1',action=action,
                    operation_id=uuid.uuid4().hex,**values),self.proof)))
            self.assertEqual(response['status'],200,response)
            return response['body']
        executed, _ = self.call('mark_executed','baseline',selected=self.named)
        old = executed['revision']['id']
        draft = invoke('save',prompt='new prompt',parent_revision=old)['revision']['id']
        invoke('label',revision=old,label='Saved execution')
        invoke('archive',revision=old)
        invoke('activate',revision=old)
        invoke('delete',revision=draft)
        index, values = self.read(self.named)
        self.assertEqual(index['active_revision'],old)
        self.assertEqual(list(values),[old])
        self.assertEqual(values[old]['prompt'],'baseline')
        self.assertEqual(self.read()[0]['revisions'],[])

    def test_http_body_cannot_grant_writes_or_claim_ownership(self):
        namespace = self.history_routes()
        body = dict(run_name='demo',scene_id='scene_1',action='save',prompt='unapproved',
                    history_writes=True,operation_id=uuid.uuid4().hex,ownership_proof=self.proof)
        before = self.store.snapshot().reference
        with runtime_access(self.store):
            response = asyncio.run(namespace['_update_prompt_history'](request(body,self.proof)))
        self.assertEqual(response['status'],400,response)
        with runtime_access(self.store,history_writes=True):
            response = asyncio.run(namespace['_update_prompt_history'](request(body)))
        self.assertEqual(response['status'],423,response)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_exact_node_host_grant_stamps_history_commit_and_replays(self):
        @scoped_node
        def execute(plan, unique_id=None):
            result = PromptHistoryStore(self.output).mark_executed('demo','scene_1','node prompt',
                operation_id=history_operation(execute,unique_id))
            return (dict(plan,history=result),)
        with runtime_access(self.store) as runtime:
            plan = dict(run_name='demo',_branch_id='main',_storage_pin=runtime.pin,_project_ownership=self.proof)
        original = copy.deepcopy(plan)
        namespace = uuid.uuid4().hex
        for _ in range(2):
            with node_host(self.store,history_writers=(execute,),operation_namespace=namespace):
                output = execute(plan,unique_id='42')[0]
            self.assertEqual(output['history']['revision']['execution_count'],1)
        self.assertNotEqual(output[PIN_KEY],plan[PIN_KEY])
        self.assertEqual(plan,original)
        with node_host(self.store,generation_writers=(execute,),operation_namespace=namespace):
            with self.assertRaises(ValueError):
                execute(plan,unique_id='42')

    def test_imported_immutable_executed_revision_versions_metadata_only(self):
        token = 'a'*32
        raw = dict(format=FORMAT,id=token,parent_id=None,prompt='Imported exact é 雪',
            prompt_sha256=state._hash('Imported exact é 雪'.encode()),label='',archived_at=None,
            created_at='2026-09-01T00:00:00Z',updated_at='2026-09-01T00:00:00Z',
            executed_at='2026-09-01T00:00:00Z',last_executed_at='2026-09-01T00:00:00Z',execution_count=2)
        index = dict(format=FORMAT,run_name='demo',scene_id='scene_1',active_revision=token,
                     revisions=[{k:v for k,v in raw.items() if k not in ('format','prompt')}])
        address = 'prompt_history/scene_1/'+token+'.json'
        self.store.commit(self.store.snapshot(),{
            address:dict(data=state._encode(raw),scope='history:main',category='history',immutable=True),
            'prompt_history/scene_1/index.json':dict(data=state._encode(index),scope='history:main',category='history',immutable=False)},
            operation_id=uuid.uuid4().hex)
        before = self.store.snapshot()
        self.call('set_label',token,'renamed')
        result, _ = self.call('mark_executed',raw['prompt'])
        self.assertEqual(result['revision']['execution_count'],3)
        self.assertEqual(result['revision']['prompt'],raw['prompt'])
        self.assertEqual(before.read(address),state._encode(raw))

    def test_read_grant_does_not_leak_through_a_nested_node(self):
        @scoped_node
        def child(plan):
            PromptHistoryStore(self.output).save_draft('demo','scene_1','not granted',
                operation_id=uuid.uuid4().hex)
        @scoped_node
        def parent(plan):
            return child(plan)
        with runtime_access(self.store) as runtime:
            plan = dict(run_name='demo',_storage_pin=runtime.pin,_project_ownership=self.proof)
        before = self.store.snapshot().reference
        with node_host(self.store,history_writers=(parent,)):
            with self.assertRaisesRegex(ValueError,'read-only'):
                parent(plan)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_other_branch_can_advance_without_invalidating_exact_retry(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        expected, _ = self.call('mark_executed','main',pin=pin,operation=operation)
        self.call('mark_executed','named',selected=self.named)
        current = self.store.snapshot().reference
        result, _ = self.call('mark_executed','main',pin=pin,operation=operation)
        self.assertEqual(result,expected)
        self.assertEqual(current,self.store.snapshot().reference)

    def test_corrupt_prepared_intent_is_not_silently_rebuilt(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        operation = uuid.uuid4().hex
        def interrupt(phase):
            raise OSError('stop staging')
        with self.assertRaises(OSError):
            self.call('save_draft','exact',operation=operation,pin=pin,fault=interrupt)
        path = self.store.project/('project/jobs/history-'+operation+'.json')
        path.write_bytes(b'{}')
        before = self.store.snapshot().reference
        with self.assertRaises(state.StateConflict):
            self.call('save_draft','exact',operation=operation,pin=pin)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_missing_accepted_revision_never_becomes_a_new_duplicate(self):
        result, _ = self.call('mark_executed','saved content')
        before = self.store.snapshot()
        address = 'prompt_history/scene_1/'+result['revision']['id']+'.json'
        physical = self.store.project/before.state['documents'][address]['file']['path']
        physical.unlink()
        returned = False
        with self.assertRaises(ValueError):
            self.call('mark_executed','saved content')
            returned = True
        self.assertFalse(returned)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
