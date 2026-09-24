"""Reviewed saved-clip frame capture using native extraction and catalog rules."""
from __future__ import annotations

import copy
import os
import shutil

if __package__:
    from . import project_assets as native
    from .asset_copy import _hash, _stamp, command_staged
else:
    import project_assets as native
    from asset_copy import _hash, _stamp, command_staged

FIELDS = ("command_version", "project", "operation_id", "action", "base_revision",
          "asset_id", "source", "time_seconds", "tag", "folder_id", "preview_revision")


def inspect_capture(store, project, request, resolve):
    catalog = store.load(project)
    record = resolve(project, request)
    folder = str(request.get("folder_id") or "")
    if folder and folder not in {item["id"] for item in catalog["folders"]}:
        raise ValueError("The capture folder no longer exists.")
    tag = native._safe_tag(request.get("tag"), "capture")
    value = {"project": catalog["project"], "base_revision": _stamp(catalog),
        "source": copy.deepcopy(request["source"]), "time_seconds": record["time_seconds"],
        "tag": tag, "folder_id": folder, "copyable": len(catalog["assets"]) < native.MAX_CATALOG_ASSETS,
        "issue": "The asset catalog is full." if len(catalog["assets"]) >= native.MAX_CATALOG_ASSETS else "",
        "source_sha256": record["sha256"], "metadata_sha256": record["metadata_sha256"]}
    value["preview_revision"] = _hash(value)
    return value


def command_capture(store, project, body, resolve, extract, commit_guard):
    def snapshot(store, project, request):
        preview = inspect_capture(store, project, request, resolve)
        catalog = store.load(project)
        if _stamp(catalog) != preview["base_revision"]:
            raise native.ProjectAssetConflictError("The library changed during frame review.")
        return preview, catalog, None

    def stage(store, directory, request, catalog, _source, preview):
        record = resolve(project, request)
        if record["sha256"] != preview["source_sha256"] or record["metadata_sha256"] != preview["metadata_sha256"]:
            raise native.ProjectAssetConflictError("The saved clip changed after frame review.")
        scratch = os.path.join(directory, "stage")
        shutil.rmtree(scratch, ignore_errors=True)
        os.makedirs(scratch, exist_ok=True)
        picture = os.path.join(scratch, "capture.png")
        try:
            extract(record["path"], record["time_seconds"], picture)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        if inspect_capture(store, project, request, resolve)["preview_revision"] != request["preview_revision"]:
            raise native.ProjectAssetConflictError("The saved clip or library changed during capture.")
        staged = native.ProjectAssetStore(os.path.join(scratch, "input"), os.path.join(scratch, "output"))
        staged_dir, _ = staged._project_dir(project)
        native._atomic_json(os.path.join(staged_dir, "catalog.json"), catalog)
        result = staged.import_file(project, picture, role="picture", tag=request["tag"],
            folder_id=request["folder_id"], source_kind="frame_capture", original_name="frame_capture.png")
        entry = result["asset"]
        relative = entry["relative_path"]
        entry["relative_path"] = "images/%s_%s" % (request["operation_id"], os.path.basename(relative))
        entry["input_path"] = "h3_projects/%s/%s" % (project, entry["relative_path"])
        entry["source_origin"] = {"project": project, "kind": "saved_frame",
            **copy.deepcopy(request["source"]), "time_seconds": record["time_seconds"],
            "sha256": record["sha256"], "metadata_sha256": record["metadata_sha256"]}
        entry["transform"] = {"kind": "frame_capture", "operation_id": request["operation_id"],
            "time_seconds": record["time_seconds"]}
        return {"request": request, "phase": "prepared", "assets": [entry], "files": [{
            "staged": os.path.relpath(os.path.join(staged_dir, relative), directory),
            "relative_path": entry["relative_path"], "sha256": entry["sha256"]}]}

    return command_staged(store, project, body, "asset_capture", FIELDS, snapshot, stage,
        commit_guard=commit_guard)
