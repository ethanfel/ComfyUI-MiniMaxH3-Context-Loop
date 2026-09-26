#!/usr/bin/env python3
"""Issue #97: chain-aware refinement and opt-in recovery of existing audio.

CPU only. Real ComfyUI mask types, synthetic sampler, real tiny checkpoints.
"""
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
package, chain, _ = harness.load_package()
refine = importlib.import_module(package.__name__ + ".audio_refine")
nodes = sys.modules[package.__name__ + ".nodes"]
import comfy.nested_tensor
import comfy.sample
import latent_preview

Nested = comfy.nested_tensor.NestedTensor


def fixture():
    video = torch.rand(1, 24, 37, 4, 4)
    audio = torch.rand(1, 32, 2, 207)
    mask = torch.ones(1, 1, 2, 207)
    mask[..., :65] = 0
    context = {"samples": Nested((torch.zeros_like(video), torch.zeros_like(audio))),
               "noise_mask": Nested((torch.ones(1, 1, 37, 4, 4), mask))}
    latent = {"samples": Nested((video, audio)), "batch_index": [3],
              "custom_metadata": "keep", "h3_context_loop_drift_control_av_prefix": 12}
    return latent, context


class RefineTest(unittest.TestCase):
    def call_refine(self, latent, context, model=None):
        return refine.MiniMaxH3ChainAudioRefineSampler().refine(
            model or SimpleNamespace(model_options={}), [], [], latent, context,
            42, 6, 1.0, "euler", "simple", .5)[0]

    def test_protected_audio_and_video_are_exact(self):
        latent, context = fixture()
        video, audio = latent["samples"].unbind()
        old_audio, old_video = audio.clone(), video.clone()
        old_mask = context["noise_mask"].unbind()[1].clone()
        def sample(*args, **kwargs):
            vmask, amask = kwargs["noise_mask"].unbind()
            self.assertFalse(torch.any(vmask))
            self.assertTrue(torch.equal(amask, old_mask))
            self.assertEqual(kwargs["seed"], 42)
            self.assertEqual(kwargs["denoise"], .5)
            # Deliberately violate the mask to test the exact final guard too.
            return Nested((torch.ones_like(video), torch.full_like(audio, 9)))
        with patch.object(comfy.sample, "prepare_noise", return_value=None) as noise, \
             patch.object(comfy.sample, "sample", side_effect=sample), \
             patch.object(latent_preview, "prepare_callback", return_value=None):
            out = self.call_refine(latent, context)
        self.assertEqual(noise.call_args.args[1:], (42, [3]))
        v, a = out["samples"].unbind()
        self.assertTrue(torch.equal(v, old_video))
        self.assertTrue(torch.equal(a[..., :65], old_audio[..., :65]))
        self.assertTrue(torch.all(a[..., 65:] == 9))
        self.assertTrue(torch.equal(audio, old_audio))
        self.assertTrue(torch.equal(context["noise_mask"].unbind()[1], old_mask))
        self.assertEqual(out["custom_metadata"], "keep")
        self.assertNotIn("noise_mask", out)
        self.assertNotIn("h3_context_loop_drift_control_av_prefix", out)

    def test_masks_fresh_feathered_and_fully_locked(self):
        latent, context = fixture()
        # Scene 1 / fresh audio: no carried mask, fully open audio.
        _, fresh = refine.refinement_mask(latent, {"samples": context["samples"]})
        self.assertTrue(torch.all(fresh == 1))
        amask = context["noise_mask"].unbind()[1]
        amask[..., 57:65] = torch.linspace(0, 1, 8)
        _, feather = refine.refinement_mask(latent, context)
        self.assertTrue(torch.equal(feather, amask))
        # The sampled latent may add protection but cannot reopen context locks.
        extra = torch.ones_like(amask)
        extra[..., 100:110] = .25
        latent["noise_mask"] = Nested((torch.ones(1, 1, 37, 4, 4), extra))
        _, composed = refine.refinement_mask(latent, context)
        self.assertTrue(torch.equal(composed, torch.minimum(amask, extra)))
        amask.zero_()
        with patch.object(comfy.sample, "sample", side_effect=AssertionError("locked sampling")), \
             patch.object(comfy.sample, "prepare_noise", side_effect=AssertionError("locked noise")):
            out = self.call_refine(latent, context)
        self.assertIs(out["samples"], latent["samples"])

    def test_invalid_inputs_and_dynamic_patch(self):
        latent, context = fixture()
        for value in (float("nan"), float("inf"), -.1, 1.1):
            with self.subTest(value=value):
                context["noise_mask"].unbind()[1][..., 0] = value
                with self.assertRaisesRegex(ValueError, "finite"):
                    refine.refinement_mask(latent, context)
        latent, context = fixture()
        v, a = context["samples"].unbind()
        context["samples"] = Nested((v, a[..., :-1]))
        with self.assertRaisesRegex(ValueError, "same scene"):
            refine.refinement_mask(latent, context)
        for key in ("denoise_mask_function", "h3_context_loop_drift_control_av_recipe"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "model branch before"):
                self.call_refine(*fixture(), model=SimpleNamespace(model_options={key: {}}))
        with self.assertRaisesRegex(ValueError, "joint AV"):
            refine.refinement_mask({}, {})

    def test_registered_schema(self):
        self.assertIs(package.NODE_CLASS_MAPPINGS["MiniMaxH3ChainAudioRefineSampler"],
                      refine.MiniMaxH3ChainAudioRefineSampler)
        schema = refine.MiniMaxH3ChainAudioRefineSampler.INPUT_TYPES()["required"]
        self.assertIn("context_latent", schema)
        self.assertNotIn("video_denoise", schema)


