#!/usr/bin/env python3
"""CPU regression for recursive H3 masked AV target-prefix construction."""

import asyncio
import functools
import importlib.util
import json
import os
import sys
import types

import torch
import torch.nn.functional as functional


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "h3_mask_test_pkg"


class NestedTensor:
    def __init__(self, parts):
        self.parts = tuple(parts)

    def unbind(self):
        return list(self.parts)


def _install_comfy_stubs():
    comfy = types.ModuleType("comfy")
    comfy.__path__ = []
    utils = types.ModuleType("comfy.utils")

    def common_upscale(samples, width, height, _method, _crop):
        return functional.interpolate(
            samples, size=(height, width), mode="bilinear",
            align_corners=False)

    utils.common_upscale = common_upscale
    utils.unpack_latents = lambda value, _shapes: value.unbind()
    nested = types.ModuleType("comfy.nested_tensor")
    nested.NestedTensor = NestedTensor
    comfy.utils = utils
    comfy.nested_tensor = nested
    sys.modules["comfy"] = comfy
    sys.modules["comfy.utils"] = utils
    sys.modules["comfy.nested_tensor"] = nested

    conds = types.ModuleType("comfy.conds")

    class CONDRegular:
        def __init__(self, value):
            self.cond = value

    conds.CONDRegular = CONDRegular
    comfy.conds = conds
    sys.modules["comfy.conds"] = conds

    ldm = types.ModuleType("comfy.ldm")
    ldm.__path__ = []
    minimax = types.ModuleType("comfy.ldm.minimax")
    minimax.__path__ = []
    model = types.ModuleType("comfy.ldm.minimax.model")

    class PackedLayout:
        pass

    model.PackedLayout = PackedLayout
    model.FRAME_RESCALE = 5.0 / 3.0
    comfy.ldm = ldm
    ldm.minimax = minimax
    minimax.model = model
    sys.modules["comfy.ldm"] = ldm
    sys.modules["comfy.ldm.minimax"] = minimax
    sys.modules["comfy.ldm.minimax.model"] = model

    model_base = types.ModuleType("comfy.model_base")

    class MiniMaxH3:
        def extra_conds(self, **_kwargs):
            return {}

    model_base.MiniMaxH3 = MiniMaxH3
    comfy.model_base = model_base
    sys.modules["comfy.model_base"] = model_base

    samplers = types.ModuleType("comfy.samplers")

    class KSamplerX0Inpaint:
        """Pre-989e7a9 sampler: scale does not receive denoise_mask."""

        def __call__(self, x, sigma, denoise_mask, model_options=None,
                     seed=None):
            options = model_options or {}
            mask_function = options.get("denoise_mask_function")
            if mask_function is not None:
                denoise_mask = mask_function(
                    sigma, denoise_mask, extra_options={})
            injected = self.inner_model.inner_model.scale_latent_inpaint(
                x=x, sigma=sigma, noise=self.noise,
                latent_image=self.latent_image)
            return x * denoise_mask + injected * (1.0 - denoise_mask)

    samplers.KSamplerX0Inpaint = KSamplerX0Inpaint
    comfy.samplers = samplers
    sys.modules["comfy.samplers"] = samplers

    helpers = types.ModuleType("node_helpers")
    helpers.conditioning_set_values = lambda value, *_args, **_kwargs: value
    sys.modules["node_helpers"] = helpers

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_output_directory = lambda: "/tmp"
    sys.modules["folder_paths"] = folder_paths

    safe = types.ModuleType("safetensors")
    safe_torch = types.ModuleType("safetensors.torch")
    safe_torch.load_file = None
    safe_torch.save_file = None
    safe.torch = safe_torch
    sys.modules["safetensors"] = safe
    sys.modules["safetensors.torch"] = safe_torch


