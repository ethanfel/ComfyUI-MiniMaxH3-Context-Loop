"""Independent sync/async node calls retain an exact, host-authorized data pin."""
import asyncio
import copy
from contextvars import copy_context
import json
import unittest
import uuid

import _storage_runtime_unit_test as fixture
from branch_scope import scoped_node, current_branch
from checkpoint_manager import CheckpointGraphManager
from storage_carriers import node_host, PIN_KEY
from storage_runtime import runtime_access, current_runtime
from working_branches import WorkingBranches
import storage_state as state


class CarrierTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RuntimeTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.output, self.named = self.f.store, self.f.output, self.f.named

        @scoped_node
        def start(run_name='demo', plan_json='{}'):
            selected = current_branch(run_name)
            return ({'run_name': run_name, 'plan': {
                'run_name': run_name, 'saved': WorkingBranches(self.output, run_name).load(selected)}},)
        self.start = start

        @scoped_node
        def read(state):
            return ({'run_name': state['run_name'],
                'saved': WorkingBranches(self.output, 'demo').load(current_branch('demo')),
                'graph': CheckpointGraphManager(self.output).graph('demo', adopt_legacy=False)},)
        self.read = read

    def named_start(self):
        return self.start(plan_json=json.dumps({'_branch_id': self.named}))[0]

    def test_first_node_stamps_main_and_named_nested_carriers(self):
        with node_host(self.store):
            main = self.start()[0]
            named = self.named_start()
        self.assertEqual(main['_branch_id'], 'main')
        self.assertEqual(named['_branch_id'], self.named)
        self.assertEqual(main[PIN_KEY]['root'], self.store.snapshot().reference)
        self.assertEqual(named[PIN_KEY], named['plan'][PIN_KEY])
        self.assertEqual(named['plan']['saved'], self.f.f.legacy.load(self.named))
        self.assertIsNone(current_runtime(self.output))

    def test_independent_nodes_keep_old_prompt_seed_canvas_after_new_save(self):
        with node_host(self.store):
            incoming = self.named_start()
        before = copy.deepcopy(incoming)
        latest = self.f.publish()
        with node_host(self.store):
            historical = self.read(incoming)[0]
            fresh = self.named_start()
        self.assertEqual(incoming, before)
        self.assertEqual(historical['saved'], incoming['plan']['saved'])
        self.assertEqual(historical[PIN_KEY], incoming[PIN_KEY])
        self.assertEqual(fresh['plan']['saved'], latest)
        shot = json.loads(historical['saved']['authoring']['plan_json'])['shots'][0]
        self.assertEqual(shot['seed'], '18446744073709551614')
        self.assertEqual(shot['prompt'], 'Exact prompt é 1')
        self.assertEqual(historical['saved']['authoring']['width'], 960)
        self.assertNotEqual(fresh[PIN_KEY]['root'], incoming[PIN_KEY]['root'])

    def test_pin_alone_does_not_authorize_storage(self):
        with node_host(self.store):
            value = self.named_start()
        with self.assertRaisesRegex(ValueError, 'host access'):
            self.read(value)

    def test_host_adapter_preserves_sync_async_signature_and_does_not_mutate_inputs(self):
        @scoped_node
        def sync(value, /, *extra, scale=1, **options):
            return value, extra, scale, options
        @scoped_node
        async def asynchronous(value, /, *extra, scale=1, **options):
            return value, extra, scale, options
        value = {'counter':1}
        def adapt(store, inputs):
            self.assertIs(store, self.store)
            return dict(inputs, value=dict(inputs['value'], counter=2), scale=3)
        with node_host(self.store, input_adapters={sync:adapt, asynchronous:adapt}):
            expected = ({'counter':2}, (7,), 3, {'label':'same'})
            self.assertEqual(sync(value, 7, label='same'), expected)
            self.assertEqual(asyncio.run(asynchronous(value, 7, label='same')), expected)
        self.assertEqual(value, {'counter':1})
        self.assertEqual(sync(value)[0], value)

    def test_host_adapter_is_exact_callable_and_cannot_change_argument_contract_or_rebind(self):
        @scoped_node
        def adapted(value):
            return value
        @scoped_node
        def other(value):
            return value
        with node_host(self.store, input_adapters={adapted:lambda store, args:dict(args, unexpected=1)}):
            self.assertEqual(other(1), 1)
            with self.assertRaisesRegex(ValueError, 'argument contract'):
                adapted(1)
            with runtime_access(self.store), self.assertRaisesRegex(ValueError, 'active runtime'):
                adapted(1)
        with self.assertRaisesRegex(ValueError, 'Python callables'):
            with node_host(self.store, input_adapters={adapted:'workflow JSON cannot grant this'}):
                pass

    def test_conflicting_nested_or_multiple_input_pins_reject_before_node(self):
        with node_host(self.store):
            old = self.named_start()
        self.f.publish()
        with node_host(self.store):
            new = self.named_start()
            @scoped_node
            def merge(state, manifest):
                self.fail('Mixed roots reached the node.')
            with self.assertRaisesRegex(ValueError, 'mixed-root'):
                merge(old, new)
            broken = copy.deepcopy(old)
            broken['plan'][PIN_KEY] = new[PIN_KEY]
            with self.assertRaisesRegex(ValueError, 'mixed-root'):
                self.read(broken)

    def test_wrong_run_branch_epoch_and_permission_flags_rejected(self):
        with node_host(self.store):
            incoming = self.named_start()
        mutations = ({'run_name': 'other'}, {'branch_id': 'main'}, {'epoch': True},
                     {'ownership_writes': True}, {'root': {'path': '../../elsewhere'}})
        with node_host(self.store):
            for mutation in mutations:
                carrier = {'run_name': 'demo', '_branch_id': self.named,
                           PIN_KEY: dict(incoming[PIN_KEY], **mutation)}
                with self.assertRaises(ValueError):
                    self.read(carrier)
            with self.assertRaisesRegex(ValueError, 'different project'):
                self.start(run_name='other')

    def test_maintenance_epoch_fences_queued_node(self):
        with node_host(self.store):
            incoming = self.named_start()
        self.store.advance_epoch(self.store.snapshot(), operation_id=uuid.uuid4().hex)
        with node_host(self.store), self.assertRaisesRegex(state.StateConflict, 'epoch'):
            self.read(incoming)

    def test_secondary_pin_cannot_hide_bool_or_float_identity(self):
        with node_host(self.store):
            incoming = self.named_start()
            broken = copy.deepcopy(incoming)
            broken['plan'][PIN_KEY]['epoch'] = float(incoming[PIN_KEY]['epoch'])
            with self.assertRaisesRegex(ValueError, 'mixed-root'):
                self.read(broken)

    def test_active_binding_also_requires_exact_pin_types(self):
        with runtime_access(self.store, selected=self.named) as bound:
            changed = copy.deepcopy(bound.pin)
            changed['epoch'] = float(changed['epoch'])
            with self.assertRaisesRegex(ValueError, 'active runtime pin'):
                self.read({'run_name': 'demo', '_branch_id': self.named, PIN_KEY: changed})

    def test_connected_project_overrides_legacy_run_widget(self):
        @scoped_node
        def plan(run_name='unused', project_assets=None, plan_json='{}'):
            run = project_assets['project']
            return ({'run_name': run, 'saved': WorkingBranches(self.output, run).load(current_branch(run))},)
        with node_host(self.store):
            result = plan(project_assets={'project': 'demo'}, plan_json=json.dumps({'_branch_id': self.named}))[0]
            expected = self.named_start()
        self.assertEqual(result[PIN_KEY], expected[PIN_KEY])
        self.assertEqual(result['saved'], expected['plan']['saved'])

    def test_wrong_output_branch_is_not_silently_restamped(self):
        @scoped_node
        def wrong(state):
            return ({'run_name': 'demo', '_branch_id': 'main'},)
        with node_host(self.store), self.assertRaisesRegex(ValueError, 'different branch'):
            wrong(self.named_start())

    def test_read_host_and_json_cannot_enable_writes(self):
        @scoped_node
        def writer(state):
            branches = WorkingBranches(self.output, 'demo')
            value = branches.load(self.named)
            branches.save(self.named, value['authoring'], value['revision'])
        with node_host(self.store):
            carrier = dict(self.named_start(), branch_writes=True)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                writer(carrier)
        with runtime_access(self.store, selected=self.named, branch_writes=True):
            with self.assertRaisesRegex(ValueError, 'node-host grants'):
                writer(carrier)

    def test_host_and_services_are_revoked_in_captured_contexts(self):
        with node_host(self.store):
            carrier = self.named_start()
            inherited = copy_context()
        with self.assertRaisesRegex(ValueError, 'escaped'):
            inherited.run(self.read, carrier)
        with node_host(self.store), self.assertRaisesRegex(ValueError, 'nest'):
            with node_host(self.store):
                self.fail('Nested host accepted.')

    def test_serialized_plan_and_checkpoint_selection_carry_pins(self):
        with node_host(self.store):
            original = self.named_start()
        self.f.publish()
        @scoped_node
        def selection(selection_json):
            return ({'run_name': 'demo', 'saved': WorkingBranches(self.output, 'demo').load(self.named)},)
        with node_host(self.store):
            result = selection(json.dumps(original))[0]
            authored = dict(original['plan'], _branch_id=self.named)
            from_plan_json = self.start(plan_json=json.dumps(authored))[0]
        self.assertEqual(result[PIN_KEY], original[PIN_KEY])
        self.assertEqual(from_plan_json[PIN_KEY], original[PIN_KEY])

    def test_legacy_utility_and_cached_tensor_objects_unchanged(self):
        sentinel = object()
        @scoped_node
        def legacy(state):
            return ({'result': (state, sentinel), 'ui': {'text': ['plain']}},)
        state_value = {'run_name': 'legacy', 'samples': sentinel}
        self.assertIs(legacy(state_value)[0]['result'][0], state_value)
        @scoped_node
        def utility(samples):
            return samples
        with node_host(self.store):
            self.assertIs(utility(sentinel), sentinel)
            carrier = self.named_start()
            @scoped_node
            def output(state):
                return {'result': [dict(run_name='demo', samples=sentinel)], 'ui': {'text': ['plain']}}
            result = output(carrier)
            self.assertIs(result['result'][0]['samples'], sentinel)
            self.assertEqual(result['result'][0][PIN_KEY], carrier[PIN_KEY])
            self.assertEqual(result['ui'], {'text': ['plain']})

    def test_async_nodes_keep_independent_pins_in_worker_threads(self):
        with node_host(self.store):
            old = self.named_start()
        self.f.publish()
        with node_host(self.store):
            new = self.named_start()
        @scoped_node
        async def read(state):
            await asyncio.sleep(0)
            value = await asyncio.wait_for(asyncio.to_thread(
                lambda: WorkingBranches(self.output, 'demo').load(self.named)), timeout=5)
            return ({'run_name': 'demo', 'saved': value},)
        async def both():
            with node_host(self.store):
                return await asyncio.gather(read(old), read(new))
        results = asyncio.run(both())
        self.assertEqual(results[0][0]['saved'], old['plan']['saved'])
        self.assertEqual(results[1][0]['saved'], new['plan']['saved'])
        self.assertEqual(results[0][0][PIN_KEY], old[PIN_KEY])
        self.assertEqual(results[1][0][PIN_KEY], new[PIN_KEY])
        self.assertIsNone(current_runtime(self.output))


if __name__ == '__main__':
    unittest.main(verbosity=2)
