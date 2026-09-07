"""Chapter recovery pins and edited-vs-source timeline regression tests."""
import copy
import asyncio
from contextlib import contextmanager
from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _checkpoint_revision_unit_test import chain, folder_paths, write_revision

retirement = import_module(chain.__package__ + ".chapter_snapshot_retirement")


class ChapterRecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder_paths.output_directory = self.temp.name
        self.run = Path(self.temp.name) / "h3_chains" / "revision_test"
        self.old = "a" * 32
        self.metadata, self.files = write_revision(
            self.run, 1, self.old, 1, active=True, audio_context_length=0)
        segment = self.metadata["segment"]
        self.manifest = {
            "format": "h3_chain_manifest_v3", "run_name": "revision_test",
            "plan_hash": "test", "compatibility": {"continuation_mode": "guide"},
            "clip_count": 1, "segments": [segment],
            "total_delivered_frames": segment["delivered_frames"],
        }
        write_revision(self.run, 1, "b" * 32, 2, active=True, audio_context_length=0)
        self.manager = chain.CheckpointGraphManager(self.temp.name)

    def test_sealing_invalidates_delete_preview_and_preserves_recovery(self):
        before = self.manager.deletion_preview("revision_test", 1, self.old)
        self.assertTrue(before["allowed"])
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        pinned = self.manager.deletion_preview("revision_test", 1, self.old)
        self.assertFalse(pinned["allowed"])
        self.assertEqual(pinned["chapter_references"][0]["snapshot"], snapshot["chapter_manifest_id"])
        self.assertIn("Sealed Chapter 1", pinned["blockers"][0])
        with self.assertRaises(ValueError):
            self.manager.delete("revision_test", 1, self.old, before["snapshot"])
        recovered, _ = chain._load_chapter_manifest("revision_test", 1, snapshot["chapter_manifest_id"])
        self.assertEqual(recovered["segments"][0]["revision"], self.old)
        self.assertTrue(Path(path).is_file())
        self.assertTrue(self.manager.deletion_preview("revision_test", 1, "b" * 32)["allowed"])

    def test_selected_alternate_and_archive_paths_are_pinned(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        candidate = "c" * 32
        metadata, _ = write_revision(self.run, 1, candidate, 3, audio_context_length=0,
                                     recovery_archive=True)
        for references in [
            {"editorial": {"replacements": [{"scene": 1, "alternate_revision": candidate, "base_revision": self.old}]}},
            # A legacy snapshot may identify an input by path, without its revision.
            {"archives": {"workflow": metadata["archives"]["workflow"]}},
        ]:
            with self.subTest(references=references):
                modified = copy.deepcopy(snapshot)
                modified.update(references)
                Path(path).write_text(json.dumps(modified))
                preview = self.manager.deletion_preview("revision_test", 1, candidate)
                self.assertFalse(preview["allowed"])
                self.assertTrue(preview["chapter_references"])

    def test_unreadable_snapshot_fails_closed(self):
        _, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        Path(path).write_text("{incomplete")
        preview = self.manager.deletion_preview("revision_test", 1, "b" * 32)
        self.assertFalse(preview["allowed"])
        self.assertIn("Cannot verify sealed chapter", preview["blockers"][0])

    def test_supersedes_history_is_not_a_recovery_dependency(self):
        current = json.loads((self.run / "checkpoints/clip_0001.json").read_text())["segment"]
        current["supersedes"] = self.metadata["segment"]["revision_metadata"]
        manifest = dict(self.manifest, segments=[current])
        snapshot, _ = chain._chapter_manifest_from_manifest(manifest, 1)
        self.assertEqual(snapshot["segments"][0]["supersedes"], current["supersedes"])
        preview = self.manager.deletion_preview("revision_test", 1, self.old)
        self.assertTrue(preview["allowed"])
        self.manager.delete("revision_test", 1, self.old, preview["snapshot"])
        recovered, _ = chain._load_chapter_manifest("revision_test", 1, snapshot["chapter_manifest_id"])
        self.assertEqual(recovered["segments"][0]["revision"], "b" * 32)

    def test_retirement_archives_only_snapshot_and_releases_only_its_pins(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        original_bytes = Path(path).read_bytes()
        manager = retirement.ChapterSnapshotManager(self.temp.name)
        address = snapshot["chapter_manifest_path"]
        preview = manager.retirement_preview("revision_test", address)
        self.assertFalse(preview["scenes"][0]["active"])
        before = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        result = manager.retire("revision_test", address, preview["snapshot"])
        archive = Path(self.temp.name) / result["retired_path"]
        self.assertEqual(archive.read_bytes(), original_bytes)
        self.assertFalse(Path(path).exists())
        for p, content in before.items():
            if p != Path(path):
                self.assertEqual(p.read_bytes(), content)
        self.assertTrue(self.manager.deletion_preview("revision_test", 1, self.old)["allowed"])
        with self.assertRaisesRegex(FileNotFoundError, "No sealed"):
            chain._load_chapter_manifest("revision_test", 1, snapshot["chapter_manifest_id"])
        # An in-memory snapshot cannot silently recreate the retired pin.
        with self.assertRaisesRegex(ValueError, "retired"):
            chain._persist_chapter_manifest(snapshot)
        self.assertFalse(Path(path).exists())
        # Archival is reversible while its recovery inputs remain intact.
        archive.rename(path)
        recovered, _ = chain._load_chapter_manifest("revision_test", 1, snapshot["chapter_manifest_id"])
        self.assertEqual(recovered["segments"][0]["revision"], self.old)

    def test_other_snapshot_and_shared_branch_dependencies_still_block(self):
        first, _ = chain._chapter_manifest_from_manifest(self.manifest, 1)
        other, _ = chain._chapter_manifest_from_manifest(dict(self.manifest, plan_hash="other"), 1)
        child, child_files = write_revision(self.run, 2, "c" * 32, 3,
                                             predecessor=self.metadata)
        manager = retirement.ChapterSnapshotManager(self.temp.name)
        for snapshot in (first, other):
            address = snapshot["chapter_manifest_path"]
            preview = manager.retirement_preview("revision_test", address)
            manager.retire("revision_test", address, preview["snapshot"])
            blocked = self.manager.deletion_preview("revision_test", 1, self.old)
            self.assertFalse(blocked["allowed"])
            self.assertEqual(blocked["dependents"][0]["revision"], child["segment"]["revision"])
            self.assertEqual(len(blocked["chapter_references"]), 1 if snapshot is first else 0)
        self.assertTrue(all(path.exists() for path in child_files))
        # Only a subsequent, explicit leaf-first deletion removes any clips.
        preview = self.manager.deletion_preview("revision_test", 2, "c" * 32)
        self.manager.delete("revision_test", 2, "c" * 32, preview["snapshot"])
        self.assertTrue(self.manager.deletion_preview("revision_test", 1, self.old)["allowed"])

    def test_retirement_rejects_missing_and_stale_confirmations(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        manager = retirement.ChapterSnapshotManager(self.temp.name)
        address = snapshot["chapter_manifest_path"]
        preview = manager.retirement_preview("revision_test", address)
        for token in ("", "wrong"):
            with self.assertRaises(retirement.CheckpointDeleteBlocked):
                manager.retire("revision_test", address, token)
        snapshot["sealed_at"] = "changed after preview"
        Path(path).write_text(json.dumps(snapshot))
        with self.assertRaises(retirement.CheckpointDeleteBlocked):
            manager.retire("revision_test", address, preview["snapshot"])
        preview = manager.retirement_preview("revision_test", address)
        write_revision(self.run, 1, "d" * 32, 4, active=True, audio_context_length=0)
        with self.assertRaises(retirement.CheckpointDeleteBlocked):
            manager.retire("revision_test", address, preview["snapshot"])
        self.assertTrue(Path(path).is_file())

    def test_retirement_refuses_invalid_paths_identity_and_symlinks(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        manager = retirement.ChapterSnapshotManager(self.temp.name)
        address = snapshot["chapter_manifest_path"]
        for target in ("../escape.json", str(path), address.replace("revision_test", "foreign"),
                       address.replace("/manifests/", "/./manifests/"), "", None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                manager.retirement_preview("revision_test", target)
        Path(path).write_text(json.dumps({**snapshot, "run_name": "foreign"}))
        with self.assertRaisesRegex(ValueError, "identity"):
            manager.retirement_preview("revision_test", address)
        Path(path).write_text(json.dumps({**snapshot, "clip_count": 999}))
        with self.assertRaisesRegex(ValueError, "identity"):
            manager.retirement_preview("revision_test", address)
        Path(path).write_text(json.dumps(snapshot))
        archive = Path(path).parent.parent / "retired_manifests"
        archive.symlink_to(Path(path).parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            manager.retirement_preview("revision_test", address)
        archive.unlink()
        real = Path(path).with_suffix(".backup")
        Path(path).rename(real)
        Path(path).symlink_to(real)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            manager.retirement_preview("revision_test", address)

    def test_retirement_does_not_overwrite_archive_and_failed_move_keeps_pin(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        manager = retirement.ChapterSnapshotManager(self.temp.name)
        address = snapshot["chapter_manifest_path"]
        preview = manager.retirement_preview("revision_test", address)
        with patch.object(retirement.os, "rename", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                manager.retire("revision_test", address, preview["snapshot"])
        self.assertFalse(self.manager.deletion_preview("revision_test", 1, self.old)["allowed"])
        destination = Path(self.temp.name) / preview["retired_path"]
        destination.write_bytes(b"existing archive")
        with self.assertRaisesRegex(ValueError, "not be overwritten"):
            manager.retire("revision_test", address, preview["snapshot"])
        self.assertEqual(destination.read_bytes(), b"existing archive")
        self.assertTrue(Path(path).is_file())

    def test_retirement_route_requires_ownership_and_preview_confirmation(self):
        snapshot, path = chain._chapter_manifest_from_manifest(self.manifest, 1)
        guards = []

        @contextmanager
        def guard(root, run, proof, action):
            guards.append(proof)
            yield

        class Request:
            path = "/chapter-snapshots/retire-preview"
            body = {"run_name": "revision_test", "path": snapshot["chapter_manifest_path"]}

            async def json(self):
                return self.body

        request = Request()
        call = lambda: asyncio.run(chain._chapter_snapshot_retirement(request))
        with patch.object(chain, "_request_project_ownership", return_value="owner-proof"), \
                patch.object(chain, "project_write_guard", side_effect=guard), \
                patch.object(chain, "_project_write_rejection") as reject:
            reject.return_value = chain.web.json_response({"error": "read only"}, status=423)
            response = call()
            self.assertEqual(response.status, 200)
            preview = json.loads(response.body)
            self.assertFalse(reject.called)
            self.assertEqual(guards, [])
            request.path = "/chapter-snapshots/retire"
            self.assertEqual(call().status, 423)
            reject.return_value = None
            self.assertEqual(call().status, 409)
            self.assertTrue(Path(path).exists())
            request.body["snapshot"] = preview["snapshot"]
            self.assertEqual(call().status, 200)
            self.assertIn("owner-proof", guards)
            self.assertFalse(Path(path).exists())
            self.assertEqual(call().status, 404)
            request.body = []
            self.assertEqual(call().status, 400)

    def test_deleted_input_cannot_be_sealed_from_stale_selection(self):
        preview = self.manager.deletion_preview("revision_test", 1, self.old)
        self.manager.delete("revision_test", 1, self.old, preview["snapshot"])
        scoped = dict(self.manifest, format=chain.CHAPTER_MANIFEST_FORMAT,
                      chapter={"number": 1, "id": "one", "title": "One"})
        with self.assertRaises((ValueError, FileNotFoundError)):
            chain._persist_chapter_manifest(scoped)
        self.assertEqual(list(self.run.glob("chapters/*/manifests/*.json")), [])

    def test_chapter_origin_uses_full_edited_timeline_not_raw_audio_offset(self):
        segments = []
        for index in range(1, 9):
            metadata, _ = write_revision(self.run, index, "%032x" % index, index,
                                         audio_context_length=0)
            segment = metadata["segment"]
            segment["raw_frames"] = segment["delivered_frames"] = 13
            segments.append(segment)
        editorial = {
            "format": "h3_chain_editorial_v1", "run_name": "revision_test",
            "scene_order": [{"scene": i, "scene_id": s["id"]} for i, s in enumerate(segments, 1)],
            "chapters": [{"id": "one", "title": "One", "start_scene": 1, "start_scene_id": segments[0]["id"]},
                         {"id": "two", "title": "Two", "start_scene": 5, "start_scene_id": segments[4]["id"]}],
            "trims": [{"scene": 1, "scene_id": segments[0]["id"], "out_frame": 9}],
            "placements": [],
            "subtitles": {"mode": "preview_srt", "asset_id": "song", "offset_seconds": 0},
        }
        manifest = dict(self.manifest, segments=segments, clip_count=8,
                        total_delivered_frames=104, editorial=editorial)
        for gap in (False, True):
            if gap:
                # Shift the whole timeline: collision resolution pushes all following scenes.
                editorial["placements"] = [{"scene": i, "scene_id": s["id"], "start_frame": 24 + 13 * (i - 1)}
                                            for i, s in enumerate(segments, 1)]
            _, records, _ = chain._editorial_timeline_records("revision_test", segments, editorial)
            expected = next(r["start_frame"] for r in records if r.get("scene") == 5)
            chapter, _ = chain._chapter_manifest_from_manifest(manifest, 2)
            self.assertEqual(chapter["chapter"]["editorial_origin_frame"], expected)
            self.assertEqual(chapter["chapter"]["source_start_frame"], 52)
            if not gap:
                self.assertEqual(expected, 48)
            self.assertEqual(chapter["editorial"]["placements"][0]["start_frame"], 0)
            with patch.object(chain, "ProjectAssetStore") as store:
                store.return_value.load.return_value = {"assets": [{"id": "song", "kind": "audio",
                    "lyrics": "[00:02.50]Line one\n[00:03.50]Line two"}]}
                cues = chain._editorial_subtitle_cues("revision_test", chapter["editorial"], 52,
                                                     timeline_origin_frames=expected)
                if not gap:
                    self.assertAlmostEqual(cues[0]["start"], 0.5)


if __name__ == "__main__":
    unittest.main()
