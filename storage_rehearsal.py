"""Checksum-verified relocation/rollback on an independently receipted COPY only.

Not exposed by nodes/routes; deliberately not a production migration command.
Legacy control records remain in place. Publication journals live outside the
copy, and unknown recovery collisions stop rather than overwrite either file.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

if __package__:
    from .storage_resolver import FORMAT, ALIASES, confined, validate_aliases
    from .storage_layout import OrganizedStorageLayout
    from .processing_persistence import atomic_json, sync_directory
    from .checkpoint_manager import _raw_checkpoint_run_lock
else:
    from storage_resolver import FORMAT, ALIASES, confined, validate_aliases
    from storage_layout import OrganizedStorageLayout
    from processing_persistence import atomic_json, sync_directory
    from checkpoint_manager import _raw_checkpoint_run_lock


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _copy_root(receipt_path):
    receipt_path = Path(receipt_path).absolute()
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('format') == 'h3_offline_migration_source_v1':
        # Public migration reads the frozen source directly and copies every
        # accepted byte into a different output. No redundant full source copy
        # and no false "independent_copies" claim are needed. The coordinator
        # verifies the frozen namespace/hashes before and after publication.
        root = Path(receipt['source']).absolute()
        if (root.parent.name != 'h3_chains' or not root.is_dir()
                or receipt_path.parent.is_relative_to(root.parent.parent)
                or root.is_relative_to(receipt_path.parent)
                or receipt.get('source_manifest_sha256') != sha256(receipt_path.parent/'migration.json')):
            raise ValueError('Invalid offline migration source or changed inventory.')
        confined(root, 'storage.json')
        return root, receipt
    root = Path(receipt["copy"]).absolute()
    if (receipt.get("independent_copies") is not True
            or root == Path(receipt["source"]).absolute()
            or not root.is_relative_to(receipt_path.parent)
            or root.parent.name != "h3_chains" or not root.is_dir()):
        raise ValueError("Rehearsal requires a receipted independent copy beneath the receipt directory.")
    confined(root, "storage.json")
    return root, receipt


def prepare(receipt_path, proposal_path, journal_directory, *, windows_root=None,
            organized_writers=False):
    root, receipt = _copy_root(receipt_path)
    folder = Path(journal_directory).absolute()
    if (not folder.is_relative_to(Path(receipt_path).absolute().parent)
            or folder.is_relative_to(root) or folder.exists()):
        raise ValueError("Use a new rehearsal journal directory beside, not inside, the copy.")
    if (root / "storage.json").exists():
        raise ValueError("Copy already has a storage marker; finish recovery before another rehearsal.")
    saved = {row["path"]: row for row in receipt["files"]}
    proposal = json.loads(Path(proposal_path).read_text())["mapping"]
    directories = {}
    for source, row in proposal.items():
        reason = row.get("reason", "")
        if reason.startswith("PNG:"):
            parent = reason[4:]
            tail = source[len(parent):]
            if not tail.startswith("/") or not row["target"].endswith(tail):
                raise ValueError("Inconsistent proposed PNG directory mapping.")
            directories[parent] = row["target"][:-len(tail)]
    for source, target in (("reference_cache", "project/reference_cache"),
                           ("project_assets", "project/assets"),
                           ("recovery_archives", "project/recovery")):
        if any(p.startswith(source + "/") for p in proposal):
            directories[source] = target
    files, rows, excluded_locks = {}, [], []
    # The lab prefix is intentionally different (and often longer). Budget the
    # actual deployment root recorded in the source receipt, not the lab name.
    policies = [OrganizedStorageLayout(str(receipt["source"]))]
    if windows_root:
        policies.append(OrganizedStorageLayout(windows_root))
    for source, row in proposal.items():
        target = row["target"]
        if source.endswith(".lock"):
            excluded_locks.append(source)
            continue
        if source not in saved or saved[source]["sha256"] != row["sha256"]:
            raise ValueError("Proposal is not bound to the verified copy receipt.")
        if re.search(r"(?:^|/)checkpoints/clip_\d+.*\.json$", source):
            raise ValueError("Bridge rehearsal cannot relocate legacy checkpoint control records.")
        inherited = next((p for p in directories if source.startswith(p + "/")), None)
        if inherited:
            if directories[inherited] + source[len(inherited):] != target:
                raise ValueError("File mapping disagrees with its directory alias.")
        else:
            files[source] = target
        original, destination = confined(root, source), confined(root, target)
        if destination.exists():
            raise FileExistsError("Rehearsal destination already exists: " + target)
        if not original.is_file() or sha256(original) != row["sha256"]:
            raise ValueError("Copy changed since receipt: " + source)
        for policy in policies:
            policy.check_budget(target)
            if target.endswith(".json"):
                policy.check_atomic_json_budget(target)
        rows.append({"source": source, "target": target, "sha256": row["sha256"]})
    # A directory alias must account for every live child, not hide untracked
    # additions, user edits, or unknown optional files at the old address.
    covered = {row["source"] for row in rows} | set(excluded_locks)
    for directory in directories:
        for path in confined(root, directory).rglob("*"):
            confined(root, path.relative_to(root).as_posix())
            if path.is_file() and path.relative_to(root).as_posix() not in covered:
                if path.name.endswith(".lock"):
                    continue
                raise ValueError("Untracked file in proposed directory: " + str(path))
    aliases = {"format": ALIASES, "files": files, "directories": directories}
    validate_aliases(aliases)
    moved = {row["source"] for row in rows}
    control = [{"source": p, "sha256": row["sha256"]} for p, row in saved.items()
               if p not in moved and not p.endswith(".lock")]
    _verify_control(root, control)
    identifier = uuid.uuid4().hex
    alias_path = "project/aliases/" + identifier + ".json"
    if confined(root, alias_path).exists():
        raise FileExistsError("Alias authority collision.")
    folder.mkdir(parents=True)
    journal = folder / "journal.json"
    atomic_json(journal, {"format": "h3_storage_rehearsal_journal_v1", "phase": "prepared",
        "root": str(root), "receipt": str(Path(receipt_path).absolute()),
        "operation_id": identifier, "alias_path": alias_path, "aliases": aliases,
        "rows": rows, "control": control, "organized_writers": bool(organized_writers),
        "excluded_coordination_files": excluded_locks})
    return journal


def _journal(path):
    path = Path(path).absolute()
    value = json.loads(path.read_text())
    if value.get("format") != "h3_storage_rehearsal_journal_v1":
        raise ValueError("Unsupported rehearsal journal.")
    root, _ = _copy_root(value["receipt"])
    if (str(root) != value["root"] or path.is_relative_to(root)
            or not path.is_relative_to(Path(value["receipt"]).parent)):
        raise ValueError("Rehearsal journal escapes its copy workspace.")
    validate_aliases(value["aliases"])
    if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("operation_id"))):
        raise ValueError("Invalid rehearsal operation identity.")
    if not re.fullmatch(r"project/aliases/[0-9a-f]{32}\.json", value["alias_path"]):
        raise ValueError("Invalid rehearsal authority path.")
    for row in value["rows"]:
        confined(root, row["source"])
        confined(root, row["target"])
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise ValueError("Invalid rehearsal checksum.")
    return path, value, root


def _verify_control(root, rows):
    for row in rows:
        path = confined(root, row["source"])
        if not path.is_file() or sha256(path) != row["sha256"]:
            raise ValueError("Project state changed during rehearsal; publication refused: " + row["source"])


def _verify_namespace(root, value):
    """A new scene/history record is a change even if old files are intact."""
    allowed = {"storage.json", value["alias_path"]}
    allowed.update(row["source"] for row in value["control"])
    allowed.update(path for row in value["rows"] for path in (row["source"], row["target"]))
    for path in root.rglob("*"):
        address = path.relative_to(root).as_posix()
        confined(root, address)
        if path.is_file() and not address.endswith(".lock") and address not in allowed:
            raise ValueError("Project acquired an untracked file during rehearsal; publication refused: " + address)


def _check_marker(root, value):
    path = confined(root, "storage.json")
    if path.exists():
        marker = json.loads(path.read_text())
        if (marker.get("format") != FORMAT or marker.get("version") != 1
                or marker.get("mode") != "rehearsal"
                or marker.get("operation_id") != value["operation_id"]):
            raise ValueError("Storage marker belongs to another operation; no overwrite performed.")


def _lock(root):
    # Same cross-process inode as normal checkpoint commits; never relocated.
    return _raw_checkpoint_run_lock(str(root.parent.parent), root.name)


def relocate(journal_path, *, after_move=None):
    journal, value, root = _journal(journal_path)
    if value["phase"] not in ("prepared", "moving"):
        raise ValueError("Rehearsal is not prepared for relocation.")
    with _lock(root):
        marker = {"format": FORMAT, "version": 1, "mode": "rehearsal", "phase": "moving",
                  "operation_id": value["operation_id"]}
        if value["phase"] == "prepared":
            if (root / "storage.json").exists():
                raise FileExistsError("Another storage operation appeared.")
            value["phase"] = "moving"
            atomic_json(journal, value)
        _check_marker(root, value)
        _verify_namespace(root, value)
        atomic_json(root / "storage.json", marker)
        for index, row in enumerate(value["rows"]):
            source, target = confined(root, row["source"]), confined(root, row["target"])
            if target.exists():
                if source.exists() or sha256(target) != row["sha256"]:
                    raise ValueError("Relocation collision or changed target; no overwrite performed.")
            else:
                if not source.is_file() or sha256(source) != row["sha256"]:
                    raise ValueError("Source changed during relocation; recover the journal.")
                target.parent.mkdir(parents=True, exist_ok=True)
                # No files outside this independent copy are renamed.
                source.rename(target)
                sync_directory(source.parent)
                sync_directory(target.parent)
            if after_move:
                after_move(index + 1)
        for row in value["rows"]:
            if sha256(confined(root, row["target"])) != row["sha256"]:
                raise ValueError("Relocated copy failed full checksum verification.")
        alias = confined(root, value["alias_path"])
        if alias.exists() and json.loads(alias.read_text()) != value["aliases"]:
            raise ValueError("Alias authority collision; no overwrite performed.")
        _verify_control(root, value["control"])
        _verify_namespace(root, value)
        atomic_json(alias, value["aliases"])
        marker.update(phase="ready", aliases=value["alias_path"], aliases_sha256=sha256(alias))
        if value.get("organized_writers"):
            marker.update(writer_policy="organized_payloads_v1", writer_generation=0)
        atomic_json(root / "storage.json", marker)
        value["phase"] = "ready"
        atomic_json(journal, value)
    return {"moved": len(value["rows"]), "locks_kept": len(value["excluded_coordination_files"])}


def rollback(journal_path):
    journal, value, root = _journal(journal_path)
    if value["phase"] == "restored":
        return {"restored": len(value["rows"])}
    with _lock(root):
        _check_marker(root, value)
        current = root / "storage.json"
        if current.exists() and json.loads(current.read_text()).get("writer_generation", 0):
            raise ValueError("New organized reservations exist after migration. Preserve/reverse-migrate "
                             "the new work before rollback; no files were moved or removed.")
        retired = journal.parent / "retired-storage.json"
        # Crash after retiring the marker but before recording completion.
        # Do not create another marker and then collide with our own archive.
        if retired.exists() and not (root / "storage.json").exists():
            previous = json.loads(retired.read_text())
            if (value["phase"] != "restoring" or previous.get("operation_id") != value["operation_id"]
                    or previous.get("format") != FORMAT):
                raise ValueError("Unrecognized recovery marker archive; retained for inspection.")
            for row in value["rows"]:
                source, target = confined(root, row["source"]), confined(root, row["target"])
                if target.exists() or not source.is_file() or sha256(source) != row["sha256"]:
                    raise ValueError("Recovery completion could not be verified.")
            value["phase"] = "restored"
            atomic_json(journal, value)
            return {"restored": len(value["rows"])}
        if value["phase"] == "ready":
            # Existing aliases can also receive new files/appends without a
            # new reservation. Refuse before replacing a usable ready marker.
            _verify_control(root, value["control"])
            _verify_namespace(root, value)
        # Inspect all collisions before recovering any file.
        for row in value["rows"]:
            source, target = confined(root, row["source"]), confined(root, row["target"])
            if source.exists() and target.exists():
                raise ValueError("Rollback collision; both files retained: " + row["source"])
            present = target if target.exists() else source
            if not present.is_file() or sha256(present) != row["sha256"]:
                raise ValueError("Rollback bytes changed/missing; preserved for inspection: " + row["source"])
        value["phase"] = "restoring"
        atomic_json(journal, value)
        atomic_json(root / "storage.json", {
            "format": FORMAT, "version": 1, "mode": "rehearsal", "phase": "restoring",
            "operation_id": value["operation_id"]})
        for row in reversed(value["rows"]):
            source, target = confined(root, row["source"]), confined(root, row["target"])
            if target.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
                sync_directory(target.parent)
                sync_directory(source.parent)
        alias = confined(root, value["alias_path"])
        if alias.exists():
            destination = journal.parent / "retired-aliases.json"
            if destination.exists():
                raise FileExistsError("Recovery alias archive already exists; no overwrite.")
            alias.rename(destination)
        if retired.exists():
            raise FileExistsError("Recovery marker archive already exists; no overwrite.")
        (root / "storage.json").rename(retired)
        sync_directory(root)
        value["phase"] = "restored"
        atomic_json(journal, value)
    return {"restored": len(value["rows"])}
