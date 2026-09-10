"""Reviewed cross-project copies with private staging and one catalog commit.

The native importer still defines tags, track bindings and media metadata. Its
intermediate saves happen in a private store; only the completed group becomes
visible in the destination. A prepared operation retains frozen bytes for retry.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil

if __package__:
    from . import project_assets as native
else:
    import project_assets as native

FIELDS = ("command_version", "project", "operation_id", "action", "base_revision",
          "source_project", "asset_id", "enabled", "folder_id", "preview_revision")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def _stamp(catalog):
    return str(catalog.get("storage_revision") or "empty")


def _snapshot(store, project, source_project, asset_id, enabled, folder_id):
    target = store.load(project)
    source = store.load(source_project)
    if source["project"] == target["project"]:
        raise ValueError("Choose another project; use Duplicate for a local asset.")
    if not isinstance(enabled, bool):
        raise ValueError("Choose whether the copied asset is enabled.")
    if folder_id and folder_id not in {item["id"] for item in target["folders"]}:
        raise ValueError("The destination folder no longer exists.")
    root = next((item for item in source["assets"] if item["id"] == asset_id), None)
    if not root:
        raise ValueError("The source asset no longer exists.")
    tracks = native.audio_track_bindings((root.get("options") or {}).get("audio_tracks"), source["assets"]) or {}
    ids = list(dict.fromkeys([asset_id, *filter(None, tracks.values())]))
    entries = [next(item for item in source["assets"] if item["id"] == value) for value in ids]
    identities = []
    for entry in entries:
        try:
            stat = os.stat(store._asset_path(source_project, entry))
        except OSError as exc:
            raise ValueError("Source media is unavailable: %s" % entry.get("tag", entry["id"])) from exc
        identities.append([entry["id"], entry.get("sha256"), stat.st_size, stat.st_mtime_ns])
    conflict = bool(enabled and root["role"] == "source_track" and any(
        item["role"] == "source_track" and item.get("enabled", True) for item in target["assets"]))
    preview = {"project": target["project"], "source_project": source["project"],
        "asset_id": asset_id, "base_revision": _stamp(target), "source_revision": _stamp(source),
        "enabled": enabled, "folder_id": folder_id, "assets": copy.deepcopy(entries),
        "copyable": not conflict and len(target["assets"]) + len(entries) <= native.MAX_CATALOG_ASSETS,
        "issue": "The destination already has an enabled Source track. Copy this one disabled." if conflict else
            "The destination asset catalog is full." if len(target["assets"]) + len(entries) > native.MAX_CATALOG_ASSETS else ""}
    preview["preview_revision"] = _hash([preview, identities])
    return preview, target, source


def preview_copy(store, project, source_project, asset_id, enabled=True, folder_id=""):
    return _snapshot(store, project, source_project, str(asset_id), enabled, str(folder_id))[0]


def _directory(store, project, operation):
    if not re.fullmatch(r"[0-9a-f]{32}", str(operation)):
        raise ValueError("A 32-character copy operation ID is required.")
    directory, _ = store._project_dir(project)
    path = os.path.realpath(os.path.join(directory, ".library_copies", operation))
    if not native._inside(directory, path):
        raise ValueError("Copy staging path escapes the project.")
    return path


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def pending_operation(store, project, operation):
    job = _read(os.path.join(_directory(store, project, operation), "operation.json"))
    if not job or job["request"].get("project") != project or job["phase"] == "rejected":
        return None
    return {"operation_id": operation, "action": job["request"]["action"], "phase": job["phase"],
            "asset_id": job["request"]["asset_id"],
            "request": job["request"]}


def pending_operations(store, project, catalog):
    directory, _ = store._project_dir(project)
    root = os.path.join(directory, ".library_copies")
    if not os.path.isdir(root):
        return []
    result = []
    for operation in sorted(os.listdir(root)):
        if not re.fullmatch(r"[0-9a-f]{32}", operation) or operation in (catalog.get("library_receipts") or {}):
            continue
        item = pending_operation(store, project, operation)
        if item:
            result.append({key: value for key, value in item.items() if key != "request"})
    return result


def pending_copy(store, project, operation):
    value = pending_operation(store, project, operation)
    return value if value and value["action"] == "asset_copy" else None


def pending_copies(store, project, catalog):
    return [item for item in pending_operations(store, project, catalog) if item["action"] == "asset_copy"]


def finish_copy(store, project, receipt):
    """A committed receipt proves publication no longer needs private staging.

    A status read holds the catalog lock. It must not take the operation lock
    in reverse order. After commit, retries follow the receipt path and no
    publisher can still need the staging bytes.
    """
    if receipt.get("project") != project or receipt.get("action") not in ("asset_copy", "asset_derive"):
        return
    directory = _directory(store, project, receipt["operation_id"])
    job = _read(os.path.join(directory, "operation.json"))
    if job and _hash(job["request"]) == receipt.get("request_sha256"):
        shutil.rmtree(os.path.join(directory, "stage"), ignore_errors=True)


def _stage(store, directory, request, target, source, preview):
    scratch = os.path.join(directory, "stage")
    shutil.rmtree(scratch, ignore_errors=True)
    source_name, target_name = source["project"], target["project"]
    entries = preview["assets"]

    class StagedStore(native.ProjectAssetStore):
        imported = 0

        def load(self, project, **kwargs):
            return copy.deepcopy(source) if project == source_name else super().load(project, **kwargs)

        def asset(self, project, asset_id):
            if project != source_name:
                return super().asset(project, asset_id)
            entry = next(item for item in entries if item["id"] == asset_id)
            return copy.deepcopy(entry), store._asset_path(source_name, entry)

        def import_file(self, project, path, **kwargs):
            origin = entries[self.imported]
            if self.imported == 0 and not request["enabled"] and kwargs.get("role") == "source_track":
                kwargs["role"] = "audio_reference" if origin["kind"] == "audio" else "video"
            result = super().import_file(project, path, **kwargs)
            entry = result["asset"]
            if origin.get("sha256") and entry["sha256"] != origin["sha256"]:
                raise native.ProjectAssetConflictError("Source media changed after review.")
            self.imported += 1
            return result

    staged = StagedStore(os.path.join(scratch, "input"), os.path.join(scratch, "output"))
    staged_directory, _ = staged._project_dir(target_name)
    if _stamp(target) != "empty":
        native._atomic_json(os.path.join(staged_directory, "catalog.json"), target)
    result = staged.import_project_asset(target_name, source_name, request["asset_id"])
    staged.update(target_name, result["asset"]["id"], {"role": entries[0]["role"], "enabled": request["enabled"]})
    final = staged.load(target_name)
    created = final["assets"][len(target["assets"]):]
    files = []
    for entry, origin in zip(created, entries, strict=True):
        relative = entry["relative_path"]
        group, basename = relative.split("/", 1)
        entry["relative_path"] = "%s/%s_%s" % (group, request["operation_id"], basename)
        entry["input_path"] = "h3_projects/%s/%s" % (target_name, entry["relative_path"])
        entry["folder_id"] = request["folder_id"]
        entry["source_origin"] = {"project": source_name, "asset_id": origin["id"],
            **{key: copy.deepcopy(origin[key]) for key in ("parent_asset_id", "transform", "source_origin", "folder_id", "sha256", "source_kind") if key in origin}}
        files.append({"staged": os.path.relpath(os.path.join(staged_directory, relative), directory),
                      "relative_path": entry["relative_path"], "sha256": entry["sha256"]})
    if preview_copy(store, target_name, source_name, request["asset_id"], request["enabled"], request["folder_id"])["preview_revision"] != request["preview_revision"]:
        raise native.ProjectAssetConflictError("Source or destination changed while copying. Review again.")
    return {"request": request, "phase": "prepared", "assets": created, "files": files}


def _publish_files(store, project, directory, job):
    for root in (store._project_dir(project)[0], store._backup_dir(project)[0]):
        for item in job["files"]:
            source = os.path.realpath(os.path.join(directory, item["staged"]))
            destination = os.path.realpath(os.path.join(root, item["relative_path"]))
            if not native._inside(directory, source) or not native._inside(root, destination):
                raise ValueError("Copy media path escapes its store.")
            if os.path.isfile(destination):
                if native._file_sha256(destination) != item["sha256"]:
                    raise ValueError("Copy destination media changed; it was not overwritten.")
                continue
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            temporary = destination + ".copy.tmp"
            shutil.copy2(source, temporary)
            if native._file_sha256(temporary) != item["sha256"]:
                raise ValueError("Staged media failed its SHA-256 check.")
            os.replace(temporary, destination)


@native._project_mutation
def _publish(store, project, request, job):
    catalog = store.load(project)
    if _stamp(catalog) != request["base_revision"]:
        raise native.ProjectAssetConflictError("The destination library changed. Refresh and review again.")
    if len(catalog.get("library_receipts") or {}) >= 1024:
        raise ValueError("The retained library-command limit was reached.")
    store._library_command = {"operation_id": request["operation_id"], "project": project,
        "action": request["action"], "request_sha256": _hash(request), "before_revision": request["base_revision"],
        "source_project": request.get("source_project", project), "source_asset_id": request["asset_id"],
        "asset_ids_before": [item["id"] for item in catalog["assets"]],
        "folder_ids_before": [item["id"] for item in catalog["folders"]]}
    try:
        catalog["assets"].extend(copy.deepcopy(job["assets"]))
        store._save_catalog(catalog)
    finally:
        store._library_command = None


def command_copy(store, project, body):
    def snapshot(store, project, request):
        return _snapshot(store, project, request["source_project"], request["asset_id"], request["enabled"], request["folder_id"])
    return command_staged(store, project, body, "asset_copy", FIELDS, snapshot, _stage)


def command_staged(store, project, body, action, fields, snapshot, stage):
    """Publish a native media operation's prepared assets with one receipt."""
    project = native._safe_project(project)
    request = {key: body.get(key) for key in fields}
    if request["project"] != project or request["command_version"] != 1 or request["action"] != action:
        raise ValueError("Media request does not match this project or command version.")
    directory = _directory(store, project, request["operation_id"])
    path = os.path.join(directory, "operation.json")
    saved = (store.load(project).get("library_receipts") or {}).get(request["operation_id"])
    if saved:
        if saved.get("project") != project or saved.get("request_sha256") != _hash(request):
            raise ValueError("Media operation belongs to another request or project.")
        finish_copy(store, project, saved)
        return {"catalog": store.public_catalog(project, create=False), "receipt": saved, "replayed": True}
    # Reserve a new operation before opening its lock file. Rejected new IDs
    # must not create more directories after the retained-operation limit.
    root = os.path.dirname(directory)
    with native._catalog_lock(root), native._catalog_file_lock(root + ".admission"):
        if not os.path.isdir(directory):
            count = len([name for name in os.listdir(root) if re.fullmatch(r"[0-9a-f]{32}", name)]) if os.path.isdir(root) else 0
            if count >= 1024:
                raise ValueError("The retained media-operation limit was reached.")
            os.makedirs(directory, exist_ok=True)
    # Operation lock first, then brief target commit lock. No source/target
    # pair of project locks is held while copying, including reciprocal imports.
    with native._catalog_lock(path), native._catalog_file_lock(path):
        saved = (store.load(project).get("library_receipts") or {}).get(request["operation_id"])
        if saved:
            if saved.get("project") != project or saved.get("request_sha256") != _hash(request):
                raise ValueError("Copy operation belongs to another request or project.")
            shutil.rmtree(os.path.join(directory, "stage"), ignore_errors=True)
            return {"catalog": store.public_catalog(project, create=False), "receipt": saved, "replayed": True}
        job = _read(path)
        if job and job["request"] != request:
            raise ValueError("Copy operation belongs to another request.")
        if job and job["phase"] == "rejected":
            raise native.ProjectAssetConflictError("This copy was rejected. Review a new copy request.")
        try:
            if not job or job["phase"] != "prepared":
                preview, target, source = snapshot(store, project, request)
                if preview["base_revision"] != request["base_revision"] or preview["preview_revision"] != request["preview_revision"]:
                    raise native.ProjectAssetConflictError("Source or destination changed after review.")
                if not preview["copyable"]:
                    raise ValueError(preview["issue"])
                native._atomic_json(path, {"request": request, "phase": "staging"})
                job = stage(store, directory, request, target, source, preview)
                native._atomic_json(path, job)
            _publish_files(store, project, directory, job)
            _publish(store, project, request, job)
        except (ValueError, TypeError) as exc:
            # A rejected publication removes only this operation's unreferenced
            # media. I/O failures retain their stage for exact retry instead.
            current = store.load(project)
            if request["operation_id"] not in (current.get("library_receipts") or {}):
                for entry in (job or {}).get("assets", []):
                    store._delete_asset_files(project, entry, current)
                native._atomic_json(path, {"request": request, "phase": "rejected"})
                shutil.rmtree(os.path.join(directory, "stage"), ignore_errors=True)
            raise exc
        shutil.rmtree(os.path.join(directory, "stage"), ignore_errors=True)
        return {"catalog": store.public_catalog(project, create=False),
                "receipt": store.load(project)["library_receipts"][request["operation_id"]], "replayed": False}
