"""Regression coverage for the hidden Motion Context fallback engine.

The public 0.5 node normally delegates compatible scenes to seitanism's
upstream implementation.  These tests keep the internal fallback honest for
the features that still require it: arbitrary visual lengths, interior Guide
placement, exact 40 Hz PCM alignment, and coexistence with other anchors.
"""

import importlib.util
import os
import sys
import types

import torch
import torch.nn.functional as functional


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TESTS_DIR)
_PACKAGE = "h3_internal_update_unit"


class Nested:
    def __init__(self, parts):
        self.parts = parts

    def unbind(self):
        return list(self.parts)


def _install_stubs():
    comfy = types.ModuleType("comfy")
    comfy_utils = types.ModuleType("comfy.utils")

    def common_upscale(samples, width, height, method, crop):
        del method, crop
        return functional.interpolate(
            samples, size=(height, width), mode="bilinear",
            align_corners=False)

    comfy_utils.common_upscale = common_upscale
    comfy.utils = comfy_utils
    sys.modules["comfy"] = comfy
    sys.modules["comfy.utils"] = comfy_utils

    folder_paths = types.ModuleType("folder_paths")
    folder_paths.get_output_directory = lambda: "/tmp"
    sys.modules["folder_paths"] = folder_paths

    node_helpers = types.ModuleType("node_helpers")

    def conditioning_set_values(conditioning, values, append=False):
        output = []
        for embedding, extra in conditioning:
            metadata = extra.copy()
            for key, incoming in values.items():
                value = incoming
                if append and metadata.get(key) is not None:
                    value = metadata[key] + incoming
                metadata[key] = value
            output.append([embedding, metadata])
        return output

    node_helpers.conditioning_set_values = conditioning_set_values
    sys.modules["node_helpers"] = node_helpers

    package = types.ModuleType(_PACKAGE)
    package.__path__ = [_PKG_DIR]
    sys.modules[_PACKAGE] = package

    timing = types.ModuleType(_PACKAGE + ".av_timing")
    timing.AUDIO_TRIM_FRAMES_KEY = "_audio_trim_frames"
    timing.AUDIO_TRIM_MODE_KEY = "_h3_audio_trim_mode"
    timing.AUDIO_WITH_OVERLAP_FRAMES_KEY = "_audio_overlap_frames"
    timing.AUDIO_WITH_OVERLAP_WAVEFORM_KEY = "_audio_overlap_waveform"
    timing.conform_waveform_length = lambda waveform, length, label: (
        waveform[..., :length])
    sys.modules[timing.__name__] = timing

    layout = types.ModuleType(_PACKAGE + ".patch_layout")
    layout.MC_KEY = "_motion_context_frame"
    layout.MC_AUDIO_KEY = "_motion_context_audio_end"
    layout.apply_patch = lambda: True
    layout.claim_patch_ownership = lambda: (True, "test")
    layout.is_applied = lambda: True
    layout.native_guides_available = lambda: True
    sys.modules[layout.__name__] = layout

    payload = types.ModuleType(_PACKAGE + ".patch_payload")
    payload.CHAIN_AUDIO_KEY = "h3_chain_context_audio"
    payload.apply_patch = lambda: True
    payload.claim_patch_ownership = lambda **kwargs: (True, "test")
    payload.is_applied = lambda: True
    payload.native_payload_merge_status = lambda: {
        "native_keyframe_ref_merge": True,
        "native_keyframe_ref_audio_merge": True,
    }
    sys.modules[payload.__name__] = payload


