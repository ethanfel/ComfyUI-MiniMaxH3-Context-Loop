"""Durable top-level handoff state for H3 Chain orchestration.

This module stores the separate run-local orchestration records that let a
finished top-level ComfyUI prompt hand off to the next heavyweight H3 job
(next scene / next candidate / review gate / completion).

Hard separation from the JSON Plan (PLAN_SCHEMA_INVARIANT_SPEC):

- Records live under ``output/h3_chains/<run_name>/orchestration/``, never
  inside the Plan JSON.
- A record holds only lightweight identity values (run name, scene number,
  candidate ordinal/count, seed, revision/checkpoint SHA-256, prompt ID,
  workflow fingerprint, status, attempt counters, timestamps).
- Tensors, models, conditioning, VAEs, CLIP, samplers, and live Python
  object references are rejected on write and on read.
- The Plan remains authoritative for all generation semantics; a record may
  say "resume scene 7 with candidate 2 of 3 using revision X" but never
  duplicates prompt text, shots, references, or model settings.

Durability:

- Every record update is written temp-file + flush + fsync + ``os.replace()``
  so a crash leaves either the previous or the new complete record.
- Mutations are serialized behind a per-run lock so the claim primitive is
  exactly-once: the first claim advances ``pending`` to ``claimed``, every
  concurrent or duplicate claim observes a status that is no longer pending.
- Corrupt or unknown-version records are never auto-repaired or queued.
  ``load``/``list`` raise ``HandoffCorruptError`` and the record is left
  untouched for manual recovery.
"""

from __future__ import annotations

import errno
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

HANDOFF_FORMAT_VERSION = "h3_top_level_handoff_v1"
HANDOFF_ORCHESTRATION_DIR = "orchestration"
HANDOFF_ACTIONS = (
    "next_scene",
    "next_candidate",
    "await_review",
    "complete",
    "manual_resume",
)
HANDOFF_STATUSES = (
    "pending",
    "claimed",
    "queued",
    "consumed",
    "cancelled",
    "failed",
    # Delivery may have reached ComfyUI even though the browser lost the
    # acknowledgement.  It is deliberately not auto-released/retried.
    "uncertain",
)
TERMINAL_HANDOFF_STATUSES = ("consumed", "cancelled", "failed", "uncertain")
DEFAULT_MAX_ATTEMPTS = 3
# Stale lock cleanup threshold. A live holder refreshes the lock while
# working; anything older is presumed to belong to a crashed process.
LOCK_STALE_SECONDS = 60.0

_HANDOFF_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# status -> statuses a legal transition may enter.
_HANDOFF_TRANSITIONS = {
    "pending": ("claimed", "cancelled"),
    "claimed": ("queued", "consumed", "cancelled", "failed", "uncertain"),
    "queued": ("consumed", "cancelled", "failed"),
    "uncertain": ("consumed", "cancelled", "failed"),
    "consumed": (),
    "cancelled": (),
    "failed": (),
}

# record keys that must always exist and are assigned by the store.
_STORE_MANAGED_KEYS = frozenset({
    "format", "handoff_id", "run_name", "status",
    "attempt", "max_attempts", "created_at", "updated_at",
})


class HandoffError(ValueError):
    """Base error for durable handoff state failures."""


class HandoffCorruptError(HandoffError):
    """A handoff record is corrupt, unknown, or holds non-lightweight data.

    Corrupt state is never auto-queued or auto-repaired: the record file is
    left exactly as found so the user can recover it manually.
    """


class HandoffNotFoundError(HandoffError):
    """No handoff record exists for this run/ID."""


class HandoffExistsError(HandoffError):
    """A handoff with this ID already exists in this run."""


class IllegalHandoffTransitionError(HandoffError):
    """The requested status transition is not legal for the current status."""


