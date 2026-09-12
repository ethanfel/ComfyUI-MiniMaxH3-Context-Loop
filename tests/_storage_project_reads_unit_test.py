"""Actual checkpoint graph reads against combined accepted controls and media."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
from threading import Barrier

import _storage_resolver_unit_test as fixture
import storage_state as state
import storage_project as project
import storage_project_migration as migration
from storage_project_reads import ProjectReadView, _read_index
from checkpoint_manager import CheckpointGraphManager
from branch_scope import branch_scope


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.RelocationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.named = 'd'*32
        self.a, self.b, self.alt = 'a'*32, 'b'*32, 'c'*32
        self.docs, self.targets = {}, {}
        for scene, token in ((1, self.a), (2, self.b), (1, self.alt)):
            stem = 'clip_%04d.%s' % (scene, token)
            files = {'segment': 'segments/'+stem+'.mp4', 'checkpoint': 'checkpoints/'+stem+'.safetensors'}
            for kind, address in files.items():
                raw = (kind+token).encode()
                path = self.f.root/address
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                self.targets[address] = {'target': 'media/generation/'+token+'/'+Path(address).name,
                                         'scope': 'archive:source', 'immutable': True}
            segment = {'index': scene, 'id': 'scene_'+str(scene), 'revision': token,
                       'seed': '18446744073709551613', 'raw_frames': 39, 'delivered_frames': 34,
                       'scene_prompt': 'Stable prompt '+token, 'context_length': 0, 'audio_context_length': 0,
                       **{key: self.f.address(value) for key, value in files.items()}}
            if scene == 2:
                segment['predecessor_revision'] = self.a
            if token == self.alt:
                segment.update(take_kind='editorial_alternate', alternate_of_revision=self.a,
                               alternate_media_mode='picture_only')
            metadata = {'run_name': 'demo', 'segment': segment, 'created_at': '2026-01-02T03:04:05Z',
                        'compatibility': {'width': 960, 'height': 544}}
            address = 'checkpoints/'+stem+'.json'
            self.docs[address] = metadata
            if token != self.alt:
                self.docs['checkpoints/clip_%04d.json' % scene] = metadata
        self.docs['branches/'+self.named+'/branch.json'] = {'id': self.named}
        self.docs['branches/'+self.named+'/checkpoints/clip_0001.json'] = self.docs['checkpoints/clip_0001.json']
        self.docs['editorial.json'] = {'chapters': [{'start_scene': 1}]}
        for address, value in self.docs.items():
            path = self.f.root/address
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(state._encode(value))
        # Keep the original PNG fixture and index as part of exact import.
        self.targets[self.f.png+'/frame_00000001.png'] = {
            'target': self.f.export+'/frame_00000001.png', 'scope': 'archive:source', 'immutable': True}
        self.inventory = {}
        for path in self.f.root.rglob('*'):
            if path.is_file() and path.suffix == '.json':
                address = path.relative_to(self.f.root).as_posix()
                self.inventory[address] = {'source': address, 'sha256': state._hash(path.read_bytes()),
                    'scope': 'branch:A', 'category': 'takes', 'immutable': False}
        self.output = self.f.lab/'combined-output'
        control = state.create_control_rehearsal(self.f.receipt, self.output, self.inventory)
        self.store = project.ProjectStore(control.project)
        access = state.control_rehearsal_access(self.store.project)
        access.__enter__()
        self.addCleanup(access.__exit__, None, None, None)
        journal = migration.prepare_join(self.f.receipt, control, self.f.lab/'join', self.targets)
        migration.join_payloads(journal)
        self.base = self.store.snapshot()
        self.view = ProjectReadView(self.store)
        self.manager = CheckpointGraphManager(self.output, rehearsal_view=self.view)
        self.old = CheckpointGraphManager(self.f.output)

    @staticmethod
    def semantic_graph(graph):
        graph = copy.deepcopy(graph)
        for record in graph['revisions']:
            for key in ('video', 'audio', 'preview_video'):
                record.pop(key, None)  # URLs must now identify new physical paths.
        return graph

    def test_actual_graph_and_lineage_match_without_legacy_metadata_directories(self):
        self.assertFalse((self.store.project/'checkpoints').exists())
        old = self.old.graph('demo', adopt_legacy=False)
        current = self.manager.graph('demo', adopt_legacy=False)
        self.assertEqual(self.semantic_graph(current), self.semantic_graph(old))
        self.assertEqual(self.manager.active_selection('demo'), self.old.active_selection('demo'))
        self.assertEqual(self.base.reference, self.store.snapshot().reference)
        for row in current['revisions']:
            self.assertTrue(row['ready'])
            self.assertTrue((self.output/row['video']['subfolder']/row['video']['filename']).exists())
            self.assertEqual(row['seed'], '18446744073709551613')

    def test_repeated_direct_reads_never_build_payload_catalog_or_audit_other_files(self):
        _read_index.cache_clear()
        verified = []
        original = state.Snapshot.verify
        def verify(snapshot, addresses=None):
            self.assertIsNotNone(addresses, 'Ordinary loading started a full-project audit')
            verified.append(set(addresses))
            return original(snapshot, addresses)
        with patch.object(project, 'payload_catalog', side_effect=AssertionError('Full payload scan')), \
                patch.object(state.Snapshot, 'verify', verify):
            for _ in range(3):
                view = ProjectReadView(self.store)
                with view.operation():
                    self.assertEqual(view.read(self.f.address('editorial.json')), self.docs['editorial.json'])
                    path = view.path(self.f.address('segments/clip_0001.'+self.a+'.mp4'))
                    self.assertTrue(path.is_file())
            with ProjectReadView(self.store).operation():
                pass  # storage handshake must not enumerate files
        self.assertEqual(verified, [
            {'editorial.json', project.payload_key('segments/clip_0001.'+self.a+'.mp4')}]*3+[set()])

    def test_parallel_listings_share_one_index_but_new_root_gets_its_own(self):
        _read_index.cache_clear()
        barrier = Barrier(4)
        def read():
            barrier.wait(timeout=5)
            view = ProjectReadView(self.store)
            with view.operation():
                return view.names(self.store.project/'checkpoints')
        with patch.object(project, 'payload_catalog', wraps=project.payload_catalog) as build:
            with ThreadPoolExecutor(max_workers=4) as workers:
                futures = [workers.submit(copy_context().run, read) for _ in range(4)]
                results = [future.result(timeout=10) for future in futures]
            self.assertTrue(all(result == results[0] for result in results))
            self.assertEqual(build.call_count, 1)
            with ProjectReadView(self.store).operation():
                pass
            self.assertEqual(build.call_count, 1)
        # Publication still audits; count only the subsequent read index builds.
        self.store.commit(self.base, {'checkpoints/new.json':dict(data=b'{}',
            scope='branch:A', category='takes', immutable=False)}, operation_id=uuid.uuid4().hex)
        with patch.object(project, 'payload_catalog', wraps=project.payload_catalog) as build:
            latest, historical = ProjectReadView(self.store), ProjectReadView(self.store, base=self.base)
            with latest.operation():
                self.assertIn('new.json', latest.names(self.store.project/'checkpoints'))
            with historical.operation():
                self.assertNotIn('new.json', historical.names(self.store.project/'checkpoints'))
            self.assertEqual(build.call_count, 1)

    def test_warm_index_does_not_mask_changed_payload_descriptor(self):
        address = 'segments/clip_0001.'+self.a+'.mp4'
        with self.view.operation():
            self.view.names(self.store.project/'segments')
        descriptor = self.base.state['documents'][project.payload_key(address)]
        (self.store.project/descriptor['file']['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'checksum'), self.view.operation():
            try:
                self.view.path(self.f.address(address))
            except ValueError:
                pass  # legacy callers cannot swallow a used file's corruption

    def test_used_document_changed_after_read_is_rejected_at_operation_end(self):
        address = 'editorial.json'
        with self.assertRaisesRegex(ValueError, 'checksum'), self.view.operation():
            self.view.read(self.f.address(address))
            path = self.store.project/self.base.state['documents'][address]['file']['path']
            path.write_bytes(b'{}')

    def test_nested_read_traces_collect_only_used_accepted_identities(self):
        metadata = 'checkpoints/clip_0001.'+self.a+'.json'
        segment = 'segments/clip_0001.'+self.a+'.mp4'
        with self.view.operation():
            with self.view.track_reads() as outer:
                self.view.read(self.f.address(metadata))
                with self.view.track_reads() as inner:
                    self.view.path(self.f.address(segment))
                    self.view.path(self.f.address('segments/missing.mp4'))
                self.view.read(self.f.address('editorial.json'))
            self.view.read(self.f.address('checkpoints/clip_0002.json'))
        self.assertEqual(inner, {project.payload_key(segment)})
        self.assertEqual(outer, {metadata, project.payload_key(segment), 'editorial.json'})
        self.assertEqual(self.store.snapshot().reference, self.base.reference)

    def archive_controls(self, addresses):
        raw = b'{"cache":[NaN],"limit":Infinity,"seed":18446744073709551613}'
        return self.store.commit(self.store.snapshot(), {
            address:dict(data=raw, scope='branch:A', category='branches', immutable=False)
            for address in addresses}, operation_id=uuid.uuid4().hex)

    def test_workflow_archive_preserves_opaque_sentinels_and_tracks_verified_bytes(self):
        addresses = ['workflow.json', 'api_prompt.json', 'branches/'+self.named+'/workflow.json',
                     'recovery_archives/'+self.a+'/api_prompt.json']
        self.archive_controls(addresses)
        with self.view.operation(), self.view.track_reads() as reads:
            for address in addresses:
                role = Path(address).stem
                decoded = self.view.read_workflow_archive(self.f.address(address), role)
                self.assertTrue(math.isnan(decoded['cache'][0]))
                self.assertTrue(math.isinf(decoded['limit']))
                self.assertEqual(decoded['seed'], 18446744073709551613)
        self.assertEqual(reads, set(addresses))

    def test_opaque_archive_reader_does_not_relax_plan_or_other_authority(self):
        addresses = ['plan.json', 'workflow.json', 'jobs/api_prompt.json']
        self.archive_controls(addresses)
        with self.view.operation():
            for address in addresses:
                with self.subTest(address=address), self.assertRaisesRegex(ValueError, 'Non-finite'):
                    self.view.read(self.f.address(address))
            for address,role in [('plan.json','plan'), ('plan.json','workflow'),
                                 ('workflow.json','api_prompt'), ('jobs/api_prompt.json','api_prompt')]:
                with self.subTest(address=address,role=role), self.assertRaisesRegex(ValueError, 'archive role'):
                    self.view.read_workflow_archive(self.f.address(address), role)

    def test_workflow_archive_reader_rejects_unaccepted_leftovers_and_corruption(self):
        leftover = self.store.project/'workflow.json'
        leftover.write_bytes(b'{"cache":NaN}')
        with self.view.operation(), self.assertRaisesRegex(FileNotFoundError, 'Missing accepted'):
            self.view.read_workflow_archive(leftover, 'workflow')
        self.archive_controls(['api_prompt.json'])
        with self.view.operation():
            path = self.view.path(self.f.address('api_prompt.json'))
        path.write_bytes(b'{"cache":NaN}')
        with self.assertRaisesRegex(ValueError, 'checksum'), self.view.operation():
            self.view.read_workflow_archive(self.f.address('api_prompt.json'), 'api_prompt')

    def test_named_branch_active_selection_uses_pinned_virtual_controls(self):
        with branch_scope('demo', self.named):
            self.assertEqual(self.manager.active_selection('demo'), self.old.active_selection('demo'))
            self.assertEqual(self.semantic_graph(self.manager.graph('demo', adopt_legacy=False)),
                             self.semantic_graph(self.old.graph('demo', adopt_legacy=False)))

    def test_missing_accepted_payload_reports_broken_revision_not_hidden_take(self):
        with self.view.operation():
            path = self.view.path(self.f.address('segments/clip_0002.'+self.b+'.mp4'))
        path.rename(self.f.lab/'retained-video')
        result = self.manager.graph('demo', adopt_legacy=False)
        self.assertEqual(len(result['revisions']), 3)
        row = next(row for row in result['revisions'] if row['revision'] == self.b)
        self.assertFalse(row['ready'])
        self.assertIn('Segment video', row['missing_files'])

    def test_corrupted_control_does_not_silently_disappear_from_graph(self):
        address = 'checkpoints/clip_0002.'+self.b+'.json'
        path = self.store.project/self.base.state['documents'][address]['file']['path']
        path.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.manager.graph('demo', adopt_legacy=False)

    def test_graph_rejects_legacy_mutation_and_adoption_before_writes(self):
        for call in (lambda: self.manager.graph('demo'),
                     lambda: self.manager.attribute('demo', 1, self.a, 2, self.b),
                     lambda: self.manager.deletion_preview('demo', 1, self.alt),
                     lambda: self.manager.delete('demo', 1, self.alt)):
            with self.assertRaisesRegex(ValueError, 'read-only'):
                call()
        self.assertEqual(self.base.reference, self.store.snapshot().reference)

    def test_read_view_never_adopts_unindexed_legacy_file(self):
        path = self.store.project/'segments/unindexed.mp4'
        path.parent.mkdir()
        path.write_bytes(b'foreign bytes')
        with self.view.operation(), self.assertRaisesRegex(ValueError, 'Unindexed'):
            self.view.path(path)
        with self.view.operation(), self.assertRaisesRegex(ValueError, 'another output'):
            self.view.path('h3_chains/other/segments/test.mp4')

    def test_operation_pins_earlier_root_while_another_branch_save_publishes(self):
        old = self.manager.graph('demo', adopt_legacy=False)
        real = self.manager._public_graph
        def update(scan):
            self.store.commit(self.base, {'checkpoints/clip_0002.json': {'data': b'{}',
                'scope': 'branch:A', 'category': 'takes', 'immutable': False}}, operation_id=uuid.uuid4().hex)
            return real(scan)
        with patch.object(self.manager, '_public_graph', side_effect=update):
            observed = self.manager.graph('demo', adopt_legacy=False)
        self.assertEqual(observed, old)
        current = self.manager.graph('demo', adopt_legacy=False)
        self.assertFalse(next(row for row in current['revisions'] if row['revision'] == self.b)['active'])
        pinned = CheckpointGraphManager(self.output, rehearsal_view=ProjectReadView(self.store, base=self.base))
        self.assertEqual(pinned.graph('demo', adopt_legacy=False), old)

    def test_access_and_project_identity_are_rechecked(self):
        with self.assertRaisesRegex(ValueError, 'different project'):
            self.manager.graph('other', adopt_legacy=False)
        with state.control_rehearsal_access(self.f.root), self.assertRaisesRegex(ValueError, 'copy-only'):
            self.manager.graph('demo', adopt_legacy=False)
        with self.assertRaisesRegex(ValueError, 'pinned operation'):
            self.view.read(self.store.project/'editorial.json')

    def test_unpublished_root_cannot_be_supplied_as_a_read_pin(self):
        def fail(phase):
            if phase == 'root':
                raise OSError('root staged but never accepted')
        known = set((self.store.project/'project/roots').glob('*.json'))
        with self.assertRaises(OSError):
            self.store.commit(self.base, {'editorial.json': {'data': b'{}', 'scope': 'branch:A',
                'category': 'takes', 'immutable': False}}, operation_id=uuid.uuid4().hex, after_stage=fail)
        candidate = next(iter(set((self.store.project/'project/roots').glob('*.json')) - known))
        raw = candidate.read_bytes()
        staged = state.Snapshot(self.store.project, {'path': candidate.relative_to(self.store.project).as_posix(),
            'sha256': state._hash(raw), 'size': len(raw)})
        manager = CheckpointGraphManager(self.output, rehearsal_view=ProjectReadView(self.store, base=staged))
        with self.assertRaisesRegex(ValueError, 'not a committed ancestor'):
            manager.graph('demo', adopt_legacy=False)

    def recovery_asset(self, group='audio'):
        data = b'exact recovery source bytes'
        source = self.f.lab/'audio-source.wav'
        source.write_bytes(data)
        token = uuid.uuid4().hex
        entry = dict(relative_path=group+'/source.wav', sha256=state._hash(data), size=len(data))
        self.catalogue = dict(format='h3_project_assets_v1', project='demo', assets=[entry])
        staged = self.store.stage_payload('project_assets/'+entry['relative_path'], source,
            'project/assets/'+token+'/source.wav', scope='archive:source', operation_id=token)
        self.store.commit_artifacts(self.store.snapshot(), {'project_assets/catalog.json':
            dict(data=state._encode(self.catalogue),scope='project',category='assets',immutable=False)},
            [staged],operation_id=uuid.uuid4().hex)
        return {'path':'/old/machine/input/source.wav','file_sha256':state._hash(data)}

    def test_recovery_asset_uses_accepted_bytes_without_modifying_descriptor(self):
        descriptor = self.recovery_asset()
        before = self.store.snapshot().reference
        original = copy.deepcopy(descriptor)
        with self.view.operation():
            path = self.view.recovery_asset(descriptor)
            self.assertEqual(path.read_bytes(), b'exact recovery source bytes')
            self.assertIn('/project/assets/', str(path))
            self.assertIsNone(self.view.recovery_asset(dict(descriptor,file_sha256='f'*64)))
        self.assertEqual(descriptor,original)
        self.assertEqual(before,self.store.snapshot().reference)

    def test_recovery_asset_keeps_pinned_catalogue_after_later_change(self):
        descriptor = self.recovery_asset()
        base = self.store.snapshot()
        self.catalogue['assets'] = []
        self.store.commit(base,{'project_assets/catalog.json':dict(data=state._encode(self.catalogue),
            scope='project',category='assets',immutable=False)},operation_id=uuid.uuid4().hex)
        historical = ProjectReadView(self.store,base=base)
        with historical.operation():
            self.assertIsNotNone(historical.recovery_asset(descriptor))
        with self.view.operation():
            self.assertIsNone(self.view.recovery_asset(descriptor))

    def test_recovery_asset_supports_the_existing_plural_videos_directory(self):
        descriptor = self.recovery_asset(group='videos')
        with self.view.operation():
            self.assertEqual(self.view.recovery_asset(descriptor).read_bytes(), b'exact recovery source bytes')

    def test_recovery_asset_rejects_corrupt_payload_or_forged_catalogue(self):
        descriptor = self.recovery_asset()
        with self.view.operation():
            path = self.view.recovery_asset(descriptor)
        path.write_bytes(b'wrong')
        with self.view.operation(),self.assertRaisesRegex(state.StateConflict,'checksum'):
            self.view.recovery_asset(descriptor)
        self.catalogue['assets'][0]['relative_path'] = '../escape.wav'
        self.store.commit(self.store.snapshot(),{'project_assets/catalog.json':dict(
            data=state._encode(self.catalogue),scope='project',category='assets',immutable=False)},
            operation_id=uuid.uuid4().hex)
        with self.view.operation(),self.assertRaises(ValueError):
            self.view.recovery_asset(descriptor)


if __name__ == '__main__':
    unittest.main(verbosity=2)
