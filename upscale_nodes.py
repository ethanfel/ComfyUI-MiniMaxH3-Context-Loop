"""Deferred, checkpoint-backed upscale loops for MiniMax H3 chains.

The source generation remains immutable.  A completed active checkpoint branch
is adapted into a second recursive graph whose body may contain any upscaler
(native H3 latent refinement, LTX video-to-video, or a custom image pipeline).
Each delivered HQ scene is persisted below the source run's ``upscaled``
folder before the loop advances, and the shared chain assembler reuses the
original audio contract without requiring the source video segments. A chain-aware
MAINodes de-rope route may instead supply exact-recovered RAW-clock audio to
the same scene save contract.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from . import chain_nodes as chain

try:
    from comfy.nested_tensor import NestedTensor as _ComfyNestedTensor
except ImportError:
    _ComfyNestedTensor = None


UPSCALE_FLOW_TYPE = "H3_CHAIN_UPSCALE_FLOW"
UPSCALE_STATE_TYPE = "H3_CHAIN_UPSCALE_STATE"
UPSCALE_SEGMENT_TYPE = "H3_CHAIN_UPSCALE_SEGMENT"
# Upscale and source runs deliberately share one public manifest wire.  The
# document's ``format`` selects the assembly behavior at runtime, which lets
# the normal H3 Chain Assemble node finish either kind of run.
UPSCALE_MANIFEST_TYPE = chain.MANIFEST_TYPE


class _AnyStateType(str):
    """Expose a comma union while accepting either state server-side."""

    def __ne__(self, other):  # pragma: no cover - Python protocol shim
        return False

    def __eq__(self, other):  # pragma: no cover - Python protocol shim
        return True

    def __hash__(self):  # pragma: no cover - retain string hashability
        return str.__hash__(self)


DEROPE_STATE_TYPE = _AnyStateType(
    "H3_CHAIN_UPSCALE_STATE,H3_CHAIN_STATE")

UPSCALE_BACKENDS = ("h3_latent", "ltx_2_5", "custom", "pixel")
CONDITIONING_SYNC_METHODS = (
    "nearest", "nearest-exact", "bilinear", "bicubic")
MOTION_REFERENCE_MODES = (
    "exclude_video_keep_audio", "keep_video_native", "resize_video")
CONDITIONING_SYNC_MOTION_MODES = (
    "conditioning_policy",) + MOTION_REFERENCE_MODES


def _profile_dir(run_name: str, profile: str, source_manifest=None) -> str:
    run = chain._safe_name(run_name, "h3_chain")
    name = chain._safe_name(profile, "upscale")
    parent = os.path.join(chain._output_root(), "h3_chains", run)
    if isinstance(source_manifest, dict) and source_manifest.get("chapter"):
        parent = chain._chapter_delivery_root({**source_manifest, "run_name": run})
    path = os.path.abspath(os.path.join(parent, "upscaled", name))
    root = os.path.abspath(chain._output_root())
    if os.path.commonpath([root, path]) != root:
        raise ValueError("H3 upscale profile path escapes the output directory.")
    return path


def _profile_paths(run_name: str, profile: str, index: int,
                   source_manifest=None) -> dict[str, str]:
    root = _profile_dir(run_name, profile, source_manifest)
    stem = "clip_%04d" % int(index)
    return {
        "root": root,
        "segment": os.path.join(root, "segments", stem + ".mp4"),
        "checkpoint": os.path.join(root, "checkpoints", stem + ".safetensors"),
        "metadata": os.path.join(root, "checkpoints", stem + ".json"),
        "prompt": os.path.join(root, "prompts", stem + ".txt"),
        "audio": os.path.join(root, "audio", stem + ".wav"),
        "manifest": os.path.join(root, "upscale_manifest.json"),
        "partial": os.path.join(
            root, "partial", "through_clip_%04d.manifest.json" % int(index)),
        "final": os.path.join(root, "final"),
    }


def _parse_recipe(value: str) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Upscale recipe_json must contain valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Upscale recipe_json must contain a JSON object.")
    return parsed


def _profile_config(backend: str, recipe_json: str, save_latent: bool,
                    segment_crf: int) -> dict[str, Any]:
    if backend not in UPSCALE_BACKENDS:
        raise ValueError("Unknown H3 upscale backend %r." % backend)
    recipe = _parse_recipe(recipe_json)
    value = {
        "backend": backend,
        "recipe": recipe,
        "save_latent": bool(save_latent),
        "segment_crf": int(segment_crf),
    }
    value["config_hash"] = chain._fingerprint(value)
    return value


def _verified_source_manifest(value: dict[str, Any]) -> dict[str, Any]:
    manifest = chain._json_document(value)
    if not isinstance(manifest, dict):
        raise ValueError(
            "Checkpoint Upscale Adapter requires a selected lineage manifest "
            "from Checkpoint Manager.")
    from .deferred_checkpoint_source import editorial_source_manifest
    manifest = editorial_source_manifest(manifest, chain)
    segments = chain._validate_manifest(manifest)
    if not manifest.get("processing_source"):
        chain.common_saved_resolution(segments, "Deferred upscale source")
    else:
        for segment in segments:
            if segment.get("processing_source"):
                _validate_processed_latent_header(segment)
    if isinstance(manifest.get("prelude"), dict):
        raise ValueError(
            "Deferred upscale does not yet support an existing-video prelude. "
            "Upscale the prelude separately or use a chain without prepend_original.")
    return manifest


def _source_hash(manifest: dict[str, Any]) -> str:
    return chain._fingerprint(manifest)


def _source_geometry(source, compatibility):
    if source.get("processing_source"):
        return {key: int(source[key]) for key in ("width", "height")}
    return chain.saved_resolution(source) or compatibility


def _source_bounds(manifest: dict[str, Any]) -> tuple[int, int]:
    return chain._manifest_segment_bounds(
        manifest, manifest.get("segments") or [], "Deferred upscale source")


def _source_scene_count(manifest: dict[str, Any]) -> int:
    # Reference schedules/cache slots use the original Plan's scene numbers,
    # not a chapter-relative index. Existing full-run behavior stays unchanged.
    if manifest.get("chapter"):
        return max(_source_bounds(manifest)[1],
                   int(manifest.get("source_scene_count") or
                       manifest["chapter"].get("planned_end_scene") or 0))
    return len(manifest["segments"])


def _state_profile_paths(state: dict[str, Any], index: int) -> dict[str, str]:
    return _profile_paths(state["run_name"], state["profile"], index,
                          state["source_manifest"])


def _public_upscale_segment(value: dict[str, Any]) -> dict[str, Any]:
    private = {"_h3_upscale_decision"}
    return {
        key: item for key, item in value.items()
        if not key.startswith("_") and key not in private
    }


def _source_segment(state: dict[str, Any], index: int | None = None
                    ) -> dict[str, Any]:
    slot = int(state["index"] if index is None else index)
    segments = state["source_manifest"].get("segments") or []
    first, last = _source_bounds(state["source_manifest"])
    if slot < first or slot > last:
        raise ValueError("Upscale scene must be between %d and %d." % (first, last))
    source = segments[slot - first]
    if int(source.get("index", -1)) != slot:
        raise ValueError("Source manifest scene indexes are not contiguous.")
    return source


def _derope_state_view(state: Any
                       ) -> tuple[dict[str, Any], int, int, dict[str, Any], str]:
    """Normalize live-chain and deferred-upscale scene state."""
    if not isinstance(state, dict):
        raise ValueError(
            "H3 De-Rope nodes require Current Shot state or Checkpoint "
            "Upscale Loop state; got %s." % type(state).__name__)
    source_manifest = state.get("source_manifest")
    if isinstance(source_manifest, dict):
        index = int(state.get("index", 0))
        scene_count = _source_bounds(source_manifest)[1]
        segment = _source_segment(state, index)
        compatibility = source_manifest.get("compatibility") or {}
        return segment, index, scene_count, compatibility, "upscale"
    plan = state.get("plan")
    if isinstance(plan, dict):
        shots = plan.get("shots") or []
        scene_count = len(shots)
        index = int(state.get("index", 0))
        if index < 1 or index > scene_count:
            raise ValueError(
                "H3 de-rope chain scene must be between 1 and %d." %
                scene_count)
        shot = shots[index - 1]
        if not isinstance(shot, dict):
            raise ValueError("H3 de-rope chain scene is not a Plan shot.")
        compatibility = plan.get("compatibility") or {}
        return shot, index, scene_count, compatibility, "chain"
    raise ValueError(
        "H3 De-Rope nodes did not recognize the connected state. Wire "
        "Current Shot or Checkpoint Upscale Loop.")


def _derope_continuation_mode(segment: dict[str, Any],
                              compatibility: dict[str, Any]) -> str:
    return str(chain.migrate_continuation_mode(
        segment.get("continuation_mode") or
        compatibility.get("continuation_mode") or "guide"))


def _derope_prefix_steps_from_view(segment: dict[str, Any],
                                   compatibility: dict[str, Any],
                                   scene_number: int) -> int:
    """Return the exact H3 latent prefix span for a Drift-Control scene."""
    mode = _derope_continuation_mode(segment, compatibility)
    if mode not in chain.DRIFT_CONTROL_CONTINUATION_MODES:
        return 0
    trim = int(segment.get("raw_frames", 0)) - int(
        segment.get("delivered_frames", 0))
    if trim <= 0:
        return 0
    expected_frames = int(chain.DRIFT_CONTROL_AV_RECIPE["context_frames"])
    if trim != expected_frames:
        raise ValueError(
            "H3 de-rope Drift-Control scene %d has a %d-frame prefix; "
            "recipe %s requires %d." % (
                scene_number, trim,
                chain.DRIFT_CONTROL_AV_RECIPE["version"], expected_frames))
    return int(chain.DRIFT_CONTROL_AV_RECIPE["video_steps"])


def _derope_future_visual_consumers(state: dict[str, Any],
                                    scene: int) -> list[int]:
    """Return later scenes whose saved visual prefix depends on ``scene``."""
    segment, _index, scene_count, compatibility, kind = _derope_state_view(
        state)
    del segment
    consumers = []
    if kind == "chain":
        plan = state["plan"]
        default_context = int(compatibility.get("context_length", 0))
        for target in range(int(scene) + 1, scene_count + 1):
            shot = plan["shots"][target - 1]
            if chain._shot_context_length(shot, default_context) <= 0:
                continue
            if "visual_context_blocks" in shot:
                sources = {int(block["source"]) for block in
                           chain._shot_visual_context_blocks(
                               plan, target,
                               chain._shot_context_length(
                                   shot, default_context))}
            else:
                sources = {
                    chain._shot_visual_context_source(plan, target),
                    chain._shot_visual_context_lead_source(plan, target),
                }
            if int(scene) in sources:
                consumers.append(target)
        return consumers
    for target in range(int(scene) + 1, scene_count + 1):
        candidate = _source_segment(state, target)
        if not isinstance(candidate, dict):
            continue
        trim = int(candidate.get("raw_frames", 0)) - int(
            candidate.get("delivered_frames", 0))
        if trim <= 0:
            continue
        visual_blocks = candidate.get("visual_context_blocks")
        explicit = ({block.get("source_scene") for block in visual_blocks
                     if isinstance(block, dict)}
                    if isinstance(visual_blocks, list) else {
                        candidate.get("visual_context_source_scene"),
                        candidate.get("visual_context_lead_source_scene"),
                    })
        sources = set()
        for value in explicit:
            try:
                if value is not None:
                    sources.add(int(value))
            except (TypeError, ValueError):
                continue
        if not sources:
            sources.add(target - 1)
        if int(scene) in sources:
            consumers.append(target)
    return consumers


def _derope_visual_state(state: dict[str, Any], kind: str,
                         scene: int, compatibility: dict[str, Any]
                         ) -> dict[str, Any]:
    """Resolve the same visual source that Chain Context conditioned on."""
    if kind != "chain" or int(scene) <= 1:
        return state
    shot = state["plan"]["shots"][int(scene) - 1]
    context_length = chain._shot_context_length(
        shot, int(compatibility.get("context_length", 0)))
    if context_length <= 0:
        return state
    return chain._visual_context_state(state)


def _derope_hold_map(value: Any, expected_frames: int,
                     label: str) -> tuple[dict[str, Any], list[int]]:
    """Parse one MAINodes hold map without importing the optional pack."""
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("%s must contain valid JSON." % label) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("holds"), list):
        raise ValueError("%s must contain a JSON object with a holds list." % label)
    holds = []
    for index, item in enumerate(parsed["holds"]):
        if isinstance(item, bool):
            raise ValueError("%s hold %d is not a positive integer." % (
                label, index))
        try:
            hold = int(item)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("%s hold %d is not a positive integer." % (
                label, index)) from exc
        if hold < 1 or float(item) != float(hold):
            raise ValueError("%s hold %d is not a positive integer." % (
                label, index))
        holds.append(hold)
    expected = int(expected_frames)
    if len(holds) != expected:
        raise ValueError(
            "%s covers %d world frames; source scene requires %d RAW "
            "frames." % (label, len(holds), expected))
    if "world_len" in parsed and int(parsed["world_len"]) != expected:
        raise ValueError(
            "%s says world_len=%s; source scene requires %d RAW frames." %
            (label, parsed["world_len"], expected))
    return dict(parsed), holds


def _source_continuation_mode(state: dict[str, Any],
                              index: int | None = None) -> str:
    source = _source_segment(state, index)
    compatibility = state["source_manifest"].get("compatibility") or {}
    return str(source.get("continuation_mode") or
               compatibility.get("continuation_mode") or "guide")


def _drift_prefix_steps(state: dict[str, Any],
                        index: int | None = None) -> int:
    """Return the exact H3 latent prefix span for a Drift-Control scene."""
    source = _source_segment(state, index)
    mode = _source_continuation_mode(state, index)
    if mode not in chain.DRIFT_CONTROL_CONTINUATION_MODES:
        return 0
    trim = int(source.get("raw_frames", 0)) - int(
        source.get("delivered_frames", 0))
    if trim <= 0:
        return 0
    expected_frames = int(chain.DRIFT_CONTROL_AV_RECIPE["context_frames"])
    if trim != expected_frames:
        raise ValueError(
            "H3 upscale Drift-Control scene %d has a %d-frame prefix; "
            "recipe %s requires %d." % (
                int(source.get("index", index or 0)), trim,
                chain.DRIFT_CONTROL_AV_RECIPE["version"], expected_frames))
    return int(chain.DRIFT_CONTROL_AV_RECIPE["video_steps"])


def _video_stream_from_latent(latent: Any, label: str) -> Any:
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("%s must be a LATENT with samples." % label)
    samples = latent["samples"]
    streams = ([samples]
               if chain.torch is not None and chain.torch.is_tensor(samples)
               else chain._streams_from_latent(latent))
    for stream in streams:
        if (getattr(stream, "ndim", 0) == 5
                and int(stream.shape[1]) == 24):
            return stream
    raise ValueError("%s has no [B,24,T,H,W] H3 video stream." % label)


def _drift_continuation_video(video: Any, state: Any
                              ) -> tuple[Any, int, str]:
    """Splice the prior HQ tail into a Drift-Control pass-2 prefix."""
    if not isinstance(state, dict):
        return video, 0, "open video"
    prefix_steps = _drift_prefix_steps(state)
    if prefix_steps <= 0:
        return video, 0, "open video"
    if int(video.shape[2]) <= prefix_steps:
        raise ValueError(
            "H3 upscale Drift-Control prefix uses %d/%d latent steps; no "
            "future remains to refine." % (prefix_steps, int(video.shape[2])))
    previous = state.get("previous_latent")
    if previous is None:
        return video, prefix_steps, (
            "source prefix protected (prior HQ context unavailable)")
    prior = _video_stream_from_latent(previous, "Previous upscaled latent")
    if int(prior.shape[2]) < prefix_steps:
        raise ValueError(
            "Previous upscaled latent has %d video steps; Drift-Control needs "
            "%d." % (int(prior.shape[2]), prefix_steps))
    if (int(prior.shape[0]) != int(video.shape[0]) or
            tuple(prior.shape[-2:]) != tuple(video.shape[-2:])):
        raise ValueError(
            "Previous/current HQ video geometry differs: %s vs %s." % (
                tuple(prior.shape), tuple(video.shape)))
    output = video.clone()
    output[:, :, :prefix_steps] = prior[:, :, -prefix_steps:].to(
        device=video.device, dtype=video.dtype)
    return output, prefix_steps, "previous HQ latent tail spliced and protected"


def _derope_drift_continuation_video(video: Any, state: Any
                                     ) -> tuple[Any, int, str]:
    """Splice the resolved prior visual tail for either loop state."""
    segment, index, _count, compatibility, kind = _derope_state_view(state)
    prefix_steps = _derope_prefix_steps_from_view(
        segment, compatibility, index)
    if prefix_steps <= 0:
        return video, 0, "open video"
    if int(video.shape[2]) <= prefix_steps:
        raise ValueError(
            "H3 de-rope Drift-Control prefix uses %d/%d latent steps; no "
            "future remains to refine." % (
                prefix_steps, int(video.shape[2])))
    visual_state = _derope_visual_state(
        state, kind, index, compatibility)
    previous = visual_state.get("previous_latent")
    if previous is None:
        return video, prefix_steps, (
            "source prefix protected (prior HQ context unavailable)")
    prior = _video_stream_from_latent(previous, "Resolved prior HQ latent")
    if int(prior.shape[2]) < prefix_steps:
        raise ValueError(
            "Resolved prior HQ latent has %d video steps; Drift-Control "
            "needs %d." % (int(prior.shape[2]), prefix_steps))
    if (int(prior.shape[0]) != int(video.shape[0]) or
            tuple(prior.shape[-2:]) != tuple(video.shape[-2:])):
        raise ValueError(
            "Prior/current HQ video geometry differs: %s vs %s." % (
                tuple(prior.shape), tuple(video.shape)))
    output = video.clone()
    output[:, :, :prefix_steps] = prior[:, :, -prefix_steps:].to(
        device=video.device, dtype=video.dtype)
    route = ("resolved chain visual context spliced and protected"
             if kind == "chain" else
             "previous HQ latent tail spliced and protected")
    return output, prefix_steps, route


def _validate_recovered_audio_shape(shape, batch, raw):
    if (len(shape) != 4 or list(shape[:3]) != [batch, 32, 2] or shape[3] < 1 or
            abs(shape[3] - raw / float(chain.FPS) * 40) > 2):
        # Audio VAE padding can leave a sub-frame tail at its 40 Hz clock.
        raise ValueError("Recovered DeRoPE audio latent must be [B,32,2,T] on the original RAW clock.")


def _validate_processed_latent_header(source):
    """Validate full recovered RAW latents without allocating their tensors."""
    from safetensors import safe_open
    raw = chain._validate_h3_length(source["raw_frames"], "DeRoPE RAW length")
    expected = [24, (raw - 5) // 17 * 5 + 2,
                int(source["height"]) // 16, int(source["width"]) // 16]
    if any(int(source[key]) < 16 or int(source[key]) % 16 for key in ("width", "height")):
        raise ValueError("DeRoPE source canvas must be aligned to the H3 VAE.")
    with safe_open(chain._absolute_output_path(source["checkpoint"]),
                   framework="pt", device="cpu") as saved:
        keys = set(saved.keys())
        layout = source.get("latent_layout")
        video_key = "upscaled_video" if layout == "joint_av" else "upscaled_samples"
        if (source.get("latent_saved") is not True or layout not in ("joint_av", "single") or
                video_key not in keys):
            raise ValueError("DeRoPE scene %s has no supported full recovered latent." % source["index"])
        shape = saved.get_slice(video_key).get_shape()
        if layout == "single" and shape == expected:
            # Older single-stream saves accidentally unbound the batch axis.
            # A four-dimensional single stream unambiguously represents B=1.
            shape = [1, *shape]
        if len(shape) != 5 or shape[0] < 1 or shape[1:] != expected:
            raise ValueError(
                "DeRoPE scene %s latent is %s, expected [B,%s] on its original RAW clock. "
                "Save VAE Encode after Exact Recover, not the stretched pass-2 latent." %
                (source["index"], shape, ",".join(map(str, expected))))
        if layout == "joint_av":
            audio = saved.get_slice("upscaled_audio").get_shape() if "upscaled_audio" in keys else []
            _validate_recovered_audio_shape(audio, shape[0], raw)
        elif source.get("audio_route") == "recovered de-rope audio":
            raise ValueError(
                "DeRoPE scene %s changed audio but saved video only. Connect recovered "
                "audio_latent to Recovered AV and save again; original audio is not a substitute."
                % source["index"])


def _load_source_tensors(source: dict[str, Any],
                         keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    presentation = source.get("presentation_source")
    if presentation and not source.get("processing_source"):
        # Only the ALT's visual stream is used. Read base audio selectively so
        # a second complete AV checkpoint never coexists just to preserve sound.
        visual_keys = ("video", "denoised_video")
        audio_keys = ("audio", "denoised_audio", "delivered_audio")
        wanted = keys if keys is not None else visual_keys + audio_keys
        picture = {key: value for key, value in source.items() if key != "presentation_source"}
        result = _load_source_tensors(picture, tuple(key for key in wanted if key in visual_keys)) \
            if any(key in visual_keys for key in wanted) else {}
        if any(key in audio_keys for key in wanted):
            requested_audio = [key for key in audio_keys if key in wanted]
            if "audio" in wanted or "denoised_audio" in wanted:
                requested_audio = list(dict.fromkeys([*requested_audio, "audio", "denoised_audio"]))
            base = _load_source_tensors(presentation["original"], tuple(requested_audio))
            clean_audio = base.get("denoised_audio", base.get("audio"))
            for key in wanted:
                if key in ("audio", "denoised_audio") and clean_audio is not None:
                    result[key] = clean_audio
                elif key == "delivered_audio" and key in base:
                    result[key] = base[key]
        return result
    if chain._st_load is None:
        raise RuntimeError("safetensors is required for deferred H3 upscaling.")
    checkpoint = chain._absolute_output_path(source["checkpoint"])
    expected = str(source.get("checkpoint_sha256") or "")
    if not os.path.isfile(checkpoint):
        raise FileNotFoundError("Source H3 checkpoint is missing: %s" % checkpoint)
    if not expected or chain._file_sha256(checkpoint) != expected:
        raise ValueError("Source H3 checkpoint failed its SHA-256 integrity check.")
    processed = source.get("processing_source")
    if keys is None and not processed:
        return chain._st_load(checkpoint)
    from safetensors import safe_open
    with safe_open(checkpoint, framework="pt", device="cpu") as saved:
        available = set(saved.keys())
        if not processed:
            return {key: saved.get_tensor(key) for key in keys if key in available}
        mapping = {"video": "upscaled_video" if source["latent_layout"] == "joint_av"
                   else "upscaled_samples", "audio": "upscaled_audio",
                   "delivered_audio": "delivered_audio"}
        wanted = keys if keys is not None else tuple(mapping)
        result = {key: saved.get_tensor(mapping[key]) for key in wanted
                  if key in mapping and mapping[key] in available}
    if source["latent_layout"] == "single" and "video" in result and result["video"].ndim == 4:
        result["video"] = result["video"].unsqueeze(0)
    if "audio" in wanted and "audio" not in result:
        # Video-only recovery preserves the exact original take's audio. Read
        # only audio tensors, never load a second full video just to join AV.
        original_audio = _load_source_tensors(processed["original"], ("denoised_audio", "audio"))
        audio = original_audio.get("denoised_audio", original_audio.get("audio"))
        if audio is None:
            raise ValueError("Original DeRoPE source has no reusable audio latent.")
        result["audio"] = audio
    return result


def _source_latent(tensors: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if "denoised_video" in tensors:
        video_key = "denoised_video"
        audio_key = "denoised_audio" if "denoised_audio" in tensors else "audio"
        route = "saved denoised x0"
    else:
        video_key = "video"
        audio_key = "audio"
        route = "terminal sampler output"
    missing = [key for key in (video_key, audio_key) if key not in tensors]
    if missing:
        raise ValueError("Source H3 checkpoint is missing tensors: %s" % missing)
    return ({"samples": _packed_samples(
        [tensors[video_key], tensors[audio_key]])}, route)


def _packed_samples(streams: list[Any]) -> Any:
    if len(streams) == 1:
        return streams[0]
    if _ComfyNestedTensor is not None:
        return _ComfyNestedTensor(streams)
    return streams


def _single_latent_tensor(latent: Any, label: str) -> Any:
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("%s must be a LATENT with samples." % label)
    samples = latent["samples"]
    if chain.torch is not None and chain.torch.is_tensor(samples):
        return samples
    streams = chain._streams_from_latent(latent)
    if len(streams) != 1:
        raise ValueError("%s must contain exactly one latent stream." % label)
    return streams[0]


def _target_video_geometry(latent: Any) -> tuple[Any, int, int]:
    video = _single_latent_tensor(latent, "Pass-2 video latent")
    if getattr(video, "ndim", 0) != 5 or int(video.shape[1]) != 24:
        raise ValueError(
            "Pass-2 video latent must be [B,24,T,H,W], got %s." %
            (tuple(getattr(video, "shape", ())),))
    return video, int(video.shape[-1]) * 16, int(video.shape[-2]) * 16


def _sync_visual_latent(value: Any, scale_x: float, scale_y: float,
                        method: str) -> Any:
    """Resize one H3 visual conditioning latent without changing time."""
    if chain.torch is None or not chain.torch.is_tensor(value):
        raise TypeError(
            "H3 visual conditioning must contain PyTorch tensors, got %r." %
            type(value))
    if value.ndim not in (4, 5):
        raise ValueError(
            "H3 visual conditioning latent must be 4D or 5D, got %s." %
            (tuple(value.shape),))
    source_height, source_width = int(value.shape[-2]), int(value.shape[-1])
    target_height = max(2, int(round(source_height * float(scale_y))))
    target_width = max(2, int(round(source_width * float(scale_x))))
    # H3 patchifies visual conditioning on a 2x2 latent grid without padding.
    target_height = (target_height + 1) // 2 * 2
    target_width = (target_width + 1) // 2 * 2
    if (target_height, target_width) == (source_height, source_width):
        return value

    dtype = value.dtype
    work = value
    if value.device.type == "cpu" and dtype in (
            chain.torch.float16, chain.torch.bfloat16):
        work = value.float()
    if value.ndim == 5:
        batch, channels, frames = work.shape[:3]
        work = work.permute(0, 2, 1, 3, 4).reshape(
            batch * frames, channels, source_height, source_width)
    options = {}
    if method in ("bilinear", "bicubic"):
        options["align_corners"] = False
    resized = chain.torch.nn.functional.interpolate(
        work, size=(target_height, target_width), mode=method, **options)
    if value.ndim == 5:
        resized = resized.reshape(
            batch, frames, channels, target_height, target_width
        ).permute(0, 2, 1, 3, 4).contiguous()
    return resized.to(dtype=dtype)


def _sync_h3_conditioning(conditioning: Any, scale_x: float,
                          scale_y: float, method: str,
                          motion_ref_mode: str) -> Any:
    """Clone CONDITIONING and apply the pass-2 reference policy."""
    if conditioning is None:
        return None
    output = []
    for entry in conditioning:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            output.append(entry)
            continue
        embedding, metadata = entry[0], entry[1]
        if not isinstance(metadata, dict):
            output.append(entry)
            continue
        synced = metadata.copy()
        effective_motion_mode = motion_ref_mode
        if motion_ref_mode == "conditioning_policy":
            effective_motion_mode = str(metadata.get(
                "_h3_upscale_motion_ref_mode") or
                "exclude_video_keep_audio")
        picture_policy = str(metadata.get(
            "_h3_upscale_ref_image_size") or "")
        pictures_already_target_sized = bool(metadata.get(
            "_h3_upscale_picture_refs_target_sized", False))
        refs = metadata.get("minimax_refs")
        if refs is not None:
            _unused, policy_refs = chain._h3_motion_reference_policy(
                [], refs, effective_motion_mode)
            synced_refs = []
            for block in policy_refs:
                copied = dict(block)
                kind = copied.get("kind")
                if kind in ("video", "video_audio"):
                    if effective_motion_mode == "keep_video_native":
                        synced_refs.append(copied)
                        continue
                # Core H3's ``max`` picture policy is deliberately independent
                # of the generation canvas: the same source picture must keep
                # the same capped reference latent at 1 MP, 2 MP, and pass 2.
                # Likewise, a ``match`` picture rebuilt from its cache-v2 RGB
                # master at the target canvas must not be scaled a second time.
                if kind == "image" and (
                        picture_policy == "max" or
                        pictures_already_target_sized):
                    synced_refs.append(copied)
                    continue
                if kind != "audio" and copied.get("latent") is not None:
                    latent = _sync_visual_latent(
                        copied["latent"], scale_x, scale_y, method)
                    copied["latent"] = latent
                    copied["latent_h"] = int(latent.shape[-2])
                    copied["latent_w"] = int(latent.shape[-1])
                    if latent.ndim == 5 and "latent_t" in copied:
                        copied["latent_t"] = int(latent.shape[2])
                synced_refs.append(copied)
            synced["minimax_refs"] = synced_refs
        keyframes = metadata.get("minimax_keyframes")
        if keyframes is not None:
            synced_keyframes = []
            for keyframe in keyframes:
                copied = dict(keyframe)
                if copied.get("latent") is not None:
                    copied["latent"] = _sync_visual_latent(
                        copied["latent"], scale_x, scale_y, method)
                synced_keyframes.append(copied)
            synced["minimax_keyframes"] = synced_keyframes
        output.append([embedding, synced])
    return output


def _mark_h3_upscale_motion_policy(
        conditioning: Any, mode: str, ref_image_size: str | None = None,
        picture_refs_target_sized: bool = False) -> Any:
    """Attach cache policies needed by the later conditioning-sync node."""
    output = []
    for entry in conditioning:
        if (not isinstance(entry, (list, tuple)) or len(entry) < 2
                or not isinstance(entry[1], dict)):
            output.append(entry)
            continue
        metadata = entry[1].copy()
        metadata["_h3_upscale_motion_ref_mode"] = str(mode)
        if ref_image_size in ("match", "max"):
            metadata["_h3_upscale_ref_image_size"] = str(ref_image_size)
            metadata["_h3_upscale_picture_refs_target_sized"] = bool(
                picture_refs_target_sized)
        output.append([entry[0], metadata])
    return output


def _conditioning_from_tagged_upscale_override(
        state: dict[str, Any], clip: Any, video_vae: Any, audio_vae: Any,
        references: Any, prompt: str, ref_image_size: str,
        motion_ref_mode: str, reference_policy: str,
        target_video_latent: Any = None,
        target_size: tuple[int, int] | None = None,
        semantic_anchor_size: str = "512",
        semantic_anchor_mode: str = "timestamped_video") -> tuple[Any, str, str]:
    """Build one coherent live Ref2VA payload for a deferred upscale scene."""
    source = _source_segment(state)
    manifest = state["source_manifest"]
    compatibility = manifest.get("compatibility") or {}
    scene = int(state["index"])
    scene_count = _source_scene_count(manifest)
    length = int(source.get("raw_frames", 0))
    geometry = _source_geometry(source, compatibility)
    width = int(geometry.get("width", 0))
    height = int(geometry.get("height", 0))
    if target_video_latent is not None:
        _video, width, height = _target_video_geometry(target_video_latent)
    elif target_size is not None:
        width, height = target_size

    anchor_bundle = chain._reference_semantic_anchor_bundle(references)
    if anchor_bundle is not None:
        anchor_bundle = chain._make_semantic_anchor_bundle(
            anchor_bundle["entries"], semantic_anchor_size, semantic_anchor_mode)
    compiled, summary, bindings = chain._compile_tagged_reference_prompt(
        references, scene, scene_count, prompt, reference_policy,
        semantic_anchor_mode, anchor_bundle)
    resolved_videos = []
    slice_details = []
    for entry in bindings["videos"]:
        video, paired_audio, detail = chain._scheduled_video_reference_slice(
            entry, None, scene, scene_count, length)
        resolved_videos.append({"video": video, "audio": paired_audio})
        if detail:
            slice_details.append(detail)
    resolved_audios = []
    for entry in bindings["audios"]:
        audio, detail = chain._tagged_audio_reference_value(
            entry, None, scene, scene_count, length)
        resolved_audios.append(audio)
        if detail:
            slice_details.append(detail)

    pictures = [entry["value"] for entry in bindings["pictures"]]
    has_visual_refs = bool(pictures or resolved_videos)
    has_audio_refs = bool(
        resolved_audios or any(
            item.get("audio") is not None for item in resolved_videos))
    if has_visual_refs and not callable(getattr(video_vae, "encode", None)):
        raise ValueError(
            "Connected upscale Tagged Picture/Video refs require the "
            "original MiniMax H3 video VAE on video_vae.")
    if has_audio_refs and not callable(getattr(audio_vae, "encode", None)):
        raise ValueError(
            "Connected upscale Tagged Audio or paired-video audio refs "
            "require the MiniMax H3 audio VAE on audio_vae.")

    semantic_anchors = bindings.get("semantic_anchors") or []
    semantic_presentation = None
    if semantic_anchors:
        semantic_presentation = {
            "version": chain.SEMANTIC_PRESENTATION_VERSION,
            "width": width,
            "height": height,
            "length": length,
            "ref_image_size": ref_image_size,
            "semantic_anchor_size": semantic_anchor_size,
            "semantic_anchor_mode": semantic_anchor_mode,
            "pictures": pictures,
            "videos": [{
                "video": item["video"],
                "paired_audio": item.get("audio") is not None,
            } for item in resolved_videos],
            "standalone_audio_count": len(resolved_audios),
            "anchors": [{
                "tag": anchor["tag"],
                "image": anchor["entry"]["value"],
                "timestamps": tuple(anchor["timestamps"]),
                "untimed": bool(anchor.get("untimed")),
            } for anchor in semantic_anchors],
        }

    presentation, blocks, _source_images = (
        chain._h3_reference_cache_payload(
            video_vae, audio_vae, pictures, resolved_videos,
            resolved_audios, width, height, length, ref_image_size,
            semantic_presentation))
    presentation, blocks = chain._h3_motion_reference_policy(
        presentation, blocks, motion_ref_mode)
    clip_items = []
    for item in presentation:
        public = {"type": item["type"]}
        if "timestamps" in item:
            public["timestamps"] = item["timestamps"]
        if "data" in item:
            public["data"] = item["data"]
        clip_items.append(public)
    tokens = clip.tokenize(compiled, minimax_ref_items=clip_items)
    conditioning = clip.encode_from_tokens_scheduled(tokens)
    if blocks:
        try:
            import node_helpers
        except ImportError as exc:
            raise RuntimeError(
                "Live upscale reference conditioning requires ComfyUI "
                "node_helpers.") from exc
        conditioning = node_helpers.conditioning_set_values(
            conditioning, {"minimax_refs": blocks})
    conditioning = _mark_h3_upscale_motion_policy(
        conditioning, motion_ref_mode, ref_image_size,
        picture_refs_target_sized=bool(
            (target_video_latent is not None or target_size is not None)
            and ref_image_size == "match"))
    if slice_details:
        summary += "; " + "; ".join(slice_details)
    status = (
        "%s; connected Tagged refs replace cached refs; canvas=%dx%d; "
        "picture policy=%s; motion refs=%s" % (
            summary, width, height, ref_image_size, motion_ref_mode))
    if semantic_anchors:
        status += "; semantic_anchor_size=%s; semantic_anchor_mode=%s" % (
            semantic_anchor_size, semantic_anchor_mode)
    return conditioning, compiled, status


def _sample_members(samples: Any) -> tuple[list[Any], bool]:
    if chain.torch is not None and chain.torch.is_tensor(samples):
        return [samples], False
    if hasattr(samples, "unbind"):
        members = list(samples.unbind())
        if members:
            return members, True
    if isinstance(samples, (list, tuple)):
        return list(samples), len(samples) > 1
    raise TypeError("Unsupported MiniMax H3 latent container %r." % type(samples))


def _cpu_latent(latent: dict[str, Any] | None) -> dict[str, Any] | None:
    if latent is None:
        return None
    samples = latent.get("samples") if isinstance(latent, dict) else None
    if samples is None:
        raise ValueError("Upscale latent has no samples value.")
    streams = ([samples] if chain.torch.is_tensor(samples)
               else chain._streams_from_latent(latent))
    copied = [chain._tensor_cpu_clone(item) for item in streams]
    return {"samples": _packed_samples(copied)}


def _latent_checkpoint_tensors(latent: dict[str, Any]) -> tuple[dict[str, Any], str]:
    samples = latent.get("samples") if isinstance(latent, dict) else None
    streams = ([samples] if chain.torch.is_tensor(samples)
               else chain._streams_from_latent(latent))
    if not streams:
        raise ValueError("Upscale latent contains no tensor streams.")
    if len(streams) == 2:
        return ({
            "upscaled_video": chain._tensor_cpu_clone(streams[0]),
            "upscaled_audio": chain._tensor_cpu_clone(streams[1]),
        }, "joint_av")
    if len(streams) == 1:
        return ({"upscaled_samples": chain._tensor_cpu_clone(streams[0])},
                "single")
    return ({"upscaled_stream_%d" % index: chain._tensor_cpu_clone(value)
             for index, value in enumerate(streams)}, "multi")


def _next_drift_context_steps(state: dict[str, Any], index: int) -> int:
    # Pixel refinement keeps the RAW image clock but does not splice latent
    # prefixes. Do not demand an unused HQ latent just because the parent used
    # Drift-Control. All pre-existing backends retain their safety contract.
    if state.get("profile_config", {}).get("backend") == "pixel":
        return 0
    total = _source_bounds(state["source_manifest"])[1]
    if int(index) >= total:
        return 0
    return _drift_prefix_steps(state, int(index) + 1)


def _upscaled_context_tensor(latent: Any, steps: int) -> Any:
    video = _video_stream_from_latent(latent, "Final upscaled latent")
    count = int(steps)
    if count < 1 or int(video.shape[2]) < count:
        raise ValueError(
            "Final upscaled video has %d steps; cannot retain %d-step "
            "continuation context." % (int(video.shape[2]), count))
    return chain._tensor_cpu_clone(video[:, :, -count:])


def _load_previous_upscaled_context(
        state: dict[str, Any], start_clip: int) -> tuple[dict[str, Any] | None,
                                                        str]:
    """Restore the prior HQ video tail for a resumed Drift-Control scene."""
    if state.get("profile_config", {}).get("backend") == "pixel":
        return None, "pixel refinement; no HQ latent continuity required"
    steps = _drift_prefix_steps(state, int(start_clip))
    if steps <= 0 or int(start_clip) <= _source_bounds(state["source_manifest"])[0]:
        return None, "no resumed Drift-Control context required"
    segments = state.get("segments") or []
    if not segments:
        return None, "prior HQ context unavailable"
    prior_segment = segments[-1]
    checkpoint = chain._absolute_output_path(prior_segment["checkpoint"])
    tensors = chain._st_load(checkpoint)
    video = tensors.get("upscaled_video_context")
    route = "compact saved HQ context"
    if video is None:
        video = tensors.get("upscaled_video")
        route = "saved full HQ video latent fallback"
    if video is None:
        candidate = tensors.get("upscaled_samples")
        if (getattr(candidate, "ndim", 0) == 5
                and int(candidate.shape[1]) == 24):
            video = candidate
            route = "saved single HQ video latent fallback"
    if video is None:
        return None, (
            "prior checkpoint predates compact HQ context; source prefix "
            "will be protected")
    if int(video.shape[2]) < steps:
        raise ValueError(
            "Upscale scene %d checkpoint contains %d context steps; scene %d "
            "requires %d." % (int(start_clip) - 1, int(video.shape[2]),
                               int(start_clip), steps))
    context = chain._tensor_cpu_clone(video[:, :, -steps:])
    return {"samples": context}, route


def _upscale_source_contract(source: dict[str, Any]) -> str:
    """Per-scene inputs; selection endpoints/editorial bookkeeping aren't inputs."""
    return chain._fingerprint({key: source.get(key) for key in (
        "index", "id", "revision", "checkpoint_sha256", "segment_sha256",
        "raw_frames", "delivered_frames", "sample_rate", "prompt", "prompt_hash",
        "seed", "steps", "width", "height", "resolution", "audio_mode",
        "continuation_mode", "context_length", "audio_context_length",
        "visual_context_blocks", "source_audio", "source_audio_timing",
        "reference_cache", "generation_fingerprint",
        *(["presentation_source"] if source.get("presentation_source") else []),
        *(["processing_source"] if source.get("processing_source") else []),
    )})


