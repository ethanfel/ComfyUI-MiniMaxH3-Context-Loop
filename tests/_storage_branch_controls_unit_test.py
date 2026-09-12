"""Actual branch operations against pinned controls, with V1 parity fixtures."""
import copy
import json
import unittest
from unittest.mock import patch
import uuid

import _storage_resolver_unit_test as fixture
import storage_state as state
from storage_branch_controls import BranchControlDocuments
from working_branches import WorkingBranches


class BranchControlTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RelocationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.named = 'b'*32
        self.authored = {'width': 1344, 'height': 768, 'base_seed': '18446744073709551615',
            'plan_json': json.dumps({'shots': [
                {'id': 'scene_'+str(i), 'prompt': 'unsaved '+str(i), 'seed': '9'} for i in range(1, 4)],
                'chapters': [{'id': 'one', 'start_scene_id': 'scene_1'},
                             {'id': 'two', 'start_scene_id': 'scene_3'}]})}
        self.records = {}
        for branch in ('main', self.named):
            prefix = '' if branch == 'main' else 'branches/'+branch+'/'
            address = 'branches/main.json' if branch == 'main' else prefix+'branch.json'
            self.records[address] = {'format': 'h3_working_branch_v1', 'id': branch,
                'run_name': 'demo', 'name': 'Original' if branch == 'main' else 'Small',
                'revision': branch+'-old', 'authoring': copy.deepcopy(self.authored)}
            self.records[prefix+'plan.json'] = {'run_name': 'demo', 'shots': [{'id': 'scene_'+str(i)} for i in range(1, 4)]}
            self.records[prefix+'editorial.json'] = {'chapters': [{'start_scene': 3}],
                'alternate_draft': {'scene_id': 'scene_2'}, 'replacements': [
                    {'scene_id': 'scene_1', 'revision': 'a'*32}, {'scene_id': 'scene_3', 'revision': 'd'*32}],
                'trims': [{'scene_id': 'scene_1', 'frames': 81}, {'scene_id': 'scene_3', 'frames': 42}],
                'locked_scene_ids': ['scene_1', 'scene_3']}
            for i in range(1, 4):
                self.records[prefix+'checkpoints/clip_%04d.json' % i] = {
                    'segment': {'index': i, 'id': 'scene_'+str(i), 'revision': str(i)*32,
                        'predecessor_revision': str(i-1)*32 if i != 3 else 'f'*32,
                        'seed': 18446744073709551615-i, 'steps': 29, 'raw_frames': 124,
                        'scene_prompt_template': 'Exact prompt é '+str(i),
                        'resolution': {'width': 960 if branch != 'main' else 1344,
                                       'height': 544 if branch != 'main' else 768}}}
        self.raw = {}
        inventory = {}
        for address, value in self.records.items():
            raw = (json.dumps(value, ensure_ascii=False, indent=3)+'\r\n').encode()
            self.raw[address] = raw
            path = self.f.root/address
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            scope, category, immutable = BranchControlDocuments._contract(address)
            inventory[address] = {'source': address, 'sha256': state._hash(raw),
                'scope': scope, 'category': category, 'immutable': immutable}
        self.output = self.f.lab/'branch-control-output'
        self.store = state.create_control_rehearsal(self.f.receipt, self.output, inventory)
        self.access = state.control_rehearsal_access(self.store.project)
        self.access.__enter__()
        self.addCleanup(self.access.__exit__, None, None, None)
        self.branches = self.manager()
        self.legacy = WorkingBranches(self.f.output, 'demo')

    def manager(self, **kwargs):
        return WorkingBranches(self.output, 'demo',
            rehearsal_controls=BranchControlDocuments(self.store, **kwargs))

    def marker(self):
        return (self.store.project/'storage.json').read_bytes()

    def test_actual_load_and_listing_match_v1_without_mutation(self):
        before = self.marker()
        for branch in ('main', self.named):
            self.assertEqual(self.branches.load(branch), self.legacy.load(branch))
        self.assertEqual(self.branches.listing(), self.legacy.listing())
        self.assertEqual(self.marker(), before)
        self.assertFalse((self.store.project/'branches').exists())
        loaded = self.branches.load(self.named)
        self.assertEqual(loaded['authoring']['width'], 960)
        shots = json.loads(loaded['authoring']['plan_json'])['shots']
        self.assertEqual(shots[0]['seed'], '18446744073709551614')
        self.assertEqual(shots[0]['prompt'], 'Exact prompt é 1')
        self.assertEqual(shots[2]['prompt'], 'Exact prompt é 3')  # independent chapter root

    def test_legacy_listing_does_not_create_coordination_files(self):
        output = self.f.lab/'read-only-legacy'
        path = output/'h3_chains/demo/branches/main.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(self.raw['branches/main.json'])
        legacy = WorkingBranches(output, 'demo')
        before = {path.relative_to(output): path.read_bytes()
                  for path in output.rglob('*') if path.is_file()}
        legacy.listing()
        self.assertIsNone(legacy.retry_create('main', 'Empty', self.authored, 0, 'c'*32))
        after = {path.relative_to(output): path.read_bytes()
                 for path in output.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_normal_nodes_and_wrong_access_still_reject(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            WorkingBranches(self.output, 'demo').load(self.named)
        with state.control_rehearsal_access(self.f.root), self.assertRaisesRegex(ValueError, 'copy-only'):
            self.branches.load(self.named)
        with self.assertRaisesRegex(ValueError, 'different project'):
            WorkingBranches(self.f.output, 'demo', rehearsal_controls=BranchControlDocuments(self.store))

    def test_unpublished_candidate_cannot_be_used_as_a_branch_read_pin(self):
        created = []
        original = self.store._write_root
        def write(root, operation, budget):
            reference = original(root, operation, budget)
            created.append(reference)
            return reference
        def interrupt(phase):
            if phase == 'root':
                raise OSError('staged but never published')
        loaded = self.branches.load(self.named)
        with patch.object(self.store, '_write_root', write), self.assertRaises(OSError):
            self.manager(after_stage=interrupt).save(self.named, loaded['authoring'], loaded['revision'])
        candidate = state.Snapshot(self.store.project, created[0])
        with self.assertRaisesRegex(state.StateConflict, 'committed ancestor'):
            self.manager(base=candidate).load(self.named)

    def test_legacy_metadata_directory_is_not_treated_as_missing_original(self):
        output = self.f.lab/'malformed-legacy'
        (output/'h3_chains/demo/branches/main.json').mkdir(parents=True)
        with self.assertRaises(OSError):
            WorkingBranches(output, 'demo').load()

    def test_save_recovery_backup_is_one_publication_and_exact_settings(self):
        loaded = self.branches.load(self.named)
        authored = copy.deepcopy(loaded['authoring'])
        plan = json.loads(authored['plan_json'])
        plan['shots'][0].update(prompt='New chosen prompt 雪', seed='18446744073709551613')
        authored['plan_json'] = json.dumps(plan, ensure_ascii=False)
        base = self.store.snapshot()
        observations = []
        def observe(_):
            view = self.store.snapshot()
            observations.append(view.reference == base.reference)
        saved = self.manager(after_stage=observe).save(self.named, authored, loaded['revision'], 'c'*32)
        self.assertTrue(observations and all(observations))
        view = self.store.snapshot()
        self.assertEqual(view.state['generation'], 1)
        self.assertEqual(json.loads(view.read(saved['authoring_backup'])), self.records['branches/'+self.named+'/branch.json'])
        self.assertEqual(saved, self.branches.load(self.named))
        shots = json.loads(saved['authoring']['plan_json'])['shots']
        self.assertEqual(shots[0]['prompt'], 'New chosen prompt 雪')
        self.assertEqual(shots[0]['seed'], '18446744073709551613')
        self.assertEqual(saved['authoring']['width'], 960)
        self.assertEqual(saved['authoring']['base_seed'], self.authored['base_seed'])
        for address, raw in self.raw.items():
            self.assertEqual((self.f.root/address).read_bytes(), raw)
            self.assertEqual(base.read(address), raw)
        self.assertFalse((self.store.project/'branches').exists())

    def test_interrupted_recovery_save_exposes_neither_half_and_can_retry(self):
        loaded = self.branches.load(self.named)
        before = self.marker()
        def fail(_):
            raise OSError('branch interruption')
        with self.assertRaisesRegex(OSError, 'interruption'):
            self.manager(after_stage=fail).save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        self.assertEqual(self.marker(), before)
        self.assertFalse(any('authoring_backups/' in key for key in self.store.snapshot().state['documents']))
        saved = self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        self.assertEqual(saved, self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32))
        self.assertEqual(self.store.snapshot().state['generation'], 1)

    def test_lost_save_acknowledgement_retry_does_not_publish_again(self):
        loaded = self.branches.load(self.named)
        real = state.atomic_json
        def lost_ack(path, value):
            real(path, value)
            raise OSError('lost acknowledgement')
        with patch.object(state, 'atomic_json', side_effect=lost_ack), self.assertRaises(OSError):
            self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        before = self.marker()
        saved = self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        self.assertEqual(self.marker(), before)
        self.assertEqual(saved, self.branches.load(self.named))
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.branches.save(self.named, dict(loaded['authoring'], width=1280), loaded['revision'], 'c'*32)

    def test_old_save_retry_cannot_replace_a_newer_branch_save(self):
        loaded = self.branches.load(self.named)
        saved = self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        self.branches.save(self.named, saved['authoring'], saved['revision'], 'd'*32)
        before = self.marker()
        with self.assertRaisesRegex(ValueError, 'another workflow'):
            self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        self.assertEqual(before, self.marker())

    def test_fork_keeps_assigned_pointers_and_relevant_alt_trims_atomically(self):
        loaded = self.branches.load(self.named)
        base = self.store.snapshot()
        seen = []
        def observe(_):
            seen.append(self.branches.listing() == self.legacy.listing())
        saved = self.manager(after_stage=observe).create(self.named, 'Fork', loaded['authoring'], 2, 'c'*32)
        self.assertTrue(seen and all(seen))
        view = self.store.snapshot()
        prefix = 'branches/'+saved['id']+'/'
        for i in (1, 2):
            self.assertEqual(json.loads(view.read(prefix+'checkpoints/clip_%04d.json' % i)),
                             self.records['branches/'+self.named+'/checkpoints/clip_%04d.json' % i])
        self.assertNotIn(prefix+'checkpoints/clip_0003.json', view.state['documents'])
        editorial = json.loads(view.read(prefix+'editorial.json'))
        self.assertIsNone(editorial['alternate_draft'])
        self.assertEqual(editorial['replacements'], [{'scene_id': 'scene_1', 'revision': 'a'*32}])
        self.assertEqual(editorial['trims'], [{'scene_id': 'scene_1', 'frames': 81}])
        self.assertEqual(editorial['locked_scene_ids'], ['scene_1'])
        self.assertEqual(json.loads(view.read(prefix+'plan.json'))['_branch_id'], saved['id'])
        self.assertEqual(view.state['generation'], base.state['generation']+1)
        self.assertEqual(self.branches.create(self.named, 'Fork', loaded['authoring'], 2, 'c'*32), saved)
        self.assertEqual(saved, self.branches.load(saved['id']))

    def test_empty_branch_keeps_authoring_but_no_checkpoint_or_alt(self):
        loaded = self.branches.load(self.named)
        saved = self.branches.create(self.named, 'Empty', loaded['authoring'], 0, 'c'*32)
        view = self.store.snapshot()
        prefix = 'branches/'+saved['id']+'/'
        self.assertFalse(any(address.startswith(prefix+'checkpoints/') for address in view.state['documents']))
        self.assertEqual(json.loads(view.read(prefix+'editorial.json'))['replacements'], [])
        self.assertEqual(saved['authoring']['width'], 960)
        self.assertEqual(json.loads(saved['authoring']['plan_json'])['shots'],
                         json.loads(loaded['authoring']['plan_json'])['shots'])

    def test_interrupted_fork_not_listed_and_lost_ack_fork_retries(self):
        loaded = self.branches.load(self.named)
        before = self.marker()
        def fail(_):
            raise OSError('interrupted fork')
        with self.assertRaises(OSError):
            self.manager(after_stage=fail).create(self.named, 'Fork', loaded['authoring'], 2, 'c'*32)
        self.assertEqual(self.marker(), before)
        self.assertNotIn('c'*32, [item['id'] for item in self.branches.listing()['branches']])
        real = state.atomic_json
        def lost_ack(path, value):
            real(path, value)
            raise OSError('lost fork ack')
        with patch.object(state, 'atomic_json', side_effect=lost_ack), self.assertRaises(OSError):
            self.branches.create(self.named, 'Fork', loaded['authoring'], 2, 'c'*32)
        before = self.marker()
        saved = self.branches.create(self.named, 'Fork', loaded['authoring'], 2, 'c'*32)
        self.assertEqual(self.marker(), before)
        self.assertEqual(saved['id'], 'c'*32)

    def test_invalid_fork_leaves_no_partial_controls(self):
        loaded = self.branches.load(self.named)
        before = self.marker()
        with self.assertRaisesRegex(ValueError, 'not saved'):
            self.branches.create(self.named, 'Bad fork', loaded['authoring'], 4, 'c'*32)
        bad = copy.deepcopy(loaded['authoring'])
        bad['plan_json'] = json.dumps({'shots': [{'id': 'wrong', 'prompt': 'wrong'}]})
        with self.assertRaisesRegex(ValueError, 'scene order'):
            self.branches.create(self.named, 'Bad fork', bad, 1, 'c'*32)
        self.assertEqual(self.marker(), before)

    def test_default_changes_only_selection_not_prompts_or_cuts(self):
        before = self.store.snapshot()
        result = self.branches.make_default(self.named)
        self.assertEqual(result['default_branch'], self.named)
        after = self.store.snapshot()
        for address in before.state['documents']:
            self.assertEqual(before.read(address), after.read(address))
        self.assertEqual(after.state['scope_revisions']['branch:'+self.named],
                         before.state['scope_revisions']['branch:'+self.named])
        self.assertEqual(self.branches.make_default('main')['default_branch'], 'main')

    def test_new_assignment_recovers_settings_and_invalidates_old_save_receipt(self):
        loaded = self.branches.load(self.named)
        saved = self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        address = 'branches/'+self.named+'/checkpoints/clip_0001.json'
        changed = copy.deepcopy(self.records[address])
        changed['_authoring_assignment'] = 'new-assignment'
        changed['segment'].update(revision='d'*32, seed=18446744073709551613,
                                  scene_prompt_template='New assigned prompt')
        self.store.commit(self.store.snapshot(), {address: {'data': state._encode(changed),
            'scope': 'branch:'+self.named, 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        recovered = self.branches.load(self.named)
        self.assertEqual(recovered['authoring_recovery']['scenes'], [1])
        self.assertNotEqual(recovered['revision'], saved['revision'])
        self.assertNotIn('last_save_operation', recovered)
        shots = json.loads(recovered['authoring']['plan_json'])['shots']
        self.assertEqual(shots[0]['prompt'], 'New assigned prompt')
        self.assertEqual(shots[0]['seed'], '18446744073709551613')
        self.assertEqual(shots[1:], json.loads(saved['authoring']['plan_json'])['shots'][1:])
        with self.assertRaisesRegex(ValueError, 'another workflow'):
            self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)

    def test_pending_assignment_with_wrong_predecessor_is_not_used_for_recovery(self):
        loaded = self.branches.load(self.named)
        saved = self.branches.save(self.named, loaded['authoring'], loaded['revision'], 'c'*32)
        address = 'branches/'+self.named+'/checkpoints/clip_0002.json'
        changed = copy.deepcopy(self.records[address])
        changed['_authoring_assignment'] = 'stale-assignment'
        changed['segment'].update(predecessor_revision='f'*32, scene_prompt_template='Must not recover')
        self.store.commit(self.store.snapshot(), {address: {'data': state._encode(changed),
            'scope': 'branch:'+self.named, 'category': 'branches', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        self.assertEqual(self.branches.load(self.named), saved)

    def test_stale_and_independent_queued_branch_saves(self):
        base = self.store.snapshot()
        named, main = self.branches.load(self.named), self.branches.load()
        self.manager(base=base).save(self.named, named['authoring'], named['revision'], 'c'*32)
        # Other branch is allowed, merging into the current root.
        self.manager(base=base).save('main', main['authoring'], main['revision'], 'd'*32)
        before = self.marker()
        with self.assertRaisesRegex(state.StateConflict, 'scope changed'):
            self.manager(base=base).save(self.named, named['authoring'], named['revision'], 'e'*32)
        self.assertEqual(self.marker(), before)
        self.assertEqual(self.store.snapshot().state['generation'], 2)

    def test_fork_watches_source_and_epoch_fences_queued_work(self):
        base = self.store.snapshot()
        named = self.branches.load(self.named)
        self.branches.save(self.named, named['authoring'], named['revision'], 'c'*32)
        before = self.marker()
        with self.assertRaisesRegex(state.StateConflict, 'scope changed'):
            self.manager(base=base).create(self.named, 'Stale fork', named['authoring'], 2, 'd'*32)
        self.assertEqual(self.marker(), before)
        base = self.store.snapshot()
        named = self.branches.load(self.named)
        self.store.advance_epoch(base, operation_id=uuid.uuid4().hex)
        before = self.marker()
        with self.assertRaisesRegex(state.StateConflict, 'epoch changed'):
            self.manager(base=base).save(self.named, named['authoring'], named['revision'], 'e'*32)
        self.assertEqual(self.marker(), before)

    def test_pending_restore_journal_and_corrupt_doc_fail_closed(self):
        address = 'branches/'+self.named+'/checkpoints/.transactions/restore.'+'f'*32+'.json'
        self.store.commit(self.store.snapshot(), {address: {'data': b'{}',
            'scope': 'branch:'+self.named, 'category': 'branches', 'immutable': True}}, operation_id=uuid.uuid4().hex)
        before = self.marker()
        with self.assertRaisesRegex(ValueError, 'recovery is pending'):
            self.branches.load(self.named)
        self.assertEqual(before, self.marker())
        ref = self.store.snapshot().state['documents']['branches/main.json']['file']
        (self.store.project/ref['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.branches.load()

    def test_wrong_import_ownership_refused_not_silently_reclassified(self):
        # Simulate an importer freezing an unsupported record; the port cannot
        # reinterpret it as mutable merely because its filename looks familiar.
        base = self.store.snapshot()
        self.store.commit(base, {'branches/default.json': {'data': b'{"branch_id":"main"}',
            'scope': 'archive:legacy', 'category': 'legacy', 'immutable': True}}, operation_id=uuid.uuid4().hex)
        before = self.marker()
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            self.branches.make_default(self.named)
        self.assertEqual(before, self.marker())


if __name__ == '__main__':
    unittest.main()
