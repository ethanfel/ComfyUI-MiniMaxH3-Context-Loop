#!/usr/bin/env python3
"""LMS guide contracts with real native ComfyUI nodes, fake VAE/CLIP, CPU only.

Set COMFYUI_PATH to a current ComfyUI checkout. No model weights are loaded.
"""
import copy
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
candidates = [Path(os.environ["COMFYUI_PATH"])] if os.environ.get("COMFYUI_PATH") else []
candidates += [ROOT.parent / "Comfyui", ROOT.parent / "ComfyUI"]
COMFY = next(path for path in candidates if (path / "comfy/options.py").is_file())
sys.path.insert(0, str(COMFY))
sys.argv = ["h3-lms-test", "--cpu"]
import comfy.options
comfy.options.enable_args_parsing()
import folder_paths
import torch
import comfy.utils
import av as pyav
import numpy as np
from comfy_api.latest import InputImpl
from comfy_extras.nodes_lt import LTXVSeparateAVLatent
from comfy_extras.nodes_minimax_h3 import EmptyMiniMaxH3LatentAV, MiniMaxH3AddGuide


class Clip:
    def tokenize(self, prompt):
        # No kwargs accepted: accidental Qwen visual inputs fail the test.
        self.prompt = prompt
        return {"text": prompt}

    def encode_from_tokens_scheduled(self, tokens):
        self.conditioning = [[torch.zeros(1, 1, 4), {"tokens": tokens}]]
        return self.conditioning