def _verify_upscale_source(metadata: dict[str, Any], source: dict[str, Any],
                           index: int) -> None:
    segment = metadata["segment"]
    for saved_key, source_key in (
            ("source_revision", "revision"),
            ("source_checkpoint_sha256", "checkpoint_sha256"),
            ("source_segment_sha256", "segment_sha256")):
        if not source.get(source_key) or segment.get(saved_key) != source[source_key]:
            raise ValueError("Upscale scene %d points to a different source revision or media "
                             "(including the selected ALT). Restart at that scene or use a new profile." % index)
    # These fields were persisted before per-scene contracts existed, so old
    # HQ saves can resume an extended branch without rewriting their metadata.
    for key in ("raw_frames", "delivered_frames", "prompt", "prompt_hash", "seed", "steps"):
        expected = str(source.get(key) or "") if key in ("prompt", "prompt_hash") else source.get(key)
        if segment.get(key) != expected:
            raise ValueError("Upscale scene %d used different source timing or settings (%s)." % (index, key))
    saved_contract = metadata.get("source_scene_contract")
    if saved_contract and saved_contract != _upscale_source_contract(source):
        raise ValueError("Upscale scene %d used different source scene settings." % index)


def _load_upscale_prefix(state: dict[str, Any], start_clip: int
                         ) -> list[dict[str, Any]]:
    values = []
    for index in range(_source_bounds(state["source_manifest"])[0], int(start_clip)):
        paths = _state_profile_paths(state, index)
        if not os.path.isfile(paths["metadata"]):
            raise FileNotFoundError(
                "Cannot resume upscale scene %d: scene %d metadata is missing: %s"
                % (start_clip, index, paths["metadata"]))
        metadata = chain._read_json(paths["metadata"])
        if metadata.get("format") != "h3_chain_upscale_segment_v1":
            raise ValueError("Upscale scene %d metadata has an unknown format." % index)
        if (metadata.get("run_name") != state["run_name"] or
                metadata.get("profile") != state["profile"]):
            raise ValueError("Upscale scene %d belongs to a different run or profile." % index)
        if metadata.get("profile_config_hash") != state["profile_config"]["config_hash"]:
            raise ValueError("Upscale scene %d used different profile settings." % index)
        segment = metadata.get("segment")
        if not isinstance(segment, dict):
            raise ValueError("Upscale scene %d metadata has no segment." % index)
        _verify_upscale_segment(segment, index)
        source = _source_segment(state, index)
        _verify_upscale_source(metadata, source, index)
        values.append(_public_upscale_segment(segment))
    return values


