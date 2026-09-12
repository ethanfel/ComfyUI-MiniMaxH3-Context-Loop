"""Fenced, workflow-scoped ownership locks for H3 project mutation.

The heartbeat reports liveness in the UI; expiry never transfers ownership
implicitly. A monotonically increasing epoch is the authoritative fencing
token: taking or releasing ownership invalidates work already queued by the
former owner. Only a SHA-256 digest of the ephemeral workflow owner id is
persisted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any


PROJECT_OWNERSHIP_FORMAT = "h3_project_ownership_v1"
PROJECT_OWNERSHIP_LEASE_SECONDS = 90.0
_OWNER_ID_RE = re.compile(r"[A-Za-z0-9._:-]{16,192}")


class _OwnershipMutationLock:
    """Re-entrant thread and process lock for one ownership record."""

    def __init__(self, path: str):
        self.path = path + ".lock"
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                handle = open(self.path, "a+b")
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0, os.SEEK_END)
                        if handle.tell() == 0:
                            handle.write(b"\0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                self._handle = handle
            self._depth += 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            self._depth -= 1
            if self._depth == 0 and self._handle is not None:
                handle, self._handle = self._handle, None
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            self._thread_lock.release()


_RUN_LOCKS: dict[str, _OwnershipMutationLock] = {}
_RUN_LOCKS_GUARD = threading.Lock()


class ProjectOwnershipError(ValueError):
    """Raised when a workflow is not allowed to mutate a protected run."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _run_name(value: Any) -> str:
    requested = str(value or "").strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", requested).strip("._-")
    if not normalized:
        raise ValueError("A non-empty H3 chain run_name is required.")
    normalized = normalized[:96]
    if requested != normalized:
        raise ValueError(
            "H3 run_name must be 1-96 characters, start and end with a "
            "letter or number, and contain only letters, numbers, '.', '_' "
            "or '-'. Use %r for this project." % normalized)
    return normalized


def _owner_id(value: Any) -> str:
    owner = str(value or "").strip()
    if _OWNER_ID_RE.fullmatch(owner) is None:
        raise ValueError("H3 workflow ownership id is invalid.")
    return owner


