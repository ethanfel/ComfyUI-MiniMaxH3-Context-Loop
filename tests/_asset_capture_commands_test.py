"""Saved frame identity, publication, replay and ownership; CPU/temp media only."""
import asyncio
import copy
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from aiohttp import web
from _project_asset_manager_unit_test import ACTIVE, chain


class CaptureCommands(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        ACTIVE["root"] = str(self.root)
        for kind in ("input", "output", "temp"):
            (self.root / kind).mkdir()
        self.store = chain._project_asset_store()
        self.video = self.root / "output" / "saved.mkv"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=64x48:r=24:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=64x48:r=24:d=1",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "ffv1", str(self.video)],
            check=True, timeout=20)
        self.revision = "a" * 32
        self.metadata = self.root / "output" / "h3_chains" / "episode" / "checkpoints" / ("clip_0001.%s.json" % self.revision)
        self.metadata.parent.mkdir(parents=True)
        self.metadata.write_text(json.dumps({"run_name": "episode", "segment": {"index": 1,
            "revision": self.revision, "id": "arrival", "segment": "saved.mkv",
            "segment_sha256": chain._file_sha256(str(self.video)), "delivered_frames": 48}}))
        self.body = {"command_version": 1, "project": "episode", "action": "asset_capture",
            "operation_id": "b" * 32, "source": {"scene": 1, "revision": self.revision,
                "branch_id": "main", "file": {"filename": "saved.mkv", "subfolder": "", "type": "output"}},
            "time_seconds": 1.25, "tag": "arrival", "folder_id": ""}
        self.proof = None
        self.review()

    def review(self):
        value = chain.inspect_capture(self.store, "episode", self.body, chain._saved_capture_source)
        self.body.update({key: value[key] for key in ("base_revision", "preview_revision")})
        return value

    def command(self):
        return chain.command_capture(self.store, "episode", self.body, chain._saved_capture_source,
            chain._capture_video_frame, lambda: chain._project_write_commit_guard("episode", self.proof, "capture"))

    def claim(self, owner="capture-owner-a-1234567890", force=False):
        value = chain.claim_project_ownership(str(self.root / "output"), "episode", owner, "test", force=force)
        return {"owner_id": owner, "epoch": value["epoch"]}

    def test_pixels_provenance_single_publication_and_idempotent_replay(self):
        self.body["folder_id"] = self.store.create_folder("episode", "Captures")["folder"]["id"]
        self.review()
        before = self.video.read_bytes()
        with patch.object(self.store, "_save_catalog", wraps=self.store._save_catalog) as save:
            result = self.command()
            self.assertEqual(save.call_count, 1)
        entry = result["catalog"]["assets"][0]
        with Image.open(self.store.asset("episode", entry["id"])[1]) as frame:
            red, green, blue = frame.convert("RGB").getpixel((20, 20))
            self.assertGreater(blue, 240)
            self.assertLess(red + green, 10)
        self.assertEqual(entry["source_origin"]["revision"], self.revision)
        self.assertEqual(entry["source_origin"]["time_seconds"], 1.25)
        self.assertEqual(entry["folder_id"], self.body["folder_id"])
        self.assertEqual(entry["source_kind"], "frame_capture")
        self.assertEqual(before, self.video.read_bytes())
        with patch.object(chain, "_capture_video_frame") as extract:
            self.assertTrue(self.command()["replayed"])
            extract.assert_not_called()
        self.assertEqual(len(self.store.load("episode")["assets"]), 1)
        self.body["time_seconds"] = 0.5
        with self.assertRaises(ValueError):
            self.command()

    def test_review_rejects_missing_wrong_or_changed_saved_source(self):
        original = copy.deepcopy(self.body)
        for change in ({"time_seconds": True}, {"time_seconds": -1}, {"time_seconds": 2},
                {"source": {**original["source"], "revision": "c" * 32}},
                {"source": {**original["source"], "file": {"filename": "saved.mkv", "subfolder": "../output", "type": "input"}}}):
            self.body = {**original, **change}
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.review()
        self.body = original
        self.metadata.write_text(self.metadata.read_text() + " ")
        with self.assertRaises(chain.ProjectAssetConflictError):
            self.command()
        self.assertEqual(self.store.load("episode")["assets"], [])

    def test_stale_library_and_takeover_during_extraction_do_not_publish(self):
        self.store.create_folder("episode", "Changed")
        with patch.object(chain, "_capture_video_frame") as extract:
            with self.assertRaises(chain.ProjectAssetConflictError):
                self.command()
            extract.assert_not_called()
        self.body["operation_id"] = "c" * 32
        self.review()
        self.proof = self.claim()
        extract = chain._capture_video_frame
        def takeover(*args):
            extract(*args)
            self.claim("capture-owner-b-1234567890", force=True)
        with patch.object(chain, "_capture_video_frame", takeover):
            with self.assertRaises(chain.ProjectOwnershipError):
                self.command()
        self.assertEqual(self.store.load("episode")["assets"], [])
        self.assertFalse(list((self.root / "input").rglob("*.png")))

    def test_prepared_frame_survives_restart_and_source_removal(self):
        with patch.object(self.store, "_save_catalog", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                self.command()
        self.store = chain._project_asset_store()
        pending = chain.inspect_library(self.store, "episode", self.body["operation_id"])["pending_operation"]
        self.assertEqual(pending["phase"], "prepared")
        self.body = pending["request"]
        self.video.unlink()
        with patch.object(chain, "_capture_video_frame") as extract:
            self.command()
            extract.assert_not_called()
        self.assertEqual(len(self.store.load("episode")["assets"]), 1)
        self.assertEqual(self.store.public_catalog("episode")["library_pending_operations"], [])

    def test_native_review_media_is_matched_to_its_revision_and_branch(self):
        directory = self.root / "output" / "h3_chains" / "episode" / "reviews"
        directory.mkdir()
        name = "clip_0001.%s.example.review.mp4" % chain._file_sha256(str(self.video))[:12]
        preview = directory / name
        preview.write_bytes(self.video.read_bytes())
        self.body["source"]["file"] = {"filename": name, "subfolder": "h3_chains/episode/reviews", "type": "output"}
        self.review()
        self.command()
        origin = self.store.load("episode")["assets"][0]["source_origin"]
        self.assertEqual(origin["file"]["filename"], name)
        self.body["source"]["branch_id"] = "d" * 32
        with self.assertRaises(ValueError):
            self.review()
        self.body["source"]["branch_id"] = "main"
        unrelated = directory / "unrelated.mp4"
        unrelated.write_bytes(preview.read_bytes())
        self.body["source"]["file"]["filename"] = unrelated.name
        with self.assertRaises(ValueError):
            self.review()

    def test_video_changes_or_extraction_failure_never_publish_partial_assets(self):
        extract = chain._capture_video_frame
        original = self.video.read_bytes()
        def replace_source(*args):
            extract(*args)
            self.video.write_bytes(self.video.read_bytes() + b"changed")
        with patch.object(chain, "_capture_video_frame", replace_source):
            with self.assertRaises(chain.ProjectAssetConflictError):
                self.command()
        self.assertEqual(self.store.load("episode")["assets"], [])
        with self.assertRaises(chain.ProjectAssetConflictError):
            self.review()
        self.video.write_bytes(original)
        self.body["operation_id"] = "d" * 32
        self.review()
        with patch.object(chain, "_capture_video_frame", side_effect=RuntimeError("no frame")):
            with self.assertRaisesRegex(ValueError, "no frame"):
                self.command()
        self.assertEqual(self.store.public_catalog("episode")["library_pending_operations"], [])

    def test_capture_numbering_and_saved_receipt_survive_a_lost_commit_acknowledgement(self):
        save = self.store._save_catalog
        def lost(catalog):
            save(catalog)
            raise OSError("lost acknowledgement")
        with patch.object(self.store, "_save_catalog", lost):
            with self.assertRaises(OSError):
                self.command()
        self.store = chain._project_asset_store()
        status = chain.inspect_library(self.store, "episode", self.body["operation_id"])
        self.assertEqual(status["receipt"]["action"], "asset_capture")
        self.assertTrue(self.command()["replayed"])
        self.body["operation_id"] = "d" * 32
        self.review()
        self.command()
        self.assertEqual([entry["tag"] for entry in self.store.load("episode")["assets"]], ["arrival", "arrival1"])

    def test_route_ownership_and_uncertain_commit_receipt(self):
        proof = self.claim()
        body = self.body
        class Request:
            headers = {}
            async def json(self):
                return body
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch.object(chain.asyncio, "to_thread", inline), patch.object(chain, "web", web):
            self.assertEqual(asyncio.run(chain._project_asset_library(Request())).status, 423)
            Request.headers = {"X-H3-Workflow-Owner": proof["owner_id"], "X-H3-Ownership-Epoch": str(proof["epoch"])}
            result = asyncio.run(chain._project_asset_library(Request()))
            self.assertEqual(result.status, 200, result.text)
            self.assertEqual(json.loads(result.text)["receipt"]["action"], "asset_capture")


if __name__ == "__main__":
    unittest.main()