def _verify_upscale_segment(segment: dict[str, Any], index: int) -> None:
    if int(segment.get("index", -1)) != int(index):
        raise ValueError("Upscale segment slot %d has the wrong scene index." % index)
    for key, hash_key in (("segment", "segment_sha256"),
                          ("checkpoint", "checkpoint_sha256")):
        value = segment.get(key)
        expected = str(segment.get(hash_key) or "")
        if not isinstance(value, str) or not expected:
            raise ValueError("Upscale scene %d has no verified %s." % (index, key))
        path = chain._absolute_output_path(value)
        if not os.path.isfile(path):
            raise FileNotFoundError("Upscale scene %d %s is missing: %s" %
                                    (index, key, path))
        if chain._file_sha256(path) != expected:
            raise ValueError("Upscale scene %d %s failed SHA-256 verification." %
                             (index, key))
    audio = segment.get("generated_audio")
    if audio is not None:
        expected = str(segment.get("generated_audio_sha256") or "")
        path = chain._absolute_output_path(audio)
        if not expected or not os.path.isfile(path) or chain._file_sha256(path) != expected:
            raise ValueError("Upscale scene %d generated audio is invalid." % index)


def _upscale_manifest(state: dict[str, Any], segments: list[dict[str, Any]],
                      complete: bool) -> dict[str, Any]:
    source = state["source_manifest"]
    total = len(source["segments"])
    first, _last = _source_bounds(source)
    indexes = [int(item.get("index", -1)) for item in segments]
    if len(segments) > total or indexes != list(range(first, first + len(segments))):
        raise ValueError("Upscale manifest segments must be contiguous from scene %d." % first)
    manifest = {
        "format": ("h3_chain_upscale_manifest_v1" if complete else
                   "h3_chain_upscale_partial_manifest_v1"),
        "run_name": state["run_name"],
        "profile": state["profile"],
        "profile_config": state["profile_config"],
        "source_manifest_hash": state["source_manifest_hash"],
        "source_plan_hash": source.get("plan_hash"),
        "source_manifest": source,
        "clip_count": total,
        "completed_clip_count": len(segments),
        "total_delivered_frames": sum(
            int(item.get("delivered_frames", 0)) for item in segments),
        "duration_seconds": sum(
            int(item.get("delivered_frames", 0)) for item in segments) /
            float(chain.FPS),
        "segments": [_public_upscale_segment(item) for item in segments],
        "latent_saving": bool(state["profile_config"]["save_latent"]),
    }
    if complete and len(segments) != total:
        raise ValueError("A complete upscale manifest requires %d scenes." % total)
    if source.get("chapter"):
        manifest["scene_start"] = first
        manifest["scene_end"] = indexes[-1] if indexes else first - 1
    if not complete:
        manifest["planned_clip_count"] = total
        manifest["last_completed_clip"] = indexes[-1] if indexes else first - 1
    return manifest


class MiniMaxH3ChainUpscaleAdapter:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "source_manifest": (chain.MANIFEST_TYPE, {
                    "tooltip": "Verified lineage emitted by Checkpoint Manager. "
                               "Selected final-cut ALTs supply picture and conditioning; "
                               "original audio and generation ancestry are preserved. It may stop before "
                               "later ungenerated Plan scenes; no source Plan "
                               "is needed."}),
                "profile": ("STRING", {
                    "default": "h3_2x",
                    "tooltip": "Child output folder under this run's upscaled directory."}),
                "backend": (list(UPSCALE_BACKENDS), {
                    "default": "h3_latent",
                    "tooltip": "Backend label for this child recipe. pixel is "
                               "an experimental IMAGE-only pass: it does not "
                               "require or splice Drift-Control HQ latents. "
                               "Other backends retain latent continuity checks."}),
                "recipe_json": ("STRING", {
                    "default": "{}", "multiline": True,
                    "tooltip": "Advanced, provenance-only backend/model/sigma "
                               "settings, hidden by default. The visible graph "
                               "remains authoritative."}),
                "start_clip": ("INT", {
                    "default": 1, "min": 1, "max": chain.MAX_SHOTS,
                    "tooltip": "Original scene number to resume. 1 starts at "
                               "the first selected scene (also for chapter-only "
                               "input). Later scenes verify and reuse this "
                               "chapter/profile's saved HQ prefix."}),
                "end_clip": ("INT", {
                    "default": 0, "min": 0, "max": chain.MAX_SHOTS,
                    "tooltip": "Last scene to upscale; 0 means the final source scene."}),
                "save_latent": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Persist the HQ sampler latent in each child "
                               "checkpoint. Off still saves assembly/audio and "
                               "any compact Drift-Control context needed for "
                               "standalone merge and resume."}),
                "segment_crf": ("INT", {
                    "default": 18, "min": 0, "max": 51,
                    "tooltip": "H.264 quality for persisted HQ scene segments."}),
            },
            "hidden": {"initial_state": (UPSCALE_STATE_TYPE,)},
        }

    RETURN_TYPES = (UPSCALE_FLOW_TYPE, UPSCALE_STATE_TYPE,
                    chain.MANIFEST_TYPE, "STRING")
    RETURN_NAMES = ("flow", "state", "source_manifest", "status")
    OUTPUT_TOOLTIPS = (
        "Raw recursive-loop link; connect it directly to Upscale Loop End.",
        "Current child-run state for Upscale Current Scene.",
        "Verified manifest of the selected generated checkpoint lineage.",
        "Selected profile, scene range, backend label, and latent-save policy.",
    )
    FUNCTION = "adapt"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("Turn Checkpoint Manager's selected generated lineage into a "
                   "resumable child upscale loop without a source Plan or "
                   "changes to the source run.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("NaN")

    def adapt(self, source_manifest, profile, backend, recipe_json,
              start_clip, end_clip, save_latent, segment_crf,
              initial_state=None):
        if initial_state is None:
            manifest = _verified_source_manifest(source_manifest)
            if any(item.get("processing_source", {}).get("profile_path") ==
                   chain._relative_output_path(_profile_dir(
                       manifest["run_name"], chain._safe_name(profile, "upscale"), manifest))
                   for item in manifest["segments"]):
                raise ValueError("Choose a new output profile; do not overwrite the selected DeRoPE source profile.")
            first, last = _source_bounds(manifest)
            start = first if int(start_clip) == 1 else int(start_clip)
            stop = last if int(end_clip) == 0 else int(end_clip)
            if start < first or start > last:
                raise ValueError(
                    "start_clip must be 1 (first selected scene) or between %d and %d. "
                    "Use original scene numbers, not chapter-relative positions. "
                    "Checkpoint Manager supplied scenes %d–%d; choose the whole branch "
                    "there if later scenes are missing." % (first, last, first, last))
            if stop < start or stop > last:
                raise ValueError("end_clip must be between start_clip and %d." % last)
            state = {
                "run_name": str(manifest["run_name"]),
                "profile": chain._safe_name(profile, "upscale"),
                "profile_config": _profile_config(
                    backend, recipe_json, save_latent, segment_crf),
                "source_manifest": manifest,
                "source_manifest_hash": _source_hash(manifest),
                "index": start,
                "range_start": start,
                # Shared by every recursive scene, but fresh for each queue.
                # PNG export uses this only for durable numbered-folder routing.
                "png_export_session": uuid.uuid4().hex,
                "end_clip": stop,
                "segments": [],
                "previous_frames": None,
                "previous_latent": None,
            }
            state["segments"] = _load_upscale_prefix(state, start)
            previous_latent, context_status = _load_previous_upscaled_context(
                state, start)
            state["previous_latent"] = previous_latent
            state["previous_context_status"] = context_status
        else:
            state = dict(initial_state)
            manifest = state["source_manifest"]
            if _source_hash(_verified_source_manifest(source_manifest)) != str(
                    state["source_manifest_hash"]):
                raise ValueError(
                    "Selected checkpoint branch changed during upscale recursion.")
        status = ("upscale %s scene %d/%d; range %d:%d; backend=%s; HQ latent %s" %
                  (state["profile"], int(state["index"]),
                   _source_bounds(state["source_manifest"])[1],
                   int(state["range_start"]), int(state["end_clip"]),
                   state["profile_config"]["backend"],
                   "saved" if state["profile_config"]["save_latent"]
                   else "not saved"))
        context_status = str(state.get("previous_context_status") or "")
        if context_status:
            status += "; %s" % context_status
        alternates = (manifest.get("presentation_source") or {}).get("scenes") or []
        if alternates:
            status += "; final-cut ALT pictures: " + ", ".join(
                "%s/%s" % (item["scene"], item["alternate_revision"][:8]) for item in alternates)
        return ("h3_upscale", state, manifest, status)


class MiniMaxH3ChainUpscaleCurrent:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"state": (UPSCALE_STATE_TYPE, {
            "tooltip": "Current child-run state from Upscale Adapter."})}}

    RETURN_TYPES = (UPSCALE_STATE_TYPE, "LATENT", "LATENT", "LATENT", "INT",
                    "INT", "STRING", "INT", "INT", "INT", "INT", "INT",
                    "INT", "AUDIO", "IMAGE", "LATENT", "STRING")
    RETURN_NAMES = ("state", "source_latent", "source_video_latent",
                    "source_audio_latent", "clip_index", "clip_count", "prompt",
                    "width", "height", "seed", "trim_frames", "raw_frames",
                    "delivered_frames", "source_audio",
                    "previous_upscaled_frames", "previous_upscaled_latent",
                    "status")
    OUTPUT_TOOLTIPS = (
        "Unchanged current child-run state for Segment Save and Loop End.",
        "Verified joint H3 video/audio x0 for combined learned upscalers.",
        "Verified 24-channel H3 video x0 for video-only upscalers.",
        "Original H3 audio latent to preserve when recombining a refined video.",
        "One-based source scene index.",
        "Scene count for reference schedules: selected parent branch count, "
        "or original Plan count for chapter-only input (not chapter length).",
        "Exact saved prompt for this source scene.",
        "Parent generation width used to rebuild H3 pass-2 conditioning.",
        "Parent generation height used to rebuild H3 pass-2 conditioning.",
        "Saved parent scene seed, suitable for deterministic pass-2 noise.",
        "Repeated raw head frames that Segment Save removes after refinement.",
        "Expected raw frame count before the repeated head is removed.",
        "Expected delivered frame count after trimming.",
        "Decoded delivered parent audio, when the checkpoint contains it.",
        "Prior scene's delivered HQ context frames for backend continuity.",
        "Prior scene's transient HQ latent, when Loop End received one.",
        "Source scene, selected x0 route, and exact frame contract.",
    )
    FUNCTION = "current"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("Verify and load one source scene checkpoint lazily for an "
                   "H3, LTX, or custom upscale body.")

    def current(self, state):
        index = int(state["index"])
        source = _source_segment(state)
        tensors = _load_source_tensors(source)
        latent, route = _source_latent(tensors)
        if source.get("processing_source"):
            route = "saved recovered DeRoPE %s; %s audio latent" % (
                source["revision"][:8], "recovered" if source["latent_layout"] == "joint_av" else "original")
        elif source.get("presentation_source"):
            route += "; ALT %s picture; original %s audio" % (
                source["revision"][:8], source["presentation_source"]["original"]["revision"][:8])
        video_stream, audio_stream = chain._streams_from_latent(latent)
        video_latent = {"samples": video_stream}
        audio_latent = {"samples": audio_stream}
        audio = None
        if "delivered_audio" in tensors:
            sample_rate = int(source.get("sample_rate", 0))
            if sample_rate < 1:
                raise ValueError("Source scene %d has audio but no sample rate." % index)
            audio = {"waveform": tensors["delivered_audio"],
                     "sample_rate": sample_rate}
        raw = int(source["raw_frames"])
        delivered = int(source["delivered_frames"])
        trim = raw - delivered
        compatibility = state["source_manifest"].get("compatibility") or {}
        geometry = _source_geometry(source, compatibility)
        width = int(geometry.get("width", 0))
        height = int(geometry.get("height", 0))
        if width < 1 or height < 1:
            raise ValueError("Source H3 manifest has no valid canvas dimensions.")
        seed = int(source.get("seed", 0))
        status = ("upscale source scene %d/%d: %s; raw=%df delivered=%df trim=%df" %
                  (index, _source_bounds(state["source_manifest"])[1], route,
                   raw, delivered, trim))
        return (state, latent, video_latent, audio_latent, index,
                _source_scene_count(state["source_manifest"]),
                str(source.get("prompt") or ""), width, height, seed, trim,
                raw, delivered, audio, state.get("previous_frames"),
                state.get("previous_latent"), status)