class HandoffClaimError(HandoffError):
    """A handoff could not be claimed (not pending, terminal, or attempts
    exhausted). Exactly one claim can ever succeed for a fresh pending
    record; this error names the reason a duplicate lost."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _validate_run_name(value: Any) -> str:
    """Apply the same run-name identity rules as the chain layer.

    1-96 characters, starts/ends with an alphanumeric, interior characters
    from ``A-Za-z0-9._-``.  The charset makes ``<run>/orchestration`` safe
    without any further path handling; containment under the output root is
    still enforced for every resolved path.
    """
    text = str(value or "").strip()
    if not 1 <= len(text) <= 96:
        raise HandoffError("H3 run_name must be 1-96 characters.")
    if not _RUN_NAME_RE.fullmatch(text):
        raise HandoffError(
            "H3 run_name must start and end with a letter or number and "
            "contain only letters, numbers, '.', '_' or '-'.")
    return text


def _validate_handoff_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _HANDOFF_ID_RE.fullmatch(text):
        raise HandoffError(
            "H3 handoff_id must be 1-128 characters from A-Za-z0-9, '.', "
            "'_' or '-'.")
    return text


def _validate_sha256(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value or "").strip()
    if not _HASH_RE.fullmatch(text):
        raise HandoffError(
            "source_checkpoint_sha256 must be a lowercase 64-character "
            "hexadecimal SHA-256 or null.")
    return text


def _assert_lightweight_value(key: str, value: Any) -> None:
    """Reject tensors, models, and any non-JSON-native payload."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_lightweight_value(key, item)
        return
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _assert_lightweight_value(str(child_key), child_value)
        return
    raise HandoffError(
        "Handoff field %r must hold lightweight JSON data only; tensors, "
        "models, and live object references are not allowed." % key)