class VAE:
    def encode(self, images):
        self.images = images
        return torch.full((1, 24, (images.shape[0] - 5) // 17 * 5 + 2,
                           images.shape[1] // 16, images.shape[2] // 16), 0.75)


class Video:
    def __init__(self, images, fps=24, count=None):
        self.images = images
        self.fps = fps
        self.count = len(images) if count is None else count
        self.decodes = 0

    def get_frame_rate(self):
        return Fraction(self.fps)

    def get_frame_count(self):
        return self.count

    def get_dimensions(self):
        return self.images.shape[2], self.images.shape[1]

    def get_components(self):
        self.decodes += 1
        return SimpleNamespace(images=self.images, audio=object())


def fails(call, message):
    try:
        call()
    except ValueError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("Expected: " + message)


def main():
    spec = importlib.util.spec_from_file_location(
        "h3_lms_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    chain = sys.modules[spec.name + ".chain_nodes"]
    upscale = sys.modules[spec.name + ".upscale_nodes"]
    lms = sys.modules[spec.name + ".lms_upscale"]
    cls = package.NODE_CLASS_MAPPINGS["MiniMaxH3ChainLMSGuide"]
    assert cls.EXPERIMENTAL and len(cls.RETURN_TYPES) == len(cls.OUTPUT_TOOLTIPS)
    node = cls()
    for raw in (5, 22, 39, 124, 362):
        # Chapter-local source beginning at scene 8, with a disposable prefix.
        source = {"index": 8, "raw_frames": raw, "delivered_frames": raw - 5,
                  "prompt": "This scene prompt must not go to Qwen."}
        state = {"index": 8, "profile_config": {"backend": "pixel"},
                 "source_manifest": {"scene_start": 8, "scene_end": 8, "segments": [source]},
                 "segments": []}
        before = copy.deepcopy(state)
        frames = torch.linspace(0, 1, raw).view(raw, 1, 1, 1).expand(raw, 32, 96, 3)
        original = frames.clone()
        clip, vae = Clip(), VAE()
        with patch.object(chain, "_find_reference_cache", side_effect=AssertionError("No ref cache")), \
                patch.object(comfy.utils, "common_upscale", side_effect=AssertionError("No RGB resize")):
            positive, latent, w, h, length, status = node.prepare(
                state, clip, vae, images=frames)
        assert (w, h, length) == (96, 32, raw)
        assert clip.prompt == lms.LMS_PROMPT
        assert "minimax_keyframes" not in clip.conditioning[0][1]
        assert "minimax_refs" not in positive[0][1]
        keyframes = positive[0][1]["minimax_keyframes"]
        assert len(keyframes) == 1 and keyframes[0]["resolved_frame_index"] == 0
        assert "audio_latent" not in keyframes[0]
        guide = keyframes[0]["latent"]
        video, audio = latent["samples"].unbind()
        assert video.shape == guide.shape == (1, 24, (raw - 5) // 17 * 5 + 2, 2, 6)
        assert audio.shape == (1, 32, 2, round(raw / 24 * 40))
        assert torch.count_nonzero(video) == torch.count_nonzero(audio) == 0
        assert torch.all(guide == 0.75) and "noise_mask" not in latent
        assert state == before and torch.equal(frames, original)
        assert torch.equal(vae.images, frames)  # no frame drop, resize or reordering
        assert vae.images is frames  # including values not representable as RGB8
        # Contract parity with core AddGuide, without its lossy RGB preprocessing.
        native = MiniMaxH3AddGuide.execute(clip.conditioning, latent, 0, vae=VAE(), image=frames)[0]
        native_guide = native[0][1]["minimax_keyframes"][0]
        assert native_guide.keys() == keyframes[0].keys()
        assert native_guide["resolved_frame_index"] == 0
        assert torch.equal(native_guide["latent"], guide)
        assert "fresh AV target" in status and "full-scene memory" in status
        movie = Video(frames)
        assert node.prepare(state, Clip(), VAE(), video=movie)[2:5] == (96, 32, raw)
        assert movie.decodes == 1
        # Container counts are not authoritative; core can even return 1 for
        # valid FFV1 clips. Check decoded frames, not metadata estimates.
        wrong_metadata = Video(frames, count=1)
        assert node.prepare(state, Clip(), VAE(), video=wrong_metadata)[4] == raw
        assert wrong_metadata.decodes == 1

    # Bad guides fail before allocation, VAE encode or text encoding.
    with patch.object(EmptyMiniMaxH3LatentAV, "execute", side_effect=AssertionError("allocated")):
        fails(lambda: node.prepare(state, None, None), "exactly one")
        fails(lambda: node.prepare(state, None, None, images=frames, video=movie), "exactly one")
        fails(lambda: node.prepare(state, None, None, images=frames[:22]), "RAW frames")
        fails(lambda: node.prepare(state, None, None, images=frames[:-1]), "RAW frames")
        fails(lambda: node.prepare(state, None, None, images=frames[..., :2]), "RGB IMAGE")
        fails(lambda: node.prepare(state, None, None, images=frames[:, :, :80]), "multiples of 32")
        fails(lambda: node.prepare(state, None, None, prompt=" ", images=frames), "caption")
        fails(lambda: node.prepare({**state, "profile_config": {"backend": "h3_latent"}},
                                   None, None, images=frames), "backend=pixel")
        fails(lambda: node.prepare({**state, "segments": [{"width": 64, "height": 32}]},
                                   None, None, images=frames), "new upscale profile")
        invalid_clock = copy.deepcopy(state)
        invalid_clock["source_manifest"]["segments"][0]["raw_frames"] = 361
        fails(lambda: node.prepare(invalid_clock, None, None, images=frames[:-1]), "H3-valid")
        wrong_fps = Video(frames, fps=30)
        fails(lambda: node.prepare(state, None, None, video=wrong_fps), "24 fps")
        assert wrong_fps.decodes == 0
        wrong_count = Video(frames[:5], count=raw)
        fails(lambda: node.prepare(state, None, None, video=wrong_count), "RAW frames")
        assert wrong_count.decodes == 1
        wrong_decoded = Video(frames[:-1], count=len(frames))
        fails(lambda: node.prepare(state, None, None, video=wrong_decoded), "RAW frames")
        assert wrong_decoded.decodes == 1
    with patch.object(VAE, "encode", return_value=torch.zeros(1, 16, 2, 2, 6)):
        fails(lambda: node.prepare(state, Clip(), VAE(), images=frames), "matching H3 video VAE")
    with patch.object(sys.modules["comfy_extras"], "nodes_minimax_h3", SimpleNamespace()):
        try:
            node.prepare(state, Clip(), VAE(), images=frames)
        except RuntimeError as exc:
            assert "Update ComfyUI" in str(exc)
        else:
            raise AssertionError("Old core should fail with installation guidance")

    # Real checkpoint save: persist only the LMS VIDEO latent, and prove that
    # the original delivered audio remains byte-identical, with no random AV
    # target sound accidentally saved for the next deferred pass.
    import json
    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": "lms", "prompt": "A quiet room.",
                                   "length": 22, "steps": 2, "seed": "42"}]}),
            "lms_test", "lms-test-cache", 32, 32, 1, "video", "head",
            "disabled", "generated_audio", 1, 22 / 24, 2, 42, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        source_state = chain._initial_state(plan, 1)
        av = {"samples": [torch.ones(1, 24, 7, 2, 2), torch.ones(1, 32, 2, 37)]}
        original_audio = {"waveform": torch.full((1, 2, round(22 / 24 * 8000)), 0.25),
                          "sample_rate": 8000}
        source = chain.MiniMaxH3ChainSegmentSave().save(
            source_state, torch.zeros(22, 32, 32, 3), av, original_audio,
            denoised_latent=av)["result"][0]
        manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps({
            "run_name": "lms_test", "lineage": [{"scene": 1, "revision": source["revision"]}]}))[0]
        originals = {p: p.read_bytes() for p in Path(temporary).rglob("*") if p.is_file()}
        _, state, _, _ = upscale.MiniMaxH3ChainUpscaleAdapter().adapt(
            manifest, "lms", "pixel", '{"experimental":"lms_v1"}', 1, 1, True, 18)
        frames = torch.full((22, 64, 96, 3), 0.5)
        # Real file-backed RGB16 input exercises native video metadata/decode.
        movie_path = Path(temporary) / "lms-guide-rgb16.mkv"
        pixels = np.full((22, 64, 96, 3), 32769, dtype=np.uint16)
        with pyav.open(str(movie_path), "w") as container:
            stream = container.add_stream("ffv1", rate=24)
            stream.width, stream.height, stream.pix_fmt = 96, 64, "gbrp16le"
            stream.time_base = stream.codec_context.time_base = Fraction(1, 24000)
            for number, image in enumerate(pixels):
                frame = pyav.VideoFrame.from_ndarray(image, format="rgb48le")
                frame.pts, frame.time_base = number * 1000, Fraction(1, 24000)
                container.mux(stream.encode(frame))
            container.mux(stream.encode())
        file_vae = VAE()
        from_file = node.prepare(state, Clip(), file_vae,
                                 video=InputImpl.VideoFromFile(str(movie_path)))
        assert from_file[2:5] == (96, 64, 22)
        assert torch.max(torch.abs(file_vae.images - 32769 / 65535)) < 2 / 65535
        assert torch.any((file_vae.images * 255 - (file_vae.images * 255).round()).abs() > 0.01)
        prepared = node.prepare(state, Clip(), VAE(), images=frames)
        sampled = prepared[1]
        sampled["samples"].tensors[0].fill_(0.6)  # fake sampler: no weights/GPU
        sampled["samples"].tensors[1].fill_(123)  # must NEVER reach saved audio
        video_latent = LTXVSeparateAVLatent.execute(sampled)[0]
        saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            state, frames, upscaled_latent=video_latent)["result"][0]
        tensors = chain._st_load(chain._absolute_output_path(saved["checkpoint"]))
        assert saved["latent_saved"] and saved["latent_layout"] == "single"
        assert saved["audio_route"] == "source checkpoint audio"
        assert torch.equal(tensors["delivered_audio"], original_audio["waveform"])
        assert "upscaled_audio" not in tensors and torch.all(tensors["upscaled_samples"] == 0.6)
        upscale._verify_upscale_segment(saved, 1)
        assert all(p.read_bytes() == data for p, data in originals.items())
    print("LMS: native IMAGE/VIDEO guide, exact clock/canvas, fresh AV target, no refs, "
          "input guards, video-only checkpoint and unchanged source audio pass (CPU, fake VAE/CLIP).")


if __name__ == "__main__":
    main()
