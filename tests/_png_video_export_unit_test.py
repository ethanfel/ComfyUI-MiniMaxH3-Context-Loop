#!/usr/bin/env python3
"""Real FFV1 VIDEO -> PNG scene streaming; no models or production files."""

import copy
from fractions import Fraction
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import weakref

from _upscale_chain_unit_test import load_package, folder_paths
import av
import numpy as np
from PIL import Image
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
        with self.assertRaisesRegex(ValueError, "different PNGs"):
            self.export(video=other)
        with self.assertRaisesRegex(ValueError, "reuse is disabled"):
            self.export(reuse_existing=False)
        with self.assertRaisesRegex(ValueError, "another sequence/settings"):
            self.export(3, png_bit_depth="16")
        changed = copy.deepcopy(self.state)
        changed["source_manifest"]["segments"][0]["revision"] = "a" * 32
        with self.assertRaisesRegex(ValueError, "branch/order changed"):
            self.export(3, state=dict(changed, index=3))
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
        with patch.object(chain, "_atomic_json", side_effect=OSError("publish failed")):
            with self.assertRaisesRegex(OSError, "publish failed"):
                self.export(2)
        self.assertEqual(before, {p: p.read_bytes() for p in directory.iterdir() if p.is_file()})
        self.assertEqual(self.export(2)["result"][1], 6)

    def test_no_overwrite_or_symlink_escape(self):
        result = self.export(output_folder="chosen")
        directory = Path(result["result"][0])
        untracked = directory / "frame_00000004.png"
        untracked.write_bytes(b"user image")
        with self.assertRaisesRegex(ValueError, "untracked"):
            self.export(2, output_folder="chosen")
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
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            self.export(2, checkpoint_verification="strict")

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
        import errno
        with patch.object(streaming.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "no links")):
            result = self.export()
        self.assertEqual(self.export(checkpoint_verification="strict")["result"][1], 3)
        record = json.loads((Path(result["result"][0]) / "export.json").read_text())
        self.assertEqual(len(record["clips"][0]["files"]), 3)

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