class MiniMaxH3ChainUpscalePixelCurrent:
    """Decode one verified RAW scene, without a joint-AV decode wire."""

    EXPERIMENTAL = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "state": (UPSCALE_STATE_TYPE, {
                "tooltip": "Upscale Adapter state with backend=pixel. Source "
                           "checkpoints and scene clocks remain unchanged."}),
            "video_vae": ("VAE", {
                "tooltip": "MiniMax H3 video VAE to decode one saved clean "
                           "video latent, including its repeated RAW prefix."}),
        }}

    RETURN_TYPES = (UPSCALE_STATE_TYPE, "IMAGE", "AUDIO", "STRING", "INT",
                    "INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("state", "images", "source_audio", "prompt", "seed",
                    "clip_index", "raw_frames", "trim_frames", "status")
    OUTPUT_TOOLTIPS = (
        "Verified current state for conditioning, Segment Save and Loop End.",
        "One complete RAW scene. Upscale/refine these frames without changing "
        "their count; Segment Save removes the repeated head exactly once.",
        "Delivered saved scene audio, or None for a silent source. For preview "
        "only: Segment Save preserves this audio automatically. Do not wire "
        "it to recovered_audio (RAW) or Assemble source_audio (whole run).",
        "Original saved scene prompt.", "Original saved scene seed.",
        "One-based scene index.", "Expected RAW frame count.",
        "Repeated head frame count removed by Segment Save.",
        "Decode route and exact RAW/delivered clock.",
    )
    FUNCTION = "current"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Experimental pixel-upscale scene reader. Decodes the saved clean "
        "VIDEO stream and recovers the original delivered audio separately. "
        "Use any IMAGE upscaler, then pixel conditioning and image refinement. "
        "No full-chain video allocation or upscaled latent is needed.")

    def current(self, state, video_vae):
        if state.get("profile_config", {}).get("backend") != "pixel":
            raise ValueError("Pixel Current Scene requires Upscale Adapter backend=pixel.")
        current = MiniMaxH3ChainUpscaleCurrent().current(state)
        images, _buffer, route = chain._decode_checkpoint_video_for_streaming(
            video_vae, current[2]["samples"], "memory", "")
        if tuple(images.shape) != (current[11], current[8], current[7], 3):
            raise ValueError(
                "Pixel Current Scene decoded %s; expected %d RAW RGB frames "
                "at %dx%d. Use the matching H3 video VAE; do not trim here." %
                (tuple(images.shape), current[11], current[7], current[8]))
        status = "%s; pixel decode: %s; original audio preserved" % (current[-1], route)
        chain._LOG.info("H3 %s", status)
        return (state, images, current[13], current[6], current[9], current[4],
                current[11], current[10], status)


class MiniMaxH3ChainDeropeGuard:
    """Make a MAINodes hold map safe at recursive H3 chain boundaries."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (DEROPE_STATE_TYPE, {
                    "tooltip": "Current Shot or Checkpoint Upscale Loop "
                               "state. Its RAW/delivered contract identifies "
                               "the disposable prefix and saved visual "
                               "context consumers."}),
                "hold_map": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Adaptive world-frame hold map from MAINodes "
                               "H3 Jerk Oracle, before H3 Time Smear."}),
            },
        }

    RETURN_TYPES = ("STRING", "BOOLEAN", "INT", "INT", "STRING")
    RETURN_NAMES = (
        "hold_map", "expand_to_end", "protected_prefix",
        "protected_suffix", "status")
    OUTPUT_TOOLTIPS = (
        "Hold map with the chain's disposable prefix and continuation-side "
        "suffix forced to rate 1.",
        "Connect to H3 Time Smear expand_to_end. It is enabled only for the "
        "last scene in the selected branch.",
        "Number of RAW prefix frames protected from temporal dilation.",
        "Number of closing frames protected because another scene follows.",
        "Verified scene-clock and boundary-protection summary.",
    )
    FUNCTION = "guard"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Adapt a MAINodes de-rope hold map to a live or deferred H3 chain "
        "scene. The repeated continuation prefix is never retimed; the last "
        "17 frames are protected only when a later scene consumes this "
        "scene as visual context. H3 Time Smear's end expansion is enabled "
        "when no later visual-context consumer exists.")

    def guard(self, state, hold_map):
        segment, scene, scene_count, _compat, kind = _derope_state_view(state)
        raw = int(segment.get("raw_frames", 0))
        delivered = int(segment.get("delivered_frames", 0))
        if raw < 1 or delivered < 1 or delivered > raw:
            raise ValueError(
                "H3 de-rope scene has invalid RAW/delivered frames %d/%d." %
                (raw, delivered))
        parsed, holds = _derope_hold_map(
            hold_map, raw, "H3 de-rope oracle hold map")
        prefix = raw - delivered
        consumers = _derope_future_visual_consumers(state, scene)
        suffix = min(17, raw) if consumers else 0
        removed_prefix = sum(holds[:prefix]) - min(prefix, len(holds))
        removed_suffix = (
            sum(holds[len(holds) - suffix:]) - suffix if suffix else 0)
        for index in range(min(prefix, len(holds))):
            holds[index] = 1
        for index in range(max(0, len(holds) - suffix), len(holds)):
            holds[index] = 1
        parsed.update({
            "holds": holds,
            "world_len": raw,
            "h3_chain_scene": scene,
            "h3_chain_protected_prefix": prefix,
            "h3_chain_protected_suffix": suffix,
            "h3_chain_visual_consumers": consumers,
        })
        expand_to_end = not consumers
        status = (
            "de-rope %s scene %d/%d: %d RAW frames; prefix %d held at 1 "
            "(%d dilated frames removed); suffix %d held at 1 "
            "(%d removed); visual consumers=%s; expand_to_end=%s; "
            "protected map currently %d "
            "frames before H3 legal-grid padding" % (
                kind, scene, scene_count, raw, prefix, removed_prefix, suffix,
                removed_suffix,
                ",".join(str(value) for value in consumers) or "none",
                "on" if expand_to_end else "off",
                sum(holds)))
        return (json.dumps(parsed, separators=(",", ":")), expand_to_end,
                prefix, suffix, status)


class MiniMaxH3ChainDeropeFreezeMask:
    """Freeze a protected chain prefix in MAINodes H3 V2V Init."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (DEROPE_STATE_TYPE, {
                    "tooltip": "Current Shot or Checkpoint Upscale Loop "
                               "state carrying the exact RAW/delivered "
                               "prefix contract."}),
                "hold_map_used": ("STRING", {
                    "forceInput": True,
                    "tooltip": "Final hold_map_used emitted by H3 Time Smear. "
                               "It includes end expansion and legal padding."}),
            },
        }

    RETURN_TYPES = ("MASK", "INT", "STRING")
    RETURN_NAMES = ("mask", "dilated_length", "status")
    OUTPUT_TOOLTIPS = (
        "Time-varying regenerate mask for H3 V2V Init: zero over the "
        "continuation prefix and one over material pass 2 may refine.",
        "Exact dilated frame length encoded by hold_map_used.",
        "Prefix-freeze and dilated-clock summary.",
    )
    FUNCTION = "mask"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Build the time-varying mask paired with De-Rope Guard. Connect it "
        "to MAINodes H3 V2V Init and enable time_varying. This prevents pass "
        "2 from re-texturing a Drift-Control continuation prefix after the "
        "same prefix has been protected from retiming.")

    def mask(self, state, hold_map_used):
        if chain.torch is None:
            raise RuntimeError("PyTorch is required for an H3 de-rope mask.")
        segment, _scene, _count, _compat, _kind = _derope_state_view(state)
        raw = int(segment.get("raw_frames", 0))
        delivered = int(segment.get("delivered_frames", 0))
        prefix = raw - delivered
        _parsed, holds = _derope_hold_map(
            hold_map_used, raw, "H3 Time Smear hold_map_used")
        if any(hold != 1 for hold in holds[:prefix]):
            raise ValueError(
                "H3 Time Smear hold_map_used retimes the %d-frame chain "
                "prefix. Route H3 Jerk Oracle through MiniMax H3 Chain "
                "De-Rope Guard before Time Smear." % prefix)
        length = sum(holds)
        mask = chain.torch.ones((length, 8, 8), dtype=chain.torch.float32)
        if prefix:
            mask[:prefix] = 0.0
        status = (
            "de-rope freeze mask: frames [0,%d) frozen, [%d,%d) open; "
            "connect to H3 V2V Init mask with time_varying enabled" %
            (prefix, prefix, length))
        return mask, length, status


class MiniMaxH3ChainDeropeContinuity:
    """Splice a saved HQ Drift-Control tail into the dilated pass-2 init."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (DEROPE_STATE_TYPE, {
                    "tooltip": "Current Shot or Checkpoint Upscale Loop "
                               "state. Live-chain state resolves the same "
                               "linear, non-linear, or composed visual "
                               "context used by Chain Context."}),
                "video_latent": ("LATENT", {
                    "tooltip": "Target-resolution 24-channel clean video "
                               "latent after Time Smear, VAE encode, and the "
                               "spatial latent upscaler."}),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("video_latent", "status")
    OUTPUT_TOOLTIPS = (
        "Target-resolution de-rope video init with the prior HQ tail spliced "
        "into a Drift-Control prefix when required.",
        "Whether prior HQ context was spliced, source context was protected, "
        "or the scene has no latent continuation prefix.",
    )
    FUNCTION = "splice"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Restore live or deferred continuity before MAINodes H3 V2V Init. "
        "For Drift-Control scenes it replaces the protected target-resolution "
        "prefix with the resolved saved visual-context latent; other scenes "
        "pass through unchanged.")

    def splice(self, state, video_latent):
        video, _width, _height = _target_video_geometry(video_latent)
        output, steps, route = _derope_drift_continuation_video(video, state)
        result = dict(video_latent)
        result["samples"] = output
        status = "de-rope continuity: %s" % route
        if steps:
            status += " (%d latent steps)" % steps
        return result, status


class MiniMaxH3ChainRecoveredAV:
    """Repack exact-recovered de-rope latents for chain save and resume."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (DEROPE_STATE_TYPE, {
                    "tooltip": "Current Shot or Checkpoint Upscale Loop "
                               "state whose RAW clock the recovered latent "
                               "must match."}),
                "video_latent": ("LATENT", {
                    "tooltip": "H3 video VAE encode of frames after H3 Exact "
                               "Recover, back on the original RAW clock."}),
            },
            "optional": {
                "audio_latent": ("LATENT", {
                    "tooltip": "H3 audio VAE encode of H3 Audio Recover. It "
                               "is optional for a deferred video-only result "
                               "and required when saving into the live H3 "
                               "chain, whose checkpoints are always joint AV."}),
            },
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "status")
    OUTPUT_TOOLTIPS = (
        "Recovered single-video or joint AV latent suitable for Upscale "
        "Segment Save and Loop End.",
        "Verified recovered RAW clock and packed stream layout.",
    )
    FUNCTION = "pack"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Return the de-roped result to the source scene's world clock. This "
        "node rejects a still-dilated video latent, optionally rejoins the "
        "recovered audio latent, and provides the exact HQ context that a "
        "following Drift-Control scene or interrupted child run needs.")

    def pack(self, state, video_latent, audio_latent=None):
        video, width, height = _target_video_geometry(video_latent)
        segment, _scene, _count, _compat, kind = _derope_state_view(state)
        raw = chain._validate_h3_length(
            segment.get("raw_frames", 0), "Recovered H3 scene length")
        expected_steps = (raw - 5) // 17 * 5 + 2
        if int(video.shape[2]) != expected_steps:
            raise ValueError(
                "Recovered de-rope video still has %d latent steps; the "
                "%d-frame RAW world clock requires %d. Decode pass 2, run "
                "H3 Exact Recover, then VAE Encode before this node." %
                (int(video.shape[2]), raw, expected_steps))
        streams = [video]
        layout = "video"
        if kind == "chain" and audio_latent is None:
            raise ValueError(
                "Live-chain de-rope requires recovered audio_latent so "
                "Segment Save and Loop End can persist a complete H3 AV "
                "checkpoint.")
        if audio_latent is not None:
            audio = _single_latent_tensor(
                audio_latent, "Recovered de-rope audio latent")
            if getattr(audio, "ndim", 0) != 4 or int(audio.shape[1]) != 32:
                raise ValueError(
                    "Recovered de-rope audio latent must be [B,32,2,T], "
                    "got %s." % (tuple(getattr(audio, "shape", ())),))
            if int(audio.shape[0]) != int(video.shape[0]):
                raise ValueError(
                    "Recovered de-rope video/audio batches differ: %d vs %d." %
                    (int(video.shape[0]), int(audio.shape[0])))
            streams.append(audio)
            layout = "joint AV"
        result = dict(video_latent)
        result["samples"] = _packed_samples(streams)
        return result, (
            "recovered de-rope %s at %dx%d on %d-frame RAW clock (%d video "
            "latent steps)" % (layout, width, height, raw, expected_steps))


