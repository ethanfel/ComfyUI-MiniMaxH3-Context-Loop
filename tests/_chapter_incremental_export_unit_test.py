#!/usr/bin/env python3
"""Real PNG/WAV writes with tiny CPU VAEs: partial chapters and safe append."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import torch
from safetensors.torch import save_file

from _png_export_unit_test import chain, folder_paths


class VideoVAE:
    def __init__(self):
        self.calls = 0

    def decode(self, video):
        self.calls += 1
        return torch.full((13, 4, 4, 3), float(video.flatten()[0]) / 255)


class AudioVAE:
    audio_sample_rate = 8000

    def __init__(self):
        self.calls = 0

    def decode(self, _audio):
        self.calls += 1
        count = chain.sample_boundary_from_frames(13, 8000, chain.FPS)
        return torch.linspace(-0.5, 0.5, count).reshape(1, 1, -1)


class IncrementalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder_paths.output_directory = self.temp.name
        self.root = Path(self.temp.name)
        self.video = VideoVAE()
        self.audio = AudioVAE()
        self.node = chain.MiniMaxH3ChainExportPNG()
        self.segments = []
        for index in range(1, 11):
            checkpoint = self.root / "h3_chains" / "chapter_append" / "checkpoints" / (
                "clip_%04d.safetensors" % index)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            save_file({"video": torch.full((1, 24, 4, 1, 1), float(index)),
                       "audio": torch.zeros((1, 32, 2, 22))}, checkpoint)
            segment = checkpoint.with_suffix(".mp4")
            segment.write_bytes(b"placeholder video")
            self.segments.append({
                "index": index, "id": "scene_%02d" % index,
                "revision": "%032x" % index,
                "checkpoint": chain._relative_output_path(str(checkpoint)),
                "checkpoint_sha256": chain._file_sha256(str(checkpoint)),
                "segment": chain._relative_output_path(str(segment)),
                "segment_sha256": chain._file_sha256(str(segment)),
                "raw_frames": 13, "delivered_frames": 13,
                "blend_frames": 0, "prompt": "test scene %d" % index,
            })
        self.editorial = {
            "format": "h3_chain_editorial_v1", "run_name": "chapter_append",
            "scene_order": [{"scene": i, "scene_id": "scene_%02d" % i}
                            for i in range(1, 11)],
            "chapters": [{"id": "one", "title": "One", "start_scene": 1,
                          "start_scene_id": "scene_01"},
                         {"id": "two", "title": "Two", "start_scene": 5,
                          "start_scene_id": "scene_05"}],
            "placements": [], "trims": [],
        }
        self.partial = self.chapter(8)
        self.complete = self.chapter(10)

    def manifest(self, end):
        return {
            "format": "h3_chain_partial_manifest_v3",
            "run_name": "chapter_append", "planned_clip_count": 10,
            "clip_count": end, "total_delivered_frames": 13 * end,
            "compatibility": {"continuation_mode": "guide"},
            "segments": copy.deepcopy(self.segments[:end]),
            "editorial": copy.deepcopy(self.editorial), "archives": {},
        }

    def chapter(self, end):
        return chain._chapter_manifest_from_manifest(self.manifest(end), 2)[0]

    def export(self, manifest=None, **kwargs):
        options = {"video_vae": self.video, "audio_vae": self.audio,
                   "embed_workflow": False, "save_workers": 2}
        options.update(kwargs)
        result = self.node.export(manifest or self.partial, **options)["result"]
        directory = Path(result[0])
        return directory, json.loads((directory / "export.json").read_text()), result

    def png_state(self, directory):
        return {path.name: (path.stat().st_mtime_ns, path.read_bytes())
                for path in directory.glob("frame_*.png")}

    def test_repeat_is_no_decode_and_preserves_png_and_wav(self):
        output, record, result = self.export()
        before = self.png_state(output)
        wav_before = (output / "audio.wav").read_bytes()
        wav_mtime = (output / "audio.wav").stat().st_mtime_ns
        self.assertEqual(result[1], 52)
        self.assertFalse(record["chapter_complete"])
        again, record, result = self.export(checkpoint_verification="strict")
        self.assertEqual(again, output)
        self.assertEqual(self.video.calls, 4)
        self.assertEqual(self.audio.calls, 4)
        self.assertEqual(self.png_state(output), before)
        self.assertEqual((output / "audio.wav").read_bytes(), wav_before)
        self.assertEqual((output / "audio.wav").stat().st_mtime_ns, wav_mtime)
        self.assertEqual(record["new_frame_count"], 0)
        self.assertEqual(record["reused_frame_count"], 52)
        self.assertIn("WAV reused", result[2])

    def test_16bit_choice_isolated_from_legacy_8bit_and_reusable(self):
        import av
        import numpy as np

        eight, _, _ = self.export(audio_vae=None)
        before = self.png_state(eight)
        # FP16 white must not overflow when scaled to RGB16's 65535 range.
        with patch.object(self.video, "decode", return_value=torch.ones((13, 4, 4, 3), dtype=torch.float16)):
            sixteen, record, result = self.export(audio_vae=None, png_bit_depth="16")
        self.assertNotEqual(sixteen, eight)
        self.assertEqual(record["settings"]["png_bit_depth"], 16)
        self.assertIsNone(result[4])
        with av.open(str(sixteen / "frame_00000001.png")) as container:
            actual = next(container.decode(video=0)).to_ndarray(format="rgb48le")
        np.testing.assert_array_equal(actual, np.full((4, 4, 3), 65535, dtype=np.uint16))
        with patch.object(self.video, "decode", side_effect=AssertionError("decoded reused scene")):
            reused, _, _ = self.export(audio_vae=None, png_bit_depth="16", checkpoint_verification="strict")
        self.assertEqual(reused, sixteen)
        self.assertEqual(self.png_state(eight), before)

    def test_append_exports_only_new_pngs_and_rebuilds_frame_locked_audio(self):
        output, _, _ = self.export()
        before = self.png_state(output)
        extended, record, result = self.export(self.complete)
        self.assertEqual(extended, output)
        self.assertEqual(self.video.calls, 6)
        self.assertEqual(self.audio.calls, 10)  # Four initially, six for new joins.
        self.assertTrue(record["chapter_complete"])
        self.assertEqual(record["new_frame_count"], 26)
        self.assertEqual(record["reused_frame_count"], 52)
        self.assertEqual(result[1], 78)
        self.assertEqual([r["index"] for r in record["clips"]], list(range(5, 11)))
        after = self.png_state(output)
        self.assertEqual({key: after[key] for key in before}, before)
        with wave.open(str(output / "audio.wav"), "rb") as audio:
            self.assertEqual(audio.getnframes(),
                             chain.sample_boundary_from_frames(78, 8000, chain.FPS))
        restored, _ = chain._load_chapter_manifest(
            "chapter_append", 2, self.partial["chapter_manifest_id"])
        self.assertEqual(restored["scene_end"], 8)
        self.assertTrue(list((output / "export.history").glob("*.json")))

    def test_changed_revision_uses_new_folder_and_preserves_old_files(self):
        output, _, _ = self.export()
        before = self.png_state(output)
        changed = copy.deepcopy(self.partial)
        changed["segments"][0]["revision"] = "f" * 32
        other, _, _ = self.export(changed)
        self.assertNotEqual(output, other)
        self.assertEqual(self.png_state(output), before)
        self.assertEqual(self.video.calls, 8)

    def test_changed_trim_or_placement_does_not_append(self):
        output, _, _ = self.export(audio_vae=None)
        self.editorial["trims"] = [
            {"scene": 5, "scene_id": "scene_05", "out_frame": 9}]
        trimmed, record, _ = self.export(self.chapter(8), audio_vae=None)
        self.assertNotEqual(trimmed, output)
        self.assertEqual(record["frame_count"], 48)
        changed = copy.deepcopy(self.partial)
        changed["editorial"]["placements"][0]["start_frame"] = 1
        moved, _, _ = self.export(changed, audio_vae=None)
        self.assertNotEqual(moved, output)

    def test_settings_and_explicit_fresh_export_are_isolated(self):
        output, _, _ = self.export(audio_vae=None)
        for options in ({"png_compression": 0}, {"first_frame_number": 10},
                        {"embed_workflow": True}, {"reuse_existing": False}):
            with self.subTest(options=options):
                other, _, _ = self.export(audio_vae=None, **options)
                self.assertNotEqual(other, output)

    def test_missing_png_does_not_repair_or_overwrite_old_folder(self):
        output, _, _ = self.export(audio_vae=None)
        removed = output / "frame_00000001.png"
        removed.unlink()
        before = self.png_state(output)
        other, _, _ = self.export(audio_vae=None)
        self.assertNotEqual(other, output)
        self.assertFalse(removed.exists())
        self.assertEqual(self.png_state(output), before)

    def test_strict_detects_same_size_same_mtime_file_corruption(self):
        output, _, _ = self.export(audio_vae=None)
        damaged = output / "frame_00000001.png"
        before = damaged.stat()
        contents = bytearray(damaged.read_bytes())
        contents[-1] ^= 1
        damaged.write_bytes(contents)
        os.utime(damaged, ns=(before.st_atime_ns, before.st_mtime_ns))
        other, _, _ = self.export(audio_vae=None, checkpoint_verification="strict")
        self.assertNotEqual(other, output)
        self.assertEqual(damaged.read_bytes(), contents)

    def test_orphan_png_or_legacy_sidecar_is_not_overwritten(self):
        output, record, _ = self.export(audio_vae=None)
        orphan = output / "frame_99999999.png"
        orphan.write_bytes(b"untracked")
        other, _, _ = self.export(audio_vae=None)
        self.assertNotEqual(other, output)
        self.assertEqual(orphan.read_bytes(), b"untracked")
        old = json.loads((other / "export.json").read_text())
        old.pop("incremental")
        chain._atomic_json(str(other / "export.json"), old)
        third, _, _ = self.export(audio_vae=None)
        self.assertNotIn(third, (output, other))

    def test_failed_append_keeps_old_success_and_retries_in_new_folder(self):
        output, before_record, _ = self.export(audio_vae=None)
        before = self.png_state(output)
        with patch.object(self.video, "decode", side_effect=RuntimeError("decode failed")):
            with self.assertRaisesRegex(RuntimeError, "decode failed"):
                self.export(self.complete, audio_vae=None)
        self.assertEqual(self.png_state(output), before)
        self.assertEqual(json.loads((output / "export.json").read_text()), before_record)
        self.assertTrue((output / "export.partial.json").exists())
        retried, _, _ = self.export(self.complete, audio_vae=None)
        self.assertNotEqual(retried, output)

    def test_audio_only_can_repeat_and_grow(self):
        output, record, _ = self.export(video_vae=None)
        self.assertEqual(record["frame_count"], 0)
        self.assertEqual(record["timeline_frame_count"], 52)
        same, _, _ = self.export(video_vae=None)
        self.assertEqual(same, output)
        self.assertEqual(self.audio.calls, 4)
        same, record, _ = self.export(self.complete, video_vae=None)
        self.assertEqual(same, output)
        self.assertEqual(record["timeline_frame_count"], 78)
        self.assertFalse(list(output.glob("*.png")))

    def test_failed_audio_publish_keeps_previous_wav_and_export_index(self):
        output, before_record, _ = self.export()
        before = (output / "audio.wav").read_bytes()
        with patch.object(chain, "_write_wav", side_effect=RuntimeError("WAV failed")):
            with self.assertRaisesRegex(RuntimeError, "WAV failed"):
                self.export(self.complete)
        self.assertEqual((output / "audio.wav").read_bytes(), before)
        self.assertEqual(json.loads((output / "export.json").read_text()), before_record)
        self.assertTrue((output / "export.partial.json").exists())

    def test_older_snapshot_never_truncates_a_grown_export(self):
        output, _, _ = self.export(self.complete, audio_vae=None)
        before = self.png_state(output)
        older, record, _ = self.export(audio_vae=None)
        self.assertNotEqual(older, output)
        self.assertEqual(record["frame_count"], 52)
        self.assertEqual(self.png_state(output), before)

    def test_whole_run_keeps_fresh_folder_behavior(self):
        first, _, _ = self.export(self.manifest(8), audio_vae=None)
        second, _, _ = self.export(self.manifest(8), audio_vae=None)
        self.assertNotEqual(first, second)

    def test_finished_chapter_is_reused_when_other_chapters_grow(self):
        first = chain._chapter_manifest_from_manifest(self.manifest(8), 1)[0]
        later = chain._chapter_manifest_from_manifest(self.manifest(10), 1)[0]
        output, _, _ = self.export(first, audio_vae=None)
        before = self.png_state(output)
        same, record, _ = self.export(later, audio_vae=None)
        self.assertEqual(output, same)
        self.assertEqual(self.png_state(output), before)
        self.assertEqual(record["new_frame_count"], 0)

    @unittest.skipIf(chain.av is None, "PyAV is required for real MP4 assembly")
    def test_partial_chapter_can_also_assemble_saved_mp4s_without_vae(self):
        for segment in self.segments[4:8]:
            path = chain._absolute_output_path(segment["segment"])
            chain._write_segment_video(torch.zeros((13, 16, 16, 3)), path, 24, 18)
            segment["segment_sha256"] = chain._file_sha256(path)
        chapter = self.chapter(8)
        result = chain.MiniMaxH3ChainAssemble().assemble(
            chapter, audio_source="none", filename="partial_chapter", audio_bitrate=192)
        output = Path(result["result"][0])
        self.assertIn("chapters/02_two/final/", output.as_posix())
        with chain.av.open(str(output)) as movie:
            self.assertEqual(sum(1 for _ in movie.decode(video=0)), 52)

    def test_numbering_can_cross_eight_digit_boundary(self):
        output, _, _ = self.export(audio_vae=None, first_frame_number=99999990)
        same, record, _ = self.export(audio_vae=None, first_frame_number=99999990)
        self.assertEqual(output, same)
        self.assertEqual(record["new_frame_count"], 0)

    def test_unrecorded_checkpoint_hash_is_still_checked_for_reuse(self):
        legacy = copy.deepcopy(self.partial)
        legacy["segments"][0].pop("checkpoint_sha256")
        output, _, _ = self.export(legacy, audio_vae=None)
        checkpoint = Path(chain._absolute_output_path(legacy["segments"][0]["checkpoint"]))
        before = checkpoint.stat()
        tensors = chain._st_load(str(checkpoint))
        tensors["video"] += 1
        save_file(tensors, checkpoint)
        os.utime(checkpoint, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(checkpoint.stat().st_size, before.st_size)
        other, _, _ = self.export(legacy, audio_vae=None)
        self.assertNotEqual(other, output)

    def test_export_lock_blocks_competitor_and_releases_on_failure(self):
        with chain._chapter_png_export_lock(self.partial, "png_sequence"):
            with self.assertRaisesRegex(ValueError, "Another export"):
                self.export(audio_vae=None)
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with chain._chapter_png_export_lock(self.partial, "png_sequence"):
                raise RuntimeError("test failure")
        self.export(audio_vae=None)

    def test_symlinked_png_and_sidecar_are_not_reused(self):
        output, _, _ = self.export(audio_vae=None)
        image = output / "frame_00000001.png"
        target = self.root / "preserve.png"
        image.rename(target)
        image.symlink_to(target)
        second, _, _ = self.export(audio_vae=None)
        self.assertNotEqual(second, output)
        sidecar = second / "export.json"
        target_record = self.root / "preserve.json"
        sidecar.rename(target_record)
        sidecar.symlink_to(target_record)
        third, _, _ = self.export(audio_vae=None)
        self.assertNotIn(third, (output, second))


if __name__ == "__main__":
    unittest.main()
