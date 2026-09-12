"""Preview-confirmed retirement of chapter recovery pins, never clip media."""

import json
import os
from pathlib import Path, PurePosixPath
import re
from .artifact_paths import artifact_address, is_link_or_junction

from .checkpoint_manager import (
    CheckpointDeleteBlocked, CheckpointGraphManager, _fingerprint,
    _strict_run_name, checkpoint_run_lock,
)


class ChapterSnapshotManager:
    def __init__(self, output_root):
        self.root = Path(output_root).resolve()

    def _path(self, address):
        if not isinstance(address, str) or not address:
            raise ValueError("Select a saved chapter snapshot.")
        parts = PurePosixPath(artifact_address(address)).parts
        path = self.root
        for part in parts:
            path /= part
            if is_link_or_junction(path):
                raise ValueError("Snapshot retirement cannot follow symlinks or junctions.")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Chapter snapshot escapes the output directory.")
        return path

    def _preview(self, run, address):
        address = artifact_address(address)
        path = self._path(address)
        parts = PurePosixPath(address).parts
        if len(parts) > 4 and parts[2] == "branches" and re.fullmatch(r"[0-9a-f]{32}", parts[3]):
            parts = parts[:2] + parts[4:]
        if not (len(parts) == 6 and parts[:3] == ("h3_chains", run, "chapters")
                and parts[4] == "manifests"
                and re.fullmatch(r"[0-9a-f]{32}\.json", parts[5])):
            raise ValueError("Select an immutable chapter snapshot inside this run.")
        if not path.is_file():
            raise FileNotFoundError("The chapter snapshot is no longer available.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Chapter snapshot must be a JSON object.")
        chapter = data.get("chapter")
        if (data.get("format") != "h3_chain_chapter_manifest_v1"
                or data.get("run_name") != run
                or not isinstance(chapter, dict)
                or not isinstance(chapter.get("number"), int)
                or chapter["number"] < 1
                or not parts[3].startswith("%02d_" % chapter["number"])
                or not isinstance(data.get("segments"), list)
                or not data["segments"]
                or any(not isinstance(s, dict) or not isinstance(s.get("index"), int)
                       for s in data["segments"])):
            raise ValueError("Invalid chapter snapshot identity.")
        identity = {k: v for k, v in data.items()
                    if k not in ("sealed_at", "chapter_manifest_id", "chapter_manifest_path")}
        if (_fingerprint(identity)[:32] != path.stem
                or data.get("chapter_manifest_id") != path.stem
                or artifact_address(data.get("chapter_manifest_path")) != address):
            raise ValueError("Chapter snapshot failed its identity check.")
        destination = path.parent.parent / "retired_manifests" / path.name
        retired_address = destination.relative_to(self.root).as_posix()
        self._path(retired_address)
        if destination.exists():
            raise ValueError("A retired copy already exists; it will not be overwritten.")
        active, _ = CheckpointGraphManager(str(self.root)).active_selection(run)
        scenes = [{"scene": s["index"], "revision": s.get("revision", ""),
                   "active": active.get(s["index"]) == s.get("revision")}
                  for s in data["segments"]]
        stat = path.stat()
        snapshot = _fingerprint({
            "document": data, "active": active,
            "file": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns],
            "path": address, "retired_path": retired_address,
        })
        return {"ok": True, "allowed": True, "run_name": run,
                "path": address, "retired_path": retired_address,
                "chapter_number": chapter["number"],
                "chapter_title": str(chapter.get("title") or "Chapter %d" % chapter["number"]),
                "chapter_manifest_id": path.stem, "scenes": scenes,
                "snapshot": snapshot,
                "message": "Retiring releases only this snapshot's recovery pins. "
                           "Its metadata is archived; no clips, active pointers, "
                           "references, processing takes or exports are deleted."}

    def retirement_preview(self, run_name, address):
        run = _strict_run_name(run_name)
        with checkpoint_run_lock(str(self.root), run):
            return self._preview(run, address)

    def retire(self, run_name, address, expected_snapshot=""):
        run = _strict_run_name(run_name)
        with checkpoint_run_lock(str(self.root), run):
            preview = self._preview(run, address)
            if not expected_snapshot or expected_snapshot != preview["snapshot"]:
                raise CheckpointDeleteBlocked(
                    "Chapter snapshot or active selection changed; preview retirement again.", preview)
            path = self._path(preview["path"])
            destination = self._path(preview["retired_path"])
            destination.parent.mkdir(exist_ok=True)
            os.rename(path, destination)
            return {**preview, "message": "Chapter snapshot %s retired; recovery metadata "
                    "kept at %s. No clips were deleted." %
                    (preview["chapter_manifest_id"][:8], preview["retired_path"])}