def _owner_digest(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()


def _lock_for(path: str) -> _OwnershipMutationLock:
    with _RUN_LOCKS_GUARD:
        return _RUN_LOCKS.setdefault(path, _OwnershipMutationLock(path))


def ownership_path(output_root: str, run_name: Any) -> str:
    chains_root = os.path.realpath(os.path.join(
        str(output_root), "h3_chains"))
    # Keep fencing records outside each deletable Run directory. Complete Run
    # deletion intentionally preserves input/h3_projects/<run>; deleting the
    # fence with only the generated output would otherwise let a stale tab
    # recreate or mutate that surviving project as an unprotected legacy Run.
    root = os.path.realpath(os.path.join(
        chains_root, ".project_ownership"))
    run = _run_name(run_name)
    path = os.path.realpath(os.path.join(root, run + ".json"))
    if os.path.commonpath((root, path)) != root:
        raise ValueError("H3 project ownership path escaped the output root.")
    return path


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(os.path.dirname(path), os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                pass
            finally:
                os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _empty_record(run_name: str) -> dict[str, Any]:
    return {
        "format": PROJECT_OWNERSHIP_FORMAT,
        "version": 1,
        "run_name": run_name,
        "enabled": True,
        "epoch": 0,
        "owner_digest": "",
        "owner_label": "",
        "acquired_at": "",
        "heartbeat_at": "",
        "lease_expires_at": 0.0,
        "updated_at": _timestamp(),
    }


def _read(path: str, run_name: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if (not isinstance(value, dict)
            or value.get("format") != PROJECT_OWNERSHIP_FORMAT
            or str(value.get("run_name") or "") != run_name):
        raise ValueError("H3 project ownership metadata is invalid.")
    record = _empty_record(run_name)
    record.update(value)
    record["epoch"] = max(0, int(record.get("epoch", 0)))
    record["lease_expires_at"] = float(
        record.get("lease_expires_at", 0.0))
    record["owner_digest"] = str(record.get("owner_digest") or "")
    record["owner_label"] = str(record.get("owner_label") or "")[:120]
    return record


def _public(record: dict[str, Any], owner_id: Any = "") -> dict[str, Any]:
    now = time.time()
    digest = ""
    try:
        digest = _owner_digest(_owner_id(owner_id)) if owner_id else ""
    except ValueError:
        pass
    occupied = bool(record.get("owner_digest"))
    owned = occupied and digest == str(record.get("owner_digest") or "")
    expired = occupied and float(record.get("lease_expires_at", 0.0)) <= now
    return {
        "format": PROJECT_OWNERSHIP_FORMAT,
        "run_name": str(record["run_name"]),
        "enabled": bool(record.get("enabled", True)),
        "epoch": int(record.get("epoch", 0)),
        "owner_label": str(record.get("owner_label") or ""),
        "owned_by_requester": owned,
        "available": not occupied,
        "expired": expired,
        "lease_expires_at": float(record.get("lease_expires_at", 0.0)),
        "server_now": now,
    }


def ownership_status(output_root: str, run_name: Any,
                     owner_id: Any = "") -> dict[str, Any]:
    run = _run_name(run_name)
    path = ownership_path(output_root, run)
    with _lock_for(path):
        record = _read(path, run)
        if record is None:
            return {
                **_public(_empty_record(run), owner_id),
                "enabled": False,
                "available": True,
            }
        return _public(record, owner_id)


def claim_project_ownership(
        output_root: str, run_name: Any, owner_id: Any,
        owner_label: Any = "", *, force: bool = False,
        lease_seconds: float = PROJECT_OWNERSHIP_LEASE_SECONDS,
) -> dict[str, Any]:
    run = _run_name(run_name)
    owner = _owner_id(owner_id)
    digest = _owner_digest(owner)
    label = str(owner_label or "Workflow")[:120]
    lease = max(30.0, min(600.0, float(lease_seconds)))
    path = ownership_path(output_root, run)
    with _lock_for(path):
        record = _read(path, run) or _empty_record(run)
        now = time.time()
        current = str(record.get("owner_digest") or "")
        if current == digest:
            record["owner_label"] = label
            record["heartbeat_at"] = _timestamp()
            record["lease_expires_at"] = now + lease
            record["updated_at"] = _timestamp()
            _atomic_json(path, record)
            return _public(record, owner)
        # Expiry is only a liveness hint. An abandoned owner must still be
        # displaced explicitly with Force ownership so merely opening a stale
        # workflow can never seize a project after a sleeping tab misses its
        # heartbeat.
        if current and not force:
            return _public(record, owner)
        record.update({
            "enabled": True,
            "epoch": int(record.get("epoch", 0)) + 1,
            "owner_digest": digest,
            "owner_label": label,
            "acquired_at": _timestamp(),
            "heartbeat_at": _timestamp(),
            "lease_expires_at": now + lease,
            "updated_at": _timestamp(),
        })
        _atomic_json(path, record)
        return _public(record, owner)


def heartbeat_project_ownership(
        output_root: str, run_name: Any, owner_id: Any, epoch: Any,
        owner_label: Any = "",
        lease_seconds: float = PROJECT_OWNERSHIP_LEASE_SECONDS,
) -> dict[str, Any]:
    """Refresh only an already-current proof; never acquire an empty lock."""
    run = _run_name(run_name)
    owner = _owner_id(owner_id)
    digest = _owner_digest(owner)
    label = str(owner_label or "Workflow")[:120]
    lease = max(30.0, min(600.0, float(lease_seconds)))
    path = ownership_path(output_root, run)
    with _lock_for(path):
        record = _read(path, run)
        if record is None:
            return {
                **_public(_empty_record(run), owner),
                "enabled": False,
                "available": True,
            }
        try:
            supplied_epoch = int(epoch)
        except (TypeError, ValueError):
            return _public(record, owner)
        if (digest != str(record.get("owner_digest") or "") or
                supplied_epoch != int(record.get("epoch", 0))):
            return _public(record, owner)
        record.update({
            "owner_label": label,
            "heartbeat_at": _timestamp(),
            "lease_expires_at": time.time() + lease,
            "updated_at": _timestamp(),
        })
        _atomic_json(path, record)
        return _public(record, owner)


def release_project_ownership(
        output_root: str, run_name: Any, owner_id: Any, epoch: Any,
) -> dict[str, Any]:
    run = _run_name(run_name)
    owner = _owner_id(owner_id)
    path = ownership_path(output_root, run)
    with _lock_for(path):
        record = _read(path, run)
        if record is None:
            return ownership_status(output_root, run, owner)
        if (_owner_digest(owner) != str(record.get("owner_digest") or "")
                or int(epoch) != int(record.get("epoch", 0))):
            raise ProjectOwnershipError(
                "This workflow no longer owns project %s. Refresh ownership "
                "status or use Force ownership." % run)
        record.update({
            "epoch": int(record.get("epoch", 0)) + 1,
            "owner_digest": "",
            "owner_label": "",
            "heartbeat_at": _timestamp(),
            "lease_expires_at": 0.0,
            "updated_at": _timestamp(),
        })
        _atomic_json(path, record)
        return _public(record, owner)


def _validate_project_ownership(
        run: str, record: dict[str, Any] | None, proof: Any,
        operation: str) -> dict[str, Any] | None:
    if record is None or not bool(record.get("enabled", True)):
        return None
    if not isinstance(proof, dict):
        raise ProjectOwnershipError(
            "Project %s is protected by workflow ownership. This "
            "workflow is read-only; use Force ownership in its Project "
            "Asset Carousel before attempting to %s." % (run, operation))
    try:
        owner = _owner_id(proof.get("owner_id"))
        epoch = int(proof.get("epoch"))
    except (TypeError, ValueError) as exc:
        raise ProjectOwnershipError(
            "Project %s received an invalid workflow ownership proof. "
            "Refresh the Project Asset Carousel." % run) from exc
    expected_epoch = int(record.get("epoch", 0))
    if (epoch != expected_epoch or
            _owner_digest(owner) != str(record.get("owner_digest") or "")):
        raise ProjectOwnershipError(
            "Project %s is owned by another workflow (%s). This stale "
            "workflow was blocked before it could %s. Use Force "
            "ownership only if you intend to invalidate the other tab."
            % (run, str(record.get("owner_label") or "active workflow"),
               operation))
    return {
        "owner_id": owner,
        "epoch": epoch,
        "run_name": run,
    }


@contextmanager
def project_write_guard(
        output_root: str, run_name: Any, proof: Any,
        operation: str = "modify this project",
):
    """Hold the ownership fence across one short durable commit.

    Forced ownership changes use the same lock, so they cannot split a
    validated checkpoint/editorial transaction. Long render work must not hold
    this guard; validate once when it starts and acquire the guard again only
    around its final pointer/catalog mutation.
    """
    run = _run_name(run_name)
    path = ownership_path(output_root, run)
    with _lock_for(path):
        yield _validate_project_ownership(
            run, _read(path, run), proof, operation)


def require_project_ownership(
        output_root: str, run_name: Any, proof: Any,
        operation: str = "modify this project",
) -> dict[str, Any] | None:
    """Validate a fencing proof when the run has ownership protection.

    A matching proof remains valid after its heartbeat deadline so a long
    render or sleeping browser does not lose ownership implicitly. An explicit
    forced takeover increments ``epoch`` and immediately fences that render.
    """
    with project_write_guard(
            output_root, run_name, proof, operation) as ownership:
        return ownership
