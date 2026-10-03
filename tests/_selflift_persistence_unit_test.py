"""SelfLift publishes bundles/previews with writable Windows flush handles.

Real temporary safetensors/MP4 files, CPU tensors and synthetic frames only.
The Windows flush contract is simulated; no model, GPU or real run is used.
"""
from contextlib import contextmanager
import errno
import importlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av
import numpy as np

import _selflift_hunt_unit_test as h


persistence = importlib.import_module(h.PACKAGE + ".processing_persistence")


class SelfLiftPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="h3-selflift-sync-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tensor = h.torch.arange(24, dtype=h.torch.float32).reshape(2, 3, 4)

    def save(self, kind, path):
        if kind == "bundle":
            h.store_module.save_bundle(path, {"samples": self.tensor, "seed": 2 ** 64 - 1})
        else:
            frames = [np.full((32, 32, 3), value, dtype=np.uint8) for value in (0, 64, 128)]
            with patch.object(h.preview, "decoder_loader", return_value=lambda name: object()), \
                    patch.object(h.preview, "preview_frames", return_value=iter(frames)):
                h.preview.save_preview(None, path, "synthetic", raw_frames=3, trim_frames=1)

    @contextmanager
    def observe_publication(self, platform, failure=None, stage="sync"):
        opened, events, synced_bytes = [], [], []
        real_fsync, real_replace = os.fsync, os.replace

        def recording_open(path, mode="r", *args, **kwargs):
            handle = open(path, mode, *args, **kwargs)
            opened.append(handle)
            return handle

        def fsync(fd):
            handle = next(handle for handle in opened if not handle.closed and handle.fileno() == fd)
            if platform == "nt" and not handle.writable():
                raise OSError(errno.EBADF, "Windows flush needs write access")
            self.assertEqual(handle.writable(), platform == "nt")
            payload = handle.read()
            self.assertTrue(payload, "The flush handle must not truncate the completed artifact")
            synced_bytes.append(payload)
            if failure is not None and stage == "sync":
                raise failure
            # A simulated POSIX read-only descriptor cannot be flushed by a
            # native Windows host; the writable Windows case always can.
            if platform == "nt" or os.name != "nt":
                real_fsync(fd)
            events.append("sync")

        def replace(source, destination):
            self.assertTrue(all(handle.closed for handle in opened), "Close the flush handle before rename")
            self.assertEqual(events, ["sync"], "Never publish before successful file sync")
            if failure is not None and stage == "replace":
                raise failure
            real_replace(source, destination)
            events.append("replace")

        def sync_directory(path):
            self.assertEqual(path, self.root)
            self.assertEqual(events, ["sync", "replace"])
            events.append("directory")

        # Patch module-local OS views, not global os.name/Path platform selection.
        # Track both old Path.open and the shared helper's open, so the old
        # read-only call sites reproduce EBADF instead of evading the regression.
        with patch.object(persistence, "os", SimpleNamespace(name=platform, fsync=fsync)), \
                patch.object(persistence, "open", new=recording_open, create=True), \
                patch.object(Path, "open", new=recording_open), \
                patch.object(h.store_module, "os", SimpleNamespace(fsync=fsync, replace=replace)), \
                patch.object(h.preview, "os", SimpleNamespace(fsync=fsync, replace=replace)), \
                patch.object(h.store_module, "_sync_directory", side_effect=sync_directory):
            yield opened, events, synced_bytes

    def assert_saved(self, kind):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform):
                path = self.root / ("take.safetensors" if kind == "bundle" else "preview.mp4")
                with self.observe_publication(platform) as (opened, events, payloads):
                    self.save(kind, path)
                self.assertEqual(events, ["sync", "replace", "directory"])
                self.assertEqual(len(opened), 1)
                self.assertEqual(opened[0].mode, "rb+" if platform == "nt" else "rb")
                self.assertTrue(opened[0].closed)
                self.assertEqual(path.read_bytes(), payloads[0])
                self.assertEqual(list(self.root.iterdir()), [path])
                if kind == "bundle":
                    restored = h.store_module.load_bundle(path)
                    h.torch.testing.assert_close(restored["samples"], self.tensor, rtol=0, atol=0)
                    self.assertEqual(restored["seed"], 2 ** 64 - 1)
                else:
                    with av.open(str(path)) as container:
                        frames = list(container.decode(video=0))
                    self.assertEqual(len(frames), 2, "Context trim still applies to the MP4 preview")
                    self.assertTrue(all((frame.width, frame.height) == (32, 32) for frame in frames))

    def test_bundle_windows_and_posix_publication(self):
        self.assert_saved("bundle")

    def test_preview_windows_and_posix_publication(self):
        self.assert_saved("preview")

    def test_io_failures_preserve_previous_artifact_and_remove_temporary_files(self):
        for kind in ("bundle", "preview"):
            for platform in ("nt", "posix"):
                for stage, error_number in (("sync", errno.EBADF), ("sync", errno.EIO),
                                            ("sync", errno.ENOSPC), ("replace", errno.EACCES)):
                    with self.subTest(kind=kind, platform=platform, stage=stage, errno=error_number):
                        path = self.root / "previous-artifact"
                        original = b"Keep the previous committed artifact intact"
                        path.write_bytes(original)
                        failure = OSError(error_number, "simulated publication failure")
                        with self.observe_publication(platform, failure, stage) as (opened, events, _):
                            with self.assertRaises(OSError) as caught:
                                self.save(kind, path)
                        self.assertIs(caught.exception, failure, "Real I/O errors must not be swallowed")
                        self.assertEqual(events, [] if stage == "sync" else ["sync"])
                        self.assertTrue(opened and all(handle.closed for handle in opened))
                        self.assertEqual(path.read_bytes(), original)
                        self.assertEqual(list(self.root.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
