"""Current Shot and ordinary recovery exercise the real history writer port."""
import copy
import importlib
from pathlib import Path
import unittest
import uuid

import _storage_generation_integration_test as fixture


class HistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.GenerationIntegrationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        self.history = fixture.module('prompt_history')

    def execute(self, incoming, namespace, unique_id='current-1'):
        with fixture.carriers.node_host(self.store,
                history_writers=(fixture.chain.MiniMaxH3ChainCurrent.current,),
                operation_namespace=namespace):
            return fixture.chain.MiniMaxH3ChainCurrent().current(incoming, unique_id=unique_id)

    def incoming(self):
        with fixture.runtime.runtime_access(self.store) as runtime:
            pin = runtime.pin
        plan = dict(self.f.plan,_storage_pin=pin)
        return dict(self.f.states[1],plan=plan,_storage_pin=pin,run_name=self.f.run)

    def test_actual_current_shot_records_exact_prompt_once_and_preserves_seed(self):
        incoming = self.incoming()
        before = copy.deepcopy(incoming)
        namespace = uuid.uuid4().hex
        first = self.execute(incoming,namespace)
        second = self.execute(incoming,namespace)
        self.assertEqual(incoming,before)
        self.assertEqual(first['result'][5],int(self.f.plan['shots'][0]['seed']))
        self.assertEqual(first['result'][0]['_storage_pin'],second['result'][0]['_storage_pin'])
        self.assertNotEqual(first['result'][0]['_storage_pin'],incoming['_storage_pin'])
        with fixture.runtime.runtime_access(self.store):
            history = self.history.PromptHistoryStore(self.f.output)
            index = history.list(self.f.run,'first')
            value = history.get(self.f.run,'first',index['active_revision'])
        self.assertEqual(len(index['revisions']),1)
        self.assertEqual(value['execution_count'],1)
        self.assertEqual(value['prompt'],self.f.plan['shots'][0]['scene_prompt'])

    def test_history_write_recovers_to_ordinary_files_without_prompt_or_payload_changes(self):
        self.execute(self.incoming(),uuid.uuid4().hex)
        with fixture.runtime.runtime_access(self.store):
            history = self.history.PromptHistoryStore(self.f.output)
            expected_index = history.list(self.f.run,'first')
            expected_revision = history.get(self.f.run,'first',expected_index['active_revision'])
        recovery = fixture.module('storage_recovery')
        receipt = self.f.lab/'history-recovery-source.json'
        fixture.state.atomic_json(receipt, dict(copy=str(self.store.project),
            source=str(self.f.source), independent_copies=True))
        journal = recovery.prepare_legacy_copy(receipt, self.f.lab/'history-recovery-output',
            self.f.lab/'history-recovery-job', rehearsal_store=self.store)
        self.assertTrue(recovery.recover_legacy_copy(journal)['source_unchanged'])
        ordinary = self.history.PromptHistoryStore(self.f.lab/'history-recovery-output')
        self.assertEqual(ordinary.list(self.f.run,'first'),expected_index)
        self.assertEqual(ordinary.get(self.f.run,'first',expected_index['active_revision']),expected_revision)
        self.assertEqual({p.relative_to(self.f.source).as_posix():p.read_bytes()
                          for p in self.f.source.rglob('*') if p.is_file()},self.f.source_files)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
