#!/usr/bin/env python3
"""Issue #99: explicit voice-over timing, saved PCM, assembly and re-decode.

CPU only: synthetic audio markers, tiny real checkpoints/media, no model or
live project. Uses the same ComfyUI loader as the deferred-upscale tests.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import _upscale_chain_unit_test as harness

torch = harness.torch
package, chain, upscale = harness.load_package()
nodes = sys.modules[package.__name__ + ".nodes"]
MODE = "fresh_narration_keep_start"


def plan():
    policy, _ = chain.MiniMaxH3GenerationProfile().build(
        "Visual continuity", "Generate fresh audio per scene")
    return chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "A narrator speaks.", "length": 124},
            {"id": "two", "prompt": "The narrator continues.", "length": 124},
        ]}), "narration_test", 64, 64, 22, "video", "head", "disabled",
        "generated_audio", 22, 1.0, 8, 7, 18, "cpu-fixture", 0, "guide", policy)


def audio(rate=24000, channels=1):
    waveform = torch.zeros((1, channels, round(124 / 24 * rate)))
    waveform[..., round(.2 * rate):round(.8 * rate)] = .5
    waveform[..., round(1.2 * rate):round(1.3 * rate)] = .25
    # Deliberate closing marker: keep-start sacrifices this, not the opening.
    waveform[..., round(4.8 * rate):round(5.0 * rate)] = .75
    return {"waveform": waveform, "sample_rate": rate}


class NarrationTrimTest(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.state = {"plan": self.plan, "index": 2}
        self.images = torch.zeros((124, 64, 64, 3))
        self.trim = nodes.MiniMaxH3LoopTrim()

    def test_default_and_explicit_narration(self):
        schema = self.trim.INPUT_TYPES()["optional"]["audio_trim_mode"]
        self.assertEqual(schema[1]["default"], "sync_with_video")
        for rate, channels in ((24000, 1), (44100, 2)):
            with self.subTest(rate=rate, channels=channels):
                source = audio(rate, channels)
                untouched = source["waveform"].clone()
                default = self.trim.trim(self.images, 22, source, state=self.state)
                explicit = self.trim.trim(
                    self.images, 22, source, state=self.state,
                    audio_trim_mode="sync_with_video")
                kept = self.trim.trim(
                    self.images, 22, source, state=self.state, audio_trim_mode=MODE)
                self.assertTrue(torch.equal(default[0], self.images[22:]))
                self.assertTrue(torch.equal(default[0], kept[0]))
                self.assertTrue(torch.equal(default[1]["waveform"], explicit[1]["waveform"]))
                self.assertEqual(set(default[1]), set(explicit[1]))
                self.assertNotIn(chain.AUDIO_TRIM_MODE_KEY, default[1])
                self.assertFalse(torch.any(default[1]["waveform"] == .5))
                self.assertTrue(torch.any(default[1]["waveform"] == .75))
                self.assertTrue(torch.any(kept[1]["waveform"] == .5))
                self.assertFalse(torch.any(kept[1]["waveform"] == .75))
                self.assertTrue(torch.equal(kept[1]["waveform"], untouched[..., :round(102 / 24 * rate)]))
                self.assertNotIn(chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY, kept[1])
                self.assertEqual(kept[1][chain.AUDIO_TRIM_MODE_KEY], MODE)
                chain._validate_audio(kept[1], "narration", expected_frames=102)
                self.assertTrue(torch.equal(source["waveform"], untouched))

    def test_first_scene_and_grid_rounding(self):
        first = {"plan": self.plan, "index": 1}
        source = audio()
        kept = self.trim.trim(self.images, 0, source, state=first, audio_trim_mode=MODE)
        self.assertTrue(torch.equal(kept[1]["waveform"], source["waveform"]))
        # A decoded 40 Hz grid can be about 8 ms longer than video.
        rounded = {**source, "waveform": torch.cat((source["waveform"], torch.zeros(1, 1, 200)), -1)}
        kept = self.trim.trim(self.images, 22, rounded, state=self.state, audio_trim_mode=MODE)
        chain._validate_audio(kept[1], "rounded narration", expected_frames=102)
        self.assertTrue(torch.allclose(kept[1]["waveform"][..., 8000:16000],
                                       torch.full((1, 1, 8000), .5), atol=1e-3))

    def test_requires_explicit_safe_policy(self):
        for options in ({"state": None}, {"audio": None}, {"match_tail": False}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "requires Current Shot"):
                self.trim.trim(self.images, 22, **{
                    "audio": audio(), "state": self.state, "audio_trim_mode": MODE, **options})
        with self.assertRaisesRegex(ValueError, "Unknown H3 audio trim mode"):
            self.trim.trim(self.images, 22, audio(), audio_trim_mode="typo")
        for key, value in (("generated_continuity", "on"), ("source_reference", "on"),
                           ("source_audio_target", "locked")):
            state = copy.deepcopy(self.state)
            state["plan"]["shots"][1][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "fresh audio per scene"):
                self.trim.trim(self.images, 22, audio(), state=state, audio_trim_mode=MODE)
        for final in ("source", "none"):
            state = copy.deepcopy(self.state)
            state["plan"]["compatibility"]["audio_policy"]["final_audio"] = final
            with self.subTest(final=final), self.assertRaisesRegex(ValueError, "fresh audio per scene"):
                self.trim.trim(self.images, 22, audio(), state=state, audio_trim_mode=MODE)
        state = copy.deepcopy(self.state)
        state["plan"]["compatibility"]["audio_policy"]["generated_continuity"] = "on"
        state["plan"]["shots"][1]["generated_continuity"] = "off"
        self.trim.trim(self.images, 22, audio(), state=state, audio_trim_mode=MODE)

    def test_save_assembly_redecode_and_deferred_save(self):
        original_output = harness.folder_paths.output_directory
        self.addCleanup(setattr, harness.folder_paths, "output_directory", original_output)
        with tempfile.TemporaryDirectory() as directory:
            harness.folder_paths.output_directory = directory
            sources = []
            for index, mode in ((1, "guide"), (2, "masked_av")):
                state = chain._initial_state(self.plan, index)
                state["plan"]["shots"][index - 1]["continuation_mode"] = mode
                raw = audio()
                trim_frames = 0 if index == 1 else 22
                images, sound, *_ = self.trim.trim(
                    self.images, trim_frames, raw, state=state, audio_trim_mode=MODE)
                latent = {"samples": [torch.zeros(1, 24, 37, 4, 4), torch.zeros(1, 32, 2, 207)]}
                saved = chain.MiniMaxH3ChainSegmentSave().save(
                    state, images, latent, sound)["result"][0]
                self.assertEqual(saved["audio_trim_mode"], MODE)
                self.assertEqual(chain._public_segment(saved)["audio_trim_mode"], MODE)
                with harness.safe_open(chain._absolute_output_path(saved["checkpoint"]), framework="pt") as checkpoint:
                    self.assertEqual(checkpoint.metadata()["audio_trim_mode"], MODE)
                    self.assertNotIn("audio_with_overlap", checkpoint.keys())
                    self.assertTrue(torch.equal(checkpoint.get_tensor("delivered_audio"), sound["waveform"]))
                metadata = json.loads(Path(chain._absolute_output_path(saved["revision_metadata"])).read_text())
                self.assertEqual(metadata["segment"]["audio_trim_mode"], MODE)
                record = chain._png_export_audio_record(
                    saved, {"audio": torch.zeros(1)}, SimpleNamespace(decode=lambda _: raw["waveform"]), 24000, mode)
                self.assertTrue(torch.equal(record["delivered"], sound["waveform"]))
                self.assertIsNone(record["overlap"])
                sources.append(saved)

            # Invalid/private metadata cannot bypass Trim's policy checks or
            # reintroduce an overlap soundtrack into a keep-start checkpoint.
            for extra, message in (({chain.AUDIO_TRIM_MODE_KEY: "typo"}, "Unknown saved"),
                                   ({chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY: raw["waveform"]}, "cannot also carry")):
                with self.assertRaisesRegex(ValueError, message):
                    chain.MiniMaxH3ChainSegmentSave().save(
                        state, images, latent, {**sound, **extra})
            unsafe = copy.deepcopy(state)
            unsafe["plan"]["shots"][1]["generated_continuity"] = "on"
            with self.assertRaisesRegex(ValueError, "fresh audio per scene"):
                chain.MiniMaxH3ChainSegmentSave().save(unsafe, images, latent, sound)

            joined = chain._generated_audio({"segments": sources})
            self.assertEqual(joined["waveform"].shape[-1], 226000)
            self.assertTrue(torch.any(joined["waveform"][..., 124000:146000] == .5))

            # An explicitly marked take must not overwrite the prior scene even
            # if a legacy/external record still supplies its raw overlap.
            records = [chain._png_export_audio_record(
                saved, {"audio": torch.zeros(1)}, SimpleNamespace(decode=lambda _: audio()["waveform"]),
                24000, "masked_av") for saved in sources]
            records[1]["overlap"] = torch.ones(1, 1, 124000)
            guarded = chain._assemble_generated_audio_records(records, 24000)
            self.assertTrue(torch.equal(guarded["waveform"], joined["waveform"]))
            prelude_case = chain._assemble_generated_audio_records(records[1:], 24000)
            self.assertNotIn(chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY, prelude_case)

            selection = json.dumps({"run_name": self.plan["run_name"], "lineage": [
                {"scene": item["index"], "revision": item["revision"]} for item in sources]})
            manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(selection)[0]
            self.assertEqual(manifest["segments"][1]["audio_trim_mode"], MODE)
            for recovered in (None, audio()):
                profile = "copied" if recovered is None else "recovered"
                _, state, _, _ = upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
                    manifest, profile, "pixel", "{}", 2, 0, False, 18,
                    start_mode="fresh_range")
                saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
                    state, self.images, recovered_audio=recovered)["result"][0]
                self.assertEqual(saved["audio_trim_mode"], MODE)
                with harness.safe_open(chain._absolute_output_path(saved["checkpoint"]), framework="pt") as checkpoint:
                    self.assertEqual(checkpoint.metadata()["audio_trim_mode"], MODE)
                    self.assertTrue(torch.equal(checkpoint.get_tensor("delivered_audio"), audio()["waveform"][..., :102000]))

    def test_picture_only_alternate_keeps_original_audio_timing(self):
        sources = importlib.import_module(package.__name__ + ".deferred_checkpoint_source")
        for base_mode, picture_mode in ((MODE, None), (None, MODE)):
            base = {"index": 1, "revision": "base", "sample_rate": 24000}
            picture = {**base, "revision": "alternate", "presentation_media_mode": "picture_only"}
            if base_mode:
                base["audio_trim_mode"] = base_mode
            if picture_mode:
                picture["audio_trim_mode"] = picture_mode
            manifest = {"run_name": "fixture", "segments": [base]}
            with patch.object(chain, "_manifest_editorial", return_value={}), \
                 patch.object(chain, "_editorial_presentation_segments", return_value=[picture]), \
                 patch.object(chain, "_load_checkpoint_revision", return_value=({}, "unused")):
                resolved = sources.editorial_source_manifest(manifest, chain)["segments"][0]
            self.assertEqual(resolved.get("audio_trim_mode"), base_mode)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
