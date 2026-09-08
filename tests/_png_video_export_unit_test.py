#!/usr/bin/env python3
"""Real FFV1 VIDEO -> PNG scene streaming; no models or production files."""

import copy
import errno
from fractions import Fraction
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from _upscale_chain_unit_test import load_package, folder_paths
import av
import numpy as np
from PIL import Image, PngImagePlugin
from comfy_api.latest import InputImpl

package, chain, upscale = load_package()
streaming = importlib.import_module(package.__name__ + ".png_video_export")


def make_video(path, count=5, seed=1, fps=24):
    pixels = np.random.default_rng(seed).integers(0, 65536, (count, 16, 24, 3), dtype=np.uint16)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("ffv1", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 24, 16, "gbrp16le"
        stream.time_base = stream.codec_context.time_base = Fraction(1, 24000)
        for number, image in enumerate(pixels):
            frame = av.VideoFrame.from_ndarray(image, format="rgb48le")
            frame.pts, frame.time_base = number * (24000 // fps), Fraction(1, 24000)
            container.mux(stream.encode(frame))
        container.mux(stream.encode())
    return InputImpl.VideoFromFile(str(path)), pixels


class PNGVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_root = folder_paths.output_directory
        folder_paths.output_directory = str(self.root)
        self.addCleanup(setattr, folder_paths, "output_directory", self.old_root)
        self.node = chain.MiniMaxH3ChainExportPNG()
        sources = [{"index": index, "revision": "%032x" % index,
                    "checkpoint_sha256": "%064x" % index, "id": "scene_%d" % index,
                    "raw_frames": 5, "delivered_frames": 3, "width": 24, "height": 16,
                    "prompt": "A blue door. Café."} for index in range(1, 8)]
        self.state = {"run_name": "demo", "profile": "pixel",
                      "png_export_session": "test-session",
                      "profile_config": {"backend": "pixel", "save_latent": False},
                      "index": 1, "range_start": 1, "end_clip": 7,
                      "source_manifest": {"run_name": "demo", "clip_count": 7, "segments": sources},
                      "segments": []}
        self.video, self.pixels = make_video(self.root / "source.mkv")

    def export(self, scene=1, video=None, state=None, **kwargs):
        return self.node.export(video=video or self.video, state=state or dict(self.state, index=scene), **kwargs)

    def test_seven_scenes_are_bounded_numbered_trimmed_and_passed_through(self):
        refs, peak, lock = [], [0], threading.Lock()
        original = chain._write_png

        def track(path, pixels, compression, metadata):
            self.assertEqual(pixels.shape, (16, 24, 3))
            with lock:
                refs.append(weakref.ref(pixels))
                peak[0] = max(peak[0], sum(ref() is not None for ref in refs))
            original(path, pixels, compression, metadata)

        with patch.object(chain, "_write_png", track), patch.object(
                InputImpl.VideoFromFile, "get_components", side_effect=AssertionError("full VIDEO materialized")):
            for scene in range(1, 8):
                result = self.export(scene, output_folder="delivery/seven", first_frame_number=101,
                                     save_workers=3, png_bit_depth="16")
                self.assertIs(result["result"][4], self.video)
                self.assertEqual(result["result"][1], scene * 3)
        directory = Path(result["result"][0])
        files = sorted(directory.glob("frame_*.png"))
        self.assertEqual([p.name for p in files], ["frame_%08d.png" % i for i in range(101, 122)])
        for i, path in enumerate(files):
            with av.open(str(path)) as container:
                actual = next(container.decode(video=0)).to_ndarray(format="rgb48le")
            np.testing.assert_array_equal(actual, self.pixels[2 + i % 3])
        self.assertLessEqual(peak[0], 3)
        self.assertFalse(any(ref() is not None for ref in refs), "no pixel batches survive between scenes")
        with Image.open(files[0]) as image:
            self.assertEqual(image.info["h3_prompt"], "A blue door. Café.")
        record = json.loads((directory / "export.json").read_text())
        self.assertTrue(record["complete"])
        self.assertEqual(record["settings"]["png_bit_depth"], 16)
        self.assertEqual([c["trim_frames"] for c in record["clips"]], [2] * 7)
        self.assertEqual(self.state["segments"], [], "the input state was not mutated")

    def test_bit_depth_choice_and_existing_node_output_slots(self):
        schema = self.node.INPUT_TYPES()
        self.assertEqual(schema["optional"]["png_bit_depth"][0], ["8", "16"])
        self.assertEqual(schema["optional"]["png_bit_depth"][1]["default"], "8")
        self.assertEqual(self.node.RETURN_NAMES[:4], ("output_directory", "frame_count", "status", "audio_path"))
        for bits in ("8", "16"):
            result = self.export(output_folder="depth_" + bits, png_bit_depth=bits)
            path = Path(result["result"][0]) / "frame_00000001.png"
            self.assertEqual(path.read_bytes()[24], int(bits))
            with av.open(str(path)) as container:
                actual = next(container.decode(video=0)).to_ndarray(format="rgb48le" if bits == "16" else "rgb24")
            expected = self.pixels[2] if bits == "16" else ((self.pixels[2].astype(np.uint32) + 128) // 257).astype(np.uint8)
            np.testing.assert_array_equal(actual, expected)
        with self.assertRaisesRegex(ValueError, "must be 8 or 16"):
            self.export(png_bit_depth="12")

    def test_resume_reuse_and_preserve_all_earlier_png_bytes(self):
        first = self.export()
        directory = Path(first["result"][0])
        before = {p: p.read_bytes() for p in directory.glob("frame_*.png")}
        with patch.object(chain, "_write_png", side_effect=AssertionError("reuse rewrote pixels")):
            reused = self.export(checkpoint_verification="strict")
        self.assertIn("reused", reused["result"][2])
        self.export(2, state=dict(self.state, index=2, range_start=2))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaisesRegex(ValueError, "gap"):
            self.export(4)
        other, _ = make_video(self.root / "other.mkv", seed=44)
        variant = Path(self.export(video=other)["result"][0])
        self.assertEqual(variant, directory.with_name(directory.name + "_2"))
        fresh = self.export(reuse_existing=False, state=dict(self.state, png_export_session="fresh"))
        self.assertEqual(Path(fresh["result"][0]).name, directory.name + "_3")
        resized = self.export(3, png_bit_depth="16")
        self.assertEqual(Path(resized["result"][0]).name, directory.name + "_4")
        changed = copy.deepcopy(self.state)
        changed["source_manifest"]["segments"][0]["revision"] = "a" * 32
        branch = self.export(3, state=dict(changed, index=3))
        self.assertEqual(Path(branch["result"][0]).name, directory.name + "_5")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_failed_scene_never_commits_partial_frames_and_can_retry(self):
        result = self.export()
        directory = Path(result["result"][0])
        before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        for failure in (RuntimeError("disk full"), KeyboardInterrupt("interrupted")):
            with self.subTest(failure=failure):
                with patch.object(chain, "_write_png", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        self.export(2)
                self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})
                self.assertFalse(list(directory.glob(".png_scene_*")))
        original_atomic = streaming.persistence.atomic_json

        def fail_index(path, value):
            if Path(path).name == "export.json":
                raise OSError("publish failed")
            return original_atomic(path, value)

        with patch.object(streaming.persistence, "atomic_json", side_effect=fail_index):
            with self.assertRaisesRegex(OSError, "publish failed"):
                self.export(2)
        self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})
        self.assertEqual(self.export(2)["result"][1], 6)

    def test_rerender_uses_one_variant_for_seven_scenes_and_third_pass(self):
        for scene in range(1, 8):
            original = self.export(scene, output_folder="final_upscale")
        original_dir = Path(original["result"][0])
        before = {p: p.read_bytes() for p in original_dir.glob("frame_*.png")}
        other, _ = make_video(self.root / "other.mkv", seed=44)
        for scene in range(1, 8):
            state = dict(self.state, index=scene, png_export_session="second-pass")
            # Recursive execution can use a different node instance per scene.
            self.node = chain.MiniMaxH3ChainExportPNG()
            result = self.export(scene, video=other, state=state, output_folder="final_upscale")
            self.assertEqual(Path(result["result"][0]).name, "final_upscale_2")
            self.assertEqual(result["result"][1], scene * 3)
            self.assertIs(result["result"][4], other)
        third = self.export(state=dict(self.state, png_export_session="third-pass"), output_folder="final_upscale")
        self.assertEqual(Path(third["result"][0]).name, "final_upscale_3")
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_reuse_disabled_creates_one_fresh_variant_per_pass_not_per_scene(self):
        self.export(output_folder="fresh")
        for scene in (1, 2, 3):
            state = dict(self.state, index=scene, png_export_session="forced-pass")
            result = self.export(scene, state=state, output_folder="fresh", reuse_existing=False)
            self.assertEqual(Path(result["result"][0]).name, "fresh_2")
        # Retrying the same accepted scene is not a new forced export.
        self.assertIn("reused", self.export(3, state=state, output_folder="fresh", reuse_existing=False)["result"][2])
        resumed = self.export(4, state=dict(self.state, index=4, png_export_session="fresh-at-four"),
                              output_folder="fresh", reuse_existing=False)
        self.assertEqual(Path(resumed["result"][0]).name, "fresh_3")
        self.assertEqual(resumed["result"][1], 3)

    def test_mid_sequence_variant_keeps_independent_prefix_and_recovers_copy_failure(self):
        for scene in (1, 2, 3):
            result = self.export(scene, output_folder="prefix")
        old_dir = Path(result["result"][0])
        before = {p: p.read_bytes() for p in old_dir.glob("frame_*.png")}
        other, _ = make_video(self.root / "other.mkv", seed=44)
        state = dict(self.state, index=3, png_export_session="changed-pass")
        publish = streaming.transaction.publish

        def stop_after_first_prefix(*args, **kwargs):
            value = publish(*args, **kwargs)
            if args[5]["last_scene"] == 1:
                raise OSError("prefix copy interrupted")
            return value

        with patch.object(streaming.transaction, "publish", side_effect=stop_after_first_prefix):
            with self.assertRaisesRegex(OSError, "prefix copy interrupted"):
                self.export(3, video=other, state=state, output_folder="prefix")
        result = self.export(3, video=other, state=state, output_folder="prefix")
        new_dir = Path(result["result"][0])
        self.assertEqual(new_dir.name, "prefix_2")
        self.assertEqual(result["result"][1], 9)
        record = json.loads((new_dir / "export.json").read_text())
        self.assertEqual([clip["index"] for clip in record["clips"]], [1, 2, 3])
        for path in sorted(old_dir.glob("frame_*.png"))[:6]:
            copied = new_dir / path.name
            self.assertEqual(path.read_bytes(), copied.read_bytes())
            self.assertNotEqual(path.stat().st_ino, copied.stat().st_ino)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertIn("reused", self.export(3, video=other, state=state, output_folder="prefix")["result"][2])
        state["index"] = 4
        self.assertEqual(self.export(4, video=other, state=state, output_folder="prefix")["result"][1], 12)

    def test_new_source_scenes_six_seven_keep_prefix_and_frame_numbers(self):
        for scene in range(1, 8):
            result = self.export(scene, output_folder="branch", first_frame_number=101)
        original = Path(result["result"][0])
        before = {p: p.read_bytes() for p in original.glob("frame_*.png")}
        state = copy.deepcopy(self.state)
        state["png_export_session"] = "new-source-branch"
        for source in state["source_manifest"]["segments"][5:]:
            source["revision"] = "b" * 32
        other, _ = make_video(self.root / "other.mkv", seed=44)
        for scene in (6, 7):
            state["index"] = scene
            result = self.export(video=other, state=state, output_folder="branch", first_frame_number=101)
        variant = Path(result["result"][0])
        self.assertEqual(variant.name, "branch_2")
        record = json.loads((variant / "export.json").read_text())
        self.assertEqual([c["index"] for c in record["clips"]], list(range(1, 8)))
        self.assertEqual(record["clips"][5]["first_frame_number"], 116)
        self.assertEqual(record["clips"][6]["last_frame_number"], 121)
        self.assertEqual(record["source_manifest"], state["source_manifest"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        for path in list(sorted(before))[:15]:
            self.assertEqual(path.read_bytes(), (variant / path.name).read_bytes())
            self.assertNotEqual(path.stat().st_ino, (variant / path.name).stat().st_ino)

    def test_changed_earlier_source_is_not_copied_into_variant(self):
        for scene in (1, 2, 3):
            self.export(scene, output_folder="changed-prefix")
        state = copy.deepcopy(self.state)
        state.update(index=3, png_export_session="branch")
        state["source_manifest"]["segments"][0]["revision"] = "b" * 32
        result = self.export(state=state, output_folder="changed-prefix")
        record = json.loads((Path(result["result"][0]) / "export.json").read_text())
        self.assertEqual([c["index"] for c in record["clips"]], [3])

    def test_deleted_scene_forks_with_surviving_prefix_and_never_replays_tombstone(self):
        for scene in (1, 2, 3):
            result = self.export(scene, output_folder="delete-resume")
        original = Path(result["result"][0])
        record = json.loads((original / "export.json").read_text())
        # Install the same take identity persisted by Segment Save; no GPU or
        # live checkpoint is needed to exercise the actual deletion manager.
        revision = "a" * 32
        relative = "h3_chains/demo/upscaled/pixel/checkpoints/clip_0002.%s.json" % revision
        metadata = {"format": "h3_chain_upscale_segment_v1", "run_name": "demo", "profile": "pixel",
                    "profile_config": self.state["profile_config"],
                    "source_scene_contract": record["clips"][1]["source_contract"],
                    "segment": {"index": 2, "revision": revision, "revision_metadata": relative,
                                "context_steps": 0, "png_export_owner": record["clips"][1]["processing_owners"][0]}}
        streaming.persistence.atomic_json(self.root / relative, metadata)
        processing = importlib.import_module(package.__name__ + ".processing_checkpoint_delete")
        manager = processing.ProcessingCheckpointManager(self.root)
        preview = manager.deletion_preview("demo", relative)
        manager.delete("demo", relative, preview["snapshot"])
        self.assertFalse((original / "frame_00000004.png").exists())
        self.assertTrue((original / "frame_00000007.png").exists())
        state = dict(self.state, index=2, png_export_session="repair")
        result = self.export(state=state, output_folder="delete-resume")
        variant = Path(result["result"][0])
        self.assertEqual(variant.name, "delete-resume_2")
        self.assertEqual(result["result"][1], 6)
        self.assertEqual((variant / "frame_00000001.png").read_bytes(),
                         (original / "frame_00000001.png").read_bytes())
        result = self.export(state=dict(state, index=3), output_folder="delete-resume")
        self.assertEqual(Path(result["result"][0]), variant)
        self.assertEqual(result["result"][1], 9)
        self.assertNotIn("deleted_scenes", json.loads((variant / "export.json").read_text()))

    def test_identical_pixel_reuse_registers_additional_owner_and_custom_folder(self):
        result = self.export(output_folder="custom/export")
        directory = Path(result["result"][0])
        before = json.loads((directory / "export.json").read_text())["clips"][0]
        self.export(state=dict(self.state, png_export_session="another-take"), output_folder="custom/export")
        after = json.loads((directory / "export.json").read_text())["clips"][0]
        self.assertEqual(len(after["processing_owners"]), 2)
        self.assertEqual(after["processing_owners"][0], before["processing_owners"][0])
        catalog = json.loads((self.root / "h3_chains/demo/png_exports.json").read_text())
        self.assertEqual(catalog["directories"], ["custom/export"])

    def test_numbering_skips_occupied_siblings_and_old_run_binding_stays_put(self):
        self.export(output_folder="chosen")
        (self.root / "chosen_2").mkdir()
        user_file = self.root / "chosen_2" / "notes.txt"
        user_file.write_text("keep")
        other, _ = make_video(self.root / "other.mkv", seed=44)
        result = self.export(video=other, state=dict(self.state, png_export_session="other-run"), output_folder="chosen")
        self.assertEqual(Path(result["result"][0]).name, "chosen_3")
        self.assertEqual(Path(self.export(2, output_folder="chosen")["result"][0]).name, "chosen")
        self.assertEqual(user_file.read_text(), "keep")

    def test_recreated_container_reuses_identical_pixels_in_new_and_legacy_exports(self):
        for bits in ("8", "16"):
            for legacy in (False, True):
                with self.subTest(bits=bits, legacy=legacy):
                    folder = "retry_%s_%s" % (bits, legacy)
                    result = self.export(output_folder=folder, png_bit_depth=bits)
                    directory = Path(result["result"][0])
                    if legacy:
                        record_path = directory / "export.json"
                        record = json.loads(record_path.read_text())
                        record["clips"][0].pop("pixel_sha256")
                        chain._atomic_json(str(record_path), record)
                    before = {p: p.read_bytes() for p in directory.glob("frame_*.png")}
                    recreated, pixels = make_video(self.root / "recreated.mkv")
                    np.testing.assert_array_equal(pixels, self.pixels)
                    self.assertNotEqual(chain._file_sha256(str(self.root / "source.mkv")),
                                        chain._file_sha256(str(self.root / "recreated.mkv")))
                    with patch.object(chain, "_write_png", side_effect=AssertionError("rewrote saved PNG")):
                        reused = self.export(video=recreated, output_folder=folder, png_bit_depth=bits)
                    self.assertIs(reused["result"][4], recreated)
                    self.assertIn("reused", reused["result"][2])
                    self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_normal_cancel_during_publication_keeps_finished_scenes(self):
        from comfy.model_management import InterruptProcessingException
        self.export(1)
        directory = Path(self.export(2)["result"][0])
        before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        original = streaming._publish_frame
        calls = []

        def cancel(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise InterruptProcessingException()
            return original(source, target)

        with patch.object(streaming, "_publish_frame", side_effect=cancel):
            with self.assertRaises(InterruptProcessingException):
                self.export(3)
        self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})
        self.assertFalse(list(directory.glob(".png_scene_*")))
        self.assertEqual(self.export(3)["result"][1], 9)

    def crash_export(self, scene, folder, boundary):
        # os._exit deliberately bypasses every Python finally/context manager.
        # Only disposable CPU fixture media is visible to this child process.
        script = r'''
import sys
root, folder, boundary, state_json, tests = sys.argv[1:]
sys.path.insert(0, tests)
import _png_video_export_unit_test as test
test.folder_paths.output_directory = root
state = test.json.loads(state_json)
original_publish = test.streaming._publish_frame
def publish(source, target):
    if boundary == "copy":
        with target.open("xb") as handle:
            handle.write(source.read_bytes()[:16])
            handle.flush()
            test.os.fsync(handle.fileno())
        test.os._exit(73)
    original_publish(source, target)
    test.os._exit(73)
original_json = test.streaming.persistence.atomic_json
def atomic_json(path, value):
    original_json(path, value)
    if test.Path(path).name == "export.json":
        test.os._exit(73)
if boundary == "index":
    test.streaming.persistence.atomic_json = atomic_json
else:
    test.streaming._publish_frame = publish
test.chain.MiniMaxH3ChainExportPNG().export(
    video=test.InputImpl.VideoFromFile(str(test.Path(root) / "source.mkv")),
    state=state, output_folder=folder)
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.root), folder, boundary,
                                 json.dumps(dict(self.state, index=scene)), str(Path(__file__).parent)],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 73, result.stdout + result.stderr)

    def test_process_exit_recovers_partial_copy_publication_and_committed_index(self):
        for boundary in ("publish", "copy", "index"):
            with self.subTest(boundary=boundary):
                folder = "crash_" + boundary
                self.export(1, output_folder=folder)
                directory = Path(self.export(2, output_folder=folder)["result"][0])
                before = {p: p.read_bytes() for p in directory.glob("frame_*.png")}
                self.crash_export(3, folder, boundary)
                self.assertTrue((directory / streaming.transaction.PENDING).exists())
                with patch.object(chain, "_write_png", side_effect=AssertionError("recovery decoded again")):
                    recovered = self.export(3, output_folder=folder)
                self.assertEqual(recovered["result"][1], 9)
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                self.assertFalse((directory / streaming.transaction.PENDING).exists())
                self.assertEqual(len(list(directory.glob("frame_*.png"))), 9)
                if boundary == "copy":
                    preserved = list(directory.glob(".png_scene_*/conflict_*"))
                    self.assertEqual(len(preserved), 1)
                    self.assertEqual(preserved[0].stat().st_size, 16)
                else:
                    self.assertFalse(list(directory.glob(".png_scene_*")))
                self.assertEqual(self.export(4, output_folder=folder)["result"][1], 12)

    def test_process_exit_recovers_in_variant_and_new_session_resumes_it(self):
        self.export(1, output_folder="variant_crash")
        old, _ = make_video(self.root / "old.mkv", seed=44)
        result = self.export(1, video=old, output_folder="variant_crash")
        directory = Path(result["result"][0])
        self.assertEqual(directory.name, "variant_crash_2")
        self.crash_export(2, "variant_crash", "copy")
        self.assertTrue((directory / streaming.transaction.PENDING).exists())
        with patch.object(chain, "_write_png", side_effect=AssertionError("variant recovery decoded again")):
            result = self.export(2, state=dict(self.state, index=2, png_export_session="after-restart"),
                                 output_folder="variant_crash")
        self.assertEqual(Path(result["result"][0]), directory)
        self.assertEqual(result["result"][1], 6)
        self.assertFalse((directory / streaming.transaction.PENDING).exists())

    def test_variant_corrupt_journal_and_binding_cannot_escape_output(self):
        self.export(output_folder="bindings")
        binding = next((self.root / "bindings/.png_variants").glob("*.json"))
        chain._atomic_json(str(binding), {"directory": "../escape"})
        with self.assertRaisesRegex(ValueError, "binding"):
            self.export(output_folder="bindings")
        self.assertFalse((self.root / "escape").exists())

    def test_legacy_state_without_session_continues_newest_variant(self):
        state = dict(self.state)
        state.pop("png_export_session")
        self.export(state=state, output_folder="legacy")
        other, _ = make_video(self.root / "other.mkv", seed=44)
        result = self.export(state=state, video=other, output_folder="legacy")
        self.assertEqual(Path(result["result"][0]).name, "legacy_2")
        state["index"] = 2
        self.assertEqual(self.export(2, state=state, video=other, output_folder="legacy")["result"][0], result["result"][0])

    def test_uncertain_index_acknowledgement_never_rolls_back_committed_frames(self):
        directory = Path(self.export(1)["result"][0])
        original = streaming.persistence.atomic_json

        def lost_ack(path, value):
            original(path, value)
            if Path(path).name == "export.json":
                raise OSError("share acknowledgement lost")

        with patch.object(streaming.persistence, "atomic_json", side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, "acknowledgement lost"):
                self.export(2)
        self.assertEqual(len(list(directory.glob("frame_*.png"))), 6)
        self.assertEqual(self.export(2, checkpoint_verification="strict")["result"][1], 6)
        self.assertFalse((directory / streaming.transaction.PENDING).exists())

    def test_pending_recovery_rejects_changed_branch_settings_and_unsafe_addresses(self):
        folder = "guarded_recovery"
        self.export(1, output_folder=folder)
        directory = Path(self.export(2, output_folder=folder)["result"][0])
        self.crash_export(3, folder, "publish")
        before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        changed = copy.deepcopy(self.state)
        changed["index"] = 3
        changed["source_manifest"]["segments"][2]["revision"] = "f" * 32
        with self.assertRaisesRegex(ValueError, "branch/order"):
            self.export(3, output_folder=folder, state=changed)
        variant = self.export(3, output_folder=folder, png_bit_depth="16")
        self.assertEqual(Path(variant["result"][0]).name, folder + "_2")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        pending = directory / streaming.transaction.PENDING
        saved = json.loads(pending.read_text())
        for change in ("escape", "earlier_frame", "symlink"):
            bad = copy.deepcopy(saved)
            if change == "escape":
                bad["stage"] = "../../elsewhere"
            elif change == "earlier_frame":
                bad["record"]["clips"][-1]["files"][0]["file"] = "frame_00000001.png"
            else:
                stage = directory / bad["stage"]
                (stage / "frame_00000008.png").unlink()
                (stage / "frame_00000008.png").symlink_to(directory / "frame_00000001.png")
            chain._atomic_json(str(pending), bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.export(3, output_folder=folder)
        self.assertEqual(before[directory / "frame_00000001.png"], (directory / "frame_00000001.png").read_bytes())

    def test_no_overwrite_or_symlink_escape(self):
        result = self.export(output_folder="chosen")
        directory = Path(result["result"][0])
        untracked = directory / "frame_00000004.png"
        untracked.write_bytes(b"user image")
        variant = self.export(2, output_folder="chosen")
        self.assertEqual(Path(variant["result"][0]).name, "chosen_2")
        self.assertEqual(untracked.read_bytes(), b"user image")
        for folder in ("../escape", str(self.root), "/outside/output"):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.export(output_folder=folder)
        (self.root / "linked").symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.export(output_folder="linked/child")

    def test_strict_verification_detects_modified_png(self):
        result = self.export()
        path = Path(result["result"][0]) / "frame_00000001.png"
        saved = path.stat()
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        import os
        os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        variant = self.export(2, checkpoint_verification="strict")
        self.assertEqual(Path(variant["result"][0]).name, Path(result["result"][0]).name + "_2")
        self.assertEqual(path.read_bytes(), data[:-1] + bytes([data[-1] ^ 1]))

    def test_timestamp_only_changes_reuse_and_append_without_rewriting_pngs(self):
        for verification in ("cached", "strict"):
            with self.subTest(verification=verification):
                result = self.export(output_folder=verification)
                directory = Path(result["result"][0])
                record_before = (directory / "export.json").read_bytes()
                for path in directory.glob("frame_*.png"):
                    saved = path.stat()
                    os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns + 49_000_000_000))
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.glob("frame_*.png")}
                with patch.object(chain, "_write_png", side_effect=AssertionError("timestamp drift rewrote PNGs")), patch.object(
                        chain, "_file_sha256", wraps=chain._file_sha256) as hasher:
                    reused = self.export(output_folder=verification, checkpoint_verification=verification)
                self.assertIs(reused["result"][4], self.video)
                self.assertIn("reused", reused["result"][2])
                hashed = {Path(call.args[0]) for call in hasher.call_args_list}
                self.assertTrue(set(before) <= hashed, "changed timestamps must trigger SHA-256 verification")
                self.assertEqual((directory / "export.json").read_bytes(), record_before)
                self.assertEqual(self.export(2, output_folder=verification, checkpoint_verification=verification)["result"][1], 6)
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def test_cached_unchanged_stat_still_skips_png_hashing(self):
        self.export()
        original = chain._file_sha256

        def hash_file(path):
            if Path(path).suffix == ".png":
                raise AssertionError("unchanged cached PNG was unnecessarily hashed")
            return original(path)

        with patch.object(chain, "_file_sha256", hash_file):
            self.assertIn("reused", self.export()["result"][2])

    def test_changed_timestamp_does_not_hide_changed_png_content(self):
        for verification in ("cached", "strict"):
            with self.subTest(verification=verification):
                result = self.export(output_folder=verification)
                directory = Path(result["result"][0])
                path = directory / "frame_00000001.png"
                saved = path.stat()
                data = path.read_bytes()
                path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns + 49_000_000_000))
                before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
                variant = self.export(2, output_folder=verification, checkpoint_verification=verification)
                self.assertEqual(Path(variant["result"][0]).name, verification + "_2")
                self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})

    def test_intentional_png_edits_are_preserved_but_not_silently_adopted(self):
        for verification in ("cached", "strict"):
            for kind in ("pixels", "metadata"):
                with self.subTest(verification=verification, kind=kind):
                    folder = "%s_%s" % (verification, kind)
                    result = self.export(output_folder=folder)
                    directory = Path(result["result"][0])
                    path = directory / "frame_00000001.png"
                    with Image.open(path) as original:
                        edited = original.copy()
                    if kind == "pixels":
                        old = edited.getpixel((0, 0))
                        edited.putpixel((0, 0), (old[0] ^ 255, old[1], old[2]))
                    metadata = PngImagePlugin.PngInfo()
                    metadata.add_text("edit_note", "User intentionally saved this frame")
                    edited.save(path, pnginfo=metadata, compress_level=9)
                    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
                    variant = self.export(2, output_folder=folder, checkpoint_verification=verification)
                    self.assertEqual(Path(variant["result"][0]).name, folder + "_2")
                    self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})

    def test_input_guards_and_failed_frame_clock(self):
        with self.assertRaisesRegex(ValueError, "file-backed"):
            self.export(video=object())
        with self.assertRaisesRegex(ValueError, "state"):
            self.node.export(video=self.video)
        with self.assertRaisesRegex(ValueError, "Disconnect manifest"):
            self.export(manifest={})
        with self.assertRaisesRegex(ValueError, "Disconnect manifest"):
            self.export(video_vae=object())
        with self.assertRaisesRegex(ValueError, "require the VIDEO"):
            self.node.export(state=self.state)
        with patch.object(self.video, "get_active_trim_window", return_value=(1.0, 0.0)):
            with self.assertRaisesRegex(ValueError, "trims or crops"):
                self.export()
        for count in (4, 6):
            video, _ = make_video(self.root / ("wrong_%d.mkv" % count), count=count)
            with self.assertRaisesRegex(ValueError, "frame count|exact RAW"):
                self.export(video=video, output_folder="wrong")
            self.assertFalse(list((self.root / "wrong").glob("frame_*.png")))
        video, _ = make_video(self.root / "rate.mkv", fps=30)
        with self.assertRaisesRegex(ValueError, "frame rate"):
            self.export(video=video)

    def test_folder_lock_and_default_location(self):
        directory = self.root / "h3_chains/demo/upscaled/pixel/frames/png_sequence"
        with streaming._folder_lock(self.root, directory):
            with self.assertRaisesRegex(ValueError, "Another PNG export"):
                self.export()
        result = self.export()
        self.assertEqual(Path(result["result"][0]), directory)

    def test_network_share_without_hardlinks(self):
        for code in (errno.EACCES, errno.EPERM, errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS):
            with self.subTest(errno=code):
                folder = "share_%d" % code
                with patch.object(streaming.os, "link", side_effect=OSError(code, "no links")), patch.object(
                        streaming.shutil, "copyfileobj", wraps=streaming.shutil.copyfileobj) as copier:
                    result = self.export(output_folder=folder)
                self.assertEqual(copier.call_count, 3)
                self.assertTrue(all(call.kwargs["length"] == 1024 * 1024 for call in copier.call_args_list))
                self.assertEqual(self.export(output_folder=folder, checkpoint_verification="strict")["result"][1], 3)
                record = json.loads((Path(result["result"][0]) / "export.json").read_text())
                self.assertEqual(len(record["clips"][0]["files"]), 3)
                self.assertIs(result["result"][4], self.video)

    def test_windows_unsupported_hardlinks_copy_without_overwriting(self):
        source = self.root / "staged.png"
        source.write_bytes(b"lossless PNG bytes")
        for code in (1, 50):
            error = OSError(errno.EINVAL, "Windows hard links unsupported")
            error.winerror = code
            target = self.root / ("windows_%d.png" % code)
            with patch.object(streaming.os, "link", side_effect=error):
                streaming._publish_frame(source, target)
                self.assertEqual(target.read_bytes(), source.read_bytes())
                with self.assertRaises(FileExistsError):
                    streaming._publish_frame(source, target)
        error = OSError(errno.EINVAL, "bad parameter")
        error.winerror = 87
        with patch.object(streaming.os, "link", side_effect=error):
            with self.assertRaises(OSError) as raised:
                streaming._publish_frame(source, self.root / "invalid.png")
        self.assertIs(raised.exception, error)
        self.assertFalse((self.root / "invalid.png").exists())

    def test_windows_junction_output_rejected(self):
        junction = self.root / "junction"
        junction.mkdir()
        with patch.object(Path, "is_junction", new=lambda path: path == junction, create=True):
            with self.assertRaisesRegex(ValueError, "junction"):
                self.export(output_folder="junction/frames")
        self.assertEqual(list(junction.iterdir()), [])

    def test_windows_folder_lock_uses_first_byte_and_releases_on_failure(self):
        calls = []
        def locking(fd, operation, count):
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 0)
            self.assertEqual(count, 1)
            self.assertGreaterEqual(os.fstat(fd).st_size, 1)
            calls.append(operation)
        windows = SimpleNamespace(name="nt", SEEK_END=os.SEEK_END)
        locks = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
        with patch.object(streaming, "os", windows), patch.dict(sys.modules, msvcrt=locks):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                with streaming._folder_lock(self.root, self.root / "locked"):
                    raise RuntimeError("cancelled")
            self.assertEqual(calls, [2, 0])
            calls.clear()
            with streaming._folder_lock(self.root, self.root / "locked"):
                pass
            self.assertEqual(calls, [2, 0])

    def test_denied_hardlink_copy_never_overwrites_existing_file_or_symlink(self):
        source = self.root / "staged.png"
        source.write_bytes(b"new PNG")
        existing = self.root / "existing.png"
        existing.write_bytes(b"user PNG")
        linked = self.root / "linked.png"
        linked.symlink_to(existing)
        with patch.object(streaming.os, "link", side_effect=PermissionError(errno.EACCES, "links denied")):
            for target in (existing, linked):
                with self.subTest(target=target), self.assertRaises(FileExistsError):
                    streaming._publish_frame(source, target)
        self.assertEqual(existing.read_bytes(), b"user PNG")
        self.assertEqual(source.read_bytes(), b"new PNG")
        self.assertTrue(linked.is_symlink())

    def test_hardlink_fallback_preserves_real_write_errors(self):
        source, target = self.root / "staged.png", self.root / "denied.png"
        source.write_bytes(b"new PNG")
        original_open = Path.open
        denied = PermissionError(errno.EACCES, "writes denied", str(target))

        def open_file(path, *args, **kwargs):
            if path == target and args == ("xb",):
                raise denied
            return original_open(path, *args, **kwargs)

        with patch.object(streaming.os, "link", side_effect=PermissionError(errno.EACCES, "links denied")), patch.object(
                Path, "open", open_file):
            with self.assertRaises(PermissionError) as failure:
                streaming._publish_frame(source, target)
        self.assertIs(failure.exception, denied)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), b"new PNG")
        full = OSError(errno.ENOSPC, "disk full")
        with patch.object(streaming.os, "link", side_effect=full), patch.object(
                streaming.shutil, "copyfileobj", side_effect=AssertionError("unexpected fallback")):
            with self.assertRaises(OSError) as failure:
                streaming._publish_frame(source, target)
        self.assertIs(failure.exception, full)

    def test_network_copy_failure_rolls_back_only_current_scene(self):
        result = self.export()
        directory = Path(result["result"][0])
        before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        original_copy = streaming.shutil.copyfileobj
        for failure in (OSError(errno.EIO, "share disconnected"), KeyboardInterrupt("interrupted")):
            calls = []

            def copy_frame(incoming, outgoing, length):
                calls.append(outgoing.name)
                if len(calls) == 2:
                    outgoing.write(incoming.read(16))
                    raise failure
                return original_copy(incoming, outgoing, length)

            with self.subTest(failure=failure), patch.object(
                    streaming.os, "link", side_effect=PermissionError(errno.EACCES, "links denied")), patch.object(
                    streaming.shutil, "copyfileobj", copy_frame):
                with self.assertRaises(type(failure)):
                    self.export(2)
            self.assertEqual(len(calls), 2)
            self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})
            self.assertFalse(list(directory.glob(".png_scene_*")))
        with patch.object(streaming.os, "link", side_effect=PermissionError(errno.EACCES, "links denied")):
            self.assertEqual(self.export(2, checkpoint_verification="strict")["result"][1], 6)

    def test_selected_range_starts_its_own_numbering(self):
        state = dict(self.state, index=3, range_start=3, end_clip=4)
        first = self.export(state=state, first_frame_number=0)
        second = self.export(state=dict(state, index=4), first_frame_number=0)
        directory = Path(first["result"][0])
        self.assertEqual(first["result"][1], 3)
        self.assertEqual(second["result"][1], 6)
        self.assertEqual([p.name for p in sorted(directory.glob("frame_*.png"))],
                         ["frame_%08d.png" % n for n in range(6)])
        record = json.loads((directory / "export.json").read_text())
        self.assertEqual([clip["index"] for clip in record["clips"]], [3, 4])
        self.assertTrue(record["complete"])


if __name__ == "__main__":
    unittest.main(argv=["h3-png-video-test"])
