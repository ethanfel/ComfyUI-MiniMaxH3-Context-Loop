#!/usr/bin/env python3
"""Deletion fixtures stay in a temporary directory; no real user media is touched."""

import ast
import asyncio
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("processing_delete_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".processing_checkpoint_delete")
catalogue = importlib.import_module(package.__name__ + ".checkpoint_variants")


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = module.ProcessingCheckpointManager(self.root)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def save(self, revision="a" * 32, scene=1, profile="hq", chapter=None,
             prefix=(), source=None, stage="latent_upscale"):
        folder = self.root / "h3_chains/demo"
        if chapter:
            folder /= "chapters/" + chapter
        folder /= "upscaled/" + profile
        stem = "clip_%04d.%s" % (scene, revision)
        path = folder / "checkpoints" / (stem + ".json")
        segment = {"index": scene, "id": "scene_%d" % scene, "revision": revision,
                   "revision_metadata": str(path.relative_to(self.root)),
                   "checkpoint_sha256": revision * 2,
                   "source_revision": source["revision"] if source else "f" * 32,
                   "source_checkpoint_sha256": source["checkpoint_sha256"] if source else "f" * 64}
        if source:
            segment["source_checkpoint"] = source["checkpoint"]
        for field, (subfolder, suffix, _) in module.ARTIFACTS.items():
            artifact = folder / subfolder / (stem + suffix)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"test artifact, never load as a tensor")
            segment[field] = str(artifact.relative_to(self.root))
        metadata = {"format": "h3_chain_upscale_segment_v1", "profile": profile,
                    "run_name": "demo", "segment": segment, "processing_stage": stage,
                    "processing_lineage": catalogue.processing_lineage([*prefix, segment])}
        self.write(path, metadata)
        self.write(folder / "checkpoints" / ("clip_%04d.json" % scene), metadata)
        return segment

    def manifest(self, segments, name="upscale_manifest.json", source=None):
        profile = (self.root / segments[-1]["revision_metadata"]).parent.parent
        path = profile / name
        self.write(path, {"format": "h3_chain_upscale_manifest_v1", "run_name": "demo",
                          "profile": profile.name, "segments": segments,
                          "source_manifest": {"segments": [source]} if source else {}})
        return path

    def preview(self, segment):
        return self.manager.deletion_preview("demo", segment["revision_metadata"])

    def delete(self, segment):
        preview = self.preview(segment)
        return self.manager.delete("demo", segment["revision_metadata"], preview["snapshot"])

    def exists(self, segment):
        return (self.root / segment["revision_metadata"]).exists()

    def test_all_processing_stages_root_and_chapter(self):
        for i, stage in enumerate(catalogue.STAGES):
            with self.subTest(stage=stage):
                take = self.save(profile=stage, stage=stage, chapter="01_intro" if i % 2 else None)
                profile = (self.root / take["revision_metadata"]).parent.parent
                export = profile / "final/movie.mp4"
                export.parent.mkdir()
                export.write_bytes(b"keep export")
                manifest = self.manifest([take])
                partial = self.manifest([take], "partial/through_clip_0001.manifest.json")
                original = self.root / "h3_chains/demo/checkpoints/original.safetensors"
                reference = self.root / "h3_reference_cache/shared.safetensors"
                for kept in (original, reference):
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    kept.write_bytes(b"keep")
                preview = self.preview(take)
                self.assertTrue(preview["allowed"])
                self.assertEqual(preview["owned_file_count"], 8)
                result = self.delete(take)
                self.assertEqual(result["reclaimed_bytes"], preview["reclaimed_bytes"])
                self.assertEqual(result["cleanup_pending"], [])
                self.assertFalse(self.exists(take))
                self.assertFalse(manifest.exists())
                self.assertFalse(partial.exists())
                self.assertFalse((profile / "checkpoints/clip_0001.json").exists())
                self.assertEqual(export.read_bytes(), b"keep export")
                self.assertEqual(original.read_bytes(), b"keep")
                self.assertEqual(reference.read_bytes(), b"keep")

    def test_old_take_deletion_preserves_new_pointer_and_other_profile(self):
        old = self.save()
        new = self.save(revision="b" * 32)
        other = self.save(profile="other")
        new_path = (self.root / new["revision_metadata"])
        value = json.loads(new_path.read_text())
        value["segment"]["supersedes"] = old["revision_metadata"]
        self.write(new_path, value)
        self.delete(old)
        self.assertTrue(self.exists(new))
        self.assertTrue(self.exists(other))
        self.assertEqual(json.loads((new_path.parent / "clip_0001.json").read_text())["segment"]["revision"], "b" * 32)

    def test_branch_descendant_blocks_then_leaf_can_be_deleted(self):
        first = self.save()
        second = self.save(revision="b" * 32, scene=2, prefix=[first])
        earlier_manifest = self.manifest([first], "partial/through_clip_0001.manifest.json")
        self.manifest([first, second])
        preview = self.preview(first)
        self.assertFalse(preview["allowed"])
        self.assertEqual(len(preview["dependents"]), 1)
        self.assertEqual(preview["dependents"][0]["metadata_path"], second["revision_metadata"])
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.delete(first)
        self.delete(second)
        self.assertTrue(earlier_manifest.exists())
        self.assertTrue(self.exists(first))
        self.delete(first)

    def test_cross_profile_derived_source_blocks(self):
        source = self.save(profile="derope", stage="derope")
        derived = self.save(revision="b" * 32, profile="pixel", source=source)
        self.assertFalse(self.preview(source)["allowed"])
        self.delete(derived)
        self.assertTrue(self.preview(source)["allowed"])

    def test_legacy_manifest_and_context_dependencies(self):
        first = self.save()
        second = self.save(revision="b" * 32, scene=2)
        for take in (first, second):
            path = self.root / take["revision_metadata"]
            value = json.loads(path.read_text())
            value.pop("processing_lineage")
            value["segment"]["context_steps"] = 2
            self.write(path, value)
            self.write(path.parent / ("clip_%04d.json" % take["index"]), value)
        self.assertFalse(self.preview(first)["allowed"])
        self.manifest([first, second])
        self.assertFalse(self.preview(first)["allowed"])

    def test_missing_media_can_be_cleaned_up(self):
        take = self.save()
        (self.root / take["checkpoint"]).unlink()
        self.assertTrue(self.preview(take)["allowed"])
        self.delete(take)
        self.assertFalse(self.exists(take))

    def test_requires_fresh_preview_after_pointer_or_file_change(self):
        take = self.save()
        before = self.preview(take)
        self.save(revision="b" * 32)
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.manager.delete("demo", take["revision_metadata"], before["snapshot"])
        before = self.preview(take)
        (self.root / take["checkpoint"]).write_bytes(b"changed")
        for snapshot in (before["snapshot"], ""):
            with self.assertRaises(module.CheckpointDeleteBlocked):
                self.manager.delete("demo", take["revision_metadata"], snapshot)
        self.assertTrue(self.exists(take))

    def test_new_dependent_invalidates_confirmation(self):
        take = self.save()
        before = self.preview(take)
        self.save(revision="b" * 32, profile="other", source=take)
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.manager.delete("demo", take["revision_metadata"], before["snapshot"])

    def test_traversal_original_run_and_symlink_rejected(self):
        take = self.save()
        for address in ("/tmp/file", "../file", take["revision_metadata"].replace("demo", "other"),
                        "h3_chains/demo/checkpoints/clip_0001." + "a" * 32 + ".json"):
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.manager.deletion_preview("demo", address)
        path = self.root / take["checkpoint"]
        path.unlink()
        path.symlink_to(self.root / "elsewhere")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.preview(take)

    def test_forged_artifact_ownership_and_malformed_other_metadata_rejected(self):
        take = self.save()
        path = self.root / take["revision_metadata"]
        value = json.loads(path.read_text())
        value["segment"]["checkpoint"] = "h3_chains/demo/checkpoints/keep.safetensors"
        self.write(path, value)
        with self.assertRaisesRegex(ValueError, "own"):
            self.preview(take)
        take = self.save()
        (path.parent / ("clip_0002." + "b" * 32 + ".json")).write_text("{")
        with self.assertRaises(ValueError):
            self.preview(take)

    def test_failed_staging_rolls_back_every_file(self):
        take = self.save()
        preview = self.preview(take)
        original = module.os.replace
        calls = []

        def fail_once(src, dst):
            calls.append(src)
            if len(calls) == 3:
                raise OSError("fixture failure")
            return original(src, dst)

        with patch.object(module.os, "replace", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.manager.delete("demo", take["revision_metadata"], preview["snapshot"])
        for item in preview["files"]:
            self.assertTrue((self.root / item["path"]).is_file())
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_save_fence_refuses_deleted_or_changed_dependency(self):
        take = self.save()
        module.require_saved_processing_segments(self.root, [take])
        forged = {**take, "checkpoint_sha256": "d" * 64}
        with self.assertRaisesRegex(ValueError, "identity changed"):
            module.require_saved_processing_segments(self.root, [forged])
        self.delete(take)
        with self.assertRaisesRegex(ValueError, "deleted"):
            module.require_saved_processing_segments(self.root, [take])

    def test_real_route_ownership_preview_and_confirmation(self):
        # Execute the actual handler without importing ComfyUI/models.
        tree = ast.parse((ROOT / "chain_nodes.py").read_text())
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == "_processing_checkpoint_deletion")
        take = self.save()
        guards = []

        @contextmanager
        def guard(root, run, proof, action):
            guards.append((run, proof, action))
            yield

        namespace = {"__package__": package.__name__, "asyncio": asyncio, "json": json,
                     "web": types.SimpleNamespace(json_response=lambda payload, status=200: (status, payload)),
                     "_strict_run_name": module._strict_run_name, "_output_root": lambda: str(self.root),
                     "checkpoint_run_lock": module.checkpoint_run_lock, "project_write_guard": guard,
                     "ProjectOwnershipError": type("ProjectOwnershipError", (Exception,), {}),
                     "CheckpointDeleteBlocked": module.CheckpointDeleteBlocked,
                     "_request_project_ownership": lambda request: "owner-proof",
                     "_project_write_rejection": lambda *args: (423, {"error": "read only"})}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "route", "exec"), namespace)

        class Request:
            path = "/processing-checkpoints/delete-preview"
            body = {"run_name": "demo", "metadata_path": take["revision_metadata"]}

            async def json(self):
                return self.body

        request = Request()
        call = lambda: asyncio.run(namespace[handler.name](request))
        status, preview = call()
        self.assertEqual(status, 200)
        self.assertEqual(guards, [])
        request.path = "/processing-checkpoints/delete"
        request.body["snapshot"] = preview["snapshot"]
        self.assertEqual(call()[0], 423)
        self.assertTrue(self.exists(take))
        namespace["_project_write_rejection"] = lambda *args: None
        self.assertEqual(call()[0], 200)
        self.assertEqual(guards[0][1], "owner-proof")
        self.assertFalse(self.exists(take))
        request.body = []
        self.assertEqual(call()[0], 400)


if __name__ == "__main__":
    unittest.main()
