"""Author-recipe and checkpoint audio routing for the nightly LMS example."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "Deferred Upscale - DLSS5 + LMS Guide - EXPERIMENTAL - MiniMax H3 0.6.json"
recipe = json.loads((ROOT / "tools/v06/recipes" / NAME).read_text())
nodes = {node["key"]: node for node in recipe["nodes"]}


def wire(node, field, source, output):
    assert nodes[node]["inputs"][field] == [source, output], (node, field)


assert nodes["adapter"]["settings"]["backend"] == "pixel"
assert nodes["adapter"]["settings"]["end_clip"] == 1
assert nodes["adapter"]["settings"]["save_latent"] is True
wire("adapter", "source_manifest", "manager", "selected_manifest")
wire("current", "state", "adapter", "state")
wire("dlss", "images", "current", "images")
wire("guide", "images", "dlss", "images")
wire("guide", "state", "current", "state")
wire("shift", "model", "model", "MODEL")
assert nodes["shift"]["settings"] == {"shift_video": 12, "shift_audio": 3}
wire("turbo", "model", "shift", "MODEL")
wire("lms", "model", "turbo", "MODEL")
assert nodes["turbo"]["settings"] == {
    "lora_name": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", "strength_model": 1.0}
assert nodes["lms"]["settings"] == {
    "lora_name": "minimax_h3_lms_v1.0_r64.safetensors", "strength_model": 1.0}
wire("guider", "model", "lms", "MODEL")
wire("schedule", "model", "lms", "MODEL")
assert nodes["schedule"]["settings"] == {"scheduler": "simple", "steps": 8, "denoise": 1.0}
assert nodes["sampler"]["settings"] == {"sampler_name": "euler"}
wire("noise", "noise_seed", "current", "seed")
wire("sample", "latent_image", "guide", "latent")
wire("guider", "conditioning", "guide", "positive")
wire("sample", "noise", "noise", "NOISE")
wire("sample", "guider", "guider", "GUIDER")
wire("sample", "sampler", "sampler", "SAMPLER")
wire("sample", "sigmas", "schedule", "SIGMAS")
wire("split", "av_latent", "sample", "denoised_output")
wire("decode", "samples", "split", "video_latent")
for target in ("save", "end"):
    wire(target, "state", "current", "state")
    wire(target, "images", "decode", "IMAGE")
    wire(target, "upscaled_latent", "split", "video_latent")
wire("end", "segment", "save", "segment")
wire("end", "flow", "adapter", "flow")
wire("assemble", "manifest", "end", "manifest")
assert "recovered_audio" not in nodes["save"]["inputs"]
assert "source_audio" not in nodes["assemble"]["inputs"]
assert not {"MiniMaxH3ReferenceToVideo", "MiniMaxH3ChainUpscalePixelConditioning",
            "UltimateSDUpscaleNoUpscaleGuider", "MiniMaxH3ChainPass2Prepare",
            "VHS_LoadVideo", "AIToolkitMiniMaxH3RefVideo", "VAEDecodeAudio"} & {
                node["type"] for node in nodes.values()}
assert not any(source == ["split", "audio_latent"] for node in nodes.values()
               for source in node["inputs"].values())
guide = (ROOT / "example_workflows/guides" / NAME.replace(".json", ".md")).read_text()
assert "Alissonerdx" in guide and "FULL-SCENE" in guide and "nightly" in guide
assert "not a GPU quality or memory validation" in guide
print("LMS workflow: published recipe, fresh-noise path, original audio, full video checkpoint and source attribution pass.")
