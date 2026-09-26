#!/usr/bin/env python3
"""Post-export cleanup uses only disposable fixtures, never user projects."""

import copy
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("assembly_cleanup_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
cleanup = importlib.import_module(package.__name__ + ".assembly_checkpoint_cleanup")
layout = importlib.import_module(package.__name__ + ".chain_layout")
conversion = importlib.import_module(package.__name__ + ".chain_layout_conversion")


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="h3-assembly-cleanup-test-")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.run = self.output / "h3_chains/demo"
        mode = os.environ.get("H3_TEST_LAYOUT", "legacy")
        if mode == "organized":
            layout.create_project(self.run)
        self.run = Path(layout.state_root(self.run))
        self.final = Path(layout.resolve_path(self.run / "final/final.mp4"))
        self.final.parent.mkdir(parents=True)
        self.final.write_bytes(b"completed export fixture")
        self.segments = [self.save(1), self.save(2)]
        self.manifest = self.make_manifest(self.segments)
        self.write(self.run / "manifest.json", self.manifest)
        if mode == "converted":
            source = self.run
            before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
            destination = self.output / "converted"
            final_address = self.final.relative_to(self.output).as_posix()
            conversion.convert_copy(source, destination)
            self.output = destination
            self.run = Path(layout.state_root(self.output / "h3_chains/demo"))
            self.final = self.path(final_address)
            self.addCleanup(lambda: self.assertEqual(
                before, {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}))

    def path(self, address):
        return Path(layout.resolve_path(self.output / address))

    def write(self, path, value):
        path = Path(layout.resolve_path(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def save(self, scene, revision=None, scope="", **extra):
        revision = revision or str(scene) * 32
        folder = self.run / scope
        stem = "clip_%04d.%s" % (scene, revision)
        result = {"index": scene, "id": "scene_%d" % scene,
                  "revision": revision, "raw_frames": 5, "delivered_frames": 5,
                  "width": 32, "height": 32, **extra}
        for key, directory, suffix in (("checkpoint", "checkpoints", ".safetensors"),
                                       ("segment", "segments", ".mp4"),
                                       ("prompt_file", "prompts", ".txt")):
            path = Path(layout.resolve_path(folder / directory / (stem + suffix)))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((key + stem).encode())
            result[key] = path.relative_to(self.output).as_posix()
            result[key + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        document = {"format": "h3_chain_segment_v3", "run_name": "demo", "segment": result}
        self.write(folder / "checkpoints" / (stem + ".json"), document)
        self.write(folder / "checkpoints" / ("clip_%04d.json" % scene), document)
        return result

    def make_manifest(self, segments, **extra):
        return {"format": "h3_chain_manifest_v3", "run_name": "demo",
                "compatibility": {"width": 32, "height": 32, "audio_mode": "generated_audio"},
                "segments": segments, "clip_count": len(segments),
                "total_delivered_frames": 5 * len(segments), **extra}

    def prepare(self, manifest=None):
        return cleanup.AssemblyCheckpointCleanup(self.output, manifest or self.manifest)

    def exists(self, segment):
        return self.path(segment["checkpoint"]).exists()


class CleanupTests(Fixture, unittest.TestCase):
    def test_deletes_only_checkpoint_payloads_preserves_all_other_bytes(self):
        self.write(self.run / "partial/through_clip_0001.manifest.json",
                   self.make_manifest(self.segments[:1], format="h3_chain_partial_manifest_v3"))
        project = self.output / "h3_chains/demo"
        untouched = {p: p.read_bytes() for p in project.rglob("*")
                     if p.is_file() and p.suffix != ".safetensors"}
        job = self.prepare()
        status = job.finish([self.final])
        self.assertIn("deleted 2 file(s)", status)
        self.assertIn("freed", status)
        self.assertFalse(any(self.exists(s) for s in self.segments))
        self.assertEqual(untouched, {p: p.read_bytes() for p in untouched})
        self.assertIn("deleted 0 file(s)", job.finish([self.final]))

    def test_other_take_keeps_shared_predecessor(self):
        self.save(2, "b" * 32, predecessor_revision=self.segments[0]["revision"])
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))
        self.assertFalse(self.exists(self.segments[1]))

    def test_physical_manifest_addresses_match_legacy_ownership_records(self):
        manifest = copy.deepcopy(self.manifest)
        for segment in manifest["segments"]:
            segment["checkpoint"] = self.path(segment["checkpoint"]).relative_to(self.output).as_posix()
        self.assertIn("deleted 2", self.prepare(manifest).finish([self.final]))
        self.assertFalse(any(self.exists(s) for s in self.segments))

    def test_physical_address_only_dependency_protects_legacy_checkpoint(self):
        address = self.path(self.segments[0]["checkpoint"]).relative_to(self.output).as_posix()
        self.write(self.run / "upscaled/hq/upscale_manifest.json", {"source_checkpoint": address})
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))
        self.assertFalse(self.exists(self.segments[1]))

    def test_non_linear_dependency_kept(self):
        self.save(3, "c" * 32, visual_context_blocks=[{
            "source_checkpoint_sha256": self.segments[0]["checkpoint_sha256"]}])
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))

    def test_legacy_filename_revision_protects_shared_checkpoint(self):
        revision = self.segments[0].pop("revision")
        self.save(2, "b" * 32, predecessor_revision=revision)
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))

    def test_pending_assignment_keeps_checkpoints(self):
        self.write(self.run / "checkpoints/.transactions/pending.json", {})
        self.assertIn("assignment is pending", self.prepare().finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_named_branch_shared_pointer_protected(self):
        self.write(self.run / ("branches/" + "b" * 32 + "/checkpoints/clip_0001.json"),
                   {"segment": self.segments[0]})
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))
        self.assertFalse(self.exists(self.segments[1]))

    def test_named_branch_can_remove_own_unshared_immutable_take(self):
        branch = "b" * 32
        segment = self.save(1, "c" * 32)
        # Original keeps its original take; this take is assigned only to b.
        self.write(self.run / "checkpoints/clip_0001.json", {"segment": self.segments[0]})
        self.write(self.run / ("branches/" + branch + "/checkpoints/clip_0001.json"), {"segment": segment})
        self.prepare(self.make_manifest([segment], _branch_id=branch)).finish([self.final])
        self.assertFalse(self.exists(segment))
        self.assertTrue(self.exists(self.segments[0]))

    def test_processed_dependents_protect_source(self):
        self.save(1, "d" * 32, "chapters/01_test/upscaled/hq",
                  source_checkpoint=self.segments[0]["checkpoint"])
        self.prepare().finish([self.final])
        self.assertTrue(self.exists(self.segments[0]))

    def test_upscale_cleanup_keeps_originals(self):
        hq = self.save(1, "d" * 32, "upscaled/hq", source_checkpoint=self.segments[0]["checkpoint"])
        manifest = self.make_manifest([hq], format="h3_chain_upscale_manifest_v1",
                                      profile="hq", source_manifest=self.make_manifest(self.segments[:1]))
        self.write(self.run / "upscaled/hq/upscale_manifest.json", manifest)
        self.prepare(manifest).finish([self.final])
        self.assertFalse(self.exists(hq))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_partial_exports_never_scan_or_delete(self):
        for manifest in (
            self.make_manifest(self.segments, format="h3_chain_partial_manifest_v3"),
            self.make_manifest(self.segments, format="h3_chain_upscale_partial_manifest_v1"),
            self.make_manifest(self.segments, format="h3_chain_chapter_manifest_v1", chapter={"complete": False}),
        ):
            with patch.object(cleanup.AssemblyCheckpointCleanup, "_documents", side_effect=AssertionError("must not scan")):
                self.assertIn("partial assembly", self.prepare(manifest).finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_failed_or_empty_copy_keeps_every_checkpoint(self):
        missing = self.output / "missing.mp4"
        for paths in ([], [missing], [self.final, missing]):
            self.assertIn("skipped", self.prepare().finish(paths))
        missing.touch()
        self.assertIn("skipped", self.prepare().finish([self.final, missing]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_sync_error_keeps_checkpoints(self):
        with patch.object(cleanup, "sync_file", side_effect=OSError("disk error")):
            self.assertIn("disk error", self.prepare().finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_changed_and_hardlinked_checkpoints_kept(self):
        os.link(self.path(self.segments[0]["checkpoint"]), self.output / "backup.safetensors")
        job = self.prepare()
        self.path(self.segments[1]["checkpoint"]).write_bytes(b"changed")
        self.assertIn("deleted 0", job.finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_corrupt_ownership_metadata_keeps_checkpoints(self):
        (self.run / "checkpoints/clip_9999.json").write_text("broken json")
        self.assertIn("skipped", self.prepare().finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_never_walks_media_or_unrelated_projects(self):
        self.write(self.run / "frames/huge/invalid.json", None)
        self.write(self.output / "h3_chains/other/checkpoints/broken.json", None)
        self.assertIn("deleted 2", self.prepare().finish([self.final]))

    def test_unsafe_target_paths_rejected(self):
        for address in ("../victim", "h3_chains/other/checkpoints/clip_0001.safetensors",
                        "h3_chains/demo/assets/checkpoints/clip_0001.safetensors", "C:\\victim"):
            manifest = copy.deepcopy(self.manifest)
            manifest["segments"][0]["checkpoint"] = address
            with self.assertRaises(ValueError):
                self.prepare(manifest)

    def test_symlinked_file_or_metadata_directory_kept(self):
        path = self.path(self.segments[0]["checkpoint"])
        moved = path.with_suffix(".kept")
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaises(ValueError):
            self.prepare()
        path.unlink()
        moved.rename(path)
        (self.run / "branches").symlink_to(self.output / "elsewhere", target_is_directory=True)
        self.assertIn("skipped", self.prepare().finish([self.final]))
        self.assertTrue(all(self.exists(s) for s in self.segments))


class AssembleTests(Fixture, unittest.TestCase):
    # Integration uses real tiny H.264 clips and the actual Assemble method,
    # but never loads GPU models or parses the dummy checkpoint as a tensor.
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cleanup_assemble_helpers", ROOT / "tests/_generated_audio_sidecar_unit_test.py")
        cls.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.helper)
        cls.chain = cls.helper.chain
        cls.runtime_cleanup = importlib.import_module(
            cls.chain.__package__ + ".assembly_checkpoint_cleanup")

    def setUp(self):
        super().setUp()
        self.helper.folder_paths.output_directory = str(self.output)
        self.addCleanup(patch.stopall)
        patch.object(self.chain, "_generated_audio", return_value=None).start()
        for segment in self.segments:
            path = self.path(segment["segment"])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", "color=c=black:s=32x32:r=24", "-frames:v", "5",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
            segment["segment_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    def assemble(self, **kwargs):
        return self.chain.MiniMaxH3ChainAssemble().assemble(
            self.manifest, "none", "batch", 96, **kwargs)

    def test_default_off_has_no_cleanup_inspection(self):
        with patch.object(self.runtime_cleanup, "AssemblyCheckpointCleanup",
                          side_effect=AssertionError("no cleanup inspection")):
            result = self.assemble()
        self.assertTrue(Path(result["result"][0]).is_file())
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_optional_widget_is_appended_and_examples_default_off(self):
        sys.path.insert(0, str(ROOT / "tools"))
        from workflow_schema import widget_fields, validate_value
        schema = {"input": self.chain.MiniMaxH3ChainAssemble.INPUT_TYPES()}
        fields = list(widget_fields(schema, {}))
        # New widgets append after cleanup; its original serialized slot must
        # remain stable so old workflows cannot accidentally enable deletion.
        name, spec = fields[8]
        self.assertEqual(name, "delete_checkpoints_after_assembly")
        self.assertIs(spec[1]["default"], False)
        self.assertIn(name, schema["input"]["optional"])
        checked = 0
        for path in sorted((ROOT / "example_workflows").rglob("*.json")):
            for node in json.loads(path.read_text())["nodes"]:
                if node["type"] != "MiniMaxH3ChainAssemble":
                    continue
                values = node["widgets_values"]
                self.assertEqual(len(values), len(fields), path.name)
                for value, (field, field_spec) in zip(values, fields):
                    validate_value(value, field_spec, (path.name, field))
                self.assertIs(values[8], False, path.name)
                self.assertEqual(values[9], "av_overlap", path.name)
                checked += 1
        self.assertGreater(checked, 0)
        for path in sorted((ROOT / "tools/v06/recipes").rglob("*.json")):
            for node in json.loads(path.read_text())["nodes"]:
                if node["type"] == "MiniMaxH3ChainAssemble":
                    self.assertIs(node["settings"][name], False, path.name)

    def test_real_export_and_copy_completed_before_cleanup(self):
        result = self.assemble(delete_checkpoints_after_assembly=True,
                               copy_to_output=True, output_subfolder="published")
        final = Path(result["result"][0])
        self.assertEqual(final.read_bytes(), (self.output / "published/batch.mp4").read_bytes())
        self.assertIn("deleted 2 file(s)", result["ui"]["text"][0])
        self.assertFalse(any(self.exists(s) for s in self.segments))
        probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                         "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(final)], text=True)
        self.assertEqual(probe.strip(), "10")

    def test_copy_failure_keeps_checkpoint_payloads(self):
        with patch.object(self.chain, "_copy_final_to_output", side_effect=OSError("copy failed")):
            with self.assertRaisesRegex(OSError, "copy failed"):
                self.assemble(delete_checkpoints_after_assembly=True, copy_to_output=True)
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_cleanup_lock_failure_keeps_successful_export(self):
        with patch.object(self.runtime_cleanup, "checkpoint_run_lock", side_effect=OSError("lock unavailable")):
            result = self.assemble(delete_checkpoints_after_assembly=True)
        self.assertTrue(Path(result["result"][0]).is_file())
        self.assertIn("lock unavailable", result["ui"]["text"][0])
        self.assertTrue(all(self.exists(s) for s in self.segments))

    def test_encoder_failure_keeps_checkpoint_payloads(self):
        with patch.object(self.chain, "_run_ffmpeg", side_effect=RuntimeError("encode failed")), patch.object(self.chain, "av", None):
            with self.assertRaises(RuntimeError):
                self.assemble(delete_checkpoints_after_assembly=True)
        self.assertTrue(all(self.exists(s) for s in self.segments))


if __name__ == "__main__":
    unittest.main()
