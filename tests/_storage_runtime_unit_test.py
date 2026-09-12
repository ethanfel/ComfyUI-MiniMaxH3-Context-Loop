"""Normal service constructors share a pinned, copy-only runtime operation."""
import asyncio
import ast
import copy
from contextvars import copy_context
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import _storage_branch_controls_unit_test as fixture
import storage_state as state
import storage_project as project
from storage_runtime import runtime_access, current_runtime, accepted_export_access
from storage_branch_controls import BranchControlDocuments
from storage_project_reads import ProjectReadView
from working_branches import WorkingBranches
from checkpoint_manager import CheckpointGraphManager
from checkpoint_variants import saved_checkpoint_variants
from branch_scope import branch_scope, current_branch
from project_ownership import ownership_path, ProjectOwnershipError


def branch_route(output):
    """Execute the real HTTP handler without importing ComfyUI/models/server."""
    source = Path(__file__).resolve().parents[1]/'chain_nodes.py'
    node = next(item for item in ast.parse(source.read_text()).body
                if isinstance(item, ast.AsyncFunctionDef) and item.name == '_working_branch_command')
    namespace = {'WorkingBranches': WorkingBranches, '_output_root': lambda: str(output),
        '_strict_run_name': lambda value: str(value), '_working_branch_id': lambda value: value,
        'web': SimpleNamespace(json_response=lambda value, status=200: {'status': status, 'body': value}),
        'ProjectOwnershipError': ProjectOwnershipError}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_working_branch_command']


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.BranchControlTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        # Empty-payload test fixture only. Actual copies use prepare_join.
        marker = state._decode((self.f.store.project/'storage.json').read_bytes())
        marker.update(format=project.FORMAT, mode=project.ProjectStore.MODE)
        state.atomic_json(self.f.store.project/'storage.json', marker)
        self.store = project.ProjectStore(self.f.store.project)
        self.output, self.named = self.f.output, self.f.named

    def branches(self):
        return WorkingBranches(self.output, 'demo')

    def publish(self):
        explicit = WorkingBranches(self.output, 'demo',
            rehearsal_controls=BranchControlDocuments(self.store))
        record = explicit.load(self.named)
        authored = copy.deepcopy(record['authoring'])
        plan = json.loads(authored['plan_json'])
        plan['shots'][0].update(prompt='New draft é 雪', seed='18446744073709551611')
        authored['plan_json'] = json.dumps(plan, ensure_ascii=False)
        return explicit.save(self.named, authored, record['revision'], uuid.uuid4().hex)

    def test_normal_constructors_share_branch_settings_and_graph_root(self):
        before = self.store.snapshot().reference
        for selected in ('main', self.named):
            legacy = self.f.legacy.load(selected)
            with runtime_access(self.store, selected=selected) as runtime:
                loaded = self.branches().load(selected)
                self.assertEqual(loaded, legacy)
                expected = CheckpointGraphManager(self.output,
                    rehearsal_view=ProjectReadView(self.store, base=runtime.base))
                actual = CheckpointGraphManager(self.output)
                self.assertEqual(actual.active_selection('demo'), expected.active_selection('demo'))
                self.assertEqual(actual.graph('demo', adopt_legacy=False),
                                 expected.graph('demo', adopt_legacy=False))
                originals = actual.graph('demo', adopt_legacy=False)['revisions']
                self.assertEqual(saved_checkpoint_variants(self.output, 'demo', originals),
                    saved_checkpoint_variants(self.output, 'demo', originals, rehearsal_view=runtime.reader))
                self.assertIs(actual._rehearsal_view, runtime.reader)
                self.assertEqual(current_branch('demo'), selected)
                self.assertEqual(runtime.pin['root'], before)
        self.assertEqual(before, self.store.snapshot().reference)
        self.assertFalse((self.store.project/'branches').exists())

    def test_copy_access_alone_does_not_activate_normal_services(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.branches().load(self.named)

    def test_actual_branch_get_handler_restores_settings_without_manual_ports(self):
        route = branch_route(self.output)
        async def request(action, selected):
            return await route(SimpleNamespace(method='GET', query={
                'run_name': 'demo', 'action': action, 'branch_id': selected}))
        rejected = asyncio.run(request('load', self.named))
        self.assertEqual(rejected['status'], 400)
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named):
            listing = asyncio.run(request('list', self.named))
            self.assertEqual(listing['status'], 200)
            for selected in ('main', self.named):
                result = asyncio.run(request('load', selected))
                self.assertEqual(result['status'], 200)
                self.assertEqual(result['body'], self.branches().load(selected))
            self.assertEqual(result['body']['authoring']['width'], 960)
            shot = json.loads(result['body']['authoring']['plan_json'])['shots'][0]
            self.assertEqual(shot['prompt'], 'Exact prompt é 1')
            self.assertEqual(shot['seed'], '18446744073709551614')
        self.assertEqual(before, self.store.snapshot().reference)

    def test_branch_write_is_explicit_and_no_old_files_are_recreated(self):
        before = self.store.snapshot()
        with runtime_access(self.store):
            loaded = self.branches().load(self.named)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                self.branches().save(self.named, loaded['authoring'], loaded['revision'])
        self.assertEqual(before.reference, self.store.snapshot().reference)
        with runtime_access(self.store, branch_writes=True):
            authored = copy.deepcopy(loaded['authoring'])
            authored['base_seed'] = '18446744073709551610'
            saved = self.branches().save(self.named, authored, loaded['revision'])
            # Other services in this operation must not jump to the newer root.
            self.assertEqual(self.branches().load(self.named), loaded)
        with runtime_access(self.store):
            self.assertEqual(self.branches().load(self.named), saved)
        self.assertEqual(self.store.snapshot().state['generation'], before.state['generation']+1)
        self.assertFalse((self.store.project/'branches').exists())

    def test_one_operation_never_mixes_new_authoring_into_old_graph(self):
        with runtime_access(self.store, selected=self.named) as runtime:
            previous = self.branches().load(self.named)
            graph = CheckpointGraphManager(self.output).graph('demo', adopt_legacy=False)
            saved = self.publish()
            self.assertNotEqual(saved['revision'], previous['revision'])
            self.assertEqual(self.branches().load(self.named), previous)
            self.assertEqual(CheckpointGraphManager(self.output).graph('demo', adopt_legacy=False), graph)
            pin = json.loads(json.dumps(runtime.pin))
        with runtime_access(self.store, pin=pin, selected=self.named):
            self.assertEqual(self.branches().load(self.named), previous)
        with runtime_access(self.store, selected=self.named):
            self.assertEqual(self.branches().load(self.named), saved)
            shot = json.loads(saved['authoring']['plan_json'])['shots'][0]
            self.assertEqual(shot['seed'], '18446744073709551611')
            self.assertEqual(shot['prompt'], 'New draft é 雪')

    def test_old_pin_cannot_overwrite_newer_branch_work(self):
        with runtime_access(self.store, selected=self.named) as runtime:
            pin, loaded = runtime.pin, self.branches().load(self.named)
        self.publish()
        before = self.store.snapshot().reference
        with runtime_access(self.store, selected=self.named, pin=pin, branch_writes=True):
            with self.assertRaises(state.StateConflict):
                self.branches().save(self.named, loaded['authoring'], loaded['revision'])
        self.assertEqual(before, self.store.snapshot().reference)

    def test_service_instances_cannot_escape_binding(self):
        with runtime_access(self.store):
            branches, graph = self.branches(), CheckpointGraphManager(self.output)
        for operation in (lambda: branches.load(self.named), lambda: graph.active_selection('demo')):
            with self.assertRaisesRegex(ValueError, 'escaped'):
                operation()
        with runtime_access(self.store), self.assertRaisesRegex(ValueError, 'escaped'):
            branches.load(self.named)

    def test_inherited_context_cannot_keep_a_finished_request_alive(self):
        with runtime_access(self.store, branch_writes=True):
            captured = copy_context()
            branches = self.branches()
        for operation in (lambda: self.branches().load(), lambda: branches.load()):
            with self.assertRaisesRegex(ValueError, 'escaped'):
                captured.run(operation)

    def test_epoch_fences_old_pins_and_running_operations(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        self.store.advance_epoch(self.store.snapshot(), operation_id=uuid.uuid4().hex)
        with self.assertRaisesRegex(state.StateConflict, 'epoch'):
            with runtime_access(self.store, pin=pin):
                self.fail('Old epoch was accepted.')
        with self.assertRaisesRegex(state.StateConflict, 'epoch'):
            with runtime_access(self.store):
                self.store.advance_epoch(self.store.snapshot(), operation_id=uuid.uuid4().hex)
                self.branches().load()
        self.assertIsNone(current_runtime(self.output))

    def test_wrong_scope_and_malformed_pins_fail_without_fallback(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
            for operation in (lambda: WorkingBranches(self.output, 'other'),
                    lambda: CheckpointGraphManager(self.f.f.output),
                    lambda: CheckpointGraphManager(self.output).active_selection('other')):
                with self.assertRaisesRegex(ValueError, 'different'):
                    operation()
        for changed in ({'run_name': 'other'}, {'branch_id': self.named}, {'epoch': True},
                        {'epoch': pin['epoch']+1}, {'allow_write': True},
                        {'root': dict(pin['root'], path='../../outside.json')},
                        {'root': dict(pin['root'], sha256='0'*64)}):
            with self.assertRaises(ValueError):
                with runtime_access(self.store, pin=dict(pin, **changed)):
                    self.fail('Invalid pin was accepted.')

    def test_pin_is_a_copy_and_nested_binding_cannot_switch_projects(self):
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
            pin['root']['sha256'] = '0'*64
            self.assertNotEqual(runtime.pin['root'], pin['root'])
            with self.assertRaisesRegex(ValueError, 'switch bindings'):
                with runtime_access(self.store):
                    self.fail('Nested binding was accepted.')
            self.branches().load()

    def test_unpublished_root_is_not_a_runtime_pin(self):
        known = set((self.store.project/'project/roots').glob('*.json'))
        def fail(phase):
            if phase == 'root':
                raise OSError('unpublished root')
        with patch.object(self.store, '_write_root', wraps=self.store._write_root):
            with self.assertRaises(OSError):
                self.store.commit(self.store.snapshot(), {'plan.json': {'data': b'{}',
                    'scope': 'branch:main', 'category': 'branches', 'immutable': False}},
                    operation_id=uuid.uuid4().hex, after_stage=fail)
        candidate = next(iter(set((self.store.project/'project/roots').glob('*.json'))-known))
        with runtime_access(self.store) as runtime:
            pin = runtime.pin
        raw = candidate.read_bytes()
        pin['root'] = {'path': candidate.relative_to(self.store.project).as_posix(),
                       'sha256': state._hash(raw), 'size': len(raw)}
        with self.assertRaisesRegex(state.StateConflict, 'committed ancestor'):
            with runtime_access(self.store, pin=pin):
                self.fail('Unpublished root accepted.')

    def test_async_tasks_and_worker_threads_keep_their_own_roots_and_branches(self):
        with runtime_access(self.store, selected=self.named) as runtime:
            old_pin, old = runtime.pin, self.branches().load(self.named)
        latest = self.publish()
        async def read(pin, expected):
            with runtime_access(self.store, selected=self.named, pin=pin):
                await asyncio.sleep(0)
                value = await asyncio.wait_for(asyncio.to_thread(
                    lambda: self.branches().load(current_branch('demo'))), timeout=5)
                self.assertEqual(value, expected)
        async def both():
            await asyncio.gather(read(old_pin, old), read(None, latest))
        asyncio.run(both())
        self.assertEqual(current_branch('demo'), 'main')
        self.assertIsNone(current_runtime(self.output))

    def test_unported_writers_and_checkpoint_mutation_remain_fenced(self):
        before = self.store.snapshot().reference
        with runtime_access(self.store, branch_writes=True):
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                ownership_path(str(self.output), 'demo')
            with self.assertRaisesRegex(ValueError, 'read-only'):
                CheckpointGraphManager(self.output).graph('demo', adopt_legacy=True)
        self.assertEqual(before, self.store.snapshot().reference)

    def test_export_successor_reads_only_acknowledged_root_and_returns_exact_commits(self):
        with runtime_access(self.store, export_writes=True) as parent:
            old = parent.pin
            receipt = self.store.commit(parent.base, {'own.json':dict(data=b'{"own":true}',
                scope='jobs:own', category='jobs', immutable=True)}, operation_id=uuid.uuid4().hex)
            parent.record_commit(receipt)
            acknowledged = parent.output_pin
            self.store.commit(self.store.snapshot(), {'unrelated.json':dict(data=b'{}',
                scope='jobs:unrelated', category='jobs', immutable=True)}, operation_id=uuid.uuid4().hex)
            with accepted_export_access(parent) as child:
                self.assertEqual(child.pin, acknowledged)
                self.assertNotIn('unrelated.json', child.base.state['documents'])
                self.assertEqual(child.base.read('own.json'), b'{"own":true}')
                with self.assertRaisesRegex(ValueError, 'escaped'):
                    parent.check()
                receipt = self.store.commit(child.base, {'export.json':dict(data=b'{}',
                    scope='exports:test', category='cuts', immutable=False)}, operation_id=uuid.uuid4().hex)
                child.record_commit(receipt)
                final = child.output_pin
            self.assertEqual(parent.pin, old)
            self.assertEqual(parent.output_pin, final)
            self.assertEqual(parent.base.reference, old['root'])
            with self.assertRaisesRegex(ValueError, 'escaped'):
                child.check()

    def test_export_successor_restricts_grants_and_preserves_node_owner(self):
        with runtime_access(self.store, export_writes=True, generation_writes=True,
                branch_writes=True, retention_writes=True) as parent:
            parent.node_write_proof = dict(owner_id='test', epoch=1)
            with accepted_export_access(parent) as child:
                self.assertEqual(child.node_write_proof, parent.node_write_proof)
                self.assertIsNot(child.node_write_proof, parent.node_write_proof)
                self.assertFalse(child.generation_writes)
                self.assertFalse(child.branch_writes)
                self.assertFalse(child.retention_writes)
                self.assertFalse(child.ownership_writes)
                self.assertFalse(child.handoff_writes)
                child.exports.require_write()
                with self.assertRaisesRegex(ValueError, 'read-only'):
                    child.branches._require_write()
                with self.assertRaisesRegex(ValueError, 'switch bindings'):
                    with runtime_access(self.store):
                        self.fail('General nested binding allowed')
            parent.node_export_write.set(False)
            with self.assertRaisesRegex(ValueError, 'read-only'):
                with accepted_export_access(parent):
                    self.fail('Denied node acquired an export grant')
        with runtime_access(self.store) as parent, self.assertRaisesRegex(ValueError, 'read-only'):
            with accepted_export_access(parent):
                self.fail('Read-only request gained an export grant')

    def test_export_successor_failure_revokes_child_and_preserves_acknowledged_output(self):
        with runtime_access(self.store, export_writes=True) as parent:
            with self.assertRaisesRegex(OSError, 'after commit'):
                with accepted_export_access(parent) as child:
                    captured = copy_context()
                    receipt = self.store.commit(child.base, {'export.json':dict(data=b'{}',
                        scope='exports:test', category='cuts', immutable=False)}, operation_id=uuid.uuid4().hex)
                    child.record_commit(receipt)
                    raise OSError('after commit')
            parent.check()
            self.assertEqual(parent.accepted.read('export.json'), b'{}')
            with self.assertRaisesRegex(ValueError, 'escaped'):
                captured.run(child.check)


if __name__ == '__main__':
    unittest.main(verbosity=2)