class MiniMaxH3UpscaleReferencePromptOverride:
    """Inline editor that remains on the normal Tagged Reference chain."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt_override": ("STRING", {
                    "default": "", "multiline": True,
                    "dynamicPrompts": True,
                    "tooltip": "Optional pass-2 prompt. Blank tells Upscale "
                               "Reference Conditioning to reuse the source "
                               "scene prompt."}),
                "disabled_tags": ("STRING", {
                    "default": "",
                    "tooltip": "Optional comma-separated @tags to remove "
                               "from the connected pass-2 registry. Leave "
                               "blank to preserve the reference line exactly."}),
            },
            "optional": {
                "references": (chain.TAGGED_REFERENCE_TYPE, {
                    "tooltip": "Normal Tagged Picture/Video/Audio Ref line. "
                               "When connected downstream, this explicit "
                               "registry replaces the automatic cached refs."}),
            },
        }

    RETURN_TYPES = (chain.TAGGED_REFERENCE_TYPE, "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "references", "prompt_override", "reference_fingerprint", "status")
    OUTPUT_TOOLTIPS = (
        "The same Tagged Ref line, optionally filtered by disabled_tags.",
        "Pass-2 prompt override; blank preserves the source scene prompt.",
        "Fingerprint of the effective connected registry, or blank.",
        "Whether the node passed, filtered, or omitted connected refs.",
    )
    FUNCTION = "override"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Stay inline on the standard H3 Tagged Reference chain while selecting "
        "the explicit refs and text used only by deferred upscale. Connected "
        "refs replace the automatic cached payload; an unconnected reference "
        "socket leaves automatic cache restore active.")

    def override(self, prompt_override="", disabled_tags="", references=None):
        prompt = str(prompt_override or "").replace(
            "\r\n", "\n").replace("\r", "\n").strip()
        if references is None:
            status = (
                "no connected Tagged refs; automatic cache remains active"
                + ("; prompt overridden" if prompt else
                   "; source prompt preserved"))
            return None, prompt, "", status

        entries = chain._tagged_reference_entries(references)
        disabled = {
            value.strip().lstrip("@")
            for value in str(disabled_tags or "").split(",")
            if value.strip().lstrip("@")
        }
        unknown = disabled.difference({
            alias for entry in entries
            for alias in chain._reference_entry_tags(entry)
        })
        if unknown:
            raise ValueError(
                "Upscale reference override cannot disable unknown tag @%s." %
                sorted(unknown)[0])
        if disabled:
            effective_entries = [
                entry for entry in entries
                if not disabled.intersection(
                    chain._reference_entry_tags(entry))]
            effective = chain._make_tagged_references(effective_entries)
        else:
            effective_entries = entries
            effective = references
        fingerprint = str(effective.get("fingerprint") or "")
        status = "%d connected Tagged ref(s) replace cache" % len(
            effective_entries)
        if disabled:
            status += "; disabled %s" % ", ".join(
                "@" + tag for tag in sorted(disabled))
        status += "; " + (
            "prompt overridden" if prompt else "source prompt preserved")
        return effective, prompt, fingerprint, status


class MiniMaxH3ChainUpscaleReferenceConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (UPSCALE_STATE_TYPE, {
                    "tooltip": "Current Upscale state. The node uses only the "
                               "selected source branch, scene, prompt, and "
                               "generation fingerprint to discover refs."}),
                "clip": ("CLIP", {
                    "tooltip": "MiniMax H3 text encoder used to rebuild the "
                               "cached Ref2VA presentation at pass 2."}),
                "missing_cache": (("text_only", "error"), {
                    "default": "text_only",
                    "tooltip": "Missing caches are first rebuilt from saved "
                               "reference identities and archived media, using "
                               "the connected VAEs. If recovery is unavailable, "
                               "text_only falls back to text; error reports the "
                               "missing source or VAE without dropping refs."}),
                "motion_ref_mode": (MOTION_REFERENCE_MODES, {
                    "default": "exclude_video_keep_audio",
                    "tooltip": "Pass-2 motion-reference policy. The default "
                               "removes both native video-ref latents and their "
                               "Qwen presentation, while keeping paired audio."}),
            },
            "optional": {
                "target_video_latent": ("LATENT", {
                    "tooltip": "Actual 24-channel pass-2 video latent. When "
                               "connected with video_vae, match-sized picture "
                               "refs and their Qwen presentation are rebuilt "
                               "for its exact H3 canvas."}),
                "video_vae": ("VAE", {
                    "tooltip": "Original MiniMax H3 video VAE used to "
                               "re-encode match-sized picture references at "
                               "the pass-2 canvas. Never use the audio VAE."}),
                "prompt_override": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "Optional pass-2 text prompt. Blank reuses the "
                               "exact compiled generation prompt. Use a concise "
                               "appearance/detail prompt to avoid repeating "
                               "source motion or camera instructions."}),
                "tagged_references": (chain.TAGGED_REFERENCE_TYPE, {
                    "tooltip": "Optional pass-2 Tagged Ref line from Upscale "
                               "Reference + Prompt Override. When connected, "
                               "it replaces the cached reference payload and "
                               "is compiled with prompt_override."}),
                "audio_vae": ("VAE", {
                    "tooltip": "MiniMax H3 audio VAE. It is needed only when "
                               "the connected override registry activates "
                               "standalone or paired reference audio."}),
                "override_ref_image_size": (("inherit", "match", "max"), {
                    "default": "inherit",
                    "tooltip": "Picture sizing for cached, rebuilt, or connected "
                               "refs. inherit preserves the saved match/max "
                               "policy. Reconstruction defaults to max when "
                               "no saved policy is available. match/max "
                               "explicitly overrides it. "
                               "Changing cached sizing requires video_vae."}),
                "override_reference_policy": (
                    list(chain.REFERENCE_COMPLIANCE_MODES), {
                        "default": "strict",
                        "tooltip": "Prompt/tag validation for connected "
                                   "pass-2 Tagged refs."}),
                "override_semantic_anchor_size": (
                    ["inherit", *chain.SEMANTIC_ANCHOR_SIZES], {
                        "default": "inherit",
                        "tooltip": "Qwen semantic-anchor size, separate from "
                                   "native picture sizing. inherit keeps saved "
                                   "settings (or a connected anchor bundle). An "
                                   "explicit size rebuilds cached anchors from "
                                   "verified source media; source keeps their "
                                   "original size. Use a new upscale profile "
                                   "when changing this setting."}),
                "override_semantic_anchor_mode": (
                    ["inherit", *chain.SEMANTIC_ANCHOR_MODES], {
                        "default": "inherit",
                        "tooltip": "Semantic-anchor presentation: timestamped "
                                   "video or picture storyboard, as in Tagged "
                                   "Scene Options. Explicit changes rebuild "
                                   "both presentation and prompt labels. Use a "
                                   "new upscale profile when changing this setting."}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = (
        "positive", "compiled_prompt", "reference_cache_used", "status")
    OUTPUT_TOOLTIPS = (
        "Pass-2 H3 conditioning rebuilt from cached or explicitly connected "
        "Ref2VA media. Match pictures remain canvas-aware and motion video "
        "follows motion_ref_mode.",
        "Prompt actually encoded for pass 2: custom override, exact cached "
        "compiled prompt, or saved source prompt.",
        "True only when the automatic cache was used; connected live refs "
        "report False even though their conditioning is active.",
        "Scene-local cache lookup result and fingerprint summary.",
    )
    FUNCTION = "condition"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Automatically restore a scene-local H3 reference payload or replace "
        "it with a connected pass-2 Tagged Ref line and, when "
        "given the upscaled video latent plus H3 video VAE, rebuild all "
        "resolution-dependent pass-2 picture conditioning. The motion policy "
        "filters both Qwen presentation and native H3 reference blocks. No "
        "Plan or Source Timeline wiring is required.")

    def condition(self, state, clip, missing_cache="text_only",
                  target_video_latent=None, video_vae=None,
                  prompt_override="",
                  motion_ref_mode="exclude_video_keep_audio",
                  tagged_references=None, audio_vae=None,
                  override_ref_image_size="inherit",
                  override_reference_policy="strict", _target_size=None,
                  override_semantic_anchor_size="inherit",
                  override_semantic_anchor_mode="inherit"):
        if override_ref_image_size not in ("inherit", "match", "max"):
            raise ValueError(
                "Override ref_image_size must be inherit, match, or max.")
        anchor_overrides = {
            "semantic_anchor_size": override_semantic_anchor_size,
            "semantic_anchor_mode": override_semantic_anchor_mode,
        }
        for key, choices in (("semantic_anchor_size", chain.SEMANTIC_ANCHOR_SIZES),
                             ("semantic_anchor_mode", chain.SEMANTIC_ANCHOR_MODES)):
            if anchor_overrides[key] not in ("inherit", *choices):
                raise ValueError("Override %s must be one of %s." % (key, ("inherit", *choices)))
        explicit_anchors = {key: value for key, value in anchor_overrides.items() if value != "inherit"}
        anchor_status = ("; explicit anchor overrides: " + ", ".join(
            "%s=%s" % item for item in explicit_anchors.items())) if explicit_anchors else ""
        from .reference_cache_recovery import (
            ReferenceRecoveryUnavailable, cache_payload_missing,
            recover_reference_cache, saved_reference_settings,
        )
        if target_video_latent is not None and video_vae is None:
            raise ValueError(
                "Target-resolution H3 conditioning needs both "
                "target_video_latent and video_vae.")
        target_size = _target_size
        if target_video_latent is not None:
            _video, target_width, target_height = _target_video_geometry(
                target_video_latent)
            target_size = (target_width, target_height)
        source = _source_segment(state)
        manifest = state["source_manifest"]
        compatibility = manifest.get("compatibility") or {}
        fingerprint = str(
            source.get("generation_fingerprint",
                       compatibility.get("generation_fingerprint")) or "")
        scene = int(state["index"])
        scene_count = _source_scene_count(manifest)
        prompt = str(source.get("prompt") or "")
        custom_prompt = str(prompt_override or "").strip()
        if motion_ref_mode not in MOTION_REFERENCE_MODES:
            raise ValueError(
                "Unknown H3 motion reference mode %r." % motion_ref_mode)
        geometry = _source_geometry(source, compatibility)
        width = int(geometry.get("width", 0))
        height = int(geometry.get("height", 0))
        length = int(source.get("raw_frames", 0))
        descriptor = source.get("reference_cache")
        if source.get("processing_source"):
            # Cache lookup belongs to generation, not the later canvas. Adapt
            # the found references to the processing canvas separately.
            target_size = target_size or (width, height)
            original = source["processing_source"]["original"]
            original_geometry = chain.saved_resolution(original) or compatibility
            width, height = int(original_geometry["width"]), int(original_geometry["height"])
        if tagged_references is not None:
            ref_image_size = str(override_ref_image_size)
            inherited_settings = {}
            if ref_image_size == "inherit" or any(value == "inherit" for value in anchor_overrides.values()):
                inherited_cache = None
                try:
                    inherited_cache = (
                        chain._load_run_reference_cache_descriptor(
                            manifest.get("run_name"), scene, descriptor)
                        if isinstance(descriptor, dict) else
                        chain._find_reference_cache(
                            fingerprint, scene, scene_count, prompt, width,
                            height, length))
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    # The explicit live registry is self-contained. A stale
                    # source cache must not block it merely because inherit
                    # was selected. Try the immutable recipe before defaults.
                    inherited_cache = None
                if inherited_cache is None:
                    try:
                        inherited_cache, _defaults = saved_reference_settings(
                            chain, source, manifest)
                    except (OSError, TypeError, ValueError, KeyError):
                        inherited_cache = None
                inherited_settings = {**(inherited_cache or {}),
                                      **((inherited_cache or {}).get("presentation_contract") or {})}
            if ref_image_size == "inherit":
                ref_image_size = str(inherited_settings.get("ref_image_size") or "match")
            bundle = chain._reference_semantic_anchor_bundle(tagged_references) or {}
            anchor_settings = {}
            for key, default in (("semantic_anchor_size", "512"),
                                 ("semantic_anchor_mode", "timestamped_video")):
                inherited = bundle.get(key)
                if inherited in (None, "inherit"):
                    inherited = inherited_settings.get(key) or default
                anchor_settings[key] = explicit_anchors.get(key, inherited)
            conditioning, compiled, status = (
                _conditioning_from_tagged_upscale_override(
                    state, clip, video_vae, audio_vae, tagged_references,
                    custom_prompt or prompt, ref_image_size,
                    motion_ref_mode, override_reference_policy,
                    target_video_latent, target_size, **anchor_settings))
            status += anchor_status
            if custom_prompt:
                status += "; custom pass-2 prompt override"
            return conditioning, compiled, False, status
        recovery_status = ""
        recovery_error = ""
        try:
            cached = (chain._load_run_reference_cache_descriptor(
                          manifest.get("run_name"), scene, descriptor)
                      if isinstance(descriptor, dict) else
                      chain._find_reference_cache(
                          fingerprint, scene, scene_count, prompt, width, height,
                          length))
        except FileNotFoundError:
            cached = None
        except ValueError:
            # The legacy loader reports a missing tensor as an integrity
            # error. Recover only if the pinned descriptor still matches its
            # metadata and a payload file is actually absent, not corrupted.
            pinned = (chain._read_json(chain._absolute_output_path(descriptor["metadata"]))
                      if isinstance(descriptor, dict) else None)
            if (pinned is None or chain._reference_cache_descriptor(pinned) != descriptor
                    or not cache_payload_missing(chain, pinned)):
                raise
            cached = None
        if cached is not None and cache_payload_missing(chain, cached):
            cached = None
        if cached is None:
            try:
                cached, recovery_status = recover_reference_cache(
                    chain, source, manifest, scene_count, video_vae, audio_vae,
                    ref_image_size=override_ref_image_size, **anchor_overrides)
            except ReferenceRecoveryUnavailable as exc:
                recovery_error = str(exc)
        if cached is not None:
            # V1 match caches may have lost their full-size masters. Recover
            # those from verified archived media before changing to max;
            # relabelling their already downsized tensors would ignore Max.
            contract = cached.get("presentation_contract") or {}
            change_anchors = bool(contract or chain._SEMANTIC_ANCHOR_RE.search(prompt)) and any(
                contract.get(key) != value for key, value in explicit_anchors.items())
            recover_max_masters = (override_ref_image_size == "max"
                    and cached.get("ref_image_size", "match") != "max"
                    and len(cached.get("source_images") or []) < sum(
                        block.get("kind") == "image"
                        for block in cached.get("reference_blocks") or []))
            if change_anchors or recover_max_masters:
                # Preserve every unspecified cache setting, not legacy defaults.
                # Changed settings participate in the derived cache identity;
                # the immutable source descriptor is never replaced.
                settings = {key: explicit_anchors.get(key, contract.get(key) or "inherit")
                            for key in anchor_overrides}
                try:
                    cached, recovery_status = recover_reference_cache(
                        chain, source, manifest, scene_count, video_vae, audio_vae,
                        ref_image_size=(cached.get("ref_image_size") or "match")
                        if override_ref_image_size == "inherit" else override_ref_image_size,
                        **settings)
                except ReferenceRecoveryUnavailable as exc:
                    if change_anchors:
                        raise ValueError(
                            "Cannot apply semantic-anchor overrides from saved media: %s "
                            "Connect explicit Tagged references with the original anchors." % exc) from exc
                    raise ValueError(
                        "Cannot change cached match references to max without "
                        "original picture masters: %s" % exc) from exc
            cached_policy = str(cached.get("ref_image_size") or "match")
            ref_image_size = (cached_policy if override_ref_image_size == "inherit"
                              else override_ref_image_size)
            target_detail = None
            if target_size is not None or ref_image_size != cached_policy:
                target_width, target_height = target_size or (width, height)
                conditioning, target_detail = (
                    chain._conditioning_from_reference_cache_target(
                        clip, video_vae, cached, target_width, target_height,
                        custom_prompt or None, motion_ref_mode,
                        ref_image_size=ref_image_size))
            else:
                conditioning = chain._conditioning_from_reference_cache(
                    clip, cached, custom_prompt or None, motion_ref_mode)
            conditioning = _mark_h3_upscale_motion_policy(
                conditioning, motion_ref_mode, ref_image_size,
                picture_refs_target_sized=bool(
                    target_size is not None and target_detail is not None and
                    target_detail.get("policy") == "match"))
            compiled = (custom_prompt or
                        str(cached.get("compiled_prompt") or prompt))
            status = (
                "scene %d/%d restored Ref2VA cache %s (%d blocks, %d "
                "presentation items)" % (
                    scene, scene_count, str(cached.get("signature") or "")[:12],
                    len(cached.get("reference_blocks") or ()),
                    len(cached.get("presentation") or ())))
            if target_detail is not None:
                status += (
                    "; pass-2 %dx%d policy=%s: rebuilt %d %s pictures "
                    "(%d source masters, %d V1 fallbacks), preserved %d "
                    "native blocks" % (
                        target_detail["target_width"],
                        target_detail["target_height"],
                        target_detail["policy"],
                        target_detail["rebuilt_images"],
                        target_detail["policy"],
                        target_detail["master_rebuilds"],
                        target_detail["fallback_rebuilds"],
                        target_detail["preserved_blocks"]))
            if override_ref_image_size != "inherit":
                status += "; explicit picture sizing override=%s" % ref_image_size
            if ref_image_size == "max" and not (target_detail or {}).get("rebuilt_images"):
                status += "; max pictures keep cached geometry"
            if custom_prompt:
                status += "; custom pass-2 prompt override"
            status += "; motion refs=%s" % motion_ref_mode
            if recovery_status:
                status += "; " + recovery_status
            status += anchor_status
            return conditioning, compiled, True, status
        if str(missing_cache) == "error":
            raise FileNotFoundError(
                "No automatic H3 reference cache matches source scene %d. "
                "Saved-reference recovery could not complete: %s "
                "Restore the named source media/connect Tagged references, "
                "or explicitly choose text_only." % (scene, recovery_error))
        compiled = custom_prompt or prompt
        tokens = clip.tokenize(compiled)
        conditioning = clip.encode_from_tokens_scheduled(tokens)
        conditioning = _mark_h3_upscale_motion_policy(
            conditioning, motion_ref_mode)
        status = (
            "scene %d/%d has no matching Ref2VA cache; rebuilt text-only "
            "conditioning%s" % (
                scene, scene_count,
                " (generation fingerprint %s)" % fingerprint[:12]
                if fingerprint else ""))
        if custom_prompt:
            status += "; custom pass-2 prompt override"
        status += "; motion refs=%s" % motion_ref_mode
        if recovery_error:
            status += "; saved-reference recovery unavailable: " + recovery_error
        return conditioning, compiled, False, status


class MiniMaxH3ChainUpscalePixelConditioning:
    """Rebuild references with optional sizing; sync spatial inputs to IMAGE."""

    EXPERIMENTAL = True

    @classmethod
    def INPUT_TYPES(cls):
        schema = MiniMaxH3ChainUpscaleReferenceConditioning.INPUT_TYPES()
        schema["optional"].pop("target_video_latent")
        # Append new widgets after the existing pixel canvas controls so old
        # positional widgets_values keep their conditioning width and height.
        anchors = {key: schema["optional"].pop(key) for key in (
            "override_semantic_anchor_size", "override_semantic_anchor_mode")}
        schema["required"]["video_vae"] = schema["optional"].pop("video_vae")
        schema["required"]["images"] = ("IMAGE", {
            "tooltip": "Actual upscaled RAW images BEFORE USDU/refinement. "
                       "Their width/height drive automatic reference sizing. Connect "
                       "the same images to the image refiner; no latent encode "
                       "or guessed scale multiplier is needed."})
        schema["required"]["method"] = (CONDITIONING_SYNC_METHODS, {
            "default": "bilinear",
            "tooltip": "Interpolation for eligible cached video/keyframe "
                       "latents. Match picture refs are rebuilt from RGB "
                       "masters; max pictures and audio remain unchanged."})
        for axis in ("width", "height"):
            schema["optional"][f"conditioning_{axis}"] = ("INT", {
                "default": 0, "min": 0, "max": 16384, "step": 32,
                "tooltip": f"Reference-conditioning canvas {axis}. 0 uses the "
                           f"actual image {axis}; a positive multiple of 32 "
                           "overrides only this axis. Sizes cached or connected "
                           "match picture refs, not output images or keyframes. "
                           "Max picture policy remains unchanged. Use a new "
                           "upscale profile when changing this setting."})
        schema["optional"].update(anchors)
        return schema

    RETURN_TYPES = ("CONDITIONING", "IMAGE", "INT", "INT", "STRING", "BOOLEAN", "STRING")
    RETURN_NAMES = ("positive", "images", "width", "height", "compiled_prompt",
                    "reference_cache_used", "status")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning with automatic or custom reference sizing. "
        "Connect to a fresh Basic Guider.",
        "Unchanged upscaled RAW images for the pixel refiner.",
        "Measured target width.", "Measured target height.",
        "Actual encoded pass-2 prompt.", "Whether an automatic cache was restored.",
        "Actual image size, reference size, scale, cache and motion-reference policy.",
    )
    FUNCTION = "condition"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Experimental IMAGE-driven pass-2 conditioning. Combines reference "
        "restore and spatial synchronization without an upscaled latent. "
        "conditioning_width/height=0 follow the images; positive values "
        "override only the reference-conditioning canvas. Output images and "
        "keyframes stay aligned to the actual canvas. "
        "The actual image canvas must be H3-aligned (multiples of 32); no "
        "silent resize, video encoding, audio sampling or prefix masking occurs.")

    def condition(self, state, clip, images, video_vae, method="bilinear",
                  missing_cache="text_only", motion_ref_mode="exclude_video_keep_audio",
                  prompt_override="", tagged_references=None, audio_vae=None,
                  override_ref_image_size="inherit", override_reference_policy="strict",
                  conditioning_width=0, conditioning_height=0,
                  override_semantic_anchor_size="inherit",
                  override_semantic_anchor_mode="inherit"):
        if method not in CONDITIONING_SYNC_METHODS:
            raise ValueError("Unknown H3 conditioning sync method %r." % method)
        source = _source_segment(state)
        if (not chain.torch.is_tensor(images) or images.ndim != 4
                or int(images.shape[-1]) != 3
                or int(images.shape[0]) != int(source["raw_frames"])):
            raise ValueError("Pixel conditioning needs the complete RAW RGB scene, "
                             "without frame trimming, interpolation or duplication.")
        height, width = map(int, images.shape[1:3])
        if width < 32 or height < 32 or width % 32 or height % 32:
            raise ValueError("Pixel target %dx%d is not H3-aligned. Resize the images "
                             "to multiples of 32 before conditioning and refinement." %
                             (width, height))
        reference_size = []
        for axis, value, automatic in (
                ("width", conditioning_width, width),
                ("height", conditioning_height, height)):
            if (not isinstance(value, int) or value < 0 or value > 16384
                    or value % 32):
                raise ValueError(
                    "conditioning_%s must be 0 (auto) or a positive "
                    "multiple of 32 up to 16384." % axis)
            reference_size.append(value or automatic)
        compatibility = state["source_manifest"].get("compatibility") or {}
        source_width, source_height = (int(compatibility.get(key, 0))
                                       for key in ("width", "height"))
        if source_width < 1 or source_height < 1:
            raise ValueError("Source H3 manifest has no valid canvas dimensions.")
        positive, compiled, used, status = (
            MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                state, clip, missing_cache, video_vae=video_vae,
                prompt_override=prompt_override, motion_ref_mode=motion_ref_mode,
                tagged_references=tagged_references, audio_vae=audio_vae,
                override_ref_image_size=override_ref_image_size,
                override_reference_policy=override_reference_policy,
                override_semantic_anchor_size=override_semantic_anchor_size,
                override_semantic_anchor_mode=override_semantic_anchor_mode,
                _target_size=tuple(reference_size)))
        # Reference pictures have already been rebuilt for the chosen canvas
        # and are marked to avoid double scaling. Keyframes and eligible
        # video latents still follow the real images, never the override.
        scale_x, scale_y = width / source_width, height / source_height
        positive = _sync_h3_conditioning(
            positive, scale_x, scale_y, method, "conditioning_policy")
        status += "; pixel target %dx%d (x%.4g/y%.4g); no target latent" % (
            width, height, scale_x, scale_y)
        if conditioning_width or conditioning_height:
            status += "; conditioning canvas %dx%d (width=%s, height=%s)" % (
                *reference_size,
                "manual" if conditioning_width else "auto",
                "manual" if conditioning_height else "auto")
        chain._LOG.info("H3 %s", status)
        return positive, images, width, height, compiled, used, status


class H3ConditioningSyncFromLatents:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original_latent": ("LATENT", {
                    "tooltip": "Original-resolution 24-channel H3 video latent "
                               "whose conditioning will be synchronized. Its "
                               "time may differ from a de-rope target."}),
                "upscaled_latent": ("LATENT", {
                    "tooltip": "Upscaled 24-channel H3 video latent. Its exact "
                               "X/Y scale drives the conditioning resize; a "
                               "dilated de-rope time axis is supported."}),
                "positive": ("CONDITIONING", {
                    "tooltip": "Original H3 conditioning containing minimax_refs "
                               "and/or minimax_keyframes. Cached max pictures "
                               "remain at their core-defined geometry."}),
                "method": (CONDITIONING_SYNC_METHODS, {
                    "default": "bilinear",
                    "tooltip": "Spatial interpolation used for match-sized "
                               "picture refs, eligible video refs, and keyframe "
                               "latents. Max pictures, time, and audio are unchanged."}),
                "motion_ref_mode": (CONDITIONING_SYNC_MOTION_MODES, {
                    "default": "conditioning_policy",
                    "tooltip": "conditioning_policy automatically follows "
                               "Upscale Reference Conditioning. Explicit modes "
                               "are available for standalone/A-B use."}),
            },
            "optional": {
                "negative": ("CONDITIONING", {
                    "tooltip": "Optional negative conditioning. Use the returned "
                               "negative when rebuilding a CFG Guider."}),
            },
        }

    RETURN_TYPES = (
        "CONDITIONING", "CONDITIONING", "INT", "INT", "FLOAT", "FLOAT")
    RETURN_NAMES = (
        "positive", "negative", "width", "height", "scale_x", "scale_y")
    OUTPUT_TOOLTIPS = (
        "Positive conditioning with visual refs/keyframes synchronized to the "
        "upscaled latent.",
        "Negative conditioning synchronized by the same scale, when connected.",
        "Upscaled video width in pixels.",
        "Upscaled video height in pixels.",
        "Exact horizontal latent scale.",
        "Exact vertical latent scale.",
    )
    FUNCTION = "sync"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Synchronize H3 pass-2 conditioning from the original and upscaled "
        "latents. Resizes match-sized picture refs and keyframes while keeping "
        "Core H3 max-sized pictures invariant, filters or retains motion refs "
        "according to the conditioner policy (or an explicit override), "
        "updates changed H/W metadata, and leaves reference time and audio "
        "conditioning untouched. The target may use a longer de-rope clock; "
        "only batch and video channels must match. Build a new Guider from "
        "the returned output.")

    def sync(self, original_latent, upscaled_latent, positive,
             method="bilinear",
             motion_ref_mode="conditioning_policy", negative=None):
        original, _source_width, _source_height = _target_video_geometry(
            original_latent)
        upscaled, width, height = _target_video_geometry(upscaled_latent)
        if tuple(original.shape[:2]) != tuple(upscaled.shape[:2]):
            raise ValueError(
                "H3 conditioning sync requires matching batch/channel; "
                "original %s vs upscaled %s." %
                (tuple(original.shape[:2]), tuple(upscaled.shape[:2])))
        if method not in CONDITIONING_SYNC_METHODS:
            raise ValueError("Unknown H3 conditioning sync method %r." % method)
        if motion_ref_mode not in CONDITIONING_SYNC_MOTION_MODES:
            raise ValueError(
                "Unknown H3 motion reference mode %r." % motion_ref_mode)
        scale_x = int(upscaled.shape[-1]) / float(original.shape[-1])
        scale_y = int(upscaled.shape[-2]) / float(original.shape[-2])
        return (
            _sync_h3_conditioning(
                positive, scale_x, scale_y, method, motion_ref_mode),
            _sync_h3_conditioning(
                negative, scale_x, scale_y, method, motion_ref_mode),
            width, height, scale_x, scale_y,
        )


class MiniMaxH3ChainPass2Prepare:
    """Rejoin LBH's video-only result with clean audio and CONST re-noise."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "upscaled_video": ("LATENT", {
                    "tooltip": "24-channel clean H3 video latent emitted by "
                               "LBH's 2D/3D latent upscaler."}),
                "source_audio": ("LATENT", {
                    "tooltip": "Untouched 32-channel audio latent from H3 "
                               "Upscale Current Scene."}),
                "model": ("MODEL", {
                    "tooltip": "The exact pass-2 H3 model after LoRA and "
                               "sigma-shift patches."}),
                "noise": ("NOISE", {
                    "tooltip": "Pass-2 random noise. Only the video member is "
                               "generated; audio stays at the saved x0."}),
                "sigmas": ("SIGMAS", {
                    "tooltip": "Pass-2 sigma schedule. Its first sigma is "
                               "used for NestedTensor-safe CONST re-noise."}),
            },
            "optional": {
                "state": (UPSCALE_STATE_TYPE, {
                    "tooltip": "Current Upscale state. When the source scene "
                               "uses Drift-Control AV, its previous HQ latent "
                               "tail replaces and protects the pass-2 prefix."}),
            },
        }

    RETURN_TYPES = ("LATENT", "INT", "INT", "STRING")
    RETURN_NAMES = ("latent", "width", "height", "status")
    OUTPUT_TOOLTIPS = (
        "Joint H3 AV latent ready for Sampler Custom Advanced with Disable "
        "Noise; video is re-noised and audio is masked clean.",
        "Exact output pixel width inferred from the upscaled latent.",
        "Exact output pixel height inferred from the upscaled latent.",
        "Verified stream geometry, audio lock, and sigma start.",
    )
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Adapt a video-only learned upscale into a joint MiniMax H3 pass-2 "
        "latent. Rejoins the saved audio, applies MiniMax CONST re-noise to "
        "the open video region only, protects a Drift-Control AV prefix from "
        "the previous HQ latent when available, inverse-scales for Disable "
        "Noise sampling, and locks audio so pass 2 cannot rewrite speech.")

    def prepare(self, upscaled_video, source_audio, model, noise, sigmas,
                state=None):
        if chain.torch is None:
            raise RuntimeError("PyTorch is required for H3 pass-2 preparation.")
        video, width, height = _target_video_geometry(upscaled_video)
        video, prefix_steps, continuation_route = _drift_continuation_video(
            video, state)
        audio = _single_latent_tensor(source_audio, "Source audio latent")
        if getattr(audio, "ndim", 0) != 4 or int(audio.shape[1]) != 32:
            raise ValueError(
                "Source audio latent must be [B,32,2,T], got %s." %
                (tuple(getattr(audio, "shape", ())),))
        if int(video.shape[0]) != int(audio.shape[0]):
            raise ValueError(
                "Upscaled video/audio batch sizes differ: %d vs %d." %
                (int(video.shape[0]), int(audio.shape[0])))
        sigma_count = int(getattr(sigmas, "numel", lambda: len(sigmas))())
        if sigma_count < 1:
            raise ValueError("Pass-2 sigma schedule is empty.")

        joint = {"samples": _packed_samples([video, audio])}
        generated = noise.generate_noise({"samples": video})
        generated_members, _generated_nested = _sample_members(generated)
        if len(generated_members) != 1:
            raise ValueError(
                "Pass-2 noise source returned %d video streams; expected 1." %
                len(generated_members))
        video_noise = generated_members[0]
        if tuple(video_noise.shape) != tuple(video.shape):
            raise ValueError(
                "Pass-2 video noise shape %s does not match latent %s." %
                (tuple(video_noise.shape), tuple(video.shape)))
        if prefix_steps:
            video_noise = video_noise.clone()
            video_noise[:, :, :prefix_steps] = 0
        joint_noise = _packed_samples([
            video_noise, chain.torch.zeros_like(audio)])

        process_in = model.get_model_object("process_latent_in")
        process_out = model.get_model_object("process_latent_out")
        model_sampling = model.get_model_object("model_sampling")
        processed = process_in(joint["samples"])
        latent_members, latent_nested = _sample_members(processed)
        noise_members, noise_nested = _sample_members(joint_noise)
        if len(latent_members) != 2 or len(noise_members) != 2:
            raise ValueError(
                "MiniMax pass-2 AV preparation expected two joint streams.")
        sigma_start = sigmas[0]
        mixed_members = []
        for latent_member, noise_member in zip(
                latent_members, noise_members):
            mixed = model_sampling.noise_scaling(
                sigma_start, noise_member, latent_member)
            inverse = getattr(model_sampling, "inverse_noise_scaling", None)
            if callable(inverse):
                mixed = inverse(sigma_start, mixed)
            mixed_members.append(mixed)
        mixed = _packed_samples(mixed_members)
        # Preserve the real Comfy NestedTensor container even if a test double
        # returned list-like members from process_latent_in.
        if not (latent_nested or noise_nested) and _ComfyNestedTensor is None:
            mixed = mixed_members
        output_samples = process_out(mixed)
        output_members, _ = _sample_members(output_samples)
        output_samples = _packed_samples([
            chain.torch.nan_to_num(member, nan=0.0, posinf=0.0, neginf=0.0)
            for member in output_members])

        output_video, output_audio = output_members[:2]
        video_mask = chain.torch.ones(
            (output_video.shape[0], 1, output_video.shape[2],
             output_video.shape[3], output_video.shape[4]),
            device=output_video.device, dtype=chain.torch.float32)
        if prefix_steps:
            video_mask[:, :, :prefix_steps] = 0
        audio_mask = chain.torch.zeros(
            (output_audio.shape[0], 1, output_audio.shape[2],
             output_audio.shape[3]),
            device=output_audio.device, dtype=chain.torch.float32)
        result = {
            "samples": output_samples,
            "noise_mask": _packed_samples([video_mask, audio_mask]),
        }
        sigma_value = float(sigma_start.detach().cpu().item()) if hasattr(
            sigma_start, "detach") else float(sigma_start)
        status = (
            "prepared H3 pass 2 at %dx%d: video %s, audio %s locked, "
            "sigma_start=%.6g; %s%s" % (
                width, height, tuple(video.shape), tuple(audio.shape),
                sigma_value, continuation_route,
                " (%d latent steps)" % prefix_steps if prefix_steps else ""))
        return result, width, height, status


