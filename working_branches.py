"""Named working branches over the existing immutable checkpoint inventory."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

if __package__:
    from .branch_scope import branch_id, working_directory, branch_scope
    from .checkpoint_manager import checkpoint_run_lock, CheckpointGraphManager
    from .branch_authoring_recovery import recover_authoring
    from .processing_persistence import atomic_json, sync_directory
else:
    from branch_scope import branch_id, working_directory, branch_scope
    from checkpoint_manager import checkpoint_run_lock, CheckpointGraphManager
    from branch_authoring_recovery import recover_authoring
    from processing_persistence import atomic_json, sync_directory


class WorkingBranches:
    def __init__(self, output_root, run):
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?", str(run)):
            raise ValueError("Invalid H3 project name.")
        self.output = Path(output_root).resolve()
        self.run = str(run)
        self.root = (self.output / "h3_chains" / run).resolve()
        self.folder = self.root / "branches"
        if not self.root.is_relative_to(self.output):
            raise ValueError("H3 project escapes the output directory.")

    def _path(self, selected):
        selected = branch_id(selected)
        path = self.folder / ("main.json" if selected == "main" else selected + "/branch.json")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("H3 branch metadata escapes the project.")
        return path

    @staticmethod
    def _read(path):
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _write(path, value):
        atomic_json(path, value)

    @staticmethod
    def _operation(value):
        if value and not re.fullmatch(r"[0-9a-f]{32}", str(value)):
            raise ValueError("Invalid branch operation id.")
        return str(value or "")

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()

    def _load_record(self, selected="main"):
        selected = branch_id(selected)
        path = self._path(selected)
        if selected == "main" and not path.exists():
            return {"format": "h3_working_branch_v1", "run_name": self.run,
                    "id": "main", "name": "Original", "revision": "", "authoring": None}
        record = self._read(path)
        if (not isinstance(record, dict) or record.get("format") != "h3_working_branch_v1"
                or record.get("run_name") != self.run or record.get("id") != selected):
            raise ValueError("Invalid saved H3 working branch.")
        return record

    def _pointers(self, selected):
        directory = Path(working_directory(str(self.root), self.run, selected)) / "checkpoints"
        if list((directory / ".transactions").glob("restore.*.json")):
            raise ValueError("Checkpoint assignment recovery is pending. Refresh Checkpoint Manager first.")
        result = {}
        for path in sorted(directory.glob("clip_????.json")):
            if not re.fullmatch(r"clip_[0-9]{4}\.json", path.name):
                continue
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Branch checkpoint metadata escapes the project.")
            result[int(path.stem[5:])] = self._read(path)
        return result

    @staticmethod
    def _assignments(pointers):
        return {str(scene): item["_authoring_assignment"] for scene, item in pointers.items()
                if item.get("_authoring_assignment")}

    def load(self, selected="main"):
        """Read-only recovery of legacy/stale assignment snapshots.

        A derived revision invalidates old browser bindings, so an old tab
        cannot save stale settings over a newly assigned path. Normal scene
        generation has no assignment marker and never clobbers authored edits.
        """
        selected = branch_id(selected)
        with checkpoint_run_lock(str(self.output), self.run), branch_scope(self.run, selected):
            record = self._load_record(selected)
            if not record.get("authoring"):
                return record
            pointers = self._pointers(selected)
            seen = record.get("authoring_assignments", {})
            legacy = record.get("authoring_version", 1) < 2
            changed = {scene: item for scene, item in pointers.items() if legacy or (
                item.get("_authoring_assignment") and
                item["_authoring_assignment"] != seen.get(str(scene)))}
            if not changed:
                return record
            active, _stale = CheckpointGraphManager(str(self.output)).active_selection(self.run)
            changed = {scene: item for scene, item in changed.items()
                       if active.get(scene) == item.get("segment", {}).get("revision")}
            if not changed:
                return record
            raw_revision = record["revision"]
            record["authoring"] = recover_authoring(record["authoring"], changed)
            record["revision"] = self._digest([raw_revision, changed])
            # An old save receipt cannot acknowledge settings invalidated by a
            # later assignment. Its retry must take the same stale-write path.
            record.pop("last_save_operation", None)
            record["authoring_recovery"] = {"snapshot_revision": raw_revision,
                "scenes": sorted(changed),
                "message": "Recovered assigned checkpoint prompts, exact seeds and scene settings. "
                           "The previous branch snapshot is retained until save, then backed up."}
            return record

    def listing(self):
        records = [self._load_record()]
        if self.folder.is_dir():
            for path in sorted(self.folder.iterdir()):
                if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name):
                    records.append(self._load_record(path.name))
        default_path = self.folder / "default.json"
        if not default_path.resolve().is_relative_to(self.root):
            raise ValueError("H3 branch metadata escapes the project.")
        records[1:] = sorted(records[1:], key=lambda item: (item.get("created_at", ""), item["id"]))
        default = self._read(default_path).get("branch_id") if default_path.exists() else "main"
        self._load_record(default)
        return {"run_name": self.run, "default_branch": default,
                "branches": [{key: value for key, value in item.items() if key != "authoring"}
                             for item in records]}

    @staticmethod
    def authoring(value):
        if not isinstance(value, dict) or not isinstance(value.get("plan_json"), str):
            raise ValueError("Working branch requires the complete authored Plan JSON.")
        plan = json.loads(value["plan_json"])
        if not isinstance(plan, dict) or not isinstance(plan.get("shots"), list) or not plan["shots"]:
            raise ValueError("Working branch Plan must contain scene prompts.")
        # No workflow-wide node graph or tensors: only the Studio's editable inputs.
        if len(json.dumps(value)) > 16 * 1024 * 1024:
            raise ValueError("Working branch authoring snapshot is too large.")
        return copy.deepcopy(value)

    def save(self, selected, authoring, expected_revision, operation_id=""):
        authoring = self.authoring(authoring)
        selected = branch_id(selected)
        plan = json.loads(authoring["plan_json"])
        if selected == "main":
            plan.pop("_branch_id", None)
        else:
            plan["_branch_id"] = selected
        authoring["plan_json"] = json.dumps(plan, ensure_ascii=False, indent=2)
        operation_id = self._operation(operation_id)
        request_hash = self._digest([authoring, str(expected_revision or "")])
        with checkpoint_run_lock(str(self.output), self.run):
            record = self.load(selected)
            receipt = record.get("last_save_operation", {})
            if operation_id and receipt.get("id") == operation_id:
                if receipt.get("hash") != request_hash:
                    raise ValueError("Branch operation id was reused with different settings.")
                sync_directory(self._path(selected).parent)
                return record
            if record.get("revision", "") != str(expected_revision or ""):
                raise ValueError("This branch was edited in another workflow or its assigned checkpoint "
                                 "settings changed. Reload saved branch before saving; local edits are kept in browser recovery.")
            recovery = record.pop("authoring_recovery", None)
            if recovery:
                # Keep the exact pre-recovery record, not a reconstructed copy.
                raw = self._load_record(selected)
                backup = self.folder / "authoring_backups" / selected / (self._digest(raw) + ".json")
                if not backup.resolve().is_relative_to(self.root):
                    raise ValueError("Branch authoring backup escapes the project.")
                if not backup.exists():
                    self._write(backup, raw)
                record["authoring_backup"] = str(backup.relative_to(self.root))
            record["authoring_version"] = 2
            record["authoring_assignments"] = self._assignments(self._pointers(selected))
            record.update(authoring=authoring, revision=uuid.uuid4().hex)
            if operation_id:
                record["last_save_operation"] = {"id": operation_id, "hash": request_hash}
            else:
                record.pop("last_save_operation", None)
            self._write(self._path(selected), record)
            return record

    def retry_create(self, source, name, authoring, through_scene=0, operation_id=""):
        """Resolve an uncertain create before inspecting today's mutable prefix."""
        operation_id = self._operation(operation_id)
        if not operation_id or not self._path(operation_id).exists():
            return None
        record = self.load(operation_id)
        digest = self._digest([source, str(name or "").strip(), authoring, through_scene])
        if record.get("create_operation_hash") != digest:
            raise ValueError("Branch operation id was reused with different settings.")
        sync_directory(self.folder)
        return record

    def create(self, source, name, authoring, through_scene=0, operation_id=""):
        authoring = self.authoring(authoring)
        name = str(name or "").strip()
        plan = json.loads(authoring["plan_json"])
        shots = plan["shots"]
        if not name or len(name) > 120:
            raise ValueError("Choose a branch name of 1–120 characters.")
        if type(through_scene) is not int or not 0 <= through_scene <= 10000:
            raise ValueError("Fork scene must be a nonnegative integer.")
        operation_id = self._operation(operation_id)
        request_hash = self._digest([source, name, authoring, through_scene])
        with checkpoint_run_lock(str(self.output), self.run):
            recovered = self.retry_create(source, name, authoring, through_scene, operation_id)
            if recovered is not None:
                return recovered
            self.load(source)
            source_dir = Path(working_directory(str(self.root), self.run, source))
            pointers = []
            for scene in range(1, through_scene + 1):
                path = source_dir / "checkpoints" / ("clip_%04d.json" % scene)
                if not path.is_file():
                    raise ValueError("Cannot fork through scene %d: scene %d is not saved on this branch."
                                     % (through_scene, scene))
                metadata = self._read(path)
                segment = metadata.get("segment") or {}
                if segment.get("index") != scene or not re.fullmatch(r"[0-9a-f]{32}", str(segment.get("revision", ""))):
                    raise ValueError("Fork contains invalid checkpoint identity.")
                if scene > len(shots) or shots[scene - 1].get("id") != segment.get("id"):
                    raise ValueError("Fork scene order differs from the saved clips. Restore the matching Plan or create an empty branch.")
                pointers.append((path.name, metadata))
            self.folder.mkdir(parents=True, exist_ok=True)
            selected = operation_id or uuid.uuid4().hex
            plan["_branch_id"] = selected
            authoring["plan_json"] = json.dumps(plan, ensure_ascii=False, indent=2)
            record = {"format": "h3_working_branch_v1", "run_name": self.run,
                      "id": selected, "name": name, "revision": uuid.uuid4().hex,
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "source_branch": source, "fork_scene": through_scene,
                      "create_operation_hash": request_hash,
                      "authoring_version": 2,
                      "authoring_assignments": self._assignments({
                          int(filename[5:9]): metadata for filename, metadata in pointers}),
                      "authoring": authoring}
            stage = Path(tempfile.mkdtemp(prefix=".branch-", dir=self.folder))
            try:
                self._write(stage / "branch.json", record)
                for filename, metadata in pointers:
                    self._write(stage / "checkpoints" / filename, metadata)
                # Legacy takes can refer to a mutable Plan mirror rather than
                # an immutable recovery snapshot. Keep a small branch-local
                # mirror so assigning/reading them never falls back to Original.
                plan_path = source_dir / "plan.json"
                if plan_path.is_file():
                    archived = self._read(plan_path)
                    archived["_branch_id"] = selected
                    self._write(stage / "plan.json", archived)
                editorial_path = source_dir / "editorial.json"
                if editorial_path.is_file():
                    editorial = self._read(editorial_path)
                    editorial["revision"] = uuid.uuid4().hex
                    editorial["alternate_draft"] = None
                    retained_ids = {str(shot.get("id", "")) for shot in plan["shots"][:through_scene]}
                    for key in ("replacements", "trims"):
                        editorial[key] = [item for item in editorial.get(key, [])
                                          if item.get("scene_id") in retained_ids]
                    editorial["locked_scene_ids"] = [item for item in editorial.get("locked_scene_ids", [])
                                                      if item in retained_ids]
                    self._write(stage / "editorial.json", editorial)
                os.replace(stage, self.folder / selected)
                sync_directory(self.folder)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)  # Only this unpublished temporary directory.
            return record

    def make_default(self, selected):
        with checkpoint_run_lock(str(self.output), self.run):
            self.load(selected)
            if not (self.folder / "default.json").resolve().is_relative_to(self.root):
                raise ValueError("H3 branch metadata escapes the project.")
            self._write(self.folder / "default.json", {"branch_id": selected})
        return self.listing()
