"""Small, lazy file store for Scene Prompt Editor revisions.

The active prompt deliberately remains in the human-readable Plan JSON.  This
store keeps immutable executed revisions and one mutable draft per branch in
the run output folder without making the workflow document grow over time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any


FORMAT = "h3_scene_prompt_history_v1"
MAX_PROMPT_LENGTH = 200_000
MAX_LABEL_LENGTH = 80
_LOCK = threading.RLock()


def _safe_component(value: Any, label: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")[:96]
    if not text:
        raise ValueError("A non-empty %s is required." % label)
    return text


def _normalized_prompt(value: Any) -> str:
    # Match Plan execution normalization so harmless leading/trailing editor
    # whitespace does not create a second "executed" variant.
    prompt = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise ValueError("The scene prompt is too large.")
    return prompt


def _normalized_label(value: Any) -> str:
    label = " ".join(str(value or "").split())
    if len(label) > MAX_LABEL_LENGTH:
        raise ValueError(
            "The prompt revision label must be %d characters or fewer."
            % MAX_LABEL_LENGTH)
    return label


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _atomic_json(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = "%s.%s.tmp" % (path, uuid.uuid4().hex)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class PromptHistoryStore:
    def __init__(self, output_root: str):
        self.output_root = os.path.abspath(output_root)

    def _scene_dir(self, run_name: Any, scene_id: Any) -> tuple[str, str, str]:
        run = _safe_component(run_name, "H3 chain run_name")
        scene = _safe_component(scene_id, "scene ID")
        path = os.path.abspath(os.path.join(
            self.output_root, "h3_chains", run, "prompt_history", scene))
        if os.path.commonpath([self.output_root, path]) != self.output_root:
            raise ValueError("Prompt history path escapes the output directory.")
        return path, run, scene

    @staticmethod
    def _empty_index(run: str, scene: str) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "run_name": run,
            "scene_id": scene,
            "active_revision": None,
            "revisions": [],
        }

    def _load_index(self, directory: str, run: str, scene: str) -> dict[str, Any]:
        path = os.path.join(directory, "index.json")
        if not os.path.isfile(path):
            return self._empty_index(run, scene)
        with open(path, "r", encoding="utf-8") as handle:
            index = json.load(handle)
        if (not isinstance(index, dict) or index.get("format") != FORMAT
                or not isinstance(index.get("revisions"), list)):
            raise ValueError("The prompt-history index is invalid.")
        index["run_name"] = run
        index["scene_id"] = scene
        for revision in index["revisions"]:
            if not isinstance(revision, dict):
                raise ValueError("The prompt-history index is invalid.")
            revision.setdefault("label", "")
            revision.setdefault("archived_at", None)
            revision.setdefault("basic_prompt", "")
        return index

    @staticmethod
    def _meta(index: dict[str, Any], revision_id: Any) -> dict[str, Any] | None:
        revision = str(revision_id or "")
        return next((item for item in index["revisions"]
                     if item.get("id") == revision), None)

    @staticmethod
    def _revision_path(directory: str, revision_id: str) -> str:
        if re.fullmatch(r"[0-9a-f]{32}", revision_id) is None:
            raise ValueError("Unknown prompt revision.")
        return os.path.join(directory, "%s.json" % revision_id)

    def _read_revision(self, directory: str, revision_id: str) -> dict[str, Any]:
        path = self._revision_path(directory, revision_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError("Prompt revision content is missing.") from exc
        if not isinstance(value, dict) or value.get("id") != revision_id:
            raise ValueError("Prompt revision content is invalid.")
        return value

    @staticmethod
    def _public_index(index: dict[str, Any]) -> dict[str, Any]:
        revisions = [dict(item) for item in index["revisions"]]
        active_revision = index.get("active_revision")
        active = next((item for item in revisions
                       if item.get("id") == active_revision), None)
        executed = [item for item in revisions if item.get("executed_at")]
        latest_executed = max(
            executed,
            key=lambda item: str(item.get("last_executed_at")
                                 or item.get("executed_at") or ""),
            default=None)
        return {
            "format": FORMAT,
            "run_name": index["run_name"],
            "scene_id": index["scene_id"],
            "active_revision": active_revision,
            "active_revision_state": (
                "executed" if active and active.get("executed_at") else
                "draft" if active else "empty"),
            "latest_executed_revision": (
                latest_executed.get("id") if latest_executed else None),
            "archived_revision_count": sum(
                1 for item in revisions if item.get("archived_at")),
            "revisions": revisions,
        }

    def list(self, run_name: Any, scene_id: Any) -> dict[str, Any]:
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            return self._public_index(self._load_index(directory, run, scene))

    def get(self, run_name: Any, scene_id: Any,
            revision_id: Any) -> dict[str, Any]:
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            revision = str(revision_id or "")
            if self._meta(index, revision) is None:
                raise ValueError("Unknown prompt revision.")
            return self._read_revision(directory, revision)

    def _find_prompt(self, directory: str, index: dict[str, Any],
                     prompt: str) -> dict[str, Any] | None:
        digest = _prompt_hash(prompt)
        for meta in reversed(index["revisions"]):
            if meta.get("prompt_sha256") != digest:
                continue
            try:
                revision = self._read_revision(directory, meta["id"])
            except ValueError:
                continue
            if revision.get("prompt") == prompt:
                return meta
        return None

    def _write_index(self, directory: str, index: dict[str, Any]) -> None:
        _atomic_json(os.path.join(directory, "index.json"), index)

    def _create(self, directory: str, index: dict[str, Any], prompt: str,
                parent_id: str | None,
                basic_prompt: str = "") -> dict[str, Any]:
        now = _timestamp()
        meta = {
            "id": uuid.uuid4().hex,
            "parent_id": parent_id,
            "label": "",
            "archived_at": None,
            "created_at": now,
            "updated_at": now,
            "executed_at": None,
            "last_executed_at": None,
            "execution_count": 0,
            "prompt_sha256": _prompt_hash(prompt),
            "basic_prompt": basic_prompt,
        }
        revision = {"format": FORMAT, **meta, "prompt": prompt}
        _atomic_json(self._revision_path(directory, meta["id"]), revision)
        index["revisions"].append(meta)
        index["active_revision"] = meta["id"]
        self._write_index(directory, index)
        return revision

    def save_draft(self, run_name: Any, scene_id: Any, prompt: Any,
                   parent_revision: Any = None,
                   basic_prompt: Any = None) -> dict[str, Any]:
        prompt = _normalized_prompt(prompt)
        basic_prompt_given = basic_prompt is not None
        if basic_prompt_given:
            basic_prompt = _normalized_prompt(basic_prompt)
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            exact = self._find_prompt(directory, index, prompt)
            if exact is not None:
                revision = self._read_revision(directory, exact["id"])
                if exact.get("archived_at"):
                    exact["archived_at"] = None
                    revision["archived_at"] = None
                if basic_prompt_given:
                    exact["basic_prompt"] = basic_prompt
                    revision["basic_prompt"] = basic_prompt
                _atomic_json(
                    self._revision_path(directory, exact["id"]), revision)
                index["active_revision"] = exact["id"]
                self._write_index(directory, index)
                return {
                    "history": self._public_index(index),
                    "revision": revision,
                }

            parent = str(parent_revision or index.get("active_revision") or "")
            parent_meta = self._meta(index, parent)
            if parent and parent_meta is None:
                raise ValueError("The parent prompt revision no longer exists.")

            # A draft stays mutable until it is executed. Editing an executed
            # revision creates an immutable-parent child, which is the branch.
            if parent_meta is not None and parent_meta.get("executed_at") is None:
                now = _timestamp()
                parent_meta["updated_at"] = now
                parent_meta["prompt_sha256"] = _prompt_hash(prompt)
                revision = self._read_revision(directory, parent)
                revision.update({
                    "updated_at": now,
                    "prompt_sha256": parent_meta["prompt_sha256"],
                    "prompt": prompt,
                })
                if basic_prompt_given:
                    parent_meta["basic_prompt"] = basic_prompt
                    revision["basic_prompt"] = basic_prompt
                _atomic_json(self._revision_path(directory, parent), revision)
                index["active_revision"] = parent
                self._write_index(directory, index)
            else:
                revision = self._create(
                    directory, index, prompt,
                    parent if parent_meta is not None else None,
                    basic_prompt if basic_prompt_given else "")
            return {
                "history": self._public_index(index),
                "revision": revision,
            }

    def activate(self, run_name: Any, scene_id: Any,
                 revision_id: Any) -> dict[str, Any]:
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            revision = str(revision_id or "")
            meta = self._meta(index, revision)
            if meta is None:
                raise ValueError("Unknown prompt revision.")
            value = self._read_revision(directory, revision)
            if meta.get("archived_at"):
                meta["archived_at"] = None
                value["archived_at"] = None
                _atomic_json(self._revision_path(directory, revision), value)
            index["active_revision"] = revision
            self._write_index(directory, index)
            return {"history": self._public_index(index), "revision": value}

    def set_label(self, run_name: Any, scene_id: Any, revision_id: Any,
                  label: Any) -> dict[str, Any]:
        label = _normalized_label(label)
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            revision_id = str(revision_id or "")
            meta = self._meta(index, revision_id)
            if meta is None:
                raise ValueError("Unknown prompt revision.")
            revision = self._read_revision(directory, revision_id)
            meta["label"] = label
            revision["label"] = label
            _atomic_json(
                self._revision_path(directory, revision_id), revision)
            self._write_index(directory, index)
            return {
                "history": self._public_index(index),
                "revision": revision,
            }

    def set_archived(self, run_name: Any, scene_id: Any, revision_id: Any,
                     archived: Any = True) -> dict[str, Any]:
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            revision_id = str(revision_id or "")
            meta = self._meta(index, revision_id)
            if meta is None:
                raise ValueError("Unknown prompt revision.")
            should_archive = bool(archived)
            if should_archive and index.get("active_revision") == revision_id:
                raise ValueError(
                    "The active prompt revision cannot be archived. "
                    "Activate another revision first.")
            revision = self._read_revision(directory, revision_id)
            archived_at = _timestamp() if should_archive else None
            meta["archived_at"] = archived_at
            revision["archived_at"] = archived_at
            _atomic_json(
                self._revision_path(directory, revision_id), revision)
            self._write_index(directory, index)
            return {
                "history": self._public_index(index),
                "revision": revision,
            }

    def delete_draft(self, run_name: Any, scene_id: Any,
                     revision_id: Any) -> dict[str, Any]:
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            revision_id = str(revision_id or "")
            meta = self._meta(index, revision_id)
            if meta is None:
                raise ValueError("Unknown prompt revision.")
            if index.get("active_revision") == revision_id:
                raise ValueError(
                    "The active prompt revision cannot be deleted. "
                    "Activate another revision first.")
            if meta.get("executed_at"):
                raise ValueError(
                    "Executed prompt history is protected. Archive it instead.")
            if any(item.get("parent_id") == revision_id
                   for item in index["revisions"]):
                raise ValueError(
                    "A prompt revision with descendants cannot be deleted. "
                    "Archive it instead.")
            index["revisions"] = [
                item for item in index["revisions"]
                if item.get("id") != revision_id
            ]
            self._write_index(directory, index)
            try:
                os.unlink(self._revision_path(directory, revision_id))
            except FileNotFoundError:
                pass
            return {"history": self._public_index(index)}

    def mark_executed(self, run_name: Any, scene_id: Any,
                      prompt: Any, basic_prompt: Any = None) -> dict[str, Any]:
        prompt = _normalized_prompt(prompt)
        basic_prompt_given = basic_prompt is not None
        if basic_prompt_given:
            basic_prompt = _normalized_prompt(basic_prompt)
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            meta = self._find_prompt(directory, index, prompt)
            if meta is None:
                parent = str(index.get("active_revision") or "")
                revision = self._create(
                    directory, index, prompt,
                    parent if self._meta(index, parent) is not None else None,
                    basic_prompt if basic_prompt_given else "")
                meta = self._meta(index, revision["id"])
            else:
                revision = self._read_revision(directory, meta["id"])
                if basic_prompt_given:
                    meta["basic_prompt"] = basic_prompt
                    revision["basic_prompt"] = basic_prompt

            if meta.get("archived_at"):
                meta["archived_at"] = None
                revision["archived_at"] = None

            now = _timestamp()
            if meta.get("executed_at") is None:
                meta["executed_at"] = now
                revision["executed_at"] = now
            meta["last_executed_at"] = now
            meta["execution_count"] = int(meta.get("execution_count") or 0) + 1
            meta["updated_at"] = now
            revision.update({
                "last_executed_at": now,
                "execution_count": meta["execution_count"],
                "updated_at": now,
            })
            _atomic_json(self._revision_path(directory, meta["id"]), revision)
            index["active_revision"] = meta["id"]
            self._write_index(directory, index)
            return {"history": self._public_index(index), "revision": revision}