class MiniMaxH3ChainUpscaleSegmentSave:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": (UPSCALE_STATE_TYPE, {
                    "tooltip": "Current child-run state from Upscale Current Scene."}),
                "images": ("IMAGE", {
                    "tooltip": "Decoded HQ RAW scene frames, including the source "
                               "scene's repeated head. This node trims it exactly."}),
            },
            "optional": {
                "upscaled_latent": ("LATENT", {
                    "tooltip": "Final HQ latent. Required when save_latent is "
                               "enabled and whenever a following Drift-Control "
                               "scene needs its compact HQ context tail."}),
                "recovered_audio": ("AUDIO", {
                    "tooltip": "Optional RAW-clock audio from H3 Audio "
                               "Recover. When connected, Segment Save applies "
                               "the same repeated-head trim as video and "
                               "replaces the source checkpoint audio."}),
            },
            "hidden": {"dynprompt": "DYNPROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (UPSCALE_SEGMENT_TYPE, "STRING")
    RETURN_NAMES = ("segment", "status")
    OUTPUT_TOOLTIPS = (
        "Verified HQ scene record for Upscale Loop End.",
        "Saved scene path, dimensions, and HQ latent persistence result.",
    )
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("Persist one trimmed HQ scene plus a self-contained assembly "
                   "checkpoint; always retain the small HQ tail needed by a "
                   "following Drift-Control scene and optionally retain the "
                   "complete large HQ latent.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("NaN")

    def save(self, state, images, upscaled_latent=None,
             recovered_audio=None, dynprompt=None, unique_id=None):
        if chain._st_save is None or chain.torch is None:
            raise RuntimeError("safetensors and torch are required for H3 upscale saves.")
        index = int(state["index"])
        source = _source_segment(state)
        raw = int(source["raw_frames"])
        delivered = int(source["delivered_frames"])
        trim = raw - delivered
        if int(images.shape[0]) != raw:
            raise ValueError(
                "Upscale scene %d decoded %d frames; expected %d RAW frames "
                "before trimming %d repeated frames." %
                (index, int(images.shape[0]), raw, trim))
        delivered_images = images[trim:trim + delivered]
        height = int(delivered_images.shape[1])
        width = int(delivered_images.shape[2])
        existing = state["segments"]
        if existing:
            first = existing[0]
            if (int(first.get("width", width)) != width or
                    int(first.get("height", height)) != height):
                raise ValueError(
                    "Upscale scene %d is %dx%d; profile scene %d is %dx%d." %
                    (index, width, height, int(first["index"]),
                     int(first["width"]), int(first["height"])))

        save_latent = bool(state["profile_config"]["save_latent"])
        context_steps = _next_drift_context_steps(state, index)
        if save_latent and upscaled_latent is None:
            raise ValueError(
                "Upscale profile enables save_latent, but scene %d received no HQ latent."
                % index)
        if context_steps and upscaled_latent is None:
            raise ValueError(
                "Upscale scene %d precedes a Drift-Control AV scene and must "
                "receive the final HQ latent so its %d-step context tail can "
                "be resumed." % (index, context_steps))
        # Only audio is needed here; do not reload the full source AV latents
        # while the complete HQ pixel clip is still being encoded to disk.
        source_tensors = _load_source_tensors(source, ("delivered_audio",))
        tensors = {"upscale_marker": chain.torch.tensor([index])}
        sample_rate = int(source.get("sample_rate", 0))
        audio_route = "none"
        if recovered_audio is not None:
            waveform, sample_rate = chain._validate_audio(
                recovered_audio, "Recovered H3 de-rope audio")
            expected_raw = int(round(raw / float(chain.FPS) * sample_rate))
            available = int(waveform.shape[-1])
            # H3's 40 Hz audio clock and VAE decode may leave a sub-frame
            # tail, but a delivered-only track is materially too short and
            # must not be mistaken for the RAW scene clock.
            tolerance = max(2, int(round(sample_rate / float(chain.FPS))))
            if available + tolerance < expected_raw:
                raise ValueError(
                    "Recovered H3 de-rope audio contains %d samples at %d Hz; "
                    "the %d-frame RAW scene needs about %d. Connect H3 Audio "
                    "Recover output, not delivered source audio." %
                    (available, sample_rate, raw, expected_raw))
            start = int(round(trim / float(chain.FPS) * sample_rate))
            count = int(round(delivered / float(chain.FPS) * sample_rate))
            padded = chain._pad_audio_to_samples({
                "waveform": waveform,
                "sample_rate": sample_rate,
            }, start + count, "Recovered H3 de-rope audio")
            tensors["delivered_audio"] = chain._tensor_cpu_clone(
                padded["waveform"][..., start:start + count])
            audio_route = "recovered de-rope audio"
        elif "delivered_audio" in source_tensors:
            tensors["delivered_audio"] = chain._tensor_cpu_clone(
                source_tensors["delivered_audio"])
            audio_route = "source checkpoint audio"
        latent_layout = "omitted"
        if save_latent:
            latent_tensors, latent_layout = _latent_checkpoint_tensors(
                upscaled_latent)
            tensors.update(latent_tensors)
            from .checkpoint_variants import processing_stage
            if processing_stage(state["profile_config"]) == "derope":
                # The saver also guards callers that bypass Recovered AV.
                video = _video_stream_from_latent(upscaled_latent, "Saved DeRoPE latent")
                expected_steps = (chain._validate_h3_length(raw, "DeRoPE RAW length") - 5) // 17 * 5 + 2
                if (int(video.shape[2]) != expected_steps or
                        int(video.shape[3]) * 16 != height or int(video.shape[4]) * 16 != width):
                    raise ValueError("Save the full DeRoPE latent after Exact Recover and VAE Encode on the original RAW clock/canvas.")
                if recovered_audio is not None and latent_layout != "joint_av":
                    raise ValueError("Saving recovered DeRoPE audio requires audio_latent on Recovered AV for later deferred passes.")
                if latent_layout == "joint_av":
                    _validate_recovered_audio_shape(tensors["upscaled_audio"].shape, int(video.shape[0]), raw)
        if context_steps:
            tensors["upscaled_video_context"] = _upscaled_context_tensor(
                upscaled_latent, context_steps)

        paths = _state_profile_paths(state, index)
        for key in ("segment", "checkpoint", "metadata", "prompt", "audio"):
            os.makedirs(os.path.dirname(paths[key]), exist_ok=True)
        transaction = uuid.uuid4().hex
        segment_path = chain._versioned_path(paths["segment"], transaction)
        checkpoint_path = chain._versioned_path(paths["checkpoint"], transaction)
        metadata_path = chain._versioned_path(paths["metadata"], transaction)
        prompt_path = chain._versioned_path(paths["prompt"], transaction)
        audio_path = (chain._versioned_path(paths["audio"], transaction)
                      if "delivered_audio" in tensors else None)
        checkpoint_tmp = "%s.%s.tmp" % (checkpoint_path, uuid.uuid4().hex)
        committed = False
        publication_started = False
        save_warning = ""
        try:
            chain._write_segment_video(
                delivered_images, segment_path, chain.FPS,
                int(state["profile_config"]["segment_crf"]), metadata={
                    "title": "H3 upscale scene %d - %s" %
                             (index, source.get("id", "scene")),
                    "comment": str(source.get("prompt") or ""),
                    "h3_upscale_profile": state["profile"],
                    "h3_upscale_backend": state["profile_config"]["backend"],
                    "h3_source_revision": str(source.get("revision") or ""),
                })
            chain._atomic_text(prompt_path, str(source.get("prompt") or ""))
            if audio_path is not None:
                chain._atomic_wav({
                    "waveform": tensors["delivered_audio"],
                    "sample_rate": sample_rate,
                }, audio_path)
            chain._st_save(tensors, checkpoint_tmp, metadata={
                "format": "h3_chain_upscale_checkpoint_v1",
                "index": str(index),
                "profile": state["profile"],
                "backend": state["profile_config"]["backend"],
                "source_revision": str(source.get("revision") or ""),
                "source_checkpoint_sha256": str(
                    source.get("checkpoint_sha256") or ""),
                "latent_layout": latent_layout,
                "latent_saved": "true" if save_latent else "false",
                "context_steps": str(context_steps),
                "sample_rate": str(sample_rate),
                "audio_route": audio_route,
            })
            os.replace(checkpoint_tmp, checkpoint_path)

            segment = {
                "index": index,
                "id": str(source.get("id") or "clip_%04d" % index),
                "revision": transaction,
                "created_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
                "segment": chain._relative_output_path(segment_path),
                "checkpoint": chain._relative_output_path(checkpoint_path),
                "metadata": chain._relative_output_path(paths["metadata"]),
                "revision_metadata": chain._relative_output_path(metadata_path),
                "prompt_file": chain._relative_output_path(prompt_path),
                "raw_frames": raw,
                "delivered_frames": delivered,
                "trim_frames": trim,
                "width": width,
                "height": height,
                "sample_rate": sample_rate,
                "audio_route": audio_route,
                "latent_saved": save_latent,
                "latent_layout": latent_layout,
                "context_steps": context_steps,
                "source_revision": str(source.get("revision") or ""),
                "source_checkpoint": str(source.get("checkpoint") or ""),
                "source_checkpoint_sha256": str(
                    source.get("checkpoint_sha256") or ""),
                "source_segment_sha256": str(source.get("segment_sha256") or ""),
                "prompt_prefix": str(source.get("prompt_prefix") or ""),
                "scene_prompt": str(source.get("scene_prompt") or ""),
                "prompt": str(source.get("prompt") or ""),
                "prompt_hash": str(source.get("prompt_hash") or ""),
                "seed": source.get("seed"),
                "steps": source.get("steps"),
                "segment_sha256": chain._file_sha256(segment_path),
                "checkpoint_sha256": chain._file_sha256(checkpoint_path),
                "prompt_file_sha256": chain._file_sha256(prompt_path),
            }
            if audio_path is not None:
                segment.update({
                    "generated_audio": chain._relative_output_path(audio_path),
                    "generated_audio_sha256": chain._file_sha256(audio_path),
                })
            previous = None
            if os.path.isfile(paths["metadata"]):
                try:
                    previous = chain._read_json(paths["metadata"])
                except (OSError, ValueError, json.JSONDecodeError):
                    previous = None
            old_segment = previous.get("segment") if isinstance(previous, dict) else None
            if isinstance(old_segment, dict):
                segment["supersedes"] = old_segment.get("revision_metadata")
            metadata = {
                "format": "h3_chain_upscale_segment_v1",
                "run_name": state["run_name"],
                "profile": state["profile"],
                "source_manifest_hash": state["source_manifest_hash"],
                "source_scene_contract": _upscale_source_contract(source),
                "profile_config_hash": state["profile_config"]["config_hash"],
                "profile_config": state["profile_config"],
                "segment": segment,
            }
            from .checkpoint_variants import processing_lineage
            metadata["processing_lineage"] = processing_lineage(
                list(state.get("segments", [])) + [segment])
            prefix = list(state.get("segments", [])) + [segment]
            complete = (index == _source_bounds(state["source_manifest"])[1])
            partial = _upscale_manifest(state, prefix, complete=complete)
            from .processing_checkpoint_delete import require_saved_processing_segments
            from . import processing_persistence as persistence
            # Media must reach storage before any durable document references it.
            artifacts = [segment_path, checkpoint_path, prompt_path]
            if audio_path:
                artifacts.append(audio_path)
            for artifact in artifacts:
                persistence.sync_file(artifact)
            for directory in {os.path.dirname(p) for p in artifacts}:
                persistence.sync_directory(directory)
            with chain.checkpoint_run_lock(chain._output_root(), state["run_name"]):
                require_saved_processing_segments(chain._output_root(),
                    list(state.get("segments", [])) + [item for item in
                        state["source_manifest"]["segments"] if item.get("processing_source")])
                # Once publication starts, a network error can mean "committed
                # but acknowledgement lost". Never remove media that an immutable
                # revision or current pointer may already reference.
                publication_started = True
                persistence.atomic_json(metadata_path, metadata)
                persistence.atomic_json(paths["metadata"], metadata)
                committed = True
                try:
                    persistence.atomic_json(
                        paths["manifest"] if complete else paths["partial"], partial)
                except OSError as exc:
                    save_warning = "; scene saved; manifest refresh failed (resume from scene checkpoints)"
                    chain._LOG.warning("H3 upscale scene %d is saved, but its manifest refresh failed: %s", index, exc)
        finally:
            chain._safe_unlink(checkpoint_tmp)
            if publication_started and not committed:
                chain._LOG.warning(
                    "H3 upscale scene %d publication was interrupted or uncertain; "
                    "new media was retained at %s. The previous take was not deleted.", index, segment_path)
            if not committed and not publication_started:
                for value in (segment_path, checkpoint_path, metadata_path,
                              prompt_path, audio_path):
                    if value:
                        chain._safe_unlink(value)

        cache_cleanup = chain.confirm_saved_use(
            dynprompt, unique_id, metadata_path, chain._output_root(), chain._LOG)
        status = ("saved HQ scene %d/%d at %dx%d; latent %s -> %s" %
                  (index, _source_bounds(state["source_manifest"])[1], width,
                   height, "saved" if save_latent else "omitted", segment_path))
        status += save_warning
        if audio_route != "none":
            status += "; %s" % audio_route
        if context_steps:
            status += "; retained %d-step Drift-Control HQ tail" % context_steps
        if cache_cleanup:
            status += "; retired %d verified legacy reference bundle(s)" % len(cache_cleanup)
        return {
            "ui": {"text": [status],
                   "images": [chain._video_output_item(segment_path)],
                   "animated": (True,)},
            "result": (segment, status),
        }


class MiniMaxH3ChainUpscaleLoopEnd:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "flow": (UPSCALE_FLOW_TYPE, {
                    "rawLink": True,
                    "lazy": True,
                    "tooltip": "Connect directly from Upscale Adapter; this raw "
                               "link defines the recursive child-loop body."}),
                "state": (UPSCALE_STATE_TYPE, {
                    "lazy": True,
                    "tooltip": "Current child-run state from Upscale Current Scene."}),
                "images": ("IMAGE", {
                    "lazy": True,
                    "tooltip": "Same RAW HQ frames sent to Upscale Segment Save."}),
                "segment": (UPSCALE_SEGMENT_TYPE, {
                    "lazy": True,
                    "tooltip": "Persisted HQ scene record from Upscale Segment Save."}),
            },
            "optional": {
                "upscaled_latent": ("LATENT", {
                    "lazy": True,
                    "tooltip": "Optional transient HQ carry for the next scene. "
                               "It is independent of save_latent."}),
            },
            "hidden": {"dynprompt": "DYNPROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (UPSCALE_MANIFEST_TYPE, "STRING", "IMAGE", "LATENT")
    RETURN_NAMES = ("manifest", "manifest_json", "last_context_frames",
                    "last_context_latent")
    OUTPUT_TOOLTIPS = (
        "Complete verified child manifest for H3 Chain Assemble, or a partial manifest.",
        "Readable JSON form of the emitted child manifest.",
        "Delivered HQ tail retained from the last processed scene.",
        "Transient HQ latent retained from the last processed scene, if connected.",
    )
    FUNCTION = "end"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("Advance the child upscale graph scene by scene and emit a "
                   "mergeable manifest after the selected range completes.")

    def _explore_dependencies(self, node_id, dynprompt, upstream, parent_ids):
        node_info = dynprompt.get_node(node_id)
        for value in node_info.get("inputs", {}).values():
            if not chain.is_link(value):
                continue
            parent_id = value[0]
            display_id = dynprompt.get_display_node_id(parent_id)
            display_node = dynprompt.get_node(display_id)
            if display_node["class_type"] != "MiniMaxH3ChainUpscaleLoopEnd":
                parent_ids.append(display_id)
            if parent_id not in upstream:
                upstream[parent_id] = []
                self._explore_dependencies(parent_id, dynprompt, upstream, parent_ids)
            upstream[parent_id].append(node_id)

    def _explore_output_nodes(self, dynprompt, upstream, parent_ids):
        try:
            import nodes as comfy_nodes
            mappings = comfy_nodes.NODE_CLASS_MAPPINGS
        except Exception:
            return
        output_nodes = {}
        for node_id, node in dynprompt.get_original_prompt().items():
            class_def = mappings.get(node.get("class_type"))
            if not class_def or not getattr(class_def, "OUTPUT_NODE", False):
                continue
            for value in node.get("inputs", {}).values():
                if chain.is_link(value):
                    output_nodes[node_id] = value
        for parent_id in list(upstream):
            display_id = dynprompt.get_display_node_id(parent_id)
            for output_id, link in output_nodes.items():
                linked_id = link[0]
                if (linked_id in parent_ids and display_id == linked_id and
                        output_id not in upstream[parent_id]):
                    if "." in parent_id:
                        parts = parent_id.split(".")
                        parts[-1] = output_id
                        upstream[parent_id].append(".".join(parts))
                    else:
                        upstream[parent_id].append(output_id)

    def _collect_contained(self, node_id, upstream, contained):
        for child_id in upstream.get(node_id, []):
            if child_id in contained:
                continue
            contained[child_id] = True
            self._collect_contained(child_id, upstream, contained)

    def _recurse(self, flow, next_state, dynprompt, unique_id):
        if chain.GraphBuilder is None:
            raise RuntimeError("H3 Upscale Loop requires ComfyUI GraphBuilder.")
        unique_id = str(unique_id)
        upstream, parent_ids = {}, []
        self._explore_dependencies(unique_id, dynprompt, upstream, parent_ids)
        parent_ids = list(set(parent_ids))
        self._explore_output_nodes(dynprompt, upstream, parent_ids)
        open_node = str(flow[0])
        start_info = dynprompt.get_node(open_node)
        if start_info["class_type"] != "MiniMaxH3ChainUpscaleAdapter":
            raise ValueError("Upscale Loop End flow must connect directly to Upscale Adapter.")
        contained = {unique_id: True, open_node: True}
        self._collect_contained(open_node, upstream, contained)
        graph = chain.GraphBuilder()
        for node_id in contained:
            original = dynprompt.get_node(node_id)
            clone_id = "Recurse" if node_id == unique_id else node_id
            node = graph.node(original["class_type"], clone_id)
            node.set_override_display_id(node_id)
        for node_id in contained:
            original = dynprompt.get_node(node_id)
            clone_id = "Recurse" if node_id == unique_id else node_id
            node = graph.lookup_node(clone_id)
            for key, value in original.get("inputs", {}).items():
                if chain.is_link(value) and value[0] in contained:
                    parent = graph.lookup_node(value[0])
                    node.set_input(key, parent.out(value[1]))
                else:
                    node.set_input(key, value)
        graph.lookup_node(open_node).set_input("initial_state", next_state)
        # Recurse with the already verified immutable document rather than the
        # external manager link; the manager browser is bootstrap-only.
        graph.lookup_node(open_node).set_input(
            "source_manifest", next_state["source_manifest"])
        recurse = graph.lookup_node("Recurse")
        return {"result": tuple(recurse.out(index)
                                for index in range(len(self.RETURN_TYPES))),
                "expand": graph.finalize()}

    def _prepare_next_state(self, state, images, segment, upscaled_latent=None):
        index = int(state["index"])
        if int(segment.get("index", -1)) != index:
            raise ValueError("Upscale Loop End received the wrong scene segment.")
        source = _source_segment(state)
        raw = int(source["raw_frames"])
        delivered = int(source["delivered_frames"])
        trim = raw - delivered
        if int(images.shape[0]) != raw:
            raise ValueError("Upscale Loop End expected %d RAW frames." % raw)
        delivered_images = images[trim:trim + delivered]
        context_length = min(
            int(state["source_manifest"].get("compatibility", {}).get(
                "context_length", 0)), delivered)
        next_state = dict(state)
        next_state.update({
            "index": index + 1,
            "segments": list(state.get("segments", [])) +
                        [_public_upscale_segment(segment)],
            "previous_frames": chain._tensor_cpu_clone(
                delivered_images[-context_length:]) if context_length else
                chain._tensor_cpu_clone(delivered_images[:0]),
            "previous_latent": _cpu_latent(upscaled_latent),
            "previous_context_status": (
                "live previous HQ latent" if upscaled_latent is not None
                else "previous HQ latent unavailable"),
        })
        return next_state

    def _advance(self, flow, next_state, dynprompt=None, unique_id=None):
        index = int(next_state["index"]) - 1
        state = {**next_state, "index": index}
        if index < int(state["end_clip"]):
            return self._recurse(flow, next_state, dynprompt, unique_id)
        complete = index == _source_bounds(state["source_manifest"])[1]
        manifest = _upscale_manifest(state, next_state["segments"], complete)
        paths = _state_profile_paths(state, index)
        from .processing_checkpoint_delete import require_saved_processing_segments
        from .processing_persistence import atomic_json
        with chain.checkpoint_run_lock(chain._output_root(), state["run_name"]):
            require_saved_processing_segments(chain._output_root(),
                next_state["segments"] + [item for item in
                    state["source_manifest"]["segments"] if item.get("processing_source")])
            atomic_json(paths["manifest"] if complete else paths["partial"], manifest)
        manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2,
                                   sort_keys=True)
        return (manifest, manifest_json, next_state["previous_frames"],
                next_state["previous_latent"])


    def end(self, flow, state, images, segment, upscaled_latent=None,
            dynprompt=None, unique_id=None):
        if dynprompt is None or unique_id is None:
            # Preserve direct Python callers; graph execution uses the lazy
            # boundary below so the pending node never owns RGB/latent inputs.
            return self._advance(flow, self._prepare_next_state(
                state, images, segment, upscaled_latent), dynprompt, unique_id)
        graph = chain.GraphBuilder()
        inputs = dynprompt.get_node(str(unique_id))["inputs"]
        prepare = graph.node("MiniMaxH3ChainUpscaleHandoff", "Handoff")
        for name in ("state", "images", "segment", "upscaled_latent"):
            if name in inputs:
                prepare.set_input(name, inputs[name])
        advance = graph.node("MiniMaxH3ChainUpscaleAdvance", "Advance",
                             next_state=prepare.out(0),
                             loop_id=str(unique_id))
        return {"result": tuple(advance.out(i) for i in range(len(self.RETURN_TYPES))),
                "expand": graph.finalize()}


class MiniMaxH3ChainUpscaleHandoff:
    """Finish the tensor-consuming step BEFORE starting the next subgraph."""
    @classmethod
    def INPUT_TYPES(cls):
        schema = MiniMaxH3ChainUpscaleLoopEnd.INPUT_TYPES()
        schema.pop("hidden")
        schema["required"].pop("flow")
        for group in schema.values():
            for _kind, options in group.values():
                options.pop("lazy", None)
        return schema

    RETURN_TYPES = (UPSCALE_STATE_TYPE,)
    OUTPUT_TOOLTIPS = ("Small continuation state after completed scene pixels are released.",)
    FUNCTION = "prepare"
    CATEGORY = "_internal/minimax/upscale"
    DESCRIPTION = "Internal tensor-to-context handoff for Upscale Loop End."

    def prepare(self, state, images, segment, upscaled_latent=None):
        return (MiniMaxH3ChainUpscaleLoopEnd()._prepare_next_state(
            state, images, segment, upscaled_latent),)


class MiniMaxH3ChainUpscaleAdvance:
    """Only the small handoff remains an input while recursion is pending."""
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"next_state": (UPSCALE_STATE_TYPE, {
                                 "tooltip": "Prepared state for the next upscale scene."}),
                             "loop_id": ("STRING", {
                                 "tooltip": "Originating Upscale Loop End node id."})},
                "hidden": {"dynprompt": "DYNPROMPT"}}

    RETURN_TYPES = MiniMaxH3ChainUpscaleLoopEnd.RETURN_TYPES
    OUTPUT_TOOLTIPS = MiniMaxH3ChainUpscaleLoopEnd.OUTPUT_TOOLTIPS
    FUNCTION = "advance"
    CATEGORY = "_internal/minimax/upscale"
    DESCRIPTION = "Internal continuation for Upscale Loop End; no full pixel input."

    def advance(self, next_state, loop_id, dynprompt):
        flow = dynprompt.get_node(loop_id)["inputs"]["flow"]
        return MiniMaxH3ChainUpscaleLoopEnd()._advance(
            flow, next_state, dynprompt, loop_id)


