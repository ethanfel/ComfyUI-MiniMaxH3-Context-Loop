"""Durable publication primitives for deferred processing outputs."""

import errno
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


class StorageFilesystemError(ValueError):
    """A known filesystem cannot satisfy the new control-root commit protocol."""


def _linux_mount_type(path):
    """Read mount identity only; never inspect/export credentials or options."""
    target = Path(path).resolve()
    # Trigger automount before reading its effective mount table.
    existing = target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    existing.stat()
    with open('/proc/self/mountinfo', encoding='utf-8') as handle:
        rows = handle.read().splitlines()
    best, filesystem = -1, None
    for row in rows:
        left, separator, right = row.partition(' - ')
        fields, details = left.split(), right.split()
        if not separator or len(fields) < 5 or not details:
            continue
        mount = Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
        if target.is_relative_to(mount) and len(str(mount)) >= best:
            best, filesystem = len(str(mount)), details[0]
    if filesystem is None:
        raise StorageFilesystemError('Cannot identify the control-storage filesystem; no write attempted.')
    return filesystem


def require_atomic_control_files(path):
    """Fence NEW storage-root writes on known unsafe Linux CIFS mounts.

    This is not a general platform qualification or a change to legacy savers.
    The actual CIFS probe observed an unlink/retry gap in rename-over-existing.
    A no-overwrite rename supports new media but cannot fix that root protocol.
    """
    if sys.platform.startswith('linux') and _linux_mount_type(path) in ('cifs', 'smb3', 'smbfs'):
        raise StorageFilesystemError(
            'The new storage-root protocol is not supported on this Linux CIFS/SMB mount: '
            'replacing its control pointer can expose a missing file. No migration or '
            'control-state write was started. Keep the existing chain unchanged and '
            'use a filesystem with verified atomic replacement, or await a compatible commit protocol.')


def temporary_path(path, suffix=".tmp"):
    """Bounded same-directory staging, independent of the final basename."""
    if suffix not in (".tmp", ".mp4", ".mkv", ".wav"):
        raise ValueError("Unsupported staging suffix.")
    return str(Path(path).with_name(".tmp-" + uuid.uuid4().hex + suffix))


def sync_file(path):
    # Windows _commit/FlushFileBuffers needs a writable handle. Reopen the
    # existing artifact without truncating it; keep read-only access on POSIX.
    mode = "r+b" if os.name == "nt" else "rb"
    with open(path, mode) as handle:
        os.fsync(handle.fileno())


def sync_directory(path):
    # Windows has no portable directory fsync. Some network filesystems also
    # explicitly don't implement it; real I/O failures must still propagate.
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise


@lru_cache(maxsize=1)
def _linux_rename_noreplace():
    import ctypes
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError:
        return None
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    return function


def publish_new_file(staging, destination):
    """Move flushed OWN staging into a new name without replacing any file.

    Linux RENAME_NOREPLACE works on CIFS shares that disallow hard links.
    Windows os.rename also rejects occupied destinations. Other systems use
    the original no-replace link/unlink operation; never fall back to overwrite.
    Callers retain staged bytes on failures and flush the containing directory.
    https://man7.org/linux/man-pages/man2/rename.2.html
    https://docs.python.org/3/library/os.html#os.rename
    """
    if os.name == 'nt':
        os.rename(staging, destination)
        return
    if sys.platform.startswith('linux'):
        function = _linux_rename_noreplace()
        if function is not None:
            import ctypes
            src, dst = os.fsencode(staging), os.fsencode(destination)
            if b'\0' in src or b'\0' in dst:
                raise ValueError('Embedded null in publication path.')
            # Native ctypes must respect the same Python audit guards as os.rename.
            sys.audit('os.rename', os.fspath(staging), os.fspath(destination), -1, -1)
            if function(-100, src, -100, dst, 1) == 0:  # AT_FDCWD, RENAME_NOREPLACE
                return
            error = ctypes.get_errno()
            if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise OSError(error, os.strerror(error), os.fspath(destination))
    os.link(staging, destination)
    os.unlink(staging)


def _file_token(path):
    try:
        value = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _replace_published_json(staging, destination):
    """Bounded retry for transient Windows/SMB sharing conflicts, not I/O errors.

    If publication was acknowledged ambiguously or the destination changed,
    stop and let the domain operation reconcile its durable receipt.
    """
    before, deadline = _file_token(destination), time.monotonic()+1.0
    delay = 0.01
    while True:
        try:
            os.replace(staging, destination)
            return
        except OSError as error:
            if (error.errno not in (errno.EACCES, errno.EPERM, errno.EBUSY)
                    or not os.path.exists(staging) or time.monotonic() >= deadline):
                raise
            if _file_token(destination) != before:
                raise OSError('JSON destination changed during a sharing conflict; no retry overwrite.') from error
            time.sleep(delay)
            delay = min(delay*2, 0.1)
            if _file_token(destination) != before:
                raise OSError('JSON destination changed while waiting; no retry overwrite.') from error


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory atomic replacement without doubling long metadata names.
    temporary = path.with_name(".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_published_json(temporary, path)
        sync_directory(path.parent)
    finally:
        # A failed post-rename fsync is an uncertain commit, not permission to
        # delete the newly published document or its referenced media.
        temporary.unlink(missing_ok=True)
