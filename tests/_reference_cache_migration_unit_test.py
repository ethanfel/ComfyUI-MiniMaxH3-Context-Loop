#!/usr/bin/env python3
"""Lossless conversion, immutable addresses, and render-success-only retirement."""

import copy
import importlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import _reference_cache_objects_unit_test as fixtures
from _reference_cache_fingerprint_unit_test import Clip
from _upscale_chain_unit_test import (
    load_package, folder_paths, torch, legacy_cache_fixture, av_latent, audio_for_frames)
from comfy_execution.graph import DynamicPrompt
from comfy_execution.utils import CurrentNodeContext


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package, cls.chain, cls.upscale = load_package()
        cls.migration = sys.modules[cls.chain.__package__ + ".reference_cache_migration"]
        cls.usage = sys.modules[cls.chain.__package__ + ".reference_cache_usage"]

    setUp = fixtures.ReferenceObjectsTest.setUp
    save = fixtures.ReferenceObjectsTest.save

    def legacy(self, **kwargs):
        current, _ = self.save(**kwargs)
        return legacy_cache_fixture(self.chain, current)

    def migrator(self):
        return self.migration.ReferenceCacheMigrator(self.root)

    def test_conversion_flushes_objects_through_shared_persistence(self):
        legacy = self.legacy()
        metadata = self.root / legacy["metadata"]
        bundle = self.root / legacy["tensors"]
        before = (metadata.read_bytes(), bundle.read_bytes())
        persistence = self.migration.persistence
        with patch.object(persistence, "sync_file", wraps=persistence.sync_file) as flush:
            report = self.migrator().convert(metadata, apply=True)
        self.assertEqual(report["status"], "converted")
        self.assertGreater(flush.call_count, 0)
        self.assertEqual(before, (metadata.read_bytes(), bundle.read_bytes()))
        for call in flush.call_args_list:
            self.assertTrue(Path(call.args[0]).is_file())

    def test_failed_object_flush_keeps_legacy_cache_and_defers_publication(self):
        legacy = self.legacy()
        metadata = self.root / legacy["metadata"]
        bundle = self.root / legacy["tensors"]
        before = (metadata.read_bytes(), bundle.read_bytes())
        with patch.object(self.migration.persistence, "sync_file",
                          side_effect=OSError("object flush failed")):
            with self.assertRaisesRegex(OSError, "object flush failed"):
                self.migrator().convert(metadata, apply=True)
        self.assertFalse(self.migration.converted_path(metadata).exists())
        self.assertEqual(before, (metadata.read_bytes(), bundle.read_bytes()))
        self.assertEqual(self.migrator().convert(metadata, apply=True)["status"],
                         "converted")

    def snapshot(self):
        return {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def saved_fixture(self):
        # Low-level cleanup tests use tiny durable artifacts. The end-to-end
        # test below exercises the real H3 savers and their commit boundaries.
        parent = self.root / "h3_chains/test/upscaled/pass"
        parent.mkdir(parents=True, exist_ok=True)
        segment = {}
        for field in ("segment", "checkpoint"):
            path = parent / (field + ".bin")
            path.write_bytes(field.encode())
            segment[field] = str(path.relative_to(self.root))
            segment[field + "_sha256"] = self.migration.file_digest(path)
        path = parent / "saved.json"
        self.chain._atomic_json(str(path), {"format": "h3_chain_upscale_segment_v1", "segment": segment})
        return path

    def graph(self, sampler="SamplerCustomAdvanced", passthrough=False):
        return DynamicPrompt({
            "cond": {"class_type": "MiniMaxH3ChainUpscalePixelConditioning", "inputs": {}},
            "other": {"class_type": "CLIPTextEncode", "inputs": {}},
            "guider": {"class_type": "BasicGuider", "inputs": {
                "positive": ["other" if passthrough else "cond", 0]}},
            "sample": {"class_type": sampler, "inputs": {"guider": ["guider", 0], "images": ["cond", 1]}},
            "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0]}},
            "save": {"class_type": "MiniMaxH3ChainUpscaleSegmentSave", "inputs": {"images": ["decode", 0]}},
        })

    def test_dry_run_cli_and_resumable_lossless_conversion(self):
        for version in ("h3_reference_cache_v1", "h3_reference_cache_v2"):
            legacy = self.legacy(prompt=version, vae=fixtures.VideoVAE(dtype=torch.bfloat16))
            legacy["format"] = version
            if version.endswith("v1"):
                legacy.pop("source_images", None)
            path = self.root / legacy["metadata"]
            self.chain._atomic_json(str(path), legacy)
            tensors = self.chain._reference_cache_tensors(legacy)
            before = self.snapshot()
            cli = subprocess.run([sys.executable, "tools/convert_reference_caches.py",
                                  "--output-root", str(self.root), "--metadata", legacy["metadata"]],
                                 capture_output=True, text=True)
            self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
            self.assertIn("would_convert", cli.stdout)
            self.assertEqual(before, self.snapshot())
            migrator = self.migrator()
            with patch.object(migrator, "atomic_json", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    migrator.convert(path, apply=True)
            self.assertTrue((self.root / legacy["tensors"]).is_file())
            self.assertFalse(self.migration.converted_path(path).exists())
            self.assertEqual(migrator.convert(path, apply=True)["status"], "converted")
            self.assertEqual(migrator.convert(path, apply=True)["status"], "already_converted")
            converted = self.chain._load_reference_cache_descriptor(self.chain._reference_cache_descriptor(legacy))
            self.assertEqual(converted["format"], "h3_reference_cache_v3")
            actual = self.chain._reference_cache_tensors(converted)
            self.assertEqual(set(actual), set(tensors))
            for key in tensors:
                self.assertEqual(actual[key].dtype, tensors[key].dtype)
                self.assertTrue(torch.equal(actual[key], tensors[key]), key)
            self.assertEqual(path.read_bytes(), before[path])
            self.assertEqual((self.root / legacy["tensors"]).read_bytes(), before[self.root / legacy["tensors"]])

    def test_retirement_keeps_original_json_old_links_and_other_hardlinks(self):
        legacy = self.legacy()
        migrator = self.migrator()
        migrator.convert(legacy["metadata"], apply=True)
        converted = migrator.resolve(legacy)
        target = self.root / legacy["tensors"]
        other_link = self.root / "unrelated-hardlink.safetensors"
        os.link(target, other_link)
        original_json = (self.root / legacy["metadata"]).read_bytes()
        result = migrator.retire_after_success(converted, self.saved_fixture())
        self.assertGreater(result["retired_bytes"], 0)
        self.assertFalse(target.exists())
        self.assertTrue(other_link.is_file())
        self.assertEqual((self.root / legacy["metadata"]).read_bytes(), original_json)
        old_descriptor = self.chain._reference_cache_descriptor(legacy)
        self.assertEqual(self.chain._load_reference_cache_descriptor(old_descriptor), converted)
        self.assertEqual(self.chain._find_reference_cache("catalog", 1, 3, "first scene", 32, 32, 5), converted)
        self.assertEqual(len(self.chain._reference_payload_from_cache(legacy)[1]), 2)
        self.assertIsNone(migrator.retire_after_success(converted, self.saved_fixture()))

    def test_no_cleanup_for_failed_other_prompt_other_branch_or_passthrough_pixels(self):
        legacy = self.legacy()
        migrator = self.migrator()
        migrator.convert(legacy["metadata"], apply=True)
        converted = migrator.resolve(legacy)
        target = self.root / legacy["tensors"]
        with CurrentNodeContext("render-A", "cond", 0):
            self.chain._conditioning_from_reference_cache(Clip(), converted)
        self.assertTrue(target.exists(), "building conditioning is not a successful render")
        saved = self.saved_fixture()
        cases = [("render-B", 0, self.graph()), ("render-A", 1, self.graph()),
                 ("render-A", 0, self.graph(passthrough=True)),
                 ("render-A", 0, self.graph(sampler="UnknownThirdPartyRenderer"))]
        for prompt_id, index, graph in cases:
            with CurrentNodeContext(prompt_id, "save", index):
                result = self.usage.confirm_saved_use(graph, "save", saved, str(self.root), logging.getLogger("test"))
            self.assertEqual(result, [])
            self.assertTrue(target.exists())
        with CurrentNodeContext("render-A", "save", 0):
            result = self.usage.confirm_saved_use(self.graph("UltimateSDUpscaleNoUpscaleGuider"),
                                                  "save", saved, str(self.root), logging.getLogger("test"))
        self.assertEqual(len(result), 1)
        self.assertFalse(target.exists())

    def test_corrupt_conversion_or_output_and_other_consumer_keep_legacy(self):
        legacy = self.legacy()
        migrator = self.migrator()
        migrator.convert(legacy["metadata"], apply=True)
        converted = migrator.resolve(legacy)
        target = self.root / legacy["tensors"]
        saved = self.saved_fixture()
        object_path = self.root / next(iter(converted["tensor_objects"].values()))["tensors"]
        contents = object_path.read_bytes()
        object_path.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "integrity"):
            migrator.retire_after_success(converted, saved)
        self.assertTrue(target.exists())
        object_path.write_bytes(contents)
        output = migrator.read(saved)
        output_path = self.root / output["segment"]["segment"]
        output_path.write_bytes(b"corrupt saved output")
        with self.assertRaisesRegex(ValueError, "verification"):
            migrator.retire_after_success(converted, saved)
        self.assertTrue(target.exists())
        saved = self.saved_fixture()
        alias_path = (self.root / legacy["metadata"]).with_name("scene_0002.alias.json")
        alias = {**legacy, "metadata": str(alias_path.relative_to(self.root)), "scene": 2}
        self.chain._atomic_json(str(alias_path), alias)
        with self.assertRaisesRegex(ValueError, "Another scene"):
            migrator.retire_after_success(converted, saved)
        self.assertTrue(target.exists())

    def test_converted_project_remains_portable_for_legacy_descriptors(self):
        legacy = self.legacy()
        migrator = self.migrator()
        migrator.convert(legacy["metadata"], apply=True)
        converted = migrator.resolve(legacy)
        local = self.chain._adopt_reference_cache_for_run(
            {"run_name": "portable", "shots": [{}, {}, {}]}, converted)
        self.assertTrue(local["metadata"].endswith(".converted.json"))
        with tempfile.TemporaryDirectory() as relocated:
            shutil.copytree(self.root / "h3_chains/portable", Path(relocated) / "h3_chains/portable")
            folder_paths.output_directory = relocated
            try:
                for cache in (legacy, converted, local):
                    loaded = self.chain._load_run_reference_cache_descriptor(
                        "portable", 1, self.chain._reference_cache_descriptor(cache))
                    self.assertEqual(len(self.chain._reference_payload_from_cache(loaded)[1]), 2)
            finally:
                folder_paths.output_directory = self.temporary.name

    def test_cli_apply_is_project_scoped_and_unsafe_targets_are_not_touched(self):
        legacy = self.legacy()
        local = self.chain._adopt_reference_cache_for_run(
            {"run_name": "only_this", "shots": [{}, {}, {}]}, legacy)
        cli = subprocess.run([sys.executable, "tools/convert_reference_caches.py",
                              "--output-root", str(self.root), "--run", "only_this", "--apply"],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr + cli.stdout)
        self.assertIsNotNone(self.migrator().resolve(local))
        self.assertIsNone(self.migrator().resolve(legacy))
        self.assertTrue((self.root / local["tensors"]).is_file())
        path = self.root / legacy["metadata"]
        changed = {**legacy, "tensors": str(self.root / "outside-bundle.safetensors")}
        self.chain._atomic_json(str(path), changed)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "sibling"):
            self.migrator().convert(path, apply=True)
        self.assertEqual(before, self.snapshot(), "unsafe conversion must not even create a lock file")

    def test_real_pixel_conditioning_failed_save_then_committed_save_and_resume(self):
        chain, upscale = self.chain, self.upscale
        cache, _ = self.save(scene_count=1, pictures=[self.picture])
        legacy_cache_fixture(chain, cache)
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": "shot", "prompt": "first scene", "length": 5,
                                    "steps": 2, "seed": "7"}]}),
            "render_test", "catalog", 32, 32, 1, "video", "head", "disabled",
            "generated_audio", 1, 5 / 24, 2, 7, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        source = chain.MiniMaxH3ChainSegmentSave().save(
            chain._initial_state(plan, 1), torch.zeros(5, 32, 32, 3), av_latent(0.25),
            audio_for_frames(5), denoised_latent=av_latent(0.75))["result"][0]
        selection = {"run_name": "render_test", "output_mode": "workflow_local",
                     "lineage": [{"scene": 1, "revision": source["revision"]}]}
        manager = chain.MiniMaxH3ChainCheckpointManager()
        original_manifest = manager.passthrough(selection)[0]
        original_hash = upscale._source_hash(original_manifest)
        old_descriptor = source["reference_cache"]
        target = self.root / old_descriptor["tensors"]
        self.migrator().convert(old_descriptor["metadata"], apply=True)
        manifest = manager.passthrough(selection)[0]
        self.assertEqual(upscale._source_hash(manifest), original_hash)
        self.assertEqual(manifest, original_manifest)
        adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
        _, state, _, _ = adapter.adapt(manifest, "converted", "pixel", "{}", 1, 0, False, 18)
        pixels = torch.zeros(5, 64, 64, 3)
        with CurrentNodeContext("actual-render", "cond", 0):
            result = upscale.MiniMaxH3ChainUpscalePixelConditioning().condition(
                state, Clip(), pixels, fixtures.VideoVAE(), missing_cache="error")
        self.assertTrue(result[5])
        saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
        persistence = importlib.import_module(chain.__package__ + ".processing_persistence")
        with CurrentNodeContext("actual-render", "save", 0):
            with patch.object(persistence, "atomic_json", side_effect=OSError("save failed")):
                with self.assertRaisesRegex(OSError, "save failed"):
                    saver.save(state, pixels, dynprompt=self.graph(), unique_id="save")
        self.assertTrue(target.exists())
        with CurrentNodeContext("actual-render", "save", 0):
            saved = saver.save(state, pixels, dynprompt=self.graph(), unique_id="save")["result"][0]
        self.assertFalse(target.exists())
        self.assertTrue((self.root / saved["segment"]).is_file())
        self.assertEqual(manager.passthrough(selection)[0], original_manifest)
        self.assertEqual(chain._load_reference_cache_descriptor(old_descriptor)["format"], "h3_reference_cache_v3")
        self.assertEqual(upscale._source_hash(manager.passthrough(selection)[0]), original_hash)


if __name__ == "__main__":
    unittest.main(argv=["reference-cache-migration"], verbosity=2)