def _validate_upscale_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("format") != "h3_chain_upscale_manifest_v1":
        raise ValueError("H3 Chain Assemble requires a complete upscale manifest.")
    segments = manifest.get("segments") or []
    count = int(manifest.get("clip_count", 0))
    if count < 1 or len(segments) != count:
        raise ValueError("Upscale manifest contains %d/%d scenes." %
                         (len(segments), count))
    total = 0
    source = manifest.get("source_manifest") or {}
    first, last = _source_bounds(source)
    if count != last - first + 1:
        raise ValueError("Upscale manifest does not cover its selected source scenes.")
    if (int(manifest.get("scene_start", first)) != first or
            int(manifest.get("scene_end", last)) != last):
        raise ValueError("Upscale manifest scene bounds do not match its source.")
    for index, segment in enumerate(segments, start=first):
        _verify_upscale_segment(segment, index)
        total += int(segment.get("delivered_frames", 0))
    if total != int(manifest.get("total_delivered_frames", -1)):
        raise ValueError("Upscale manifest delivered-frame total is inconsistent.")
    return segments


def _assembly_manifest(manifest: dict[str, Any],
                       segments: list[dict[str, Any]]) -> dict[str, Any]:
    source = manifest.get("source_manifest")
    if not isinstance(source, dict):
        raise ValueError("Upscale manifest has no embedded source manifest.")
    compatibility = dict(source.get("compatibility") or {})
    compatibility["video_blend_frames"] = 0
    compatibility["segment_crf"] = int(
        manifest["profile_config"].get("segment_crf", 18))
    # Child output dimensions are pixels, not the H3 source latent grid (a
    # custom image backend may legitimately deliver e.g. 1920x1080).
    sizes = {(int(item.get("width", 0)), int(item.get("height", 0)))
             for item in segments}
    if len(sizes) != 1 or min(next(iter(sizes), (0, 0))) < 1:
        raise ValueError("Upscaled scenes must have one valid output resolution before assembly.")
    width, height = next(iter(sizes))
    resolution = {"width": width, "height": height}
    compatibility.update(resolution)
    assembled = []
    for item in segments:
        assembled.append({
            **item,
            "blend_frames": 0,
        })
    assembly = {
        "format": "h3_chain_manifest_v3",
        "run_name": manifest["run_name"],
        "plan_hash": source.get("plan_hash"),
        "prompt_prefix": source.get("prompt_prefix", ""),
        "compatibility": compatibility,
        "clip_count": len(assembled),
        "total_delivered_frames": int(manifest["total_delivered_frames"]),
        "duration_seconds": float(manifest["duration_seconds"]),
        "segments": assembled,
        "archives": source.get("archives", {}),
        "upscale": {
            "profile": manifest["profile"],
            "source_manifest_hash": manifest["source_manifest_hash"],
            "profile_config": manifest["profile_config"],
        },
    }
    if isinstance(source.get("source_timeline"), dict):
        assembly["source_timeline"] = chain._json_document(
            source["source_timeline"])
    if source.get("chapter"):
        assembly["format"] = chain.CHAPTER_MANIFEST_FORMAT
        for key in ("chapter", "scene_start", "scene_end", "source_scene_count", "editorial"):
            if key in source:
                assembly[key] = (chain._json_document(source[key])
                                 if isinstance(source[key], dict) else source[key])
        assembly["chapter"]["resolution"] = resolution
    if source.get("presentation_source"):
        # ALT pictures have already been baked into HQ. Keep the frozen cut's
        # timing but never substitute low-resolution alternates during assembly.
        assembly["editorial"] = {**chain._json_document(source["editorial"]), "replacements": []}
    return assembly


