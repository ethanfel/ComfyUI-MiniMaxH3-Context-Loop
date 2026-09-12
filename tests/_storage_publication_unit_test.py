"""No-clobber publication, audit guards and bounded SMB/Windows retries."""
import ctypes
import errno
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch, mock_open

MODULE = Path(__file__).resolve().parents[1]/'processing_persistence.py'
spec = importlib.util.spec_from_file_location('publication_under_test', MODULE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.target = self.root/'staged', self.root/'published'
        self.source.write_bytes(b'new independent bytes')

    def test_native_move_into_unoccupied_name(self):
        p.publish_new_file(self.source, self.target)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.target.read_bytes(), b'new independent bytes')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux mount detection')
    def test_mount_detection_decodes_spaces_and_uses_deepest_effective_mount(self):
        target = self.root/'mounted share'/'new-output'
        escaped = str(target.parent).replace(' ', r'\040')
        table = ('20 1 0:1 / / rw - ext4 source rw\n'
                 '21 20 0:2 / '+escaped+' rw - autofs source rw\n'
                 '22 21 0:3 / '+escaped+' rw - cifs source rw\n')
        with patch.object(p, 'open', mock_open(read_data=table), create=True):
            self.assertEqual(p._linux_mount_type(target), 'cifs')
            with self.assertRaisesRegex(p.StorageFilesystemError, 'CIFS/SMB'):
                p.require_atomic_control_files(target)
        self.assertFalse(target.exists())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux mount detection')
    def test_known_unsafe_mounts_are_fenced_but_local_mount_is_not(self):
        for kind in ('cifs', 'smb3', 'smbfs'):
            with self.subTest(filesystem=kind), patch.object(p, '_linux_mount_type', return_value=kind):
                with self.assertRaises(p.StorageFilesystemError):
                    p.require_atomic_control_files(self.root)
        with patch.object(p, '_linux_mount_type', return_value='ext4'):
            p.require_atomic_control_files(self.root)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux mount detection')
    def test_unreadable_mount_table_does_not_guess_local_filesystem(self):
        with patch.object(p, 'open', side_effect=PermissionError('mount table unavailable'), create=True):
            with self.assertRaises(PermissionError):
                p.require_atomic_control_files(self.root)

    def test_native_occupied_destination_is_never_replaced(self):
        self.target.write_bytes(b'existing bytes')
        with self.assertRaises(FileExistsError):
            p.publish_new_file(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b'existing bytes')
        self.assertEqual(self.source.read_bytes(), b'new independent bytes')

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires Windows privileges')
    def test_dangling_symlink_is_an_occupied_destination(self):
        self.target.symlink_to(self.root/'absent')
        with self.assertRaises(FileExistsError):
            p.publish_new_file(self.source, self.target)
        self.assertTrue(self.target.is_symlink())
        self.assertTrue(self.source.is_file())
        self.assertFalse((self.root/'absent').exists())

    def test_windows_uses_rename_and_propagates_collision(self):
        rename = Mock(side_effect=FileExistsError('occupied'))
        with patch.object(p, 'os', SimpleNamespace(name='nt', rename=rename)):
            with self.assertRaises(FileExistsError):
                p.publish_new_file(self.source, self.target)
        rename.assert_called_once_with(self.source, self.target)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux-specific fallback')
    def test_unavailable_linux_primitive_falls_back_to_safe_link(self):
        with patch.object(p, '_linux_rename_noreplace', return_value=None), patch.object(
                p.os, 'link', wraps=os.link) as link:
            p.publish_new_file(self.source, self.target)
        link.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b'new independent bytes')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux-specific errors')
    def test_unsupported_linux_primitive_uses_safe_fallback(self):
        def unavailable(*args):
            ctypes.set_errno(errno.EOPNOTSUPP)
            return -1
        with patch.object(p, '_linux_rename_noreplace', return_value=unavailable), patch.object(
                p.os, 'link', wraps=os.link) as link:
            p.publish_new_file(self.source, self.target)
        link.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux-specific errors')
    def test_permission_error_never_falls_back(self):
        def denied(*args):
            ctypes.set_errno(errno.EACCES)
            return -1
        with patch.object(p, '_linux_rename_noreplace', return_value=denied), patch.object(
                p.os, 'link') as link:
            with self.assertRaises(PermissionError):
                p.publish_new_file(self.source, self.target)
        link.assert_not_called()
        self.assertTrue(self.source.is_file())

    def test_null_path_rejected_without_publication(self):
        with self.assertRaises(ValueError):
            p.publish_new_file(str(self.source)+'\0suffix', self.target)
        self.assertFalse(self.target.exists())

    def test_publication_respects_python_audit_guard(self):
        code = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location('p', sys.argv[1])
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
def guard(event, args):
    if event in ('os.rename', 'os.link'):
        raise PermissionError('audit guard')
sys.addaudithook(guard)
try:
    p.publish_new_file(sys.argv[2], sys.argv[3])
except PermissionError as exc:
    assert str(exc) == 'audit guard'
else:
    raise AssertionError('publication bypassed audit guard')
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(MODULE),
                                 str(self.source), str(self.target)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.source.is_file())

    def test_transient_json_permission_conflict_is_retried(self):
        original = os.replace
        calls = []
        def replace(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, 'transient conflict')
            return original(src, dst)
        with patch.object(p.os, 'replace', side_effect=replace), patch.object(p.time, 'sleep'):
            p._replace_published_json(self.source, self.target)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.target.read_bytes(), b'new independent bytes')

    def test_permanent_permission_error_is_bounded(self):
        error = PermissionError(errno.EACCES, 'permanent')
        with patch.object(p.os, 'replace', side_effect=error) as replace, patch.object(
                p.time, 'monotonic', side_effect=[0, 0.1, 1.1]), patch.object(p.time, 'sleep'):
            with self.assertRaises(PermissionError):
                p._replace_published_json(self.source, self.target)
        self.assertEqual(replace.call_count, 2)
        self.assertTrue(self.source.is_file())

    def test_real_io_and_space_errors_are_not_retried(self):
        for number in (errno.EIO, errno.ENOSPC, errno.EXDEV):
            with self.subTest(errno=number), patch.object(p.os, 'replace', side_effect=OSError(
                    number, 'real error')) as replace, patch.object(p.time, 'sleep') as sleep:
                with self.assertRaises(OSError):
                    p._replace_published_json(self.source, self.target)
            replace.assert_called_once()
            sleep.assert_not_called()

    def test_foreign_change_during_backoff_is_not_overwritten(self):
        self.target.write_bytes(b'previous value')
        def intervening_write(delay):
            self.target.write_bytes(b'foreign accepted value')
        with patch.object(p.os, 'replace', side_effect=PermissionError(errno.EACCES, 'busy')) as replace:
            with patch.object(p.time, 'sleep', side_effect=intervening_write):
                with self.assertRaisesRegex(OSError, 'destination changed'):
                    p._replace_published_json(self.source, self.target)
        replace.assert_called_once()
        self.assertEqual(self.target.read_bytes(), b'foreign accepted value')
        self.assertTrue(self.source.is_file())

    def test_lost_json_publication_ack_is_not_retried(self):
        original = os.replace
        def replace(src, dst):
            original(src, dst)
            raise PermissionError(errno.EACCES, 'lost acknowledgement')
        with patch.object(p.os, 'replace', side_effect=replace) as replace, patch.object(p.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                p._replace_published_json(self.source, self.target)
        replace.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'new independent bytes')


if __name__ == '__main__':
    unittest.main()
