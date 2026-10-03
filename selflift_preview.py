"""Execution-only tiny previews. Uses KJ's same decoder as Preview Override."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import uuid

from .processing_persistence import sync_file


def tiny_models():
    try:
        import folder_paths
        names = [v for v in folder_paths.get_filename_list("vae_approx") if "taeh3" in v.lower()]
    except (ImportError, AttributeError, KeyError):
        names = []
    return sorted(names) or ["taeh3.safetensors"]


def decoder_loader():
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("ModelPreviewOverrideKJ")
    if cls is None:
        raise ValueError("SelfLift Seed Hunt needs KJNodes' Tiny VAE support (Model Preview Override KJ).")
    module = importlib.import_module(cls.__module__.rsplit(".", 1)[0] + ".tiny_vae")
    return module.load_tiny_vae_decoder


def check_preview(name):
    import av  # Fail before spending time on a low pass if the encoder is unavailable.
    import folder_paths
    decoder_loader()
    if not folder_paths.get_full_path("vae_approx", name):
        raise ValueError("Install taeh3.safetensors in models/vae_approx for SelfLift Seed Hunt previews.")
    av.codec.Codec("libx264", "w")


def preview_frames(decoder, video, raw_frames):
    """Stream RGB frames at H3's (1,4,4,4,4) token timing, never a full GPU clip."""
    import torch
    from comfy.model_management import throw_exception_if_processing_interrupted
    if int(video.shape[0]) != 1 or int(video.shape[1]) != 24 or decoder.latent_channels != 24:
        raise ValueError("SelfLift Seed Hunt expects one H3 video and a 24-channel tiny decoder.")
    if getattr(decoder, "decodes_prefix", False):
        decoded = decoder.decode_video(video)
        if len(decoded) < raw_frames:
            raise ValueError("Tiny decoder returned fewer frames than the scene.")
        for frame in decoded[:raw_frames]:
            throw_exception_if_processing_interrupted()
            if frame.dtype != torch.uint8:
                frame = frame.clamp(0, 1).mul(255).to(torch.uint8)
            yield frame.permute(1, 2, 0).contiguous().cpu().numpy()
        return
    emitted = 0
    for index in range(video.shape[2]):
        throw_exception_if_processing_interrupted()
        frame = decoder.decode(video[:, :, index])[0]
        pixels = frame.clamp(0, 1).mul(255).to(torch.uint8).permute(1, 2, 0).contiguous().cpu().numpy()
        del frame
        for _ in range((1, 4, 4, 4, 4)[index % 5]):
            if emitted >= raw_frames:
                return
            yield pixels
            emitted += 1
    if emitted != raw_frames:
        raise ValueError("SelfLift tiny preview frame count does not match the scene.")


def save_preview(video, path, tiny_vae, raw_frames, trim_frames=0):
    import av
    import torch
    from .selflift_hunt_store import _sync_directory
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + "." + uuid.uuid4().hex + ".tmp.mp4")
    decoder = decoder_loader()(tiny_vae)
    if decoder is None:
        raise ValueError("Could not load the H3 tiny VAE; the low pass is saved and can be retried.")
    try:
        with torch.inference_mode(), av.open(str(temporary), "w", format="mp4") as container:
            stream = None
            for index, pixels in enumerate(preview_frames(decoder, video, int(raw_frames))):
                if index < int(trim_frames):
                    continue
                if stream is None:
                    stream = container.add_stream("libx264", rate=24)
                    stream.width, stream.height = pixels.shape[1], pixels.shape[0]
                    stream.pix_fmt = "yuv420p"
                    stream.options = {"crf": "20", "preset": "veryfast"}
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                for packet in stream.encode(frame):
                    container.mux(packet)
            if stream is None:
                raise ValueError("SelfLift preview is empty after context trim.")
            for packet in stream.encode():
                container.mux(packet)
        sync_file(temporary)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        del decoder
        temporary.unlink(missing_ok=True)
