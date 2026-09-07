#!/usr/bin/env python3
"""Deferred ALT selection through real CPU checkpoints, saves and resume."""

import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _upscale_chain_unit_test import load_package, folder_paths, torch, av_latent, audio_for_frames

package, chain, upscale = load_package()
sources = importlib.import_module(package.__name__ + ".deferred_checkpoint_source")
variants = importlib.import_module(package.__name__ + ".checkpoint_variants")
recovery = importlib.import_module(package.__name__ + ".reference_cache_recovery")


class Clip:
    def tokenize(self, prompt, **kwargs):
        return {"prompt": prompt}

    def encode_from_tokens_scheduled(self, tokens):
        return [[torch.zeros(1, 1, 4), {"tokens": tokens}]]


class AlternateUpscaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder_paths.output_directory = self.temp.name
        self.root = Path(self.temp.name)
        self.run = "alternate_upscale_test"
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [
                {"id": "first", "prompt": "The original red folder.", "length": 5, "steps": 2, "seed": "7"},
                {"id": "second", "prompt": "The next scene.", "length": 5, "steps": 2, "seed": "8"}]}),
            self.run, "unit-test", 32, 32, 1, "video", "head", "disabled", "generated_audio",
            1, 5 / 24, 2, 7, 18, 0, "guide")[0]
        self.plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        self.frames = torch.zeros(5, 32, 32, 3)
        self.bases = []
        for index in (1, 2):
            state = chain._initial_state(self.plan, index)
            delivered = self.plan["shots"][index - 1]["delivered_frames"]
            audio = audio_for_frames(delivered)
            audio["waveform"].fill_(0.2)
            self.bases.append(chain.MiniMaxH3ChainSegmentSave().save(
                state, self.frames[:delivered], av_latent(0.1), audio,
                denoised_latent=av_latent(0.3))["result"][0])
        self.selection = {"run_name": self.run, "output_mode": "workflow_local", "lineage": [
            {"scene": index, "revision": item["revision"]} for index, item in enumerate(self.bases, 1)]}
        self.manager = chain.MiniMaxH3ChainCheckpointManager()
        self.manifest = self.manager.passthrough(json.dumps(self.selection))[0]
        self.adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
        self.saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
        self.alt = self.make_alternate()
        self.original_files = {path: path.read_bytes() for path in (
            self.root / item[key] for item in self.bases
            for key in ("checkpoint", "revision_metadata", "metadata", "segment"))}

    def make_alternate(self, scene=1):
        base = self.bases[scene - 1]
        plan = chain._alternate_take_plan(self.plan, {"alternate_draft": {
            "enabled": True, "scene": scene, "scene_id": base["id"], "base_revision": base["revision"],
            "prompt": "The alternate blue folder.", "seed": 91}})
        audio = audio_for_frames(base["delivered_frames"])
        audio["waveform"].fill_(0.8)
        alt = chain.MiniMaxH3ChainSegmentSave().save(
            chain._initial_state(plan, scene), self.frames[:base["delivered_frames"]],
            av_latent(0.7), audio, denoised_latent=av_latent(0.9))["result"][0]
        chain._select_editorial_alternate(plan, alt)
        return alt

    def adapt(self, manifest=None, profile="hq", backend="h3_latent", start=1, recipe="{}", save_latent=False):
        return self.adapter.adapt(manifest or self.manifest, profile, backend, recipe, start, 0, save_latent, 18)

    def test_full_sequence_uses_alt_picture_original_audio_and_frozen_lineage(self):
        before = copy.deepcopy(self.manifest)
        _, state, manifest, status = self.adapt()
        self.assertIn("final-cut ALT pictures: 1/" + self.alt["revision"][:8], status)
        self.assertEqual(manifest["segments"][0]["revision"], self.alt["revision"])
        self.assertEqual(manifest["segments"][1], before["segments"][1])
        self.assertEqual(self.manifest, before)
        current = upscale.MiniMaxH3ChainUpscaleCurrent().current(state)
        self.assertTrue(torch.all(current[2]["samples"] == 0.9))
        self.assertTrue(torch.all(current[3]["samples"] == 0.3))
        self.assertTrue(torch.all(current[13]["waveform"] == 0.2))
        self.assertEqual(current[6], self.alt["prompt"])
        self.assertIn("ALT " + self.alt["revision"][:8], current[-1])
        self.assertEqual(current[9], 91)
        # Resolving twice does not reapply today's editorial to an already pinned source.
        self.assertEqual(upscale._verified_source_manifest(manifest), manifest)
        self.assertEqual(self.original_files, {p: p.read_bytes() for p in self.original_files})

    def test_chapter_and_pixel_reader_select_same_alt(self):
        chapter, _ = chain._chapter_manifest_from_manifest(self.manifest, 1, persist=False)
        self.make_alternate()  # Chapter selection remains frozen to the first ALT.
        _, state, _, _ = self.adapt(chapter, backend="pixel")
        self.assertEqual(state["source_manifest"]["segments"][0]["revision"], self.alt["revision"])

        class VideoVAE:
            def decode(_self, video):
                self.assertTrue(torch.all(video == 0.9))
                return self.frames

        current = upscale.MiniMaxH3ChainUpscalePixelCurrent().current(state, VideoVAE())
        self.assertEqual(current[3], self.alt["prompt"])
        self.assertTrue(torch.all(current[2]["waveform"] == 0.2))
        self.assertIn("ALT ", current[-1])

    def test_alt_conditioning_uses_own_prompt_and_fingerprint(self):
        path = self.root / self.alt["revision_metadata"]
        meta = chain._read_json(str(path))
        meta["compatibility"]["generation_fingerprint"] = "f" * 64
        chain._atomic_json(str(path), meta)
        _, state, _, _ = self.adapt()
        with patch.object(chain, "_find_reference_cache", return_value=None) as lookup, patch.object(
                recovery, "recover_reference_cache", side_effect=recovery.ReferenceRecoveryUnavailable("no references")):
            result = upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(state, Clip())
        self.assertEqual(lookup.call_args.args[0], "f" * 64)
        self.assertEqual(lookup.call_args.args[3], self.alt["prompt"])
        self.assertEqual(result[0][0][1]["tokens"]["prompt"], self.alt["prompt"])

    def test_selective_reader_never_loads_original_video_or_alt_audio(self):
        from safetensors import safe_open
        source = self.adapt()[2]["segments"][0]
        reads = []

        class TrackedFile:
            def __init__(self, path, **kwargs):
                self.path = path
                self.reader = safe_open(path, **kwargs)

            def __enter__(self):
                self.reader.__enter__()
                return self

            def __exit__(self, *args):
                return self.reader.__exit__(*args)

            def keys(self):
                return self.reader.keys()

            def get_tensor(self, key):
                reads.append((self.path, key))
                return self.reader.get_tensor(key)

        with patch("safetensors.safe_open", TrackedFile), patch.object(
                chain, "_st_load", side_effect=AssertionError("must load selectively")):
            upscale._load_source_tensors(source)
            self.assertFalse(any(path.endswith(self.bases[0]["checkpoint"].split("/")[-1]) and "video" in key
                                 or path.endswith(self.alt["checkpoint"].split("/")[-1]) and "audio" in key
                                 for path, key in reads))
            reads.clear()
            audio = upscale._load_source_tensors(source, ("delivered_audio",))
            self.assertEqual(reads, [(str(self.root / self.bases[0]["checkpoint"]), "delivered_audio")])
            self.assertTrue(torch.all(audio["delivered_audio"] == 0.2))

    def test_terminal_alt_video_keeps_denoised_base_audio(self):
        path = self.root / self.alt["checkpoint"]
        tensors = chain._st_load(str(path))
        tensors.pop("denoised_video")
        tensors.pop("denoised_audio")
        chain._st_save(tensors, str(path))
        metadata_path = self.root / self.alt["revision_metadata"]
        metadata = chain._read_json(str(metadata_path))
        metadata["segment"]["checkpoint_sha256"] = chain._file_sha256(str(path))
        chain._atomic_json(str(metadata_path), metadata)
        current = upscale.MiniMaxH3ChainUpscaleCurrent().current(self.adapt()[1])
        self.assertTrue(torch.all(current[2]["samples"] == 0.7))
        self.assertTrue(torch.all(current[3]["samples"] == 0.3))

    def test_save_resume_and_catalogue_track_alt_not_base(self):
        _, state, manifest, _ = self.adapt()
        saved = self.saver.save(state, self.frames)["result"][0]
        self.assertEqual(saved["source_revision"], self.alt["revision"])
        tensors = chain._st_load(str(self.root / saved["checkpoint"]))
        self.assertTrue(torch.all(tensors["delivered_audio"] == 0.2))
        resumed = self.adapt(start=2)[1]
        self.assertEqual(resumed["segments"][0]["revision"], saved["revision"])
        graph = chain.CheckpointGraphManager(self.temp.name).graph(self.run, adopt_legacy=False)
        catalog = variants.saved_checkpoint_variants(self.temp.name, self.run, graph["revisions"])
        record = next(v for v in catalog["variants"] if v["revision"] == saved["revision"])
        self.assertEqual(record["originals"], [{"scene": 1, "revision": self.bases[0]["revision"]}])
        # An ALT baked into HQ must not be swapped back to its low-res media at assembly.
        child = upscale._upscale_manifest(state, [saved], complete=False)
        assembly = upscale._assembly_manifest(child, [saved])
        self.assertEqual(assembly["editorial"]["replacements"], [])
        self.assertEqual(assembly["segments"][0]["segment"], saved["segment"])
        self.make_alternate()
        with self.assertRaisesRegex(ValueError, "different source revision"):
            self.adapt(start=2)
        with self.assertRaisesRegex(ValueError, "branch changed"):
            self.adapter.adapt(self.manifest, "hq", "h3_latent", "{}", 1, 0, False, 18, initial_state=state)
        # The frozen previous source is still a valid explicit selection.
        self.assertEqual(self.adapt(manifest, start=2)[1]["segments"][0]["revision"], saved["revision"])

    def test_previous_original_upscale_not_reused_for_alt(self):
        original = {**self.manifest, "editorial": {**chain._load_run_editorial(self.run), "replacements": []}}
        _, old_state, _, _ = self.adapt(original)
        self.saver.save(old_state, self.frames)
        with self.assertRaisesRegex(ValueError, "different source revision"):
            self.adapt(start=2)
        # Restarting at the affected scene is allowed and retains old outputs.
        self.assertEqual(self.adapt()[1]["segments"], [])

    def test_missing_selected_alt_fails_without_silent_base_fallback(self):
        (self.root / self.alt["revision_metadata"]).unlink()
        with self.assertRaisesRegex(FileNotFoundError, "alternate revision is missing"):
            self.adapt()

    def test_no_alt_and_stale_other_branch_replacements_keep_original(self):
        editorial = chain._load_run_editorial(self.run)
        for replacements in ([], [{**editorial["replacements"][0], "base_revision": "0" * 32}]):
            source = {**self.manifest, "editorial": {**editorial, "replacements": replacements}}
            self.assertEqual(self.adapt(source)[2], source)

    def test_derope_from_alt_is_reused_and_absent_scenes_resolve_alts(self):
        # Both scenes have final-cut ALTs, but only scene 1 has DeRoPE yet.
        alt_two = self.make_alternate(2)
        _, state, _, _ = self.adapt(profile="motion", recipe='{"derope":true}', save_latent=True)
        saved = self.saver.save(state, self.frames, av_latent(0.6))["result"][0]
        meta = chain._read_json(str(self.root / saved["revision_metadata"]))
        choice = {"stage": "derope", "profile_path": str((self.root / saved["metadata"]).parent.parent.relative_to(self.root)),
                  "branch": {"kind": "metadata", "path": saved["revision_metadata"], "lineage": meta["processing_lineage"]}}
        processed = self.manager.passthrough(json.dumps({**self.selection, "processing_source": choice}))[0]
        self.assertEqual(processed["segments"][0]["revision"], saved["revision"])
        self.assertEqual(processed["segments"][1]["revision"], alt_two["revision"])
        _, after, _, _ = self.adapt(processed, profile="after-motion")
        current = upscale.MiniMaxH3ChainUpscaleCurrent().current(after)
        self.assertTrue(torch.all(current[2]["samples"] == 0.6))
        self.assertTrue(torch.all(current[3]["samples"] == 0.6))
        self.assertTrue(torch.all(current[13]["waveform"] == 0.2))
        self.make_alternate()
        with self.assertRaisesRegex(ValueError, "different original take or final-cut ALT"):
            sources.derope_source_manifest(self.manifest, choice, chain, upscale)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
