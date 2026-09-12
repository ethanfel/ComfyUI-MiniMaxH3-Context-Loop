"""Delete only scene frames explicitly associated with a processed take.

Export indexes remain as tombstones/fragments: surviving frames retain their
numbers, and a later export forks instead of resurrecting deleted scenes.
"""

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import re

from .png_export_ownership import FORMAT as CATALOG_FORMAT
from .png_video_export import FORMAT, _folder_lock


@contextmanager
def locked_exports(manager, run):
    directories = set()
    catalog = manager._path("h3_chains/%s/png_exports.json" % run)
    if catalog.exists():
        value = manager._read(catalog)
        if (value.get("format") != CATALOG_FORMAT or value.get("run_name") != run
                or not isinstance(value.get("directories"), list)):
            raise ValueError("Invalid PNG export catalog; preview deletion after repairing it.")
        directories.update(manager._path(item) for item in value["directories"])
    # Older default exports predate the catalog. Never recurse into arbitrary
    # output folders or infer ownership merely from their folder names.
    run_dir = manager._path("h3_chains/" + run)
    for pattern in ("upscaled/*/frames/*/export.json",
                    "chapters/*/upscaled/*/frames/*/export.json"):
        for path in run_dir.glob(pattern):
            directories.add(manager._path(manager._address(path)).parent)
    with ExitStack() as stack:
        records = {}
        for directory in sorted(directories):
            if not directory.is_dir():
                continue
            stack.enter_context(_folder_lock(manager.root, directory))
            path = manager._path(manager._address(directory / "export.json"))
            if path.is_file():
                record = manager._read(path)
                if record.get("format") == FORMAT and record.get("settings", {}).get("run_name") == run:
                    records[path] = record
        yield records


def plan(manager, metadata, docs, exports):
    owner = metadata["segment"].get("png_export_owner")
    owned, updates, kept = {}, {}, []
    if not owner:
        return owned, updates, ["Legacy PNG exports without exact take ownership (kept)"]
    if not re.fullmatch(r"[0-9a-f]{64}", str(owner)):
        raise ValueError("Invalid PNG take ownership.")
    revision = metadata["segment"]["revision"]
    # A retry may save two immutable takes for the same scene/pass. Neither
    # deletion may remove the PNGs while the other take survives.
    shared_take = any(value.get("segment", {}).get("png_export_owner") == owner
                      and value["segment"].get("revision") != revision for value in docs.values())
    for path, record in exports.items():
        clips = record.get("clips")
        if (not isinstance(clips, list) or any(not isinstance(clip, dict) for clip in clips)
                or any(type(clip.get("index")) is not int for clip in clips)
                or len({clip["index"] for clip in clips}) != len(clips)):
            raise ValueError("Invalid PNG scene index: %s" % manager._address(path))
        for clip in clips:
            owners = clip.get("processing_owners", [])
            if (not isinstance(owners, list)
                    or any(not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) for key in owners)):
                raise ValueError("Invalid PNG processing ownership.")
        matching = [clip for clip in clips if owner in clip.get("processing_owners", [])]
        if not matching:
            continue
        if (path.parent / ".png_pending.json").exists():
            raise ValueError("PNG publication is pending; finish/recover this export before deleting its take.")
        next_record = deepcopy(record)
        removed = []
        changed = False
        for clip in matching:
            if (shared_take or clip.get("legacy_unattributed")
                    or any(key != owner for key in clip["processing_owners"])):
                kept.append("Shared PNG scene %s in %s (another owner retained)" % (
                    clip["index"], manager._address(path.parent)))
                if not shared_take:
                    retained = next(item for item in next_record["clips"] if item["index"] == clip["index"])
                    retained["processing_owners"] = [key for key in clip["processing_owners"] if key != owner]
                    changed = True
                continue
            if (clip.get("index") != metadata["segment"]["index"]
                    or clip.get("source_contract") != metadata.get("source_scene_contract")):
                raise ValueError("PNG ownership disagrees with the selected take.")
            files = clip.get("files")
            first = clip.get("first_frame_number")
            if (not isinstance(files, list) or not files or type(first) is not int
                    or len(files) != clip.get("delivered_frames")):
                raise ValueError("Invalid owned PNG frame range.")
            for offset, item in enumerate(files):
                if item.get("file") != "frame_%08d.png" % (first + offset):
                    raise ValueError("Invalid owned PNG frame address.")
                frame = manager._path(manager._address(path.parent / item["file"]))
                owned[frame] = "PNG frame for deleted upscale (including edited pixels)"
            removed.append(clip["index"])
        if not changed and not removed:
            continue
        if removed:
            next_record["clips"] = [clip for clip in next_record["clips"] if clip["index"] not in removed]
            next_record.update(complete=False,
                               frame_count=sum(clip["delivered_frames"] for clip in next_record["clips"]),
                               last_scene=next_record["clips"][-1]["index"] if next_record["clips"] else None,
                               deleted_scenes=sorted(set(record.get("deleted_scenes", [])) | set(removed)))
        updates[path] = next_record
        marker = manager._path(manager._address(path.parent / ".png_variant.json"))
        if marker.exists():
            # Its prefix is only a copy/recovery recipe, not a surviving take.
            # Once a scene is explicitly deleted, never replay that snapshot.
            value = manager._read(marker)
            value["prefix"] = None
            updates[marker] = value
    return owned, updates, kept
