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
MAX_COMMANDS = 1024
_LOCK = threading.RLock()


def _safe_component(value: Any, label: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    text = text.strip("._-")[:96]
    if not text:
        raise ValueError("A non-empty %s is required." % label)
    return text


def _strict_run_name(value: Any) -> str:
    requested = str(value or "").strip()
    normalized = _safe_component(requested, "H3 chain run_name")
    if requested != normalized:
        raise ValueError("Prompt history requires the exact saved run name.")
    return normalized


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


def _branch(run):
    if __package__:
        from .branch_scope import current_branch
    else:
        from branch_scope import current_branch
    return current_branch(run)


def _history_revision(index):
    value = {key: index.get(key) for key in
             ("format", "run_name", "scene_id", "active_revision", "revisions")}
    value["working_branch_id"] = _branch(index["run_name"])
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class PromptHistoryConflict(ValueError):
    pass


class PromptHistoryStore:
    def __init__(self, output_root: str):
        self.output_root = os.path.abspath(output_root)
        self._pending_command = None
        self._staged_revisions = {}

    def _scene_dir(self, run_name: Any, scene_id: Any) -> tuple[str, str, str]:
        run = _strict_run_name(run_name)
        scene = _safe_component(scene_id, "scene ID")
        if __package__:
            from .branch_scope import working_directory
        else:
            from branch_scope import working_directory
        path = os.path.abspath(os.path.join(working_directory(os.path.join(
            self.output_root, "h3_chains", run), run), "prompt_history", scene))
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
        self._recover_command(directory, run, scene)
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
        if path in self._staged_revisions:
            return dict(self._staged_revisions[path])
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
            "command_version": 1,
            "working_branch_id": _branch(index["run_name"]),
            "history_revision": _history_revision(index),
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
            value = self._read_revision(directory, revision)
            return {**value, "run_name": run, "scene_id": scene,
                    "working_branch_id": _branch(run), "command_version": 1,
                    "history_revision": _history_revision(index)}

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

    def _recover_command(self, directory, run, scene):
        path = os.path.join(directory, ".command-journal.json")
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as handle:
            journal = json.load(handle)
        if not isinstance(journal, dict):
            raise ValueError("The pending prompt-history journal is invalid.")
        index, revisions = journal.get("index"), journal.get("revisions")
        if (not isinstance(index, dict) or index.get("format") != FORMAT
                or index.get("run_name") != run or index.get("scene_id") != scene
                or not isinstance(index.get("revisions"), list) or not isinstance(revisions, dict)):
            raise ValueError("The pending prompt-history journal is invalid.")
        targets = []
        for revision_id, value in revisions.items():
            target = self._revision_path(directory, revision_id)
            if not isinstance(value, dict) or value.get("id") != revision_id:
                raise ValueError("The pending prompt-history revision is invalid.")
            targets.append((target, value))
        for target, value in targets:
            _atomic_json(target, value)
        _atomic_json(os.path.join(directory, "index.json"), index)
        os.unlink(path)

    def _write_revision(self, path, value):
        if self._pending_command:
            self._staged_revisions[path] = dict(value)
        else:
            _atomic_json(path, value)

    def _write_index(self, directory: str, index: dict[str, Any]) -> None:
        pending = self._pending_command
        if pending:
            # Commit the receipt with the index change, not in a later write.
            # A lost acknowledgement can never create a second fork on retry.
            revision_id = (index.get("active_revision") if pending["action"] in
                           ("save", "fork", "activate") else pending["revision"])
            meta = self._meta(index, revision_id)
            receipt = {**pending, "result_revision": revision_id,
                       "result_prompt_sha256": meta.get("prompt_sha256") if meta else None,
                       "after_revision": _history_revision(index), "committed_at": _timestamp()}
            index.setdefault("command_receipts", {})[pending["operation_id"]] = receipt
        if pending:
            # The journal is the durable intent. All native readers/writers
            # recover it under _LOCK before observing an index or revision.
            journal = {"index": index, "revisions": {
                os.path.basename(path)[:-5]: value
                for path, value in self._staged_revisions.items()}}
            _atomic_json(os.path.join(directory, ".command-journal.json"), journal)
            self._recover_command(directory, index["run_name"], index["scene_id"])
            self._staged_revisions = {}
        else:
            _atomic_json(os.path.join(directory, "index.json"), index)

    def command_status(self, run_name, scene_id, operation_id):
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            receipt = index.get("command_receipts", {}).get(str(operation_id))
            if receipt and (receipt.get("run_name"), receipt.get("scene_id"), receipt.get("working_branch_id")) != (run, scene, _branch(run)):
                receipt = None
            return {"history": self._public_index(index), "receipt": receipt}

    def command(self, run_name, scene_id, command):
        """Conditional, idempotent authoring. Existing native writers share _LOCK."""
        if not isinstance(command, dict) or command.get("command_version") != 1:
            raise ValueError("Unsupported prompt-history command version.")
        action = command.get("action")
        if action not in ("save", "fork", "activate", "label", "archive", "delete"):
            raise ValueError("Unknown prompt-history command.")
        operation = str(command.get("operation_id") or "")
        if re.fullmatch(r"[0-9a-f]{32}", operation) is None:
            raise ValueError("A 32-character operation ID is required.")
        expected = command.get("base_revision")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Review the current prompt history before changing it.")
        request = {key: command.get(key) for key in
                   ("action", "base_revision", "revision", "parent_revision", "prompt", "label", "archived")}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            receipt = index.get("command_receipts", {}).get(operation)
            if receipt:
                if (receipt.get("run_name"), receipt.get("scene_id"), receipt.get("working_branch_id")) != (run, scene, _branch(run)):
                    raise ValueError("This operation belongs to a different project, scene or branch.")
                if receipt["request_sha256"] != fingerprint:
                    raise ValueError("This operation ID was already used for a different command.")
                if action == "delete":
                    # The index/receipt commit precedes file cleanup. Finish a
                    # cleanup interrupted by a crash without repeating the edit.
                    try:
                        os.unlink(self._revision_path(directory, str(command.get("revision"))))
                    except FileNotFoundError:
                        pass
                return {"history": self._public_index(index), "receipt": receipt, "replayed": True}
            if _history_revision(index) != expected:
                raise PromptHistoryConflict("Prompt history changed. Reload and review it before retrying.")
            if len(index.get("command_receipts", {})) >= MAX_COMMANDS:
                raise ValueError("This scene reached its retained history-command limit; no receipts were discarded.")
            self._pending_command = {"operation_id": operation, "action": action,
                "run_name": run, "scene_id": scene, "working_branch_id": _branch(run),
                "revision": command.get("revision"), "request_sha256": fingerprint,
                "before_revision": expected}
            try:
                if action == "save":
                    self.save_draft(run, scene, command.get("prompt", ""), command.get("parent_revision"))
                elif action == "fork":
                    parent = str(command.get("revision") or "")
                    if self._meta(index, parent) is None:
                        raise ValueError("The parent prompt revision no longer exists.")
                    self._create(directory, index, _normalized_prompt(command.get("prompt", "")), parent)
                elif action == "activate":
                    self.activate(run, scene, command.get("revision"))
                elif action == "label":
                    self.set_label(run, scene, command.get("revision"), command.get("label", ""))
                elif action == "archive":
                    if not isinstance(command.get("archived"), bool):
                        raise ValueError("Archived must be a boolean.")
                    self.set_archived(run, scene, command.get("revision"), command["archived"])
                else:
                    self.delete_draft(run, scene, command.get("revision"))
            finally:
                self._pending_command = None
                self._staged_revisions = {}
            result = self.command_status(run, scene, operation)
            result["replayed"] = False
            return result

    def _create(self, directory: str, index: dict[str, Any], prompt: str,
                parent_id: str | None) -> dict[str, Any]:
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
        }
        revision = {"format": FORMAT, **meta, "prompt": prompt}
        self._write_revision(self._revision_path(directory, meta["id"]), revision)
        index["revisions"].append(meta)
        index["active_revision"] = meta["id"]
        self._write_index(directory, index)
        return revision

    def save_draft(self, run_name: Any, scene_id: Any, prompt: Any,
                   parent_revision: Any = None) -> dict[str, Any]:
        prompt = _normalized_prompt(prompt)
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            exact = self._find_prompt(directory, index, prompt)
            if exact is not None:
                if exact.get("archived_at"):
                    exact["archived_at"] = None
                    revision = self._read_revision(directory, exact["id"])
                    revision["archived_at"] = None
                    self._write_revision(
                        self._revision_path(directory, exact["id"]), revision)
                index["active_revision"] = exact["id"]
                self._write_index(directory, index)
                return {
                    "history": self._public_index(index),
                    "revision": self._read_revision(directory, exact["id"]),
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
                self._write_revision(self._revision_path(directory, parent), revision)
                index["active_revision"] = parent
                self._write_index(directory, index)
            else:
                revision = self._create(
                    directory, index, prompt,
                    parent if parent_meta is not None else None)
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
                self._write_revision(self._revision_path(directory, revision), value)
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
            self._write_revision(
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
            self._write_revision(
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
                      prompt: Any) -> dict[str, Any]:
        prompt = _normalized_prompt(prompt)
        with _LOCK:
            directory, run, scene = self._scene_dir(run_name, scene_id)
            index = self._load_index(directory, run, scene)
            meta = self._find_prompt(directory, index, prompt)
            if meta is None:
                parent = str(index.get("active_revision") or "")
                revision = self._create(
                    directory, index, prompt,
                    parent if self._meta(index, parent) is not None else None)
                meta = self._meta(index, revision["id"])
            else:
                revision = self._read_revision(directory, meta["id"])

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
            self._write_revision(self._revision_path(directory, meta["id"]), revision)
            index["active_revision"] = meta["id"]
            self._write_index(directory, index)
            return {"history": self._public_index(index), "revision": revision}
