"""Native image geometry and staged, conditional variant publication."""
from __future__ import annotations

import copy
import os
import shutil
from contextlib import nullcontext

if __package__:
    from . import project_assets as native
    from .asset_copy import _hash, _stamp, command_staged
else:
    import project_assets as native
    from asset_copy import _hash, _stamp, command_staged

FIELDS = ("command_version", "project", "operation_id", "action", "base_revision",
          "asset_id", "crop", "target", "resample", "tag", "folder_id", "preview_revision")
RESAMPLING = ("lanczos", "bicubic", "bilinear", "nearest", "box", "hamming")


def _source(store, project, asset_id):
    entry, path = store.asset(project, asset_id)
    if entry["kind"] != "image":
        raise ValueError("Choose a picture asset to create an image variant.")
    if native.Image is None or native.ImageOps is None:
        raise ValueError("Pillow is required for image variants.")
    with native.Image.open(path) as opened:
        image = native.ImageOps.exif_transpose(opened)
        width, height = image.size
    return entry, path, width, height


def inspect_image(store, project, asset_id, edit=None):
    catalog = store.load(project)
    entry, path, width, height = _source(store, project, asset_id)
    value = {"project": catalog["project"], "asset_id": asset_id, "base_revision": _stamp(catalog),
        "asset": entry, "source": {"width": width, "height": height},
        "resampling": list(RESAMPLING), "max_pixels": native.MAX_DERIVED_IMAGE_PIXELS}
    if edit is None:
        return value
    if not isinstance(edit, dict):
        raise ValueError("Image edit must be a JSON object.")
    crop, target = edit.get("crop"), edit.get("target")
    for fields, keys in ((crop, ("x", "y", "width", "height")), (target, ("width", "height"))):
        if not isinstance(fields, dict) or any(type(fields.get(key)) is not int for key in keys):
            raise ValueError("Crop and target dimensions must be whole pixels.")
    # Geometry validation uses the same oriented image bounds as native derive.
    bounds = type("ImageBounds", (), {"width": width, "height": height})()
    geometry = native._image_operation_geometry(bounds, crop, target)
    resample, _ = native._image_resampling(edit.get("resample"))
    folder = str(edit.get("folder_id") or "")
    if folder and folder not in {item["id"] for item in catalog["folders"]}:
        raise ValueError("The destination folder no longer exists.")
    tag = native._safe_tag(edit.get("tag"), entry["tag"] + "_variant")
    stat = os.stat(path)
    value.update({"crop": dict(zip(("x", "y", "width", "height"), geometry[:4])),
        "target": {"width": geometry[4], "height": geometry[5]}, "resample": resample,
        "tag": tag, "folder_id": folder, "copyable": len(catalog["assets"]) < native.MAX_CATALOG_ASSETS,
        "issue": "The asset catalog is full." if len(catalog["assets"]) >= native.MAX_CATALOG_ASSETS else ""})
    value["preview_revision"] = _hash([value, entry.get("sha256"), stat.st_size, stat.st_mtime_ns])
    return value


def _snapshot(store, project, request):
    preview = inspect_image(store, project, request["asset_id"], request)
    catalog = store.load(project)
    if _stamp(catalog) != preview["base_revision"]:
        raise native.ProjectAssetConflictError("The library changed during image review.")
    return preview, catalog, preview["asset"]


def _stage(store, directory, request, catalog, parent, preview):
    scratch = os.path.join(directory, "stage")
    shutil.rmtree(scratch, ignore_errors=True)
    project, asset_id = catalog["project"], request["asset_id"]
    source_path = store._asset_path(project, parent)
    source_digest = native._file_sha256(source_path)
    if parent.get("sha256") and source_digest != parent["sha256"]:
        raise native.ProjectAssetConflictError("The source image no longer matches its reviewed bytes.")
    os.makedirs(scratch, exist_ok=True)
    frozen_source = os.path.join(scratch, "source" + os.path.splitext(source_path)[1])
    shutil.copy2(source_path, frozen_source)
    if native._file_sha256(frozen_source) != source_digest:
        raise native.ProjectAssetConflictError("The source image changed while staging its bytes.")

    class StagedStore(native.ProjectAssetStore):
        def asset(self, selected_project, selected_id):
            if selected_project == project and selected_id == asset_id:
                return copy.deepcopy(parent), frozen_source
            return super().asset(selected_project, selected_id)

    staged = StagedStore(os.path.join(scratch, "input"), os.path.join(scratch, "output"))
    staged_dir, _ = staged._project_dir(project)
    native._atomic_json(os.path.join(staged_dir, "catalog.json"), catalog)
    result = staged.derive_image(project, asset_id, **{key: request[key] for key in
        ("crop", "target", "resample", "tag", "folder_id", "operation_id")})
    if result.get("reused"):
        raise ValueError("This image operation ID already belongs to a native variant.")
    entry = result["asset"]
    relative = entry["relative_path"]
    entry["relative_path"] = "images/%s_%s" % (request["operation_id"], os.path.basename(relative))
    entry["input_path"] = "h3_projects/%s/%s" % (project, entry["relative_path"])
    if parent.get("source_origin"):
        entry["source_origin"] = copy.deepcopy(parent["source_origin"])
    if native._file_sha256(source_path) != source_digest or inspect_image(store, project, asset_id, request)["preview_revision"] != request["preview_revision"]:
        raise native.ProjectAssetConflictError("Source image or library changed while deriving the variant.")
    return {"request": request, "phase": "prepared", "assets": [entry], "files": [{
        "staged": os.path.relpath(os.path.join(staged_dir, relative), directory),
        "relative_path": entry["relative_path"], "sha256": entry["sha256"]}]}


def command_image(store, project, body, *, commit_guard=nullcontext):
    return command_staged(store, project, body, "asset_derive", FIELDS, _snapshot, _stage, commit_guard=commit_guard)
