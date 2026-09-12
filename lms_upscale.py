"""Experimental full-scene LMS guidance for the existing pixel upscale loop.

The enlarged movie is a native H3 guide, not an initial sampled latent or a
Qwen visual reference. Core owns the VAE, AV allocation and guide layout.
"""
from __future__ import annotations

import math


LMS_PROMPT = (
    "Enhance this video with sharp, crisp details while preserving a natural "
    "photorealistic appearance."
)


class MiniMaxH3ChainLMSGuide:
    EXPERIMENTAL = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "state": ("H3_CHAIN_UPSCALE_STATE", {
                    "tooltip": "Current scene from a pixel Upscale Adapter. "
                               "Its RAW frame clock must match the guide exactly."}),
                "clip": ("CLIP", {
                    "tooltip": "H3 Qwen text encoder. Only the enhancement "
                               "caption is encoded; no visual refs/cache are used."}),
                "video_vae": ("VAE", {
                    "tooltip": "H3 video VAE for encoding the enlarged guide."}),
                "prompt": ("STRING", {"default": LMS_PROMPT, "multiline": True,
                    "tooltip": "Enhancement caption for the enlarged video guide. "
                               "Encoded as text only; does not replace the saved scene prompt."}),
            },
            "optional": {
                "images": ("IMAGE", {
                    "tooltip": "One complete enlarged RAW scene from DLSS5 "
                               "or another frame-preserving upscaler. Connect "
                               "images OR video, not both. No trim or resampling."}),
                "video": ("VIDEO", {
                    "tooltip": "Alternative RAW 24 fps guide from a VIDEO "
                               "upscaler. Decoded as ONE FULL SCENE in RAM, "
                               "not streamed. Its audio is ignored."}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("positive", "latent", "width", "height", "raw_frames", "status")
    OUTPUT_TOOLTIPS = (
        "Caption plus a full-length native H3 video guide at frame zero.",
        "Fresh empty joint AV target. Use RandomNoise, denoise=1 and LMS + "
        "Ref2VA Turbo on the model; do not use Pass-2 AV Prepare.",
        "Actual guide width.", "Actual guide height.", "Unchanged RAW frame count.",
        "Guide route, memory warning and audio preservation contract.",
    )
    FUNCTION = "prepare"
    CATEGORY = "conditioning/minimax/context_loop/upscale"
    DESCRIPTION = (
        "Experimental LMS refinement AFTER an image/video upscaler. Encodes "
        "one enlarged RAW scene as a native guide and creates a fresh AV "
        "target at the same size. Does not reconstruct Ref2VA caches, send "
        "images to Qwen, or change timing. Requires full-scene sampling memory; "
        "this is not a tiled CAT replacement with the same memory footprint. "
        "Separate the sampled video latent for decode/save; discard sampled "
        "audio. Upscale Segment Save retains the original checkpoint sound.")

    def prepare(self, state, clip, video_vae, prompt=LMS_PROMPT,
                images=None, video=None):
        from . import chain_nodes as chain
        from .upscale_nodes import _source_segment

        if state.get("profile_config", {}).get("backend") != "pixel":
            raise ValueError("LMS Guide requires Upscale Adapter backend=pixel.")
        if (images is None) == (video is None):
            raise ValueError("LMS Guide needs exactly one input: images OR video.")
        source = _source_segment(state)
        raw = int(source["raw_frames"])
        chain._validate_h3_length(raw, "LMS RAW scene length")
        if not str(prompt).strip():
            raise ValueError("LMS Guide needs a nonempty enhancement caption.")

        if video is not None:
            if not math.isclose(float(video.get_frame_rate()), chain.FPS,
                                rel_tol=0, abs_tol=1e-6):
                raise ValueError("LMS VIDEO guide must be 24 fps; no resampling is performed.")
            # VIDEO.get_frame_count can be an estimate (and some core versions
            # report 1 for untrimmed FFV1). Only decoded frames are authoritative.
            width, height = self._check_canvas(state, *video.get_dimensions())
        else:
            width, height = self._check_images(state, raw, images)

        # Lazy import keeps older ComfyUI installations usable for other nodes.
        try:
            from comfy_extras import nodes_minimax_h3 as native
            from node_helpers import conditioning_set_values
            if not hasattr(native, "MiniMaxH3AddGuide"):
                raise ImportError("Native H3 guide support is unavailable")
        except ImportError as exc:
            raise RuntimeError(
                "LMS Guide requires ComfyUI's native EmptyMiniMaxH3LatentAV "
                "and MiniMaxH3AddGuide nodes. Update ComfyUI to use this experiment."
            ) from exc

        rgb_gib = raw * width * height * 3 * 4 / 1024 ** 3
        chain._LOG.info(
            "H3 LMS scene %d: expecting %d RAW frames at %dx%d as a full-scene "
            "guide (~%.2f GiB RGB float32 alone, plus VAE/sampling memory).",
            state["index"], raw, width, height, rgb_gib)
        if video is not None:
            images = video.get_components().images
        width, height = self._check_images(state, raw, images)
        expected = (1, 24, (raw - 5) // 17 * 5 + 2, height // 16, width // 16)
        # Native AddGuide's unconditional Lanczos resize takes a PIL/uint8
        # round-trip, even at identical dimensions. Geometry is already exact
        # here: encode unchanged pixels and use its native keyframe contract.
        guide = video_vae.encode(images)
        if tuple(guide.shape) != expected:
            raise ValueError(
                "LMS guide encoded %s; expected %s. Use the matching H3 video "
                "VAE; no frame truncation or latent resizing is permitted."
                % (tuple(guide.shape), expected))
        latent = native.EmptyMiniMaxH3LatentAV.execute(width, height, raw)[0]
        positive = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
        positive = conditioning_set_values(positive, {"minimax_keyframes": [{
            "resolved_frame_index": 0, "latent": guide,
        }]})
        status = (
            "Experimental LMS scene %d: %d RAW frames, %dx%d; native video "
            "guide + fresh AV target; caption-only Qwen; no reference cache; "
            "discard sampled audio, Segment Save preserves source audio; "
            "full-scene memory required" % (state["index"], raw, width, height))
        chain._LOG.info("H3 %s", status)
        return positive, latent, width, height, raw, status

    @staticmethod
    def _check_canvas(state, width, height):
        if width < 32 or height < 32 or width % 32 or height % 32:
            raise ValueError("LMS guide width and height must be positive multiples of 32.")
        completed = state.get("segments") or []
        if completed and (int(completed[0]["width"]), int(completed[0]["height"])) != (width, height):
            raise ValueError(
                "LMS guide size differs from completed scenes in this profile. "
                "Choose a new upscale profile for a different output size.")
        return width, height

    @classmethod
    def _check_images(cls, state, raw, images):
        if len(images.shape) != 4 or images.shape[-1] != 3:
            raise ValueError("LMS guide must be an RGB IMAGE batch [frames, height, width, 3].")
        count, height, width, _ = map(int, images.shape)
        if count != raw:
            raise ValueError(
                "LMS guide has %d frames; scene %d needs %d RAW frames, "
                "including its repeated prefix. Do not trim or resample."
                % (count, state["index"], raw))
        return cls._check_canvas(state, width, height)
