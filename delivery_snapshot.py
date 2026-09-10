"""Read-only, exact saved-source snapshots for native H3 delivery.

Snapshots are portable descriptions, not authorization tokens. Execution still
verifies the immutable artifacts and uses H3's normal assembly implementation.
"""
import copy
import hashlib
import json
import math

from .branch_scope import branch_id, branch_scope

FORMAT = "h3_delivery_snapshot_v1"
MAX_BYTES = 16 * 1024 * 1024


class DeliveryConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _timing(chain, manifest):
    editorial = chain._manifest_editorial(manifest)
    _editorial, _records, frames = chain._editorial_timeline_records(
        manifest["run_name"], manifest["segments"], editorial)
    origin = int((manifest.get("chapter") or {}).get("editorial_origin_frame", 0))
    return editorial, frames, origin


def presentation_segments(chain, manifest):
    """Use the captured picture descriptors, never reread a mutable ALT sidecar."""
    pictures = manifest.get("delivery_pictures")
    if pictures is None:
        return None
    bases = manifest.get("segments", [])
    if not isinstance(pictures, list) or len(pictures) != len(bases):
        raise ValueError("Frozen delivery has an invalid picture selection.")
    for base, picture in zip(bases, pictures):
        if not isinstance(picture, dict) or any(picture.get(key) != base.get(key)
                for key in ("index", "id", "raw_frames", "delivered_frames")):
            raise ValueError("Frozen delivery pictures change scene identity or timing.")
        if picture.get("revision") != base.get("revision") and (
                picture.get("presentation_base_revision") != base.get("revision")
                or picture.get("presentation_alternate_revision") != picture.get("revision")
                or picture.get("presentation_media_mode") != "picture_only"):
            raise ValueError("Frozen delivery contains an unrelated picture alternate.")
        chain._verify_segment_artifacts(picture, int(base["index"]))
    return copy.deepcopy(pictures)


def subtitle_cues(manifest, frames, origin):
    """Return frozen pre-prelude cues, or None for ordinary live editorial."""
    record = manifest.get("delivery_subtitles")
    if record is None:
        return None
    if (not isinstance(record, dict) or record.get("version") != 1
            or record.get("frame_count") != frames
            or record.get("timeline_origin_frame") != origin
            or not isinstance(record.get("cues"), list)):
        raise ValueError("Frozen delivery subtitle timing no longer matches the manifest.")
    for cue in record["cues"]:
        if not isinstance(cue, dict) or not isinstance(cue.get("text"), str):
            raise ValueError("Frozen delivery contains invalid subtitle text.")
        start, end = cue.get("start"), cue.get("end")
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or end > frames / 24.0 + 1e-9):
            raise ValueError("Frozen delivery contains invalid subtitle timing.")
    return copy.deepcopy(record["cues"])


def prepare(chain, body):
    if not isinstance(body, dict) or not isinstance(body.get("selection"), dict):
        raise ValueError("Delivery preparation requires a saved checkpoint selection.")
    selection = copy.deepcopy(body["selection"])
    run = chain._strict_run_name(selection.get("run_name"))
    selected = branch_id(selection.get("_branch_id", "main"))
    if selection.get("output_mode") != "workflow_local":
        raise ValueError("Delivery preparation requires an explicit workflow-local checkpoint pin.")
    if selection.get("processing_source") is not None:
        raise ValueError("Processed sources need their own immutable delivery snapshot adapter.")
    cut = selection.get("final_cut_branch_id")
    if not isinstance(cut, str) or cut == "auto":
        raise ValueError("Choose an explicit final-cut branch for delivery.")
    branch_id(cut)
    revision = body.get("editorial_revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("Refresh the final cut before preparing a delivery snapshot.")
    with branch_scope(run, selected):
        manifest = chain._checkpoint_selection_manifest(selection, freeze_editorial=True)
        source = manifest.get("final_cut_source") or {}
        if source.get("branch_id") != cut or source.get("editorial_revision") != revision:
            raise DeliveryConflict("The final cut changed. Refresh it and review delivery again.")
        editorial, frames, origin = _timing(chain, manifest)
        # Validate chosen alternate media during preparation as well as execution.
        pictures = chain._editorial_presentation_segments(run, manifest["segments"], editorial)
        manifest["delivery_pictures"] = copy.deepcopy(pictures)
        catalog = (chain.ProjectAssetStore(chain._input_root(), chain._output_root())
                   .load(run, repair=False)
                   if editorial.get("subtitles", {}).get("mode") == "preview_srt" else {})
        cues = chain._editorial_subtitle_cues(run, editorial, frames,
                                            timeline_origin_frames=origin, catalog=catalog)
        manifest["delivery_subtitles"] = {"version": 1, "frame_count": frames,
                                          "timeline_origin_frame": origin, "cues": cues}
        prelude = chain._validate_prelude(manifest)
    value = {"format": FORMAT, "version": 1, "run_name": run,
             "branch_id": selected, "selection": selection, "manifest": manifest}
    value["id"] = _digest(value)
    serialized = _json(value)
    if len(serialized.encode("utf-8")) > MAX_BYTES:
        raise ValueError("This delivery snapshot exceeds the supported size.")
    return {"version": 1, "snapshot_id": value["id"], "snapshot_json": serialized,
            "summary": {"run_name": run, "branch_id": selected,
                        "final_cut_branch_id": cut, "editorial_revision": revision,
                        "scene_start": manifest["segments"][0]["index"],
                        "scene_end": manifest["segments"][-1]["index"],
                        "frames": frames + (int(prelude["frame_count"]) if prelude else 0),
                        "fps": chain.FPS, "subtitle_count": len(cues),
                        "pictures": [{"scene": item["index"],
                                      "revision": str(item.get("revision") or ""),
                                      "scene_id": str(item.get("id") or "")}
                                     for item in pictures]}}


def load(chain, serialized):
    if not isinstance(serialized, str) or len(serialized.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Invalid delivery snapshot size or type.")
    value = json.loads(serialized)
    if not isinstance(value, dict) or value.get("format") != FORMAT or value.get("version") != 1:
        raise ValueError("Unsupported delivery snapshot. Prepare it again in SceneWeaver.")
    saved_id = value.pop("id", None)
    if not isinstance(saved_id, str) or saved_id != _digest(value):
        raise ValueError("The delivery snapshot was changed or damaged. Prepare it again.")
    run = chain._strict_run_name(value.get("run_name"))
    selected = branch_id(value.get("branch_id"))
    manifest = value.get("manifest")
    if (not isinstance(manifest, dict) or manifest.get("run_name") != run
            or manifest.get("_branch_id", "main") != selected
            or "editorial" not in manifest or "delivery_subtitles" not in manifest
            or "delivery_pictures" not in manifest):
        raise ValueError("The delivery snapshot has inconsistent source identity.")
    with branch_scope(run, selected):
        # Verify that the destination branch still exists; never fall back to Original.
        chain.WorkingBranches(chain._output_root(), run)._load_record(selected)
        segments = chain._validate_manifest(manifest)
        editorial, frames, origin = _timing(chain, manifest)
        presentation_segments(chain, manifest)
        chain._validate_prelude(manifest)
        subtitle_cues(manifest, frames, origin)
    manifest["delivery_snapshot_id"] = saved_id
    manifest["_delivery_snapshot_json"] = serialized
    return manifest
