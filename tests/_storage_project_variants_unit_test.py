"""Processing catalogue parity on independently joined fixture data."""
import copy
import json
import unittest
import uuid
from pathlib import Path

import _checkpoint_variants_unit_test as fixture
import storage_state as state
import storage_project as project
import storage_project_migration as migration
from storage_project_reads import ProjectReadView
from branch_scope import branch_scope
from checkpoint_variants import saved_checkpoint_variants


class ProjectVariantTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.VariantTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.lab, self.f.root = self.f.root, self.f.root/'source-output'
        self.f.save(profile='plain')
        self.f.save(profile='pixel', backend='pixel', revision='d'*32)
        path, saved = self.f.save(profile='motion', recipe={'derope': True}, chapter=True, revision='e'*32)
        self.f.write(path.parent.parent/'upscale_manifest.json', {
            'format': 'h3_chain_upscale_manifest_v1', 'run_name': 'demo', 'profile': 'motion', 'segments': [saved['segment']]})
        self.f.save(profile='ltx', backend='ltx_2_5', revision='f'*32)
        source = self.f.root/'h3_chains/demo'
        self.named = '1'*32
        branch = source/'branches'/self.named
        branch.mkdir(parents=True)
        (branch/'branch.json').write_bytes(b'{}')
        inventory, targets = {}, {}
        for path in source.rglob('*'):
            if not path.is_file():
                continue
            address, raw = path.relative_to(source).as_posix(), path.read_bytes()
            if path.suffix == '.json':
                inventory[address] = {'source': address, 'sha256': state._hash(raw),
                    'scope': 'pass:test', 'category': 'passes', 'immutable': False}
            else:
                targets[address] = {'target': 'media/custom/'+state._hash(address.encode())[:32]+'/artifact'+path.suffix,
                                    'scope': 'pass:test', 'immutable': True}
        receipt = self.lab/'copy-receipt.json'
        state.atomic_json(receipt, {'copy': str(source), 'source': str(self.lab/'not-this-copy'), 'independent_copies': True})
        self.output = self.lab/'combined-output'
        control = state.create_control_rehearsal(receipt, self.output, inventory)
        self.store = project.ProjectStore(control.project)
        access = state.control_rehearsal_access(control.project)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        journal = migration.prepare_join(receipt, control, self.lab/'join', targets)
        migration.join_payloads(journal)
        self.view = ProjectReadView(self.store)
        self.base = self.store.snapshot()

    def scan(self):
        return saved_checkpoint_variants(self.output, 'demo', self.f.originals, rehearsal_view=self.view)

    @staticmethod
    def semantic(result):
        result = copy.deepcopy(result)
        for row in result['variants']:
            for field in ('video', 'audio'):
                row.pop(field, None)
        return result

    def test_all_stages_profiles_chapters_and_legacy_lineage_match(self):
        expected, actual = self.f.scan(), self.scan()
        self.assertEqual(self.semantic(actual), self.semantic(expected))
        self.assertEqual({row['stage'] for row in actual['variants']}, set(fixture.module.STAGES))
        self.assertEqual(self.store.snapshot().reference, self.base.reference)
        ordering = [(row['profile_path'], row['path'], row['kind']) for row in actual['branches']]
        self.assertEqual(ordering, sorted(ordering))
        self.assertFalse((self.store.project/'upscaled').exists())
        for row in actual['variants']:
            self.assertTrue(row['ready'])
            for field in ('video', 'audio'):
                url = row[field]
                self.assertTrue((self.output/url['subfolder']/url['filename']).is_file())

    def test_selected_named_branch_does_not_fall_back_to_original_processing(self):
        with branch_scope('demo', self.named):
            self.assertEqual(self.scan(), self.f.scan())
            self.assertEqual(self.scan()['variants'], [])

    def test_corrupt_control_fails_whole_operation_not_a_silent_empty_catalogue(self):
        address = next(p for p in self.base.state['documents'] if '/checkpoints/' in p)
        path = self.store.project/self.base.state['documents'][address]['file']['path']
        path.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.scan()

    def test_missing_payload_keeps_processed_take_visible_but_broken(self):
        address = next(p for p in project.payload_catalog(self.base) if p.endswith('.mp4'))
        self.store.payload_path(self.base, address).rename(self.lab/'retained-video')
        actual = self.scan()
        self.assertEqual(len(actual['variants']), 4)
        self.assertEqual(sum(not v['ready'] for v in actual['variants']), 1)

    def test_historical_pin_does_not_follow_a_later_metadata_edit(self):
        old = self.scan()
        address = next(p for p in self.base.state['documents'] if '/checkpoints/' in p and p.count('.') == 2)
        value = json.loads(self.base.read(address))
        value['segment']['latent_saved'] = False
        self.store.commit(self.base, {address: {'data': state._encode(value), 'scope': 'pass:test',
            'category': 'passes', 'immutable': False}}, operation_id=uuid.uuid4().hex)
        self.view = ProjectReadView(self.store, base=self.base)
        self.assertEqual(self.scan(), old)
        self.view = ProjectReadView(self.store)
        self.assertNotEqual(self.scan(), old)


if __name__ == '__main__':
    unittest.main(verbosity=2)
