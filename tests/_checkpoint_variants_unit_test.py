#!/usr/bin/env python3
"""Disk-only processing catalogue tests: no ComfyUI, tensors, or GPU required."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("checkpoint_variants", ROOT / "checkpoint_variants.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.originals = [{"scene": 8, "revision": "a" * 32, "checkpoint_sha256": "b" * 64}]

    def save(self, profile="hq", revision="c" * 32, chapter=False, recipe=None,
             source_revision="a" * 32, source_hash="b" * 64, backend="h3_latent", scene=8):
        parent = self.root / "h3_chains/demo"
        if chapter:
            parent /= "chapters/02_test"
        parent = parent / "upscaled" / profile
        path = parent / "checkpoints" / ("clip_%04d.%s.json" % (scene, revision))
        path.parent.mkdir(parents=True, exist_ok=True)
        segment = {
            "index": scene, "id": "eighth", "revision": revision,
            "source_revision": source_revision, "source_checkpoint_sha256": source_hash,
            "checkpoint_sha256": revision * 2, "width": 960, "height": 544,
            "raw_frames": 175, "delivered_frames": 175,
            "latent_saved": True, "latent_layout": "joint_av",
            "revision_metadata": str(path.relative_to(self.root)),
        }
        for name, suffix in (("checkpoint", ".safetensors"), ("segment", ".mp4"), ("generated_audio", ".wav")):
            artifact = parent / (revision + suffix)
            artifact.write_bytes(b"fixture: catalogue must not deserialize or hash tensors")
            segment[name] = str(artifact.relative_to(self.root))
        value = {"format": "h3_chain_upscale_segment_v1", "run_name": "demo",
                 "profile": profile, "profile_config": {"backend": backend, "recipe": recipe or {}},
                 "segment": segment}
        self.write(path, value)
        # Mutable pointer plus its immutable history must appear only once.
        self.write(path.parent / ("clip_%04d.json" % scene), value)
        return path, value

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def scan(self):
        return module.saved_checkpoint_variants(self.root, "demo", self.originals)

    def test_profiles_chapters_stages_and_duplicate_pointers(self):
        self.save(profile="derope_in_name_only")
        self.save(profile="motion", recipe={"derope": "MAINodes adaptive pixel smear"}, chapter=True)
        self.save(profile="pixel", backend="pixel")
        self.save(profile="ltx", backend="ltx_2_5")
        before = {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")}
        result = self.scan()
        self.assertEqual(result["warnings"], [])
        self.assertEqual({v["stage"] for v in result["variants"]}, set(module.STAGES))
        self.assertEqual(len(result["variants"]), 4)
        for item in result["variants"]:
            self.assertEqual(item["originals"], [{"scene": 8, "revision": "a" * 32}])
            self.assertTrue(item["ready"])
            self.assertIn("video", item)
            self.assertIn("audio", item)
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.root.rglob("*")})

    def test_wrong_revision_or_hash_never_attaches_to_same_scene(self):
        self.save(profile="wrong_revision", source_revision="d" * 32)
        self.save(profile="wrong_hash", source_hash="d" * 64)
        for item in self.scan()["variants"]:
            self.assertEqual(item["originals"], [])
            self.assertIn("mismatch", item["source_status"])

    def test_retained_takes_and_transitive_derope_source(self):
        self.save(profile="motion", recipe={"derope": True})
        self.save(profile="motion", recipe={"derope": True}, revision="d" * 32)
        self.save(profile="hq", revision="e" * 32, source_revision="c" * 32, source_hash="c" * 64)
        values = self.scan()["variants"]
        self.assertEqual(len(values), 3)
        self.assertTrue(all(item["originals"] for item in values))

    def test_attributed_alias_requires_matching_checkpoint_content(self):
        self.originals.append({"scene": 8, "revision": "d" * 32,
                               "adopted_from_revision": "a" * 32, "checkpoint_sha256": "b" * 64})
        self.originals.append({"scene": 8, "revision": "e" * 32,
                               "adopted_from_revision": "a" * 32, "checkpoint_sha256": "f" * 64})
        self.save()
        originals = self.scan()["variants"][0]["originals"]
        self.assertEqual([v["revision"] for v in originals], ["a" * 32, "d" * 32])

    def test_alt_processing_is_linked_to_exact_base_including_derope_children(self):
        self.originals.append({"scene": 8, "revision": "d" * 32, "checkpoint_sha256": "e" * 64,
                               "take_kind": "editorial_alternate", "alternate_of_revision": "a" * 32})
        self.save(profile="motion", source_revision="d" * 32, source_hash="e" * 64)
        self.save(profile="hq", revision="f" * 32, source_revision="c" * 32, source_hash="c" * 64)
        self.save(profile="wrong", source_revision="d" * 32, source_hash="b" * 64)
        records = {v["profile"]: v for v in self.scan()["variants"]}
        expected = [{"scene": 8, "revision": "a" * 32}]
        self.assertEqual(records["motion"]["originals"], expected)
        self.assertEqual(records["hq"]["originals"], expected)
        self.assertEqual(records["wrong"]["originals"], [])
        self.originals = self.originals[1:]  # No guessing a base if its record is gone.
        self.assertTrue(all(not v["originals"] for v in self.scan()["variants"]))

    def test_absent_full_latent_broken_files_and_malformed_metadata(self):
        path, value = self.save()
        value["segment"].update(latent_saved=False, latent_layout="omitted", context_steps=12)
        self.write(path, value)
        (self.root / value["segment"]["checkpoint"]).unlink()
        (path.parent / ("clip_0009." + "f" * 32 + ".json")).write_text("{", encoding="utf-8")
        result = self.scan()
        self.assertFalse(result["variants"][0]["ready"])
        self.assertFalse(result["variants"][0]["latent_saved"])
        self.assertEqual(result["variants"][0]["context_steps"], 12)
        self.assertEqual(len(result["warnings"]), 1)

    def test_unsafe_paths_and_cross_run_metadata_are_not_exposed(self):
        path, value = self.save()
        value["segment"]["segment"] = "../secret.mp4"
        self.write(path, value)
        self.assertEqual(self.scan()["variants"], [])
        self.assertTrue(self.scan()["warnings"])
        value["run_name"] = "another"
        self.write(path, value)
        self.assertEqual(self.scan()["variants"], [])
        with self.assertRaises(ValueError):
            module.saved_checkpoint_variants(self.root, "../outside", [])

    def test_cycles_do_not_attach_or_recurse_forever(self):
        self.save(profile="one", revision="c" * 32, source_revision="d" * 32, source_hash="d" * 64)
        self.save(profile="two", revision="d" * 32, source_revision="c" * 32, source_hash="c" * 64)
        self.assertTrue(all(not item["originals"] for item in self.scan()["variants"]))

    def test_stage_classification_uses_recipe_not_profile_name(self):
        for off in (False, "false", "off", "none", "disabled", "", 0):
            self.assertEqual(module.processing_stage({"backend": "h3_latent", "recipe": {"derope": off}}), "latent_upscale")
        self.assertEqual(module.processing_stage({"backend": "h3_latent", "recipe": {"stage": "derope"}}), "derope")

    def test_processing_prefix_extends_to_unique_tip_but_not_across_forks(self):
        first_path, first = self.save(profile="motion", recipe={"derope": True})
        first["processing_lineage"] = module.processing_lineage([first["segment"]])
        self.write(first_path, first)
        second_path, second = self.save(profile="motion", recipe={"derope": True}, scene=9, revision="d" * 32)
        second["processing_lineage"] = module.processing_lineage([first["segment"], second["segment"]])
        self.write(second_path, second)
        records = self.scan()["variants"]
        self.assertTrue(all(len(item["processing_branch"]["lineage"]) == 2 for item in records))
        fork_path, fork = self.save(profile="motion", recipe={"derope": True}, scene=9, revision="e" * 32)
        fork["processing_lineage"] = module.processing_lineage([first["segment"], fork["segment"]])
        self.write(fork_path, fork)
        records = {item["revision"]: item for item in self.scan()["variants"]}
        self.assertIsNone(records["c" * 32]["processing_branch"])
        for revision in ("d" * 32, "e" * 32):
            self.assertEqual(records[revision]["processing_branch"]["lineage"][-1]["revision"], revision)

    def test_legacy_profile_manifest_supplies_saved_branch_without_rewriting(self):
        path, saved = self.save(profile="motion", recipe={"derope": True})
        manifest = path.parent.parent / "upscale_manifest.json"
        self.write(manifest, {"format": "h3_chain_upscale_manifest_v1", "run_name": "demo",
                              "profile": "motion", "segments": [saved["segment"]]})
        stamp = path.stat().st_mtime_ns
        record = self.scan()["variants"][0]
        self.assertEqual(record["processing_branch"]["kind"], "manifest")
        self.assertEqual(record["processing_branch"]["lineage"], module.processing_lineage([saved["segment"]]))
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_missing_pixel_lineage_member_does_not_hide_survivor_or_substitute_new_take(self):
        first_path, first = self.save(profile="pixel", backend="pixel")
        second_path, second = self.save(profile="pixel", backend="pixel", scene=9, revision="d" * 32)
        second["processing_lineage"] = module.processing_lineage([first["segment"], second["segment"]])
        self.write(second_path, second)
        self.assertTrue(all(v["processing_branch"] for v in self.scan()["variants"]))
        first_path.unlink()
        # Neither its surviving mutable pointer nor a different same-scene
        # take may fill this immutable branch's hole.
        self.save(profile="pixel", backend="pixel", revision="e" * 32)
        before = second_path.read_bytes()
        surviving = next(v for v in self.scan()["variants"] if v["revision"] == "d" * 32)
        self.assertTrue(surviving["ready"])
        self.assertIsNone(surviving["processing_branch"])
        self.assertEqual(second_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
