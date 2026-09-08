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
                   "revision_metadata": path.relative_to(self.root).as_posix(),
                   "checkpoint_sha256": revision * 2,
                   "source_revision": source["revision"] if source else "f" * 32,
                   "source_checkpoint_sha256": source["checkpoint_sha256"] if source else "f" * 64}
        if source:
            segment["source_checkpoint"] = source["checkpoint"]
        for field, (subfolder, suffix, _) in module.ARTIFACTS.items():
            artifact = folder / subfolder / (stem + suffix)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"test artifact, never load as a tensor")
            segment[field] = artifact.relative_to(self.root).as_posix()
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

    def pixel_save(self, legacy=False, **kwargs):
        segment = self.save(stage="pixel_upscale", **kwargs)
        path = self.root / segment["revision_metadata"]
        value = json.loads(path.read_text())
        value["profile_config"] = {"backend": "pixel", "recipe": {"refiner": "USDU H3"}}
        segment["context_steps"] = value["segment"]["context_steps"] = 0
        if legacy:
            value.pop("processing_lineage")
        self.write(path, value)
        self.write(path.parent / ("clip_%04d.json" % segment["index"]), value)
        return segment

    def png_owner(self, take, owner="1" * 64):
        take["png_export_owner"] = owner
        path = self.root / take["revision_metadata"]
        metadata = self.manager._read(path)
        metadata["segment"] = take
        metadata["source_scene_contract"] = "e" * 64
        self.write(path, metadata)
        self.write(path.parent / ("clip_%04d.json" % take["index"]), metadata)
        return owner

    def png_sequence(self, scenes, folder="delivery/upscale"):
        directory = self.root / folder
        clips = []
        for scene, owners in scenes:
            name = "frame_%08d.png" % (100 + scene)
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"PNG fixture, possibly hand edited")
            clips.append({"index": scene, "source_contract": "e" * 64,
                          "processing_owners": owners, "first_frame_number": 100 + scene,
                          "last_frame_number": 100 + scene, "delivered_frames": 1,
                          "files": [{"file": name}]})
        record = {"format": "h3_video_png_sequence_v1", "settings": {"run_name": "demo"},
                  "clips": clips, "complete": True, "frame_count": len(clips)}
        self.write(directory / "export.json", record)
        catalog = self.root / "h3_chains/demo/png_exports.json"
        value = self.manager._read(catalog) if catalog.exists() else {
            "format": "h3_png_export_catalog_v1", "run_name": "demo", "directories": []}
        value["directories"].append(folder)
        self.write(catalog, value)
        return directory

    def test_png_delete_removes_owned_scene_including_copies_and_keeps_other_scenes(self):
        take = self.pixel_save(scene=6)
        owner = self.png_owner(take)
        directories = [self.png_sequence([(5, ["2" * 64]), (6, [owner]), (7, ["3" * 64])], name)
                       for name in ("delivery/upscale", "delivery/upscale_2")]
        for directory in directories:
            (directory / "notes.txt").write_text("keep user notes")
            self.write(directory / ".png_variant.json", {"prefix": {"old": "snapshot"}})
        preview = self.preview(take)
        self.assertEqual(sum(item["label"].startswith("PNG frame") for item in preview["files"]), 2)
        self.delete(take)
        for directory in directories:
            self.assertFalse((directory / "frame_00000106.png").exists())
            self.assertTrue((directory / "frame_00000105.png").exists())
            self.assertTrue((directory / "frame_00000107.png").exists())
            self.assertTrue((directory / "notes.txt").exists())
            record = self.manager._read(directory / "export.json")
            self.assertEqual([item["index"] for item in record["clips"]], [5, 7])
            self.assertEqual(record["deleted_scenes"], [6])
            self.assertFalse(record["complete"])
            self.assertIsNone(self.manager._read(directory / ".png_variant.json")["prefix"])

    def test_reused_png_survives_until_last_owner_is_deleted(self):
        first = self.pixel_save()
        second = self.pixel_save(revision="b" * 32)
        a, b = self.png_owner(first), self.png_owner(second, "2" * 64)
        directory = self.png_sequence([(1, [a, b])])
        self.delete(first)
        self.assertTrue((directory / "frame_00000101.png").exists())
        self.assertEqual(self.manager._read(directory / "export.json")["clips"][0]["processing_owners"], [b])
        self.delete(second)
        self.assertFalse((directory / "frame_00000101.png").exists())
        self.assertEqual(self.manager._read(directory / "export.json")["clips"], [])

    def test_retry_takes_with_same_owner_keep_png_until_last_take(self):
        first = self.pixel_save()
        second = self.pixel_save(revision="b" * 32)
        owner = self.png_owner(first)
        self.png_owner(second, owner)
        directory = self.png_sequence([(1, [owner])])
        self.delete(first)
        self.assertTrue((directory / "frame_00000101.png").exists())
        self.delete(second)
        self.assertFalse((directory / "frame_00000101.png").exists())

    def test_png_cleanup_refuses_pending_publication_and_unsafe_addresses(self):
        take = self.pixel_save()
        directory = self.png_sequence([(1, [self.png_owner(take)])])
        pending = directory / ".png_pending.json"
        self.write(pending, {})
        with self.assertRaisesRegex(ValueError, "pending"):
            self.preview(take)
        pending.unlink()
        record = self.manager._read(directory / "export.json")
        record["clips"][0]["files"][0]["file"] = "../other.png"
        self.write(directory / "export.json", record)
        with self.assertRaisesRegex(ValueError, "frame address"):
            self.preview(take)
        self.assertTrue(self.exists(take))

    def test_png_snapshot_detects_edits_and_failed_index_update_restores_frames(self):
        take = self.pixel_save()
        directory = self.png_sequence([(1, [self.png_owner(take)])])
        preview = self.preview(take)
        frame = directory / "frame_00000101.png"
        frame.write_bytes(b"edited after preview")
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.manager.delete("demo", take["revision_metadata"], preview["snapshot"])
        persistence = importlib.import_module(package.__name__ + ".processing_persistence")
        original = persistence.atomic_json
        failed = False

        def fail_once(path, value):
            nonlocal failed
            if Path(path).name == "export.json" and not failed:
                failed = True
                raise OSError("index failure")
            return original(path, value)

        before = (directory / "export.json").read_bytes()
        with patch.object(persistence, "atomic_json", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "index failure"):
                self.delete(take)
        self.assertTrue(self.exists(take))
        self.assertEqual(frame.read_bytes(), b"edited after preview")
        self.assertEqual(json.loads(before), self.manager._read(directory / "export.json"))

    def test_legacy_png_without_exact_ownership_is_never_guessed(self):
        take = self.pixel_save()
        directory = self.png_sequence([(1, [])])
        self.assertTrue(any("Legacy PNG" in line for line in self.preview(take)["not_deleted"]))
        self.delete(take)
        self.assertTrue((directory / "frame_00000101.png").exists())

    def test_independent_pixel_middle_deletion_keeps_later_clips_and_invalidates_manifests(self):
        for legacy in (True, False):
            for chapter in (None, "02_chapter_02"):
                with self.subTest(legacy=legacy, chapter=chapter):
                    kwargs = {"profile": "pixel_%s" % legacy, "chapter": chapter, "legacy": legacy}
                    first = self.pixel_save(scene=8, **kwargs)
                    middle = self.pixel_save(revision="b" * 32, scene=9, prefix=[first], **kwargs)
                    last = self.pixel_save(revision="c" * 32, scene=10, prefix=[first, middle], **kwargs)
                    preserved = self.manifest([first], "partial/through_clip_0008.manifest.json")
                    invalidated = [self.manifest([first, middle, last]),
                                   self.manifest([first, middle], "partial/through_clip_0009.manifest.json"),
                                   self.manifest([first, middle, last], "partial/through_clip_0010.manifest.json")]
                    profile = (self.root / last["revision_metadata"]).parent.parent
                    export = profile / "final/export.mp4"
                    export.parent.mkdir()
                    export.write_bytes(b"keep assembled video")
                    preview = self.preview(middle)
                    self.assertTrue(preview["allowed"], preview["dependents"])
                    self.assertEqual(preview["retained_independent_takes"], [{
                        "scene": 10, "revision": last["revision"], "metadata_path": last["revision_metadata"]}])
                    removed = {self.root / f["path"] for f in preview["files"]}
                    kept = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p not in removed}
                    self.delete(middle)
                    self.assertFalse(self.exists(middle))
                    self.assertTrue(preserved.exists())
                    self.assertTrue(all(not p.exists() for p in invalidated))
                    self.assertTrue(self.exists(last))
                    self.assertEqual(kept, {p: p.read_bytes() for p in kept})
                    variants = catalogue.saved_checkpoint_variants(self.root, "demo", [])["variants"]
                    record = next(v for v in variants if v["key"] == last["revision_metadata"])
                    self.assertTrue(record["ready"])
                    self.assertIsNone(record["processing_branch"], "missing scene must not be advertised as a full branch")
                    # The remaining independent take is still deletable, even
                    # when its historical lineage mentions the removed middle.
                    self.assertTrue(self.preview(last)["allowed"])

    def test_pixel_compact_lineage_only_is_history_not_a_render_dependency(self):
        first = self.pixel_save()
        last = self.pixel_save(revision="b" * 32, scene=2, prefix=[first])
        # No full/partial manifest is necessary for modern metadata.
        self.assertTrue(self.preview(first)["allowed"])
        self.delete(first)
        self.assertTrue(self.exists(last))

    def test_pixel_independence_requires_saved_backend_and_explicit_zero_context(self):
        first = self.pixel_save()
        last = self.pixel_save(revision="b" * 32, scene=2, prefix=[first])
        self.manifest([first, last])
        for take in (first, last):
            path = self.root / take["revision_metadata"]
            original = json.loads(path.read_text())
            for change in ({"profile_config": {}}, {"profile_config": {"backend": "h3_latent"}},
                           {"profile_config": "pixel"}, {"context_steps": None},
                           {"context_steps": 1}, {"context_steps": False}):
                with self.subTest(scene=take["index"], change=change):
                    modified = json.loads(json.dumps(original))
                    if "context_steps" in change:
                        modified["segment"].update(change)
                    else:
                        modified.update(change)
                    self.write(path, modified)
                    self.assertFalse(self.preview(first)["allowed"])
            self.write(path, original)
        self.assertTrue(self.preview(first)["allowed"])

    def test_pixel_source_and_context_dependencies_still_block(self):
        first = self.pixel_save()
        for chapter in (None, "02_chapter_02"):
            with self.subTest(chapter=chapter):
                derived = self.pixel_save(revision="b" * 32, profile="derived", chapter=chapter, source=first)
                self.assertFalse(self.preview(first)["allowed"])
                self.delete(derived)
        last = self.pixel_save(revision="c" * 32, scene=2, prefix=[first])
        path = self.root / last["revision_metadata"]
        original = json.loads(path.read_text())
        for fields in ({"source_checkpoint": first["checkpoint"]},
                       {"predecessor_revision": first["revision"]},
                       {"previous_context": {"checkpoint": first["checkpoint"]}}):
            with self.subTest(fields=fields):
                modified = json.loads(json.dumps(original))
                modified["segment"].update(fields)
                self.write(path, modified)
                preview = self.preview(first)
                self.assertFalse(preview["allowed"])
                self.assertEqual(preview["retained_independent_takes"], [])
        self.write(path, original)
        # An embedded processing source is not sequence bookkeeping.
        manifest = self.manifest([last], source=first)
        self.assertFalse(self.preview(first)["allowed"])
        manifest.unlink()
        self.assertTrue(self.preview(first)["allowed"])

    def test_pixel_manifest_does_not_trust_unverified_or_missing_successor(self):
        first = self.pixel_save()
        last = self.pixel_save(revision="b" * 32, scene=2)
        forged = dict(last, checkpoint_sha256="d" * 64)
        manifest = self.manifest([first, forged])
        self.assertFalse(self.preview(first)["allowed"])
        self.manifest([first, last])
        (self.root / last["revision_metadata"]).unlink()
        self.assertFalse(self.preview(first)["allowed"], "mutable pointer cannot substitute for immutable proof")
        manifest.unlink()

    def test_pixel_history_in_manifests_does_not_invalidate_unrelated_sequence(self):
        old = self.pixel_save()
        new = self.pixel_save(revision="b" * 32)
        new["supersedes"] = old["revision_metadata"]
        manifest = self.manifest([new])
        self.delete(old)
        self.assertTrue(manifest.exists())
        self.assertTrue(self.exists(new))

    def test_pixel_new_dependency_or_changed_proof_rejects_old_confirmation(self):
        first = self.pixel_save()
        last = self.pixel_save(revision="b" * 32, scene=2, prefix=[first])
        manifest = self.manifest([first, last])
        preview = self.preview(first)
        self.assertTrue(preview["allowed"])
        path = self.root / last["revision_metadata"]
        value = json.loads(path.read_text())
        value["segment"]["context_steps"] = 2
        self.write(path, value)
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.manager.delete("demo", first["revision_metadata"], preview["snapshot"])
        self.assertTrue(self.exists(first))
        self.assertTrue(manifest.exists())

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

    def legacy_windows_documents(self):
        def convert(value):
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v) for v in value]
            if isinstance(value, str) and value.startswith("h3_chains/"):
                return value.replace("/", "\\")
            return value
        for path in self.root.rglob("*.json"):
            self.write(path, convert(json.loads(path.read_text())))

    def test_windows_saved_prefix_accepts_mixed_separators_without_rewriting(self):
        take = self.save(chapter="01_intro")
        self.legacy_windows_documents()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        module.require_saved_processing_segments(self.root, [take])
        legacy = dict(take, revision_metadata=take["revision_metadata"].replace("/", "\\"))
        module.require_saved_processing_segments(self.root, [legacy])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        with self.assertRaisesRegex(ValueError, "identity changed"):
            module.require_saved_processing_segments(self.root, [dict(legacy, checkpoint_sha256="f" * 64)])

    def test_windows_delete_blocks_path_only_cross_profile_dependency(self):
        take = self.save()
        self.save(profile="derived", revision="b" * 32, source=take)
        self.legacy_windows_documents()
        preview = self.preview(take)
        self.assertFalse(preview["allowed"])
        self.assertTrue(all("\\" not in item["metadata_path"] for item in preview["dependents"]))
        with self.assertRaises(module.CheckpointDeleteBlocked):
            self.manager.delete("demo", take["revision_metadata"].replace("/", "\\"), preview["snapshot"])
        self.assertTrue(self.exists(take))

    def test_windows_pixel_history_and_preview_confirmation_share_canonical_paths(self):
        first = self.pixel_save()
        last = self.pixel_save(revision="b" * 32, scene=2, prefix=[first])
        self.manifest([first, last])
        self.legacy_windows_documents()
        preview = self.manager.deletion_preview("demo", first["revision_metadata"].replace("/", "\\"))
        self.assertTrue(preview["allowed"])
        self.assertEqual(preview["snapshot"], self.preview(first)["snapshot"])
        self.manager.delete("demo", first["revision_metadata"], preview["snapshot"])
        self.assertTrue(self.exists(last))
        self.assertTrue(self.preview(last)["allowed"])

    def test_windows_junction_is_not_followed(self):
        take = self.save()
        junction = self.root / "h3_chains/demo/upscaled"
        with patch.object(Path, "is_junction", new=lambda path: path == junction, create=True):
            with self.assertRaisesRegex(ValueError, "junction"):
                self.preview(take)

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

    def test_real_route_lock_preview_and_confirmation(self):
        # Execute the actual handler without importing ComfyUI/models.
        tree = ast.parse((ROOT / "chain_nodes.py").read_text())
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                       and node.name == "_processing_checkpoint_deletion")
        take = self.save()
        guards = []

        @contextmanager
        def guard(root, run):
            guards.append(run)
            with module.checkpoint_run_lock(root, run):
                yield

        namespace = {"__package__": package.__name__, "asyncio": asyncio, "json": json,
                     "web": types.SimpleNamespace(json_response=lambda payload, status=200: (status, payload)),
                     "_strict_run_name": module._strict_run_name, "_output_root": lambda: str(self.root),
                     "checkpoint_run_lock": guard,
                     "CheckpointDeleteBlocked": module.CheckpointDeleteBlocked}
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
        self.assertEqual(call()[0], 409)
        self.assertTrue(self.exists(take))
        request.body["snapshot"] = preview["snapshot"]
        self.assertEqual(call()[0], 200)
        self.assertEqual(guards[0], "demo")
        self.assertFalse(self.exists(take))
        request.body = []
        self.assertEqual(call()[0], 400)


if __name__ == "__main__":
    unittest.main()
