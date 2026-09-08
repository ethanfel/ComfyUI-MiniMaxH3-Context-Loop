#!/usr/bin/env python3
"""Weight-free regression tests for the native-first H3 #15988 bridge."""

import functools
import importlib.util
import itertools
from pathlib import Path
import types
import unittest

import torch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "h3_velocity_compat_test", ROOT / "h3_mask_compat.py")
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def time_shift_sigma(sigma, video_shift, audio_shift):
    ratio = audio_shift / video_shift
    return ratio * sigma / (1.0 + (ratio - 1.0) * sigma)


class LegacyModel:
    sigma_shift_video = 12.0
    sigma_shift_audio = 3.0

    def _raw(self, x):
        return [torch.full_like(x[0], 2.0), torch.full_like(x[1], 3.0)]

    def _carry(self, out, x, timestep, payload, options):
        scale = float((payload or {}).get("audio_scale", 1.0))
        if scale != 1.0:
            sigma_v = (timestep.flatten()[0] / 1000.0).float().clamp(min=1e-6)
            sigma_a = time_shift_sigma(
                sigma_v, options.get("minimax_h3_sigma_shift_video", 12.0),
                options.get("minimax_h3_sigma_shift_audio", 3.0))
            carry = (sigma_a / sigma_v).to(x[1].dtype)
            out[1] = ((1.0 - scale) * (x[1] * carry)
                      + (1.0 + (scale - 1.0) * sigma_a).to(out[1].dtype) * out[1])
        return out

    def forward(self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
        return self._carry(self._raw(x), x, timestep,
                           minimax_payload, transformer_options)


class NativeModel(LegacyModel):
    def forward(self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
        out = self._raw(x)
        if denoise_mask is not None:
            out[0] = out[0] * denoise_mask
        if audio_denoise_mask is not None:
            out[1] = out[1] * audio_denoise_mask
        return self._carry(out, x, timestep, minimax_payload, transformer_options)


class NativeVideoModel(LegacyModel):
    def forward(self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
        out = self._raw(x)
        if denoise_mask is not None:
            out[0].mul_(denoise_mask)
        return self._carry(out, x, timestep, minimax_payload, transformer_options)


class NativeAudioModel(LegacyModel):
    def forward(self, x, timestep, context, transformer_options={},
                minimax_payload=None, denoise_mask=None,
                audio_denoise_mask=None, **kwargs):
        out = self._raw(x)
        if audio_denoise_mask is not None:
            out[1] *= audio_denoise_mask
        return self._carry(out, x, timestep, minimax_payload, transformer_options)


class VelocityMaskTests(unittest.TestCase):
    def test_native_syntax_variants(self):
        def in_place(out, denoise_mask, audio_denoise_mask):
            out[0].mul_(denoise_mask)
            out[1].mul_(audio_denoise_mask)
            return out

        def augmented(out, denoise_mask, audio_denoise_mask):
            out[0] *= denoise_mask
            out[1] *= audio_denoise_mask
            return out

        def reversed_operands(out, denoise_mask, audio_denoise_mask):
            out[0] = denoise_mask * out[0]
            out[1] = audio_denoise_mask * out[1]
            return out

        for fn in (NativeModel.forward, in_place, augmented, reversed_operands):
            with self.subTest(fn=fn.__name__):
                self.assertTrue(compat._forward_scales_masked_velocity(fn))

    def test_examples_and_unused_helpers_are_not_capabilities(self):
        def examples(out, denoise_mask, audio_denoise_mask):
            """Use out[0] = out[0] * denoise_mask and
            out[1] = out[1] * audio_denoise_mask in a future implementation.
            """
            # out[0].mul_(denoise_mask)
            # out[1].mul_(audio_denoise_mask)
            def unused():
                out[0] *= denoise_mask
                out[1] *= audio_denoise_mask
            return out

        self.assertFalse(compat._forward_scales_masked_velocity(examples))

    def test_partial_streams_are_detected_separately(self):
        self.assertEqual(compat._forward_velocity_mask_streams(
            NativeVideoModel.forward), {0})
        self.assertEqual(compat._forward_velocity_mask_streams(
            NativeAudioModel.forward), {1})
        self.assertFalse(compat._forward_scales_masked_velocity(
            NativeAudioModel.forward))

    def test_wrong_stream_or_discarded_product_is_not_a_fix(self):
        def wrong(out, denoise_mask, audio_denoise_mask):
            out[0] = out[0] * audio_denoise_mask
            out[1] = out[1] * denoise_mask
            discarded = out[0] * denoise_mask
            return out, discarded

        self.assertEqual(compat._forward_velocity_mask_streams(wrong), set())

    def test_transparent_wrapper_streams_are_combined(self):
        @functools.wraps(NativeAudioModel.forward)
        def wrapper(self, x, timestep, context, denoise_mask=None, **kwargs):
            out = NativeAudioModel.forward(
                self, x, timestep, context, denoise_mask=denoise_mask, **kwargs)
            if denoise_mask is not None:
                out[0] = out[0] * denoise_mask
            return out

        self.assertEqual(compat._source_velocity_mask_streams(wrapper), {0})
        self.assertTrue(compat._forward_scales_masked_velocity(wrapper))
        runtime = type("Runtime", (LegacyModel,), {"forward": wrapper})
        module = types.SimpleNamespace(MiniMaxH3Model=runtime)
        self.assertIs(compat._install_velocity_mask_compat(module), wrapper)

    def test_wrapper_cycle_is_bounded(self):
        def wrapper(*args, **kwargs):
            return None

        wrapper.__wrapped__ = wrapper
        self.assertFalse(compat._forward_scales_masked_velocity(wrapper))

    def test_video_audio_carry_and_no_double_scaling(self):
        video = torch.arange(8, dtype=torch.float32).reshape(1, 2, 1, 2, 2)
        audio = torch.full((1, 2, 2, 3), 2.0)
        video_before, audio_before = video.clone(), audio.clone()
        mixed_video = torch.tensor([[[[[0.0, 0.25], [0.75, 1.0]]]]])
        mixed_audio = torch.tensor([[[[0.0, 0.25, 0.5], [0.75, 1.0, 0.875]]]])
        video_masks = (None, torch.zeros_like(mixed_video),
                       torch.ones_like(mixed_video), mixed_video)
        audio_masks = (None, torch.zeros_like(mixed_audio),
                       torch.ones_like(mixed_audio), mixed_audio)
        for model in (LegacyModel, NativeVideoModel, NativeAudioModel, NativeModel):
            runtime = type("Runtime", (model,), {})
            module = types.SimpleNamespace(
                MiniMaxH3Model=runtime, time_shift_sigma=time_shift_sigma)
            before = runtime.forward
            installed = compat._install_velocity_mask_compat(module)
            self.assertIs(compat._install_velocity_mask_compat(module), installed)
            if model is NativeModel:
                self.assertIs(installed, before)
            else:
                self.assertIsNot(installed, before)
            for scale, vmask, amask in itertools.product(
                    (1.0, 4.0, 0.5), video_masks, audio_masks):
                with self.subTest(model=model.__name__, scale=scale,
                                  video_mask=vmask, audio_mask=amask):
                    options = {"minimax_h3_sigma_shift_video": 10.0,
                               "minimax_h3_sigma_shift_audio": 4.0}
                    args = ([video, audio], torch.tensor([650.0]),
                            torch.empty(1, 1, 1))
                    kwargs = dict(
                        minimax_payload={"audio_scale": scale},
                        transformer_options=options, denoise_mask=vmask,
                        audio_denoise_mask=amask)
                    actual = runtime().forward(*args, **kwargs)
                    expected = NativeModel().forward(*args, **kwargs)
                    for got, want in zip(actual, expected):
                        torch.testing.assert_close(got, want)
            torch.testing.assert_close(video, video_before, rtol=0, atol=0)
            torch.testing.assert_close(audio, audio_before, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
