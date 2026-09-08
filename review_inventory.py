"""Durable review inventory for H3 Chain runs.

Implements the M5 durability contract (REVIEW_GATE_DURABILITY_SPEC): a
saved candidate batch must remain useful recovery inventory after a browser
refresh and after a ComfyUI crash/restart, without depending on a live
PromptExecutor or live IMAGE tensors.

Design (HARD SCOPE: no Plan JSON involvement):

- Everything lives under ``output/h3_chains/<run_name>/orchestration/``,
  the same run-local orchestration directory used by handoff records.
- Two kinds of records are inventoried:
  1. Durable candidate-batch / handoff records
     (``h3_top_level_handoff_v1`` with action ``next_candidate`` /
     ``await_review``) — written by the chain layer while the live review
     prompt is still active; candidate retries do not imply a top-level boundary.
  2. Review snapshots (``h3_review_snapshot_v1``) — a lightweight,
     atomic-written copy of the pending-review *identity* (token, run,
     scene, candidate revisions/seeds, deadline). Tensors and media are
     deliberately excluded: previews come from the saved
     segment/checkpoint files, which are the authoritative media
     inventory.

- Snapshots are written when a review becomes pending and are marked
  ``decided`` when the live review resolves (approve/stop/retry/reroll/
  next_candidate/interrupt). After a crash, the executor future is gone;
  the route surfaces the snapshot as ``durable: true`` and
  ``actionable: false``. It is read-only recovery inventory: inspect saved
  candidates/checkpoints and resume manually. It is never silently dropped
  or presented as an actionable approval/retry.

Lightweight-value guarantee: the same rejection rules as
``handoff_state._assert_lightweight_value`` apply — no tensors, models,
conditioning, or live object references may enter a snapshot.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

REVIEW_SNAPSHOT_FORMAT_VERSION = "h3_review_snapshot_v1"
BATCH_FORMAT_VERSION = "h3_candidate_batch_v1"
HANDOFF_FORMAT_VERSION = "h3_top_level_handoff_v1"

_SNAPSHOT_RE = re.compile(r"^review_[A-Za-z0-9._-]{1,128}\.json$")


def _assert_lightweight(key: str, value: Any) -> None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_lightweight(key, item)
        return
    if isinstance(value, dict):
        for child_key, child in value.items():
            _assert_lightweight(str(child_key), child)
        return
    raise ValueError(
        "Review inventory field %r must hold lightweight JSON data only; "
        "tensors and live objects are not allowed." % key)


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2,
                      sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _orchestration_dir(run_dir: str) -> str:
    return os.path.join(run_dir, "orchestration")


def _snapshot_path(run_dir: str, token: str) -> str:
    safe = str(token).strip()
    if not safe or len(safe) > 128:
        raise ValueError("Review token must be 1-128 characters.")
    name = "review_%s.json" % safe
    if not _SNAPSHOT_RE.match(name):
        raise ValueError("Review token is not a safe snapshot name.")
    return os.path.join(_orchestration_dir(run_dir), name)


def write_review_snapshot(
        run_dir: str, token: str, run_name: str, scene: int,
        candidates: list[dict[str, Any]], deadline: float | None,
        server_now: float) -> str:
    """Persist a lightweight pending-review snapshot (atomic replace).

    ``deadline`` may be None when the review has no timeout (wait forever).
    ``candidates`` entries must already be the public (tensor-free)
    projection: number/revision/seed/created_at/has_audio/warning.
    """
    for candidate in candidates:
        for key in ("number", "revision", "seed", "created_at",
                    "has_audio", "warning"):
            if key in candidate:
                _assert_lightweight(key, candidate[key])
    snapshot = {
        "format": REVIEW_SNAPSHOT_FORMAT_VERSION,
        "token": str(token),
        "run_name": str(run_name),
        "scene": int(scene),
        "status": "pending",
        "candidates": [
            {key: candidate[key] for key in (
                "number", "revision", "seed", "created_at",
                "has_audio", "warning") if key in candidate}
            for candidate in candidates
        ],
        "deadline": (float(deadline) if deadline is not None else None),
        "server_now": float(server_now),
    }
    for key, value in snapshot.items():
        _assert_lightweight(key, value)
    path = _snapshot_path(run_dir, token)
    _atomic_json(path, snapshot)
    return path


def mark_review_snapshot_decided(run_dir: str, token: str,
                                 action: str, server_now: float) -> bool:
    """Mark a pending snapshot decided (idempotent; False if absent)."""
    path = _snapshot_path(run_dir, token)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(snapshot, dict) or \
            snapshot.get("format") != REVIEW_SNAPSHOT_FORMAT_VERSION:
        return False
    if snapshot.get("status") != "pending":
        return False
    snapshot["status"] = "decided"
    snapshot["decision_action"] = str(action)
    snapshot["decided_at"] = float(server_now)
    _atomic_json(path, snapshot)
    return True


def load_review_snapshots(run_dir: str) -> list[dict[str, Any]]:
    """All valid review snapshots for a run (corrupt files are skipped)."""
    directory = _orchestration_dir(run_dir)
    if not os.path.isdir(directory):
        return []
    results = []
    for name in sorted(os.listdir(directory)):
        if not name.startswith("review_") or not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "r",
                      encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("format") != REVIEW_SNAPSHOT_FORMAT_VERSION:
            continue
        if not isinstance(snapshot.get("token"), str):
            continue
        results.append(snapshot)
    return results


def load_batch_inventory(run_dir: str) -> list[dict[str, Any]]:
    """Durable candidate-batch records for a run (M4/M5 source of truth).

    Returns ``h3_candidate_batch_v1`` records plus the durable
    ``next_candidate`` / ``await_review`` handoff records.  Corrupt
    records are skipped, never repaired, and never queued.
    """
    inventories: list[dict[str, Any]] = []
    directory = _orchestration_dir(run_dir)
    if not os.path.isdir(directory):
        return inventories
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), "r",
                      encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("format") == BATCH_FORMAT_VERSION:
            inventories.append(data)
        elif data.get("format") == HANDOFF_FORMAT_VERSION and \
                data.get("action") in ("next_candidate", "await_review"):
            inventories.append(data)
    return inventories