def _validate_record(record: Any, *, expect_format: str = HANDOFF_FORMAT_VERSION
                     ) -> dict[str, Any]:
    """Validate a parsed record without touching disk.

    Unknown formats and corrupt structures raise ``HandoffCorruptError``
    (never repaired, never queued).
    """
    if not isinstance(record, dict):
        raise HandoffCorruptError("Handoff record must be a JSON object.")
    if record.get("format") != expect_format:
        raise HandoffCorruptError(
            "Unknown handoff format %r; expected %r. Corrupt or "
            "unknown state is never auto-queued; recover manually."
            % (record.get("format"), expect_format))
    for key in ("handoff_id", "run_name", "action", "status", "attempt",
                "max_attempts", "created_at", "updated_at"):
        if key not in record:
            raise HandoffCorruptError("Handoff record is missing %r." % key)
    action = record.get("action")
    if action not in HANDOFF_ACTIONS:
        raise HandoffCorruptError(
            "Handoff record has unknown action %r." % action)
    status = record.get("status")
    if status not in HANDOFF_STATUSES:
        raise HandoffCorruptError(
            "Handoff record has unknown status %r." % status)
    attempt = record.get("attempt")
    max_attempts = record.get("max_attempts")
    if (not isinstance(attempt, int) or isinstance(attempt, bool)
            or attempt < 0):
        raise HandoffCorruptError("Handoff record attempt must be a "
                                  "non-negative integer.")
    if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
            or max_attempts < 1):
        raise HandoffCorruptError("Handoff record max_attempts must be a "
                                  "positive integer.")
    if attempt > max_attempts:
        raise HandoffCorruptError("Handoff record attempt exceeds "
                                  "max_attempts.")
    for key in ("created_at", "updated_at"):
        if not isinstance(record.get(key), str) or not record.get(key):
            raise HandoffCorruptError(
                "Handoff record %s must be an ISO-8601 string." % key)
    _validate_handoff_id(record.get("handoff_id"))
    _validate_run_name(record.get("run_name"))
    selected_branch = record.get("working_branch_id", "main")
    if selected_branch != "main" and not re.fullmatch(r"[0-9a-f]{32}", str(selected_branch)):
        raise HandoffCorruptError("Invalid working branch identity in handoff.")
    for key in ("scene", "start_clip", "end_clip", "candidate_ordinal",
                "candidate_count", "seed", "predecessor_scene"):
        value = record.get(key)
        if value is not None and (not isinstance(value, int)
                                  or isinstance(value, bool)):
            raise HandoffCorruptError(
                "Handoff record field %r must be an integer or null." % key)
    end_clip = record.get("end_clip")
    if end_clip is not None and record.get("start_clip") is not None:
        if end_clip < record["start_clip"]:
            raise HandoffCorruptError(
                "end_clip must not be below start_clip.")
    if record.get("candidate_ordinal") is not None:
        if record.get("candidate_count") is None:
            raise HandoffCorruptError(
                "candidate_ordinal requires candidate_count.")
        ordinal = record.get("candidate_ordinal")
        count = record.get("candidate_count")
        if not 1 <= ordinal <= count:
            raise HandoffCorruptError(
                "candidate_ordinal must be within 1..candidate_count.")
    for key in ("candidate_batch_id", "source_prompt_id", "source_revision",
                "source_checkpoint_sha256", "workflow_fingerprint",
                "transition_key", "accepted_prompt_id"):
        value = record.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise HandoffCorruptError(
                "Handoff record field %r must be a non-empty string or "
                "null." % key)
    for key, value in record.items():
        _assert_lightweight_value(key, value)
    return record


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    """Temp file + flush + fsync + os.replace(): crash-safe replacement."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp",
        dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class _RunLock:
    """Best-effort cross-process per-run lock.

    Uses ``O_CREAT | O_EXCL`` marker files (portable to Windows without
    mandatory locking).  A live holder refreshes the marker's mtime; markers
    older than ``LOCK_STALE_SECONDS`` are presumed abandoned and replaced.
    On platforms/builds where exclusive creation races are impossible the
    lock degrades to no-serialization, and single-process callers still get
    exactly-once behavior from the read-modify-replace cycle.
    """

    def __init__(self, path: str, timeout_seconds: float = 30.0):
        self._path = path
        self._timeout = timeout_seconds
        self._acquired = False

    def _read_owner(self) -> str | None:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                return handle.read().strip() or None
        except FileNotFoundError:
            return None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        deadline = time.monotonic() + self._timeout
        while True:
            owner = self._read_owner()
            if owner is None:
                try:
                    fd = os.open(self._path, os.O_CREAT | os.O_EXCL
                                 | os.O_WRONLY, 0o644)
                except FileExistsError:
                    continue
                except OSError as exc:
                    if exc.errno not in (errno.EEXIST,):
                        raise
                    continue
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write("%d.%d" % (os.getpid(),
                                                time.monotonic_ns()))
                except Exception:
                    _safe_unlink(self._path)
                    raise
                self._acquired = True
                return
            # Mark exists: refresh or treat as stale.
            try:
                stale = (time.time() - os.path.getmtime(self._path)) > \
                    LOCK_STALE_SECONDS
            except OSError:
                stale = True
            if stale:
                _safe_unlink(self._path)
                continue
            if time.monotonic() > deadline:
                raise HandoffError(
                    "Timed out waiting for the H3 run orchestration lock.")
            time.sleep(0.05)

    def __enter__(self) -> "_RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._acquired:
            _safe_unlink(self._path)
            self._acquired = False


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class HandoffStore:
    """Durable handoff records for one ComfyUI output root.

    ``output_root`` is the ComfyUI output directory; records are stored at
    ``<output_root>/h3_chains/<run_name>/orchestration/<handoff_id>.json``.
    Pass the real root in production (``folder_paths.get_output_directory()``)
    and a temp directory in tests.
    """

    def __init__(self, output_root: str,
                 now: Callable[[], str] | None = None):
        self._root = os.path.realpath(output_root)
        self._now = now or _utc_now_iso

    # -- path helpers -----------------------------------------------------

    def orchestration_dir(self, run_name: Any) -> str:
        """Absolute, containment-checked orchestration directory."""
        normalized = _validate_run_name(run_name)
        path = os.path.realpath(os.path.join(
            self._root, "h3_chains", normalized, HANDOFF_ORCHESTRATION_DIR))
        if os.path.commonpath((self._root, path)) != self._root:
            raise HandoffError("H3 orchestration path escapes the output "
                               "directory.")
        return path

    def _record_path(self, run_name: Any, handoff_id: Any) -> str:
        return os.path.join(self.orchestration_dir(run_name),
                            "%s.json" % _validate_handoff_id(handoff_id))

    def _lock_path(self, run_name: Any) -> str:
        return os.path.join(self.orchestration_dir(run_name), ".lock")

    # -- read -------------------------------------------------------------

    def load(self, run_name: Any, handoff_id: Any) -> dict[str, Any]:
        """Read and validate one record.

        Raises ``HandoffNotFoundError`` when absent and
        ``HandoffCorruptError`` when the file cannot be trusted.  Corrupt
        files are left exactly as found.
        """
        path = self._record_path(run_name, handoff_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except FileNotFoundError:
            raise HandoffNotFoundError(
                "No H3 handoff %s in run %s." % (
                    _validate_handoff_id(handoff_id),
                    _validate_run_name(run_name)))
        except (OSError, json.JSONDecodeError) as exc:
            raise HandoffCorruptError(
                "H3 handoff %s in run %s is corrupt (%s). It was not "
                "modified; recover it manually." % (
                    _validate_handoff_id(handoff_id),
                    _validate_run_name(run_name), exc))
        _validate_record(record, expect_format=HANDOFF_FORMAT_VERSION)
        if record["run_name"] != _validate_run_name(run_name):
            raise HandoffCorruptError(
                "H3 handoff %s is stored under the wrong run." %
                _validate_handoff_id(handoff_id))
        return record

    def list(self, run_name: Any) -> list[dict[str, Any]]:
        """All valid records for a run, sorted by handoff_id.

        Corrupt records are reported (as dicts with ``_corrupt_reason``)
        instead of raising, so a manual-recovery UI can list them without
        being blocked; they are never queued or repaired.
        """
        directory = self.orchestration_dir(run_name)
        if not os.path.isdir(directory):
            return []
        results: list[dict[str, Any]] = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    record = json.load(handle)
                _validate_record(record,
                                 expect_format=HANDOFF_FORMAT_VERSION)
                results.append(record)
            except (OSError, json.JSONDecodeError, HandoffError) as exc:
                results.append({
                    "format": HANDOFF_FORMAT_VERSION,
                    "handoff_id": name[:-5],
                    "_corrupt": True,
                    "_corrupt_reason": str(exc),
                })
        return results

    # -- write ------------------------------------------------------------

    def create(self, run_name: Any, *, action: str, scene: int | None = None,
               start_clip: int | None = None,
               end_clip: int | None = None,
               candidate_batch_id: str | None = None,
               candidate_ordinal: int | None = None,
               candidate_count: int | None = None,
               seed: int | None = None,
               source_prompt_id: str | None = None,
               source_revision: str | None = None,
               source_checkpoint_sha256: str | None = None,
               workflow_fingerprint: str | None = None,
               predecessor_scene: int | None = None,
               transition_key: str | None = None,
               max_attempts: int = DEFAULT_MAX_ATTEMPTS,
               handoff_id: str | None = None,
               working_branch_id: str = "main") -> dict[str, Any]:
        """Create a fresh ``pending`` handoff with a unique ID.

        Every value must be lightweight JSON data; the store raises before
        writing anything when it is not.
        """
        if action not in HANDOFF_ACTIONS:
            raise HandoffError("Unknown handoff action %r." % action)
        if (not isinstance(max_attempts, int) or isinstance(max_attempts, bool)
                or max_attempts < 1):
            raise HandoffError("max_attempts must be a positive integer.")
        run = _validate_run_name(run_name)
        record_id = _validate_handoff_id(
            handoff_id or "h3-%s" % uuid.uuid4().hex)
        for key, value in {
            "scene": scene, "start_clip": start_clip, "end_clip": end_clip,
            "candidate_ordinal": candidate_ordinal,
            "candidate_count": candidate_count, "seed": seed,
            "predecessor_scene": predecessor_scene,
        }.items():
            if value is not None and (not isinstance(value, int)
                                      or isinstance(value, bool)):
                raise HandoffError("Handoff field %r must be an integer or "
                                   "null." % key)
        if (end_clip is not None and start_clip is not None
                and end_clip < start_clip):
            raise HandoffError("end_clip must not be below start_clip.")
        if candidate_ordinal is not None:
            if candidate_count is None:
                raise HandoffError("candidate_ordinal requires "
                                   "candidate_count.")
            if not 1 <= candidate_ordinal <= candidate_count:
                raise HandoffError("candidate_ordinal must be within "
                                   "1..candidate_count.")
        for key, value in {
            "candidate_batch_id": candidate_batch_id,
            "source_prompt_id": source_prompt_id,
            "source_revision": source_revision,
            "workflow_fingerprint": workflow_fingerprint,
            "transition_key": transition_key,
        }.items():
            if value is not None and (not isinstance(value, str) or not value):
                raise HandoffError("Handoff field %r must be a non-empty "
                                   "string or null." % key)
        checkpoint = _validate_sha256(source_checkpoint_sha256)
        now = self._now()
        record = {
            "format": HANDOFF_FORMAT_VERSION,
            "handoff_id": record_id,
            "run_name": run,
            "action": action,
            "status": "pending",
            "scene": scene,
            "start_clip": start_clip,
            "end_clip": end_clip,
            "candidate_batch_id": candidate_batch_id,
            "candidate_ordinal": candidate_ordinal,
            "candidate_count": candidate_count,
            "seed": seed,
            "predecessor_scene": predecessor_scene,
            "source_prompt_id": source_prompt_id,
            "source_revision": source_revision,
            "source_checkpoint_sha256": checkpoint,
            "workflow_fingerprint": workflow_fingerprint,
            "transition_key": transition_key,
            "attempt": 0,
            "max_attempts": max_attempts,
            "created_at": now,
            "updated_at": now,
        }
        if working_branch_id != "main":
            if not re.fullmatch(r"[0-9a-f]{32}", str(working_branch_id)):
                raise HandoffError("Invalid working branch identity.")
            record["working_branch_id"] = working_branch_id
        for key, value in record.items():
            _assert_lightweight_value(key, value)
        path = self._record_path(run, record_id)
        if os.path.exists(path):
            raise HandoffExistsError(
                "H3 handoff %s already exists in run %s." % (record_id, run))
        with _RunLock(self._lock_path(run)):
            if os.path.exists(path):
                raise HandoffExistsError(
                    "H3 handoff %s already exists in run %s." %
                    (record_id, run))
            _atomic_json(path, record)
        return record

    def _write_record(self, run_name: str, record: dict[str, Any]) -> None:
        path = self._record_path(run_name, record["handoff_id"])
        _atomic_json(path, record)

    # -- transitions ------------------------------------------------------

    def transition(self, run_name: Any, handoff_id: Any,
                   new_status: str, accepted_prompt_id: str | None = None
                   ) -> dict[str, Any]:
        """Apply a legal status transition atomically.

        Bounded retries never go through this method: ``claimed -> pending``
        and the budget check live in ``release`` only.  All other illegal
        moves raise ``IllegalHandoffTransitionError`` and the record is left
        untouched.
        """
        if new_status not in HANDOFF_STATUSES:
            raise HandoffError("Unknown handoff status %r." % new_status)
        run = _validate_run_name(run_name)
        with _RunLock(self._lock_path(run)):
            record = self.load(run, handoff_id)
            current = record["status"]
            if new_status in _HANDOFF_TRANSITIONS.get(current, ()):
                record["status"] = new_status
                if accepted_prompt_id:
                    record["accepted_prompt_id"] = str(accepted_prompt_id)
                record["updated_at"] = self._now()
                self._write_record(run, record)
                return record
        raise IllegalHandoffTransitionError(
            "Illegal H3 handoff transition %s -> %s for %s." %
            (current, new_status, _validate_handoff_id(handoff_id)))

    def claim(self, run_name: Any, handoff_id: Any,
              claimant: str | None = None,
              source_prompt_id: str | None = None) -> dict[str, Any]:
        """Exactly-once claim: the first pending -> claimed wins.

        Claiming consumes one attempt (``attempt`` goes 0 -> 1 on the first
        claim).  A duplicate claim for an already claimed/queued/consumed
        record raises ``HandoffClaimError`` and changes nothing.  A pending
        record whose budget is already exhausted is marked ``failed`` and
        the claim raises.

        ``source_prompt_id`` (the top-level prompt that produced the
        checkpoint this handoff resumes from) is written inside the same
        locked record update as the status change, so it is first-writer-
        wins with the claim itself and can never be stamped by a duplicate.
        """
        run = _validate_run_name(run_name)
        with _RunLock(self._lock_path(run)):
            record = self.load(run, handoff_id)
            current = record["status"]
            if current == "pending":
                if record["attempt"] >= record["max_attempts"]:
                    record["status"] = "failed"
                    record["updated_at"] = self._now()
                    self._write_record(run, record)
                    raise HandoffClaimError(
                        "H3 handoff %s cannot be claimed: attempt budget "
                        "of %d is exhausted." % (
                            record["handoff_id"], record["max_attempts"]))
                record["status"] = "claimed"
                record["attempt"] = record["attempt"] + 1
                record["updated_at"] = self._now()
                if claimant:
                    record["claimant"] = str(claimant)
                if source_prompt_id:
                    record["source_prompt_id"] = str(source_prompt_id)
                self._write_record(run, record)
                return record
            if current in TERMINAL_HANDOFF_STATUSES:
                raise HandoffClaimError(
                    "H3 handoff %s is already %s; duplicate terminal events "
                    "never re-claim." % (record["handoff_id"], current))
            raise HandoffClaimError(
                "H3 handoff %s is %s, not pending; a concurrent claim "
                "already won." % (record["handoff_id"], current))

    def release(self, run_name: Any, handoff_id: Any,
                reason: str | None = None) -> dict[str, Any]:
        """Release a claim within the bounded attempt rules.

        ``claimed`` with a remaining attempt budget returns to ``pending``
        (retryable); an exhausted budget or an explicit final failure moves
        to ``failed``.  Non-claimed records raise.
        """
        run = _validate_run_name(run_name)
        with _RunLock(self._lock_path(run)):
            record = self.load(run, handoff_id)
            if record["status"] != "claimed":
                raise IllegalHandoffTransitionError(
                    "H3 handoff %s is %s, not claimed; nothing to "
                    "release." % (record["handoff_id"], record["status"]))
            if record["attempt"] < record["max_attempts"]:
                record["status"] = "pending"
            else:
                record["status"] = "failed"
            if reason:
                record["last_release_reason"] = str(reason)
            record["updated_at"] = self._now()
            self._write_record(run, record)
            return record


def default_output_root() -> str:
    """The ComfyUI output directory for production callers."""
    import folder_paths
    return os.path.realpath(os.path.abspath(
        folder_paths.get_output_directory()))


def default_store() -> HandoffStore:
    return HandoffStore(default_output_root())