def _load(name):
    path = os.path.join(ROOT, "%s.py" % name)
    spec = importlib.util.spec_from_file_location(
        "%s.%s" % (PACKAGE, name), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    _install_comfy_stubs()
    package = types.ModuleType(PACKAGE)
    package.__path__ = [ROOT]
    sys.modules[PACKAGE] = package
    _load("patch_layout")
    _load("patch_payload")
    nodes = _load("nodes")
    masked = _load("masked_context")
    masked._require_h3_mask_support = lambda: None
    master_audio = _load("master_audio_context")
    master_audio.require_h3_mask_support = lambda _operation: True

    # The fixed AV recipe reproduces the observed 1376x768 -> 1152x640
    # experiment in latent space without changing time or final geometry.
    proxy_source = torch.randn((1, 4, 3, 48, 86), dtype=torch.float32)
    proxy_source_copy = proxy_source.clone()
    proxy_result, proxy_shape = masked._latent_context_spatial_proxy(
        proxy_source)
    assert proxy_shape == (40, 72)
    assert proxy_result.shape == proxy_source.shape
    assert not torch.equal(proxy_result, proxy_source)
    assert torch.equal(proxy_source, proxy_source_copy)

    target_frames = 192
    target_video_steps = 57
    target_audio_steps = 320
    assert nodes._pixel_frames(target_video_steps) == target_frames
    target_video = torch.zeros((1, 16, target_video_steps, 2, 3))
    target_audio = torch.zeros((1, 32, 2, target_audio_steps))
    target = {"samples": NestedTensor((target_video, target_audio))}

    previous_audio = torch.arange(
        1 * 32 * 2 * target_audio_steps, dtype=torch.float32).reshape(
            1, 32, 2, target_audio_steps)
    previous_video = torch.arange(
        target_video_steps, dtype=torch.float32).reshape(
            1, 1, target_video_steps, 1, 1).expand_as(target_video).clone()
    previous = {"samples": NestedTensor((
        previous_video, previous_audio,
    ))}
    frames = torch.zeros((64, 32, 48, 3), dtype=torch.float32)
    for index in range(int(frames.shape[0])):
        frames[index].fill_(float(index))

    class UnexpectedVideoVAE:
        def encode(self, _images):
            raise AssertionError(
                "generated continuation must not re-encode decoded frames")

    refs = [{"kind": "image", "latent_h": 2, "latent_w": 3}]
    conditioning = [["embedding", {
        "minimax_refs": refs,
        "minimax_keyframes": [
            {"resolved_frame_index": 0, "name": "conflicting first"},
            {"resolved_frame_index": 191, "name": "retained last"},
        ],
    }]]

    class LockedAudioVAE:
        audio_sample_rate = 32000

        def encode(self, waveform):
            assert waveform.ndim == 3
            assert int(waveform.shape[-1]) == 2
            steps = torch.arange(
                target_audio_steps + 1, dtype=torch.float32).reshape(
                    1, 1, 1, -1)
            return steps.expand(
                1, 32, 2, target_audio_steps + 1).clone()

    locked_source_window = {
        "waveform": torch.zeros((
            1, 1, round(target_frames / 24.0 * 32000)),
            dtype=torch.float32),
        "sample_rate": 32000,
    }
    locked_target = masked.apply_locked_source_audio_target(
        target, LockedAudioVAE(), locked_source_window)
    locked_video, locked_audio = locked_target["samples"].unbind()
    locked_video_mask, locked_audio_mask = (
        locked_target["noise_mask"].unbind())
    assert torch.equal(locked_video, target_video)
    assert tuple(locked_audio.shape) == tuple(target_audio.shape)
    assert torch.all(locked_audio[..., -1] == target_audio_steps - 1)
    assert torch.all(locked_video_mask == 1.0)
    assert not torch.count_nonzero(locked_audio_mask)
    assert "noise_mask" not in target
    assert not torch.count_nonzero(target_audio)

    out_conditioning, out, trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
    )

    assert trim == 39
    video, audio = out["samples"].unbind()
    video_mask, audio_mask = out["noise_mask"].unbind()
    prefix_video_steps = 12
    prefix_audio_steps = 65
    assert nodes._pixel_frames(prefix_video_steps) == 39
    assert torch.equal(
        video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert not torch.count_nonzero(video[:, :, prefix_video_steps:])
    assert torch.equal(
        audio[..., :prefix_audio_steps],
        previous_audio[..., -prefix_audio_steps:],
    )
    assert not torch.count_nonzero(audio[..., prefix_audio_steps:])
    assert not torch.count_nonzero(video_mask[:, :, :prefix_video_steps])
    assert torch.all(video_mask[:, :, prefix_video_steps:] == 1.0)
    assert not torch.count_nonzero(audio_mask[..., :prefix_audio_steps])
    assert torch.all(audio_mask[..., prefix_audio_steps:] == 1.0)

    class ColorVideoVAE:
        def decode(self, value):
            level = value[:, :3].float().mean()
            return torch.full((39, 32, 48, 3), float(level))

        def encode(self, value):
            level = value[..., :3].float().mean() + 0.25
            return torch.full((1, 16, 12, 2, 3), float(level))

    color_previous_video = torch.full_like(previous_video, 0.5)
    color_previous = {"samples": NestedTensor((
        color_previous_video, previous_audio,
    ))}
    anchor_stats = {
        "version": "h3_latent_color_stats_v1",
        "luma_percentiles": [100.0, 100.0, 100.0],
        "saturation_percentiles": [80.0, 80.0, 80.0],
        "sampled_frames": 12,
    }
    current_stats = {
        "version": "h3_latent_color_stats_v1",
        "luma_percentiles": [112.0, 112.0, 112.0],
        "saturation_percentiles": [80.0, 80.0, 80.0],
        "sampled_frames": 12,
    }
    _, color_out, color_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=ColorVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=color_previous,
        latent_color_carry={
            "anchor_stats": anchor_stats,
            "current_stats": current_stats,
        },
    )
    color_video, color_audio = color_out["samples"].unbind()
    assert color_trim == 39
    assert torch.equal(
        color_video[:, :, 0], color_previous_video[:, :, -12])
    assert torch.all(
        color_video[:, :, 11] < color_previous_video[:, :, -1])
    assert torch.equal(
        color_audio[..., :prefix_audio_steps],
        previous_audio[..., -prefix_audio_steps:])
    assert torch.equal(color_previous_video, torch.full_like(
        color_previous_video, 0.5))

    _, proxy_out, proxy_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        context_spatial_proxy="latent_5_6",
    )
    proxy_video, proxy_audio = proxy_out["samples"].unbind()
    assert proxy_trim == 39
    # This tiny fixture only changes one latent spatial axis, while proving
    # that the paired sound is copied exactly and never spatially filtered.
    assert proxy_video.shape == video.shape
    assert torch.equal(
        proxy_audio[..., :prefix_audio_steps],
        previous_audio[..., -prefix_audio_steps:])

    # Detail AV perturbs only a disposable copy of the carried 12-step video
    # prefix. Audio, masks, target latent, and accepted predecessor stay exact.
    previous_video_copy = previous_video.clone()
    previous_audio_copy = previous_audio.clone()
    detail_seed = 912345
    _, detail_a, detail_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        detail_video_taper=True,
        detail_video_seed=detail_seed,
    )
    _, detail_b, _ = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        detail_video_taper=True,
        detail_video_seed=detail_seed,
    )
    _, detail_c, _ = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        detail_video_taper=True,
        detail_video_seed=detail_seed + 1,
    )
    detail_video_a, detail_audio_a = detail_a["samples"].unbind()
    detail_video_b, detail_audio_b = detail_b["samples"].unbind()
    detail_video_c, _ = detail_c["samples"].unbind()
    detail_video_mask, detail_audio_mask = detail_a["noise_mask"].unbind()
    assert detail_trim == 39
    assert masked._detail_av_alpha(0, 12) == 0.30
    assert abs(masked._detail_av_alpha(8, 12) - 0.225) < 1e-9
    assert abs(masked._detail_av_alpha(9, 12) - 0.15) < 1e-9
    assert abs(masked._detail_av_alpha(10, 12) - 0.075) < 1e-9
    assert masked._detail_av_alpha(11, 12) == 0.0
    assert torch.equal(detail_video_a, detail_video_b)
    assert not torch.equal(detail_video_a, detail_video_c)
    assert not torch.equal(
        detail_video_a[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert not torch.count_nonzero(
        detail_video_a[:, :, prefix_video_steps:])
    assert torch.equal(detail_audio_a, audio)
    assert torch.equal(detail_audio_b, audio)
    assert torch.equal(detail_video_mask, video_mask)
    assert torch.equal(detail_audio_mask, audio_mask)
    assert torch.equal(previous_video, previous_video_copy)
    assert torch.equal(previous_audio, previous_audio_copy)
    assert not torch.count_nonzero(target_video)
    assert not torch.count_nonzero(target_audio)

    _, feathered, feathered_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        temporal_feather=True,
    )
    feathered_video_mask, feathered_audio_mask = (
        feathered["noise_mask"].unbind())
    assert feathered_trim == 39
    assert not torch.count_nonzero(feathered_video_mask[:, :, :8])
    assert torch.allclose(
        feathered_video_mask[0, 0, 8:12, 0, 0],
        torch.linspace(0.85, 0.95, 4),
    )
    assert torch.all(feathered_video_mask[:, :, 12:] == 1.0)
    assert not torch.count_nonzero(feathered_audio_mask[..., :57])
    assert torch.allclose(
        feathered_audio_mask[0, 0, 0, 57:65],
        torch.linspace(0.85, 0.95, 8),
    )
    assert torch.all(feathered_audio_mask[..., 65:] == 1.0)
    feathered_video, feathered_audio = feathered["samples"].unbind()
    assert torch.equal(
        feathered_video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert torch.equal(
        feathered_audio[..., :prefix_audio_steps],
        previous_audio[..., -prefix_audio_steps:],
    )

    _, audio_feathered, audio_feathered_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        audio_only_feather=True,
    )
    audio_feathered_video_mask, audio_feathered_audio_mask = (
        audio_feathered["noise_mask"].unbind())
    assert audio_feathered_trim == 39
    assert not torch.count_nonzero(
        audio_feathered_video_mask[:, :, :prefix_video_steps])
    assert torch.all(
        audio_feathered_video_mask[:, :, prefix_video_steps:] == 1.0)
    assert not torch.count_nonzero(audio_feathered_audio_mask[..., :57])
    indices = torch.arange(1, 9, dtype=torch.float32)
    expected_audio_release = 0.5 - 0.5 * torch.cos(torch.pi * indices / 8.0)
    assert torch.allclose(
        audio_feathered_audio_mask[0, 0, 0, 57:65],
        expected_audio_release,
    )
    assert audio_feathered_audio_mask[0, 0, 0, 64] == 1.0
    assert torch.all(audio_feathered_audio_mask[..., 65:] == 1.0)

    _, source_driven, source_driven_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        audio_only_feather=True,
        preserve_audio_prefix=False,
    )
    source_video, source_audio = source_driven["samples"].unbind()
    source_video_mask, source_audio_mask = (
        source_driven["noise_mask"].unbind())
    assert source_driven_trim == 39
    assert torch.equal(
        source_video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert not torch.count_nonzero(source_audio)
    assert not torch.count_nonzero(
        source_video_mask[:, :, :prefix_video_steps])
    assert torch.all(source_audio_mask == 1.0)

    # A source-driven/video-only AV test may use H3's five-frame video run.
    # The two copied video steps are protected while the entire audio target
    # remains open; enabling predecessor-audio carry keeps the 39f minimum.
    _, five_frame_av, five_frame_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=target,
        previous_frames=frames,
        context_length=5,
        crop="disabled",
        previous_latent=previous,
        preserve_audio_prefix=False,
    )
    five_video, five_audio = five_frame_av["samples"].unbind()
    five_video_mask, five_audio_mask = (
        five_frame_av["noise_mask"].unbind())
    assert five_frame_trim == 5
    assert nodes._pixel_frames(2) == 5
    assert torch.equal(five_video[:, :, :2], previous_video[:, :, -2:])
    assert not torch.count_nonzero(five_video_mask[:, :, :2])
    assert torch.all(five_video_mask[:, :, 2:] == 1.0)
    assert not torch.count_nonzero(five_audio)
    assert torch.all(five_audio_mask == 1.0)
    try:
        masked.apply_masked_prefix(
            conditioning=conditioning,
            vae=UnexpectedVideoVAE(),
            latent=target,
            previous_frames=frames,
            context_length=5,
            crop="disabled",
            previous_latent=previous,
            preserve_audio_prefix=True,
        )
    except ValueError as exc:
        assert "at least 39" in str(exc)
    else:
        raise AssertionError(
            "five-frame masked AV accepted predecessor-audio carry")

    _, locked_av, locked_av_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=locked_target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        audio_only_feather=True,
        preserve_audio_prefix=False,
    )
    locked_av_video, locked_av_audio = locked_av["samples"].unbind()
    locked_av_video_mask, locked_av_audio_mask = (
        locked_av["noise_mask"].unbind())
    assert locked_av_trim == 39
    assert torch.equal(
        locked_av_video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert torch.equal(locked_av_audio, locked_audio)
    assert not torch.count_nonzero(
        locked_av_video_mask[:, :, :prefix_video_steps])
    assert not torch.count_nonzero(locked_av_audio_mask)

    existing_video_mask = torch.ones_like(target_video[:, :1])
    existing_video_mask[:, :, 8:12, 0, 0] = 0.1
    existing_audio_mask = torch.ones(
        (1, 1, int(target_audio.shape[2]), target_audio_steps))
    existing_audio_mask[..., 42:65] = 0.25
    pre_masked_target = {
        "samples": target["samples"],
        "noise_mask": NestedTensor((
            existing_video_mask, existing_audio_mask,
        )),
    }
    _, composed_feathered, _ = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=UnexpectedVideoVAE(),
        latent=pre_masked_target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=previous,
        temporal_feather=True,
    )
    composed_video_mask, composed_audio_mask = (
        composed_feathered["noise_mask"].unbind())
    assert torch.allclose(
        composed_video_mask[0, 0, 8:12, 0, 0],
        torch.tensor([0.1, 0.1, 0.1, 0.1]),
    )
    assert torch.allclose(
        composed_video_mask[0, 0, 8:12, 0, 1],
        torch.linspace(0.85, 0.95, 4),
    )
    assert torch.allclose(
        composed_audio_mask[0, 0, 0, 57:65],
        torch.minimum(
            torch.linspace(0.85, 0.95, 8),
            torch.full((8,), 0.25),
        ),
    )

    metadata = out_conditioning[0][1]
    assert metadata["minimax_refs"] is refs
    assert [item["name"] for item in metadata["minimax_keyframes"]] == [
        "retained last"]
    assert not torch.count_nonzero(target_video)
    assert not torch.count_nonzero(target_audio)
    print(
        "masked prefix: 39 frames -> 12 video / 65 audio steps; target "
        "streams cloned, generated AV latent tails copied directly, future "
        "generated, refs retained")
    print(
        "feathered AV: first 8 video / 57 audio steps protected, final "
        "4 video / 8 audio prefix steps use a 0.85..0.95 denoise handoff")
    print(
        "Detail AV: deterministic 0.30 -> 0.00 video-only latent taper; "
        "audio, masks, target, and predecessor remain exact")

    class VideoVAE:
        def __init__(self):
            self.calls = 0

        def encode(self, images):
            self.calls += 1
            steps = max(1, (int(images.shape[0]) - 5) // 17 * 5 + 2)
            value = float(images[-1, 0, 0, 0])
            return torch.full((1, 16, steps, 2, 3), value)

    class ImportedAudioVAE:
        audio_sample_rate = 32000

        def encode(self, _waveform):
            return torch.full((1, 32, 2, prefix_audio_steps), 17.0)

    imported_video_vae = VideoVAE()
    imported_target = {"samples": NestedTensor((
        torch.zeros_like(target_video), torch.zeros_like(target_audio),
    ))}
    _, imported_out, imported_trim = masked.apply_masked_prefix(
        conditioning=conditioning,
        vae=imported_video_vae,
        latent=imported_target,
        previous_frames=frames,
        context_length=39,
        crop="disabled",
        previous_latent=None,
        audio_vae=ImportedAudioVAE(),
        previous_audio={
            "waveform": torch.zeros((1, 2, 60000)),
            "sample_rate": 32000,
        },
    )
    imported_video, imported_audio = imported_out["samples"].unbind()
    assert imported_trim == 39
    assert imported_video_vae.calls == 1
    assert torch.all(imported_video[:, :, :prefix_video_steps] == 63.0)
    assert torch.all(imported_audio[..., :prefix_audio_steps] == 17.0)
    print("masked prefix: imported video/audio retain the VAE fallback path")

    chain = _load("chain_nodes")
    assert chain._context_spatial_proxy_size(1376, 768) == (1152, 640)
    color_history = chain._state_latent_color_carry({
        "segments": [
            {"index": 1, "latent_color_stats": anchor_stats},
            {"index": 2, "latent_color_stats": current_stats},
        ],
    })
    assert color_history == {
        "anchor_stats": anchor_stats,
        "current_stats": current_stats,
        "anchor_scene": 1,
        "source_scene": 2,
    }

    # Guide must reproduce the mixed-resolution operation in latent space:
    # full saved 48x86 predecessor -> complete 40x72 decode -> delivered tail.
    low_grid_video = torch.randn(
        (1, 4, 7, 48, 86), dtype=torch.float32)
    low_grid_source_copy = low_grid_video.clone()
    low_grid_previous = {"samples": NestedTensor((
        low_grid_video, torch.zeros((1, 32, 2, 37)),
    ))}

    class LowGridDecodeVAE:
        def __init__(self):
            self.shape = None
            self.input = None

        def decode(self, latent):
            self.shape = tuple(latent.shape)
            self.input = latent.detach().clone()
            decoded = torch.zeros((22, 2, 3, 3), dtype=torch.float32)
            for frame_index in range(22):
                decoded[frame_index].fill_(float(frame_index))
            return decoded

    low_grid_vae = LowGridDecodeVAE()
    low_grid_result, low_grid_size = chain._low_grid_guide_context({
        "previous_latent": low_grid_previous,
        "segments": [{
            "index": 1, "raw_frames": 22, "delivered_frames": 17,
        }],
    }, low_grid_vae, 5, 1376, 768)
    assert low_grid_vae.shape == (1, 4, 7, 40, 72)
    assert low_grid_size == (1152, 640)
    assert tuple(low_grid_result.shape) == (5, 2, 3, 3)
    assert float(low_grid_result[0, 0, 0, 0]) == 17.0
    assert float(low_grid_result[-1, 0, 0, 0]) == 21.0
    assert torch.equal(low_grid_video, low_grid_source_copy)

    native_low_grid_video = torch.randn(
        (1, 4, 7, 40, 72), dtype=torch.float32)
    native_low_grid_vae = LowGridDecodeVAE()
    chain._low_grid_guide_context({
        "previous_latent": {"samples": NestedTensor((
            native_low_grid_video, torch.zeros((1, 32, 2, 37)),
        ))},
        "segments": [{
            "index": 1, "raw_frames": 22, "delivered_frames": 17,
        }],
    }, native_low_grid_vae, 5, 1376, 768)
    assert torch.equal(native_low_grid_vae.input, native_low_grid_video)

    class ReviewInterrupted(BaseException):
        pass

    async def assert_review_interrupt_poll():
        future = asyncio.get_running_loop().create_future()
        original_check = chain._throw_if_review_interrupted

        def interrupt():
            raise ReviewInterrupted()

        chain._throw_if_review_interrupted = interrupt
        try:
            try:
                await chain._await_review_decision(future, 0)
            except ReviewInterrupted:
                pass
            else:
                raise AssertionError(
                    "Review Gate ignored the ComfyUI interruption check")
            assert not future.cancelled()
        finally:
            chain._throw_if_review_interrupted = original_check
            future.cancel()

    asyncio.run(assert_review_interrupt_poll())
    print("review gate: indefinite wait honors ComfyUI Stop/Cancel")
    plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "masked_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "masked_av",
    )
    assert plan["compatibility"]["continuation_mode"] == "masked_av"
    assert plan["shots"][1]["delivered_frames"] == 153
    assert "context=39/masked_av" in plan["summary"]
    assert "continuation_mode" not in chain._history_contract(
        plan, 1)["compatibility"]
    assert chain._legacy_history_contract(plan, 1)["compatibility"][
        "continuation_mode"] == "masked_av"
    feathered_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "feathered_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "feathered_av",
    )
    assert feathered_plan["compatibility"][
        "continuation_mode"] == "feathered_av"
    assert "context=39/feathered_av" in feathered_plan["summary"]
    audio_feathered_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "audio_feathered_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "audio_feathered_av",
    )
    assert audio_feathered_plan["compatibility"][
        "continuation_mode"] == "audio_feathered_av"
    assert "context=39/audio_feathered_av" in audio_feathered_plan["summary"]
    detail_av_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "detail_av_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "tapered_av",
    )
    assert detail_av_plan["compatibility"][
        "continuation_mode"] == "tapered_av"
    assert "context=39/tapered_av" in detail_av_plan["summary"]
    detail_dependency = chain._scene_dependency_record(
        detail_av_plan, 2, None)
    assert detail_dependency["scopes"]["incoming_boundary"][
        "detail_av_recipe"] == chain.DETAIL_AV_RECIPE

    assert chain.DISPOSABLE_PREFIX_CONTINUATION_MODES == frozenset((
        "tapered_av", "drift_control_av", "color_stable_drift_av"))
    clean_blend_source = frames.clone()
    noisy_blend = torch.full((12, 32, 48, 3), -7.0)
    clean_prefix = chain._detail_av_clean_blend_prefix(
        {"previous_frames": clean_blend_source}, noisy_blend, 5)
    assert torch.equal(clean_prefix, clean_blend_source[-5:])
    assert clean_prefix.untyped_storage().data_ptr() == (
        clean_blend_source.untyped_storage().data_ptr())
    assert torch.all(noisy_blend == -7.0)
    assert torch.equal(clean_blend_source, frames)
    migrated_av_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192,
             "continuation_mode": "feathered_av_rgb"},
        ]}),
        "migrated_av_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "feathered_av_rgb",
    )
    assert migrated_av_plan["compatibility"][
        "continuation_mode"] == "feathered_av"
    assert migrated_av_plan["shots"][1][
        "continuation_mode"] == "feathered_av"
    assert "context=39/feathered_av" in migrated_av_plan["summary"]
    guide_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "guide_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "guide",
    )
    assert "continuation_mode" not in guide_plan["compatibility"]
    # The Plan-wide selector chooses how the next scene consumes an immutable
    # predecessor. It must not invalidate that predecessor's resume history.
    assert chain._history_hash(plan, 1) == chain._history_hash(guide_plan, 1)
    assert chain._history_hash(plan, 2) == chain._history_hash(guide_plan, 2)
    legacy_masked_hash = chain._fingerprint(
        chain._legacy_history_contract(plan, 1))
    assert chain._accepted_resume_history_hash(guide_plan, 1, {
        "history_hash": legacy_masked_hash,
        "compatibility": dict(plan["compatibility"]),
    }) == legacy_masked_hash
    legacy_guide_hash = chain._fingerprint(
        chain._legacy_history_contract(guide_plan, 1))
    assert chain._accepted_resume_history_hash(plan, 1, {
        "history_hash": legacy_guide_hash,
        "compatibility": dict(guide_plan["compatibility"]),
    }) == legacy_guide_hash
    short_context_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "two", "prompt": "second", "length": 192},
        ]}),
        "short_context_test", 64, 32, 22, "video", "head", "disabled",
        "generated_audio", 39, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "guide",
    )
    assert chain._history_hash(short_context_plan, 1) == chain._history_hash(
        guide_plan, 1)
    assert chain._history_hash(short_context_plan, 2) != chain._history_hash(
        guide_plan, 2)
    legacy_short_context_hash = chain._fingerprint(
        chain._legacy_history_contract(short_context_plan, 1))
    legacy_short_context_metadata = {
        "history_hash": legacy_short_context_hash,
        "compatibility": dict(short_context_plan["compatibility"]),
    }
    assert chain._accepted_resume_history_hash(
        guide_plan, 1, legacy_short_context_metadata,
    ) == legacy_short_context_hash
    intermediate_plan = json.loads(json.dumps(short_context_plan))
    intermediate_plan["compatibility"]["continuation_mode"] = "masked_av"
    intermediate_contract = chain._legacy_history_contract(
        intermediate_plan, 1)
    intermediate_contract["compatibility"] = dict(
        intermediate_contract["compatibility"])
    intermediate_contract["compatibility"].pop("continuation_mode")
    intermediate_hash = chain._fingerprint(intermediate_contract)
    assert chain._accepted_resume_history_hash(guide_plan, 1, {
        "history_hash": intermediate_hash,
        "compatibility": dict(intermediate_plan["compatibility"]),
    }) == intermediate_hash
    legacy_short_context_metadata["history_hash"] = chain._fingerprint(
        chain._legacy_history_contract(short_context_plan, 2))
    assert chain._accepted_resume_history_hash(
        guide_plan, 2, legacy_short_context_metadata,
    ) is None

    class ContextDecodeVAE:
        def decode(self, _latent):
            decoded = torch.zeros((192, 32, 48, 3), dtype=torch.float32)
            for frame_index in range(192):
                decoded[frame_index].fill_(float(frame_index))
            return decoded

    recovered = chain._previous_context_frames({
        "previous_frames": frames[-22:],
        "previous_latent": previous,
        "segments": [{
            "index": 1, "raw_frames": 192, "delivered_frames": 192,
        }],
    }, ContextDecodeVAE(), 39)
    assert tuple(recovered.shape) == (39, 32, 48, 3)
    assert float(recovered[0, 0, 0, 0]) == 153.0
    assert float(recovered[-1, 0, 0, 0]) == 191.0
    changed_prompt_plan = json.loads(json.dumps(guide_plan))
    changed_prompt_plan["shots"][0]["prompt_hash"] = "different-prompt"
    assert chain._accepted_resume_history_hash(changed_prompt_plan, 1, {
        "history_hash": legacy_masked_hash,
        "compatibility": dict(plan["compatibility"]),
    }) is None
    incompatible_metadata = {
        "history_hash": legacy_masked_hash,
        "compatibility": dict(plan["compatibility"]),
    }
    assert chain._selected_resume_history_hash(
        changed_prompt_plan, 1, incompatible_metadata, True) is None
    assert chain._selected_resume_history_hash(
        changed_prompt_plan, 1, incompatible_metadata, False,
    ) == legacy_masked_hash
    loop_start_inputs = chain.MiniMaxH3ChainLoopStart.INPUT_TYPES()
    assert loop_start_inputs["optional"]["verify_resume_history"][1][
        "default"] is True
    resume_calls = []
    original_resume_loader = chain._load_resume_state

    def fake_resume_loader(requested_plan, start_clip, verify_history=True,
                           source_timeline=None, source_audio=None):
        resume_calls.append((start_clip, verify_history))
        return {
            "plan": requested_plan,
            "index": start_clip,
            "segments": [],
            "resumed_from": start_clip - 1,
        }

    chain._load_resume_state = fake_resume_loader
    try:
        unsafe_initial = chain._initial_state(
            changed_prompt_plan, 2, verify_resume_history=False)
    finally:
        chain._load_resume_state = original_resume_loader
    assert unsafe_initial["index"] == 2
    assert resume_calls == [(2, False)]
    mixed_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "new_shot", "prompt": "first", "length": 192},
            {"id": "same_shot", "prompt": "second", "length": 192,
             "continuation_mode": "masked_av"},
        ]}),
        "mixed_test", 64, 32, 39, "video", "head", "disabled",
        "generated_audio", 22, 8.0, 8, 1, 18, "model-stack-v1", 5,
        "guide",
    )
    assert "continuation_mode" not in mixed_plan["compatibility"]
    assert "continuation_mode" not in mixed_plan["shots"][0]
    assert mixed_plan["shots"][1]["continuation_mode"] == "masked_av"
    assert "context=39/mixed" in mixed_plan["summary"]
    assert "continuation_mode" not in chain._history_contract(
        mixed_plan, 1)["shots"][0]
    assert chain._history_contract(mixed_plan, 2)["shots"][1][
        "continuation_mode"] == "masked_av"
    changed_scene_mode_plan = json.loads(json.dumps(mixed_plan))
    changed_scene_mode_plan["shots"][1]["continuation_mode"] = "guide"
    assert chain._history_hash(mixed_plan, 2) != chain._history_hash(
        changed_scene_mode_plan, 2)
    assert chain._effective_editor_plan(mixed_plan)["shots"][1][
        "continuation_mode"] == "masked_av"
    per_scene_context_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "clean", "prompt": "independent", "length": 192,
             "context_length": 0, "continuation_mode": "masked_av"},
            {"id": "long_context", "prompt": "continued", "length": 192,
             "context_length": 39},
        ]}),
        "scene_context_test", 64, 32, 22, "frames", "before", "disabled",
        "generated_audio", 22, 8.0, 8, 1, 18, "model-stack-v1", 0,
        "guide",
    )
    assert [shot["delivered_frames"] for shot in
            per_scene_context_plan["shots"]] == [192, 192, 192]
    assert per_scene_context_plan["shots"][1]["context_length"] == 0
    assert per_scene_context_plan["shots"][2]["context_length"] == 39
    assert per_scene_context_plan["compatibility"][
        "context_storage_length"] == 39
    assert chain._history_contract(per_scene_context_plan, 2)["shots"][1][
        "context_length"] == 0
    assert chain._effective_editor_plan(per_scene_context_plan)["shots"][1][
        "context_length"] == 0
    clean_result = chain.MiniMaxH3ChainContext().apply(
        {"plan": per_scene_context_plan, "index": 2,
         "previous_frames": frames, "previous_latent": previous},
        conditioning, VideoVAE(), target)
    assert clean_result[:3] == (conditioning, 0, False)
    assert clean_result[3] is target
    audio_only_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "audio_only", "prompt": "new visual", "length": 192,
             "context_length": 0, "audio_context_length": 33},
        ]}),
        "audio_only_context_test", 64, 32, 22, "video", "head",
        "disabled", "generated_audio", 22, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "guide",
    )
    assert audio_only_plan["shots"][1]["delivered_frames"] == 192
    assert audio_only_plan["shots"][1]["audio_context_length"] == 33
    assert chain._history_contract(audio_only_plan, 2)["shots"][1][
        "audio_context_length"] == 33
    assert chain._effective_editor_plan(audio_only_plan)["shots"][1][
        "audio_context_length"] == 33
    original_activate = nodes._activate_inline_patches
    nodes._activate_inline_patches = lambda **_kwargs: "native"
    try:
        audio_only_result = chain.MiniMaxH3ChainContext().apply(
            {"plan": audio_only_plan, "index": 2,
             "previous_frames": frames, "previous_latent": previous},
            conditioning, VideoVAE(), target)
    finally:
        nodes._activate_inline_patches = original_activate
    assert audio_only_result[1:3] == (0, True)
    assert audio_only_result[3] is target
    assert any("audio_latent" in keyframe for keyframe in
               audio_only_result[0][0][1]["minimax_keyframes"])
    scene_no_carry_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "audio_cut", "prompt": "new sound", "length": 192,
             "context_length": 0, "audio_context_length": 33,
             "generated_continuity": "off"},
        ]}),
        "scene_audio_cut_test", 64, 32, 22, "video", "head",
        "disabled", "generated_audio", 22, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "guide",
    )
    original_prepare = chain._prepare_native_guide_conditioning
    chain._prepare_native_guide_conditioning = lambda value: value
    try:
        scene_no_carry_result = chain.MiniMaxH3ChainContext().apply(
            {"plan": scene_no_carry_plan, "index": 2,
             "previous_frames": frames, "previous_latent": previous},
            conditioning, VideoVAE(), target)
    finally:
        chain._prepare_native_guide_conditioning = original_prepare
    assert scene_no_carry_result[1:3] == (0, False)
    assert scene_no_carry_result[3] is target
    mixed_state = {
        "plan": mixed_plan,
        "index": 2,
        "previous_frames": frames,
        "previous_latent": previous,
    }
    mixed_result = chain.MiniMaxH3ChainContext().apply(
        mixed_state, conditioning, VideoVAE(), target)
    assert mixed_result[1:3] == (39, True)
    assert "noise_mask" in mixed_result[3]
    no_carry_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        chain._contract_transition_policy("soft_av"),
        audio_context_length=39)
    no_carry_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "source_driven", "prompt": "second", "length": 192},
        ]}),
        "masked_source_audio_test", 64, 32, 39, "video", "head",
        "disabled", "source_track", 39, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "audio_feathered_av", no_carry_policy,
    )
    no_carry_result = chain.MiniMaxH3ChainContext().apply(
        {"plan": no_carry_plan, "index": 2, "previous_frames": frames,
         "previous_latent": previous},
        conditioning, VideoVAE(), target)
    no_carry_video, no_carry_audio = no_carry_result[3]["samples"].unbind()
    no_carry_video_mask, no_carry_audio_mask = (
        no_carry_result[3]["noise_mask"].unbind())
    assert no_carry_result[1:3] == (39, True)
    assert torch.equal(
        no_carry_video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert not torch.count_nonzero(no_carry_audio)
    assert not torch.count_nonzero(
        no_carry_video_mask[:, :, :prefix_video_steps])
    assert torch.all(no_carry_audio_mask == 1.0)
    five_frame_transition = chain._contract_transition_policy(
        "soft_av", expert_override=True,
        continuation_mode="audio_feathered_av", context_length=5)
    five_frame_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        five_frame_transition, audio_context_length=5)
    five_frame_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "video_only_five", "prompt": "second", "length": 192},
        ]}),
        "masked_video_only_five_test", 64, 32, 5, "video", "head",
        "disabled", "source_track", 5, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "audio_feathered_av", five_frame_policy,
    )
    five_frame_result = chain.MiniMaxH3ChainContext().apply(
        {"plan": five_frame_plan, "index": 2, "previous_frames": frames,
         "previous_latent": previous},
        conditioning, VideoVAE(), target)
    five_chain_video_mask, five_chain_audio_mask = (
        five_frame_result[3]["noise_mask"].unbind())
    assert five_frame_result[1:3] == (5, True)
    assert not torch.count_nonzero(five_chain_video_mask[:, :, :2])
    assert torch.all(five_chain_video_mask[:, :, 2:] == 1.0)
    assert torch.all(five_chain_audio_mask == 1.0)
    locked_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "on", "locked"),
        chain._contract_transition_policy("soft_av"),
        audio_context_length=39)
    locked_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "source_locked", "prompt": "second", "length": 192},
        ]}),
        "locked_source_audio_test", 64, 32, 39, "video", "head",
        "disabled", "source_track", 39, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "audio_feathered_av", locked_policy,
    )
    locked_state = {
        "plan": locked_plan, "index": 2, "previous_frames": frames,
        "previous_latent": previous,
        "current_source_audio_target": locked_source_window,
    }
    locked_result = chain.MiniMaxH3ChainContext().apply(
        locked_state, conditioning, VideoVAE(), target, LockedAudioVAE())
    locked_chain_video, locked_chain_audio = (
        locked_result[3]["samples"].unbind())
    locked_chain_video_mask, locked_chain_audio_mask = (
        locked_result[3]["noise_mask"].unbind())
    assert locked_result[1:3] == (39, True)
    assert torch.equal(
        locked_chain_video[:, :, :prefix_video_steps],
        previous_video[:, :, -prefix_video_steps:],
    )
    assert torch.equal(locked_chain_audio, locked_audio)
    assert not torch.count_nonzero(
        locked_chain_video_mask[:, :, :prefix_video_steps])
    assert not torch.count_nonzero(locked_chain_audio_mask)
    lip_voice = {
        "waveform": torch.zeros((1, 1, 16 * 32000)),
        "sample_rate": 32000,
    }
    lip_voice["waveform"][..., 7 * 32000:8 * 32000] = 1.0
    lip_options = chain._contract_masked_song_options(
        preroll_seconds=0.0,
        lookahead_seconds=0.0,
        audio_denoise=0.0,
        gap_denoise=0.3,
        gate_hold_seconds=0.0,
        voice_fingerprint=chain._audio_fingerprint(lip_voice),
    )
    contextual_locked_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy(
            "source", "off", "off", "locked", lip_options),
        chain._contract_transition_policy("soft_av"),
        audio_context_length=39)
    contextual_locked_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "voice_locked", "prompt": "second", "length": 192},
        ]}),
        "contextual_locked_source_audio_test", 64, 32, 39, "video", "head",
        "disabled", "source_track", 39, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "audio_feathered_av",
        contextual_locked_policy,
    )
    contextual_locked_state = {
        **locked_state,
        "plan": contextual_locked_plan,
        "current_source_audio_target_clip_start_seconds": 0.0,
    }
    try:
        chain.MiniMaxH3ChainContext().apply(
            contextual_locked_state, conditioning, VideoVAE(), target,
            LockedAudioVAE())
    except ValueError as exc:
        assert "lip_sync_voice input is disconnected" in str(exc)
    else:
        raise AssertionError("fingerprinted vocal stem was not required")
    contextual_locked_result = chain.MiniMaxH3ChainContext().apply(
        contextual_locked_state, conditioning, VideoVAE(), target,
        LockedAudioVAE(), lip_sync_voice=lip_voice)
    contextual_locked_audio_mask = contextual_locked_result[3][
        "noise_mask"].unbind()[1]
    assert torch.all(contextual_locked_audio_mask[..., 10] == 0.3)
    assert not torch.count_nonzero(contextual_locked_audio_mask[..., 50])
    assert torch.all(contextual_locked_audio_mask[..., 100] == 0.3)
    scene_locked_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 192},
            {"id": "scene_locked", "prompt": "second", "length": 192,
             "source_audio_target": "locked"},
        ]}),
        "scene_locked_source_audio_test", 64, 32, 39, "video", "head",
        "disabled", "generated_audio", 39, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "audio_feathered_av",
    )
    scene_locked_result = chain.MiniMaxH3ChainContext().apply(
        {"plan": scene_locked_plan, "index": 2,
         "previous_frames": frames, "previous_latent": previous,
         "current_source_audio_target": locked_source_window},
        conditioning, VideoVAE(), target, LockedAudioVAE())
    scene_locked_audio = scene_locked_result[3]["samples"].unbind()[1]
    scene_locked_audio_mask = scene_locked_result[3]["noise_mask"].unbind()[1]
    assert torch.equal(scene_locked_audio, locked_audio)
    assert not torch.count_nonzero(scene_locked_audio_mask)
    locked_first_result = chain.MiniMaxH3ChainContext().apply(
        {"plan": locked_plan, "index": 1, "external_context": False,
         "current_source_audio_target": locked_source_window},
        conditioning, VideoVAE(), target, LockedAudioVAE())
    locked_first_audio_mask = locked_first_result[3][
        "noise_mask"].unbind()[1]
    assert locked_first_result[1:3] == (0, False)
    assert not torch.count_nonzero(locked_first_audio_mask)
    feathered_state = dict(mixed_state)
    feathered_state["plan"] = feathered_plan
    feathered_result = chain.MiniMaxH3ChainContext().apply(
        feathered_state, conditioning, VideoVAE(), target)
    assert feathered_result[1:3] == (39, True)
    feathered_chain_video_mask, feathered_chain_audio_mask = (
        feathered_result[3]["noise_mask"].unbind())
    assert torch.allclose(
        feathered_chain_video_mask[0, 0, 8:12, 0, 0],
        torch.linspace(0.85, 0.95, 4),
    )
    assert torch.allclose(
        feathered_chain_audio_mask[0, 0, 0, 57:65],
        torch.linspace(0.85, 0.95, 8),
    )
    preflight_calls = []
    original_require = masked._require_h3_mask_support
    original_prepare = chain._prepare_native_guide_conditioning
    masked._require_h3_mask_support = lambda: preflight_calls.append("masked")
    chain._prepare_native_guide_conditioning = lambda value: (
        preflight_calls.append("guide") or value)
    try:
        mixed_first_result = chain.MiniMaxH3ChainContext().apply(
            {"plan": mixed_plan, "index": 1, "external_context": False},
            conditioning, VideoVAE(), target)
    finally:
        masked._require_h3_mask_support = original_require
        chain._prepare_native_guide_conditioning = original_prepare
    assert mixed_first_result[:3] == (conditioning, 0, False)
    assert preflight_calls == ["masked", "guide"]
    imported_audio = {
        "waveform": torch.zeros((1, 2, 6400), dtype=torch.float32),
        "sample_rate": 2400,
    }
    external_context, _status = chain.MiniMaxH3ChainExternalVideo().prepare(
        plan, frames, 24.0, False, imported_audio)
    assert int(external_context["context_frames"].shape[0]) == 39
    assert int(external_context["context_audio"]["waveform"].shape[-1]) == 3900
    mixed_external_context, _status = (
        chain.MiniMaxH3ChainExternalVideo().prepare(
            mixed_plan, frames, 24.0, False, imported_audio))
    assert int(mixed_external_context[
        "context_audio"]["waveform"].shape[-1]) == 2200
    zero_external_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "clean_first", "prompt": "first", "length": 192,
             "context_length": 0},
        ]}),
        "zero_external_test", 64, 32, 22, "video", "head", "disabled",
        "generated_audio", 22, 8.0, 8, 1, 18, "model-stack-v1", 0,
        "guide",
    )
    zero_external_context, _status = (
        chain.MiniMaxH3ChainExternalVideo().prepare(
            zero_external_plan, frames, 24.0, False, imported_audio))
    assert int(zero_external_context["context_frames"].shape[0]) == 0
    assert int(zero_external_context[
        "context_audio"]["waveform"].shape[-1]) == 2200
    zero_external_prepared = chain._plan_with_external_context(
        zero_external_plan, zero_external_context)
    assert zero_external_prepared["shots"][0]["delivered_frames"] == 192
    fully_clean_external_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "fully_clean", "prompt": "first", "length": 192,
             "context_length": 0, "audio_context_length": 0},
        ]}),
        "fully_clean_external_test", 64, 32, 22, "video", "head",
        "disabled", "generated_audio", 22, 8.0, 8, 1, 18,
        "model-stack-v1", 0, "guide",
    )
    fully_clean_context, _status = (
        chain.MiniMaxH3ChainExternalVideo().prepare(
            fully_clean_external_plan, frames, 24.0, False, imported_audio))
    assert int(fully_clean_context["context_frames"].shape[0]) == 0
    assert fully_clean_context["context_audio"] is None
    first_state = {
        "plan": plan,
        "index": 1,
        "external_context": False,
    }
    first_result = chain.MiniMaxH3ChainContext().apply(
        first_state, conditioning, VideoVAE(), target)
    assert first_result[:3] == (conditioning, 0, False)
    assert first_result[3] is target
    for av_mode in (
            "masked_av", "tapered_av", "feathered_av",
            "audio_feathered_av", "drift_control_av"):
        for invalid_args, expected in (
            ((1, "video", "head"), "exact shared"),
            ((22, "video", "head"), "exact shared"),
            ((56, "video", "head"), "exact shared"),
            ((39, "frames", "head"), "encode_mode=video"),
            ((39, "video", "before"), "anchor_mode=head"),
        ):
            context, encode, anchor = invalid_args
            try:
                chain._normalize_plan(
                    json.dumps({"shots": [
                        {"id": "one", "prompt": "first", "length": 192},
                        {"id": "two", "prompt": "second", "length": 192},
                    ]}),
                    "invalid_masked_test", 64, 32, context, encode, anchor,
                    "disabled", "generated_audio", 39, 8.0, 8, 1, 18,
                    "model-stack-v1", 0, av_mode,
                )
            except ValueError as exc:
                assert expected in str(exc), str(exc)
            else:
                raise AssertionError(
                    "%s plan accepted invalid %s/%s/%s" %
                    (av_mode, *invalid_args))
    try:
        chain._normalize_plan(
            json.dumps({"shots": [
                {"id": "one", "prompt": "first", "length": 192},
                {"id": "two", "prompt": "second", "length": 192},
            ]}),
            "invalid_detail_av_test", 64, 32, 90, "video", "head",
            "disabled", "generated_audio", 90, 8.0, 8, 1, 18,
            "model-stack-v1", 0, "tapered_av",
        )
    except ValueError as exc:
        assert "exactly 39" in str(exc)
    else:
        raise AssertionError("Detail AV accepted a 90-frame context")
    try:
        chain._normalize_plan(
            json.dumps({"shots": [
                {"id": "one", "prompt": "first", "length": 192},
                {"id": "two", "prompt": "second", "length": 192,
                 "continuation_mode": "masked_av"},
            ]}),
            "invalid_scene_masked_test", 64, 32, 1, "video", "head",
            "disabled", "generated_audio", 1, 8.0, 8, 1, 18,
            "model-stack-v1", 0, "guide",
        )
    except ValueError as exc:
        assert "shot 2" in str(exc).lower(), str(exc)
    else:
        raise AssertionError("per-scene masked mode accepted context_length=1")
    print(
        "masked plan: global mode/context are next-scene controls, explicit "
        "scene settings are history-significant, and invalid configurations "
        "are rejected")

    # Native merged PR #15375 support must remain authoritative. The final
    # helper-based extra_conds path must be detected even though its bytecode
    # no longer mentions audio_denoise_mask directly.
    h3m = sys.modules["comfy.ldm.minimax.model"]
    model_base = sys.modules["comfy.model_base"]
    samplers = sys.modules["comfy.samplers"]
    legacy_sampler = samplers.KSamplerX0Inpaint

    def mask_row_values(*_args):
        return None

    def mod_row(*_args):
        return None

    class NativeModel:
        def forward(
                self, x, timestep=None, context=None,
                transformer_options={}, minimax_payload=None,
                denoise_mask=None, audio_denoise_mask=None, **kwargs):
            out = [x[0].clone(), x[1].clone()]
            if denoise_mask is not None:
                out[0] = out[0] * denoise_mask
            if audio_denoise_mask is not None:
                out[1] = out[1] * audio_denoise_mask
            return out

        def _forward(
                self, x, timestep=None, context=None,
                transformer_options={}, minimax_payload=None,
                denoise_mask=None, audio_denoise_mask=None, **kwargs):
            return x

    class NativeFinal:
        def forward(self, value):
            return value

    class NativeBase:
        def _pool_masks_to_token_grid(self, masks):
            return masks

        def _token_grid_masks(self, denoise_mask, latent_shapes):
            return [denoise_mask, latent_shapes]

        def _denoise_mask_values(self, denoise_mask, latent_shapes):
            return {"denoise_mask": denoise_mask}

        def _denoise_mask_conds(self, denoise_mask, latent_shapes):
            return self._denoise_mask_values(denoise_mask, latent_shapes)

        def scale_latent_inpaint(
                self, sigma, noise, latent_image, x=None,
                denoise_mask=None, **kwargs):
            return kwargs

        def extra_conds(self, **kwargs):
            out = {}
            denoise_mask = kwargs.get("denoise_mask")
            if denoise_mask is not None:
                out.update(self._denoise_mask_conds(
                    denoise_mask, kwargs.get("latent_shapes")))
            return out

    class NativeSampler:
        def __call__(self, x, sigma, denoise_mask, model_options=None,
                     seed=None):
            return self.inner_model.inner_model.scale_latent_inpaint(
                x=x, sigma=sigma, noise=self.noise,
                latent_image=self.latent_image,
                denoise_mask=denoise_mask)

    h3m.mask_row_values = mask_row_values
    h3m._mod_row = mod_row
    h3m.MiniMaxH3Model = NativeModel
    h3m.FinalLayer = NativeFinal
    model_base.MiniMaxH3 = NativeBase
    samplers.KSamplerX0Inpaint = NativeSampler
    native_forward = NativeModel.forward
    native_extra = NativeBase.extra_conds
    native_scale = NativeBase.scale_latent_inpaint
    native_sampler_call = NativeSampler.__call__
    mask_compat = _load("h3_mask_compat")
    payload_compat = _load("h3_mask_payload_compat")
    assert mask_compat.ensure_h3_mask_compat()
    assert payload_compat.ensure_av_mask_payload_compat()
    assert h3m.MiniMaxH3Model.forward is native_forward
    assert model_base.MiniMaxH3.extra_conds is native_extra
    assert model_base.MiniMaxH3.scale_latent_inpaint is native_scale
    assert samplers.KSamplerX0Inpaint.__call__ is native_sampler_call
    assert "process_denoise_mask" not in NativeBase.__dict__
    native_status = mask_compat.capability_status()
    assert native_status["mask_engine_native"]
    assert native_status["velocity_mask_conversion_native"]
    assert not native_status["velocity_mask_conversion_compat"]
    assert native_status["mask_helpers_native"]
    assert native_status["scale_latent_inpaint_native"]
    assert native_status["sampler_mask_blend_native"]
    native_payload_status = payload_compat.capability_status()
    assert native_payload_status["native_av_mask_payload"]
    assert not native_payload_status["native_payload_direct"]
    assert native_payload_status["native_h3_mask_hooks"]
    assert mask_compat._MARKER == "_h3_motion_context_pr15375_compat_v4"

    def wrapper(self, **kwargs):
        return native_extra(self, **kwargs)

    setattr(wrapper, payload_compat._MARKER, True)
    assert payload_compat._is_compatible_wrapper(wrapper)
    print(
        "mask compatibility: native merged #15375 model/sampler support is "
        "detected and left untouched when native #15988 velocity conversion "
        "is present; the removed preprocessing hook stays removed")

    # Native #15375 builds from before #15988 need a narrow forward wrapper.
    # It must scale video velocity directly and apply the audio mask to the
    # converted residual rather than to the carry base.
    def time_shift_sigma(sigma, _shift_video, _shift_audio):
        return sigma * 0.5

    h3m.time_shift_sigma = time_shift_sigma

    class PreVelocityFixModel:
        sigma_shift_video = 12.0
        sigma_shift_audio = 3.0

        def forward(
                self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
            scale = float((minimax_payload or {}).get("audio_scale", 1.0))
            audio_src = x[1]
            sigma_v = (timestep.flatten()[0] / 1000.0).float()
            sigma_a = time_shift_sigma(
                sigma_v, self.sigma_shift_video, self.sigma_shift_audio)
            carry = (sigma_a / sigma_v).to(audio_src.dtype)
            out = self._forward(
                x, timestep, context,
                transformer_options=transformer_options,
                minimax_payload=minimax_payload,
                denoise_mask=denoise_mask,
                audio_denoise_mask=audio_denoise_mask,
                **kwargs)
            if scale != 1.0:
                out[1] = ((1.0 - scale) * (audio_src * carry)
                          + (1.0 + (scale - 1.0) * sigma_a).to(
                              out[1].dtype) * out[1])
            return out

        def _forward(
                self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
            return [torch.full_like(x[0], 2.0),
                    torch.full_like(x[1], 3.0)]

    h3m.MiniMaxH3Model = PreVelocityFixModel
    velocity_compat = _load("h3_mask_compat")
    pre_velocity_forward = PreVelocityFixModel.forward
    assert not velocity_compat.capability_status()[
        "velocity_mask_conversion"]
    assert velocity_compat.ensure_h3_mask_compat()
    assert PreVelocityFixModel.forward is not pre_velocity_forward
    patched_velocity_forward = PreVelocityFixModel.forward
    velocity_status = velocity_compat.capability_status()
    assert velocity_status["velocity_mask_conversion"]
    assert velocity_status["velocity_mask_conversion_compat"]
    assert velocity_compat.ensure_h3_mask_compat()
    assert PreVelocityFixModel.forward is patched_velocity_forward

    video_velocity_mask = torch.tensor(
        [[[[[1.0, 0.75], [0.5, 0.25]]]]])
    audio_velocity_mask = torch.tensor(
        [[[[1.0, 0.5, 0.25], [0.75, 0.5, 0.0]]]])
    clean_video = torch.arange(8, dtype=torch.float32).reshape(
        1, 2, 1, 2, 2)
    video_input = clean_video + 0.5 * video_velocity_mask * 2.0
    audio_input = torch.full((1, 2, 2, 3), 2.0)
    velocity_out = PreVelocityFixModel().forward(
        [video_input, audio_input], torch.tensor([500.0]),
        torch.empty((1, 1, 1)),
        minimax_payload={"audio_scale": 4.0},
        denoise_mask=video_velocity_mask,
        audio_denoise_mask=audio_velocity_mask)
    torch.testing.assert_close(
        velocity_out[0],
        torch.full_like(video_input, 2.0) * video_velocity_mask)
    torch.testing.assert_close(
        video_input - 0.5 * velocity_out[0], clean_video)
    carry_base = torch.full_like(audio_input, -3.0)
    expected_audio_velocity = carry_base + (
        torch.full_like(audio_input, 5.25) * audio_velocity_mask)
    torch.testing.assert_close(
        velocity_out[1], expected_audio_velocity)
    print(
        "mask compatibility: pre-#15988 native velocity conversion is "
        "corrected once for masked video and carried audio")

    # A checkout from immediately before the merge-time refactor has the
    # direct payload and scale hook, but lacks the final helper contract. It
    # must be upgraded rather than mistaken for the merged implementation.
    class PreMergeBase:
        def _pool_masks_to_token_grid(self, masks):
            return masks

        def scale_latent_inpaint(
                self, sigma, noise, latent_image, x=None,
                denoise_mask=None, **kwargs):
            return kwargs

        def extra_conds(self, **kwargs):
            return {
                "denoise_mask": kwargs.get("denoise_mask"),
                "audio_denoise_mask": kwargs.get("audio_denoise_mask"),
            }

    model_base.MiniMaxH3 = PreMergeBase
    premerge_extra = PreMergeBase.extra_conds
    premerge_mask = _load("h3_mask_compat")
    premerge_payload = _load("h3_mask_payload_compat")
    assert not premerge_mask.capability_status()["mask_helpers_complete"]
    assert premerge_mask.ensure_h3_mask_compat()
    assert premerge_mask.capability_status()["mask_helpers_compat"]
    assert premerge_payload.ensure_av_mask_payload_compat()
    assert PreMergeBase.extra_conds is not premerge_extra
    assert premerge_payload.capability_status()["wrapper_present"]

    # A legacy post-#15439 H3 core receives all missing #15375 pieces lazily.
    for name in ("mask_row_values", "_mod_row"):
        delattr(h3m, name)

    class LegacyModel:
        def forward(self, x, timestep, context, transformer_options={},
                    minimax_payload=None, **kwargs):
            return x

        def _forward(self, x, timestep, context, transformer_options={},
                     minimax_payload=None, **kwargs):
            return x

    class LegacyFinal:
        def forward(self, x, t_emb, video_seg, audio_seg):
            return x

    class LegacyBase:
        latent_shapes = None

        def extra_conds(self, **_kwargs):
            return {}

    h3m.MiniMaxH3Model = LegacyModel
    h3m.FinalLayer = LegacyFinal
    h3m.torch = torch
    h3m.VISUAL_COND_TIMESTEP = 0.2
    model_base.MiniMaxH3 = LegacyBase
    model_base.torch = torch
    samplers.KSamplerX0Inpaint = legacy_sampler

    video_shape = (1, 1, 1, 2, 2)
    audio_shape = (1, 2, 2, 1)
    latent_shapes = (video_shape, audio_shape)

    def pack_latents(parts):
        return torch.cat(
            [part.reshape(part.shape[0], -1) for part in parts], dim=1), None

    def unpack_latents(value, shapes):
        parts = []
        offset = 0
        for shape in shapes:
            count = 1
            for size in shape[1:]:
                count *= size
            parts.append(value[:, offset:offset + count].reshape(shape))
            offset += count
        return parts

    utils = sys.modules["comfy.utils"]
    utils.unpack_latents = unpack_latents
    utils.pack_latents = pack_latents
    model_base.utils = utils
    model_base.comfy = sys.modules["comfy"]
    fallback_mask = _load("h3_mask_compat")
    fallback_payload = _load("h3_mask_payload_compat")
    assert not fallback_mask.capability_status()["mask_engine_complete"]
    assert fallback_mask.ensure_h3_mask_compat()
    fallback_status = fallback_mask.capability_status()
    assert fallback_status["mask_engine_compat"]
    assert fallback_status["velocity_mask_conversion_compat"]
    assert fallback_status["mask_helpers_compat"]
    assert fallback_status["process_denoise_mask_compat"]
    assert fallback_status["scale_latent_inpaint_compat"]
    assert fallback_status["sampler_mask_blend_compat"]
    assert fallback_mask.is_ready()

    class Patch:
        patch_size = (1, 2, 2)

    class LegacyRuntime(LegacyBase):
        def __init__(self):
            self.latent_shapes = latent_shapes
            self.diffusion_model = Patch()

        def audio_scale(self):
            return 1.0

    model_base.MiniMaxH3 = LegacyRuntime
    # Reattach the lazily installed class hooks after specializing the test
    # runtime, as ComfyUI uses one stable MiniMaxH3 class in production.
    for name in (
        "process_denoise_mask",
        "_pool_masks_to_token_grid",
        "_token_grid_masks",
        "_denoise_mask_values",
        "_denoise_mask_conds",
        "scale_latent_inpaint",
    ):
        setattr(LegacyRuntime, name, getattr(LegacyBase, name))

    runtime = LegacyRuntime()
    video_mask = torch.tensor([[[[[0.25, 0.50], [0.75, 0.30]]]]])
    audio_mask = torch.tensor(
        [[[[0.25], [0.25]], [[0.75], [0.50]]]])
    packed_mask = pack_latents([video_mask, audio_mask])[0]
    packed_zero = torch.zeros_like(packed_mask)
    packed_x = torch.full_like(packed_mask, 10.0)
    sampler = samplers.KSamplerX0Inpaint()
    sampler.inner_model = types.SimpleNamespace(inner_model=runtime)
    sampler.noise = packed_zero
    sampler.latent_image = packed_zero
    result = sampler(
        packed_x, torch.ones((1,)), packed_mask, model_options={})
    result_video, result_audio = unpack_latents(result, latent_shapes)
    assert torch.allclose(
        result_video, torch.full_like(result_video, 7.5)), result_video
    expected_result_audio = torch.tensor(
        [[[[7.5], [5.0]], [[7.5], [5.0]]]])
    assert torch.allclose(result_audio, expected_result_audio)
    assert not hasattr(runtime, fallback_mask._ACTIVE_MASK_ATTR)

    half_mask = torch.full_like(packed_mask, 0.5)

    def replace_mask(_sigma, _mask, extra_options=None):
        return half_mask

    replaced = sampler(
        packed_x, torch.ones((1,)), packed_mask,
        model_options={"denoise_mask_function": replace_mask})
    assert torch.allclose(replaced, torch.full_like(replaced, 5.0))
    assert not hasattr(runtime, fallback_mask._ACTIVE_MASK_ATTR)
    original_parts = [video_mask, audio_mask]
    assert runtime.process_denoise_mask(original_parts) is original_parts

    payload_base = LegacyRuntime.extra_conds

    def add_legacy_payload(out, kwargs):
        masks = unpack_latents(kwargs["denoise_mask"],
                               kwargs["latent_shapes"])
        out["denoise_mask"] = sys.modules["comfy.conds"].CONDRegular(
            masks[0][:, :1])
        out["audio_denoise_mask"] = sys.modules[
            "comfy.conds"].CONDRegular(masks[1][:, :1])

    @functools.wraps(payload_base, updated=())
    def legacy_payload_wrapper(self, **kwargs):
        out = payload_base(self, **kwargs)
        add_legacy_payload(out, kwargs)
        return out

    setattr(legacy_payload_wrapper, fallback_payload._LEGACY_MARKER, True)
    LegacyRuntime.extra_conds = legacy_payload_wrapper
    before_payload = LegacyRuntime.extra_conds
    assert fallback_payload.ensure_av_mask_payload_compat()
    assert LegacyRuntime.extra_conds is not before_payload
    assert not any(fallback_payload._is_legacy_wrapper(item) for item in
                   fallback_payload._walk_wrapped(
                       LegacyRuntime.extra_conds))
    assert fallback_payload.capability_status()["wrapper_present"]
    payload_out = runtime.extra_conds(
        denoise_mask=packed_mask, latent_shapes=latent_shapes)
    expected_video = torch.full_like(video_mask, 0.75)
    expected_audio = audio_mask.amax(dim=1, keepdim=True)
    expected_audio = torch.ceil(expected_audio * 256.0) / 256.0
    assert torch.equal(
        payload_out["denoise_mask"].cond, expected_video), (
            payload_out["denoise_mask"].cond, expected_video)
    assert torch.equal(payload_out["audio_denoise_mask"].cond,
                       expected_audio)
    print(
        "mask compatibility: legacy post-#15439 H3 receives the merged "
        "#15375 token-grid blend, per-step sampler bridge, and quantized AV "
        "payload")


if __name__ == "__main__":
    main()
