"""Dependency-aware discovery and deletion for H3 scene checkpoints."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any


_REVISION = re.compile(r"clip_(\d{4})\.([0-9a-f]{32})\.json")
_ACTIVE = re.compile(r"clip_(\d{4})\.json")
_ARTIFACT_KEYS = (
    "segment", "checkpoint", "prompt_file", "generated_audio",
    "blend_segment", "revision_metadata",
)
_ARTIFACT_KINDS = {
    "segment": "Segment video",
    "checkpoint": "Continuation checkpoint",
    "prompt_file": "Prompt snapshot",
    "generated_audio": "Generated audio",
    "blend_segment": "Blend-ready video",
    "revision_metadata": "Revision metadata",
    "review_preview": "Review preview",
    "active_pointer": "Active scene pointer",
    "archive_plan": "Recovery Plan snapshot",
    "archive_workflow": "Recovery workflow snapshot",
    "archive_api_prompt": "Recovery API prompt snapshot",
}
_ARCHIVE_KEYS = {
    "plan": "archive_plan",
    "workflow": "archive_workflow",
    "api_prompt": "archive_api_prompt",
}


def checkpoint_audio_context_length(
        continuation: str, visual: int, audio: int,
        generated_continuity: str = "", source_audio_target: str = "") -> int:
    """Interpret saved predecessor audio using the generation contract.

    Legacy saves recorded the configured audio span even when AV conditioning
    returned early with no picture prefix. AV modes consume a joint prefix;
    Guide modes can consume audio alone. Unknown modes/policies retain their
    recorded dependency rather than treating missing evidence as independence.
    This must never consult a mutable Plan to reinterpret an existing take.
    """
    if generated_continuity == "off" or source_audio_target == "locked":
        return 0
    if visual >= 0 and continuation in (
            "masked_av", "tapered_av", "feathered_av", "audio_feathered_av",
            "drift_control_av", "color_stable_drift_av"):
        return visual if audio > 0 else audio
    return audio


class _RunMutationLock:
    """Re-entrant thread and process lock for one Run's mutable pointers."""

    def __init__(self, path: str):
        self.path = path
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


_RUN_LOCKS: dict[tuple[str, str], _RunMutationLock] = {}
_RUN_LOCKS_GUARD = threading.Lock()
_LOG = logging.getLogger("minimax_h3_context_loop.checkpoints")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._-")[:96]


def _strict_run_name(value: Any) -> str:
    requested = str(value or "").strip()
    normalized = _safe_name(requested)
    if not normalized:
        raise ValueError("A non-empty H3 chain run_name is required.")
    if requested != normalized:
        raise ValueError("Checkpoint operation requires the exact saved run name.")
    return normalized


def checkpoint_run_lock(output_root: str, run_name: Any) -> _RunMutationLock:
    """Return the cross-process mutation lock shared by save and delete."""
    root = os.path.realpath(os.path.abspath(output_root))
    run = _strict_run_name(run_name)
    key = (root, run)
    with _RUN_LOCKS_GUARD:
        lock_path = os.path.join(
            root, "h3_chains", ".run_locks", run + ".lock")
        return _RUN_LOCKS.setdefault(key, _RunMutationLock(lock_path))


