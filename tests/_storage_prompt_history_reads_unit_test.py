"""Prompt history reads must use accepted branch controls, never empty disk paths."""
import asyncio
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

import _storage_runtime_unit_test as fixture
from branch_scope import branch_scope
from prompt_history import PromptHistoryStore, FORMAT
from storage_runtime import runtime_access
from storage_project_reads import ProjectReadView
import storage_state as state


class HistoryReadTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named
        self.saved, self.indexes = {}, {}
        changes = {}
        for branch in ('main', self.named):
            token = uuid.uuid4().hex
            raw = 'Executed prompt é 雪: '+branch
            meta = dict(id=token, parent_id=None, label='Original', archived_at=None,
                created_at='2026-09-01T12:00:00Z', updated_at='2026-09-01T12:00:00Z',
                executed_at='2026-09-01T12:00:00Z', last_executed_at='2026-09-01T12:00:00Z',
                execution_count=1, prompt_sha256=state._hash(raw.encode()))
            self.saved[branch] = dict(format=FORMAT, prompt=raw, **meta)
            self.indexes[branch] = dict(format=FORMAT, run_name='demo', scene_id='scene_1',
                                        active_revision=token, revisions=[meta])
            prefix = '' if branch == 'main' else 'branches/'+branch+'/'
            directory = prefix+'prompt_history/scene_1/'
            for key, value in ((directory+'index.json', self.indexes[branch]),
                               (directory+token+'.json', self.saved[branch])):
                changes[key] = dict(data=state._encode(value), scope='branch:'+branch,
                                    category='history', immutable=False)
        self.store.commit(self.store.snapshot(), changes, operation_id=uuid.uuid4().hex)

    def test_list_and_get_read_exact_history_for_each_active_branch(self):
        before = self.store.snapshot().reference
        for branch in self.saved:
            with runtime_access(self.store, selected=branch):
                history = PromptHistoryStore(self.output)
                self.assertEqual(history.list('demo', 'scene_1'), history._public_index(self.indexes[branch]))
                self.assertEqual(history.get('demo', 'scene_1', self.saved[branch]['id']), self.saved[branch])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_actual_get_history_http_handler_uses_the_active_branch(self):
        path = Path(__file__).resolve().parents[1]/'chain_nodes.py'
        node = next(item for item in ast.parse(path.read_text()).body
                    if isinstance(item, ast.AsyncFunctionDef) and item.name == '_get_prompt_history')
        namespace = dict(PromptHistoryStore=PromptHistoryStore, asyncio=asyncio, json=json,
            _output_root=lambda:str(self.output), web=SimpleNamespace(json_response=lambda body,status=200:(status,body)))
        exec(compile(ast.Module(body=[node],type_ignores=[]), str(path), 'exec'), namespace)
        request = SimpleNamespace(query=dict(run_name='demo', scene_id='scene_1', revision=self.saved[self.named]['id']))
        with runtime_access(self.store, selected=self.named):
            code, result = asyncio.run(namespace['_get_prompt_history'](request))
        self.assertEqual(code, 200, result)
        self.assertEqual(result, self.saved[self.named])

    def test_pinned_history_does_not_follow_later_draft_changes(self):
        before = self.store.snapshot()
        value = copy.deepcopy(self.indexes['main'])
        value['revisions'][0]['label'] = 'Newer label'
        self.store.commit(before, {'prompt_history/scene_1/index.json':dict(
            data=state._encode(value), scope='branch:main', category='history', immutable=False)},
            operation_id=uuid.uuid4().hex)
        with runtime_access(self.store) as bound:
            with branch_scope('demo','main'):
                history = PromptHistoryStore(self.output, rehearsal_view=ProjectReadView(self.store,base=before))
                self.assertEqual(history.list('demo','scene_1'), history._public_index(self.indexes['main']))
            self.assertEqual(PromptHistoryStore(self.output).list('demo','scene_1'), history._public_index(value))

    def test_missing_history_is_empty_but_unindexed_legacy_content_is_not_adopted(self):
        path = self.store.project/'prompt_history/unindexed/index.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(state._encode(self.indexes['main']))
        with runtime_access(self.store):
            history = PromptHistoryStore(self.output)
            self.assertEqual(history.list('demo','absent')['revisions'], [])
            self.assertEqual(history.list('demo','unindexed')['revisions'], [])
            with self.assertRaises(ValueError):
                history.get('demo','unindexed',self.saved['main']['id'])

    def test_corrupt_accepted_history_is_not_replaced_by_an_empty_list(self):
        base = self.store.snapshot()
        path = self.store.project/base.state['documents']['prompt_history/scene_1/index.json']['file']['path']
        path.write_bytes(b'{}')
        returned = False
        with self.assertRaises(ValueError), runtime_access(self.store):
            PromptHistoryStore(self.output).list('demo','scene_1')
            returned = True
        self.assertFalse(returned)

    def test_reader_rejects_other_project_and_keeps_unported_mutations_blocked(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            history = PromptHistoryStore(self.output)
            with self.assertRaisesRegex(ValueError,'different project'):
                history.list('other','scene_1')
            with self.assertRaises(ValueError):
                history.save_draft('demo','scene_1','not authorized by a read adapter')
        self.assertEqual(before,self.store.snapshot().reference)
        self.assertFalse((self.store.project/'prompt_history').exists())


if __name__ == '__main__':
    unittest.main(argv=[__file__])
