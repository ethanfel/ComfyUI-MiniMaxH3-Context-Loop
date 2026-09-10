"""Bounded, read-only legacy storage inventory. No ComfyUI or tensor imports.

This is evidence for a future migration, NOT a retention/deletion authority.
Only stat data and selected JSON metadata are read. No repair, adoption, locks,
hash caches, model deserialization, or project writes are permitted here.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

if __package__:
    from .artifact_paths import artifact_address, is_link_or_junction
else:
    from artifact_paths import artifact_address, is_link_or_junction

FORMAT = "h3_storage_inventory_v1"
CATEGORIES = ("takes", "processing", "exports", "recovery", "assets",
              "conditioning_cache", "preview_cache", "state", "unclassified")
# Limit both disk work and the size of a report requested through the UI.
MAX_FILES = 100_000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
MAX_REFERENCES = 200_000
MAX_ISSUES = 500
_RUN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?\Z")
_BRANCH = re.compile(r"[0-9a-f]{32}\Z")
_POINTER = re.compile(r"clip_[0-9]{4}\.json\Z")
# Do not interpret prompts, workflow widgets, tokens, authoring snapshots or
# arbitrary strings as artifact addresses. These files still count in usage.
_OPAQUE = {"plan.json", "workflow.json", "api_prompt.json", "branch.json",
           "main.json", "plan_studio_presentation.json", "project_ownership.json"}
_PATH_FIELDS = {
    "segment", "checkpoint", "generated_audio", "blend_segment", "prompt_file",
    "revision_metadata", "metadata", "metadata_path", "checkpoint_path", "video_path",
    "audio_path", "manifest_path", "chapter_manifest_path", "run_archive_plan",
    "run_archive_workflow", "run_archive_api_prompt", "reference_cache_path",
    "path", "file", "directory",
}


def _scope(address):
    parts = PurePosixPath(address).parts
    if len(parts) > 2 and parts[0] == "branches" and _BRANCH.fullmatch(parts[1]):
        return parts[1], parts[2:]
    return "main", parts


def _classification(address):
    branch, parts = _scope(address)
    top, name = parts[0], parts[-1]
    profile = None
    if top == "upscaled" and len(parts) >= 3:
        profile = "/".join(parts[:2])
    elif top == "chapters" and len(parts) >= 5 and parts[2] == "upscaled":
        profile = "/".join(parts[:4])
    if profile:
        category = "exports" if "frames" in parts or "final" in parts else "processing"
    elif top in {"frames", "final"} or (top == "chapters" and "frames" in parts):
        category = "exports"
    elif top == "chapters" and "final" in parts:
        category = "exports"
    elif top in {"recovery_archives", "reviews", "prompt_history", "authoring_backups"}:
        category = "recovery"
    elif top == "project_assets":
        category = "assets"
    elif top == "reference_cache":
        category = "conditioning_cache"
    elif top in {".plan_studio_thumbnails", ".plan_studio_source_previews"}:
        category = "preview_cache"
    elif top in {"segments", "blend_segments", "generated_audio", "checkpoints"}:
        category = "state" if _POINTER.fullmatch(name) else "takes"
    elif (top in {"branches", "chapters", "alternates", "partial", "orchestration"}
          or name in _OPAQUE or name in {"manifest.json", "editorial.json", "png_exports.json",
                                        ".png_export_hash_cache.json"}):
        category = "state"
    else:
        category = "unclassified"
    return category, branch, profile


def _metadata_candidate(address):
    _, parts = _scope(address)
    return (parts[-1].endswith(".json") and parts[-1] not in _OPAQUE
            and parts[0] not in {"recovery_archives", "prompt_history", "authoring_backups"}
            and not parts[-1].startswith(".png_export_hash_cache"))


class _Inventory:
    def __init__(self, output_root, run_name, max_files):
        if not isinstance(run_name, str) or not _RUN.fullmatch(run_name):
            raise ValueError("Invalid H3 project name.")
        self.output = Path(output_root).resolve()
        # Do not resolve an untrusted project path before checking its parents.
        self.root = self.output / "h3_chains" / run_name
        for path in (self.root.parent, self.root):
            if is_link_or_junction(path):
                raise ValueError("Storage inspection does not follow project links or junctions.")
        if not self.root.is_dir():
            raise FileNotFoundError("H3 project folder does not exist.")
        self.run = run_name
        self.max_files = max(1, min(int(max_files), MAX_FILES))
        self.files = {}
        self.dirs = {}
        self.stats = {}
        self.issues = []
        self.issue_counts = Counter()
        self.references = []
        self.records = []
        self.complete = True
        self.json_bytes = 0
        self.json_documents = 0
        self.observed_formats = Counter()

    def issue(self, code, path, message):
        self.issue_counts[code] += 1
        if len(self.issues) < MAX_ISSUES:
            self.issues.append({"code": code, "path": path, "message": message})

    def guarded(self, address):
        path = self.root
        for part in PurePosixPath(address).parts:
            path = path / part
            if is_link_or_junction(path):
                raise ValueError("Link or junction was not followed.")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Path leaves the inspected project.")
        return path

    def scan(self):
        pending = [self.root]
        entries_seen = 0
        while pending:
            directory = pending.pop()
            relative = directory.relative_to(self.root).as_posix()
            try:
                self.guarded(relative)
                self.dirs[relative] = directory.stat().st_mtime_ns
                with os.scandir(directory) as entries:
                    for entry in entries:
                        entries_seen += 1
                        if entries_seen > self.max_files:
                            self.complete = False
                            self.issue("entry_limit", relative, "Entry limit reached; report is partial.")
                            return
                        address = Path(entry.path).relative_to(self.root).as_posix()
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode) or is_link_or_junction(Path(entry.path)):
                                self.complete = False
                                self.issue("link_skipped", address, "Link or junction was not followed.")
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(Path(entry.path))
                                continue
                            if not stat.S_ISREG(info.st_mode):
                                self.complete = False
                                self.issue("special_file", address, "Non-regular file was not opened.")
                                continue
                            category, branch, profile = _classification(address)
                            flags = ["unverified"]
                            if "retired_manifests" in PurePosixPath(address).parts:
                                flags.append("retired")
                            if category == "preview_cache":
                                flags.append("rebuildable_preview")
                            if any(part == ".transactions" or part == ".png_pending.json"
                                   or part.startswith((".png-stage", ".branch-"))
                                   for part in PurePosixPath(address).parts):
                                self.issue("pending_transaction", address,
                                           "Pending/staged write needs its owning feature's recovery; inspection does not recover it.")
                            self.files[address] = {
                                "path": address, "category": category, "branch_id": branch,
                                "profile": profile, "logical_bytes": info.st_size,
                                "allocated_bytes": getattr(info, "st_blocks", None),
                                "mtime_ns": str(info.st_mtime_ns), "flags": flags,
                            }
                            if self.files[address]["allocated_bytes"] is not None:
                                self.files[address]["allocated_bytes"] *= 512
                            self.stats[address] = info
                        except (OSError, ValueError):
                            self.complete = False
                            self.issue("unreadable_entry", address, "Could not inspect this entry.")
            except (OSError, ValueError):
                self.complete = False
                self.issue("unreadable_directory", relative, "Could not inspect this directory.")

    def read_json(self, address):
        expected = self.stats[address]
        if (expected.st_size > MAX_JSON_BYTES
                or self.json_bytes + expected.st_size > MAX_TOTAL_JSON_BYTES):
            self.complete = False
            self.issue("metadata_limit", address, "Metadata read limit reached; references are incomplete.")
            return None
        try:
            path = self.guarded(address)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (not stat.S_ISREG(opened.st_mode)
                        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                        != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                    raise ValueError("Metadata changed during inspection.")
                raw = handle.read(MAX_JSON_BYTES + 1)
                self.json_bytes += len(raw)
                if len(raw) > MAX_JSON_BYTES:
                    raise ValueError("Metadata grew during inspection.")
            self.json_documents += 1
            return json.loads(raw)
        except (OSError, ValueError, RecursionError):
            self.complete = False
            self.issue("unreadable_metadata", address, "Invalid, unreadable or changing JSON; no repair attempted.")
            return None

    def target(self, value, document, field):
        # Output-relative legacy addresses are the dominant contract. PNG files
        # are relative to export.json. Never guess by matching basenames.
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            # Saved source-reference media may use host-absolute input paths.
            # They are outside this lookup contract, not necessarily corrupt.
            # Never resolve/stat them or expose host-private address contents.
            return None, "external"
        try:
            address = artifact_address(value)
        except ValueError:
            return None, "invalid"
        prefix = "h3_chains/" + self.run + "/"
        if address.startswith(prefix):
            return address[len(prefix):], "project"
        if address.startswith("h3_chains/") or field == "directories":
            return address, "external"
        if field == "file" or (field == "path" and PurePosixPath(document).name == "export.json"):
            return (PurePosixPath(document).parent / address).as_posix(), "project"
        # Unqualified filenames in arbitrary metadata have feature-specific
        # meaning. Report them as unresolved, not as missing project artifacts.
        return address, "unresolved"

    def inspect_document(self, document, value):
        if not isinstance(value, dict):
            self.issue("unknown_document", document, "Expected a metadata object; references not interpreted.")
            return
        if value.get("run_name", self.run) != self.run:
            self.issue("foreign_metadata", document, "Metadata declares a different project; references not interpreted.")
            return
        fmt = value.get("format")
        if isinstance(fmt, str):
            self.observed_formats[fmt[:120]] += 1
        record = value.get("segment")
        if isinstance(record, dict) and isinstance(record.get("revision"), str):
            self.records.append({
                "metadata": document, "revision": record["revision"],
                "scene": record.get("index"), "source_revision": record.get("source_revision"),
                "alternate_of_revision": record.get("alternate_of_revision"),
                "full_latent": ("declared_saved" if record.get("latent_saved") is True
                                else "declared_omitted" if record.get("latent_saved") is False
                                else "unknown"),
                "branch_id": self.files[document]["branch_id"],
                "profile": self.files[document]["profile"],
                "is_assignment": bool(_POINTER.fullmatch(PurePosixPath(document).name)),
            })
        stack = [(value, "")]
        while stack:
            item, location = stack.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    if (location.endswith("/archives") and key in {"plan", "workflow", "api_prompt"}
                            and isinstance(child, str)):
                        self.add_reference(document, location + "/" + key, key, child, item)
                        continue
                    if key in {"prompt", "prompt_text", "authoring", "workflow", "api_prompt",
                               "shots", "positive", "negative", "supersedes", "execution"}:
                        continue
                    loc = location + "/" + str(key)
                    if isinstance(child, str) and key in _PATH_FIELDS:
                        self.add_reference(document, loc, key, child, item)
                    elif key == "directories" and isinstance(child, list):
                        for entry in child:
                            if isinstance(entry, str):
                                self.add_reference(document, loc, key, entry, {})
                    elif isinstance(child, (dict, list)):
                        stack.append((child, loc))
            elif isinstance(item, list):
                stack.extend((child, location + "/" + str(index))
                             for index, child in enumerate(item) if isinstance(child, (dict, list)))

    def add_reference(self, document, location, field, value, item):
        if not value:
            return  # Optional media fields commonly contain empty placeholders.
        if len(self.references) >= MAX_REFERENCES:
            if not self.issue_counts["reference_limit"]:
                self.issue("reference_limit", document, "Reference limit reached; report is partial.")
            self.complete = False
            return
        target, scope = self.target(value, document, field)
        # Never expose arbitrary invalid strings (could be a prompt or secret).
        row = {"document": document, "field": location, "target": target, "scope": scope}
        if scope == "project":
            present = target in self.files or target in self.dirs
            row["status"] = "present_unverified" if present else "missing_or_unscanned"
            if not present:
                self.issue("missing_reference", document, "Referenced project address was not found: " + target)
            elif target in self.files and field == "file":
                # Size/mtime discrepancies are evidence, not a checksum verdict.
                expected_size = item.get("size", item.get("size_bytes"))
                if isinstance(expected_size, int) and expected_size != self.files[target]["logical_bytes"]:
                    row["status"] = "size_mismatch"
                    self.files[target]["flags"].append("possible_edit")
                    self.issue("size_mismatch", target, "Size differs from recorded PNG metadata; preserve current bytes.")
                elif isinstance(item.get("mtime_ns"), int) and str(item["mtime_ns"]) != self.files[target]["mtime_ns"]:
                    row["status"] = "timestamp_changed_unverified"
                    self.files[target]["flags"].append("timestamp_changed")
        else:
            row["status"] = "not_followed"
            self.issue(scope + "_reference", document,
                       "Address is outside this inventory or cannot be resolved safely; it was not followed.")
        self.references.append(row)

    def finish(self):
        references = defaultdict(set)
        for row in self.references:
            if row["scope"] == "project" and row["target"] in self.files:
                references[row["target"]].add(row["document"])
        inode_paths = defaultdict(list)
        for address, info in self.stats.items():
            if info.st_ino:
                inode_paths[(info.st_dev, info.st_ino)].append(address)
        for paths in inode_paths.values():
            if len(paths) > 1:
                for path in paths:
                    self.files[path]["flags"].append("hardlinked_in_project")
        totals = {category: {"files": 0, "logical_bytes": 0, "allocated_bytes": 0,
                             "allocation_unknown_files": 0} for category in CATEGORIES}
        for address, row in sorted(self.files.items()):
            incoming = sorted(references[address])
            row["referenced_by"] = incoming
            row["referencing_branches"] = sorted({_scope(path)[0] for path in incoming})
            if len(incoming) > 1:
                row["flags"].append("shared_reference")
            elif incoming:
                row["flags"].append("referenced")
            elif row["category"] in {"takes", "processing", "exports", "conditioning_cache"}:
                row["flags"].append("unreferenced_candidate")
            row["flags"] = sorted(set(row["flags"]))
            group = totals[row["category"]]
            group["files"] += 1
            group["logical_bytes"] += row["logical_bytes"]
            if row["allocated_bytes"] is None:
                group["allocation_unknown_files"] += 1
            else:
                group["allocated_bytes"] += row["allocated_bytes"]
        # Detect additions/removals and replacements during this unlocked read.
        # This is not an atomic snapshot and does not detect same-stat byte edits.
        for address, before in self.stats.items():
            try:
                after = self.guarded(address).stat()
                changed = ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                           != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
            except (OSError, ValueError):
                changed = True
            if changed:
                self.complete = False
                self.issue("changed_during_scan", address, "File changed during inspection; rescan when writers are idle.")
        for address, before in self.dirs.items():
            try:
                changed = self.guarded(address).stat().st_mtime_ns != before
            except (OSError, ValueError):
                changed = True
            if changed:
                self.complete = False
                self.issue("changed_during_scan", address, "Directory changed during inspection; rescan when writers are idle.")
        files = [self.files[key] for key in sorted(self.files)]
        stat_digest = hashlib.sha256(json.dumps([
            [row["path"], row["logical_bytes"], row["mtime_ns"]] for row in files
        ], separators=(",", ":")).encode()).hexdigest()
        return {
            "format": FORMAT, "run_name": self.run,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "scope": "project_all_branches", "scan_complete": self.complete,
            "inventory_stat_id": stat_digest,
            "verification": "stat_and_metadata_only",
            "migration": {"enabled": False, "status": "not_assessed",
                          "reason": "Migration and authoritative retention checks are not implemented."},
            "limitations": [
                "No media/tensor hashes or tensor contents were read. Latent availability is metadata-declared only.",
                "Reference coverage is partial: opaque authoring/workflows, external files, global caches and input assets are excluded.",
                "Multiple references are not proof of shared live ownership; unreferenced candidates are NOT safe-to-delete files.",
                "Allocated bytes are per-path filesystem reports, may count hardlinks twice, and do not measure backend deduplication.",
                "Unlocked, non-atomic scan. The stat ID is not a content checksum or a migration/cleanup authorization.",
            ],
            "totals": {key: sum(row[key] for row in totals.values())
                       for key in ("files", "logical_bytes", "allocated_bytes", "allocation_unknown_files")},
            "categories": totals, "files": files,
            "records": sorted(self.records, key=lambda row: row["metadata"]),
            "references": self.references,
            "observed_formats": dict(sorted(self.observed_formats.items())),
            "metadata_documents_read": self.json_documents,
            "issues": self.issues, "issue_counts": dict(sorted(self.issue_counts.items())),
            "issues_omitted": sum(self.issue_counts.values()) - len(self.issues),
            "longest_paths": [{"path": row["path"], "relative_chars": len(row["path"]),
                               "absolute_chars": len(str(self.root / row["path"]))}
                              for row in sorted(files, key=lambda row: (-len(row["path"]), row["path"]))[:10]],
        }


def inspect_storage(output_root, run_name, *, max_files=MAX_FILES):
    """Inspect one existing project without locks, repair, hashing media or writes."""
    inventory = _Inventory(output_root, run_name, max_files)
    inventory.scan()
    for address in sorted(inventory.files):
        if _metadata_candidate(address):
            value = inventory.read_json(address)
            if value is not None:
                inventory.inspect_document(address, value)
    return inventory.finish()