def checkpoint_revision_token(scene: Any, segment: Any) -> str:
    """Return the immutable revision id used by current and legacy saves.

    Builds before revision sidecars stored the transaction id only in the
    versioned MP4 and safetensors filenames. Both artifacts were committed by
    the same transaction, so a matching filename token is the original
    revision identity rather than a newly invented one.
    """
    if not isinstance(segment, dict):
        return ""
    try:
        index = int(scene)
    except (TypeError, ValueError):
        return ""
    stored = str(segment.get("revision") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", stored):
        return stored
    tokens = set()
    patterns = {
        "segment": r"clip_%04d\.([0-9a-f]{32})\.mp4" % index,
        "checkpoint": r"clip_%04d\.([0-9a-f]{32})\.safetensors" % index,
    }
    for key, pattern in patterns.items():
        name = os.path.basename(str(segment.get(key) or "")).lower()
        match = re.fullmatch(pattern, name)
        if match is not None:
            tokens.add(match.group(1))
    return next(iter(tokens)) if len(tokens) == 1 else ""


class CheckpointDeleteBlocked(ValueError):
    """Deletion was safe to inspect but is blocked by checkpoint state."""

    def __init__(self, message: str, preview: dict[str, Any]):
        super().__init__(message)
        self.preview = preview


class CheckpointGraphManager:
    """Build lineage graphs and remove only dependency-free revisions."""

    def __init__(self, output_root: str):
        self.output_root = os.path.realpath(os.path.abspath(output_root))
        self.chains_root = os.path.realpath(os.path.join(
            self.output_root, "h3_chains"))

    @staticmethod
    def _inside(root: str, path: str) -> bool:
        try:
            return os.path.commonpath([root, path]) == root
        except ValueError:
            return False

    def _run_dir(self, run_name: Any) -> tuple[str, str]:
        run = _strict_run_name(run_name)
        path = os.path.realpath(os.path.join(self.chains_root, run))
        if not self._inside(self.output_root, path):
            raise ValueError("H3 checkpoint run path escapes the output directory.")
        return path, run

    def _artifact_path(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Checkpoint artifact path is empty.")
        path = os.path.realpath(
            text if os.path.isabs(text) else os.path.join(self.output_root, text))
        if not self._inside(self.output_root, path):
            raise ValueError("Checkpoint artifact path escapes the output directory.")
        return path

    def _output_item(self, path: str) -> dict[str, str]:
        relative = os.path.relpath(path, self.output_root)
        return {
            "filename": os.path.basename(relative),
            "subfolder": os.path.dirname(relative),
            "type": "output",
        }

    @staticmethod
    def _read_json(path: str) -> Any:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _atomic_json(path: str, value: Any) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = "%s.write.%s.tmp" % (path, uuid.uuid4().hex)
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
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

    @staticmethod
    def _integer(value: Any, fallback: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(fallback)

    @staticmethod
    def _created_at(metadata: dict[str, Any], path: str) -> str:
        stored = str(metadata.get("created_at") or
                     metadata.get("segment", {}).get("created_at") or "")
        if stored:
            return stored
        return datetime.fromtimestamp(
            os.path.getmtime(path), timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z")

    @classmethod
    def _chapter_starts(cls, run_dir: str) -> set[int]:
        """Read editorial branch roots without making notes executable state."""
        path = os.path.join(run_dir, "editorial.json")
        try:
            document = cls._read_json(path)
            chapters = document.get("chapters")
            if not isinstance(chapters, list):
                return set()
            return {
                int(chapter.get("start_scene", 0))
                for chapter in chapters if isinstance(chapter, dict)
                and int(chapter.get("start_scene", 0)) > 0
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError,
                AttributeError):
            return set()

    def _effective_context(
            self, scene: int, segment: dict[str, Any],
            compatibility: dict[str, Any]) -> tuple[str, int, int]:
        """Return context that actually depended on the preceding checkpoint."""
        continuation = str(
            segment.get("continuation_mode") or
            compatibility.get("continuation_mode") or "guide")
        context = self._integer(
            segment.get("resolved_context_length",
                        segment.get("context_length",
                                    compatibility.get("context_length", 0))))
        has_resolved_audio = "resolved_audio_context_length" in segment
        if has_resolved_audio:
            audio_context = self._integer(
                segment.get("resolved_audio_context_length"))
        elif "audio_context_length" in segment:
            audio_context = self._integer(segment.get("audio_context_length"))
        elif "audio_context_length" in compatibility:
            audio_context = self._integer(
                compatibility.get("audio_context_length"))
        else:
            audio_context = context
        if scene <= 1:
            return continuation, 0, 0
        audio_mode = str(compatibility.get("audio_mode") or "source_track")
        if (not has_resolved_audio and continuation != "masked_av" and
                audio_mode not in (
                    "generated_audio", "source_plus_timeline")):
            audio_context = 0
        audio_context = checkpoint_audio_context_length(
            continuation, context, audio_context,
            str(segment.get("generated_continuity") or "").lower(),
            str(segment.get("source_audio_target") or "").lower())
        return continuation, max(0, context), max(0, audio_context)

    def _adopt_legacy_active_revisions(
            self, checkpoint_dir: str, run: str) -> int:
        """Create the small revision sidecars omitted by pre-manager saves.

        Existing video, prompt, audio, and safetensors artifacts remain in
        place. The canonical ``clip_NNNN.json`` pointer is also left byte-for-
        byte unchanged so adopting an old run is non-destructive.
        """
        if not os.path.isdir(checkpoint_dir):
            return 0
        active: dict[int, tuple[str, dict[str, Any], str]] = {}
        for filename in sorted(os.listdir(checkpoint_dir)):
            match = _ACTIVE.fullmatch(filename)
            if match is None:
                continue
            path = os.path.realpath(os.path.join(checkpoint_dir, filename))
            try:
                metadata = self._read_json(path)
                segment = metadata.get("segment")
                scene = int(match.group(1))
                if (not isinstance(segment, dict) or
                        int(segment.get("index", -1)) != scene or
                        str(metadata.get("run_name") or run).strip() != run):
                    continue
                revision = checkpoint_revision_token(scene, segment)
                if revision:
                    active[scene] = (revision, metadata, path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError,
                    AttributeError):
                continue

        adopted = 0
        for scene in sorted(active):
            revision, metadata, pointer_path = active[scene]
            sidecar = os.path.realpath(os.path.join(
                checkpoint_dir, "clip_%04d.%s.json" % (scene, revision)))
            if os.path.isfile(sidecar):
                continue
            segment = dict(metadata["segment"])
            segment.update({
                "revision": revision,
                "revision_metadata": os.path.relpath(sidecar, self.output_root),
            })
            predecessor = active.get(scene - 1)
            if predecessor is not None and not segment.get(
                    "predecessor_revision"):
                predecessor_revision, predecessor_metadata, _pointer = predecessor
                predecessor_segment = predecessor_metadata.get("segment")
                segment["predecessor_revision"] = predecessor_revision
                if isinstance(predecessor_segment, dict):
                    predecessor_hash = str(
                        predecessor_segment.get("checkpoint_sha256") or "")
                    if predecessor_hash:
                        segment["predecessor_checkpoint_sha256"] = predecessor_hash
                    segment.setdefault(
                        "branch_id", str(predecessor_segment.get("branch_id") or
                                         predecessor_revision))
            snapshot = dict(metadata)
            snapshot["segment"] = segment
            snapshot.setdefault(
                "created_at", self._created_at(metadata, pointer_path))
            snapshot["legacy_adoption"] = {
                "version": 1,
                "source": "active_pointer",
                "source_metadata": os.path.relpath(
                    pointer_path, self.output_root),
            }
            self._atomic_json(sidecar, snapshot)
            adopted += 1
        if adopted:
            _LOG.info(
                "H3 Checkpoint Manager adopted %d legacy active revision%s for "
                "run %s without copying media or latent files.",
                adopted, "" if adopted == 1 else "s", run)
        return adopted

    @classmethod
    def select_active_lineage(
            cls, segments: dict[int, dict[str, Any]], chapter_starts=()
            ) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
        """Select coherent chapter prefixes, without modifying saved pointers.

        A single-scene rerender can leave canonical pointers belonging to the
        old branch. Their files remain recoverable, but they are not selected
        until that lineage is explicitly activated or attributed again.
        """
        active, stale = {}, {}
        roots = {1, *chapter_starts}
        for scene, segment in sorted(segments.items()):
            reason = ""
            if scene not in roots:
                parent = active.get(scene - 1)
                if parent is None:
                    reason = "The preceding scene is not on the active chapter branch."
                else:
                    expected = str(
                        segment.get("predecessor_revision") or "").lower()
                    digest = str(
                        segment.get("predecessor_checkpoint_sha256") or "")
                    if expected and expected != checkpoint_revision_token(
                            scene - 1, parent):
                        reason = "This take belongs to a different predecessor revision."
                    elif digest and digest != str(
                            parent.get("checkpoint_sha256") or ""):
                        reason = "This take belongs to a different predecessor checkpoint."
            if reason:
                stale[scene] = reason
            else:
                active[scene] = segment
        return active, stale

    def active_selection(
            self, run_name: Any) -> tuple[dict[int, str], dict[int, str]]:
        """Cheap, read-only selection shared by recovery and the UI."""
        run_dir, _run = self._run_dir(run_name)
        checkpoint_dir = os.path.join(run_dir, "checkpoints")
        segments = {}
        if os.path.isdir(checkpoint_dir):
            for filename in os.listdir(checkpoint_dir):
                match = _ACTIVE.fullmatch(filename)
                if match is None:
                    continue
                try:
                    segment = self._read_json(os.path.join(
                        checkpoint_dir, filename)).get("segment")
                    scene = int(match.group(1))
                    if (isinstance(segment, dict) and
                            int(segment.get("index", -1)) == scene):
                        segments[scene] = segment
                except (OSError, TypeError, ValueError, AttributeError):
                    continue
        active, stale = self.select_active_lineage(
            segments, self._chapter_starts(run_dir))
        return {scene: checkpoint_revision_token(scene, segment)
                for scene, segment in active.items()}, stale

    def _active_revisions(self, checkpoint_dir: str) -> dict[int, str]:
        active: dict[int, str] = {}
        if not os.path.isdir(checkpoint_dir):
            return active
        for filename in os.listdir(checkpoint_dir):
            match = _ACTIVE.fullmatch(filename)
            if match is None:
                continue
            try:
                metadata = self._read_json(os.path.join(checkpoint_dir, filename))
                segment = metadata.get("segment")
                if not isinstance(segment, dict):
                    continue
                scene = int(match.group(1))
                if int(segment.get("index", -1)) != scene:
                    continue
                revision = checkpoint_revision_token(scene, segment)
                if revision:
                    active[scene] = revision
            except (OSError, TypeError, ValueError, json.JSONDecodeError,
                    AttributeError):
                continue
        return active

    def _recorded_dependency_keys(
            self, record: dict[str, Any],
            parent_key: tuple[int, str] | None,
            records: dict[tuple[int, str], dict[str, Any]],
            by_hash: dict[tuple[int, str], list[tuple[int, str]]]
            ) -> list[tuple[int, str]]:
        """Resolve saved visual and generated-audio dependencies exactly."""
        segment = record["_segment"]
        scene = int(record["scene"])
        found: list[tuple[int, str]] = []

        def add(scene_value: Any, revision_value: Any,
                hash_value: Any) -> None:
            try:
                source_scene = int(scene_value)
            except (TypeError, ValueError):
                return
            revision = str(revision_value or "").lower()
            key = ((source_scene, revision)
                   if re.fullmatch(r"[0-9a-f]{32}", revision) else None)
            if key not in records:
                digest = str(hash_value or "")
                candidates = by_hash.get((source_scene, digest), [])
                key = candidates[0] if digest and len(candidates) == 1 else None
            if key in records and key not in found:
                found.append(key)

        visual_frames = max(0, self._integer(record.get("context_length")))
        if visual_frames:
            visual_blocks = segment.get("visual_context_blocks")
            if isinstance(visual_blocks, list):
                for block in visual_blocks:
                    if not isinstance(block, dict):
                        continue
                    add(block.get("source_scene"),
                        block.get("source_revision"),
                        block.get("source_checkpoint_sha256"))
            else:
                source_scene = self._integer(
                    segment.get("visual_context_source_scene"), scene - 1)
                if source_scene == scene - 1 and not segment.get(
                        "visual_context_source_revision"):
                    if parent_key in records:
                        found.append(parent_key)
                else:
                    add(source_scene,
                        segment.get("visual_context_source_revision"),
                        segment.get(
                            "visual_context_source_checkpoint_sha256"))
                lead_frames = max(0, self._integer(
                    segment.get("visual_context_lead_frames")))
                if lead_frames:
                    add(segment.get("visual_context_lead_source_scene"),
                        segment.get("visual_context_lead_source_revision"),
                        segment.get(
                            "visual_context_lead_checkpoint_sha256"))

        dependency = record["_metadata"].get("scene_dependency")
        scopes = dependency.get("scopes") if isinstance(dependency, dict) else None
        incoming = (scopes.get("incoming_boundary")
                    if isinstance(scopes, dict) else None)
        generated = str(
            segment.get("generated_continuity",
                        incoming.get("generated_continuity")
                        if isinstance(incoming, dict) else "off") or "off").lower()
        audio_frames = max(0, self._integer(
            segment.get("resolved_audio_context_length",
                        incoming.get("audio_context_length"))
            if isinstance(incoming, dict) else
            record.get("audio_context_length")))
        audio_frames = checkpoint_audio_context_length(
            str(record.get("continuation_mode") or ""), visual_frames,
            audio_frames, generated,
            str(segment.get("source_audio_target") or "").lower())
        if generated == "on" and audio_frames and parent_key in records:
            if parent_key not in found:
                found.append(parent_key)
        return found

    def _scan(self, run_name: Any, *, adopt_legacy: bool = True) -> dict[str, Any]:
        run_dir, run = self._run_dir(run_name)
        checkpoint_dir = os.path.join(run_dir, "checkpoints")
        review_dir = os.path.join(run_dir, "reviews")
        chapter_starts = self._chapter_starts(run_dir)
        if adopt_legacy:
            self._adopt_legacy_active_revisions(checkpoint_dir, run)
        active = self._active_revisions(checkpoint_dir)
        selected, stale = self.active_selection(run)
        records: dict[tuple[int, str], dict[str, Any]] = {}
        if os.path.isdir(checkpoint_dir):
            for filename in sorted(os.listdir(checkpoint_dir)):
                match = _REVISION.fullmatch(filename)
                if match is None:
                    continue
                metadata_path = os.path.realpath(os.path.join(
                    checkpoint_dir, filename))
                try:
                    metadata = self._read_json(metadata_path)
                    segment = metadata.get("segment")
                    if not isinstance(segment, dict):
                        continue
                    scene = int(segment.get("index", int(match.group(1))))
                    revision = str(
                        segment.get("revision") or match.group(2)).lower()
                    stored_run = str(metadata.get("run_name") or run).strip()
                    if (scene != int(match.group(1)) or
                            revision != match.group(2) or stored_run != run):
                        continue
                    compatibility = metadata.get("compatibility")
                    if not isinstance(compatibility, dict):
                        compatibility = {}
                    adoption = metadata.get("adoption")
                    if not isinstance(adoption, dict):
                        adoption = {}
                    continuation, context, audio_context = (
                        self._effective_context(scene, segment, compatibility))
                    segment_path = self._artifact_path(segment.get("segment"))
                    checkpoint_path = self._artifact_path(
                        segment.get("checkpoint"))
                    record = {
                        "scene": scene,
                        "scene_id": str(
                            segment.get("id") or "clip_%04d" % scene),
                        "revision": revision,
                        "active": selected.get(scene) == revision,
                        "pointer_active": active.get(scene) == revision,
                        "inactive_reason": (
                            stale.get(scene, "")
                            if active.get(scene) == revision else ""),
                        "ready": (os.path.isfile(segment_path) and
                                  os.path.isfile(checkpoint_path)),
                        "raw_frames": self._integer(
                            segment.get("raw_frames")),
                        "delivered_frames": self._integer(
                            segment.get("delivered_frames")),
                        "seed": str(segment.get("seed") or ""),
                        "steps": self._integer(segment.get("steps")),
                        "created_at": self._created_at(metadata, metadata_path),
                        "branch_id": str(segment.get("branch_id") or ""),
                        "forked_from_branch_id": str(
                            segment.get("forked_from_branch_id") or ""),
                        "adopted_from_revision": str(
                            segment.get("adopted_from_revision") or
                            adoption.get("source_revision") or "").lower(),
                        "predecessor_revision": str(
                            segment.get("predecessor_revision") or "").lower(),
                        "predecessor_checkpoint_sha256": str(
                            segment.get("predecessor_checkpoint_sha256") or ""),
                        "checkpoint_sha256": str(
                            segment.get("checkpoint_sha256") or ""),
                        "segment_sha256": str(
                            segment.get("segment_sha256") or ""),
                        "continuation_mode": continuation,
                        "context_length": context,
                        "audio_context_length": audio_context,
                        "compatibility": {
                            key: compatibility[key] for key in (
                                "width", "height", "fps", "audio_mode",
                                "generation_fingerprint", "encode_mode",
                                "anchor_mode", "crop")
                            if key in compatibility
                        },
                        "prompt_preview": re.sub(
                            r"\s+", " ", str(
                                segment.get("scene_prompt") or
                                segment.get("prompt") or "").strip())[:240],
                        "prompt": str(segment.get("prompt") or
                                      segment.get("scene_prompt") or ""),
                        "take_kind": str(segment.get("take_kind") or
                                         "generation"),
                        "alternate_of_revision": str(
                            segment.get("alternate_of_revision") or "").lower(),
                        "alternate_media_mode": str(
                            segment.get("alternate_media_mode") or ""),
                        "_metadata": metadata,
                        "_metadata_path": metadata_path,
                        "_segment": segment,
                        "_segment_path": segment_path,
                        "_checkpoint_path": checkpoint_path,
                        "_chapter_root": scene in chapter_starts,
                        "_parent": None,
                        "_children": [],
                        "_dependencies": [],
                        "_dependents": [],
                        "_lineage_issue": "",
                    }
                    records[(scene, revision)] = record
                except (OSError, TypeError, ValueError, json.JSONDecodeError,
                        AttributeError):
                    continue

        by_hash: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
        for key, record in records.items():
            digest = record["checkpoint_sha256"]
            if digest:
                by_hash[(record["scene"], digest)].append(key)
        for key, record in records.items():
            scene = record["scene"]
            if scene <= 1:
                continue
            revision = record["predecessor_revision"]
            digest = record["predecessor_checkpoint_sha256"]
            parent_key = (scene - 1, revision) if revision else None
            parent = records.get(parent_key) if parent_key else None
            if parent is None and digest:
                candidates = by_hash.get((scene - 1, digest), [])
                if len(candidates) == 1:
                    parent_key = candidates[0]
                    parent = records[parent_key]
            if parent is None:
                dependencies = self._recorded_dependency_keys(
                    record, None, records, by_hash)
                for dependency_key in dependencies:
                    record["_dependencies"].append(dependency_key)
                    records[dependency_key]["_dependents"].append(key)
                if not record["_chapter_root"]:
                    record["_lineage_issue"] = "missing predecessor"
                continue
            if digest and parent["checkpoint_sha256"] != digest:
                record["_lineage_issue"] = "predecessor hash mismatch"
                # Keep the declared revision edge for conservative cleanup:
                # corrupted lineage must never make its parent look deletable.
            # A chapter start is a branch root even though immutable metadata
            # keeps its original predecessor as provenance. Preserve a
            # deletion dependency only when that scene actually consumed
            # predecessor picture/generated-audio context.
            dependencies = self._recorded_dependency_keys(
                record, parent_key, records, by_hash)
            if not record["_chapter_root"]:
                record["_parent"] = parent_key
                parent["_children"].append(key)
                if parent_key not in dependencies:
                    dependencies.append(parent_key)
            for dependency_key in dependencies:
                record["_dependencies"].append(dependency_key)
                records[dependency_key]["_dependents"].append(key)

        # An editorial alternate is not a lineage child, but its immutable
        # files are meaningful only while the same-scene base revision still
        # exists. Model that as a cleanup dependency so Checkpoint Manager
        # requires deleting unused alternates before their base take.
        for key, record in records.items():
            if record.get("take_kind") != "editorial_alternate":
                continue
            base_key = (record["scene"], record["alternate_of_revision"])
            base = records.get(base_key)
            if base is None:
                if not record["_lineage_issue"]:
                    record["_lineage_issue"] = "missing alternate base"
                continue
            if base_key not in record["_dependencies"]:
                record["_dependencies"].append(base_key)
            if key not in base["_dependents"]:
                base["_dependents"].append(key)

        try:
            review_names = os.listdir(review_dir) if os.path.isdir(
                review_dir) else []
        except OSError:
            review_names = []
        segment_hash_counts: dict[str, int] = defaultdict(int)
        artifact_path_counts: dict[str, int] = defaultdict(int)
        for record in records.values():
            if record["segment_sha256"]:
                segment_hash_counts[record["segment_sha256"]] += 1
            for artifact_key in _ARTIFACT_KEYS:
                artifact_value = record["_segment"].get(artifact_key)
                if not isinstance(artifact_value, str) or not artifact_value:
                    continue
                try:
                    artifact_path_counts[self._artifact_path(artifact_value)] += 1
                except ValueError:
                    continue
            archives = record["_metadata"].get("archives")
            if isinstance(archives, dict):
                for archive_key in _ARCHIVE_KEYS:
                    archive_value = archives.get(archive_key)
                    if not isinstance(archive_value, str) or not archive_value:
                        continue
                    try:
                        artifact_path_counts[
                            self._artifact_path(archive_value)] += 1
                    except ValueError:
                        continue
        return {
            "run_dir": run_dir,
            "run_name": run,
            "checkpoint_dir": checkpoint_dir,
            "review_dir": review_dir,
            "review_names": review_names,
            "chapter_starts": chapter_starts,
            "records": records,
            "segment_hash_counts": segment_hash_counts,
            "artifact_path_counts": artifact_path_counts,
        }

    @staticmethod
    def _attribution_source(record: dict[str, Any]) -> str:
        return str(record.get("adopted_from_revision") or
                   record.get("revision") or "").lower()

    def _attributable_without_predecessor(
            self, record: dict[str, Any]) -> bool:
        """Return whether a saved scene can safely receive another parent."""
        dependency = record["_metadata"].get("scene_dependency")
        if not isinstance(dependency, dict):
            dependency = record["_segment"].get("scene_dependency")
        scopes = dependency.get("scopes") if isinstance(dependency, dict) else None
        incoming = (scopes.get("incoming_boundary")
                    if isinstance(scopes, dict) else None)
        segment = record["_segment"]
        incoming = incoming if isinstance(incoming, dict) else {}
        # Resolved values describe the saved take; old scope snapshots can
        # still contain inherited defaults. Never consult today's edited Plan.
        visual = segment.get("resolved_context_length", incoming.get(
            "context_length", record.get("context_length")))
        audio = segment.get("resolved_audio_context_length", incoming.get(
            "audio_context_length", record.get("audio_context_length")))
        generated = str(segment.get("generated_continuity", incoming.get(
            "generated_continuity", ""))).lower()
        try:
            visual, audio = int(visual), int(audio)
        except (TypeError, ValueError):
            return False
        if visual < 0 or audio < 0:
            return False
        compatibility = record["_metadata"].get("compatibility")
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        continuation = str(segment.get("continuation_mode") or
                           incoming.get("continuation_mode") or
                           compatibility.get("continuation_mode") or
                           record.get("continuation_mode") or "")
        audio = checkpoint_audio_context_length(
            continuation, visual, audio, generated,
            str(segment.get("source_audio_target") or "").lower())
        # Unknown legacy audio policy must not silently mean 'off'.
        return visual == 0 and (audio == 0 or (audio > 0 and generated == "off"))

    def _artifacts(self, scan: dict[str, Any], record: dict[str, Any]
                   ) -> list[dict[str, Any]]:
        run_dir = scan["run_dir"]
        allowed_roots = [os.path.realpath(os.path.join(run_dir, name)) for name in (
            "segments", "checkpoints", "generated_audio", "blend_segments",
            "reviews", "recovery_archives")]
        paths: dict[str, tuple[str, bool]] = {
            record["_metadata_path"]: ("revision_metadata", False),
        }
        for key in _ARTIFACT_KEYS:
            value = record["_segment"].get(key)
            if not isinstance(value, str) or not value:
                continue
            path = self._artifact_path(value)
            paths.setdefault(path, (key, False))
        archives = record["_metadata"].get("archives")
        if isinstance(archives, dict):
            for key, kind in _ARCHIVE_KEYS.items():
                value = archives.get(key)
                if not isinstance(value, str) or not value:
                    continue
                paths.setdefault(self._artifact_path(value), (kind, False))
        video_hash = record["segment_sha256"][:12]
        if video_hash and os.path.isdir(scan["review_dir"]):
            prefix = "clip_%04d.%s." % (record["scene"], video_hash)
            shared = scan["segment_hash_counts"].get(
                record["segment_sha256"], 0) > 1
            for candidate in scan["review_names"]:
                if candidate.startswith(prefix) and candidate.endswith(
                        ".review.mp4"):
                    path = os.path.realpath(os.path.join(
                        scan["review_dir"], candidate))
                    paths[path] = ("review_preview", shared)

        expected_prefix = "clip_%04d.%s" % (
            record["scene"], record["revision"])
        expected_archive_root = os.path.realpath(os.path.join(
            run_dir, "recovery_archives", record["revision"]))
        recovery_root = os.path.realpath(os.path.join(
            run_dir, "recovery_archives"))
        # Older saves reference the mutable Run-level snapshots. They are
        # valid recovery references, but never belong to one revision (even
        # when only one revision still references them). Keep this whitelist
        # exact and kind-specific; do not allow arbitrary files at Run root
        # or follow a snapshot symlink into another file's ownership.
        shared_archive_paths = {
            kind: os.path.join(run_dir, key + ".json")
            for key, kind in _ARCHIVE_KEYS.items()
        }
        canonical = os.path.realpath(os.path.join(
            scan["checkpoint_dir"], "clip_%04d.json" % record["scene"]))
        artifacts = []
        for path, (kind, shared) in sorted(paths.items()):
            is_shared_archive = path == shared_archive_paths.get(kind)
            if path == canonical:
                raise ValueError("Refusing to manage an active checkpoint pointer.")
            if (not is_shared_archive and
                    not any(self._inside(root, path) for root in allowed_roots)):
                raise ValueError("Checkpoint revision owns an unexpected path.")
            path_references = scan["artifact_path_counts"].get(path, 0)
            shared = bool(shared or is_shared_archive or path_references > 1)
            adopted_prefix = "clip_%04d.%s" % (
                record["scene"], record.get("adopted_from_revision") or "")
            owns_named_path = os.path.basename(path).startswith(expected_prefix)
            adopts_named_path = bool(
                record.get("adopted_from_revision") and
                os.path.basename(path).startswith(adopted_prefix))
            is_archive_path = self._inside(recovery_root, path)
            owns_archive_path = (
                is_archive_path and os.path.dirname(path) ==
                expected_archive_root)
            if (not self._inside(scan["review_dir"], path) and
                    not owns_named_path and not adopts_named_path and
                    not is_archive_path and not is_shared_archive):
                raise ValueError(
                    "Checkpoint revision references a file owned by another revision.")
            try:
                exists = os.path.isfile(path)
                stat_result = os.stat(path) if exists else None
            except OSError:
                exists = False
                stat_result = None
            size = int(stat_result.st_size) if stat_result is not None else 0
            mtime_ns = (int(stat_result.st_mtime_ns)
                        if stat_result is not None else 0)
            artifacts.append({
                "kind": kind,
                "label": _ARTIFACT_KINDS.get(kind, kind.replace("_", " ").title()),
                "path": os.path.relpath(path, self.output_root),
                "exists": exists,
                "size_bytes": size,
                "shared": bool(shared),
                "owned": bool(not shared and (
                    not is_archive_path or owns_archive_path)),
                "_path": path,
                "_mtime_ns": mtime_ns,
            })
        return artifacts

    def _attribution_slot(
            self, records: dict[tuple[int, str], dict[str, Any]],
            leaf_key: tuple[int, str]) -> dict[str, Any] | None:
        leaf = records[leaf_key]
        scene = int(leaf["scene"]) + 1
        if any(record["scene"] == scene and record["_chapter_root"]
               for record in records.values()):
            return None
        already_attached = {
            self._attribution_source(records[key])
            for key in leaf["_children"] if key in records
        }
        candidates: dict[str, dict[str, Any]] = {}
        blocked = []
        for key, candidate in records.items():
            if key[0] != scene or key == leaf_key or not candidate["ready"]:
                continue
            if candidate.get("take_kind") == "editorial_alternate":
                continue
            if candidate["_parent"] == leaf_key:
                continue
            if not self._attributable_without_predecessor(candidate):
                blocked.append({
                    "scene": scene, "revision": candidate["revision"],
                    "reason": "Saved predecessor video/audio context is in use, "
                              "or the saved metadata cannot prove it was unused. "
                              "Changing the current Plan does not change a saved take.",
                })
                continue
            source = self._attribution_source(candidate)
            if not source or source in already_attached:
                continue
            previous = candidates.get(source)
            if previous is None or candidate["revision"] == source:
                candidates[source] = candidate
        if not candidates and not blocked:
            return None
        ordered = sorted(candidates.values(), key=lambda item: (
            str(item.get("created_at") or ""), item["revision"]), reverse=True)
        return {
            "scene": scene,
            "parent_scene": leaf["scene"],
            "parent_revision": leaf["revision"],
            "blocked_candidates": blocked,
            "candidates": [
                {"scene": item["scene"], "revision": item["revision"]}
                for item in ordered
            ],
        }

    def _active_pointer_artifact(
            self, scan: dict[str, Any], record: dict[str, Any]
    ) -> dict[str, Any]:
        """Describe the mutable pointer removed by an active-tip rollback."""
        path = os.path.realpath(os.path.join(
            scan["checkpoint_dir"], "clip_%04d.json" % record["scene"]))
        if not self._inside(scan["checkpoint_dir"], path):
            raise ValueError("Active checkpoint pointer escapes its run directory.")
        exists = os.path.isfile(path)
        stat_result = os.stat(path) if exists else None
        return {
            "kind": "active_pointer",
            "label": _ARTIFACT_KINDS["active_pointer"],
            "path": os.path.relpath(path, self.output_root),
            "exists": exists,
            "size_bytes": int(stat_result.st_size) if stat_result else 0,
            "shared": False,
            "owned": True,
            "_path": path,
            "_mtime_ns": int(stat_result.st_mtime_ns) if stat_result else 0,
        }

    @staticmethod
    def _descendant_keys(records: dict[tuple[int, str], dict[str, Any]],
                         start: tuple[int, str]) -> list[tuple[int, str]]:
        found = []
        queue = deque(records[start]["_dependents"])
        seen = set()
        while queue:
            key = queue.popleft()
            if key in seen or key not in records:
                continue
            seen.add(key)
            found.append(key)
            queue.extend(records[key]["_dependents"])
        return sorted(found, key=lambda item: (item[0], item[1]))

    @staticmethod
    def _chapter_bounds(scan: dict[str, Any], scene: int) -> tuple[int, int]:
        records = scan["records"]
        maximum = max((int(item[0]) for item in records), default=int(scene))
        starts = sorted({1, *(
            int(item) for item in scan.get("chapter_starts", set())
            if int(item) > 0
        )})
        start = max((item for item in starts if item <= scene), default=1)
        following = next((item for item in starts if item > scene), None)
        return start, (following - 1 if following is not None else maximum)

    def _public_graph(self, scan: dict[str, Any]) -> dict[str, Any]:
        records = scan["records"]
        lineage_keys = {
            key for key, record in records.items()
            if record.get("take_kind") != "editorial_alternate"
        }
        leaves = [
            key for key in lineage_keys
            if not any(child in lineage_keys
                       for child in records[key]["_children"])
            or (records[key]["active"] and not any(
                child in lineage_keys and records[child]["active"]
                for child in records[key]["_children"]))
        ]
        branch_paths = []
        memberships: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
        for leaf_key in leaves:
            path = []
            cursor = leaf_key
            seen = set()
            while cursor in records and cursor not in seen:
                seen.add(cursor)
                if cursor in lineage_keys:
                    path.append(cursor)
                cursor = records[cursor]["_parent"]
            path.reverse()
            leaf = records[leaf_key]
            active = bool(path) and all(records[key]["active"] for key in path)
            branch_id = leaf["branch_id"] or leaf["revision"]
            branch = {
                "id": branch_id,
                "label": "Active branch" if active else "Branch %s" % branch_id[:8],
                "active": active,
                "leaf_scene": leaf["scene"],
                "leaf_revision": leaf["revision"],
                "path": [{"scene": key[0], "revision": key[1]} for key in path],
            }
            slot = self._attribution_slot(records, leaf_key)
            if slot is not None:
                branch["attribution_slot"] = slot
            branch_paths.append(branch)
            membership = {"id": branch["id"], "label": branch["label"],
                          "active": active}
            for key in path:
                memberships[key].append(membership)
        branch_paths.sort(key=lambda item: (
            not item["active"], -int(item["leaf_scene"]), item["id"]))

        public_records = []
        artifact_sizes: dict[str, int] = {}
        record_artifact_paths: dict[tuple[int, str], dict[str, int]] = {}
        for key in sorted(records):
            record = records[key]
            artifacts = self._artifacts(scan, record)
            record_artifact_paths[key] = {
                part["path"]: int(part["size_bytes"])
                for part in artifacts if part["exists"]
            }
            artifact_sizes.update(record_artifact_paths[key])
            reviews = [item for item in artifacts
                       if item["kind"] == "review_preview" and item["exists"]]
            video = (self._output_item(record["_segment_path"])
                     if os.path.isfile(record["_segment_path"]) else None)
            audio = None
            audio_value = record["_segment"].get("generated_audio")
            if isinstance(audio_value, str) and audio_value:
                audio_path = self._artifact_path(audio_value)
                if os.path.isfile(audio_path):
                    audio = self._output_item(audio_path)
            parent = record["_parent"]
            children = []
            for child_key in sorted(record["_children"]):
                child = records[child_key]
                if child.get("take_kind") == "editorial_alternate":
                    continue
                children.append({
                    "scene": child["scene"],
                    "scene_id": child["scene_id"],
                    "revision": child["revision"],
                    "continuation_mode": child["continuation_mode"],
                    "context_length": child["context_length"],
                    "audio_context_length": child["audio_context_length"],
                })
            item = {name: record[name] for name in (
                "scene", "scene_id", "revision", "active", "ready",
                "pointer_active", "inactive_reason",
                "raw_frames", "delivered_frames", "seed", "steps",
                "created_at", "branch_id", "forked_from_branch_id",
                "adopted_from_revision",
                "predecessor_revision",
                "predecessor_checkpoint_sha256", "checkpoint_sha256",
                "continuation_mode", "context_length", "audio_context_length",
                "compatibility", "prompt_preview", "prompt", "take_kind",
                "alternate_of_revision", "alternate_media_mode")}
            item.update({
                "size_bytes": sum(part["size_bytes"] for part in artifacts),
                "owned_size_bytes": sum(
                    part["size_bytes"] for part in artifacts if part["owned"]),
                "shared_size_bytes": sum(
                    part["size_bytes"] for part in artifacts if part["shared"]),
                "missing_files": [part["label"] for part in artifacts
                                  if not part["exists"]],
                "video": video,
                "audio": audio,
                "preview_video": (
                    self._output_item(reviews[-1]["_path"]) if reviews else None),
                "parent": ({"scene": parent[0], "revision": parent[1]}
                           if parent else None),
                "dependencies": [
                    {"scene": dependency[0], "revision": dependency[1]}
                    for dependency in record["_dependencies"]
                ],
                "children": children,
                "descendant_count": len(self._descendant_keys(records, key)),
                "branches": memberships.get(key, []),
                "lineage_status": record["_lineage_issue"] or (
                    "chapter root" if record["_chapter_root"] else
                    "root" if record["scene"] == 1 else "linked"),
                "metadata_path": os.path.relpath(
                    record["_metadata_path"], self.output_root),
            })
            public_records.append(item)
        try:
            editorial = self._read_json(os.path.join(
                scan["run_dir"], "editorial.json"))
            replacement_rows = editorial.get("replacements", [])
        except (OSError, TypeError, ValueError, json.JSONDecodeError,
                AttributeError):
            replacement_rows = []
        active_revisions = {
            int(item["scene"]): str(item["revision"])
            for item in public_records
            if item["active"] and item["take_kind"] != "editorial_alternate"
        }
        selected_alternates = {
            (self._integer(item.get("scene")),
             str(item.get("alternate_revision") or "").lower())
            for item in replacement_rows if isinstance(item, dict)
            and str(item.get("base_revision") or "").lower() ==
            active_revisions.get(self._integer(item.get("scene")), "")
        }
        by_key = {
            (int(item["scene"]), str(item["revision"])): item
            for item in public_records
        }
        for item in public_records:
            if item["take_kind"] == "editorial_alternate":
                item["used_in_final_cut"] = (
                    (int(item["scene"]), str(item["revision"]))
                    in selected_alternates)
                base = by_key.get((
                    int(item["scene"]), str(item["alternate_of_revision"])))
                if base is not None:
                    base.setdefault("alternates", []).append(item)
        for item in public_records:
            if item.get("alternates"):
                item["alternates"].sort(key=lambda alternate: (
                    not bool(alternate.get("used_in_final_cut")),
                    str(alternate.get("created_at") or "")))
        scenes = []
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in public_records:
            grouped[item["scene"]].append(item)
        for scene in sorted(grouped):
            revisions = grouped[scene]
            scene_artifacts: dict[str, int] = {}
            for revision in revisions:
                scene_artifacts.update(record_artifact_paths.get(
                    (int(revision["scene"]), str(revision["revision"])), {}))
            scenes.append({
                "scene": scene,
                "scene_id": next((item["scene_id"] for item in revisions
                                  if item["active"]), revisions[0]["scene_id"]),
                "revision_count": len(revisions),
                "active_revision": next((item["revision"] for item in revisions
                                         if item["active"]), ""),
                "bytes": sum(scene_artifacts.values()),
                "broken_count": sum(not item["ready"] for item in revisions),
            })
        graph_hash = _fingerprint([{
            "scene": item["scene"], "revision": item["revision"],
            "active": item["active"], "ready": item["ready"],
            "parent": item["parent"],
            "dependencies": item["dependencies"],
            "size_bytes": item["size_bytes"],
        } for item in public_records])
        return {
            "run_name": scan["run_name"],
            "graph_hash": graph_hash,
            "scenes": scenes,
            "branches": branch_paths,
            "revisions": public_records,
            "summary": {
                "scene_count": len(scenes),
                "revision_count": len(public_records),
                "branch_count": len(branch_paths),
                "bytes": sum(artifact_sizes.values()),
                "broken_count": sum(not item["ready"] for item in public_records),
            },
        }

    def graph(self, run_name: Any, *, adopt_legacy: bool = True) -> dict[str, Any]:
        run_dir, run = self._run_dir(run_name)
        if not os.path.isdir(run_dir):
            raise FileNotFoundError("H3 run %r does not exist." % run)
        return self._public_graph(self._scan(run, adopt_legacy=adopt_legacy))

    def attribute(
            self, run_name: Any, parent_scene: Any, parent_revision: Any,
            candidate_scene: Any, candidate_revision: Any) -> dict[str, Any]:
        """Attach an independent saved candidate to another branch tip.

        Only metadata is created. Video, audio, prompt, and checkpoint files
        remain shared with the original immutable candidate revision.
        """
        run_dir, run = self._run_dir(run_name)
        parent_scene_number = int(parent_scene)
        candidate_scene_number = int(candidate_scene)
        parent_token = str(parent_revision or "").strip().lower()
        candidate_token = str(candidate_revision or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", parent_token):
            raise ValueError("Parent revision must be a 32-character revision id.")
        if not re.fullmatch(r"[0-9a-f]{32}", candidate_token):
            raise ValueError("Candidate revision must be a 32-character revision id.")
        if candidate_scene_number != parent_scene_number + 1:
            raise ValueError(
                "A candidate can only be attributed to the immediately preceding scene slot.")
        with checkpoint_run_lock(self.output_root, run):
            if not os.path.isdir(run_dir):
                raise FileNotFoundError("H3 run %r does not exist." % run)
            scan = self._scan(run)
            records = scan["records"]
            parent_key = (parent_scene_number, parent_token)
            candidate_key = (candidate_scene_number, candidate_token)
            parent = records.get(parent_key)
            candidate = records.get(candidate_key)
            if parent is None:
                raise FileNotFoundError(
                    "Parent scene %d revision %s is no longer available." %
                    (parent_scene_number, parent_token[:8]))
            if candidate is None:
                raise FileNotFoundError(
                    "Candidate scene %d revision %s is no longer available." %
                    (candidate_scene_number, candidate_token[:8]))
            if (parent.get("take_kind") == "editorial_alternate" or
                    candidate.get("take_kind") == "editorial_alternate"):
                raise ValueError(
                    "Picture-only editorial alternates cannot be attached to "
                    "generation lineage. Select them in Plan Studio's final "
                    "cut instead.")
            if not parent["ready"] or not candidate["ready"]:
                raise ValueError(
                    "Both the target branch tip and candidate must have intact video and checkpoint files.")
            if candidate["_parent"] == parent_key:
                raise ValueError("This candidate is already attached to that branch.")
            if not self._attributable_without_predecessor(candidate):
                raise ValueError(
                    "This candidate consumes predecessor video or generated-audio context and cannot be safely attributed to another branch.")

            source_revision = self._attribution_source(candidate)
            for record in records.values():
                adoption = record["_metadata"].get("adoption")
                if not isinstance(adoption, dict):
                    continue
                if (record["scene"] == candidate_scene_number and
                        str(adoption.get("source_revision") or "").lower() ==
                        source_revision and
                        str(adoption.get("parent_revision") or "").lower() ==
                        parent_token):
                    return {
                        "ok": True,
                        "created": False,
                        "run_name": run,
                        "scene": record["scene"],
                        "revision": record["revision"],
                        "source_revision": source_revision,
                        "parent_scene": parent_scene_number,
                        "parent_revision": parent_token,
                        "message": "Candidate is already attributed to this branch.",
                    }

            revision = uuid.uuid4().hex
            created_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z")
            metadata = json.loads(_canonical_json(candidate["_metadata"]))
            segment = metadata.get("segment")
            if not isinstance(segment, dict):
                raise ValueError("Candidate revision metadata has no segment record.")
            metadata_path = os.path.join(
                scan["checkpoint_dir"],
                "clip_%04d.%s.json" % (candidate_scene_number, revision))
            segment.update({
                "revision": revision,
                "revision_metadata": os.path.relpath(
                    metadata_path, self.output_root),
                "predecessor_revision": parent_token,
                "predecessor_checkpoint_sha256": str(
                    parent.get("checkpoint_sha256") or ""),
                "branch_id": str(parent.get("branch_id") or parent_token),
                "forked_from_branch_id": str(
                    candidate.get("branch_id") or candidate_token),
                "adopted_from_revision": source_revision,
                "adopted_from_branch_id": str(
                    candidate.get("branch_id") or candidate_token),
            })
            metadata["created_at"] = created_at
            metadata["adoption"] = {
                "version": 1,
                "created_at": created_at,
                "source_scene": candidate_scene_number,
                "source_revision": source_revision,
                "candidate_revision": candidate_token,
                "parent_scene": parent_scene_number,
                "parent_revision": parent_token,
                "shared_artifacts": True,
            }
            self._atomic_json(metadata_path, metadata)
            return {
                "ok": True,
                "created": True,
                "run_name": run,
                "scene": candidate_scene_number,
                "revision": revision,
                "source_revision": source_revision,
                "parent_scene": parent_scene_number,
                "parent_revision": parent_token,
                "message": (
                    "Attributed scene %d candidate %s to branch tip %s; saved media and checkpoint files remain shared." %
                    (candidate_scene_number, candidate_token[:8],
                     parent_token[:8])),
            }

    def _chapter_references(self, scan: dict[str, Any], revision: str,
                            artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sealed delivery snapshots pin their recovery inputs, not just MP4s.

        Called under the same Run lock used to publish chapter manifests.
        Inspect small JSON documents only; never follow chapter symlinks.
        Unreadable snapshots block cleanup rather than risk losing recovery.
        """
        root = os.path.join(scan["run_dir"], "chapters")
        paths = {os.path.realpath(item["_path"]) for item in artifacts
                 if item.get("owned")}

        def mentions(value: Any) -> bool:
            if isinstance(value, dict):
                # Superseded takes are audit history, not inputs required to
                # recover this snapshot. Keep every other recovery edge.
                return any(mentions(item) for key, item in value.items()
                           if key != "supersedes")
            if isinstance(value, list):
                return any(mentions(item) for item in value)
            if not isinstance(value, str):
                return False
            if value.lower() == revision:
                return True
            relative = value.replace("\\", "/")
            if not (relative.startswith("h3_chains/") or os.path.isabs(relative)):
                return False
            return os.path.realpath(os.path.join(self.output_root, relative)) in paths

        references = []
        if not os.path.lexists(root):
            return references
        try:
            if os.path.islink(root):
                raise ValueError("chapter directory is a symlink")
            with os.scandir(root) as entries:
                chapters = sorted(entries, key=lambda entry: entry.name)
            for directory in chapters:
                if directory.is_symlink():
                    raise ValueError("chapter directory is a symlink: " + directory.name)
                if not directory.is_dir(follow_symlinks=False):
                    continue
                manifest_dir = os.path.join(directory.path, "manifests")
                if not os.path.lexists(manifest_dir):
                    continue
                if os.path.islink(manifest_dir):
                    raise ValueError("chapter manifests directory is a symlink")
                with os.scandir(manifest_dir) as entries:
                    snapshots = sorted(entries, key=lambda entry: entry.name)
                for entry in snapshots:
                    if not re.fullmatch(r"[0-9a-f]{32}\.json", entry.name):
                        continue
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        raise ValueError("chapter snapshot is not a regular file")
                    document = self._read_json(entry.path)
                    if (not isinstance(document, dict)
                            or document.get("format") != "h3_chain_chapter_manifest_v1"
                            or document.get("run_name") != scan["run_name"]
                            or not isinstance(document.get("segments"), list)
                            or not document["segments"]
                            or not isinstance(document.get("chapter"), dict)):
                        raise ValueError("invalid chapter snapshot: " + entry.name)
                    if mentions(document):
                        chapter = document["chapter"]
                        references.append({
                            "number": chapter.get("number"),
                            "title": str(chapter.get("title") or directory.name),
                            "snapshot": entry.name[:-5],
                            "path": os.path.relpath(entry.path, self.output_root),
                        })
        except (OSError, ValueError, TypeError, RecursionError) as error:
            references.append({"error": "Cannot verify sealed chapter recovery: " + str(error)})
        return references

    def deletion_preview(self, run_name: Any, scene: Any,
                         revision: Any) -> dict[str, Any]:
        run_dir, run = self._run_dir(run_name)
        scene_number = int(scene)
        token = str(revision or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Checkpoint revision must be a 32-character revision id.")
        with checkpoint_run_lock(self.output_root, run):
            if not os.path.isdir(run_dir):
                raise FileNotFoundError("H3 run %r does not exist." % run)
            scan = self._scan(run)
            key = (scene_number, token)
            record = scan["records"].get(key)
            if record is None:
                raise FileNotFoundError(
                    "Scene %d revision %s is no longer available." %
                    (scene_number, token[:8]))
            artifacts = self._artifacts(scan, record)
            chapter_start, chapter_end = self._chapter_bounds(
                scan, scene_number)
            descendant_keys = self._descendant_keys(scan["records"], key)
            dependents = []
            for child_key in descendant_keys:
                child = scan["records"][child_key]
                dependents.append({
                    "scene": child["scene"],
                    "scene_id": child["scene_id"],
                    "revision": child["revision"],
                    "active": child["active"],
                    "direct": key in child["_dependencies"],
                    "leaf": not child["_dependents"],
                    "continuation_mode": child["continuation_mode"],
                    "context_length": child["context_length"],
                    "audio_context_length": child["audio_context_length"],
                })
            dependents.sort(key=lambda item: (
                not item["leaf"], -int(item["scene"]), item["revision"]))
            chapter_references = self._chapter_references(scan, token, artifacts)
            blockers = [
                item.get("error") or (
                    "Sealed Chapter %s (%s), snapshot %s, requires this revision "
                    "or its recovery artifacts. Retire that snapshot explicitly "
                    "if its chapter recovery is no longer needed." %
                    (item["number"], item["title"], item["snapshot"][:8]))
                for item in chapter_references]
            try:
                editorial = self._read_json(os.path.join(
                    scan["run_dir"], "editorial.json"))
                active_revision = next((
                    str(record.get("revision") or "").lower()
                    for record in scan["records"].values()
                    if int(record.get("scene", 0)) == scene_number
                    and bool(record.get("active"))
                    and str(record.get("take_kind") or "") !=
                    "editorial_alternate"), "")
                selected_in_cut = any(
                    isinstance(item, dict)
                    and self._integer(item.get("scene")) == scene_number
                    and str(item.get("alternate_revision") or "").lower()
                    == token
                    and str(item.get("base_revision") or "").lower()
                    == active_revision
                    for item in editorial.get("replacements", []))
                selected_base = any(
                    isinstance(item, dict)
                    and self._integer(item.get("scene")) == scene_number
                    and str(item.get("base_revision") or "").lower() == token
                    and str(item.get("base_revision") or "").lower()
                    == active_revision
                    for item in editorial.get("replacements", []))
            except (OSError, TypeError, ValueError, json.JSONDecodeError,
                    AttributeError):
                selected_in_cut = False
                selected_base = False
            if selected_in_cut:
                blockers.append(
                    "This alternate is selected in the final cut. Restore the "
                    "original take in Plan Studio before deleting it.")
            if selected_base:
                blockers.append(
                    "This generation revision is the immutable base of the "
                    "selected final-cut alternate. Restore the original take "
                    "in Plan Studio before rolling it back.")
            later_active = sorted(
                item["scene"] for item in scan["records"].values()
                if (item["active"] and item["scene"] > scene_number and
                    item["scene"] <= chapter_end))
            if record.get("pointer_active") and not record["active"]:
                blockers.append(
                    "This saved take has a stale active pointer. Make the desired "
                    "chapter branch active before deleting it; its files are retained.")
            if dependents:
                blockers.append(
                    "%d later checkpoint revision%s depend%s on it." %
                    (len(dependents), "" if len(dependents) == 1 else "s",
                     "s" if len(dependents) == 1 else ""))
            if record["active"] and dependents:
                blockers.append(
                    "This active revision can only be rolled back after its "
                    "later revisions are deleted.")
            elif record["active"] and later_active:
                blockers.append(
                    "A later active scene pointer exists at scene %d." %
                    later_active[-1])
            rollback = bool(record["active"] and not blockers)
            if rollback:
                artifacts.append(self._active_pointer_artifact(scan, record))
            public_files = [{key: value for key, value in item.items()
                             if not key.startswith("_")} for item in artifacts]
            snapshot = _fingerprint({
                "run_name": run,
                "scene": scene_number,
                "revision": token,
                "active": record["active"],
                "rollback": rollback,
                "chapter_references": chapter_references,
                "dependents": [(item["scene"], item["revision"])
                               for item in dependents],
                "files": [(item["path"], item["exists"], item["size_bytes"],
                           item["shared"], artifact["_mtime_ns"])
                          for item, artifact in zip(public_files, artifacts)],
            })
            owned = [item for item in public_files if item["owned"]]
            return {
                "ok": True,
                "run_name": run,
                "scene": scene_number,
                "scene_id": record["scene_id"],
                "revision": token,
                "active": record["active"],
                "rollback": rollback,
                "rollback_to_scene": scene_number - 1 if rollback else None,
                "scope_start_scene": chapter_start,
                "scope_end_scene": chapter_end,
                "allowed": not blockers,
                "blockers": blockers,
                "dependents": dependents,
                "chapter_references": chapter_references,
                "files": public_files,
                "owned_file_count": sum(item["exists"] for item in owned),
                "reclaimed_bytes": sum(item["size_bytes"] for item in owned),
                "snapshot": snapshot,
                "not_deleted": [
                    "Run Plan and workflow archives",
                    "Archived references and source media",
                    "Prompt revision history",
                    "Final and partial assembled exports",
                ],
            }

    def delete(self, run_name: Any, scene: Any, revision: Any,
               expected_snapshot: Any = "") -> dict[str, Any]:
        run_dir, run = self._run_dir(run_name)
        with checkpoint_run_lock(self.output_root, run):
            preview = self.deletion_preview(run, scene, revision)
            expected = str(expected_snapshot or "")
            if not preview["allowed"]:
                raise CheckpointDeleteBlocked(" ".join(preview["blockers"]), preview)
            if not expected:
                raise CheckpointDeleteBlocked(
                    "Preview this checkpoint deletion before confirming it.",
                    preview)
            if expected != preview["snapshot"]:
                raise CheckpointDeleteBlocked(
                    "Checkpoint files or dependencies changed; preview the deletion again.",
                    preview)
            scan = self._scan(run)
            key = (int(scene), str(revision).strip().lower())
            record = scan["records"][key]
            managed = self._artifacts(scan, record)
            if preview["rollback"]:
                managed.append(self._active_pointer_artifact(scan, record))
            artifacts = [item for item in managed
                         if item["owned"] and item["exists"]]
            transaction = uuid.uuid4().hex
            staged = []
            try:
                for artifact in artifacts:
                    path = artifact["_path"]
                    temporary = "%s.delete.%s.tmp" % (path, transaction)
                    os.replace(path, temporary)
                    staged.append((path, temporary, artifact["size_bytes"]))
            except Exception:
                for original, temporary, _size in reversed(staged):
                    try:
                        os.replace(temporary, original)
                    except Exception:
                        pass
                raise
            failed = []
            for _original, temporary, _size in staged:
                try:
                    os.unlink(temporary)
                except OSError:
                    failed.append(temporary)
            reclaimed = sum(size for _original, temporary, size in staged
                            if temporary not in failed)
            return {
                "ok": True,
                "run_name": run,
                "scene": int(scene),
                "revision": str(revision).strip().lower(),
                "rollback": bool(preview["rollback"]),
                "rollback_to_scene": preview["rollback_to_scene"],
                "deleted_files": len(staged) - len(failed),
                "reclaimed_bytes": reclaimed,
                "message": (
                    ("Rolled the active chain back through scene %d and " %
                     (int(scene) - 1) if int(scene) > 1 else
                     "Rolled the active chain back to no saved scenes and ") +
                    "deleted scene %d revision %s." %
                    (int(scene), str(revision)[:8])
                    if preview["rollback"] else
                    "Deleted scene %d revision %s." %
                    (int(scene), str(revision)[:8])
                ),
            }
