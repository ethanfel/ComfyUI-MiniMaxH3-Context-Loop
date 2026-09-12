"""Sealed chapter retention must use accepted logical controls, not old paths."""
import copy
import unittest
import uuid

import _storage_project_reads_unit_test as fixture

state = fixture.state


class RetentionReferenceTests(unittest.TestCase):
    setUp = fixture.ReadTests.setUp

    def add_snapshot(self, value=None, *, branch='', token=None):
        token = token or uuid.uuid4().hex
        address = (('branches/'+branch+'/') if branch else '')+'chapters/01_first/manifests/'+token+'.json'
        document = value if value is not None else dict(format='h3_chain_chapter_manifest_v1',
            run_name='demo', chapter=dict(number=1, title='First'),
            segments=[copy.deepcopy(self.docs['checkpoints/clip_0001.json']['segment'])])
        self.store.commit(self.store.snapshot(), {address:dict(data=state._encode(document),
            scope='branch:'+(branch or 'main'), category='cuts', immutable=True)}, operation_id=uuid.uuid4().hex)
        return address, document

    def references(self, token=None, manager=None):
        manager = manager or self.manager
        with manager._read_operation():
            scan = manager._scan('demo', adopt_legacy=False)
            record = scan['records'][(1, token or self.a)]
            return manager._chapter_references(scan, token or self.a, manager._artifacts(scan, record))

    def test_main_and_named_sealed_cuts_pin_their_generation_revision_without_legacy_folders(self):
        first, _ = self.add_snapshot()
        named, _ = self.add_snapshot(branch=self.named)
        self.assertFalse((self.store.project/'chapters').exists())
        before = self.store.snapshot().reference
        references = self.references()
        self.assertEqual({r['path'] for r in references}, {'h3_chains/demo/'+first, 'h3_chains/demo/'+named})
        self.assertTrue(all(r['number'] == 1 for r in references))
        self.assertEqual(before, self.store.snapshot().reference)

    def test_same_bytes_as_legacy_reference_check(self):
        address, document = self.add_snapshot()
        path = self.f.root/address
        path.parent.mkdir(parents=True)
        path.write_bytes(state._encode(document))
        self.assertEqual(self.references(), self.references(manager=self.old))

    def test_owned_artifact_reference_in_other_snapshot_is_not_lost_during_relocation(self):
        for separator in ('/', '\\'):
            segment = dict(self.docs['checkpoints/clip_0001.json']['segment'])
            segment.pop('revision')
            # An external snapshot may retain only the saved artifact, without
            # the original generation revision token as a standalone field.
            segment = {'segment': segment['segment'].replace('/', separator)}
            self.add_snapshot(dict(format='h3_chain_chapter_manifest_v1', run_name='demo',
                chapter=dict(number=2), segments=[segment]))
        self.assertEqual(len(self.references()), 2)

    def test_supersedes_history_does_not_pin_the_old_take(self):
        segment = dict(self.docs['checkpoints/clip_0002.json']['segment'])
        segment.pop('predecessor_revision')
        segment['supersedes'] = self.a
        self.add_snapshot(dict(format='h3_chain_chapter_manifest_v1', run_name='demo',
            chapter=dict(number=2), segments=[segment]))
        self.assertEqual(self.references(), [])

    def test_snapshot_added_after_read_pin_is_not_adopted(self):
        pinned = fixture.CheckpointGraphManager(self.output,
            rehearsal_view=fixture.ProjectReadView(self.store, base=self.base))
        self.add_snapshot()
        self.assertEqual(self.references(manager=pinned), [])
        self.assertEqual(len(self.references()), 1)

    def test_external_references_do_not_falsely_pin_this_projects_take(self):
        segment = dict(self.docs['checkpoints/clip_0002.json']['segment'])
        segment.pop('predecessor_revision')
        self.add_snapshot(dict(format='h3_chain_chapter_manifest_v1', run_name='demo',
            chapter=dict(number=2), segments=[segment], references=[
                '/external/reference.png', 'h3_chains/another/segments/reference.mp4']))
        self.assertEqual(self.references(), [])

    def test_invalid_accepted_snapshot_blocks_cleanup_instead_of_disappearing(self):
        self.add_snapshot(dict(format='h3_chain_chapter_manifest_v1', run_name='other', segments=[]))
        references = self.references()
        self.assertEqual(len(references), 1)
        self.assertIn('Cannot verify sealed chapter recovery', references[0]['error'])

    def test_unindexed_legacy_snapshot_is_not_adopted(self):
        path = self.store.project/'chapters/01_first/manifests'/('e'*32+'.json')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{}')
        self.assertEqual(self.references(), [])


if __name__ == '__main__':
    unittest.main(argv=[__file__])
