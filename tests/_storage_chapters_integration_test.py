"""Public Chapter Delivery/Load on independent organized-storage fixtures."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
from types import SimpleNamespace

import _storage_latent_exports_integration_test as fixture

chain, upscale, carriers, runtime, state = (getattr(fixture, name) for name in
    ('chain', 'upscale', 'carriers', 'runtime', 'state'))
chapters = fixture.module('storage_chapters')
function = chain.MiniMaxH3ChainChapterDelivery.select


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.LatentExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof, self.manifest = self.f.store, self.f.proof, self.f.manifest
        self.namespace = uuid.uuid4().hex

    def seal(self, manifest=None, *, unique_id='chapter', enabled=True, number=0):
        with carriers.node_host(self.store, export_writers=(function,), operation_namespace=self.namespace):
            return chain.MiniMaxH3ChainChapterDelivery().select(
                manifest or self.manifest, enabled, number, unique_id=unique_id)

    def load(self, token='', *, plan=None, branch='main'):
        with carriers.node_host(self.store):
            return chain.MiniMaxH3ChainChapterLoad().load(self.store.project.name, 1, token,
                plan=plan, working_branch_id=branch)

    def address(self, sealed):
        return sealed['chapter_manifest_path'].removeprefix('h3_chains/'+self.store.project.name+'/')

    def test_seal_load_keeps_exact_takes_editorial_and_identity_without_legacy_writes(self):
        before = self.store.snapshot()
        original = copy.deepcopy(self.manifest)
        with patch.object(chain, '_persist_chapter_manifest', side_effect=AssertionError('legacy write')):
            result = self.seal()
        sealed, serialized, number, path, status = result
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        self.assertEqual(original, self.manifest)
        self.assertEqual(sealed['segments'], original['segments'])
        self.assertEqual(sealed['chapter_manifest_id'], chain._chapter_manifest_digest(sealed))
        self.assertEqual(json.loads(serialized), sealed)
        self.assertEqual(number, 1)
        self.assertIn('saved Chapter 1', status)
        self.assertTrue(Path(path).is_file())
        self.assertFalse((self.store.project/'chapters').exists())
        stored = state._decode(after.read(self.address(sealed)))
        self.assertNotIn('_storage_pin', stored)
        self.assertNotIn('_project_ownership', stored)
        loaded, raw, loaded_path, _ = self.load()
        self.assertEqual(loaded_path, path)
        self.assertEqual(json.loads(raw), loaded)
        self.assertEqual(chapters.body(loaded), chapters.body(sealed))
        self.assertNotIn('_project_ownership', loaded)
        self.assertEqual(loaded['_storage_pin']['root'], after.reference)
        self.assertEqual(self.store.snapshot().reference, after.reference)
        with runtime.runtime_access(self.store, pin=loaded['_storage_pin']) as bound:
            base_audio, pictures, _, sources = chain._checkpoint_export_views(loaded, rehearsal_view=bound.reader)
            self.assertEqual(pictures[0]['revision'], self.f.f.f.f.alt['revision'])
            self.assertNotEqual(base_audio[0]['revision'], pictures[0]['revision'])
            self.assertTrue(sources)
        self.store.verify_payloads()

    def test_same_invocation_retry_and_reseal_do_not_advance_latest(self):
        first = self.seal()
        accepted = self.store.snapshot().reference
        self.assertEqual(self.seal(), first)
        self.assertEqual(self.store.snapshot().reference, accepted)
        newer = self.f.repin()
        newer.setdefault('editorial', {})['subtitles'] = {'mode': 'off', 'offset_seconds': 1}
        second = self.seal(newer, unique_id='new-cut')
        self.assertNotEqual(first[0]['chapter_manifest_id'], second[0]['chapter_manifest_id'])
        latest = self.store.snapshot().reference
        self.assertEqual(self.seal(), first)
        self.assertEqual(self.store.snapshot().reference, latest)
        resealed = self.seal(self.f.repin(first[0]), unique_id='old-seal')
        self.assertEqual(resealed[0]['chapter_manifest_id'], first[0]['chapter_manifest_id'])
        self.assertEqual(self.load()[0]['chapter_manifest_id'], second[0]['chapter_manifest_id'])
        self.assertEqual(self.load(first[0]['chapter_manifest_id'])[0]['chapter_manifest_id'], first[0]['chapter_manifest_id'])

    def test_disabled_is_readonly_and_enabled_requires_exact_grant_operation_and_owner(self):
        before = self.store.snapshot().reference
        with carriers.node_host(self.store):
            result = chain.MiniMaxH3ChainChapterDelivery().select(self.manifest, False)
            self.assertEqual(result[0], self.manifest)
            with self.assertRaisesRegex(ValueError, 'export writes'):
                chain.MiniMaxH3ChainChapterDelivery().select(self.manifest)
        with carriers.node_host(self.store, export_writers=(function,)):
            with self.assertRaisesRegex(ValueError, 'operation ID'):
                chain.MiniMaxH3ChainChapterDelivery().select(self.manifest)
        ownerless = dict(self.manifest, _project_ownership=None)
        with self.assertRaises(fixture.module('project_ownership').ProjectOwnershipError):
            self.seal(ownerless)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_internal_loader_is_pinned_and_internal_legacy_writer_cannot_bypass_host(self):
        sealed = self.seal()[0]
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store) as bound:
            loaded, _ = chain._load_chapter_manifest(self.store.project.name, 1, sealed['chapter_manifest_id'])
            self.assertEqual(loaded['chapter_manifest_id'], sealed['chapter_manifest_id'])
            self.assertEqual(loaded['_storage_pin'], bound.pin)
            self.assertNotIn('_project_ownership', loaded)
            with self.assertRaisesRegex(ValueError, 'host operation ID'):
                chain._persist_chapter_manifest(loaded)
        self.assertEqual(self.store.snapshot().reference, before)
        self.assertEqual(chain.MiniMaxH3ChainChapterDelivery.INPUT_TYPES()['hidden']['unique_id'], 'UNIQUE_ID')
        self.assertEqual(chain.MiniMaxH3ChainChapterLoad.INPUT_TYPES()['optional']['plan'][0], chain.PLAN_TYPE)

    def test_forged_seed_and_prompt_and_changed_retry_are_rejected(self):
        before = self.store.snapshot().reference
        for key, value in [('seed', 17), ('prompt', 'Not the saved prompt')]:
            forged = copy.deepcopy(self.manifest)
            forged['segments'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'immutable metadata'):
                self.seal(forged)
            self.assertEqual(self.store.snapshot().reference, before)
        self.seal()
        changed = copy.deepcopy(self.manifest)
        changed['editorial'] = {}
        with self.assertRaisesRegex(ValueError, 'different inputs'):
            self.seal(changed)

    def test_load_uses_only_caller_ownership_and_cannot_fall_back_to_unaccepted_files(self):
        sealed = self.seal()[0]
        caller = dict(run_name=self.store.project.name, _branch_id='main',
                      _storage_pin=sealed['_storage_pin'], _project_ownership=self.proof)
        loaded = self.load(plan=caller)[0]
        self.assertEqual(loaded['_project_ownership'], self.proof)
        wrong = dict(caller, run_name='another-project')
        with self.assertRaisesRegex(ValueError, 'different project'):
            self.load(plan=wrong)
        before = self.store.snapshot().reference
        path = self.store.project/self.address(sealed)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(sealed))
        unknown = uuid.uuid4().hex
        (path.parent/(unknown+'.json')).write_text(json.dumps(sealed))
        with self.assertRaises(FileNotFoundError):
            self.load(unknown)
        accepted = self.store.project/self.store.snapshot().state['documents'][self.address(sealed)]['file']['path']
        accepted.write_bytes(accepted.read_bytes()+b' ')
        with self.assertRaises(ValueError):
            self.load(sealed['chapter_manifest_id'])
        self.assertEqual(self.store.snapshot().reference, before)

    def test_interrupted_publication_and_lost_acknowledgement_reuse_exact_seal(self):
        operation = uuid.uuid4().hex
        def seal(bound):
            return chapters.ChapterSnapshots(bound, chain, upscale).seal(self.manifest, 0, operation)
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=self.manifest['_storage_pin'], export_writes=True) as bound:
            def fail(stage):
                if stage == 'document':
                    raise OSError('injected chapter publication failure')
            bound.exports.after_stage = fail
            with self.assertRaisesRegex(OSError, 'injected'):
                seal(bound)
        self.assertEqual(self.store.snapshot().reference, before)
        request = state._decode((self.store.project/'project/jobs'/operation/'chapter-request.json').read_bytes())
        with runtime.runtime_access(self.store, pin=self.manifest['_storage_pin'], export_writes=True) as bound:
            def lost_ack(stage):
                if stage == 'commit':
                    raise OSError('lost chapter ack')
            bound.exports.after_stage = lost_ack
            with self.assertRaisesRegex(OSError, 'lost chapter ack'):
                seal(bound)
        after = self.store.snapshot().reference
        self.assertNotEqual(before, after)
        with runtime.runtime_access(self.store, pin=self.manifest['_storage_pin'], export_writes=True) as bound:
            result, _ = seal(bound)
        self.assertEqual(result['sealed_at'], request['sealed_at'])
        self.assertEqual(self.store.snapshot().reference, after)

    def test_named_branch_seal_load_preserves_main_and_all_active_assignments(self):
        f = self.f.f.f
        with runtime.runtime_access(self.store, branch_writes=True):
            branch = fixture.module('working_branches').WorkingBranches(f.output, f.run).create(
                'main', 'Chapter fork', {'plan_json':json.dumps({'shots':[
                    {'id':s['id'], 'prompt':s['prompt']} for s in f.f.plan['shots']]})}, through_scene=2)
        with runtime.runtime_access(self.store, selected=branch['id']) as bound:
            incoming = dict(self.manifest, _storage_pin=bound.pin, _branch_id=branch['id'])
        before = self.store.snapshot()
        sealed = self.seal(incoming)[0]
        after = self.store.snapshot()
        prefix = 'branches/'+branch['id']+'/'
        self.assertTrue(self.address(sealed).startswith(prefix+'chapters/'))
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
        self.assertEqual(after.state['scope_revisions']['branch:main'], before.state['scope_revisions']['branch:main'])
        loaded = self.load(branch=branch['id'])[0]
        self.assertEqual(loaded['_branch_id'], branch['id'])
        self.assertEqual(loaded['segments'], sealed['segments'])
        with self.assertRaises(FileNotFoundError):
            self.load(sealed['chapter_manifest_id'])
        self.assertEqual(self.load(plan=sealed)[0]['chapter_manifest_id'], sealed['chapter_manifest_id'])

    def test_partial_chapter_can_grow_without_changing_older_snapshot(self):
        source = copy.deepcopy(self.manifest)
        source.update(format='h3_chain_partial_manifest_v3', segments=source['segments'][:1],
                      clip_count=1, last_completed_clip=1, planned_clip_count=2)
        source['total_delivered_frames'] = source['segments'][0]['delivered_frames']
        partial = self.seal(source)[0]
        self.assertFalse(partial['chapter']['complete'])
        self.assertEqual(partial['chapter']['planned_end_scene'], 2)
        full = self.seal(self.f.repin(), unique_id='completed')[0]
        self.assertTrue(full['chapter']['complete'])
        self.assertEqual(len(full['segments']), 2)
        self.assertEqual(len(self.load(partial['chapter_manifest_id'])[0]['segments']), 1)
        self.assertEqual(self.load()[0]['chapter_manifest_id'], full['chapter_manifest_id'])

    def test_base_seal_and_direct_alt_passthrough_preserve_picture_only_export(self):
        for name, original in [('base', self.f.f.f.f.manifest), ('alt', self.f.f.f.direct_alt)]:
            with self.subTest(source=name):
                source = self.f.repin(original)
                source.update(_storage_pin=self.f.repin()['_storage_pin'], _branch_id='main', _project_ownership=self.proof)
                if name == 'alt':
                    before = self.store.snapshot().reference
                    with self.assertRaisesRegex(ValueError, 'generated or partial'):
                        self.seal(source, unique_id=name)
                    self.assertEqual(self.store.snapshot().reference, before)
                sealed = self.seal(source, unique_id=name, enabled=name != 'alt')[0]
                self.f.video = fixture.VideoVAE([s['raw_frames'] for s in sealed['segments']])
                self.f.audio = fixture.AudioVAE([s['raw_frames'] for s in sealed['segments']])
                self.f.export(sealed, unique_id=name)
                self.assertAlmostEqual(self.f.video.calls[0], .7)
                self.assertAlmostEqual(self.f.audio.calls[0], .1)

    def test_imported_legacy_chapter_load_preserves_its_original_content_id(self):
        f = self.f.f.f
        with patch.object(chain.folder_paths, 'output_directory', str(f.legacy_output)):
            legacy = chain.MiniMaxH3ChainChapterDelivery().select(f.f.manifest)[0]
        address = self.address(legacy)
        raw = (f.legacy_output/legacy['chapter_manifest_path']).read_bytes()
        self.store.commit(self.store.snapshot(), {address:dict(data=raw, scope='branch:main',
            category='cuts', immutable=True)}, operation_id=uuid.uuid4().hex)
        before = self.store.snapshot().reference
        loaded = self.load(legacy['chapter_manifest_id'])[0]
        self.assertEqual(loaded['chapter_manifest_id'], legacy['chapter_manifest_id'])
        self.assertEqual(loaded['segments'], legacy['segments'])
        self.assertEqual(loaded['editorial'], legacy['editorial'])
        self.assertNotIn('_project_ownership', loaded)
        self.assertEqual(self.store.snapshot().read(address), raw)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_derope_seals_keep_the_saved_video_and_audio_route(self):
        f = self.f.f.f
        for name, expected_audio in [('recovered-video', .3), ('recovered-av', .6)]:
            with self.subTest(profile=name):
                with runtime.runtime_access(self.store) as bound:
                    source = fixture.module('deferred_checkpoint_source').derope_source_manifest(
                        f.f.manifest, f.derope[name], chain, upscale)
                    source.update(_storage_pin=bound.pin, _branch_id='main', _project_ownership=self.proof)
                sealed = self.seal(source, unique_id=name)[0]
                self.assertEqual(sealed['processing_source']['stage'], 'derope')
                self.f.video = fixture.VideoVAE([s['raw_frames'] for s in sealed['segments']])
                self.f.audio = fixture.AudioVAE([s['raw_frames'] for s in sealed['segments']])
                self.f.export(sealed, unique_id=name)
                self.assertAlmostEqual(self.f.video.calls[0], .6)
                self.assertAlmostEqual(self.f.audio.calls[0], expected_audio)

    def test_retired_snapshot_cannot_be_republished_from_an_old_input(self):
        sealed = self.seal()[0]
        address = self.address(sealed)
        before = self.store.snapshot()
        retired = address.replace('/manifests/', '/retired_manifests/')
        # Simulate the accepted retirement tombstone, without deleting media.
        self.store.commit(before, {retired:dict(data=before.read(address), scope='branch:main',
            category='cuts', immutable=True)}, operation_id=uuid.uuid4().hex)
        current = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.seal()
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.load(sealed['chapter_manifest_id'])
        self.assertEqual(self.store.snapshot().reference, current)

    def test_accepted_head_corruption_and_invalid_ids_never_choose_another_snapshot(self):
        self.seal()
        for invalid in ('../outside', 'not-a-revision', 'a'*33):
            with self.subTest(token=invalid), self.assertRaises(ValueError):
                self.load(invalid)
        before = self.store.snapshot()
        head = 'chapter_heads/0001.json'
        document = state._decode(before.read(head))
        document['sha256'] = '0'*64
        self.store.commit(before, {head:dict(data=state._encode(document), scope='branch:main',
            category='cuts', immutable=False)}, operation_id=uuid.uuid4().hex)
        current = self.store.snapshot().reference
        with self.assertRaisesRegex(ValueError, 'Chapter latest selector differs'):
            self.load()
        self.assertEqual(self.store.snapshot().reference, current)

    def test_actual_prompt_executor_chapter_to_assembly_uses_accepted_successor(self):
        import execution
        import nodes
        incoming = self.manifest
        outputs = []
        class Source:
            @classmethod
            def INPUT_TYPES(cls):
                return {'required':{}}
            RETURN_TYPES = (chain.MANIFEST_TYPE,)
            FUNCTION = 'read'
            def read(self):
                return (incoming,)
        class Sink:
            @classmethod
            def INPUT_TYPES(cls):
                return {'required':{'video':('STRING',)}}
            RETURN_TYPES = ()
            FUNCTION = 'read'
            OUTPUT_NODE = True
            def read(self, video):
                outputs.append(video)
                return ()
        mapping = dict(H3ChapterSource=Source, H3ChapterSink=Sink,
            MiniMaxH3ChainChapterDelivery=chain.MiniMaxH3ChainChapterDelivery,
            MiniMaxH3ChainAssemble=chain.MiniMaxH3ChainAssemble)
        prompt = {
            '1':dict(class_type='H3ChapterSource', inputs={}),
            '2':dict(class_type='MiniMaxH3ChainChapterDelivery', inputs=dict(manifest=['1',0],enabled=True,chapter_number=0)),
            '3':dict(class_type='MiniMaxH3ChainAssemble', inputs=dict(manifest=['2',0],audio_source='generated',filename='Chapter',audio_bitrate=96)),
            '4':dict(class_type='H3ChapterSink', inputs=dict(video=['3',0]))}
        server = SimpleNamespace(client_id=None, last_node_id=None, send_sync=lambda *args:None)
        with patch.dict(nodes.NODE_CLASS_MAPPINGS, mapping), carriers.node_host(self.store,
                export_writers=(function, chain.MiniMaxH3ChainAssemble.assemble), operation_namespace=self.namespace):
            executor = execution.PromptExecutor(server, cache_type=execution.CacheType.NONE,
                                                cache_args={'ram':0,'ram_inactive':0})
            executor.execute(prompt, uuid.uuid4().hex, execute_outputs=['4'])
        errors = [data['exception_message'] for event,data in executor.status_messages if event == 'execution_error']
        self.assertTrue(executor.success, errors)
        self.assertEqual(len(outputs), 1)
        with chain.av.open(outputs[0]) as media:
            self.assertEqual(len(list(media.decode(video=0))), self.manifest['total_delivered_frames'])
            self.assertEqual(len(media.streams.audio), 1)
            embedded = json.loads(media.metadata['h3_manifest'])
        self.assertEqual(embedded['format'], chain.CHAPTER_MANIFEST_FORMAT)
        self.assertIn('chapter_manifest_id', embedded)
        self.assertEqual(embedded['segments'], self.manifest['segments'])
        self.store.verify_payloads()

    def test_reverse_recovery_keeps_seal_identity_and_legacy_load_assembly_retirement(self):
        first = self.seal()[0]
        changed = self.f.repin()
        changed.setdefault('editorial', {})['subtitles'] = {'mode':'off', 'offset_seconds':2}
        sealed = self.seal(changed, unique_id='newer')[0]
        before = self.store.snapshot()
        f = self.f.f.f
        receipt = f.lab/'chapter-copy-receipt.json'
        state.atomic_json(receipt, dict(copy=str(self.store.project), source=str(f.source), independent_copies=True))
        recovery = fixture.module('storage_recovery')
        output = f.lab/'chapter-recovered-output'
        journal = recovery.prepare_legacy_copy(receipt, output, f.lab/'chapter-recovery', rehearsal_store=self.store)
        recovered = recovery.recover_legacy_copy(journal)
        self.assertTrue(recovered['source_unchanged'])
        root = output/'h3_chains'/f.run
        copied = root/self.address(sealed)
        self.assertEqual(copied.read_bytes(), before.read(self.address(sealed)))
        original = self.store.project/before.state['documents'][self.address(sealed)]['file']['path']
        self.assertNotEqual((copied.stat().st_dev,copied.stat().st_ino), (original.stat().st_dev,original.stat().st_ino))
        self.assertEqual(recovered, recovery.recover_legacy_copy(journal))
        # Recovery/copying must not turn an older snapshot into "latest".
        older_path = root/self.address(first)
        os.utime(copied, ns=(1_000_000_000, 1_000_000_000))
        os.utime(older_path, ns=(9_000_000_000, 9_000_000_000))
        with patch.object(chain.folder_paths, 'output_directory', str(output)):
            self.assertEqual(chain.MiniMaxH3ChainChapterLoad().load(f.run, 1)[0]['chapter_manifest_id'], sealed['chapter_manifest_id'])
            loaded = chain.MiniMaxH3ChainChapterLoad().load(f.run, 1, sealed['chapter_manifest_id'])[0]
            self.assertEqual(chapters.body(loaded), chapters.body(sealed))
            loaded['_project_ownership'] = self.proof
            assembled = chain.MiniMaxH3ChainAssemble().assemble(loaded, 'generated', 'RestoredChapter', 96)
            self.assertTrue(Path(assembled['result'][0]).is_file())
            manager = fixture.module('chapter_snapshot_retirement').ChapterSnapshotManager(output)
            preview = manager.retirement_preview(f.run, sealed['chapter_manifest_path'])
            manager.retire(f.run, sealed['chapter_manifest_path'], preview['snapshot'])
            with self.assertRaises(FileNotFoundError):
                chain.MiniMaxH3ChainChapterLoad().load(f.run, 1, sealed['chapter_manifest_id'])
            self.assertEqual(chain.MiniMaxH3ChainChapterLoad().load(f.run, 1)[0]['chapter_manifest_id'], first['chapter_manifest_id'])
            # A new save made by the ordinary legacy node after recovery must
            # supersede the imported portable head, without rewriting old seals.
            new = copy.deepcopy(loaded)
            new.setdefault('editorial', {})['subtitles'] = {'mode':'off', 'offset_seconds':7}
            newest = chain.MiniMaxH3ChainChapterDelivery().select(new, True, 1)[0]
            self.assertNotIn(newest['chapter_manifest_id'], (first['chapter_manifest_id'], sealed['chapter_manifest_id']))
            self.assertEqual(chain.MiniMaxH3ChainChapterLoad().load(f.run, 1)[0]['chapter_manifest_id'], newest['chapter_manifest_id'])
            preview = manager.retirement_preview(f.run, newest['chapter_manifest_path'])
            manager.retire(f.run, newest['chapter_manifest_path'], preview['snapshot'])
            self.assertEqual(chain.MiniMaxH3ChainChapterLoad().load(f.run, 1)[0]['chapter_manifest_id'], first['chapter_manifest_id'])
        self.assertEqual(self.store.snapshot().reference, before.reference)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
