"""Durable publication primitives for deferred processing outputs."""

import errno
import json
import os
from pathlib import Path
import uuid


def sync_file(path):
    with open(path, "rb") as handle:
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


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        # A failed post-rename fsync is an uncertain commit, not permission to
        # delete the newly published document or its referenced media.
        temporary.unlink(missing_ok=True)
