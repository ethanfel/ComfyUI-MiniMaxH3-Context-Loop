"""Conditional library editing over the native ProjectAssetStore operations."""
from __future__ import annotations

import hashlib
import json
import re

if __package__:
    from .project_assets import ProjectAssetConflictError, _project_mutation
    from .asset_copy import command_copy, pending_copy, pending_operation, finish_copy
    from .asset_image import command_image
else:
    from project_assets import ProjectAssetConflictError, _project_mutation
    from asset_copy import command_copy, pending_copy, pending_operation, finish_copy
    from asset_image import command_image

MAX_COMMANDS = 1024
ACTIONS = frozenset(("folder_create", "folder_update", "folder_delete",
    "folder_reorder", "asset_update", "asset_duplicate", "asset_delete", "asset_reorder"))


def _receipt(value):
    return {key: item for key, item in value.items()
            if key not in ("cleanup_asset", "asset_ids_before", "folder_ids_before")}


@_project_mutation
def inspect_library(store, project, operation_id=""):
    catalog = store.load(project)
    receipt = (catalog.get("library_receipts") or {}).get(str(operation_id))
    if receipt and receipt.get("project") != catalog["project"]:
        receipt = None
    if receipt and receipt.get("cleanup_asset"):
        # Finish cleanup for the already committed deletion before confirming
        # it. Current catalog references still protect any reused media.
        store._delete_asset_files(project, receipt["cleanup_asset"], catalog)
    if receipt:
        finish_copy(store, project, receipt)
    return {"catalog": store.public_catalog(project, create=False),
            "receipt": _receipt(receipt) if receipt else None,
            "pending_copy": pending_copy(store, project, operation_id) if operation_id and not receipt else None,
            "pending_operation": pending_operation(store, project, operation_id) if operation_id and not receipt else None}


def command_library(store, project, body):
    if isinstance(body, dict) and body.get("action") == "asset_copy":
        return command_copy(store, project, body)
    if isinstance(body, dict) and body.get("action") == "asset_derive":
        return command_image(store, project, body)
    return _command_library(store, project, body)


@_project_mutation
def _command_library(store, project, body):
    if not isinstance(body, dict) or body.get("command_version") != 1:
        raise ValueError("Unsupported asset library command version.")
    action = body.get("action")
    if action not in ACTIONS:
        raise ValueError("Unsupported asset library action.")
    operation = str(body.get("operation_id") or "")
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("A 32-character library operation ID is required.")
    expected = body.get("base_revision")
    if expected != "empty" and not re.fullmatch(r"[0-9a-f]{32}", str(expected or "")):
        raise ValueError("Review the current library before changing it.")
    request = {key: body.get(key) for key in ("action", "base_revision", "asset_id",
        "folder_id", "name", "color", "tag", "changes", "asset_ids", "folder_ids")}
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    catalog = store.load(project)
    saved = (catalog.get("library_receipts") or {}).get(operation)
    if saved:
        if saved["project"] != catalog["project"] or saved["request_sha256"] != fingerprint:
            raise ValueError("Library operation ID already belongs to another request or project.")
        if saved.get("cleanup_asset"):
            store._delete_asset_files(project, saved["cleanup_asset"], catalog)
        return {**inspect_library(store, project, operation), "replayed": True}
    if str(catalog.get("storage_revision") or "empty") != expected:
        raise ProjectAssetConflictError("The library changed. Refresh and review before applying.")
    if len(catalog.get("library_receipts") or {}) >= MAX_COMMANDS:
        raise ValueError("The project reached its retained library-command limit; no receipts were discarded.")
    if action == "asset_update":
        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes or set(changes) - {"tag", "role", "enabled", "lyrics", "folder_id", "options"}:
            raise ValueError("Unsupported asset fields.")
    if action == "folder_update":
        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "color"}:
            raise ValueError("Unsupported folder fields.")
    pending = {"operation_id": operation, "project": catalog["project"],
        "action": action, "request_sha256": fingerprint, "before_revision": expected,
        "asset_ids_before": [item["id"] for item in catalog["assets"]],
        "folder_ids_before": [item["id"] for item in catalog["folders"]]}
    if action == "asset_delete":
        pending["cleanup_asset"] = next(({key: item.get(key) for key in ("id", "relative_path")} for item in catalog["assets"]
            if item["id"] == body.get("asset_id")), None)
    store._library_command = pending
    try:
        if action == "folder_create":
            store.create_folder(project, body.get("name"), color=body.get("color", ""))
        elif action == "folder_update":
            store.update_folder(project, body.get("folder_id"), body["changes"])
        elif action == "folder_delete":
            store.delete_folder(project, body.get("folder_id"))
        elif action == "folder_reorder":
            store.reorder_folders(project, body.get("folder_ids"))
        elif action == "asset_reorder":
            store.reorder(project, body.get("asset_ids"))
        elif action == "asset_update":
            store.update(project, body.get("asset_id"), body["changes"])
        elif action == "asset_duplicate":
            store.duplicate(project, body.get("asset_id"), tag=body.get("tag", ""),
                            folder_id=body.get("folder_id") if "folder_id" in body else None)
        else:
            store.delete(project, body.get("asset_id"))
    finally:
        store._library_command = None
    return {**inspect_library(store, project, operation), "replayed": False}