def _write_upscale_final_record(manifest: dict[str, Any],
                                final_path: str) -> None:
    """Publish the child-profile provenance beside a unified assembly."""
    generated_sidecar = os.path.splitext(final_path)[0] + ".generated.wav"
    record = {
        "format": "h3_chain_upscale_final_v1",
        "run_name": manifest["run_name"],
        "profile": manifest["profile"],
        "profile_config": manifest["profile_config"],
        "source_manifest_hash": manifest["source_manifest_hash"],
        "video": chain._relative_output_path(final_path),
        "video_sha256": chain._file_sha256(final_path),
        "frame_count": int(manifest["total_delivered_frames"]),
        "created_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
    }
    if os.path.isfile(generated_sidecar):
        record.update({
            "generated_audio": chain._relative_output_path(
                generated_sidecar),
            "generated_audio_sha256": chain._file_sha256(
                generated_sidecar),
        })
    chain._atomic_json(os.path.splitext(final_path)[0] + ".json", record)


class MiniMaxH3ChainUpscaleMerge:
    DEPRECATED = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "manifest": (chain.MANIFEST_TYPE, {
                    "tooltip": "Legacy compatibility input. New workflows "
                               "connect this manifest directly to H3 Chain "
                               "Assemble."}),
                "audio_source": (["plan", "source", "generated", "none"],
                                 {"default": "plan",
                                  "tooltip": "Final audio policy. plan follows the "
                                             "parent chain's configured mode."}),
                "filename": ("STRING", {
                    "default": "final",
                    "tooltip": "Final MP4 basename; collisions are versioned."}),
                "audio_bitrate": ("INT", {
                    "default": 256, "min": 64, "max": 512,
                    "tooltip": "AAC bitrate in kbps when audio is muxed."}),
            },
            "optional": {
                "source_audio": ("AUDIO", {
                    "tooltip": "Original full source track when audio_source "
                               "resolves to source."}),
                "source_timeline": (chain.SOURCE_TIMELINE_TYPE, {
                    "tooltip": "Optional 0.5 Source Timeline. Usually the "
                               "parent manifest's recovery descriptor is enough."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    OUTPUT_TOOLTIPS = (
        "Absolute path of the verified merged HQ MP4 under the child profile.",
    )
    FUNCTION = "merge"
    OUTPUT_NODE = True
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = ("Deprecated compatibility wrapper. New workflows connect "
                   "Upscale Loop End directly to H3 Chain Assemble.")

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("NaN")

    def merge(self, manifest, audio_source, filename, audio_bitrate,
              source_audio=None, source_timeline=None):
        return chain.MiniMaxH3ChainAssemble().assemble(
            manifest, audio_source, filename, audio_bitrate,
            source_audio=source_audio, source_timeline=source_timeline)


UPSCALE_NODE_CLASS_MAPPINGS = {
    "MiniMaxH3ChainUpscalePixelCurrent": MiniMaxH3ChainUpscalePixelCurrent,
    "MiniMaxH3ChainUpscalePixelConditioning": MiniMaxH3ChainUpscalePixelConditioning,
    "MiniMaxH3ChainUpscaleAdapter": MiniMaxH3ChainUpscaleAdapter,
    "MiniMaxH3ChainUpscaleCurrent": MiniMaxH3ChainUpscaleCurrent,
    "MiniMaxH3ChainDeropeGuard": MiniMaxH3ChainDeropeGuard,
    "MiniMaxH3ChainDeropeFreezeMask": MiniMaxH3ChainDeropeFreezeMask,
    "MiniMaxH3ChainDeropeContinuity": MiniMaxH3ChainDeropeContinuity,
    "MiniMaxH3ChainRecoveredAV": MiniMaxH3ChainRecoveredAV,
    "MiniMaxH3UpscaleReferencePromptOverride": (
        MiniMaxH3UpscaleReferencePromptOverride),
    "MiniMaxH3ChainUpscaleReferenceConditioning": (
        MiniMaxH3ChainUpscaleReferenceConditioning),
    "H3ConditioningSyncFromLatents": H3ConditioningSyncFromLatents,
    "MiniMaxH3ChainPass2Prepare": MiniMaxH3ChainPass2Prepare,
    "MiniMaxH3ChainUpscaleSegmentSave": MiniMaxH3ChainUpscaleSegmentSave,
    "MiniMaxH3ChainUpscaleLoopEnd": MiniMaxH3ChainUpscaleLoopEnd,
    "MiniMaxH3ChainUpscaleHandoff": MiniMaxH3ChainUpscaleHandoff,
    "MiniMaxH3ChainUpscaleAdvance": MiniMaxH3ChainUpscaleAdvance,
    "MiniMaxH3ChainUpscaleMerge": MiniMaxH3ChainUpscaleMerge,
}

UPSCALE_NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ChainUpscalePixelCurrent": "MiniMax H3 Pixel Upscale Current Scene (Experimental)",
    "MiniMaxH3ChainUpscalePixelConditioning": "MiniMax H3 Pixel Upscale Conditioning (Experimental)",
    "MiniMaxH3ChainUpscaleAdapter": "MiniMax H3 Checkpoint Upscale Adapter",
    "MiniMaxH3ChainUpscaleCurrent": "MiniMax H3 Upscale Current Scene",
    "MiniMaxH3ChainDeropeGuard": "MiniMax H3 Chain De-Rope Guard",
    "MiniMaxH3ChainDeropeFreezeMask": (
        "MiniMax H3 Chain De-Rope Freeze Mask"),
    "MiniMaxH3ChainDeropeContinuity": (
        "MiniMax H3 Chain De-Rope Continuity"),
    "MiniMaxH3ChainRecoveredAV": "MiniMax H3 Chain Recovered AV",
    "MiniMaxH3UpscaleReferencePromptOverride": (
        "MiniMax H3 Upscale Reference + Prompt Override"),
    "MiniMaxH3ChainUpscaleReferenceConditioning": (
        "MiniMax H3 Upscale Reference Conditioning"),
    "H3ConditioningSyncFromLatents": "H3 Conditioning Sync From Latents",
    "MiniMaxH3ChainPass2Prepare": "MiniMax H3 Pass-2 AV Prepare",
    "MiniMaxH3ChainUpscaleSegmentSave": "MiniMax H3 Upscale Segment Save",
    "MiniMaxH3ChainUpscaleLoopEnd": "MiniMax H3 Upscale Loop End",
    "MiniMaxH3ChainUpscaleMerge": "MiniMax H3 Upscale Merger (Legacy)",
}
