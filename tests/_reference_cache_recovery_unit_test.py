#!/usr/bin/env python3
"""CPU recovery regression; all media/cache writes are temporary fixtures."""

import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from _reference_cache_fingerprint_unit_test import Clip, VideoVAE
from _upscale_chain_unit_test import load_package, folder_paths, torch, audio_for_frames

package, chain, upscale = load_package()
recovery = importlib.import_module(package.__name__ + ".reference_cache_recovery")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder_paths.output_directory = str(self.root)
        self.input_patch = patch.object(chain, "_input_root", return_value=str(self.root / "input"))
        self.input_patch.start()
        self.addCleanup(self.input_patch.stop)
        self.run = self.root / "h3_chains/demo"
        self.assets = []
        self.picture = self.image("subject", "picture", (30, 60, 90))
        self.anchor = self.image("setting", "semantic_anchor", (90, 30, 60))
        self.entries = [{"kind": "picture", "tag": "subject", "activation": "prompt",
                         "scenes": "all", "content_hash": self.picture["sha256"]},
                        {"kind": "semantic_anchor", "tag": "setting", "activation": "prompt",
                         "content_hash": self.anchor["sha256"],
                         "semantic_anchor_mode": "picture_storyboard", "semantic_anchor_size": "512"},
                        {"kind": "picture", "tag": "unused", "activation": "prompt",
                         "scenes": "all", "content_hash": "e" * 64}]
        self.source = {"index": 1, "revision": "a" * 32, "checkpoint_sha256": "b" * 64,
                       "raw_frames": 5, "resolution": {"width": 32, "height": 32},
                       "prompt": "@subject stands by #setting and #setting[0.1s]."}
        self.state = {"index": 1, "source_manifest": {
            "run_name": "demo", "source_scene_count": 13,
            "compatibility": {"width": 32, "height": 32}, "segments": [self.source]}}
        self.set_lineage()

    def image(self, tag, role, color):
        base = self.run / "project_assets/images"
        base.mkdir(parents=True, exist_ok=True)
        temporary = base / (tag + ".png")
        Image.new("RGB", (48, 64), color).save(temporary)
        digest = chain._file_sha256(str(temporary))
        path = base / (digest[:16] + "_" + tag + ".png")
        temporary.rename(path)
        asset = {"tag": tag, "kind": "image", "role": role, "sha256": digest,
                 "relative_path": "images/" + path.name}
        self.assets.append(asset)
        chain._atomic_json(str(base.parent / "catalog.json"), {"assets": self.assets})
        return asset

    def set_lineage(self, ordered=False):
        registry = (chain._make_reference_schedule if ordered else chain._make_tagged_references)(self.entries)
        lineage = chain._reference_fingerprint_lineage(registry)
        self.source["scene_dependency"] = {"generation_fingerprint_lineage": lineage}
        self.state["source_manifest"]["compatibility"]["generation_fingerprint"] = lineage["current"]

    def condition(self, **kwargs):
        return upscale.MiniMaxH3ChainUpscalePixelConditioning().condition(
            self.state, Clip(), torch.zeros(5, 64, 64, 3), VideoVAE(),
            missing_cache="error", **kwargs)

    def test_missing_cache_rebuilt_and_reused_without_regeneration(self):
        before = copy.deepcopy(self.state)
        result = self.condition()
        self.assertTrue(result[5])
        self.assertIn("rebuilt references from verified saved media", result[-1])
        self.assertIn("legacy presentation defaults: ref_image_size=match", result[-1])
        self.assertNotIn("@subject", result[4])
        self.assertNotIn("#setting", result[4])
        self.assertEqual(len(result[0][0][1]["minimax_refs"]), 1)
        self.assertEqual(result[0][0][1]["minimax_refs"][0]["latent_h"], 4)
        self.assertEqual(len(result[0][0][1]["tokens"]["presentation"]), 2)
        self.assertEqual(self.state, before)
        self.assertTrue(list((self.run / "reference_cache/objects").glob("*.safetensors")))
        with patch.object(chain, "_cache_reference_scene", side_effect=AssertionError("must reuse")):
            self.assertIn("reused references rebuilt", self.condition()[-1])

    def test_old_media_not_current_tag_assignment_or_catalog_required(self):
        self.image("subject", "picture", (200, 200, 200))
        (self.run / "project_assets/catalog.json").unlink()
        result = self.condition()
        image = result[0][0][1]["tokens"]["presentation"][0]["data"]
        self.assertAlmostEqual(float(image[0, 0, 0, 0]), 30 / 255, places=4)

    def test_missing_native_latent_objects_are_recreated(self):
        self.condition()
        pointer = json.loads(next((self.run / "reference_cache").glob("rebuilt_*.json")).read_text())
        descriptor = pointer["reference_cache"]
        cached = chain._read_json(str(self.root / descriptor["metadata"]))
        # Pin the damaged cache exactly, matching old Segment Save metadata.
        self.source["reference_cache"] = descriptor
        path = self.root / cached["tensor_objects"]["block_000_latent"]["tensors"]
        path.unlink()
        self.assertIn("rebuilt references from verified saved media", self.condition()[-1])
        self.assertTrue(path.is_file())

    def test_missing_metadata_rebuilds_but_corruption_is_not_ignored(self):
        self.condition()
        pointer = json.loads(next((self.run / "reference_cache").glob("rebuilt_*.json")).read_text())
        self.source["reference_cache"] = pointer["reference_cache"]
        metadata_path = self.root / pointer["reference_cache"]["metadata"]
        cached = chain._read_json(str(metadata_path))
        metadata_path.unlink()
        self.assertIn("rebuilt references", self.condition()[-1])
        tensor = self.root / cached["tensor_objects"]["block_000_latent"]["tensors"]
        tensor.write_bytes(b"corrupt existing tensor")
        with self.assertRaisesRegex(ValueError, "integrity|SHA-256"):
            self.condition()

    def test_absent_source_and_vae_report_specific_recovery_requirement(self):
        path = self.run / "project_assets" / self.picture["relative_path"]
        path.write_bytes(b"not the saved image")
        with self.assertRaisesRegex(FileNotFoundError, "Archived image @subject.*missing or changed"):
            self.condition()
        self.assertFalse((self.run / "reference_cache").exists())

    def test_latent_conditioner_requires_vae_only_when_recovery_needs_it(self):
        with self.assertRaisesRegex(FileNotFoundError, "video VAE"):
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                self.state, Clip(), missing_cache="error")

    def test_immutable_source_lineage_wins_over_current_manifest(self):
        path = self.run / "checkpoints" / ("clip_0001." + self.source["revision"] + ".json")
        self.source["revision_metadata"] = str(path.relative_to(self.root))
        chain._atomic_json(str(path), {"segment": copy.deepcopy(self.source),
                                      "scene_dependency": self.source["scene_dependency"]})
        self.source.pop("scene_dependency")
        self.state["source_manifest"]["compatibility"]["generation_fingerprint_lineage"] = {"wrong": True}
        self.assertTrue(self.condition()[5])
        with patch.object(chain, "_cache_reference_scene", side_effect=AssertionError("must reuse")):
            self.state["source_manifest"]["segments"] = [{
                **self.source, "width": 128, "height": 128,
                "processing_source": {"original": copy.deepcopy(self.source)}}]
            self.assertIn("reused references rebuilt", self.condition()[-1])

    def test_ordered_schedule_only_rebuilds_active_scene_references(self):
        self.entries = [{**self.entries[0], "activation": "schedule", "scenes": "1"},
                        {**self.entries[2], "activation": "schedule", "scenes": "2"}]
        self.source["prompt"] = "@subject waits."
        self.set_lineage(ordered=True)
        self.assertTrue(self.condition()[5])

    def test_loader_backed_picture_matches_saved_decoded_hash(self):
        path = self.run / "project_assets" / self.picture["relative_path"]
        value = chain._project_asset_image(chain._project_asset_descriptor(self.picture, str(path)))
        self.entries[0]["content_hash"] = chain._tensor_fingerprint(value)
        self.set_lineage()
        self.assertTrue(self.condition()[5])

    def test_archived_video_reference_rebuilds_native_bank(self):
        path = self.run / "project_assets/videos/action.mp4"
        path.parent.mkdir(parents=True)
        chain._write_segment_video(torch.zeros(5, 32, 32, 3), str(path), chain.FPS, 18)
        digest = chain._file_sha256(str(path))
        self.assets.append({"kind": "video", "sha256": digest, "relative_path": "videos/action.mp4"})
        chain._atomic_json(str(self.run / "project_assets/catalog.json"), {"assets": self.assets})
        self.entries = [{"kind": "video", "tag": "action", "activation": "prompt", "content_hash": digest}]
        self.source["prompt"] = "Follow @action."
        self.set_lineage()
        result = self.condition(motion_ref_mode="resize_video")
        self.assertEqual(result[0][0][1]["minimax_refs"][0]["kind"], "video")

    def test_path_escape_and_wrong_saved_take_are_rejected(self):
        self.assets[0]["relative_path"] = "../outside.png"
        chain._atomic_json(str(self.run / "project_assets/catalog.json"), {"assets": self.assets})
        with self.assertRaisesRegex(FileNotFoundError, "escapes"):
            self.condition()
        path = self.run / "checkpoints/wrong.json"
        self.source["revision_metadata"] = str(path.relative_to(self.root))
        chain._atomic_json(str(path), {"segment": {**self.source, "revision": "d" * 32}})
        with self.assertRaisesRegex(FileNotFoundError, "different take"):
            self.condition()

    def test_immutable_recipe_restores_max_policy_and_anchor_size(self):
        path = self.run / "recovery_archives" / self.source["revision"] / "api_prompt.json"
        self.source["archives"] = {"api_prompt": str(path.relative_to(self.root))}
        chain._atomic_json(str(path), {"1": {"class_type": "MiniMaxH3TaggedReferenceToVideo", "inputs": {
            "ref_image_size": "max", "semantic_anchor_size": "1024", "semantic_anchor_mode": "timestamped_video"}}})
        self.entries[1].update(semantic_anchor_mode="inherit", semantic_anchor_size="inherit")
        self.set_lineage()
        result = self.condition()
        self.assertIn("max pictures keep cached geometry", result[-1])
        self.assertNotIn("legacy presentation defaults", result[-1])

    def test_audio_reference_recovers_with_audio_vae(self):
        path = self.run / "project_assets/audio/voice.wav"
        chain._atomic_wav(audio_for_frames(5), str(path))
        digest = chain._file_sha256(str(path))
        self.assets.append({"kind": "audio", "sha256": digest, "relative_path": "audio/voice.wav"})
        chain._atomic_json(str(self.run / "project_assets/catalog.json"), {"assets": self.assets})
        self.entries = [{"kind": "audio", "tag": "voice", "activation": "prompt", "content_hash": digest}]
        self.source["prompt"] = "@voice speaks."
        self.set_lineage()
        with self.assertRaisesRegex(FileNotFoundError, "audio VAE"):
            self.condition()

        class AudioVAE:
            audio_sample_rate = 32000

            def encode(self, waveform):
                return torch.zeros(1, 32, 2, 9)

        result = self.condition(audio_vae=AudioVAE())
        self.assertEqual(result[0][0][1]["minimax_refs"][0]["kind"], "audio")


if __name__ == "__main__":
    unittest.main(argv=[__file__])