def _load_nodes():
    _install_stubs()
    spec = importlib.util.spec_from_file_location(
        _PACKAGE + ".nodes", os.path.join(_PKG_DIR, "nodes.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _video_latent(frame_count=124):
    assert frame_count == 124
    video = torch.zeros((1, 16, 37, 1, 1), dtype=torch.float32)
    audio = torch.zeros((1, 8, 2, 207), dtype=torch.float32)
    return {"samples": Nested([video, audio])}


def _conditioning(keyframes=None):
    metadata = {
        "minimax_frame_count": 124,
        "minimax_keyframes": list(keyframes or []),
    }
    return [[torch.zeros((1, 1), dtype=torch.float32), metadata]]


class VideoVAE:
    def __init__(self):
        self.calls = []

    def encode(self, images):
        count = int(images.shape[0])
        self.calls.append(tuple(images.shape))
        if count == 1:
            latent_steps = 1
        else:
            assert count >= 5 and (count - 5) % 17 == 0
            latent_steps = 2 + 5 * ((count - 5) // 17)
        return torch.zeros(
            (1, 16, latent_steps, 1, 1), dtype=torch.float32)


class AudioVAE:
    audio_sample_rate = 32000

    def __init__(self):
        self.first_stage_model = types.SimpleNamespace(
            samples_per_latent=800)
        self.last_pcm = None

    def encode(self, waveform):
        self.last_pcm = waveform.clone()
        steps = int(waveform.shape[1]) // 800
        return torch.zeros((1, 8, 2, steps), dtype=torch.float32)


def _visual_keyframes(metadata):
    return [item for item in metadata["minimax_keyframes"]
            if item.get("latent") is not None]


def main():
    nodes = _load_nodes()
    engine = nodes.MiniMaxH3MotionContext()
    schema = engine.INPUT_TYPES()["required"]
    assert "target_start" in schema
    assert schema["context_length"][1]["max"] == 9999

    # Seven is not a native H3 video run. It must become exactly seven still
    # Guides at the requested interior location, not snap down to five.
    vae = VideoVAE()
    context = torch.zeros((7, 4, 4, 3), dtype=torch.float32)
    anchors = [
        {"resolved_frame_index": 0, "marker": "first"},
        {"resolved_frame_index": 33, "marker": "collision"},
        {"resolved_frame_index": 123, "marker": "last"},
    ]
    result, trim = engine.apply(
        _conditioning(anchors), vae, _video_latent(), context, 7,
        "video", "head", "disabled", audio_context_length=0,
        target_start=30)
    metadata = result[0][1]
    visual = _visual_keyframes(metadata)
    assert len(vae.calls) == 7
    assert [item["resolved_frame_index"] for item in visual] == list(
        range(30, 37))
    assert trim == 0
    markers = {item.get("marker") for item in metadata["minimax_keyframes"]}
    assert "first" in markers and "last" in markers
    assert "collision" not in markers

    # A native 22-frame run remains a single efficient multi-frame Guide and
    # still supports interior placement.
    vae = VideoVAE()
    context = torch.zeros((22, 4, 4, 3), dtype=torch.float32)
    result, trim = engine.apply(
        _conditioning(), vae, _video_latent(), context, 22,
        "video", "head", "disabled", audio_context_length=0,
        target_start=30)
    visual = _visual_keyframes(result[0][1])
    assert len(vae.calls) == 1
    assert len(visual) == 1
    assert visual[0]["resolved_frame_index"] == 30
    assert tuple(visual[0]["latent"].shape) == (1, 16, 7, 1, 1)
    assert trim == 0

    # A 56-frame picture boundary is 74,667 samples at 32 kHz, while its
    # nearest 40 Hz latent grid is exactly 74,400. The encoder must see the
    # final exact-grid window, beginning at sample 267, with no center shift.
    vae = VideoVAE()
    audio_vae = AudioVAE()
    context = torch.zeros((56, 4, 4, 3), dtype=torch.float32)
    picture_samples = round(56 / 24 * 32000)
    waveform = torch.arange(
        picture_samples, dtype=torch.float32).reshape(1, 1, -1)
    result, trim = engine.apply(
        _conditioning(), vae, _video_latent(), context, 56,
        "video", "head", "disabled", audio_context_length=56,
        audio_vae=audio_vae,
        context_audio={"waveform": waveform, "sample_rate": 32000})
    assert trim == 56
    assert tuple(audio_vae.last_pcm.shape) == (1, 74400, 1)
    assert float(audio_vae.last_pcm[0, 0, 0]) == 267.0
    assert float(audio_vae.last_pcm[0, -1, 0]) == 74666.0
    audio_guides = [item for item in result[0][1]["minimax_keyframes"]
                    if item.get("audio_latent") is not None]
    assert len(audio_guides) == 1
    assert tuple(audio_guides[0]["audio_latent"].shape) == (1, 8, 2, 93)

    # Conversely, 124 frames end 267 samples before the rounded 207-cell
    # audio grid. Only that partial older boundary cell is padded; the seam
    # remains the final real PCM sample.
    audio_vae = AudioVAE()
    picture_samples = round(124 / 24 * 32000)
    waveform = torch.arange(
        picture_samples, dtype=torch.float32).reshape(1, 1, -1)
    encoded, steps = nodes._encode_tail_audio(
        audio_vae, {"waveform": waveform, "sample_rate": 32000}, 124)
    assert steps == 207
    assert tuple(encoded.shape) == (1, 8, 2, 207)
    assert tuple(audio_vae.last_pcm.shape) == (1, 165600, 1)
    assert torch.count_nonzero(audio_vae.last_pcm[0, :267, 0]) == 0
    assert float(audio_vae.last_pcm[0, 268, 0]) == 1.0
    assert float(audio_vae.last_pcm[0, -1, 0]) == 165332.0

    # An interior Guide may not consume the entire target; H3 must retain
    # some frames for generation.
    try:
        engine.apply(
            _conditioning(), VideoVAE(), _video_latent(),
            torch.zeros((7, 4, 4, 3)), 7, "video", "head", "disabled",
            audio_context_length=0, target_start=117)
    except ValueError as exc:
        assert "must leave room" in str(exc)
    else:
        raise AssertionError("full-target interior Guide was accepted")

    print("internal engine: arbitrary Guide lengths and target_start pass")
    print("internal engine: exact 40 Hz PCM tail alignment and boundary pad pass")


if __name__ == "__main__":
    main()
