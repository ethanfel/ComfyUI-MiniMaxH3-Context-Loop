"""PR 46 regressions; synthetic media and isolated project directories only."""
import asyncio
import json
import pathlib
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PIL import Image
from aiohttp import web

from _project_asset_manager_unit_test import ACTIVE, chain


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        ACTIVE["root"] = str(self.root)
        for kind in ("input", "output", "temp"):
            (self.root / kind).mkdir()
        self.store = chain._project_asset_store()
        self.video = self.root / "output" / "preview.mkv"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=64x48:r=24:d=1",
            "-c:v", "ffv1", str(self.video),
        ], check=True, timeout=20)

    def capture(self, **kwargs):
        args = dict(project="episode", video_path="preview.mkv", subfolder="",
                    source_type="output", time_seconds=0.5, tag="hero",
                    role="", folder_id=None)
        args.update(kwargs)
        return chain._project_asset_capture_frame_sync(**args)

    def test_capture_pixels_and_numbered_takes(self):
        first = self.capture()
        second = self.capture()
        third = self.capture(tag="hero1")
        self.assertEqual([x["asset"]["tag"] for x in (first, second, third)],
                         ["hero", "hero1", "hero2"])
        path = self.store.asset("episode", first["asset"]["id"])[1]
        with Image.open(path) as image:
            self.assertEqual(image.size, (64, 48))
            red, green, blue = image.convert("RGB").getpixel((30, 20))
            self.assertGreater(red, 240)
            self.assertLess(green + blue, 10)
        self.assertEqual(len(self.store.load("episode")["assets"]), 3)
        self.assertFalse(list((self.root / "input").rglob("*.tmp.png")))

    def test_valid_folder_is_part_of_successful_import(self):
        folder = self.store.create_folder("episode", "Captures")["folder"]
        result = self.capture(folder_id=folder["id"])
        self.assertEqual(result["asset"]["folder_id"], folder["id"])
        self.assertEqual(len(result["catalog"]["assets"]), 1)

    def test_invalid_folder_does_not_leave_an_asset(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.capture(folder_id="deleted-folder")
        self.assertEqual(self.store.load("episode")["assets"], [])

    def test_invalid_times_do_not_capture_a_different_frame(self):
        for offset in (-1, float("nan"), float("inf"), "", None, True, 10 ** 400):
            with self.subTest(offset=offset):
                with self.assertRaises((ValueError, TypeError)):
                    self.capture(time_seconds=offset)
        self.assertEqual(self.store.load("episode")["assets"], [])

    def test_source_is_confined_to_the_requested_root(self):
        other = self.root / "input" / "other.mkv"
        other.write_bytes(self.video.read_bytes())
        for name, sub in ((str(other), ""), ("other.mkv", "../input")):
            with self.subTest(name=name, sub=sub):
                with self.assertRaises(ValueError):
                    self.capture(video_path=name, subfolder=sub)
        link = self.root / "output" / "link.mkv"
        link.symlink_to(other)
        with self.assertRaises(ValueError):
            self.capture(video_path=link.name)

    def test_playlist_is_not_a_capture_source(self):
        playlist = self.root / "output" / "preview.m3u8"
        playlist.write_text("#EXTM3U\n")
        with self.assertRaises(ValueError):
            chain._capture_frame_video_path(playlist.name, "", "output")

    def test_renamed_concat_cannot_dereference_another_video(self):
        disguised = self.root / "output" / "playlist.mp4"
        disguised.write_text("ffconcat version 1.0\nfile 'preview.mkv'\n")
        with self.assertRaisesRegex(RuntimeError, "whitelist"):
            self.capture(video_path=disguised.name)
        self.assertEqual(self.store.load("episode")["assets"], [])

    def test_decode_failure_cleans_up_temporary_images(self):
        def fail(_command, **_kwargs):
            pathlib.Path(_command[-1]).write_bytes(b"partial png")
            raise RuntimeError("decoder failed")

        with patch.object(chain, "_usable_ffmpeg", return_value="ffmpeg"), \
                patch.object(chain, "_run_ffmpeg", fail):
            with self.assertRaisesRegex(RuntimeError, "decoder failed"):
                self.capture()
        self.assertFalse(list((self.root / "input").rglob("*.png")))
        self.assertEqual(self.store.load("episode")["assets"], [])

    def test_capture_endpoint_returns_validation_errors(self):
        class Request:
            async def json(self):
                return {"project": "episode", "filename": "preview.mkv", "time_seconds": -1}

        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        with patch.object(chain.asyncio, "to_thread", inline), patch.object(chain, "web", web):
            response = asyncio.run(chain._project_asset_capture_frame(Request()))
        self.assertEqual(response.status, 400)
        self.assertIn("finite", json.loads(response.text)["error"])

    def test_concurrent_captures_preserve_all_takes(self):
        # The production handler creates a new store per request. Keep only
        # extraction cheap so catalog imports overlap without a GPU or video IO.
        picture = self.root / "frame.png"
        Image.new("RGB", (32, 32), "red").save(picture)

        def cheap_frame(_source, _time, target):
            pathlib.Path(target).write_bytes(picture.read_bytes())

        with patch.object(chain, "_capture_video_frame", cheap_frame):
            self.capture()
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: self.capture(), range(12)))
        self.assertEqual(len(self.store.load("episode")["assets"]), 13)
        self.assertEqual({r["asset"]["tag"] for r in results},
                         {"hero%d" % n for n in range(1, 13)})

    def test_capture_tag_does_not_collide_with_disabled_asset(self):
        first = self.capture()
        self.store.update("episode", first["asset"]["id"], {"enabled": False})
        self.assertEqual(self.capture()["asset"]["tag"], "hero1")

    def test_tag_lookup_does_not_create_projects(self):
        class Request:
            query = {"project": "not-yet-created", "create": "false"}

        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        # Exercise the HTTP handler contract without relying on sandboxed
        # event-loop thread wakeup sockets; concurrency is tested above.
        with patch.object(chain.asyncio, "to_thread", inline), patch.object(chain, "web", web):
            response = asyncio.run(chain._project_asset_catalog(Request()))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)["assets"], [])
        self.assertFalse((self.root / "input" / "h3_projects").exists())


if __name__ == "__main__":
    unittest.main()
