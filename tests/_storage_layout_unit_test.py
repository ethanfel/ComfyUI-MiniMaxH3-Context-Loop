"""Pure layout policy and byte-compatible legacy paths, no runtime migration."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from storage_layout import (OrganizedStorageLayout, storage_stage, workflow_storage_route,
                            MEDIA_STAGES, DISPOSABLE_GROUPS)
from storage_legacy import LegacyStoragePaths

TAKE = "1" * 32
BRANCH = "a" * 32
PASS = "b" * 32


class LegacyPathTests(unittest.TestCase):
    def test_original_named_branch_shared_generation_and_private_pointers(self):
        project = os.path.abspath("test-output/h3_chains/demo")
        original = LegacyStoragePaths(project, project)
        working = os.path.join(project, "branches", BRANCH)
        named = LegacyStoragePaths(project, working)
        for index in (1, 5, 7):
            old = {
                "run_dir": project,
                "segment": os.path.join(project, "segments", "clip_%04d.mp4" % index),
                "blend_segment": os.path.join(project, "blend_segments", "clip_%04d.mp4" % index),
                "generated_audio": os.path.join(project, "generated_audio", "clip_%04d.wav" % index),
                "checkpoint": os.path.join(project, "checkpoints", "clip_%04d.safetensors" % index),
                "metadata": os.path.join(project, "checkpoints", "clip_%04d.json" % index),
            }
            self.assertEqual(original.generation(index), old)
            self.assertEqual(named.generation(index), {**old,
                "metadata": os.path.join(working, "checkpoints", "clip_%04d.json" % index)})
        self.assertEqual(named.archives(TAKE), original.archives(TAKE))
        self.assertNotEqual(named.archives(), original.archives())

    def test_processing_chapters_and_png_legacy_names_are_exact(self):
        project = os.path.abspath("test-output/h3_chains/demo")
        for branch in (project, os.path.join(project, "branches", BRANCH)):
            paths = LegacyStoragePaths(project, branch)
            for chapter in (None, "01_chapter_01"):
                delivery = paths.chapter(chapter)
                expected = branch if chapter is None else os.path.join(branch, "chapters", chapter)
                self.assertEqual(delivery, expected)
                profile = paths.processing_profile(delivery, "h3_video_dlss5")
                self.assertEqual(profile, os.path.join(expected, "upscaled", "h3_video_dlss5"))
                files = paths.processing_files(profile, 5)
                self.assertEqual(files, {
                    "root": profile,
                    "segment": os.path.join(profile, "segments", "clip_0005.mp4"),
                    "checkpoint": os.path.join(profile, "checkpoints", "clip_0005.safetensors"),
                    "metadata": os.path.join(profile, "checkpoints", "clip_0005.json"),
                    "prompt": os.path.join(profile, "prompts", "clip_0005.txt"),
                    "audio": os.path.join(profile, "audio", "clip_0005.wav"),
                    "manifest": os.path.join(profile, "upscale_manifest.json"),
                    "partial": os.path.join(profile, "partial", "through_clip_0005.manifest.json"),
                    "final": os.path.join(profile, "final"),
                })
                self.assertEqual(paths.png_sequence(profile, "DLSS_upscale_dmd_2"),
                                 os.path.join(profile, "frames", "DLSS_upscale_dmd_2"))

    def test_reader_and_deletion_scope_parser_agree_without_weakening_guards(self):
        for branch in ("", "branches/" + BRANCH + "/"):
            for chapter in ("", "chapters/01_chapter_01/"):
                profile = "h3_chains/demo/" + branch + chapter + "upscaled/hq"
                address = profile + "/checkpoints/clip_0005." + TAKE + ".json"
                for text in (address, address.replace("/", "\\")):
                    self.assertEqual(LegacyStoragePaths.processing_metadata(text, "demo"), {
                        "metadata": address, "profile_root": profile})
                relative = tuple((chapter + "upscaled/hq").split("/"))
                self.assertTrue(LegacyStoragePaths.is_processing_profile(relative))
        for path in ("../x", "h3_chains/other/upscaled/hq/checkpoints/clip_0005." + TAKE + ".json",
                     "h3_chains/demo/upscaled/hq/checkpoints/clip_0005.json",
                     "h3_chains/demo/upscaled/hq/nested/checkpoints/clip_0005." + TAKE + ".json",
                     "h3_chains/demo/branches/not-an-id/upscaled/hq/checkpoints/clip_0005." + TAKE + ".json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                LegacyStoragePaths.processing_metadata(path, "demo")
        with self.assertRaises(ValueError):
            LegacyStoragePaths("/project", "/project").chapter("../escape")


class OrganizedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "not-created"
        self.layout = OrganizedStorageLayout(str(self.root))

    def test_three_areas_and_no_directories_created_even_for_optional_data(self):
        paths = []
        for stage in MEDIA_STAGES:
            paths.extend(self.layout.media(stage, TAKE,
                pass_id=None if stage in ("generation", "alternate") else PASS).values())
        paths += [self.layout.take_metadata(TAKE), self.layout.take_prompt(TAKE),
                  self.layout.export_file("png", TAKE, "frame", frame_number=123),
                  self.layout.export_file("video", TAKE, "video"),
                  self.layout.project_data("recovery", TAKE, "plan.json")]
        paths += [self.layout.optional(group, "fixture.png") for group in DISPOSABLE_GROUPS]
        self.assertEqual({path.split("/")[0] for path in paths}, {"project", "media", "exports"})
        self.assertTrue(all(len(Path(path).parts) <= 5 for path in paths))
        self.assertFalse(self.root.exists(), "Constructing paths must not create an empty folder forest")
        self.assertNotIn("branches", self.layout.export("png", TAKE))
        self.assertNotIn("chapters", self.layout.export("png", TAKE))

    def test_audio_only_exports_and_versioned_soundtracks_remain_short(self):
        self.assertEqual(self.layout.export_file('audio',TAKE,'audio'), 'exports/audio/'+TAKE+'/audio.wav')
        first = self.layout.export_file('audio',TAKE,'audio',revision=BRANCH)
        second = self.layout.export_file('audio',TAKE,'audio',revision=PASS)
        self.assertNotEqual(first,second)
        self.assertEqual(Path(first).parent,Path(second).parent)
        self.assertEqual(len(Path(first).parts),4)
        self.assertFalse(self.root.exists())
        with self.assertRaises(ValueError):
            self.layout.export_file('audio',TAKE,'audio',revision='../escape')
        with self.assertRaises(ValueError):
            self.layout.export_file('png',TAKE,'frame',revision=BRANCH)

    def test_all_processing_semantics_not_profile_labels(self):
        self.assertEqual(storage_stage(), "generation")
        self.assertEqual(storage_stage(take_kind="editorial_alternate"), "alternate")
        cases = [({"backend": "h3_latent"}, "latent_upscale"),
                 ({"backend": "pixel"}, "pixel_upscale"),
                 ({"backend": "ltx_2_5"}, "video_refine"),
                 ({"backend": "custom"}, "custom"),
                 ({"backend": "pixel", "recipe": {"derope": True}}, "derope"),
                 ({"backend": "pixel", "recipe": {"experimental": "lms_v1"}}, "pixel_upscale"),
                 ({"backend": "h3_latent", "recipe": {"stage": "derope"}}, "derope")]
        for config, expected in cases:
            before = json.dumps(config, sort_keys=True)
            self.assertEqual(storage_stage(profile_config=config), expected)
            self.assertEqual(json.dumps(config, sort_keys=True), before)
        with self.assertRaises(ValueError):
            storage_stage(take_kind="unknown")

    def test_every_shipped_deferred_workflow_has_a_storage_route(self):
        count = 0
        for path in (ROOT / "example_workflows").glob("Deferred*.json"):
            workflow = json.loads(path.read_text())
            routed = 0
            for node in workflow.get("nodes", []):
                if node.get("type") == "MiniMaxH3ChainLatentVideoAdapter":
                    route = workflow_storage_route("external_video")
                    self.assertFalse(route["scene_checkpoints"])
                    self.assertFalse(route["managed_writer"])
                    self.assertIn("required", route["integration"])
                    self.assertTrue(self.layout.export(route["export_kind"], TAKE).startswith("exports/video/"))
                    count += 1
                    routed += 1
                    continue
                if node.get("type") != "MiniMaxH3ChainUpscaleAdapter":
                    continue
                values = node["widgets_values"]
                config = {"backend": values[1], "recipe": json.loads(values[2])}
                route = workflow_storage_route("processing", profile_config=config)
                self.assertIn(route["media_stage"], MEDIA_STAGES)
                self.assertTrue(route["scene_checkpoints"])
                self.assertTrue(self.layout.media(route["media_stage"], TAKE, pass_id=PASS)["video"].startswith("media/" + route["media_stage"] + "/"))
                count += 1
                routed += 1
            self.assertGreater(routed, 0, "No supported storage boundary in " + path.name)
        self.assertGreaterEqual(count, 8, "Do not silently stop testing the shipped workflow set")

    def test_metadata_and_precious_support_are_not_disposable(self):
        self.assertEqual(self.layout.take_metadata(TAKE), "project/takes/" + TAKE + ".json")
        for category in ("recovery", "reference_cache", "assets", "history", "jobs", "branches"):
            self.assertTrue(self.layout.project_data(category, "record.json").startswith("project/"))
            with self.assertRaises(ValueError):
                self.layout.optional(category, "record.json")

    def test_full_ids_distinct_scopes_and_no_short_id_aliases(self):
        # Migrator must allocate different storage IDs for colliding old revision
        # tokens. A branch/profile label is never a filesystem identity here.
        for value in ("1" * 8, "1" * 31, "A" * 32, "../take", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.layout.media("generation", value)
        first = self.layout.media("pixel_upscale", "1" * 32, pass_id=PASS)
        second = self.layout.media("pixel_upscale", "2" * 32, pass_id=PASS)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, self.layout.media("pixel_upscale", "1" * 32, pass_id="c" * 32))
        with self.assertRaises(ValueError):
            self.layout.media("pixel_upscale", TAKE)
        with self.assertRaises(ValueError):
            self.layout.media("generation", TAKE, pass_id=PASS)
        with self.assertRaises(ValueError):
            self.layout.media("profile-label", TAKE)

    def test_path_budget_counts_staging_windows_and_unicode_without_truncation(self):
        root = r"C:\ComfyUI\output\h3_chains\demo"
        layout = OrganizedStorageLayout(root, path_budget=180)
        address = layout.media("latent_upscale", TAKE, pass_id=PASS)["checkpoint"]
        self.assertIn(TAKE, address)
        with self.assertRaises(ValueError):
            layout.check_budget(address, staging_suffix="." + "a" * 64 + ".tmp")
        unicode_root = "/" + "😀" * 70
        with self.assertRaises(ValueError):
            OrganizedStorageLayout(unicode_root, path_budget=150).export("png", TAKE)
        for suffix in ("/escape", "\\escape", ":stream", "\0"):
            with self.assertRaises(ValueError):
                layout.check_budget(address, staging_suffix=suffix)

    def test_png_frames_flat_under_export_and_original_numbers_unchanged(self):
        self.assertEqual(self.layout.export_file("png", TAKE, "frame", frame_number=1806),
                         "exports/png/" + TAKE + "/frame_00001806.png")
        self.assertEqual(self.layout.export_file("png", TAKE, "index"),
                         "exports/png/" + TAKE + "/export.json")
        for number in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                self.layout.export_file("png", TAKE, "frame", frame_number=number)

    def test_containment_and_no_link_or_junction_following(self):
        for address in ("../secret", "/secret", r"C:\secret", "media/a:stream", "media//a"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.layout.native_path(address)
        with self.assertRaises(ValueError):
            self.layout.project_data("assets", "../../secret")
        self.root.mkdir()
        (self.root / "media").symlink_to(self.temp.name, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.layout.native_path("media/video.mp4")


if __name__ == "__main__":
    unittest.main()
