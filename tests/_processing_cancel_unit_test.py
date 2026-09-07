"""Deferred save cancellation, uncertain commits, and disk-only scene resume."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _upscale_chain_unit_test import load_package, folder_paths, torch, av_latent, audio_for_frames
from comfy.model_management import InterruptProcessingException

package, chain, upscale = load_package()
persistence = importlib.import_module(package.__name__ + ".processing_persistence")


class ProcessingCancelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        old_root = folder_paths.output_directory
        self.addCleanup(setattr, folder_paths, "output_directory", old_root)
        folder_paths.output_directory = temporary.name
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [dict(id="scene_%d" % i, prompt="A blue folder.",
                                      length=5, steps=2, seed=str(i)) for i in range(1, 4)]}),
            "cancel_audit", "unit-test", 32, 32, 1, "video", "head", "disabled",
            "generated_audio", 1, 5 / 24, 2, 7, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        self.frames = torch.zeros(5, 32, 32, 3)
        self.originals = []
        for index in range(1, 4):
            delivered = plan["shots"][index - 1]["delivered_frames"]
            self.originals.append(chain.MiniMaxH3ChainSegmentSave().save(
                chain._initial_state(plan, index), self.frames[:delivered], av_latent(0.1),
                audio_for_frames(delivered), denoised_latent=av_latent(0.3))["result"][0])
        self.manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps({
            "run_name": "cancel_audit", "output_mode": "workflow_local", "lineage": [
                {"scene": i, "revision": item["revision"]} for i, item in enumerate(self.originals, 1)]}))[0]

    def adapt(self, stage="pixel", start=1):
        return upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
            self.manifest, stage, "pixel" if stage == "pixel" else "h3_latent",
            '{"derope":true}' if stage == "derope" else "{}",
            start, 0, stage != "pixel", 18)[1]

    def save(self, state):
        return upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            state, self.frames, av_latent(0.6) if state["profile_config"]["save_latent"] else None)

    def prefix(self, stage):
        segments = [self.save(self.adapt(stage, i))["result"][0] for i in (1, 2)]
        before = {self.root / item[key]: (self.root / item[key]).read_bytes()
                  for item in self.originals + segments
                  for key in ("checkpoint", "segment", "revision_metadata", "metadata")}
        return segments, before

    def test_cancel_before_publication_preserves_prefix_and_resumes_from_disk(self):
        for stage in ("derope", "latent", "pixel"):
            with self.subTest(stage=stage):
                segments, before = self.prefix(stage)
                with patch.object(chain, "_write_segment_video", side_effect=InterruptProcessingException()):
                    with self.assertRaises(InterruptProcessingException):
                        self.save(self.adapt(stage, 3))
                resumed = self.adapt(stage, 3)
                self.assertEqual([s["revision"] for s in resumed["segments"]], [s["revision"] for s in segments])
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                self.assertFalse(Path(upscale._state_profile_paths(resumed, 3)["metadata"]).exists())
                upscale._verify_upscale_segment(self.save(resumed)["result"][0], 3)

    def test_failed_manifest_refresh_keeps_saved_scene_and_reports_warning(self):
        original = persistence.atomic_json
        for stage in ("derope", "latent", "pixel"):
            with self.subTest(stage=stage):
                _, before = self.prefix(stage)
                state = self.adapt(stage, 3)
                paths = upscale._state_profile_paths(state, 3)

                def fail_manifest(path, value):
                    if str(path) == paths["manifest"]:
                        raise OSError("network manifest failure")
                    return original(path, value)

                with patch.object(persistence, "atomic_json", side_effect=fail_manifest):
                    result = self.save(state)
                segment, status = result["result"]
                self.assertIn("scene saved; manifest refresh failed", status)
                upscale._verify_upscale_segment(segment, 3)
                self.assertEqual(chain._read_json(paths["metadata"])["segment"], segment)
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                # Reconstruct all saved scenes without depending on the missing manifest.
                self.assertEqual(len(upscale._load_upscale_prefix(state, 4)), 3)

    def test_cancel_or_lost_ack_after_pointer_commit_never_deletes_referenced_media(self):
        for failure in (OSError("lost acknowledgement"), InterruptProcessingException()):
            with self.subTest(failure=type(failure).__name__):
                state = self.adapt()
                paths = upscale._state_profile_paths(state, 1)
                original = persistence.atomic_json

                def fail_after_pointer(path, value):
                    original(path, value)
                    if str(path) == paths["metadata"]:
                        raise failure

                with patch.object(persistence, "atomic_json", side_effect=fail_after_pointer):
                    with self.assertRaises(type(failure)):
                        self.save(state)
                segment = chain._read_json(paths["metadata"])["segment"]
                upscale._verify_upscale_segment(segment, 1)
                self.assertEqual(self.adapt(start=2)["segments"][0]["revision"], segment["revision"])

    def test_failed_pointer_write_retains_new_immutable_take_and_previous_active_take(self):
        state = self.adapt()
        first = self.save(state)["result"][0]
        paths = upscale._state_profile_paths(state, 1)
        previous_pointer = Path(paths["metadata"]).read_bytes()
        original = persistence.atomic_json

        def fail_pointer(path, value):
            if str(path) == paths["metadata"]:
                raise OSError("pointer update failed")
            return original(path, value)

        with patch.object(persistence, "atomic_json", side_effect=fail_pointer):
            with self.assertRaisesRegex(OSError, "pointer update failed"):
                self.save(state)
        self.assertEqual(previous_pointer, Path(paths["metadata"]).read_bytes())
        revisions = list(Path(paths["metadata"]).parent.glob("clip_0001.*.json"))
        self.assertEqual(len(revisions), 2)
        for path in revisions:
            upscale._verify_upscale_segment(chain._read_json(str(path))["segment"], 1)
        self.assertEqual(self.adapt(start=2)["segments"][0]["revision"], first["revision"])

    def test_sync_failure_before_publication_does_not_change_existing_take(self):
        state = self.adapt()
        first = self.save(state)["result"][0]
        paths = upscale._state_profile_paths(state, 1)
        before = {p: p.read_bytes() for p in Path(paths["root"]).rglob("*") if p.is_file()}
        with patch.object(persistence, "sync_file", side_effect=OSError("disk flush failed")):
            with self.assertRaisesRegex(OSError, "disk flush failed"):
                self.save(state)
        self.assertEqual(before, {p: p.read_bytes() for p in Path(paths["root"]).rglob("*") if p.is_file()})
        upscale._verify_upscale_segment(first, 1)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
