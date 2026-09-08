"""File flush handles must preserve artifacts and work on Windows."""

import errno
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "processing_persistence_under_test", ROOT / "processing_persistence.py")
persistence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persistence)


class SyncFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "saved-artifact.bin"
        self.payload = bytes(range(256)) * 32
        self.path.write_bytes(self.payload)
        self.mtime_ns = self.path.stat().st_mtime_ns
        self.opened = []

    def recording_open(self, *args, **kwargs):
        handle = open(*args, **kwargs)
        self.opened.append(handle)
        return handle

    def assert_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.payload)
        self.assertEqual(self.path.stat().st_mtime_ns, self.mtime_ns)
        self.assertTrue(all(handle.closed for handle in self.opened))

    def test_windows_flush_requires_writable_nontruncating_handle(self):
        def windows_fsync(fd):
            self.assertEqual(len(self.opened), 1)
            handle = self.opened[0]
            self.assertEqual(fd, handle.fileno())
            if not handle.writable():
                raise OSError(errno.EBADF, "Windows flush needs write access")
            self.assertEqual(os.fstat(fd).st_size, len(self.payload))
            os.fsync(fd)

        with patch.object(persistence, "os", SimpleNamespace(
                name="nt", fsync=windows_fsync)), patch.object(
                    persistence, "open", side_effect=self.recording_open,
                    create=True) as opener:
            persistence.sync_file(self.path)
        opener.assert_called_once_with(self.path, "r+b")
        self.assert_preserved()

    def test_posix_keeps_read_only_access(self):
        def posix_fsync(fd):
            self.assertFalse(self.opened[0].writable())
            self.assertEqual(fd, self.opened[0].fileno())
            self.assertEqual(os.fstat(fd).st_size, len(self.payload))
            if os.name != "nt":
                os.fsync(fd)

        with patch.object(persistence, "os", SimpleNamespace(
                name="posix", fsync=posix_fsync)), patch.object(
                    persistence, "open", side_effect=self.recording_open,
                    create=True) as opener:
            persistence.sync_file(self.path)
        opener.assert_called_once_with(self.path, "rb")
        self.assert_preserved()

    def test_missing_file_is_not_created(self):
        missing = self.path.with_name("missing.bin")
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform), patch.object(
                    persistence, "os", SimpleNamespace(
                        name=platform, fsync=os.fsync)):
                with self.assertRaises(FileNotFoundError):
                    persistence.sync_file(missing)
                self.assertFalse(missing.exists())
        self.assert_preserved()

    def test_open_permission_error_propagates_without_fallback(self):
        for platform in ("nt", "posix"):
            error = PermissionError(errno.EACCES, "access denied")
            with self.subTest(platform=platform), patch.object(
                    persistence, "os", SimpleNamespace(
                        name=platform, fsync=os.fsync)), patch.object(
                            persistence, "open", side_effect=error,
                            create=True) as opener:
                with self.assertRaises(PermissionError) as caught:
                    persistence.sync_file(self.path)
                self.assertIs(caught.exception, error)
                self.assertEqual(opener.call_count, 1)
        self.assert_preserved()

    def test_flush_errors_propagate_and_close_handle(self):
        for platform in ("nt", "posix"):
            for error_number in (errno.EBADF, errno.EIO, errno.ENOSPC):
                self.opened = []
                error = OSError(error_number, "flush failed")
                with self.subTest(platform=platform, errno=error_number), patch.object(
                        persistence, "os", SimpleNamespace(
                            name=platform, fsync=lambda fd: None)), patch.object(
                                persistence.os, "fsync", side_effect=error) as flush, patch.object(
                                    persistence, "open", side_effect=self.recording_open,
                                    create=True):
                    with self.assertRaises(OSError) as caught:
                        persistence.sync_file(self.path)
                    self.assertIs(caught.exception, error)
                    flush.assert_called_once()
                self.assert_preserved()

    def test_native_platform_round_trip(self):
        persistence.sync_file(self.path)
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