class AssemblyTest(unittest.TestCase):
    def test_join_modes_and_prelude(self):
        first = torch.ones(1, 2, 243)
        second = torch.full((1, 2, 238), 3.)
        records = [
            dict(segment={"index": 1}, delivered=first, overlap=None,
                 delivered_frames=243, raw_frames=243, repeated_frames=0, mode="drift_control_av"),
            dict(segment={"index": 2}, delivered=second,
                 overlap=torch.cat((torch.full((1, 2, 39), 2.), second), -1),
                 delivered_frames=238, raw_frames=277, repeated_frames=39, mode="drift_control_av"),
        ]
        for mode in chain.MASKED_CONTINUATION_MODES:
            records[1]["mode"] = mode
            with self.subTest(mode=mode):
                default = chain._assemble_generated_audio_records(records, 24)
                explicit = chain._assemble_generated_audio_records(records, 24, "av_overlap")
                delivered = chain._assemble_generated_audio_records(records, 24, "delivered_only")
                self.assertTrue(torch.equal(default["waveform"], explicit["waveform"]))
                self.assertTrue(torch.all(default["waveform"][..., 204:243] == 2))
                self.assertTrue(torch.equal(delivered["waveform"], torch.cat((first, second), -1)))
                self.assertEqual(default["waveform"].shape, delivered["waveform"].shape)
        # Recovery beginning after an external prelude must not reinsert raw audio.
        recovered = chain._assemble_generated_audio_records(records[1:], 24, "delivered_only")
        self.assertNotIn(chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY, recovered)
        with patch.object(chain, "_prelude_audio", return_value={"waveform": first, "sample_rate": 24}):
            joined = chain._audio_with_prelude(recovered, 238, {"frame_count": 243})
        self.assertTrue(torch.equal(joined["waveform"], torch.cat((first, second), -1)))
        with self.assertRaisesRegex(ValueError, "Unknown generated audio join"):
            chain._assemble_generated_audio_records(records, 24, "typo")
        with self.assertRaisesRegex(ValueError, "Unknown generated audio join"):
            chain.MiniMaxH3ChainAssemble().assemble({}, "generated", "test", 256, generated_audio_join="typo")

    def test_real_saved_checkpoints_and_recovery_assembly(self):
        original_output = harness.folder_paths.output_directory
        self.addCleanup(setattr, harness.folder_paths, "output_directory", original_output)
        policy = chain.MiniMaxH3GenerationProfile().build()[0]
        policy = chain.MiniMaxH3AdvancedPolicy().apply(policy, "drift_av")[0]
        plan = chain._normalize_plan(
            json.dumps({"shots": [{"id": "one", "prompt": "A quiet room.", "length": 124},
                                  {"id": "two", "prompt": "The room continues.", "length": 124}]}),
            "audio_refine_test", 64, 64, 39, "video", "head", "disabled",
            "generated_audio", 39, 1., 20, 42, 18, "cpu-test", 0, "drift_control_av", policy)
        rate = 24000
        with tempfile.TemporaryDirectory() as directory:
            harness.folder_paths.output_directory = directory
            segments, delivered = [], []
            for index in (1, 2):
                state = chain._initial_state(plan, index)
                raw = torch.full((1, 2, 124000), .1 * index)
                if index == 2:
                    raw[..., :39000] = .8
                images, sound, *_ = nodes.MiniMaxH3LoopTrim().trim(
                    torch.zeros(124, 64, 64, 3), 0 if index == 1 else 39,
                    {"waveform": raw, "sample_rate": rate}, state=state)
                latent, _ = fixture()
                saved = chain.MiniMaxH3ChainSegmentSave().save(state, images, latent, sound)["result"][0]
                segments.append(saved)
                delivered.append(sound["waveform"])
            selection = json.dumps({"run_name": plan["run_name"], "lineage": [
                {"scene": item["index"], "revision": item["revision"]} for item in segments]})
            manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(selection)[0]
            checkpoints = [Path(chain._absolute_output_path(s["checkpoint"])) for s in segments]
            before = [p.read_bytes() for p in checkpoints]
            normal = chain._generated_audio(manifest)
            recovered = chain._generated_audio(manifest, "delivered_only")
            self.assertTrue(torch.all(normal["waveform"][..., 85000:124000] == .8))
            self.assertTrue(torch.equal(recovered["waveform"], torch.cat(delivered, -1)))
            with patch.object(chain, "_generated_audio", wraps=chain._generated_audio) as assemble_audio, \
                 patch.object(chain, "_publish_final_review_preview"):
                output = chain.MiniMaxH3ChainAssemble().assemble(
                    manifest, "generated", "recovered", 256, generated_audio_join="delivered_only")
            self.assertTrue(Path(output["result"][0]).is_file())
            self.assertEqual(assemble_audio.call_args.kwargs, {"audio_join_mode": "delivered_only"})
            self.assertIn("delivered-only", output["ui"]["text"][0])
            self.assertEqual([p.read_bytes() for p in checkpoints], before)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
