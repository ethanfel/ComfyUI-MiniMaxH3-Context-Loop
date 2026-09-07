#!/usr/bin/env python3
"""Release-level checks for nightly's clean 0.6 workflow baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "example_workflows"
LEGACY_ARCHIVE = EXAMPLES / "Archive" / "pre-0.6-nightly"
WORKFLOWS = {
    "Deferred De-Rope Only - MiniMax H3 0.6.json",
    "Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6.json",
    "Deferred Upscale + De-Rope - H3 LBH 3D - MiniMax H3 0.6.json",
    "Deferred Upscale - H3 LBH 3D - MiniMax H3 0.6.json",
    "Deferred Upscale - Pixel DLSS5 + USDU - EXPERIMENTAL - MiniMax H3 0.6.json",
    "Deferred Upscale - SeedVR2 Full Chain - MiniMax H3 0.6.json",
    "FL2V Normal - MiniMax H3 0.6.json",
    "I2V Normal - MiniMax H3 0.6.json",
    "I2V Studio - MiniMax H3 0.6.json",
    "Masked AV Bridge - Two Clips - MiniMax H3 0.6.json",
    "Masked AV Extension - Chain + Reference Image - MiniMax H3 0.6.json",
    "Masked AV Extension - Single Clip - MiniMax H3 0.6.json",
    "Masked Video Inpaint - MiniMax H3 0.6.json",
    "Ref2V Basic - MiniMax H3 0.6.json",
    "Ref2V Masked Video Inpaint - MiniMax H3 0.6.json",
    "Ref2V Sequential Motion - EXPERIMENTAL - MiniMax H3 0.6.json",
    "Ref2V Studio - MiniMax H3 0.6.json",
    "Ref2V Studio Source Audio - MiniMax H3 0.6.json",
    "Ref2V Tagged - MiniMax H3 0.6.json",
    "T2V Normal - MiniMax H3 0.6.json",
    "T2V Studio - MiniMax H3 0.6.json",
}
LEGACY_NAMES = {
    "Ref2V Studio Tagged - MiniMax H3.json",
    "Ref2V Studio Tagged Source Audio - MiniMax H3.json",
}
ARRIVAL_SHA256 = (
    "1e7b02dbf5d4f3d51f6e65e1c8cbf6f35b542995a7b27f278889efa32ef9462c")
DELIVERY_SHA256 = (
    "d225b876063e9fe7c611f8b0217dc961f2ed34eaee4445a0c895d9b6e30b5b76")
CRAB_HASHES = {
    "soldier_crabs_bribie_island_cc0.webm":
        "aacef1ac138445311eb61734f8ca92f8dc438b8d9ca3210fd8893aa5e925ee47",
    "soldier_crabs_inpaint_mask.png":
        "95cf18228cd3559ad980339fe9d8fccdcef25799368719b8e044cd61c6691fe4",
    "soldier_crabs_reference_cc0.png":
        "432dc2c9b0b9d0c33ed33217247fefcbe551d240959f6eefb7c04dfc99378047",
}
STALE_KEYS = {"cnr_id", "ver", "aux_id", "frontendVersion",
              "ue_links", "links_added_by_ue"}
BASE_SECTIONS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)
REF_SECTIONS = (
    "subject_definitions:", "summary:", "retention_analysis:",
    "detailed_description:", "overall_soundscape:",
    "non_diegetic_music:",
)
CANONICAL_H3_MODELS = {
    "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    "minimax_h3_video_vae_fp16.safetensors",
    "minimax_h3_audio_vae_fp32.safetensors",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def nodes(workflow: dict, node_type: str) -> list[dict]:
    return [node for node in workflow["nodes"]
            if node.get("type") == node_type]


def one(workflow: dict, node_type: str) -> dict:
    result = nodes(workflow, node_type)
    assert len(result) == 1, (node_type, len(result))
    return result[0]


def input_socket(node: dict, name: str) -> dict:
    return next(value for value in node.get("inputs", [])
                if value.get("name") == name)


def output_socket(node: dict, name: str) -> dict:
    return next(value for value in node.get("outputs", [])
                if value.get("name") == name)


def link(workflow: dict, link_id: int) -> list:
    return next(value for value in workflow["links"] if value[0] == link_id)


def origin(workflow: dict, target: dict, input_name: str) -> dict:
    linked = link(workflow, input_socket(target, input_name)["link"])
    return next(node for node in workflow["nodes"] if node["id"] == linked[1])


def validate_links(workflow: dict) -> None:
    node_map = {node["id"]: node for node in workflow["nodes"]}
    link_map = {item[0]: item for item in workflow["links"]}
    assert sorted(node_map) == list(range(1, len(node_map) + 1))
    assert sorted(link_map) == list(range(1, len(link_map) + 1))
    assert workflow["last_node_id"] == len(node_map)
    assert workflow["last_link_id"] == len(link_map)
    for link_id, value in link_map.items():
        _, source_id, source_slot, target_id, target_slot, kind = value
        assert source_id in node_map and target_id in node_map
        source = node_map[source_id]["outputs"][source_slot]
        target = node_map[target_id]["inputs"][target_slot]
        assert link_id in (source.get("links") or [])
        assert target.get("link") == link_id
        assert isinstance(kind, str) and kind
    for node in node_map.values():
        for value in node.get("inputs", []):
            if value.get("link") is not None:
                assert value["link"] in link_map
        for value in node.get("outputs", []):
            assert all(item in link_map for item in (value.get("links") or []))


def validate_layout(workflow: dict) -> None:
    rectangles = []
    for node in workflow["nodes"]:
        assert len(node.get("pos", [])) >= 2
        assert len(node.get("size", [])) >= 2
        x, y = map(float, node["pos"][:2])
        width, height = map(float, node["size"][:2])
        assert width > 0 and height > 0
        # LiteGraph position starts below the title. The former body-only check
        # missed title clipping when two nodes were stacked too closely.
        rectangles.append((node["id"], node["type"], x, y - 30, width, height + 30))
    left = min(item[2] for item in rectangles)
    top = min(item[3] for item in rectangles)
    right = max(item[2] + item[4] for item in rectangles)
    bottom = max(item[3] + item[5] for item in rectangles)
    assert right - left <= 10000, "workflow layout is excessively wide"
    assert bottom - top <= 4600, "workflow layout is excessively tall"
    for index, first in enumerate(rectangles):
        _, _, ax, ay, aw, ah = first
        for second in rectangles[index + 1:]:
            _, _, bx, by, bw, bh = second
            assert not (ax < bx + bw and bx < ax + aw
                        and ay < by + bh and by < ay + ah), (first, second)
    assert workflow["groups"][0]["title"] == "01 • PROJECT & SCENES"
    membership = {item[0]: 0 for item in rectangles}
    for group in workflow["groups"]:
        gx, gy, gw, gh = map(float, group["bounding"])
        members = [item for item in rectangles
                   if gx <= item[2] and gy <= item[3]
                   and item[2] + item[4] <= gx + gw
                   and item[3] + item[5] <= gy + gh]
        assert members, group["title"]
        for member in members:
            membership[member[0]] += 1
    assert set(membership.values()) == {1}, "Every node must fit inside exactly one group, including its title"
    groups = {group["title"]: group for group in workflow["groups"]}
    author_x, _, author_width, _ = map(float, groups["01 • PROJECT & SCENES"]["bounding"])
    for title, runtime in groups.items():
        if title != "01 • PROJECT & SCENES":
            assert author_x + author_width <= runtime["bounding"][0], "Project controls must precede runtime"
    for index, first in enumerate(workflow["groups"]):
        ax, ay, aw, ah = first["bounding"]
        for second in workflow["groups"][index + 1:]:
            bx, by, bw, bh = second["bounding"]
            assert not (ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah), (first["title"], second["title"])
    recovery_loaders = [n for n in workflow["nodes"]
                        if n["type"] == "MiniMaxH3ChainManifestLoad" and n["mode"] == 2]
    recovery_assemblers = [n for n in workflow["nodes"]
                           if n["type"] == "MiniMaxH3ChainAssemble" and n["mode"] == 2]
    if recovery_loaders and recovery_assemblers:
        assert recovery_loaders[0]["pos"][0] < recovery_assemblers[0]["pos"][0]
    assert not any(n.get("title", "").startswith("RECOVERY") and n["mode"] != 2
                   for n in workflow["nodes"]), "Bypassed runtime controls are not a recovery path"


def validate_clean_metadata(workflow: dict) -> None:
    assert workflow["revision"] == 0
    assert workflow["version"] == 0.4  # ComfyUI UI-workflow schema version.
    assert set(workflow["extra"]) == {"ds", "comfyui_mcp"}
    assert workflow["id"] == workflow["extra"]["comfyui_mcp"][
        "workflow_uuid"]

    def walk(value):
        if isinstance(value, dict):
            assert not (set(value) & STALE_KEYS), set(value) & STALE_KEYS
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(workflow)
    serialized = json.dumps(workflow, ensure_ascii=False)
    for stale_text in (
            "jigen_market_garden", "@style_base", "@interior",
            "Classic Doom", "0.5 PREFLIGHT", "MiniMaxH3ChainRunManager",
            "MiniMax-H3/", "MiniMaw-H3/"):
        assert stale_text not in serialized, stale_text
    for node in workflow["nodes"]:
        assert node.get("properties") == {
            "Node name for S&R": node["type"]}


def validate_modern_authoring(workflow: dict, path: Path) -> None:
    legacy = {"MiniMaxH3ChainPlan", "MiniMaxH3ChainPolicy",
              "MiniMaxH3ChainRunManager", "MiniMaxH3Legacy04PolicyAdapter"}
    assert not [node for node in workflow["nodes"]
                if node["type"] in legacy], path.name
    plans = nodes(workflow, "MiniMaxH3ChainPlanModern")
    if not plans:
        return
    plan = one(workflow, "MiniMaxH3ChainPlanModern")
    profile = one(workflow, "MiniMaxH3GenerationProfile")
    assert origin(workflow, plan, "chain_policy") == profile
    assert len(plan["widgets_values"]) == 12
    assert plan["widgets_values"][8] == 20
    document = json.loads(plan["widgets_values"][0])
    assert document["shots"]
    assert all(shot.get("steps") == 20 for shot in document["shots"])
    assert all(str(shot.get("seed", "")) for shot in document["shots"])

    if path.name.startswith(("T2V", "I2V", "FL2V")):
        for shot in document["shots"]:
            text = str(shot["prompt"])
            positions = [text.index(section) for section in BASE_SECTIONS]
            assert positions == sorted(positions) and positions[0] == 0
            assert "Doom" not in text and "Market Garden" not in text
    if path.name.startswith("Ref2V") and "Masked" not in path.name:
        for shot in document["shots"]:
            text = str(shot["prompt"])
            positions = [text.index(section) for section in REF_SECTIONS]
            assert positions == sorted(positions) and positions[0] == 0
            assert "<Subject 1>" in text
            assert "Doom" not in text and "Market Garden" not in text


def validate_studio(workflow: dict, path: Path) -> None:
    studios = nodes(workflow, "MiniMaxH3ChainPlanStudio")
    if not studios:
        return
    studio = one(workflow, "MiniMaxH3ChainPlanStudio")
    carousel = one(workflow, "MiniMaxH3ProjectAssetManager")
    manager = one(workflow, "MiniMaxH3ChainCheckpointManager")
    plan = one(workflow, "MiniMaxH3ChainPlanModern")
    loop_start = one(workflow, "MiniMaxH3ChainLoopStart")
    assert origin(workflow, plan, "project_assets") == carousel
    assert origin(workflow, studio, "project_assets") == carousel
    assert origin(workflow, studio, "tagged_references") == carousel
    assert origin(workflow, manager, "plan") == studio
    assert origin(workflow, loop_start, "plan") == studio
    for external in nodes(workflow, "MiniMaxH3ChainExternalVideo"):
        assert origin(workflow, external, "plan") == studio
    assert manager["size"][0] >= 1200 and manager["size"][1] >= 900
    assert carousel["size"][0] >= 760 and carousel["size"][1] >= 700
    author_group = next(group for group in workflow["groups"]
                        if group["title"] == "01 • PROJECT & SCENES")
    assert float(author_group["bounding"][2]) <= 4300
    assert not nodes(workflow, "MiniMaxH3ChainRunManager")
    if path.name.startswith("Ref2V Studio"):
        assert not nodes(workflow, "MiniMaxH3TaggedPictureReference")
        wrapper = one(workflow, "MiniMaxH3TaggedReferenceToVideo")
        assert origin(workflow, wrapper, "references") == carousel
    if "Source Audio" in path.name:
        note = one(workflow, "Note")
        instructions = note["widgets_values"][0]
        for required in ("IMPORT YOUR SOUNDTRACK HERE", "Project Asset Carousel",
                         "Audio use → Project timeline source", "Lip-sync to source audio",
                         "preset alone does not load an audio file", "Carousel.project_assets"):
            assert required in instructions, f"Missing source-audio setup note: {required}"
        assert note["size"][1] >= 70 + 20 * len(instructions.splitlines())
        profile = one(workflow, "MiniMaxH3GenerationProfile")
        assert profile["widgets_values"][1] == "Lip-sync to source audio"
        assert not nodes(workflow, "LoadAudio")
        assert not nodes(workflow, "MiniMaxH3SourceTimeline")
        context = one(workflow, "MiniMaxH3ChainContext")
        audio_vae = origin(workflow, context, "audio_vae")
        assert audio_vae["type"] == "VAELoader"
        assert "audio_vae" in audio_vae["widgets_values"][0]


def validate_execution_regressions(workflow: dict, path: Path) -> None:
    for start in nodes(workflow, "MiniMaxH3ChainLoopStart"):
        assert start["widgets_values"][0] == 1, "Release examples must start a fresh project"
    for external in nodes(workflow, "MiniMaxH3ChainExternalVideo"):
        assert origin(workflow, external, "source_video")["type"] == "LoadVideo"
        assert input_socket(external, "source_frames")["link"] is None
        assert input_socket(external, "source_audio")["link"] is None
    for target in nodes(workflow, "MiniMaxH3ContexLoopSourceAVTarget"):
        components = origin(workflow, target, "source_frames")
        assert components["type"] == "GetVideoComponents"
        assert origin(workflow, components, "video")["type"] == "LoadVideo"
        # The core loader's native frame rate is not necessarily H3's 24 fps.
        assert origin(workflow, target, "source_audio") == components
        assert origin(workflow, target, "source_fps") == components
        for field, output in (("source_frames", "images"), ("source_audio", "audio"),
                              ("source_fps", "fps")):
            wire = link(workflow, input_socket(target, field)["link"])
            assert components["outputs"][wire[2]]["name"] == output
    for bridge in nodes(workflow, "MiniMaxH3ContexMaskedAVBridge"):
        assert not nodes(workflow, "VHS_LoadVideo")
        prepare = one(workflow, "MiniMaxH3ReferenceVideoPrepare")
        assert prepare["widgets_values"][0] == 311
        resize = one(workflow, "ImageScale")
        conditioning = one(workflow, "MiniMaxH3ImageToVideo")
        assert resize["widgets_values"][1:3] == conditioning["widgets_values"][1:3]
        assert conditioning["widgets_values"][3] == 192  # 39 + 114 + 39
        for side, start, duration in (("start", 0, 99 / 24), ("end", 213 / 24, 0)):
            components = origin(workflow, bridge, side + "_frames")
            assert components["type"] == "GetVideoComponents"
            assert origin(workflow, bridge, side + "_audio") == components
            sliced = origin(workflow, components, "video")
            assert sliced["type"] == "Video Slice"
            assert sliced["widgets_values"] == [start, duration, True]
            source = origin(workflow, sliced, "video")
            assert source["type"] == "CreateVideo" and source["widgets_values"][0] == 24
            assert origin(workflow, source, "images") == resize
            assert origin(workflow, source, "audio") == prepare
            assert any(origin(workflow, concat, field) == components
                       for concat in nodes(workflow, "AudioConcat")
                       for field in ("audio1", "audio2"))


def validate_assets() -> None:
    assets = EXAMPLES / "assets"
    expected = {
        "h3_v06_courier_greenhouse_arrival.png": ARRIVAL_SHA256,
        "h3_v06_courier_greenhouse_delivery.png": DELIVERY_SHA256,
        **CRAB_HASHES,
    }
    for name, digest in expected.items():
        path = assets / name
        assert path.is_file(), path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def main() -> None:
    paths = sorted(EXAMPLES.glob("*.json"))
    assert {path.name for path in paths} == WORKFLOWS
    assert all("MiniMax H3 0.6.json" in path.name for path in paths)
    assert not (LEGACY_NAMES & {path.name for path in paths})
    assert LEGACY_ARCHIVE.is_dir()
    assert len(list(LEGACY_ARCHIVE.glob("*.json"))) == 19
    assert LEGACY_NAMES <= {path.name for path in LEGACY_ARCHIVE.glob("*.json")}
    uuids = set()
    for path in paths:
        workflow = load(path)
        validate_links(workflow)
        validate_layout(workflow)
        validate_clean_metadata(workflow)
        validate_modern_authoring(workflow, path)
        validate_studio(workflow, path)
        validate_execution_regressions(workflow, path)
        note = one(workflow, "Note")
        assert note["title"] == "START HERE • NIGHTLY"
        guide = (EXAMPLES / "guides" / path.with_suffix(".md").name).read_text()
        assert "nightly" in guide and "not nightly" not in guide
        uuids.add(workflow["id"])
        pixel = bool(nodes(workflow, "MiniMaxH3ChainUpscalePixelConditioning"))
        derope_fast = path.name == "Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6.json"
        for node in workflow["nodes"]:
            if node["type"] in {"UNETLoader", "CLIPLoader", "VAELoader"}:
                values = node.get("widgets_values") or []
                if values and values[0] in CANONICAL_H3_MODELS:
                    assert "/" not in values[0] and "\\" not in values[0]
            if node["type"] == "KSamplerSelect":
                expected = "gradient_estimation" if derope_fast else "er_sde" if pixel else "res_multistep"
                assert node["widgets_values"] == [expected]
            elif node["type"] == "BasicScheduler":
                assert node["widgets_values"][:2] == (["beta", 3] if pixel else ["simple", 20])
            elif node["type"] == "H3InjectSchedule":
                assert node["widgets_values"][:2] == (["beta", 6] if derope_fast else ["simple", 20])
    assert len(uuids) == len(paths)

    i2v = load(EXAMPLES / "I2V Normal - MiniMax H3 0.6.json")
    i2v_load = one(i2v, "LoadImage")
    assert i2v_load["widgets_values"][0] == (
        "h3_v06_courier_greenhouse_arrival.png")
    fl2v = load(EXAMPLES / "FL2V Normal - MiniMax H3 0.6.json")
    assert {node["widgets_values"][0] for node in nodes(fl2v, "LoadImage")} == {
        "h3_v06_courier_greenhouse_arrival.png",
        "h3_v06_courier_greenhouse_delivery.png",
    }
    tagged = load(EXAMPLES / "Ref2V Tagged - MiniMax H3 0.6.json")
    assert {node["widgets_values"][0]
            for node in nodes(tagged, "MiniMaxH3TaggedPictureReference")} == {
        "courier_arrival", "greenhouse_delivery"}
    sequential = load(
        EXAMPLES / "Ref2V Sequential Motion - EXPERIMENTAL - MiniMax H3 0.6.json")
    video_ref = one(sequential, "MiniMaxH3TaggedVideoReference")
    assert video_ref["widgets_values"] == [
        "courier_motion", "courier_motion_audio", "sequential"]
    sequential_plan = json.loads(one(
        sequential, "MiniMaxH3ChainPlanModern")["widgets_values"][0])
    assert all("@courier_motion" in shot["prompt"]
               and "@courier_motion_audio" in shot["prompt"]
               for shot in sequential_plan["shots"])
    validate_assets()
    print(f"H3 nightly 0.6 baseline: {len(paths)} clean UI documents, current Plan/Profile "
          "authoring, Studio Carousel + Checkpoint Manager, fresh prompts and "
          "references, valid links, collision-free layouts, and legacy archive pass")


if __name__ == "__main__":
    main()
