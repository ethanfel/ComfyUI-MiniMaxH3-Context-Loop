"""Real ComfyUI executor/GraphBuilder on independently migrated CPU fixtures.

Only the model-backed image producer and UI sink are test nodes. Adapter,
Current, Save, lazy End, Handoff, Advance and recursive clones are production
classes; media encoding, storage and scheduling are not mocked.
"""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import _storage_processing_saves_integration_test as fixture

chain, upscale, torch = fixture.chain, fixture.upscale, fixture.torch
carriers, runtime, continuation = fixture.carriers, fixture.runtime, fixture.continuation


def execute_graph(store, source, *, profile='executor-pixels', start=1, end=0,
                  mode='resume', cache='classic', change_handoff=False, adapters=True,
                  stop_scene=None, pre_save=False, export_png=False, assembly_node=None,
                  normal_host=False):
    import execution
    import nodes
    calls, outputs, messages, videos, assembled = [], [], [], {}, []

    class Source:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required': {}}
        RETURN_TYPES = (chain.MANIFEST_TYPE,)
        FUNCTION = 'read'
        def read(self):
            calls.append(('source',))
            return (copy.deepcopy(source),)

    class Pixels:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required': {'state': (upscale.UPSCALE_STATE_TYPE,)}}
        RETURN_TYPES = ('IMAGE', 'IMAGE', 'VIDEO')
        FUNCTION = 'render'
        def render(self, state):
            index = state['index']
            calls.append(('pixels', index, copy.deepcopy(state['_storage_pin'])))
            if index == stop_scene:
                raise RuntimeError('Injected stop before scene '+str(index))
            scene = upscale._source_segment(state)
            pixels = torch.full((int(scene['raw_frames']), 32, 32, 3), index/10)
            video = None
            if export_png:
                from _storage_png_exports_integration_test import video_file
                if index not in videos:
                    videos[index] = video_file(store.project.parent.parent/
                        ('graph-'+job_id+'-%d.mkv' % index), int(scene['raw_frames']), index)[0]
                video = videos[index]
            return pixels, pixels+.1 if change_handoff else pixels, video

    class VideoPixels:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required': {'video': ('VIDEO',)}}
        RETURN_TYPES = ('IMAGE',)
        FUNCTION = 'decode'
        def decode(self, video):
            # A real decode of the VIDEO returned by ExportPNG. Neither its
            # result nor Save/Handoff's image witness is replaced by a fixture.
            assert any(video is value for value in videos.values())
            return (video.get_components().images,)

    class Sink:
        @classmethod
        def INPUT_TYPES(cls):
            return {'required': {'manifest': (upscale.UPSCALE_MANIFEST_TYPE,)},
                    'optional': {'video_path': ('STRING',)}}
        RETURN_TYPES = ()
        FUNCTION = 'read'
        OUTPUT_NODE = True
        def read(self, manifest, video_path=None):
            outputs.append(manifest)
            if video_path is not None:
                assembled.append(video_path)
            return {'ui': {'text': ['accepted processing delivery']}, 'result': ()}

    class Server:
        client_id = None
        last_node_id = None
        def send_sync(self, event, data, *args):
            messages.append((event, data))

    mapping = {'H3StorageTestSource': Source, 'H3StorageTestPixels': Pixels, 'H3StorageTestSink': Sink}
    if export_png:
        mapping.update(H3StorageTestVideoPixels=VideoPixels,
                       MiniMaxH3ChainExportPNG=chain.MiniMaxH3ChainExportPNG)
    for suffix in ('Adapter', 'Current', 'SegmentSave', 'LoopEnd', 'Handoff', 'Advance'):
        name = 'MiniMaxH3ChainUpscale'+suffix
        mapping[name] = getattr(upscale, name)
    prompt = {
        '1': {'class_type': 'H3StorageTestSource', 'inputs': {}},
        '2': {'class_type': 'MiniMaxH3ChainUpscaleAdapter', 'inputs': {
            'source_manifest': ['1', 0], 'profile': profile, 'backend': 'pixel', 'recipe_json': '{}',
            'start_clip': start, 'end_clip': end, 'save_latent': False, 'segment_crf': 18, 'start_mode': mode}},
        '3': {'class_type': 'MiniMaxH3ChainUpscaleCurrent', 'inputs': {'state': ['2', 1]}},
        '4': {'class_type': 'H3StorageTestPixels', 'inputs': {'state': ['3', 0]}},
        '5': {'class_type': 'MiniMaxH3ChainUpscaleSegmentSave', 'inputs': {'state': ['3', 0], 'images': ['4', 0]}},
        '6': {'class_type': 'MiniMaxH3ChainUpscaleLoopEnd', 'inputs': {
            'flow': ['2', 0], 'state': ['3', 0], 'images': ['4', 1], 'segment': ['5', 0]}},
        '7': {'class_type': 'H3StorageTestSink', 'inputs': {'manifest': ['6', 0]}},
    }
    if export_png:
        prompt.update({
            '8': {'class_type': 'MiniMaxH3ChainExportPNG', 'inputs': {
                'state': ['3', 0], 'video': ['4', 2], 'export_name': 'Graph',
                'first_frame_number': 101, 'png_bit_depth': '16', 'embed_workflow': False}},
            '9': {'class_type': 'H3StorageTestVideoPixels', 'inputs': {'video': ['8', 4]}},
        })
        prompt['5']['inputs']['images'] = ['9', 0]
        prompt['6']['inputs']['images'] = ['9', 0]
    export_writers = [chain.MiniMaxH3ChainExportPNG.export] if export_png else []
    if assembly_node is not None:
        if assembly_node not in ('assemble', 'copy', 'merge'):
            raise ValueError('Unknown test assembly node')
        cls = (upscale.MiniMaxH3ChainUpscaleMerge if assembly_node == 'merge'
               else chain.MiniMaxH3ChainAssemble)
        mapping[cls.__name__] = cls
        export_writers.append(getattr(cls, cls.FUNCTION))
        prompt['10'] = {'class_type':cls.__name__, 'inputs':dict(manifest=['6',0],
            audio_source='generated', filename='Graph_final', audio_bitrate=96)}
        if assembly_node == 'copy':
            prompt['10']['inputs'].update(copy_to_output=True, output_subfolder='graph-copies')
        prompt['7']['inputs']['video_path'] = ['10',0]
    host_adapters = {
        upscale.MiniMaxH3ChainUpscaleLoopEnd.end: continuation.loop_end_inputs,
        upscale.MiniMaxH3ChainUpscaleHandoff.prepare: continuation.loop_inputs,
    } if adapters else {}
    job_id = uuid.uuid4().hex
    from contextlib import nullcontext
    host_context = nullcontext() if normal_host else carriers.node_host(store,
            processing_writers=(upscale.MiniMaxH3ChainUpscaleSegmentSave.save,), input_adapters=host_adapters,
            export_writers=tuple(export_writers),
            operation_namespace=job_id)
    with patch.dict(nodes.NODE_CLASS_MAPPINGS, mapping), host_context:
        executor = execution.PromptExecutor(Server(), cache_type={
            'classic': execution.CacheType.CLASSIC, 'none': execution.CacheType.NONE}[cache],
            cache_args={'ram': 0, 'ram_inactive': 0})
        executor.execute(prompt, job_id, execute_outputs=['5', '7'] if pre_save else ['7'])
    errors = [data['exception_message'] for event, data in executor.status_messages if event == 'execution_error']
    return dict(success=executor.success, errors=errors, calls=calls, outputs=outputs,
                messages=messages, prompt=prompt, assembled=assembled)


class ProcessingGraphTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProcessingSaveTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store = self.f.store
        incoming = self.f.incoming()
        self.source = dict(incoming['source_manifest'], _project_ownership=self.f.proof)

    def test_actual_lazy_recursive_two_scene_executor(self):
        result = execute_graph(self.store, self.source)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([call[1] for call in result['calls'] if call[0] == 'pixels'], [1, 2])
        self.assertEqual(result['calls'].count(('source',)), 1)
        self.assertEqual(len(result['outputs']), 1)
        manifest = result['outputs'][0]
        self.assertEqual(manifest['_project_ownership'], self.f.proof)
        self.assertEqual([s['index'] for s in manifest['segments']], [1, 2])
        self.assertEqual(manifest['_storage_pin']['root'], self.store.snapshot().reference)
        with runtime.runtime_access(self.store, pin=manifest['_storage_pin']) as bound:
            for saved in manifest['segments']:
                metadata = bound.reader.read(saved['revision_metadata'])
                execution = fixture.fixture.module('processing_execution').from_metadata(metadata)
                self.assertEqual(chain._fingerprint(execution['api_prompt']), chain._fingerprint(result['prompt']))
                tensors = chain._st_load(str(bound.reader.path(saved['checkpoint'])))
                self.assertTrue(torch.all(tensors['delivered_audio'] == .2))
                self.assertEqual(saved['seed'], manifest['source_manifest']['segments'][saved['index']-1]['seed'])
        self.store.verify_payloads()

    def assert_png_graph(self, *, cache='classic', start=1, mode='resume'):
        before = self.store.snapshot().state['generation']
        result = execute_graph(self.store,self.source,export_png=True,cache=cache,start=start,mode=mode)
        self.assertTrue(result['success'],result['errors'])
        after = self.store.snapshot()
        manifest = result['outputs'][0]
        saved = manifest['segments']
        self.assertEqual([s['index'] for s in saved],list(range(start,3)))
        self.assertEqual(manifest['_storage_pin']['root'],after.reference)
        self.assertEqual(after.state['generation'],before+2*len(saved))
        addresses = [p for p in after.state['documents']
                     if p.startswith('upscaled/executor-pixels/frames/') and p.endswith('/export.json')]
        self.assertEqual(addresses,['upscaled/executor-pixels/frames/Graph/export.json'])
        index = fixture.state._decode(after.read(addresses[0]))
        self.assertEqual([c['index'] for c in index['clips']],[s['index'] for s in saved])
        cursor = 101
        for clip, segment in zip(index['clips'],saved):
            self.assertIn(segment['png_export_owner'],clip['processing_owners'])
            self.assertEqual(clip['first_frame_number'],cursor)
            cursor += segment['delivered_frames']
        directory = self.store.project/'exports/png'/index['_storage_export_id']
        self.assertEqual(len(list(directory.glob('frame_*.png'))),cursor-101)
        self.assertEqual(index['frame_count'],cursor-101)
        self.store.verify_payloads()

    def test_real_recursive_executor_exports_png_before_saving_each_scene(self):
        self.assert_png_graph()

    def test_uncached_recursive_png_executor_does_not_duplicate_exports_or_saves(self):
        self.assert_png_graph(cache='none')

    def test_fresh_range_recursive_png_executor_needs_no_earlier_pngs(self):
        self.assert_png_graph(start=2,mode='fresh_range')

    def assert_assembled_graph(self, **settings):
        import av
        before = self.store.snapshot().state['generation']
        result = execute_graph(self.store, self.source, **settings)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual(len(result['assembled']), 1)
        path = Path(result['assembled'][0])
        self.assertIn('/exports/video/', str(path))
        manifest = result['outputs'][0]
        with av.open(str(path)) as video:
            self.assertEqual(len(list(video.decode(video=0))), manifest['total_delivered_frames'])
            self.assertEqual(len(video.streams.audio), 1)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot.state['generation'], before+
            len(manifest['segments'])*(2 if settings.get('export_png') else 1)+1)
        records = [p for p in snapshot.state['documents'] if p.startswith('export_records/')
                   and fixture.state._decode(snapshot.read(p)).get('format') == 'h3_storage_assembly_v1']
        self.assertEqual(len(records), 1, 'No duplicate assembly on uncached lazy reevaluation')
        record = fixture.state._decode(snapshot.read(records[0]))
        self.assertEqual(set(record['files']), {'video', 'audio', 'metadata'})
        metadata = fixture.state._decode(snapshot.read(record['logical']['metadata']))
        self.assertEqual([s['revision'] for s in metadata['processing_executions']],
                         [s['revision'] for s in manifest['segments']])
        last = manifest['segments'][-1]
        delivery_path = 'upscaled/executor-pixels/partial/through_clip_%04d.%s.manifest.json' % (
            last['index'], last['revision'])
        persisted = fixture.state._decode(snapshot.read(delivery_path))
        self.assertNotIn('_project_ownership', persisted)
        self.assertNotIn('_project_ownership', persisted['source_manifest'])
        if settings['assembly_node'] == 'copy':
            copied = self.store.project.parent.parent/'graph-copies/Graph_final.mp4'
            self.assertEqual(copied.read_bytes(), path.read_bytes())
            self.assertNotEqual(copied.stat().st_ino, path.stat().st_ino)
        self.store.verify_payloads()

    def test_real_png_save_loop_assemble_and_output_copy_graph(self):
        self.assert_assembled_graph(export_png=True, assembly_node='copy')

    def test_uncached_recursive_executor_assembles_once(self):
        self.assert_assembled_graph(cache='none', assembly_node='assemble')

    def test_legacy_merge_executor_receives_its_real_unique_id(self):
        self.assert_assembled_graph(assembly_node='merge')

    def test_later_fresh_range_can_assemble_without_earlier_hq_or_pngs(self):
        self.assert_assembled_graph(start=2, mode='fresh_range', assembly_node='assemble')

    def test_actual_uncached_executor(self):
        before = self.store.snapshot().state['generation']
        result = execute_graph(self.store, self.source, cache='none')
        self.assertTrue(result['success'], result['errors'])
        # NullCache may reevaluate scene 2's producer/save when resolving the
        # expanded graph. That must recover the take, not publish a new one.
        self.assertEqual(sorted({call[1] for call in result['calls'] if call[0] == 'pixels'}), [1, 2])
        self.assertEqual(self.store.snapshot().state['generation'], before+2)
        self.assertEqual(len(result['outputs'][0]['segments']), 2)

    def test_save_scheduled_before_lazy_end_with_cached_inputs(self):
        before = self.store.snapshot().state['generation']
        result = execute_graph(self.store, self.source, pre_save=True)
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual(self.store.snapshot().state['generation'], before+2)
        self.assertEqual([call[1] for call in result['calls'] if call[0] == 'pixels'], [1, 2])

    def test_actual_fresh_range_needs_no_earlier_processed_scene(self):
        result = execute_graph(self.store, self.source, start=2, mode='fresh_range')
        self.assertTrue(result['success'], result['errors'])
        self.assertEqual([s['index'] for s in result['outputs'][0]['segments']], [2])
        self.assertNotIn('upscaled/executor-pixels/checkpoints/clip_0001.json', self.store.snapshot().state['documents'])

    def test_failed_next_scene_retains_first_and_new_executor_resumes(self):
        with self.assertLogs(level='ERROR'):
            stopped = execute_graph(self.store, self.source, stop_scene=2)
        self.assertFalse(stopped['success'])
        self.assertIn('Injected stop before scene 2', str(stopped['errors']))
        self.assertFalse(stopped['outputs'])
        with runtime.runtime_access(self.store) as bound:
            first = bound.reader.read('h3_chains/'+self.f.run+'/upscaled/executor-pixels/checkpoints/clip_0001.json')['segment']
            restart_source = dict(self.source, _storage_pin=bound.pin)
        resumed = execute_graph(self.store, restart_source, start=2)
        self.assertTrue(resumed['success'], resumed['errors'])
        self.assertEqual([call[1] for call in resumed['calls'] if call[0] == 'pixels'], [2])
        self.assertEqual(resumed['outputs'][0]['segments'][0]['revision'], first['revision'])
        self.assertEqual(len(resumed['outputs'][0]['segments']), 2)
        self.store.verify_payloads()

    def test_expanded_handoff_rejects_different_pixels_without_continuing(self):
        with self.assertLogs(level='ERROR'):
            result = execute_graph(self.store, self.source, change_handoff=True)
        self.assertFalse(result['success'])
        self.assertIn('tensors differ', str(result['errors']))
        self.assertEqual([call[1] for call in result['calls'] if call[0] == 'pixels'], [1])
        self.assertFalse(result['outputs'])
        self.assertNotIn('upscaled/executor-pixels/checkpoints/clip_0002.json', self.store.snapshot().state['documents'])

    def test_expansion_cannot_grant_its_handoff_adapter(self):
        with self.assertLogs(level='ERROR'):
            result = execute_graph(self.store, self.source, adapters=False)
        self.assertFalse(result['success'])
        self.assertIn('mixed-root', str(result['errors']))
        self.assertFalse(result['outputs'])

    def test_lazy_boundary_only_discards_inputs_for_declared_loop_end(self):
        from comfy_execution.graph import DynamicPrompt
        original = dict(state={'ignored':True}, images=self.f.f.f.frames, segment={'ignored':True},
                        upscaled_latent=None, unique_id='6', flow=['2', 0],
                        dynprompt=DynamicPrompt({'6':{'class_type':'MiniMaxH3ChainUpscaleLoopEnd','inputs':{}}}))
        prepared = continuation.loop_end_inputs(self.store, original)
        self.assertEqual(set(prepared), set(original))
        self.assertIsNone(prepared['state'])
        self.assertIsNone(prepared['segment'])
        self.assertIsNone(prepared['images'])
        self.assertEqual(original['state'], {'ignored':True})
        with self.assertRaisesRegex(ValueError, 'not an Upscale Loop End'):
            continuation.loop_end_inputs(self.store, dict(original,
                dynprompt=DynamicPrompt({'6':{'class_type':'MiniMaxH3ChainUpscaleHandoff','inputs':{}}})))
        with self.assertRaisesRegex(ValueError, 'state and saved segment'):
            continuation.loop_end_inputs(self.store, dict(original, dynprompt=None, state=None))

    def test_job_namespace_is_stable_per_exact_dynamic_node_and_domain(self):
        operation = uuid.uuid4().hex
        processing = upscale.MiniMaxH3ChainUpscaleSegmentSave.save
        generation = chain.MiniMaxH3ChainSegmentSave.save
        options = dict(processing_writers=(processing,), generation_writers=(generation,),
                       operation_namespace=operation)
        before = self.store.snapshot().reference
        with carriers.node_host(self.store, **options):
            first = carriers.processing_operation(processing, '6.0.0.5')
            self.assertEqual(first, carriers.processing_operation(processing, '6.0.0.5'))
            self.assertNotEqual(first, carriers.processing_operation(processing, '6.0.1.5'))
            self.assertNotEqual(first, carriers.generation_operation(generation, '6.0.0.5'))
        with carriers.node_host(self.store, **options):
            self.assertEqual(first, carriers.processing_operation(processing, '6.0.0.5'))
        with carriers.node_host(self.store, **dict(options, operation_namespace=uuid.uuid4().hex)):
            self.assertNotEqual(first, carriers.processing_operation(processing, '6.0.0.5'))
        self.assertEqual(before, self.store.snapshot().reference)

    def test_job_namespace_never_grants_permission_or_accepts_ambiguous_ids(self):
        function = upscale.MiniMaxH3ChainUpscaleSegmentSave.save
        with carriers.node_host(self.store, operation_namespace=uuid.uuid4().hex):
            with self.assertRaisesRegex(ValueError, 'separate exact writer grant'):
                carriers.processing_operation(function, '5')
        with carriers.node_host(self.store, processing_writers=(function,), operation_namespace=uuid.uuid4().hex):
            for bad in (None, '', 5, True):
                with self.assertRaisesRegex(ValueError, 'exact execution unique_id'):
                    carriers.processing_operation(function, bad)
        with self.assertRaisesRegex(ValueError, 'cannot be mixed'), carriers.node_host(self.store,
                processing_writers=(function,), operation_namespace=uuid.uuid4().hex,
                processing_operations={(function,'5'):uuid.uuid4().hex}):
            pass


if __name__ == '__main__':
    unittest.main(argv=['processing-graph'])
