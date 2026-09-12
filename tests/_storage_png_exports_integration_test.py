"""Actual RGB16 encoding and atomic finished exports on independent migration fixtures."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import av
import numpy as np
import _storage_processing_saves_integration_test as fixture
from comfy_api.latest import InputImpl

chain, upscale, runtime, state = fixture.chain, fixture.upscale, fixture.runtime, fixture.state
exports = fixture.fixture.module('storage_exports')
png = fixture.fixture.module('png_video_export')
ownership = fixture.ownership


def video_file(path, count, seed):
    pixels = np.random.default_rng(seed).integers(0, 65536, (count, 32, 32, 3), dtype=np.uint16)
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1', rate=24)
        stream.width, stream.height, stream.pix_fmt = 32, 32, 'gbrp16le'
        stream.time_base = stream.codec_context.time_base = Fraction(1,24000)
        for index, pixels_frame in enumerate(pixels):
            frame = av.VideoFrame.from_ndarray(pixels_frame, format='rgb48le')
            frame.pts, frame.time_base = index*1000, Fraction(1,24000)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return InputImpl.VideoFromFile(str(path)), pixels


class PNGExportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ProcessingSaveTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.store, self.proof = self.f.store, self.f.proof
        incoming = self.f.incoming()
        self.source = dict(incoming['source_manifest'], _storage_pin=incoming['_storage_pin'],
                           _branch_id='main', _project_ownership=self.proof)
        self.operation = uuid.uuid4().hex
        self.settings = dict(first_frame_number=101, png_bit_depth=16, png_compression=1, embed_workflow=False)
        self.videos, self.pixels = {}, {}
        for source in self.source['segments']:
            index = source['index']
            self.videos[index], self.pixels[index] = video_file(self.f.output/('png-source-%d.mkv' % index), source['raw_frames'], index)

    def workspace(self, bound, **kwargs):
        return exports.FinishedPNGExport(bound, upscale, kwargs.pop('source', self.source),
            operation=kwargs.pop('operation', self.operation), label=kwargs.pop('label', 'DLSS_upscale_2'),
            settings=kwargs.pop('settings', self.settings), videos=kwargs.pop('videos', self.videos), **kwargs)

    def access(self, **kwargs):
        return runtime.runtime_access(self.store, pin=self.source['_storage_pin'], export_writes=True, **kwargs)

    def test_actual_rgb16_numbering_and_atomic_catalogue_without_processing_grant(self):
        before, incoming = self.store.snapshot(), copy.deepcopy(self.source)
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode(workers=2)
            self.assertEqual(self.store.snapshot().reference, before.reference)
            result = workspace.publish()
            self.assertFalse(bound.processing_writes)
        directory = Path(result['directory'])
        self.assertEqual(directory.name, 'DLSS_upscale_2')
        self.assertEqual(directory.relative_to(self.store.project).parts[:2], ('exports', 'png'))
        record = json.loads((directory/'export.json').read_text())
        expected_count = sum(s['delivered_frames'] for s in self.source['segments'])
        self.assertEqual(result['frame_count'], expected_count)
        self.assertEqual(record['sources'], self.source['segments'])
        self.assertEqual(record['label'], 'DLSS_upscale_2')
        cursor = 101
        for source in self.source['segments']:
            for pixels in self.pixels[source['index']][-source['delivered_frames']:]:
                with av.open(str(directory/('frame_%08d.png' % cursor))) as container:
                    actual = next(container.decode(video=0)).to_ndarray(format='rgb48le')
                np.testing.assert_array_equal(actual, pixels)
                cursor += 1
        after = self.store.snapshot()
        self.assertEqual(after.state['generation'], before.state['generation']+1)
        catalog = state._decode(after.read('export_catalog.json'))
        self.assertEqual(catalog['exports'][self.operation]['frame_count'], expected_count)
        for address, descriptor in before.state['documents'].items():
            self.assertEqual(after.state['documents'][address], descriptor)
        self.assertEqual(self.source, incoming)
        self.assertFalse((self.store.project/'frames').exists())
        self.store.verify_payloads()

    def test_new_range_can_export_scene_two_without_scene_one(self):
        source = dict(self.source, segments=self.source['segments'][1:])
        with self.access() as bound:
            workspace = self.workspace(bound, source=source, videos={2:self.videos[2]})
            workspace.encode()
            result = workspace.publish()
        record = json.loads((Path(result['directory'])/'export.json').read_text())
        self.assertEqual([c['index'] for c in record['clips']], [2])
        self.assertEqual(record['clips'][0]['first_frame_number'], 101)

    def test_second_export_changes_only_catalogue_and_preserves_all_old_entries(self):
        with self.access() as bound:
            first = self.workspace(bound)
            first.encode()
            first.publish()
        before = self.store.snapshot()
        prior_catalogue = state._decode(before.read('export_catalog.json'))
        source = dict(self.source, _storage_pin=dict(self.source['_storage_pin'], root=before.reference))
        operation = uuid.uuid4().hex
        with runtime.runtime_access(self.store, pin=source['_storage_pin'], export_writes=True) as bound:
            second = self.workspace(bound, source=source, operation=operation)
            second.encode()
            result = second.publish()
        after = self.store.snapshot()
        for address, descriptor in before.state['documents'].items():
            if address != 'export_catalog.json':
                self.assertEqual(after.state['documents'][address], descriptor)
        catalogue = state._decode(after.read('export_catalog.json'))
        added = catalogue['exports'].pop(operation)
        self.assertEqual(catalogue, prior_catalogue)
        self.assertEqual(added['metadata'], second.record_address)
        self.assertEqual(added['frame_count'], result['frame_count'])
        self.assertEqual(state._decode(before.read('export_catalog.json')), prior_catalogue)
        self.store.verify_payloads(before)
        self.store.verify_payloads(after)

    def test_prepared_failure_restarts_without_reencoding(self):
        before = self.store.snapshot().reference
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            def fail(stage):
                if stage == 'payload':
                    raise OSError('interrupted PNG publication')
            bound.exports.after_stage = fail
            with self.assertRaisesRegex(OSError, 'interrupted'):
                workspace.publish()
        self.assertEqual(self.store.snapshot().reference, before)
        with self.access() as bound, patch.object(png, 'encode_scene', side_effect=AssertionError('reencoded')):
            workspace = self.workspace(bound)
            workspace.encode()
            result = workspace.publish()
        self.assertTrue((Path(result['directory'])/'export.json').is_file())
        self.store.verify_payloads()

    def test_public_index_is_staged_last_after_every_numbered_frame(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            directory = self.store.project/'exports/png/DLSS_upscale_2'
            count = sum(s['delivered_frames'] for s in self.source['segments'])
            observations = []
            def observe(stage):
                if stage == 'payload':
                    frames = list(directory.glob('frame_*.png'))
                    index = (directory/'export.json').exists()
                    observations.append((len(frames), index))
                    if index:
                        self.assertEqual(len(frames), count)
            bound.exports.after_stage = observe
            workspace.publish()
            self.assertEqual(observations[-1], (count, True))
            self.assertTrue(all(not present for _, present in observations[:-1]))

    def test_interruption_after_first_frame_has_no_readable_finished_index(self):
        before = self.store.snapshot().reference
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            def stop(stage):
                if stage == 'payload':
                    raise OSError('stop after frame')
            bound.exports.after_stage = stop
            with self.assertRaisesRegex(OSError, 'stop after frame'):
                workspace.publish()
        directory = self.store.project/'exports/png/DLSS_upscale_2'
        self.assertEqual(len(list(directory.glob('frame_*.png'))), 1)
        self.assertFalse((directory/'export.json').exists())
        self.assertEqual(self.store.snapshot().reference, before)

    def test_lost_ack_returns_exact_export_without_duplicate_or_pointer_rollback(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            def fail(stage):
                if stage == 'commit':
                    raise OSError('lost PNG acknowledgement')
            bound.exports.after_stage = fail
            with self.assertRaisesRegex(OSError, 'lost PNG'):
                workspace.publish()
        accepted = self.store.snapshot().reference
        # A later export is accepted; retrying the first must not undo it.
        newer_source = dict(self.source, _storage_pin=dict(self.source['_storage_pin'], root=accepted))
        with runtime.runtime_access(self.store, pin=newer_source['_storage_pin'], export_writes=True) as bound:
            newer = self.workspace(bound, operation=uuid.uuid4().hex, source=newer_source)
            newer.encode()
            newer.publish()
        latest = self.store.snapshot().reference
        with self.access() as bound, patch.object(png, 'encode_scene', side_effect=AssertionError('reencoded')):
            workspace = self.workspace(bound)
            workspace.encode()
            result = workspace.publish()
        self.assertEqual(result['storage_pin']['root'], accepted)
        self.assertEqual(self.store.snapshot().reference, latest)
        self.store.verify_payloads()

    def test_readonly_missing_owner_and_forged_source_reject_before_encoding(self):
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, pin=self.source['_storage_pin']) as bound:
            with self.assertRaisesRegex(ValueError, 'export writes'):
                self.workspace(bound)
        with self.access() as bound:
            with self.assertRaises(ownership.ProjectOwnershipError):
                self.workspace(bound, source=dict(self.source, _project_ownership=None))
            changed = copy.deepcopy(self.source)
            changed['segments'][0]['seed'] += 1
            with self.assertRaisesRegex(ValueError, 'immutable metadata'):
                self.workspace(bound, source=changed)
        self.assertEqual(self.store.snapshot().reference, before)

    def test_changed_video_or_settings_cannot_reuse_operation(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            workspace.publish()
        for settings in (dict(self.settings, png_bit_depth=8), dict(self.settings, first_frame_number=1)):
            with self.access() as bound, self.assertRaisesRegex(ValueError, 'Immutable control-state collision'):
                self.workspace(bound, settings=settings)
        path = self.f.output/'changed-png-source.mkv'
        changed, _ = video_file(path, self.source['segments'][0]['raw_frames'], 88)
        with self.access() as bound, self.assertRaisesRegex(ValueError, 'Immutable control-state collision'):
            self.workspace(bound, videos=self.videos | {1:changed})

    def test_changed_staged_pixels_and_path_escape_reject(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            prepared = workspace._prepared()
            name, item = next((name,item) for name,item in prepared['files'].items() if name.endswith('.png'))
            path = self.store.project/item['path']
            # Fault injection only into the independent test's private job.
            path.write_bytes(b'edited staged PNG')
            before = self.store.snapshot().reference
            with self.assertRaisesRegex(state.StateConflict, 'Prepared PNG file changed'):
                workspace.publish()
            files = {name:self.store.project/item['path'] for name,item in prepared['files'].items() if name.endswith('.png')}
            files[name] = self.f.output/'outside.png'
            with self.assertRaisesRegex(ValueError, 'outside its private encoder'):
                workspace.prepare(prepared['record']['clips'], files)
            self.assertEqual(self.store.snapshot().reference, before)

    def test_source_video_mutation_during_encode_keeps_root_unchanged(self):
        before = self.store.snapshot().reference
        real = png.encode_scene
        with self.access() as bound:
            workspace = self.workspace(bound)
            def change(*args, **kwargs):
                result = real(*args, **kwargs)
                Path(args[1]).write_bytes(b'changed video source')
                return result
            with patch.object(png, 'encode_scene', change), self.assertRaisesRegex(ValueError, 'VIDEO changed'):
                workspace.encode()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_archived_workflow_is_embedded_from_exact_pin(self):
        from PIL import Image
        with self.access() as bound:
            workspace = self.workspace(bound, settings=dict(self.settings, embed_workflow=True))
            expected = dict(workspace.archive_tags)
            self.assertTrue(expected)
            workspace.encode()
            result = workspace.publish()
        with Image.open(Path(result['directory'])/'frame_00000101.png') as image:
            for key, value in expected.items():
                self.assertEqual(image.info[key], value)

    def test_takeover_after_encoding_fences_export_acceptance(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
        before = self.store.snapshot().reference
        with runtime.runtime_access(self.store, ownership_writes=True):
            ownership.claim_project_ownership(self.f.output, self.f.run, 'new-export-owner', force=True)
        with self.access() as bound, self.assertRaises(ownership.ProjectOwnershipError):
            self.workspace(bound).publish()
        self.assertEqual(self.store.snapshot().reference, before)

    def test_unported_node_cannot_borrow_direct_export_permission(self):
        incoming = self.f.incoming()
        with self.access():
            with self.assertRaisesRegex(ValueError, 'explicit node-host grants'):
                chain.MiniMaxH3ChainExportPNG().export(video=self.videos[1], state=incoming)

    def test_published_frame_corruption_is_not_an_exact_retry(self):
        with self.access() as bound:
            workspace = self.workspace(bound)
            workspace.encode()
            result = workspace.publish()
        frame = Path(result['directory'])/'frame_00000101.png'
        original = frame.read_bytes()
        frame.write_bytes(bytes([original[0] ^ 1])+original[1:])
        with self.access() as bound, self.assertRaisesRegex(state.StateConflict, 'checksum'):
            self.workspace(bound).accepted()


if __name__ == '__main__':
    unittest.main(argv=[__file__])
