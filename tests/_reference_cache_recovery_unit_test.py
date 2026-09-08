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

    def image(self, tag, role, color, size=(48, 64)):
        base = self.run / "project_assets/images"
        base.mkdir(parents=True, exist_ok=True)
        temporary = base / (tag + ".png")
        Image.new("RGB", size, color).save(temporary)
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

    def recipe(self, prompt):
        path = self.run / "recovery_archives" / self.source["revision"] / "api_prompt.json"
        self.source["archives"] = {"api_prompt": str(path.relative_to(self.root))}
        chain._atomic_json(str(path), prompt)

    def large_picture(self):
        self.picture = self.image("subject", "picture", (30, 60, 90), size=(192, 128))
        self.entries[0]["content_hash"] = self.picture["sha256"]
        self.set_lineage()

    def pin_rebuilt_cache(self):
        pointer = json.loads(next((self.run / "reference_cache").glob("rebuilt_*.json")).read_text())
        self.source["reference_cache"] = pointer["reference_cache"]
        return chain._load_reference_cache_descriptor(pointer["reference_cache"])

    def test_linked_and_modern_saved_max_rebuilt_with_native_geometry(self):
        self.large_picture()
        for kind in ("MiniMaxH3TaggedReferenceToVideo", "MiniMaxH3ScheduledReferenceToVideo",
                     "MiniMaxH3CurrentTaggedReferenceScene"):
            with self.subTest(kind=kind):
                self.recipe({
                    "1": {"class_type": kind, "inputs": (
                        {"options": ["options", 0]} if kind.endswith("ReferenceScene")
                        else {"ref_image_size": ["sizing", 0]})},
                    "options": {"class_type": "MiniMaxH3TaggedSceneOptions",
                                "inputs": {"ref_image_size": ["sizing", 0]}},
                    "sizing": {"class_type": "Reroute", "inputs": {"value": ["max", 0]}},
                    "max": {"class_type": "PrimitiveString", "inputs": {"value": "max"}},
                })
                result = self.condition()
                self.assertIn("policy=max", result[-1])
                self.assertNotIn("ref_image_size=match", result[-1])
                self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (8, 12))

    def test_recipe_reader_does_not_guess_unknown_or_ambiguous_links(self):
        defaults = {"ref_image_size": "match", "semantic_anchor_size": "512",
                    "semantic_anchor_mode": "timestamped_video"}
        for value, producer in (
            (["2", 0], {"class_type": "ExecuteAnything", "inputs": {"value": "max"}}),
            (["2", 1], {"class_type": "PrimitiveString", "inputs": {"value": "max"}}),
            (["2", 0], {"class_type": "PrimitiveString", "inputs": {"value": ["2", 0]}}),
            (["missing", 0], {}),
        ):
            with self.subTest(value=value, producer=producer):
                prompt = {
                    "1": {"class_type": "MiniMaxH3TaggedReferenceToVideo", "inputs": {"ref_image_size": value}},
                    "2": producer,
                    "3": {"class_type": "MiniMaxH3TaggedReferenceToVideo", "inputs": {"ref_image_size": "max"}},
                }
                self.assertNotIn("ref_image_size", recovery._recipe_settings(prompt, defaults))
        self.assertEqual(recovery._recipe_settings({
            "1": {"class_type": "MiniMaxH3CurrentTaggedReferenceScene", "inputs": {}},
            "unused": {"class_type": "MiniMaxH3TaggedSceneOptions", "inputs": {"ref_image_size": "max"}},
        }, defaults), defaults)

    def test_linked_options_restore_anchor_settings_too(self):
        self.entries[1].update(semantic_anchor_size="inherit", semantic_anchor_mode="inherit")
        self.set_lineage()
        self.recipe({
            "1": {"class_type": "MiniMaxH3CurrentTaggedReferenceScene", "inputs": {"options": ["2", 0]}},
            "2": {"class_type": "MiniMaxH3TaggedSceneOptions", "inputs": {
                "ref_image_size": "max", "semantic_anchor_size": ["3", 0],
                "semantic_anchor_mode": "picture_storyboard"}},
            "3": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "1024"}},
        })
        settings, defaults = recovery.saved_reference_settings(chain, self.source, self.state["source_manifest"])
        self.assertEqual(settings, {"ref_image_size": "max", "semantic_anchor_size": "1024",
                                    "semantic_anchor_mode": "picture_storyboard"})
        self.assertEqual(defaults, [])

    def test_fixed_recipe_does_not_reuse_old_default_match_rebuild(self):
        self.large_picture()
        self.condition()
        self.recipe({"1": {"class_type": "MiniMaxH3CurrentTaggedReferenceScene",
                            "inputs": {"options": ["2", 0]}},
                     "2": {"class_type": "MiniMaxH3TaggedSceneOptions", "inputs": {"ref_image_size": "max"}}})
        result = self.condition()
        self.assertIn("policy=max", result[-1])
        self.assertIn("rebuilt references from verified saved media", result[-1])
        self.assertEqual(len(list((self.run / "reference_cache").glob("rebuilt_*.json"))), 2)

    def test_explicit_max_reencodes_existing_match_cache_without_mutating_it(self):
        self.large_picture()
        match = self.condition()
        cached = self.pin_rebuilt_cache()
        before = copy.deepcopy(self.source)
        files = {str(path): chain._file_sha256(str(path)) for path in (self.run / "reference_cache").rglob("*") if path.is_file()}
        with patch.object(chain, "_cache_reference_scene", side_effect=AssertionError("use cached masters")):
            result = self.condition(override_ref_image_size="max")
        refs = result[0][0][1]
        self.assertEqual(tuple(refs["minimax_refs"][0]["latent"].shape[-2:]), (8, 12))
        self.assertEqual(refs["_h3_upscale_ref_image_size"], "max")
        self.assertFalse(refs["_h3_upscale_picture_refs_target_sized"])
        self.assertIn("rebuilt 1 max pictures", result[-1])
        self.assertNotIn("max pictures keep cached geometry", result[-1])
        torch.testing.assert_close(refs["tokens"]["presentation"][1]["data"],
                                   match[0][0][1]["tokens"]["presentation"][1]["data"])
        self.assertEqual(self.source, before)
        self.assertEqual(cached["ref_image_size"], "match")
        self.assertEqual(files, {name: chain._file_sha256(name) for name in files})
        self.assertIn("policy=match", self.condition()[-1])

    def test_explicit_max_on_recovery_and_match_on_max_cache(self):
        self.large_picture()
        result = self.condition(override_ref_image_size="max")
        self.assertIn("policy=max", result[-1])
        self.assertNotIn("ref_image_size=match", result[-1])
        cached = self.pin_rebuilt_cache()
        self.assertEqual(cached["ref_image_size"], "max")
        with patch.object(VideoVAE, "encode", side_effect=AssertionError("max retains native geometry")):
            self.assertIn("policy=max", self.condition(override_ref_image_size="max")[-1])
        result = self.condition(override_ref_image_size="match")
        self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (4, 4))
        self.assertTrue(result[0][0][1]["_h3_upscale_picture_refs_target_sized"])
        self.assertEqual(cached["ref_image_size"], "max")

    def test_max_override_without_target_canvas_and_missing_vae(self):
        self.large_picture()
        self.condition()
        self.pin_rebuilt_cache()
        conditioner = upscale.MiniMaxH3ChainUpscaleReferenceConditioning()
        with self.assertRaisesRegex(ValueError, "video VAE"):
            conditioner.condition(self.state, Clip(), "error", override_ref_image_size="max")
        result = conditioner.condition(self.state, Clip(), "error", video_vae=VideoVAE(), override_ref_image_size="max")
        self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (8, 12))
        self.assertFalse(result[0][0][1]["_h3_upscale_picture_refs_target_sized"])

    def test_live_override_inherit_uses_immutable_policy_if_cache_missing(self):
        self.recipe({"1": {"class_type": "MiniMaxH3TaggedReferenceToVideo", "inputs": {"ref_image_size": "max"}}})
        with patch.object(upscale, "_conditioning_from_tagged_upscale_override", return_value=([], "", "")) as build:
            self.condition(tagged_references={})
        self.assertEqual(build.call_args.args[6], "max")

    def test_invalid_override_is_not_ignored_on_automatic_path(self):
        with self.assertRaisesRegex(ValueError, "inherit, match, or max"):
            self.condition(override_ref_image_size="invalid")

    def test_anchor_override_schema_matches_scene_options_without_moving_pixel_widgets(self):
        for node in (upscale.MiniMaxH3ChainUpscaleReferenceConditioning,
                     upscale.MiniMaxH3ChainUpscalePixelConditioning):
            optional = node.INPUT_TYPES()["optional"]
            for key, choices in (("semantic_anchor_size", chain.SEMANTIC_ANCHOR_SIZES),
                                 ("semantic_anchor_mode", chain.SEMANTIC_ANCHOR_MODES)):
                field = optional["override_" + key]
                self.assertEqual(field[0], ["inherit", *choices])
                self.assertEqual(field[1]["default"], "inherit")
        self.assertEqual(list(optional)[-4:], ["conditioning_width", "conditioning_height",
                         "override_semantic_anchor_size", "override_semantic_anchor_mode"])
        for key in ("override_semantic_anchor_size", "override_semantic_anchor_mode"):
            for refs in (None, {}):
                with self.subTest(key=key, live=refs is not None):
                    with self.assertRaisesRegex(ValueError, key.removeprefix("override_")):
                        self.condition(tagged_references=refs, **{key: "invalid"})

    def test_explicit_anchor_settings_replace_legacy_defaults_during_recovery(self):
        self.entries[1].update(semantic_anchor_size="inherit", semantic_anchor_mode="inherit")
        self.set_lineage()
        result = self.condition(override_ref_image_size="max",
                                override_semantic_anchor_size="1280",
                                override_semantic_anchor_mode="timestamped_video")
        cached = self.pin_rebuilt_cache()
        self.assertEqual(cached["presentation_contract"]["semantic_anchor_size"], "1280")
        self.assertEqual(cached["presentation_contract"]["semantic_anchor_mode"], "timestamped_video")
        self.assertNotIn("legacy presentation defaults", result[-1])
        self.assertIn("explicit anchor overrides: semantic_anchor_size=1280", result[-1])
        self.assertIn("<Video 1>", result[4])
        items = result[0][0][1]["tokens"]["presentation"]
        expected = chain._h3_semantic_anchor_image(torch.zeros(1, 64, 48, 3), "1280")
        self.assertEqual([item["type"] for item in items], ["image", "image", "video"])
        self.assertEqual(tuple(items[-1]["data"].shape[1:3]), tuple(expected.shape[1:3]))

    def test_anchor_override_rebuilds_pinned_cache_without_mutation_and_reuses_it(self):
        self.large_picture()
        self.condition(override_ref_image_size="max")
        cached = self.pin_rebuilt_cache()
        before = copy.deepcopy(self.source)
        files = {str(path): chain._file_sha256(str(path)) for path in self.run.rglob("*") if path.is_file()}
        options = {"override_semantic_anchor_size": "1280",
                   "override_semantic_anchor_mode": "timestamped_video"}
        result = self.condition(**options)
        self.assertIn("policy=max", result[-1], "an anchor override must not reset native picture sizing")
        self.assertIn("<Video 1>", result[4])
        self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (8, 12))
        self.assertEqual(self.source, before)
        self.assertEqual(cached["presentation_contract"]["semantic_anchor_size"], "512")
        self.assertEqual(files, {name: chain._file_sha256(name) for name in files})
        self.assertEqual(len(list((self.run / "reference_cache").glob("rebuilt_*.json"))), 2)
        with patch.object(chain, "_cache_reference_scene", side_effect=AssertionError("reuse derived cache")), \
                patch.object(VideoVAE, "encode", side_effect=AssertionError("no duplicate VAE encode")):
            self.assertIn("reused references rebuilt", self.condition(**options)[-1])
        self.assertNotIn("<Video 1>", self.condition()[4], "inherit still uses the original pinned presentation")

    def test_mode_only_override_preserves_cached_anchor_size(self):
        self.condition(override_semantic_anchor_size="768")
        self.pin_rebuilt_cache()
        result = self.condition(override_semantic_anchor_mode="timestamped_video")
        self.assertIn("<Video 1>", result[4])
        pointers = [json.loads(path.read_text()) for path in (self.run / "reference_cache").glob("rebuilt_*.json")]
        settings = [item["identity"]["settings"] for item in pointers]
        self.assertTrue(any(item["semantic_anchor_size"] == "768" and
                            item["semantic_anchor_mode"] == "timestamped_video" for item in settings))

    def test_matching_anchor_override_does_not_require_original_media(self):
        self.condition(override_ref_image_size="max")
        self.pin_rebuilt_cache()
        (self.run / "project_assets" / self.anchor["relative_path"]).unlink()
        with patch.object(recovery, "recover_reference_cache", side_effect=AssertionError("cache already matches")):
            self.condition(override_semantic_anchor_size="512", override_semantic_anchor_mode="picture_storyboard")
        with self.assertRaisesRegex(ValueError, "Cannot apply semantic-anchor overrides.*media"):
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                self.state, Clip(), "text_only", video_vae=VideoVAE(), override_semantic_anchor_size="1280")

    def test_connected_anchor_overrides_change_prompt_and_pixels_and_inherit_bundle(self):
        picture = {**self.entries[0], "value": torch.zeros(1, 64, 48, 3)}
        anchor = {**self.entries[1], "value": torch.ones(1, 64, 48, 3)}
        references = chain._make_tagged_references([picture])
        references["semantic_anchors"] = chain._make_semantic_anchor_bundle(
            [anchor], "768", "timestamped_video")
        before = copy.deepcopy(references)
        result = self.condition(tagged_references=references, override_semantic_anchor_size="1280",
                                override_semantic_anchor_mode="picture_storyboard")
        self.assertFalse(result[5])
        self.assertNotIn("<Video 1>", result[4])
        self.assertEqual([item["type"] for item in result[0][0][1]["tokens"]["presentation"]], ["image", "image"])
        self.assertIn("semantic_anchor_size=1280; semantic_anchor_mode=picture_storyboard", result[-1])
        self.assertEqual(references["semantic_anchors"]["semantic_anchor_size"], "768")
        torch.testing.assert_close(references["semantic_anchors"]["entries"][0]["value"],
                                   before["semantic_anchors"]["entries"][0]["value"])
        inherited = self.condition(tagged_references=references)
        self.assertIn("<Video 1>", inherited[4])
        self.assertIn("semantic_anchor_size=768; semantic_anchor_mode=timestamped_video", inherited[-1])

    def test_latent_path_honors_source_size_anchor_override(self):
        result = upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
            self.state, Clip(), "error", video_vae=VideoVAE(),
            target_video_latent={"samples": torch.zeros(1, 24, 2, 4, 4)},
            override_semantic_anchor_size="source", override_semantic_anchor_mode="picture_storyboard")
        self.assertEqual(tuple(result[0][0][1]["tokens"]["presentation"][-1]["data"].shape), (1, 64, 48, 3))
        self.assertIn("semantic_anchor_size=source", result[-1])

    def test_legacy_match_cache_recovers_originals_before_max_override(self):
        self.large_picture()
        self.condition()
        cached = self.pin_rebuilt_cache()
        cached.pop("source_images")
        with patch.object(chain, "_load_run_reference_cache_descriptor", return_value=cached):
            result = self.condition(override_ref_image_size="max")
        self.assertIn("rebuilt references from verified saved media", result[-1])
        self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (8, 12))

    def test_legacy_match_to_max_cannot_silently_relabel_small_tensors(self):
        self.large_picture()
        self.condition()
        cached = self.pin_rebuilt_cache()
        cached.pop("source_images")
        (self.run / "project_assets" / self.picture["relative_path"]).unlink()
        with patch.object(chain, "_load_run_reference_cache_descriptor", return_value=cached):
            with self.assertRaisesRegex(ValueError, "Cannot change cached match references to max.*masters"):
                self.condition(override_ref_image_size="max")
        with self.assertRaisesRegex(ValueError, "requires original picture masters"):
            chain._conditioning_from_reference_cache_target(Clip(), VideoVAE(), cached, 64, 64,
                                                             ref_image_size="max")

    def test_legacy_max_to_match_caps_native_picture_instead_of_enlarging_it(self):
        self.large_picture()
        self.condition(override_ref_image_size="max")
        cached = self.pin_rebuilt_cache()
        cached.pop("source_images")
        with patch.object(chain, "_load_run_reference_cache_descriptor", return_value=cached):
            result = self.condition(override_ref_image_size="match")
        self.assertEqual(tuple(result[0][0][1]["minimax_refs"][0]["latent"].shape[-2:]), (4, 4))
        self.assertIn("1 V1 fallbacks", result[-1])

    def test_exact_cache_policy_wins_over_recipe_when_rebuilding_missing_tensors(self):
        self.condition(override_ref_image_size="max")
        cached = self.pin_rebuilt_cache()
        self.recipe({"1": {"class_type": "MiniMaxH3TaggedReferenceToVideo", "inputs": {"ref_image_size": "match"}}})
        (self.root / cached["tensor_objects"]["block_000_latent"]["tensors"]).unlink()
        result = self.condition()
        self.assertIn("policy=max", result[-1])

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
