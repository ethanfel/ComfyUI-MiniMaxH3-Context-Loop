"""Audio-only second passes that retain the original chain audio protections."""

import torch

from .masked_context import _existing_mask_streams
from .nodes import _streams_from_latent


def _av_streams(latent):
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError("H3 Chain Audio Refine requires a joint AV latent.")
    streams = _streams_from_latent(latent)
    if (len(streams) != 2 or not all(torch.is_tensor(s) for s in streams)
            or streams[0].ndim != 5
            or streams[1].ndim != 4 or streams[1].shape[2] != 2
            or streams[0].shape[0] != 1 or streams[1].shape[0] != 1):
        raise ValueError(
            "H3 Chain Audio Refine requires video and stereo audio latents "
            "with batch size 1.")
    return streams


def refinement_mask(latent, context_latent):
    """Freeze finished video; compose audio masks without reopening locked ticks.

    context_latent is the *pre-sampling* target from Apply Scene Context, not
    the predecessor checkpoint. A sampler may discard its input noise_mask,
    so the sampled latent alone cannot be the authority for audio protection.
    """
    video, audio = _av_streams(latent)
    context_video, context_audio = _av_streams(context_latent)
    if (video.shape != context_video.shape or audio.shape != context_audio.shape):
        raise ValueError(
            "H3 Chain Audio Refine: context_latent must be the same scene's "
            "pre-sampling target at the same resolution and length.")
    _, audio_mask = _existing_mask_streams(context_latent, video, audio)
    if "noise_mask" in latent:
        _, sampled_mask = _existing_mask_streams(latent, video, audio)
        masks = (audio_mask, sampled_mask)
    else:
        masks = (audio_mask,)
    for mask in masks:
        if not torch.isfinite(mask).all() or torch.any((mask < 0) | (mask > 1)):
            raise ValueError(
                "H3 Chain Audio Refine: audio masks must be finite and in [0, 1].")
    if len(masks) == 2:
        audio_mask = torch.minimum(audio_mask, sampled_mask.to(audio_mask))
    video_mask = torch.zeros(
        (1, 1, *video.shape[2:]), device=video.device, dtype=torch.float32)
    return video_mask, audio_mask.to(device=audio.device)


class MiniMaxH3ChainAudioRefineSampler:
    @classmethod
    def INPUT_TYPES(cls):
        import comfy.samplers

        return {"required": {
            "model": ("MODEL", {
                "tooltip": "Refinement model branched BEFORE Turbo LoRA and "
                           "Apply Scene Context's Drift patch. Optional Frozen "
                           "Video Cache may fall back to uncached execution "
                           "with protected audio."}),
            "positive": ("CONDITIONING",),
            "negative": ("CONDITIONING",),
            "latent": ("LATENT", {
                "tooltip": "Finished joint AV latent from Sample Video + Audio."}),
            "context_latent": ("LATENT", {
                "tooltip": "Apply Scene Context's latent output, before the "
                           "first sampler. Supplies the original carried/source-"
                           "audio protection mask; not a previous scene's "
                           "checkpoint."}),
            "seed": ("INT", {"default": 0, "min": 0,
                             "max": 0xffffffffffffffff,
                             "control_after_generate": True}),
            "steps": ("INT", {"default": 6, "min": 1, "max": 100}),
            "cfg": ("FLOAT", {"default": 1.0, "min": 0.0,
                              "max": 100.0, "step": 0.1}),
            "sampler_name": (comfy.samplers.KSampler.SAMPLERS,
                             {"default": "euler"}),
            "scheduler": (comfy.samplers.KSampler.SCHEDULERS,
                          {"default": "simple"}),
            "audio_denoise": ("FLOAT", {"default": 0.5, "min": 0.01,
                                        "max": 1.0, "step": 0.01}),
        }}

    RETURN_TYPES = ("LATENT",)
    FUNCTION = "refine"
    CATEGORY = "sampling/minimax/context_loop"
    DESCRIPTION = (
        "Refine sampled audio while freezing video and retaining the original "
        "Chain Context audio mask, including carried prefixes, feathering and "
        "source locks. Connect the result to both decoders, Segment Save and "
        "Loop End. Does not require the third-party AudioRefine pack.")

    def refine(self, model, positive, negative, latent, context_latent, seed,
               steps, cfg, sampler_name, scheduler, audio_denoise):
        import comfy.nested_tensor
        import comfy.sample
        import comfy.utils
        import latent_preview

        # A video Drift/Differential Diffusion mask callback would override
        # this audio-only contract. Never mutate or unpatch the caller's model.
        options = getattr(model, "model_options", {})
        if (options.get("denoise_mask_function") is not None
                or options.get("h3_context_loop_drift_control_av_recipe") is not None):
            raise ValueError(
                "H3 Chain Audio Refine needs a model branch before Apply Scene "
                "Context/Drift-Control and other dynamic denoise-mask patches.")
        video, audio = _av_streams(latent)
        video_mask, audio_mask = refinement_mask(latent, context_latent)
        out = dict(latent)
        out.pop("noise_mask", None)
        out.pop("h3_context_loop_drift_control_av_prefix", None)
        if not torch.any(audio_mask):
            # Fully source-locked audio and frozen video: nothing to sample.
            return (out,)
        from .masked_context import _require_h3_mask_support

        _require_h3_mask_support()
        samples = comfy.nested_tensor.NestedTensor((video, audio))
        noise = comfy.sample.prepare_noise(samples, seed, latent.get("batch_index"))
        result = comfy.sample.sample(
            model, noise, steps, cfg, sampler_name, scheduler,
            positive, negative, samples, denoise=audio_denoise,
            noise_mask=comfy.nested_tensor.NestedTensor((video_mask, audio_mask)),
            callback=latent_preview.prepare_callback(model, steps),
            disable_pbar=not comfy.utils.PROGRESS_BAR_ENABLED, seed=seed)
        _, refined_audio = _av_streams({"samples": result})
        if refined_audio.shape != audio.shape:
            raise ValueError("H3 Chain Audio Refine sampler changed the audio shape.")
        # Keep the exact original bits at hard locks, including when a sampler
        # does its final denoised-output conversion at a different precision.
        refined_audio = torch.where(
            audio_mask == 0, audio, refined_audio.to(audio))
        out["samples"] = comfy.nested_tensor.NestedTensor((video, refined_audio))
        return (out,)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3ChainAudioRefineSampler": MiniMaxH3ChainAudioRefineSampler,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3ChainAudioRefineSampler": "MiniMax H3 Chain Audio Refine Sampler",
}
