"""Recover editable generation settings from assigned checkpoint metadata.

No media or project writes. Unsampled scenes and editorial notes stay authored;
only scenes explicitly being recovered take their generation values from disk.
"""
import copy
import json

if __package__:
    from .run_manager import archive_policy_inputs
else:
    from run_manager import archive_policy_inputs


def recover_authoring(authoring, metadata):
    result = copy.deepcopy(authoring)
    plan = json.loads(result["plan_json"])
    shots = plan["shots"]
    dimensions = {}
    for scene, item in sorted(metadata.items()):
        segment = item["segment"]
        if not 1 <= scene <= len(shots) or shots[scene - 1].get("id") != segment.get("id"):
            raise ValueError("Assigned scene %d does not match the saved Plan scene order. "
                             "Restore its matching Plan before recovering settings." % scene)
        shot = shots[scene - 1]
        # Explicit overrides prevent today's global policy from changing the
        # meaning of an older take. Absence clears stale overrides, too.
        for key in ("context_length", "audio_context_length", "continuation_mode",
                    "context_spatial_proxy", "source_reference", "generated_continuity",
                    "source_audio_target", "lora_route", "prompt_seed_mode", "prompt_seed",
                    "audio_context_unlocked", "visual_context_blocks",
                    "visual_context_source", "visual_context_start_frame",
                    "visual_context_lead_source", "visual_context_lead_frames",
                    "visual_context_lead_start_frame", "audio_context_source",
                    "audio_context_start_frame", "audio_context_lead_source",
                    "audio_context_lead_frames", "audio_context_lead_start_frame",
                    "video_blend_frames"):
            source = {"video_blend_frames": "blend_frames"}.get(key, key)
            if key.endswith("_source"):
                source += "_id"
            shot.pop(key, None)
            if source in segment:
                shot[key] = copy.deepcopy(segment[source])
        if isinstance(segment.get("visual_context_blocks"), list):
            shot["visual_context_blocks"] = [
                {"source": block["source_id"], "frames": block["frames"],
                 **({"start_frame": block["start_frame"]} if "start_frame" in block else {})}
                for block in segment["visual_context_blocks"]]
        # Strings preserve uint64 seeds through JSON and the browser.
        shot["seed"] = str(segment["seed"])
        if "prompt_seed" in shot:
            shot["prompt_seed"] = str(shot["prompt_seed"])
        shot["prompt"] = segment.get("scene_prompt_template", segment.get("scene_prompt", ""))
        shot["steps"] = int(segment["steps"])
        shot["length"] = int(segment["raw_frames"])
        shot.pop("frames", None)
        shot.pop("duration_seconds", None)
        compatibility = item.get("compatibility") or {}
        resolution = segment.get("resolution") or compatibility
        if resolution.get("width") and resolution.get("height"):
            dimensions[scene] = {key: int(resolution[key]) for key in ("width", "height")}

    # Resolution belongs to a chapter, not its label or the last opened tab.
    chapters = plan.get("chapters") or []
    starts = {shot["id"]: i + 1 for i, shot in enumerate(shots)}
    ordered = sorted(chapters, key=lambda chapter: starts[chapter["start_scene_id"]])
    ranges = [(chapter, starts[chapter["start_scene_id"]],
               starts[ordered[i + 1]["start_scene_id"]] - 1 if i + 1 < len(ordered) else len(shots))
              for i, chapter in enumerate(ordered)] or [(None, 1, len(shots))]
    for chapter, start, end in ranges:
        sizes = [size for scene, size in dimensions.items() if start <= scene <= end]
        if not sizes:
            continue
        if any(size != sizes[0] for size in sizes):
            raise ValueError("Assigned scenes in one chapter have different resolutions; "
                             "split that chapter before restoring its settings.")
        if chapter is not None:
            chapter["resolution"] = sizes[0]
        if start == 1:
            # Preserve the effective canvas of every untouched chapter before
            # updating the fallback used by the first chapter / legacy Plans.
            for other, other_start, other_end in ranges:
                if other is not None and other is not chapter and not any(
                        other_start <= scene <= other_end for scene in dimensions):
                    if "width" in result and "height" in result:
                        other.setdefault("resolution", {key: result[key] for key in ("width", "height")})
            result.update(sizes[0])
    if metadata:
        tip_metadata = metadata[max(metadata)]
        tip = tip_metadata["segment"]
        # Global fallbacks matter for continuation beyond the restored prefix.
        # Existing per-scene overrides above keep older takes exact.
        for key in ("context_length", "audio_context_length", "continuation_mode",
                    "encode_mode", "anchor_mode", "crop", "audio_mode",
                    "segment_crf", "video_blend_frames"):
            if key in (tip_metadata.get("compatibility") or {}):
                result[key] = tip_metadata["compatibility"][key]
        result["policy_inputs"] = archive_policy_inputs(tip_metadata)
        if "prompt_prefix" in tip:
            key = "global_prompt" if "global_prompt" in plan and "prompt_prefix" not in plan else "prompt_prefix"
            plan[key] = tip["prompt_prefix"]
    result["plan_json"] = json.dumps(plan, ensure_ascii=False, indent=2)
    return result
