#!/usr/bin/env python3
"""CPU smoke test for H3 chain timing, segments, checkpoints and resume.

Uses the adjacent ComfyUI checkout for its real node/runtime modules, but no
model or GPU.  It encodes two tiny H.264 segments, resumes clip 2 from clip 1's
safetensors checkpoint, and assembles both source-track and generated-audio
outputs with ffmpeg.
"""

import asyncio
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMFY_CANDIDATES = ([pathlib.Path(os.environ["COMFYUI_PATH"])]
                    if os.environ.get("COMFYUI_PATH") else [])
COMFY_CANDIDATES += [ROOT.parent / "Comfyui", ROOT.parent / "ComfyUI"]
COMFY = next((path for path in COMFY_CANDIDATES
              if (path / "comfy" / "options.py").is_file()), None)
if COMFY is None:
    raise SystemExit("adjacent ComfyUI checkout not found")

sys.path.insert(0, str(COMFY))
sys.argv = ["h3-chain-smoke", "--cpu"]
import comfy.options  # noqa: E402

comfy.options.enable_args_parsing()
import folder_paths  # noqa: E402
import torch  # noqa: E402
import execution  # noqa: E402
import nodes as comfy_nodes  # noqa: E402
from PIL import Image as PILImage  # noqa: E402
from safetensors import safe_open  # noqa: E402


def load_package():
    spec = importlib.util.spec_from_file_location(
        "h3_chain_smoke_package",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    return package, sys.modules[spec.name + ".chain_nodes"]


def av_latent(video_t=1, audio_t=9):
    return {
        "samples": [
            torch.zeros((1, 16, video_t, 2, 2), dtype=torch.float32),
            torch.zeros((1, 32, 2, audio_t), dtype=torch.float32),
        ]
    }


def audio_for_frames(frames, sample_rate=8000):
    samples = round(frames / 24.0 * sample_rate)
    return {
        "waveform": torch.zeros((1, 2, samples), dtype=torch.float32),
        "sample_rate": sample_rate,
    }


class FakeDynamicPrompt:
    def __init__(self, prompt):
        self.prompt = prompt

    def get_node(self, node_id):
        return self.prompt[str(node_id)]

    def get_display_node_id(self, node_id):
        return str(node_id)

    def get_original_prompt(self):
        return self.prompt


def main():
    package, chain = load_package()

    segment_prompt = {
        "26": {"class_type": "MiniMaxH3ChainSegmentSave", "inputs": {}},
        "27": {"class_type": "MiniMaxH3ChainReview", "inputs": {
            "segment": ["26", 0],
        }},
    }
    assert chain._has_downstream_review_gate(
        FakeDynamicPrompt(segment_prompt), "26")
    segment_prompt["27"]["inputs"]["segment"] = ["26", 1]
    assert not chain._has_downstream_review_gate(
        FakeDynamicPrompt(segment_prompt), "26")
    segment_prompt["27"]["class_type"] = "MiniMaxH3ChainLoopEnd"
    segment_prompt["27"]["inputs"]["segment"] = ["26", 0]
    assert not chain._has_downstream_review_gate(
        FakeDynamicPrompt(segment_prompt), "26")

    cleanup_input = chain.MiniMaxH3ChainLoopEnd.INPUT_TYPES()[
        "optional"]["between_scene_cleanup"]
    assert cleanup_input[0] == ["off", "unload_models", "fresh_scene"]
    cleanup_calls = []
    import comfy.memory_management as memory_management
    import comfy.model_management as model_management
    original_free_pins = model_management.free_pins
    original_unload = model_management.unload_all_models
    original_empty_cache = model_management.soft_empty_cache
    original_cleanup_models = model_management.cleanup_models_gc
    original_ram_release = memory_management.extra_ram_release
    original_collect = chain.gc.collect
    model_management.free_pins = lambda size, evict_active=False: (
        cleanup_calls.append(("pins", size, evict_active)) or 12_345)
    model_management.unload_all_models = lambda: cleanup_calls.append(
        ("unload",))
    model_management.soft_empty_cache = lambda force=False: cleanup_calls.append(
        ("empty", force))
    model_management.cleanup_models_gc = lambda: cleanup_calls.append(
        ("models_gc",))
    memory_management.extra_ram_release = (
        lambda target, free_active=False: (
            cleanup_calls.append(("ram", target, free_active)) or 54_321))
    chain.gc.collect = lambda: cleanup_calls.append(("gc",)) or 7
    try:
        untouched = chain._release_loop_boundary_resources("off", 1)
        assert cleanup_calls == [] and untouched["policy"] == "off"

        unloaded = chain._release_loop_boundary_resources(
            "unload_models", 1)
        assert cleanup_calls == [("unload",), ("empty", True)]
        assert unloaded["pinned_bytes"] == 0
        assert unloaded["cache_bytes"] == 0

        cleanup_calls.clear()
        fresh = chain._release_loop_boundary_resources("fresh_scene", 2)
        assert cleanup_calls == [
            ("ram", sys.maxsize, True),
            ("gc",),
            ("models_gc",),
            ("pins", sys.maxsize, True),
            ("unload",),
            ("empty", True),
        ]
        assert fresh["pinned_bytes"] == 12_345
        assert fresh["cache_bytes"] == 54_321
        assert fresh["collected_objects"] == 7
        try:
            chain._release_loop_boundary_resources("erase_everything", 2)
        except ValueError as exc:
            assert "between_scene_cleanup" in str(exc)
        else:
            raise AssertionError("unknown loop cleanup policy was accepted")
    finally:
        model_management.free_pins = original_free_pins
        model_management.unload_all_models = original_unload
        model_management.soft_empty_cache = original_empty_cache
        model_management.cleanup_models_gc = original_cleanup_models
        memory_management.extra_ram_release = original_ram_release
        chain.gc.collect = original_collect
    print("loop cleanup: cache-first eviction and model unload passed")

    fixed_now = datetime(2026, 8, 11, 14, 5, 9)
    assert chain._expand_filename_date(
        "render_%date:yyyy-MM-dd%_%hour%-%minute%-%second%", fixed_now
    ) == "render_2026-08-11_14-05-09"
    # Collision checking resolves managed output paths. Keep this fixture in
    # its own declared output root, not outside the real ComfyUI output tree.
    with tempfile.TemporaryDirectory() as version_dir, patch.object(
            chain, '_output_root', return_value=version_dir):
        original = pathlib.Path(version_dir) / "render.mp4"
        original.touch()
        (pathlib.Path(version_dir) / "render_001.mp4").touch()
        assert chain._available_versioned_path(str(original)).endswith(
            "render_002.mp4"
        )
    print("assemble filenames: date expansion and collision versioning passed")

    async def review_route_check():
        token = "review-route-smoke"
        future = asyncio.get_running_loop().create_future()
        review_plan = package.NODE_CLASS_MAPPINGS[
            "MiniMaxH3ChainPlan"]().build(
                json.dumps({
                    "prompt_prefix": "Shared only.",
                    "shots": [{
                        "id": "review_route",
                        "prompt": "Original scene prompt.",
                        "length": 22,
                        "steps": 8,
                        "seed": "7",
                    }],
                }),
                "review_route_smoke", "", 64, 64, 22,
                "video", "head", "disabled", "generated_audio", 22,
                1.0, 8, 0, 18, 0, "guide")[0]
        chain._PENDING_REVIEWS[token] = {
            "future": future,
            "loop": asyncio.get_running_loop(),
            "public": {
                "token": token,
                "prompt_prefix": "Shared only.",
                "clip_index": 1,
            },
            "plan": review_plan,
            "current_seed": 7,
            "current_length": 22,
        }

        class Request:
            async def json(self):
                return {
                    "token": token,
                    "action": "retry",
                    "scene_prompt": "",
                    "seed": "18446744073709551615",
                }

        try:
            response = await chain._submit_review_decision(Request())
            assert response.status == 200
            decision = await future
            assert decision["action"] == "retry"
            assert decision["scene_prompt"] == ""
            assert decision["seed"] == 18446744073709551615
        finally:
            chain._PENDING_REVIEWS.pop(token, None)

    asyncio.run(review_route_check())
    print("review: async decision route preserves exact uint64 seeds")
    required = {
        "MiniMaxH3ChainPlan", "MiniMaxH3ChainScenePromptEditor",
        "MiniMaxH3ChainRunManager",
        "MiniMaxH3ChainFirstSceneImage",
        "MiniMaxH3ReferenceVideoPrepare",
        "MiniMaxH3ScheduledPictureReference",
        "MiniMaxH3ScheduledVideoReference",
        "MiniMaxH3ScheduledAudioReference",
        "MiniMaxH3ScheduledReferenceToVideo",
        "MiniMaxH3TaggedSceneOptions",
        "MiniMaxH3CurrentTaggedScenePack",
        "MiniMaxH3CurrentTaggedReferenceScene",
        "MiniMaxH3SceneDataExtract",
        "MiniMaxH3ChainExternalVideo",
        "MiniMaxH3ChainLoopStart",
        "MiniMaxH3ChainCurrent", "MiniMaxH3ChainContext",
        "MiniMaxH3DriftControlModelPatch",
        "MiniMaxH3ChainSegmentSave", "MiniMaxH3ChainLoopEnd",
        "MiniMaxH3ChainManifestLoad", "MiniMaxH3ChainExportPNG",
        "MiniMaxH3ChainLatentVideoAdapter",
        "MiniMaxH3ChainAssemble",
        "MiniMaxH3LoopTrim",
        "MiniMaxH3ContexLoopSeamProbe",
        "MiniMaxH3ContexTrimSourceAV",
        "MiniMaxH3ContexMaskedTarget",
        "MiniMaxH3ContexMaskGridPreview",
        "MiniMaxH3ContexMasterAudioMaskedAV",
    }
    assert required <= set(package.NODE_CLASS_MAPPINGS)
    upstream_ids = {
        "MiniMaxH3MotionContext",
        "MiniMaxH3MotionContextTrim",
        "MiniMaxH3MotionContextSaveLatent",
        "MiniMaxH3MotionContextLoadLatent",
        "MiniMaxH3SongMaskedAVContext",
        "MiniMaxH3ContexSongMaskedAVContext",
    }
    assert not upstream_ids.intersection(package.NODE_CLASS_MAPPINGS)
    retired_mask_pack_ids = {
        "MiniMaxH3TrimSourceAV",
        "MiniMaxH3PerRowMaskPatch",
        "MiniMaxH3SetGenerationMask",
        "MiniMaxH3MaskGridPreview",
    }
    assert not retired_mask_pack_ids.intersection(package.NODE_CLASS_MAPPINGS)
    layout_patch = sys.modules[package.__name__ + ".patch_layout"]
    payload_patch = sys.modules[package.__name__ + ".patch_payload"]
    for module in (layout_patch, payload_patch):
        assert module.MC_KEY == "motion_context_index"
        assert module.MC_AUDIO_KEY == "motion_context_audio_end_frame"
    assert layout_patch.PATCH_MARKER == "_h3_motion_context_layout_patch"
    assert payload_patch.PATCH_MARKER == "_h3_motion_context_payload_patch"
    print("coexistence: node ids are disjoint and runtime patch ABI is shared")
    assert package.WEB_DIRECTORY == "./web"
    assert (ROOT / "web" / "h3_chain_plan_editor.js").is_file()
    assert (ROOT / "web" / "h3_chain_plan_core.mjs").is_file()
    assert (ROOT / "web" / "h3_prompt_completion_core.mjs").is_file()
    assert (ROOT / "web" / "h3_chain_cancel_reroll.js").is_file()
    assert (ROOT / "web" / "h3_chain_cancel_reroll_core.mjs").is_file()
    assert (ROOT / "web" / "h3_chain_scene_prompt_editor.js").is_file()
    assert (ROOT / "web" / "h3_reference_autoconnect.js").is_file()
    assert (ROOT / "web" / "h3_reference_autoconnect_core.mjs").is_file()
    assert (ROOT / "web" / "h3_scene_data_extract.js").is_file()
    assert (ROOT / "web" / "h3_scene_data_core.mjs").is_file()
    workflow_path = (ROOT / "example_workflows" / "Archive" /
                     "Looping Seamless Chain Global Refs Example - MiniMax H3.json")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow_types = {node.get("type") for node in workflow["nodes"]}
    assert "MiniMaxH3LoopTrim" in workflow_types
    assert not upstream_ids.intersection(workflow_types)
    loop_nodes = [
        node for node in workflow["nodes"]
        if node.get("type") == "MiniMaxH3LoopTrim"
        or str(node.get("type", "")).startswith("MiniMaxH3Chain")
    ]
    assert loop_nodes and all(
        node.get("properties", {}).get("aux_id") ==
        "ethanfel/ComfyUI-MiniMaxH3-Contex-Loop"
        for node in loop_nodes)
    workflow_start = next(
        node for node in workflow["nodes"]
        if node.get("type") == "MiniMaxH3ChainLoopStart")
    assert workflow_start.get("widgets_values") == [1, ""]
    assert "SEGMENT" not in str(workflow_start.get("title", "")).upper()
    print("workflow: loop node ids and package metadata use the new namespace")

    def assert_workflow_links(payload):
        nodes = {int(node["id"]): node for node in payload["nodes"]}
        links = {int(link[0]): link for link in payload["links"]}
        assert len(nodes) == len(payload["nodes"])
        assert len(links) == len(payload["links"])
        for link_id, link in links.items():
            _, origin_id, origin_slot, target_id, target_slot, _ = link
            origin = nodes[int(origin_id)]
            target = nodes[int(target_id)]
            assert link_id in (origin["outputs"][int(origin_slot)].get("links") or [])
            assert target["inputs"][int(target_slot)].get("link") == link_id
        for node in nodes.values():
            for input_socket in node.get("inputs", []):
                link_id = input_socket.get("link")
                assert link_id is None or int(link_id) in links
            for output_socket in node.get("outputs", []):
                for link_id in output_socket.get("links") or []:
                    assert int(link_id) in links

    fl2va_path = (ROOT / "example_workflows" / "Archive" /
                  "Looping Core FL2VA V2 - MiniMax H3.json")
    fl2va = json.loads(fl2va_path.read_text(encoding="utf-8"))
    assert_workflow_links(fl2va)
    fl2va_types = {node.get("type") for node in fl2va["nodes"]}
    assert {
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ChainScenePromptEditor",
        "MiniMaxH3ChainReview",
    } <= fl2va_types
    assert not any(str(value).startswith("MiniMaxH3Scheduled")
                   for value in fl2va_types)
    assert "LoadAudio" not in fl2va_types
    fl_plan_node = next(node for node in fl2va["nodes"]
                        if node.get("type") == "MiniMaxH3ChainPlan")
    fl_plan = json.loads(fl_plan_node["widgets_values"][0])
    assert len(fl_plan["shots"]) == 1
    assert fl_plan["shots"][0]["length"] == 124
    fl_prompt = "\n".join(fl_plan["shots"][0]["prompt"])
    assert fl_prompt.startswith(
        "How the reference pictures align with the target video")
    fl_sections = [
        "integrated_multimodal_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    ]
    assert [fl_prompt.index(value) for value in fl_sections] == sorted(
        fl_prompt.index(value) for value in fl_sections)
    assert fl_plan_node["widgets_values"][9] == "generated_audio"
    fl_conditioner = next(node for node in fl2va["nodes"]
                          if node.get("type") == "MiniMaxH3ImageToVideo")
    assert {input_socket["name"] for input_socket in fl_conditioner["inputs"]
            if input_socket.get("link") is not None} >= {
        "first_frame", "last_frame", "prompt", "width", "height", "length",
    }
    fl_assemble = next(node for node in fl2va["nodes"]
                       if node.get("type") == "MiniMaxH3ChainAssemble"
                       and node.get("mode", 0) == 0)
    assert "%date:yyyy-MM-dd%" in fl_assemble["widgets_values"][1]
    print("workflow v2: scheduler-free core FL2VA editing/review graph passes")

    i2va_path = (ROOT / "example_workflows" / "Archive" /
                  "Looping Single Image I2VA 20s V2 - MiniMax H3.json")
    i2va = json.loads(i2va_path.read_text(encoding="utf-8"))
    assert_workflow_links(i2va)
    i2va_types = {node.get("type") for node in i2va["nodes"]}
    assert {
        "MiniMaxH3ImageToVideo",
        "MiniMaxH3ChainFirstSceneImage",
        "MiniMaxH3ChainScenePromptEditor",
        "MiniMaxH3ChainReview",
    } <= i2va_types
    assert "LoadAudio" not in i2va_types
    i2va_plan_node = next(node for node in i2va["nodes"]
                           if node.get("type") == "MiniMaxH3ChainPlan")
    i2va_plan = package.NODE_CLASS_MAPPINGS["MiniMaxH3ChainPlan"]().build(
        *i2va_plan_node["widgets_values"])[0]
    assert [shot["raw_frames"] for shot in i2va_plan["shots"]] == [243, 243]
    assert [shot["delivered_frames"] for shot in i2va_plan["shots"]] == [243, 238]
    assert i2va_plan["total_delivered_frames"] == 481
    i2va_conditioner = next(node for node in i2va["nodes"]
                            if node.get("type") == "MiniMaxH3ImageToVideo")
    i2va_inputs = {item["name"]: item.get("link")
                    for item in i2va_conditioner["inputs"]}
    assert i2va_inputs["first_frame"] is not None
    assert i2va_inputs["last_frame"] is None
    print("workflow v2: gated two-scene I2VA 20-second graph passes")

    scheduled_path = (ROOT / "example_workflows" / "Archive" /
                      "Looping Seamless Chain V2 - Scheduled Refs - MiniMax H3.json")
    scheduled = json.loads(scheduled_path.read_text(encoding="utf-8"))
    assert_workflow_links(scheduled)
    scheduled_types = {node.get("type") for node in scheduled["nodes"]}
    assert {
        "MiniMaxH3ScheduledPictureReference",
        "MiniMaxH3ScheduledVideoReference",
        "MiniMaxH3ScheduledAudioReference",
        "MiniMaxH3ScheduledReferenceToVideo",
        "MiniMaxH3ReferenceVideoPrepare",
        "MiniMaxH3ChainScenePromptEditor",
        "MiniMaxH3ChainReview",
    } <= scheduled_types
    assert "MiniMaxH3ReferenceToVideo" not in scheduled_types
    scheduled_plan_node = next(
        node for node in scheduled["nodes"]
        if node.get("type") == "MiniMaxH3ChainPlan")
    scheduled_plan = json.loads(scheduled_plan_node["widgets_values"][0])
    assert len(scheduled_plan["shots"]) == 14
    for index, shot in enumerate(scheduled_plan["shots"], start=1):
        text = "\n".join(shot["prompt"])
        assert "@hero_look" in text and "@song" in text
        assert any(line.startswith("@song is ") for line in shot["prompt"])
        assert ("@hero_face" in text) == (index <= 7)
        assert ("@performance" in text) == (4 <= index <= 6)
        assert (any(line.startswith("@performance provides ")
                    for line in shot["prompt"]) == (4 <= index <= 6))
        assert "<Picture 1>" not in text and "<Picture 2>" not in text
        assert "<Audio 1>" not in text
    picture_nodes = {
        node["widgets_values"][0]: node for node in scheduled["nodes"]
        if node.get("type") == "MiniMaxH3ScheduledPictureReference"
    }
    assert picture_nodes["hero_face"]["widgets_values"][1] == "1:7"
    assert picture_nodes["hero_look"]["widgets_values"][1] == "all"
    assert all(len(node["widgets_values"]) == 2
               for node in picture_nodes.values())
    video_schedule = next(
        node for node in scheduled["nodes"]
        if node.get("type") == "MiniMaxH3ScheduledVideoReference")
    assert video_schedule["widgets_values"][:2] == ["performance", "4:6"]
    assert video_schedule["widgets_values"][2] == "performance_audio"
    assert len(video_schedule["widgets_values"]) == 3
    audio_schedule = next(
        node for node in scheduled["nodes"]
        if node.get("type") == "MiniMaxH3ScheduledAudioReference")
    assert audio_schedule["widgets_values"][:2] == ["song", "all"]
    assert len(audio_schedule["widgets_values"]) == 2
    demo_schedule = chain._make_reference_schedule([
        {
            "kind": "picture", "tag": "hero_face", "scenes": "1:7",
            "ranges": ((1, 7),), "value": object(), "content_hash": "face",
        },
        {
            "kind": "picture", "tag": "hero_look", "scenes": "all",
            "ranges": (), "value": object(), "content_hash": "look",
        },
        {
            "kind": "video", "tag": "performance", "scenes": "4:6",
            "ranges": ((4, 6),), "value": object(), "audio": object(),
            "audio_tag": "performance_audio", "content_hash": "video",
            "audio_hash": "paired-audio",
        },
        {
            "kind": "audio", "tag": "song", "scenes": "all",
            "ranges": (), "value": object(), "content_hash": "song",
        },
    ])
    for scene in (1, 4, 8):
        source = "\n".join(scheduled_plan["shots"][scene - 1]["prompt"])
        compiled_demo, _mapping, _bindings = (
            chain._compile_scheduled_reference_prompt(
                demo_schedule, scene, 14, source))
        assert "@hero" not in compiled_demo
        assert "@performance" not in compiled_demo
        assert "@song" not in compiled_demo
        assert "{ref}" not in compiled_demo
        assert "defines <Subject 1>" not in compiled_demo
        assert "for scenes 1-7" not in compiled_demo
        assert compiled_demo.startswith("subject_definitions:\n<Subject 1>")
        if scene == 1:
            assert "<Picture 1>" in compiled_demo
            assert "<Picture 2>" in compiled_demo
            assert "<Audio 1> is the current frame-exact" in compiled_demo
        elif scene == 4:
            assert "<Video 1> provides a weak reference" in compiled_demo
            assert "<Audio 1> is the synchronized soundtrack" in compiled_demo
            assert "<Audio 2> is the current frame-exact" in compiled_demo
        else:
            assert "defined by <Picture 1>" in compiled_demo
            assert "<Picture 2>" not in compiled_demo
            assert "<Audio 1> is the current frame-exact" in compiled_demo
    scheduled_links = {int(link[0]): link for link in scheduled["links"]}
    fingerprint_input = next(
        item for item in scheduled_plan_node["inputs"]
        if item["name"] == "generation_fingerprint")
    fingerprint_link = scheduled_links[int(fingerprint_input["link"])]
    assert fingerprint_link[1] == video_schedule["id"]
    current_node = next(node for node in scheduled["nodes"]
                        if node.get("type") == "MiniMaxH3ChainCurrent")
    current_audio_link = next(
        item for item in current_node["outputs"]
        if item["name"] == "source_audio_slice")
    assert len(current_audio_link["links"]) == 1
    assert scheduled_links[current_audio_link["links"][0]][3] == audio_schedule["id"]
    scheduled_assemble = next(
        node for node in scheduled["nodes"]
        if node.get("type") == "MiniMaxH3ChainAssemble"
        and node.get("mode", 0) == 0)
    assert "%date:yyyy-MM-dd%" in scheduled_assemble["widgets_values"][1]
    print("workflow v2: scheduled picture/video/audio aliases and review graph pass")

    angle_workflow_path = (ROOT / "example_workflows" / "Archive" /
                           "Three-Angle Guitar Ref2VA - EXPERIMENTAL - MiniMax H3.json")
    angle_workflow = json.loads(
        angle_workflow_path.read_text(encoding="utf-8"))
    angle_types = {node.get("type") for node in angle_workflow["nodes"]}
    assert "MiniMaxH3ReferenceVideoPrepare" in angle_types
    assert "MiniMaxH3ReferenceToVideo" in angle_types
    assert "MiniMaxH3LoopTrim" in angle_types
    assert not any(str(value).startswith("MiniMaxH3ChainLoop")
                   for value in angle_types)
    angle_loader = next(node for node in angle_workflow["nodes"]
                        if node.get("type") == "LoadVideo")
    angle_prep = next(node for node in angle_workflow["nodes"]
                      if node.get("type") == "MiniMaxH3ReferenceVideoPrepare")
    angle_ref = next(node for node in angle_workflow["nodes"]
                     if node.get("type") == "MiniMaxH3ReferenceToVideo")
    assert angle_loader["widgets_values"][0] == "3ClbaJYWVO4_000030.mp4"
    assert angle_prep["widgets_values"] == [209, 24.0]
    prompt = angle_ref["widgets_values"][0]
    sections = ["subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:",
                "non_diegetic_music:"]
    positions = [prompt.index(section) for section in sections]
    assert positions == sorted(positions)
    assert "exactly three shots" in prompt
    assert "Tera Echo product card" in prompt
    links = {int(link[0]): link for link in angle_workflow["links"]}
    prep_audio_links = next(
        output["links"] for output in angle_prep["outputs"]
        if output["name"] == "source_audio")
    assert len(prep_audio_links) == 2
    assert {links[link_id][3] for link_id in prep_audio_links} == {110, 132}
    print("workflow: one-pass three-angle Ref2VA copies source audio exactly")

    # Every public socket/widget should explain its role in the graph, and
    # every output should describe what it carries. This keeps newly added
    # controls from silently regressing to opaque ComfyUI labels.
    for node_name, node_class in package.NODE_CLASS_MAPPINGS.items():
        if str(getattr(node_class, "CATEGORY", "")).startswith("_internal/"):
            continue  # Recursion plumbing has no public socket/widget UI.
        schema = node_class.INPUT_TYPES()
        for section in ("required", "optional"):
            for input_name, input_spec in schema.get(section, {}).items():
                options = input_spec[1] if len(input_spec) > 1 else {}
                assert isinstance(options, dict) and str(
                    options.get("tooltip", "")).strip(), (
                        "%s.%s has no tooltip" % (node_name, input_name))
        output_tooltips = getattr(node_class, "OUTPUT_TOOLTIPS", ())
        assert len(output_tooltips) == len(node_class.RETURN_TYPES), (
            "%s output tooltip count is %d; expected %d" %
            (node_name, len(output_tooltips), len(node_class.RETURN_TYPES)))
        assert all(str(value).strip() for value in output_tooltips), (
            "%s has an empty output tooltip" % node_name)
    print("tooltips: every public input and output is documented")
    review_inputs = chain.MiniMaxH3ChainReview.INPUT_TYPES()
    partial_audio_tooltip = review_inputs["required"][
        "partial_audio_source"][1]["tooltip"]
    legacy_source_tooltip = review_inputs["optional"][
        "source_audio"][1]["tooltip"]
    assert "Source Timeline audio carried in state" in partial_audio_tooltip
    assert "none creates a silent partial" in partial_audio_tooltip
    assert "state predates recoverable source audio" in legacy_source_tooltip
    assert "legacy AUDIO connected at Loop Start" in legacy_source_tooltip
    assert "does not affect generation" in legacy_source_tooltip

    readable_prompts = chain._normalize_plan(
        json.dumps({
            "prompt_prefix": ["Shared identity.", "", "Shared wardrobe."],
            "shots": [{
                "id": "multiline",
                "prompt": [
                    "Use <Picture 1> for her facial identity.",
                    "Throughout every scene S1 wears the same dress.",
                    "<Subject 2> enters from camera right.",
                ],
                "length": 39,
            }],
        }),
        "readable", 32, 32, 22, "video", "head", "disabled",
        "source_track", 0, 15, 2, 1, 30,
    )
    assert readable_prompts["shots"][0]["prompt"] == (
        "Shared identity.\n\nShared wardrobe.\n\n"
        "Use <Picture 1> for her facial identity.\n"
        "Throughout every scene S1 wears the same dress.\n"
        "<Subject 2> enters from camera right."
    )
    prompt_editor = package.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ChainScenePromptEditor"]()
    assert prompt_editor.passthrough(readable_prompts)[0] is readable_prompts
    run_manager = package.NODE_CLASS_MAPPINGS["MiniMaxH3ChainRunManager"]()
    run_manager_result = run_manager.passthrough(
        readable_prompts, True, True, False, "[]")
    assert run_manager_result == (readable_prompts, None)
    opening_image = object()
    first_scene_gate = package.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ChainFirstSceneImage"]()
    assert first_scene_gate.select({"index": 1}, opening_image)[:2] == (
        opening_image, True)
    assert first_scene_gate.select({"index": 2}, opening_image)[:2] == (
        None, False)
    last_target = object()
    assert first_scene_gate.select(
        {"index": 1}, opening_image, last_target)[3] is last_target
    assert first_scene_gate.select(
        {"index": 2}, opening_image, last_target)[3] is last_target
    assert "last-frame target supplied" in first_scene_gate.select(
        {"index": 2}, opening_image, last_target)[2]
    shared_only = chain._normalize_plan(
        json.dumps({
            "prompt_prefix": ["Shared identity.", "", "Shared direction."],
            "shots": [{"id": "shared_only", "prompt": "", "length": 39}],
        }),
        "shared_only", 32, 32, 22, "video", "head", "disabled",
        "source_track", 0, 15, 2, 1, 30,
    )
    assert shared_only["shots"][0]["scene_prompt"] == ""
    assert shared_only["shots"][0]["prompt"] == (
        "Shared identity.\n\nShared direction.")
    shared_only_revision = chain._plan_with_review_revision(
        shared_only, 1, "", 123)
    assert shared_only_revision["shots"][0]["scene_prompt"] == ""
    assert shared_only_revision["shots"][0]["prompt"] == (
        "Shared identity.\n\nShared direction.")
    try:
        chain._normalize_plan(
            json.dumps({"shots": [{"id": "empty", "prompt": ""}]}),
            "empty", 32, 32, 22, "video", "head", "disabled",
            "source_track", 0, 15, 2, 1, 30,
        )
    except ValueError as exc:
        assert "scene prompt or shared prompt" in str(exc)
    else:
        raise AssertionError("plan accepted an empty scene and shared prompt")
    numeric_seed_plan = chain._normalize_plan(
        '{"shots":[{"prompt":"seed test","seed":18446744073709551615}]}',
        "numeric_seed", 32, 32, 22, "video", "head", "disabled",
        "source_track", 0, 15, 2, 1, 30,
    )
    string_seed_plan = chain._normalize_plan(
        '{"shots":[{"prompt":"seed test","seed":"18446744073709551615"}]}',
        "numeric_seed", 32, 32, 22, "video", "head", "disabled",
        "source_track", 0, 15, 2, 1, 30,
    )
    assert numeric_seed_plan["shots"][0]["seed"] == chain.MAX_SEED
    assert numeric_seed_plan["plan_hash"] == string_seed_plan["plan_hash"]
    shorthand_defaults = chain._normalize_plan(
        '{"duration_seconds":8,"steps":10,'
        '"shots":[{"prompt":"top-level defaults"}]}',
        "shorthand_defaults", 32, 32, 22, "video", "head", "disabled",
        "source_track", 0, 15, 20, 1, 30,
    )
    assert shorthand_defaults["shots"][0]["raw_frames"] == 192
    assert shorthand_defaults["shots"][0]["steps"] == 10
    try:
        chain._normalize_plan(
            json.dumps({"shots": [{"prompt": ["valid", 42]}]}),
            "bad_lines", 32, 32, 22, "video", "head", "disabled",
            "source_track", 0, 15, 2, 1, 30,
        )
    except ValueError as exc:
        assert "only strings" in str(exc)
    else:
        raise AssertionError("prompt line array accepted a non-string item")
    print("prompts: multiline and shared-only scenes pass; fully empty prompts fail")

    assert chain._parse_scene_range("", 8, 1) == (1, 8)
    assert chain._parse_scene_range("3", 8, 1) == (3, 3)
    assert chain._parse_scene_range(" 3 : 8 ", 8, 1) == (3, 8)
    assert chain._parse_scene_range("", 8, 4) == (4, 8)
    for invalid in ("1,3", "3:2", "0:2", "2:9", "abc"):
        try:
            chain._parse_scene_range(invalid, 8, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("scene_range accepted %r" % invalid)
    print("scene range: blank, single scene, and one inclusive range validated")

    assert chain._parse_reference_selector("") == ()
    assert chain._parse_reference_selector("all") == ()
    assert chain._parse_reference_selector(" 1, 3, 5:8 ") == (
        (1, 1), (3, 3), (5, 8))
    assert chain._parse_reference_selector("1,2:4,3,8") == (
        (1, 4), (8, 8))
    for invalid in ("0", "4:2", "1;3", "hello"):
        try:
            chain._parse_reference_selector(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "reference selector accepted %r" % invalid)

    picture = torch.zeros((2, 8, 8, 3), dtype=torch.float32)
    video = torch.zeros((22, 8, 8, 3), dtype=torch.float32)
    paired_audio = audio_for_frames(22)
    voice_audio = audio_for_frames(22)
    picture_node = chain.MiniMaxH3ScheduledPictureReference()
    video_node = chain.MiniMaxH3ScheduledVideoReference()
    audio_node = chain.MiniMaxH3ScheduledAudioReference()
    picture_schedule = picture_node.add(
        picture, "@hero", "1,3,5:8")[0]
    video_schedule = video_node.add(
        video, "performance", "2:4", "performance_sound",
        audio=paired_audio, previous=picture_schedule)[0]
    schedule, schedule_fingerprint, _status = audio_node.add(
        voice_audio, "voice", "3", previous=video_schedule)
    assert chain._generation_fingerprint_value(schedule_fingerprint)[0] == (
        schedule["fingerprint"])
    assert len(schedule["entries"]) == 3
    assert schedule["entries"][0]["value"].shape[0] == 1

    source_prompt = (
        "subject_definitions:\n"
        "<Subject 1> follows @hero and @performance.\n"
        "@performance_sound is synchronized with @performance.\n"
        "@voice provides voice timing.\n\n"
        "summary:\n"
        "Use @performance_sound and @voice in scene 3."
    )
    compiled, active_summary, bindings = (
        chain._compile_scheduled_reference_prompt(
            schedule, 3, 8, source_prompt))
    assert active_summary == (
        "scene 3/8: @hero -> <Picture 1>; "
        "@performance_sound -> <Audio 1>; "
        "@performance -> <Video 1>; @voice -> <Audio 2>")
    assert compiled.startswith(
        "subject_definitions:\n"
        "<Subject 1> follows <Picture 1> and <Video 1>.\n"
        "<Audio 1> is synchronized with <Video 1>.\n"
        "<Audio 2> provides voice timing.\n")
    assert "@hero" not in compiled
    assert "@performance" not in compiled
    assert "@voice" not in compiled
    assert bindings["aliases"] == {
        "hero": "<Picture 1>",
        "performance_sound": "<Audio 1>",
        "performance": "<Video 1>",
        "voice": "<Audio 2>",
    }

    expanded = chain.MiniMaxH3ScheduledReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", schedule, 3, 8,
        source_prompt, 960, 544, 124, "match")
    graph_node = next(iter(expanded["expand"].values()))
    assert graph_node["class_type"] == "MiniMaxH3ReferenceToVideo"
    graph_inputs = graph_node["inputs"]
    assert graph_inputs["prompt"] == compiled
    assert graph_inputs["ref_images.ref_image_0"] is schedule[
        "entries"][0]["value"]
    assert graph_inputs["ref_videos.ref_video_0"] is video
    assert graph_inputs[
        "ref_video_audios.ref_video_audio_0"] is paired_audio
    assert graph_inputs["ref_audios.ref_audio_0"] is voice_audio
    assert expanded["result"][2:] == (
        compiled, active_summary, schedule_fingerprint)

    sequential_video = torch.arange(
        500, dtype=torch.float32).reshape(500, 1, 1, 1).expand(-1, 8, 8, 3)
    sequential_audio = {
        "waveform": torch.arange(
            5000, dtype=torch.float32).reshape(1, 1, 5000),
        "sample_rate": 240,
    }
    sequential_schedule = video_node.add(
        sequential_video, "motion", "", "motion_audio", "sequential",
        audio=sequential_audio)[0]
    sequential_state = {
        "index": 2,
        "plan": {"shots": [
            {"raw_frames": 243, "generation_start_frame": 0},
            {"raw_frames": 243, "generation_start_frame": 221},
        ]},
    }
    sequential_expanded = chain.MiniMaxH3ScheduledReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", sequential_schedule, 2, 2,
        "Use @motion and @motion_audio.", 960, 544, 243, "match",
        state=sequential_state)
    sequential_inputs = next(iter(
        sequential_expanded["expand"].values()))["inputs"]
    sequential_video_slice = sequential_inputs["ref_videos.ref_video_0"]
    sequential_audio_slice = sequential_inputs[
        "ref_video_audios.ref_video_audio_0"]
    assert float(sequential_video_slice[0, 0, 0, 0]) == 221
    assert float(sequential_video_slice[-1, 0, 0, 0]) == 463
    assert float(sequential_audio_slice["waveform"][0, 0, 0]) == 2210
    assert "@motion sequential frames 221:464" in (
        sequential_expanded["result"][3])

    first_picture_schedule = picture_node.add(
        picture, "picture_1", "1")[0]
    renumbering_schedule = picture_node.add(
        picture, "picture_2", "",
        previous=first_picture_schedule)[0]
    scene_one_compiled, scene_one_summary, _ = (
        chain._compile_scheduled_reference_prompt(
            renumbering_schedule, 1, 2,
            "Use @picture_2 as the second identity reference.\n"
            "Follow @picture_2."))
    assert scene_one_summary == (
        "scene 1/2: @picture_1 -> <Picture 1>; "
        "@picture_2 -> <Picture 2>")
    assert "Use <Picture 2> as the second identity reference." in (
        scene_one_compiled)
    assert "Follow <Picture 2>." in scene_one_compiled
    scene_two_compiled, scene_two_summary, _ = (
        chain._compile_scheduled_reference_prompt(
            renumbering_schedule, 2, 2,
            "Use @picture_2 as the second identity reference.\n"
            "Follow @picture_2."))
    assert scene_two_summary == "scene 2/2: @picture_2 -> <Picture 1>"
    assert "Use <Picture 1> as the second identity reference." in (
        scene_two_compiled)
    assert "Follow <Picture 1>." in scene_two_compiled

    picture_only = picture_node.add(
        picture, "single", "1")[0]
    unreferenced = chain.MiniMaxH3ScheduledReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", picture_only, 2, 2,
        "A text-only second scene.", 960, 544, 124, "match")
    unreferenced_inputs = next(iter(unreferenced["expand"].values()))[
        "inputs"]
    assert not any(
        key.startswith(("ref_images.", "ref_videos.",
                        "ref_video_audios.", "ref_audios."))
        for key in unreferenced_inputs)
    assert unreferenced["result"][3] == (
        "scene 2/2: no scheduled references")
    try:
        chain._compile_scheduled_reference_prompt(
            picture_only, 2, 2, "Use @single here.")
    except ValueError as exc:
        assert "not active in scene 2" in str(exc)
    else:
        raise AssertionError("compiler accepted an inactive reference tag")
    try:
        chain._compile_scheduled_reference_prompt(
            picture_only, 1, 2, "Use @unknown here.")
    except ValueError as exc:
        assert "unknown scheduled reference tag" in str(exc)
    else:
        raise AssertionError("compiler accepted an unknown reference tag")
    warning_prompt, warning_summary, warning_bindings = (
        chain._compile_scheduled_reference_prompt(
            picture_only, 1, 2,
            "Keep @unknown and @unknown; resolve @single.",
            compliance_mode="soft"))
    assert warning_prompt == (
        "Keep @unknown and @unknown; resolve <Picture 1>.")
    assert len(warning_bindings["compliance_warnings"]) == 1
    assert "unknown scheduled reference tag @unknown" in warning_summary
    inactive_prompt, _, inactive_bindings = (
        chain._compile_scheduled_reference_prompt(
            picture_only, 2, 2, "Keep @single here.",
            compliance_mode="soft"))
    assert inactive_prompt == "Keep @single here."
    assert "not active in scene 2" in (
        inactive_bindings["compliance_warnings"][0])
    disabled_prompt, disabled_summary, disabled_bindings = (
        chain._compile_scheduled_reference_prompt(
            picture_only, 1, 2, "Leave @single and @unknown unchanged.",
            compliance_mode="disabled"))
    assert disabled_prompt == "Leave @single and @unknown unchanged."
    assert disabled_bindings["compliance_warnings"] == []
    assert "prompt compliance disabled" in disabled_summary
    try:
        audio_node.add(
            voice_audio, "hero", "", previous=picture_schedule)
    except ValueError as exc:
        assert "already in this chain" in str(exc)
    else:
        raise AssertionError("scheduler accepted a duplicate tag")
    try:
        video_node.add(
            video, "same", "1", "same", audio=paired_audio)
    except ValueError as exc:
        assert "must be different" in str(exc)
    else:
        raise AssertionError(
            "video scheduler accepted the same video and audio tag")
    try:
        chain._active_reference_bindings(picture_schedule, 1, 4)
    except ValueError as exc:
        assert "exceeds this plan's 4 scenes" in str(exc)
    else:
        raise AssertionError("scheduler accepted an out-of-plan selector")

    tagged_picture_node = chain.MiniMaxH3TaggedPictureReference()
    tagged_video_node = chain.MiniMaxH3TaggedVideoReference()
    tagged_audio_node = chain.MiniMaxH3TaggedAudioReference()
    assert "scenes" not in tagged_picture_node.INPUT_TYPES()["required"]
    assert "scenes" not in tagged_video_node.INPUT_TYPES()["required"]
    assert "scenes" not in tagged_audio_node.INPUT_TYPES()["required"]
    tagged = tagged_picture_node.add(picture, "hero_face")[0]
    tagged = tagged_picture_node.add(
        picture, "hero_look", previous=tagged)[0]
    tagged = tagged_audio_node.add(
        voice_audio, "voice", previous=tagged)[0]
    assert tagged["activation"] == "prompt"
    assert all(entry["activation"] == "prompt"
               for entry in tagged["entries"])
    tagged_prompt = (
        "<Subject 1> @S1 follows @hero_look. "
        "@voice defines vocal identity. @unregistered stays user-managed.")
    tagged_compiled, tagged_summary, tagged_bindings = (
        chain._compile_tagged_reference_prompt(
            tagged, 2, 4, tagged_prompt))
    assert tagged_compiled == (
        "<Subject 1> @S1 follows <Picture 1>. "
        "<Audio 1> defines vocal identity. @unregistered stays user-managed.")
    assert tagged_summary == (
        "scene 2/4: @hero_look -> <Picture 1>; @voice -> <Audio 1>")
    assert [entry["tag"] for entry in tagged_bindings["pictures"]] == [
        "hero_look"]
    assert [entry["tag"] for entry in tagged_bindings["audios"]] == [
        "voice"]
    assert "hero_face" not in tagged_bindings["aliases"]

    tagged_expanded = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 2, 4,
        tagged_prompt, 960, 544, 124, "match")
    tagged_inputs = next(iter(tagged_expanded["expand"].values()))["inputs"]
    assert tagged_inputs["prompt"] == tagged_compiled
    assert tagged_inputs["ref_images.ref_image_0"] is tagged[
        "entries"][1]["value"]
    assert tagged_inputs["ref_audios.ref_audio_0"] is voice_audio
    assert "ref_images.ref_image_1" not in tagged_inputs
    assert chain._generation_fingerprint_value(
        tagged_expanded["result"][4])[0] == tagged["fingerprint"]

    tagged_motion = tagged_video_node.add(
        sequential_video, "motion", "motion_audio", "sequential",
        audio=sequential_audio)[0]
    prompt_driven_state = {
        "index": 3,
        "plan": {"shots": [
            {"raw_frames": 243, "generation_start_frame": 0,
             "prompt": "No motion reference in this opening."},
            {"raw_frames": 243, "generation_start_frame": 221,
             "prompt": "Begin @motion."},
            {"raw_frames": 243, "generation_start_frame": 442,
             "prompt": "Continue using @motion_audio."},
        ]},
    }
    tagged_motion_expanded = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged_motion, 3, 3,
        "Continue using @motion_audio.", 960, 544, 243, "match",
        state=prompt_driven_state)
    tagged_motion_inputs = next(iter(
        tagged_motion_expanded["expand"].values()))["inputs"]
    assert float(tagged_motion_inputs[
        "ref_videos.ref_video_0"][0, 0, 0, 0]) == 221
    assert float(tagged_motion_inputs[
        "ref_video_audios.ref_video_audio_0"]["waveform"][0, 0, 0]) == 2210
    assert "origin scene 2" in tagged_motion_expanded["result"][3]

    tagged_motion_role = chain.MiniMaxH3TaggedMotionReference().add(
        sequential_video, "performance", "<Subject 1> and <Subject 2>",
        "the source performer's pose sequence and action timing", "384", "",
        "restart_each_scene")[0]
    motion_role_prompt = (
        "subject_definitions:\n"
        "<Subject 1> is the target character.\n"
        "<Subject 2> is the target partner.\n\n"
        "detailed_description:\n"
        "[Shot 1] <Subject 1> performs @performance.")
    motion_role_compiled, motion_role_summary, motion_role_bindings = (
        chain._compile_tagged_reference_prompt(
            tagged_motion_role, 1, 1, motion_role_prompt))
    assert "<Subject 3> is the reusable pose, action, and motion from " \
           "<Video 1>" in motion_role_compiled
    assert "<Subject 1> performs <Subject 3>." in motion_role_compiled
    assert "without importing the source identity, wardrobe, setting, " \
           "lighting, or composition" in motion_role_compiled
    assert motion_role_bindings["aliases"]["performance"] == "<Subject 3>"
    assert "@performance -> <Subject 3> motion from <Video 1>" in \
           motion_role_summary
    motion_role_expanded = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged_motion_role, 1, 1,
        motion_role_prompt, 960, 544, 243, "match")
    motion_role_inputs = next(iter(
        motion_role_expanded["expand"].values()))["inputs"]
    assert motion_role_inputs["ref_videos.ref_video_0"] is sequential_video
    assert motion_role_inputs["prompt"] == motion_role_compiled

    no_tag_compiled, no_tag_summary, no_tag_bindings = (
        chain._compile_tagged_reference_prompt(
            tagged, 1, 4, "A scene without registered aliases; keep @S1."))
    assert no_tag_compiled.endswith("keep @S1.")
    assert no_tag_bindings["pictures"] == []
    assert no_tag_bindings["audios"] == []
    assert no_tag_summary.endswith("no tagged references used by prompt")
    print("reference schedule: disjoint selectors, stable tags, native label "
          "compilation, strict/warning-only compliance, dynamic Ref2VA "
          "sockets, prompt-driven tagged references, and validation pass")

    # ComfyUI rounds H3's 40 Hz audio grid to the nearest step. Depending on
    # frame length, the decoded stream can land 1/3 step above or below the
    # exact 24 fps picture duration. Match Tail must frame-lock both cases by
    # tiny time-conformance rather than inserting a silence tail.
    trim_node = package.NODE_CLASS_MAPPINGS["MiniMaxH3LoopTrim"]()
    short_images = torch.zeros((260, 1, 1, 3), dtype=torch.float32)
    short_samples = 346400  # 433 audio steps; exact 260f target is 346667
    short_audio = {
        "waveform": torch.ones((1, 2, short_samples), dtype=torch.float32),
        "sample_rate": 32000,
    }
    _, padded, _, retained = trim_node.trim(
        short_images, 0, short_audio, 24.0, True)
    assert retained == 0
    assert int(padded["waveform"].shape[-1]) == 346667
    assert torch.all(padded["waveform"] > 0.99)
    chain._validate_audio(padded, "260-frame regression", expected_frames=260)

    long_images = torch.zeros((124, 1, 1, 3), dtype=torch.float32)
    long_samples = 165600  # 207 audio steps; exact 124f target is 165333
    long_audio = {
        "waveform": torch.ones((1, 2, long_samples), dtype=torch.float32),
        "sample_rate": 32000,
    }
    _, truncated, _, retained = trim_node.trim(
        long_images, 0, long_audio, 24.0, True)
    assert retained == 0
    assert int(truncated["waveform"].shape[-1]) == 165333
    assert torch.all(truncated["waveform"] > 0.99)
    numbered = torch.arange(10, dtype=torch.float32).reshape(10, 1, 1, 1)
    delivered, _, with_overlap, retained = trim_node.trim(
        numbered, 4, retain_overlap_frames=2)
    assert delivered[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap[:, 0, 0, 0].tolist() == list(range(2, 10))
    assert retained == 2
    delivered, _, with_overlap, retained = trim_node.trim(
        numbered, 4, retain_overlap_frames=99)
    assert delivered[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap[:, 0, 0, 0].tolist() == list(range(10))
    assert retained == 4
    scene_state = {
        "index": 2,
        "plan": {
            "shots": [{}, {"video_blend_frames": 3}],
            "compatibility": {"video_blend_frames": 0},
        },
    }
    delivered, _, with_overlap, retained = trim_node.trim(
        numbered, 4, retain_overlap_frames=0, state=scene_state)
    assert delivered[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap[:, 0, 0, 0].tolist() == list(range(1, 10))
    assert retained == 3
    first_scene_state = {
        "index": 1,
        "plan": {
            "shots": [{
                "raw_frames": 6,
                "delivered_frames": 6,
                "video_blend_frames": 39,
            }],
            "compatibility": {"video_blend_frames": 39},
        },
    }
    delivered, _, with_overlap, retained = trim_node.trim(
        numbered[4:], 0, retain_overlap_frames=39, state=first_scene_state)
    assert delivered[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert with_overlap[:, 0, 0, 0].tolist() == list(range(4, 10))
    assert retained == 0
    print("trim: AV tails frame-locked; optional visual overlap is clamped and "
          "scene state overrides the legacy Plan-default wire")

    real_st_load = chain._st_load
    try:
        loads = iter([
            {"delivered_audio": torch.ones((1, 2, 1667))},
            {"delivered_audio": torch.ones((1, 2, 1667))},
        ])
        chain._st_load = lambda _path: next(loads)
        cumulative_trimmed = chain._generated_audio({"segments": [
            {"index": 1, "checkpoint": "one", "sample_rate": 8000,
             "delivered_frames": 5},
            {"index": 2, "checkpoint": "two", "sample_rate": 8000,
             "delivered_frames": 5},
        ]})
        assert cumulative_trimmed["waveform"].shape[-1] == round(10 / 24 * 8000)

        loads = iter([
            {"delivered_audio": torch.ones((1, 2, 1333))},
            {"delivered_audio": torch.ones((1, 2, 1333))},
        ])
        cumulative_padded = chain._generated_audio({"segments": [
            {"index": 1, "checkpoint": "one", "sample_rate": 8000,
             "delivered_frames": 4},
            {"index": 2, "checkpoint": "two", "sample_rate": 8000,
             "delivered_frames": 4},
        ]})
        assert cumulative_padded["waveform"].shape[-1] == round(8 / 24 * 8000)
        assert torch.count_nonzero(cumulative_padded["waveform"][..., -1:]) == 0

        real_prelude_audio = chain._prelude_audio
        chain._prelude_audio = lambda _record: {
            "waveform": torch.ones((1, 2, 1667)), "sample_rate": 8000}
        try:
            joined = chain._audio_with_prelude(
                {"waveform": torch.ones((1, 2, 1667)),
                 "sample_rate": 8000},
                5, {"frame_count": 5})
        finally:
            chain._prelude_audio = real_prelude_audio
        assert joined["waveform"].shape[-1] == round(10 / 24 * 8000)
    finally:
        chain._st_load = real_st_load
    print("generated audio: per-scene rounding reconciled at cumulative frame "
          "boundaries for both trim and pad cases")

    giant_plan = chain._normalize_plan(
        json.dumps({
            "shots": [
                {"id": str(index), "prompt": "shot %d" % index}
                for index in range(1, 14)
            ] + [{"id": "14", "prompt": "outro", "duration_seconds": 5}]
        }),
        "timing", 960, 544, 22, "video", "head", "disabled",
        "source_track", 22, 15, 20, 123, 18,
    )
    assert giant_plan["shots"][0]["raw_frames"] == 362
    assert giant_plan["shots"][1]["generation_start_frame"] == 340
    assert giant_plan["shots"][-1]["raw_frames"] == 124
    assert giant_plan["shots"][-1]["generation_start_frame"] == 4420
    assert giant_plan["total_delivered_frames"] == 4544
    print("timing: 14 clips -> 4544 frames / 189.333s; frame-exact starts pass")

    assert chain._h3_frame_length(5 / 24 + 0.001) == 22
    assert chain._h3_frame_length(22 / 24 + 0.001) == 39
    try:
        chain._h3_frame_length(150.0)
    except ValueError as exc:
        assert "largest valid" in str(exc)
    else:
        raise AssertionError("duration-derived length exceeded H3's maximum")
    print("duration grid: always rounds up and rejects over-limit lengths")

    before_plan = chain._normalize_plan(
        json.dumps({"shots": ["first", "second", "third", "fourth"]}),
        "before", 32, 32, 1, "video", "before", "disabled",
        "generated_audio", 1, 0.1, 2, 1, 30,
    )
    assert [shot["delivered_frames"] for shot in before_plan["shots"]] == [5] * 4
    assert before_plan["shots"][1]["generation_start_frame"] == 5

    try:
        chain._normalize_plan(
            json.dumps({"shots": [
                {"prompt": "too short", "length": 5},
                {"prompt": "next", "length": 39},
            ]}),
            "short", 32, 32, 22, "video", "head", "disabled",
            "generated_audio", 22, 1, 2, 1, 30,
        )
    except ValueError as exc:
        assert "next clip requires 22 second-block context frames" in str(exc)
    else:
        raise AssertionError("plan accepted an undersized predecessor context")

    plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "first", "length": 5, "seed": 1},
            {"id": "two", "prompt": "second", "length": 5, "seed": 2},
        ]}),
        "smoke", 32, 32, 1, "video", "head", "disabled",
        "source_track", 1, 1, 2, 1, 30,
    )
    assert [shot["delivered_frames"] for shot in plan["shots"]] == [5, 4]

    observed = {}

    class SmokePlan:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {}}

        RETURN_TYPES = (chain.PLAN_TYPE,)
        FUNCTION = "make"

        def make(self):
            return (before_plan,)

    class SmokeBody:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {"state": (chain.STATE_TYPE,)}}

        RETURN_TYPES = ("IMAGE", "LATENT", chain.SEGMENT_TYPE)
        FUNCTION = "render"

        def render(self, state):
            shot = state["plan"]["shots"][state["index"] - 1]
            images = torch.zeros(
                (shot["delivered_frames"], 32, 32, 3), dtype=torch.float32)
            segment = {"index": state["index"], "id": shot["id"]}
            return (images, av_latent(), segment)

    class SmokeSink:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {"manifest": (chain.MANIFEST_TYPE,)}}

        RETURN_TYPES = ("STRING",)
        FUNCTION = "take"
        OUTPUT_NODE = True

        def take(self, manifest):
            observed["manifest"] = manifest
            return ("ok",)

    class SmokeServer:
        client_id = None
        last_node_id = None

        def send_sync(self, *args, **kwargs):
            pass

    runtime_nodes = dict(package.NODE_CLASS_MAPPINGS)
    runtime_nodes.update({
        "H3ChainSmokePlan": SmokePlan,
        "H3ChainSmokeBody": SmokeBody,
        "H3ChainSmokeSink": SmokeSink,
    })
    previous_nodes = {name: comfy_nodes.NODE_CLASS_MAPPINGS.get(name)
                      for name in runtime_nodes}
    comfy_nodes.NODE_CLASS_MAPPINGS.update(runtime_nodes)
    try:
        prompt = {
            "1": {"class_type": "H3ChainSmokePlan", "inputs": {}},
            "2": {"class_type": "MiniMaxH3ChainLoopStart", "inputs": {
                "plan": ["1", 0], "start_clip": 1,
                "scene_range": "1:2",
            }},
            "3": {"class_type": "H3ChainSmokeBody", "inputs": {
                "state": ["2", 1],
            }},
            "4": {"class_type": "MiniMaxH3ChainLoopEnd", "inputs": {
                "flow": ["2", 0], "state": ["2", 1],
                "images": ["3", 0], "sampled_latent": ["3", 1],
                "segment": ["3", 2],
            }},
            "5": {"class_type": "H3ChainSmokeSink", "inputs": {
                "manifest": ["4", 0],
            }},
        }
        executor = execution.PromptExecutor(
            SmokeServer(),
            cache_type=execution.CacheType.CLASSIC,
            cache_args={"ram": 0, "ram_inactive": 0},
        )
        executor.execute(prompt, "h3-chain-recursion-smoke", execute_outputs=["5"])
        assert executor.success
        assert observed["manifest"]["clip_count"] == 2
        assert len(observed["manifest"]["segments"]) == 2
        assert observed["manifest"]["format"] == "h3_chain_partial_manifest_v3"
        assert observed["manifest"]["planned_clip_count"] == 4
        print("runtime recursion: scene_range 1:2 stopped a four-clip plan at 2")
    finally:
        for name, previous in previous_nodes.items():
            if previous is None:
                comfy_nodes.NODE_CLASS_MAPPINGS.pop(name, None)
            else:
                comfy_nodes.NODE_CLASS_MAPPINGS[name] = previous

    previous_output = folder_paths.get_output_directory()
    with tempfile.TemporaryDirectory() as tempdir:
        folder_paths.set_output_directory(tempdir)
        try:
            source = audio_for_frames(9)
            changed_source = audio_for_frames(9)
            changed_source["waveform"][..., 0] = 1.0
            prepared_plan = chain._plan_with_source_audio(plan, source)
            assert prepared_plan["plan_hash"] == chain._fingerprint({
                "base_plan_hash": plan["plan_hash"],
                "source_audio_hash": chain._audio_fingerprint(source),
            })
            started = chain.MiniMaxH3ChainLoopStart().start(plan, 1, source)
            assert started[1]["plan"]["compatibility"]["source_audio_hash"]
            current_payload = chain.MiniMaxH3ChainCurrent().current(
                started[1], source)
            assert current_payload["ui"]["h3_chain_active_scene"] == [{
                "run_name": prepared_plan["run_name"],
                "clip_index": 1,
                "clip_count": 2,
                "end_clip": 2,
                "shot_id": prepared_plan["shots"][0]["id"],
                "seed": str(prepared_plan["shots"][0]["seed"]),
                "workflow_fingerprint": str(prepared_plan["plan_hash"]),
            }]
            current = current_payload["result"]
            assert current[1:3] == (1, 2)
            assert current[6:10] == (5, 2, 32, 32)
            assert int(current[12]["waveform"].shape[-1]) == round(5 / 24 * 8000)

            aligned_plan = chain._normalize_plan(
                json.dumps({"shots": [
                    {"id": "aligned", "prompt": "test", "length": 362},
                ]}),
                "aligned", 32, 32, 1, "video", "head", "disabled",
                "source_track", 1, 15, 2, 1, 30,
            )
            aligned_source = audio_for_frames(362, 32000)
            aligned_state = chain.MiniMaxH3ChainLoopStart().start(
                aligned_plan, 1, aligned_source)[1]
            frame_exact = chain.MiniMaxH3ChainCurrent().current(
                aligned_state, aligned_source,
                align_audio_reference=False)["result"]
            assert int(frame_exact[12]["waveform"].shape[-1]) == 482667
            grid_aligned = chain.MiniMaxH3ChainCurrent().current(
                aligned_state, aligned_source,
                align_audio_reference=True)["result"]
            assert int(grid_aligned[12]["waveform"].shape[-1]) == 482240
            assert "target 603 steps, safe 15.070000s" in grid_aligned[13]

            aligned_44k_source = audio_for_frames(362, 44100)
            aligned_44k_state = chain.MiniMaxH3ChainLoopStart().start(
                aligned_plan, 1, aligned_44k_source)[1]
            grid_aligned_44k = chain.MiniMaxH3ChainCurrent().current(
                aligned_44k_state, aligned_44k_source,
                align_audio_reference=True)["result"][12]
            aligned_44k_samples = int(
                grid_aligned_44k["waveform"].shape[-1])
            assert aligned_44k_samples == 664587
            assert math.ceil(aligned_44k_samples * 32000 / 44100) == 482240
            try:
                chain.MiniMaxH3ChainCurrent().current(
                    started[1], changed_source)
            except ValueError as exc:
                assert "different source waveform" in str(exc)
            else:
                raise AssertionError("Current Shot accepted a different source song")
            short_source = audio_for_frames(4)
            short_started = chain.MiniMaxH3ChainLoopStart().start(
                plan, 1, short_source)
            assert short_started[1]["plan"]["compatibility"][
                "source_audio_silent_padding"]
            short_current = chain.MiniMaxH3ChainCurrent().current(
                short_started[1], short_source)["result"]
            assert int(short_current[12]["waveform"].shape[-1]) == round(
                5 / 24 * 8000)
            assert not torch.count_nonzero(short_current[12]["waveform"])
            short_non_silent = audio_for_frames(4)
            short_non_silent["waveform"][..., 0] = 0.25
            try:
                chain.MiniMaxH3ChainLoopStart().start(
                    plan, 1, short_non_silent)
            except ValueError as exc:
                assert "source_audio_too_short" in str(exc)
                assert "Provide a source track" in str(exc)
            else:
                raise AssertionError("Loop Start accepted a short non-silent song")
            conditioning = [["cond", {}]]
            bypass_latent = av_latent()
            bypass = chain.MiniMaxH3ChainContext().apply(
                started[1], conditioning, None, bypass_latent)
            assert bypass[:3] == (conditioning, 0, False)
            assert bypass[3] is bypass_latent
            print("current/context: source window exact or 40 Hz aligned; short "
                  "silence pads safely")

            external_plan = chain._normalize_plan(
                json.dumps({"shots": [
                    {"id": "extension_one", "prompt": "continue", "length": 5},
                    {"id": "extension_two", "prompt": "continue again", "length": 5},
                ]}),
                "external_smoke", 32, 32, 1, "video", "head", "disabled",
                "source_plus_timeline", 5, 1, 2, 11, 30,
            )
            source_frames = torch.zeros((8, 32, 32, 3), dtype=torch.float32)
            for frame_index in range(8):
                source_frames[frame_index, ..., 0] = frame_index / 10.0
            source_video_audio = audio_for_frames(8)
            source_video_audio["waveform"].fill_(0.75)
            adapter = chain.MiniMaxH3ChainExternalVideo()
            external_context, external_status = adapter.prepare(
                external_plan, source_frames, 30.0, True, source_video_audio)
            assert "decoded IMAGE/AUDIO" in external_status
            assert "will be prepended" in external_status
            assert tuple(external_context["context_frames"].shape) == (
                1, 32, 32, 3)
            assert int(external_context["context_audio"][
                "waveform"].shape[-1]) == round(5 / 24 * 8000)
            assert abs(float(external_context["context_frames"][0, 0, 0, 0])
                       - 0.6) < 1e-6
            prelude = external_context["prelude"]
            assert prelude["frame_count"] == 6
            assert pathlib.Path(
                tempdir, prelude["video"]).is_file()
            assert pathlib.Path(
                tempdir, prelude["audio"]).is_file()

            class FakeVideoComponents:
                images = source_frames
                audio = source_video_audio
                frame_rate = 30

            class FakeNativeVideo:
                def get_components(self):
                    return FakeVideoComponents()

            ref_prep = chain.MiniMaxH3ReferenceVideoPrepare()
            ref_frames, ref_audio, ref_length, ref_status = ref_prep.prepare(
                5, 1.0, source_video=FakeNativeVideo())
            assert ref_length == 5
            assert tuple(ref_frames.shape) == (5, 32, 32, 3)
            assert abs(float(ref_frames[-1, 0, 0, 0]) - 0.5) < 1e-6
            assert int(ref_audio["waveform"].shape[-1]) == round(
                5 / 24 * 8000)
            assert torch.all(ref_audio["waveform"] == 0.75)
            assert "native VIDEO" in ref_status
            assert "5 frames at 24 fps" in ref_status
            ref_override_audio = audio_for_frames(8)
            ref_override_audio["waveform"].fill_(0.5)
            decoded_ref = ref_prep.prepare(
                5, 30.0, source_frames=source_frames,
                source_audio=ref_override_audio)
            assert "decoded IMAGE/AUDIO" in decoded_ref[3]
            assert torch.all(decoded_ref[1]["waveform"] == 0.5)
            try:
                ref_prep.prepare(
                    22, 30.0, source_frames=source_frames,
                    source_audio=source_video_audio)
            except ValueError as exc:
                assert "Choose a shorter H3-valid length" in str(exc)
            else:
                raise AssertionError(
                    "reference-video prep accepted an overlong source")
            print("reference prep: native/decoded video and exact audio copy pass")

            native_context, native_status = adapter.prepare(
                external_plan, source_fps=1.0, prepend_original=False,
                source_video=FakeNativeVideo())
            assert "native VIDEO" in native_status
            assert "30.000 fps" in native_status
            assert native_context["prelude"] is None
            assert abs(float(native_context[
                "context_frames"][0, 0, 0, 0]) - 0.6) < 1e-6
            assert int(native_context["context_audio"][
                "waveform"].shape[-1]) == round(5 / 24 * 8000)
            override_audio = audio_for_frames(8)
            override_audio["waveform"].fill_(0.5)
            overridden_context = adapter.prepare(
                external_plan, source_fps=24.0, prepend_original=False,
                source_audio=override_audio,
                source_video=FakeNativeVideo())[0]
            assert torch.allclose(
                overridden_context["context_audio"]["waveform"],
                torch.full_like(
                    overridden_context["context_audio"]["waveform"], 0.5))
            try:
                adapter.prepare(
                    external_plan, source_frames, 30.0, False,
                    source_video=FakeNativeVideo())
            except ValueError as exc:
                assert "both source_video and source_frames" in str(exc)
            else:
                raise AssertionError(
                    "existing-video adapter accepted both video input routes")
            try:
                adapter.prepare(
                    external_plan, source_fps=30.0, prepend_original=False)
            except ValueError as exc:
                assert "requires source_video or source_frames" in str(exc)
            else:
                raise AssertionError(
                    "existing-video adapter accepted no video input")

            extension_audio = audio_for_frames(8)
            extension_audio["waveform"].fill_(0.25)
            external_started = chain.MiniMaxH3ChainLoopStart().start(
                external_plan, 1, extension_audio,
                external_context=external_context)
            external_state1 = external_started[1]
            effective_external_plan = external_state1["plan"]
            assert [shot["delivered_frames"] for shot in
                    effective_external_plan["shots"]] == [4, 4]
            assert effective_external_plan["total_delivered_frames"] == 8
            assert external_state1["external_context"]
            assert tuple(external_state1["previous_frames"].shape) == (
                1, 32, 32, 3)
            first_current = chain.MiniMaxH3ChainCurrent().current(
                external_state1, extension_audio)["result"]
            external_current_state1 = first_current[0]
            first_slice = first_current[12]["waveform"]
            first_lead_samples = round(1 / 24 * 8000)
            assert int(first_slice.shape[-1]) == round(5 / 24 * 8000)
            assert torch.allclose(
                first_slice[..., :first_lead_samples],
                torch.full_like(first_slice[..., :first_lead_samples], 0.75))
            assert torch.allclose(
                first_slice[..., first_lead_samples:],
                torch.full_like(first_slice[..., first_lead_samples:], 0.25))

            context_call = {}

            class FakeExternalMotionContext:
                def apply(self, **kwargs):
                    context_call.update(kwargs)
                    return ("continued", 1)

            real_motion_context = chain.MiniMaxH3MotionContext
            chain.MiniMaxH3MotionContext = FakeExternalMotionContext
            try:
                external_conditioning = chain.MiniMaxH3ChainContext().apply(
                    external_current_state1, conditioning, None, av_latent(),
                    audio_vae="audio-vae")
            finally:
                chain.MiniMaxH3MotionContext = real_motion_context
            assert external_conditioning[:3] == ("continued", 1, True)
            assert external_conditioning[3] is context_call["latent"]
            assert context_call["context_latent"] is None
            assert context_call["audio_vae"] == "audio-vae"
            assert context_call["context_audio"] is external_current_state1[
                "previous_audio"]

            external_saver = chain.MiniMaxH3ChainSegmentSave()
            external_saver.save(
                external_current_state1,
                torch.zeros((4, 32, 32, 3), dtype=torch.float32),
                av_latent())["result"][0]
            external_state2 = chain._initial_state(
                effective_external_plan, 2)
            external_current_state2 = chain.MiniMaxH3ChainCurrent().current(
                external_state2, extension_audio)["result"][0]
            external_segment2 = external_saver.save(
                external_current_state2,
                torch.zeros((4, 32, 32, 3), dtype=torch.float32),
                av_latent())["result"][0]
            external_complete = dict(external_current_state2)
            external_complete["segments"] = (
                external_current_state2["segments"] + [external_segment2])
            external_manifest = chain._manifest_from_state(external_complete)
            assert external_manifest["prelude"]["frame_count"] == 6
            loaded_external = chain.MiniMaxH3ChainManifestLoad().load(
                external_plan, extension_audio, external_context)[0]
            assert loaded_external["plan_hash"] == external_manifest["plan_hash"]

            joined_audio = chain._audio_with_prelude(
                extension_audio, 8, prelude)
            assert int(joined_audio["waveform"].shape[-1]) == round(
                14 / 24 * 8000)
            prelude_samples = round(6 / 24 * 8000)
            assert torch.allclose(
                joined_audio["waveform"][..., :prelude_samples],
                torch.full_like(
                    joined_audio["waveform"][..., :prelude_samples], 0.75))
            external_result = chain.MiniMaxH3ChainAssemble().assemble(
                external_manifest, "source", "extended_with_original", 96,
                extension_audio)
            external_path = pathlib.Path(external_result["result"][0])
            assert external_path.is_file() and external_path.stat().st_size > 0
            external_duration = float(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(external_path),
            ], text=True, encoding="utf-8", errors="replace").strip())
            assert abs(external_duration - 14 / 24) < 0.05
            assert "existing-video prelude" in external_result["ui"]["text"][0]
            external_original_which = chain.shutil.which
            chain.shutil.which = lambda executable: (
                None if executable == "ffmpeg"
                else external_original_which(executable))
            try:
                external_fallback = chain.MiniMaxH3ChainAssemble().assemble(
                    external_manifest, "source", "extended_pyav", 96,
                    extension_audio)
            finally:
                chain.shutil.which = external_original_which
            with chain.av.open(
                    external_fallback["result"][0], mode="r") as media:
                assert len(media.streams.video) == 1
                assert len(media.streams.audio) == 1
                assert sum(1 for _frame in media.decode(video=0)) == 14
            print("existing video: native VIDEO and decoded IMAGE/AUDIO routes "
                  "normalized 30 fps input, scene 1 continued with AV context, "
                  "and original prelude assembled with both media backends")

            saver = chain.MiniMaxH3ChainSegmentSave()
            generated_state = chain._initial_state(
                chain._plan_with_source_audio(before_plan, None), 1)
            try:
                saver.save(
                    generated_state,
                    torch.zeros((5, 32, 32, 3), dtype=torch.float32),
                    av_latent())
            except ValueError as exc:
                assert "requires decoded audio" in str(exc)
            else:
                raise AssertionError("generated_audio saved without decoded audio")
            try:
                saver.save(
                    generated_state,
                    torch.zeros((5, 32, 32, 3), dtype=torch.float32),
                    av_latent(), audio_for_frames(4))
            except ValueError as exc:
                assert "expected exactly" in str(exc)
            else:
                raise AssertionError("Segment Save accepted mistimed audio")
            state1 = current[0]
            images1 = torch.zeros((5, 32, 32, 3), dtype=torch.float32)
            queued_prompt = {
                "1700": {
                    "class_type": "MiniMaxH3ChainPlan",
                    "inputs": {
                        "plan_json": '{"shots":["stale"]}',
                        "run_name": "smoke",
                    },
                },
            }
            queued_workflow = {
                "nodes": [{
                    "id": 1700,
                    "type": "MiniMaxH3ChainPlan",
                    "widgets_values": [
                        '{"shots":["stale"]}', "smoke", "fingerprint",
                    ],
                }],
            }
            result1 = saver.save(
                state1, images1, av_latent(), audio_for_frames(5),
                prompt=queued_prompt,
                extra_pnginfo={"workflow": queued_workflow})
            segment1 = result1["result"][0]
            assert pathlib.Path(chain._absolute_output_path(
                segment1["segment"])).is_file()
            segment1_audio_path = pathlib.Path(chain._absolute_output_path(
                segment1["generated_audio"]))
            assert segment1_audio_path.is_file()
            assert segment1["generated_audio_sha256"] == chain._file_sha256(
                str(segment1_audio_path))
            with wave.open(str(segment1_audio_path), "rb") as saved_audio:
                assert saved_audio.getframerate() == 8000
                assert saved_audio.getnchannels() == 2
                assert saved_audio.getnframes() == round(5 / 24 * 8000)

            assert segment1["prompt_prefix"] == ""
            assert segment1["scene_prompt"] == "first"
            assert segment1["prompt"] == "first"
            prompt_path = pathlib.Path(chain._absolute_output_path(
                segment1["prompt_file"]))
            assert prompt_path.read_text(encoding="utf-8") == "first"
            assert segment1["prompt_file_sha256"] == chain._file_sha256(
                str(prompt_path))
            segment_metadata = json.loads(pathlib.Path(
                chain._absolute_output_path(segment1["metadata"])
            ).read_text(encoding="utf-8"))
            revision_metadata_path = pathlib.Path(
                chain._absolute_output_path(segment1["revision_metadata"]))
            assert segment_metadata["format"] == "h3_chain_segment_v3"
            assert segment_metadata["segment"]["prompt"] == "first"
            assert segment_metadata["archives"] == segment1["archives"]
            assert revision_metadata_path.is_file()
            assert json.loads(revision_metadata_path.read_text(
                encoding="utf-8"))["segment"]["revision"] == segment1["revision"]

            run_dir = pathlib.Path(tempdir, "h3_chains", "smoke")
            exact_text_path = run_dir / "exact-lf.txt"
            chain._atomic_text(str(exact_text_path), "line one\nline two")
            assert exact_text_path.read_bytes() == b"line one\nline two"

            legacy_prompt_path = run_dir / "legacy-windows.prompt.txt"
            legacy_prompt_path.write_bytes(b"line one\r\nline two")
            legacy_segment = dict(segment1)
            legacy_segment["prompt_file"] = chain._relative_output_path(
                str(legacy_prompt_path))
            legacy_segment["prompt_hash"] = hashlib.sha256(
                b"line one\nline two").hexdigest()
            legacy_segment.pop("prompt_file_sha256", None)
            chain._verify_segment_artifacts(legacy_segment, 1)
            legacy_prompt_path.write_bytes(b"line one\r\nchanged")
            try:
                chain._verify_segment_artifacts(legacy_segment, 1)
            except ValueError as exc:
                assert "prompt sidecar" in str(exc)
            else:
                raise AssertionError("changed legacy prompt sidecar was accepted")

            archived_plan = json.loads(
                (run_dir / "plan.json").read_text(encoding="utf-8"))
            archived_api = json.loads(
                (run_dir / "api_prompt.json").read_text(encoding="utf-8"))
            archived_workflow = json.loads(
                (run_dir / "workflow.json").read_text(encoding="utf-8"))
            assert archived_plan["format"] == "h3_chain_plan_archive_v1"
            assert archived_plan["shots"][0]["prompt"] == "first"
            assert json.loads(
                archived_api["1700"]["inputs"]["plan_json"]
            )["shots"][0]["prompt"] == "first"
            assert json.loads(
                archived_workflow["nodes"][0]["widgets_values"][0]
            )["shots"][0]["prompt"] == "first"
            embedded_tags = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format_tags",
                "-of", "json",
                str(chain._absolute_output_path(segment1["segment"])),
            ], text=True, encoding="utf-8", errors="replace"))["format"]["tags"]
            assert embedded_tags["comment"] == "first"
            assert embedded_tags["h3_prompt"] == "first"
            assert json.loads(embedded_tags["workflow"])["nodes"][0][
                "type"] == "MiniMaxH3ChainPlan"
            assert json.loads(embedded_tags["prompt"])["1700"][
                "class_type"] == "MiniMaxH3ChainPlan"
            assert json.loads(embedded_tags["h3_plan"])["shots"][0][
                "prompt"] == "first"
            print("recovery metadata: MP4, prompt sidecar, plan, API prompt, "
                  "and workflow archive exact inputs")

            interrupted_manifest = chain.MiniMaxH3ChainManifestLoad().load(
                plan, source)
            assert interrupted_manifest[0]["format"] == (
                "h3_chain_partial_manifest_v3")
            assert interrupted_manifest[0]["clip_count"] == 1
            assert interrupted_manifest[0]["planned_clip_count"] == 2
            assert "partial manifest through clip 1/2" in (
                interrupted_manifest[2])
            assert pathlib.Path(
                tempdir, "h3_chains", "smoke", "partial",
                "through_clip_0001.manifest.json").is_file()
            print("manifest load: interrupted run restored through scene 1")

            segment1_path = pathlib.Path(
                chain._absolute_output_path(segment1["segment"]))
            checkpoint1_path = pathlib.Path(
                chain._absolute_output_path(segment1["checkpoint"]))
            with safe_open(checkpoint1_path, framework="pt", device="cpu") as saved:
                checkpoint_metadata = saved.metadata()
            assert checkpoint_metadata["format"] == "h3_chain_checkpoint_v3"
            assert checkpoint_metadata["prompt"] == "first"
            assert checkpoint_metadata["seed"] == "1"
            before_interruption = (
                segment1_path.read_bytes(), checkpoint1_path.read_bytes(),
                segment1_audio_path.read_bytes())
            real_st_save = chain._st_save

            def interrupted_save(*args, **kwargs):
                raise RuntimeError("simulated interrupted checkpoint write")

            chain._st_save = interrupted_save
            try:
                saver.save(
                    state1, torch.ones_like(images1), av_latent(),
                    audio_for_frames(5))
            except RuntimeError as exc:
                assert "simulated interrupted" in str(exc)
            else:
                raise AssertionError("simulated checkpoint interruption did not fire")
            finally:
                chain._st_save = real_st_save
            assert segment1_path.read_bytes() == before_interruption[0]
            assert checkpoint1_path.read_bytes() == before_interruption[1]
            assert segment1_audio_path.read_bytes() == before_interruption[2]
            assert chain._initial_state(prepared_plan, 2)["index"] == 2
            replacement = saver.save(
                state1, images1, av_latent(), audio_for_frames(5))["result"][0]
            assert replacement["segment"] != segment1["segment"]
            assert segment1_path.exists()
            assert checkpoint1_path.exists()
            assert segment1_audio_path.exists()
            assert prompt_path.exists()
            assert revision_metadata_path.exists()
            assert replacement["supersedes"] == segment1["revision_metadata"]
            active_metadata = json.loads(pathlib.Path(
                chain._absolute_output_path(replacement["metadata"])
            ).read_text(encoding="utf-8"))
            assert active_metadata["segment"]["revision"] == replacement["revision"]
            segment1 = replacement
            print("atomic save: interruption preserved old AV artifacts; retry "
                  "switched + retained prior revision")

            review_item, has_audio, warning = chain._review_video(
                prepared_plan, segment1, audio_for_frames(5))
            review_path = pathlib.Path(
                tempdir, review_item["subfolder"], review_item["filename"])
            assert has_audio and not warning and review_path.is_file()
            streams = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", str(review_path),
            ], text=True, encoding="utf-8", errors="replace").splitlines()
            assert "video" in streams and "audio" in streams

            fallback_review_audio = audio_for_frames(5)
            fallback_review_audio["waveform"][..., 0] = 0.25
            original_which = chain.shutil.which
            chain.shutil.which = lambda executable: (
                None if executable == "ffmpeg" else original_which(executable))
            try:
                fallback_review, fallback_has_audio, fallback_warning = (
                    chain._review_video(
                        prepared_plan, segment1, fallback_review_audio))
            finally:
                chain.shutil.which = original_which
            fallback_review_path = pathlib.Path(
                tempdir, fallback_review["subfolder"],
                fallback_review["filename"])
            assert (fallback_has_audio and not fallback_warning and
                    fallback_review_path.is_file())
            with chain.av.open(
                    str(fallback_review_path), mode="r") as fallback_media:
                assert len(fallback_media.streams.video) == 1
                assert len(fallback_media.streams.audio) == 1
            print("review: persisted segment muxed with frame-exact audio via "
                  "ffmpeg and the PyAV fallback")

            async def approve_live_review():
                sent = []
                unload_calls = []

                class ReviewServerInstance:
                    client_id = "smoke-client"

                    def send_sync(self, event, payload, client_id):
                        sent.append((event, payload, client_id))

                class ReviewServer:
                    instance = ReviewServerInstance()

                original_server = chain.PromptServer
                import comfy.model_management as model_management
                original_unload = model_management.unload_all_models
                model_management.unload_all_models = lambda: unload_calls.append(True)
                chain.PromptServer = ReviewServer
                try:
                    task = asyncio.create_task(
                        chain.MiniMaxH3ChainReview().review(
                            state1, segment1, True, False, 0.0,
                            True, False, "none",
                            audio_for_frames(5), unique_id="review-node"))
                    for _ in range(100):
                        if chain._PENDING_REVIEWS:
                            break
                        await asyncio.sleep(0.01)
                    assert chain._PENDING_REVIEWS and sent
                    review_events = [
                        payload for event, payload, _client in sent
                        if event == "minimax_h3_context_loop_review"]
                    assert review_events[0]["run_name"] == prepared_plan["run_name"]
                    assert review_events[0]["preview_pending"]
                    assert review_events[0]["preview_revision"] == 0
                    assert not review_events[-1]["preview_pending"]
                    assert review_events[-1]["preview_revision"] == 1
                    assert review_events[-1]["has_audio"]
                    token = review_events[-1]["token"]

                    class ApproveRequest:
                        async def json(self):
                            return {"token": token, "action": "approve"}

                    response = await chain._submit_review_decision(
                        ApproveRequest())
                    assert response.status == 200
                    result = await asyncio.wait_for(task, timeout=5.0)
                    assert result["result"][0]["segment"] == segment1["segment"]
                    assert unload_calls == [True]
                    assert not chain._PENDING_REVIEWS

                    timeout_task = asyncio.create_task(
                        chain.MiniMaxH3ChainReview().review(
                            state1, segment1, True, True, 0.001,
                            False, False, "none",
                            audio_for_frames(5), unique_id="review-node"))
                    timeout_result = await asyncio.wait_for(
                        timeout_task, timeout=2.0)
                    assert "timed out" in timeout_result["result"][1]
                    assert any(event == "minimax_h3_context_loop_review_resolved"
                               for event, _payload, _client in sent)
                    assert not chain._PENDING_REVIEWS

                    # Review muxing is UI-only. Publish the pending token first
                    # and fall back to a silent, actionable review if audio
                    # preview preparation fails.
                    real_review_video = chain._review_video

                    def fail_audio_preview(plan_arg, segment_arg, audio_arg):
                        if audio_arg is None:
                            return real_review_video(
                                plan_arg, segment_arg, audio_arg)
                        raise RuntimeError("simulated review audio failure")

                    chain._review_video = fail_audio_preview
                    event_offset = len(sent)
                    try:
                        fallback_task = asyncio.create_task(
                            chain.MiniMaxH3ChainReview().review(
                                state1, segment1, True, False, 0.0,
                                False, False, "none",
                                audio_for_frames(5), unique_id="review-node"))
                        for _ in range(100):
                            if chain._PENDING_REVIEWS:
                                break
                            await asyncio.sleep(0.01)
                        assert chain._PENDING_REVIEWS
                        fallback_events = [
                            payload for event, payload, _client in sent[event_offset:]
                            if event == "minimax_h3_context_loop_review"]
                        assert fallback_events[0]["preview_pending"]
                        assert not fallback_events[-1]["preview_pending"]
                        assert not fallback_events[-1]["has_audio"]
                        assert "review is silent" in fallback_events[-1]["warning"]
                        fallback_token = fallback_events[-1]["token"]

                        class FallbackApproveRequest:
                            async def json(self):
                                return {"token": fallback_token,
                                        "action": "approve"}

                        fallback_response = await chain._submit_review_decision(
                            FallbackApproveRequest())
                        assert fallback_response.status == 200
                        fallback_result = await asyncio.wait_for(
                            fallback_task, timeout=5.0)
                        assert "approved clip" in fallback_result["result"][1]
                    finally:
                        chain._review_video = real_review_video
                    assert not chain._PENDING_REVIEWS
                finally:
                    chain.PromptServer = original_server
                    model_management.unload_all_models = original_unload

            asyncio.run(approve_live_review())

            def approve_cross_thread_review():
                sent = []
                result = []

                class ReviewServerInstance:
                    client_id = "cross-thread-smoke-client"

                    def send_sync(self, event, payload, client_id):
                        sent.append((event, payload, client_id))

                class ReviewServer:
                    instance = ReviewServerInstance()

                original_server = chain.PromptServer
                chain.PromptServer = ReviewServer
                try:
                    def execute_review():
                        result.append(asyncio.run(
                            chain.MiniMaxH3ChainReview().review(
                                state1, segment1, True, False, 0.0,
                                False, False, "none",
                                audio_for_frames(5), unique_id="review-node")))

                    worker = threading.Thread(target=execute_review, daemon=True)
                    worker.start()
                    for _ in range(200):
                        if chain._PENDING_REVIEWS:
                            break
                        time.sleep(0.01)
                    assert chain._PENDING_REVIEWS and sent
                    token = sent[-1][1]["token"]

                    class ApproveRequest:
                        async def json(self):
                            return {"token": token, "action": "approve"}

                    response = asyncio.run(
                        chain._submit_review_decision(ApproveRequest()))
                    assert response.status == 200
                    worker.join(timeout=5.0)
                    assert not worker.is_alive()
                    assert result and "approved clip" in result[0]["result"][1]
                    assert not chain._PENDING_REVIEWS
                finally:
                    chain.PromptServer = original_server

            approve_cross_thread_review()
            assert chain._review_timeout_seconds(0) == 0
            assert chain._review_timeout_seconds(1.5) == 90
            print("review: same-loop, cross-thread, timeout, and silent-preview "
                  "fallback approvals resume")

            revised = chain._plan_with_review_revision(
                prepared_plan, 2, "Revised second scene.", 999)
            assert revised["base_plan_hash"] == prepared_plan["base_plan_hash"]
            assert revised["shots"][1]["prompt"] == "Revised second scene."
            assert revised["shots"][1]["seed"] == 999
            assert (chain._history_hash(revised, 1) ==
                    chain._history_hash(prepared_plan, 1))
            assert (chain._history_hash(revised, 2) !=
                    chain._history_hash(prepared_plan, 2))
            print("review: prompt/seed retry preserves accepted predecessor history")

            requeue_return = chain.MiniMaxH3ChainLoopEnd().end(
                ["1", 0], dict(state1), images1.clone(), av_latent(), dict(segment1),
                execution_mode="top_level_requeue")
            parsed_output, parsed_ui, parsed_subgraph = execution.get_output_from_returns(
                [requeue_return], chain.MiniMaxH3ChainLoopEnd)
            assert not parsed_subgraph and parsed_output and parsed_ui
            completion = parsed_ui.get("h3_chain_top_level_requeue")
            assert isinstance(completion, list) and len(completion) == 1
            assert completion[0]["handoff_id"]
            print("top-level requeue: real ComfyUI return parser emits completion UI")

            fake_prompt = {
                "1": {"class_type": "MiniMaxH3ChainLoopStart", "inputs": {
                    "plan": plan, "start_clip": 1, "source_audio": source,
                }},
                "2": {"class_type": "MiniMaxH3ChainCurrent", "inputs": {
                    "state": ["1", 1],
                }},
                "3": {"class_type": "MiniMaxH3ChainSegmentSave", "inputs": {
                    "state": ["2", 0],
                }},
                "4": {"class_type": "MiniMaxH3ChainLoopEnd", "inputs": {
                    "flow": ["1", 0], "state": ["2", 0],
                    "images": ["3", 0], "sampled_latent": ["3", 0],
                    "segment": ["3", 0],
                }},
            }
            cleanup_boundaries = []
            original_boundary_cleanup = chain._release_loop_boundary_resources
            chain._release_loop_boundary_resources = (
                lambda policy, scene: cleanup_boundaries.append(
                    (policy, scene)) or {"policy": policy})
            try:
                expanded = chain.MiniMaxH3ChainLoopEnd().end(
                    ["1", 0], state1, images1, av_latent(), segment1,
                    between_scene_cleanup="fresh_scene",
                    dynprompt=FakeDynamicPrompt(fake_prompt), unique_id="4")
            finally:
                chain._release_loop_boundary_resources = (
                    original_boundary_cleanup)
            assert isinstance(expanded, dict) and expanded.get("expand")
            assert cleanup_boundaries == [("fresh_scene", 1)]
            cloned_starts = [node for node in expanded["expand"].values()
                             if node["class_type"] == "MiniMaxH3ChainLoopStart"]
            assert len(cloned_starts) == 1
            assert cloned_starts[0]["inputs"]["initial_state"]["index"] == 2
            assert all(isinstance(link, list) for link in expanded["result"])
            print("recursion: GraphBuilder cloned the typed H3 body for clip 2")

            retry_segment = dict(segment1)
            retry_segment["_h3_review_decision"] = {
                "action": "retry",
                "scene_prompt": "Try the opening again.",
                "seed": 1234,
            }
            retried = chain.MiniMaxH3ChainLoopEnd().end(
                ["1", 0], state1, images1, av_latent(), retry_segment,
                dynprompt=FakeDynamicPrompt(fake_prompt), unique_id="4")
            retried_starts = [
                node for node in retried["expand"].values()
                if node["class_type"] == "MiniMaxH3ChainLoopStart"
            ]
            retry_state = retried_starts[0]["inputs"]["initial_state"]
            assert retry_state["index"] == 1
            assert retry_state["segments"] == []
            assert retry_state["plan"]["shots"][0]["seed"] == 1234
            assert retry_state["plan"]["shots"][0]["prompt"] == "Try the opening again."
            print("review: rejected clip recurses at the same index")

            state2 = chain._initial_state(prepared_plan, 2)
            assert state2["resumed_from"] == 1
            assert len(state2["segments"]) == 1
            assert tuple(state2["previous_frames"].shape) == (1, 32, 32, 3)
            assert len(state2["previous_latent"]["samples"]) == 2
            print("resume: clip 2 restored clip 1 frame tail + AV latent")

            state2 = chain.MiniMaxH3ChainCurrent().current(
                state2, source)["result"][0]
            images2 = torch.zeros((4, 32, 32, 3), dtype=torch.float32)
            result2 = saver.save(
                state2, images2, av_latent(), audio_for_frames(4))
            segment2 = result2["result"][0]

            async def stop_with_partial_review():
                sent = []

                class ReviewServerInstance:
                    client_id = "partial-stop-smoke-client"

                    def send_sync(self, event, payload, client_id):
                        sent.append((event, payload, client_id))

                class ReviewServer:
                    instance = ReviewServerInstance()

                original_server = chain.PromptServer
                chain.PromptServer = ReviewServer
                try:
                    task = asyncio.create_task(
                        chain.MiniMaxH3ChainReview().review(
                            state2, segment2, True, False, 0.0,
                            False, True, "checkpointed", audio_for_frames(4),
                            source, unique_id="review-node"))
                    for _ in range(100):
                        if chain._PENDING_REVIEWS:
                            break
                        await asyncio.sleep(0.01)
                    assert chain._PENDING_REVIEWS and sent
                    token = sent[-1][1]["token"]

                    class StopRequest:
                        async def json(self):
                            return {"token": token, "action": "stop"}

                    response = await chain._submit_review_decision(StopRequest())
                    assert response.status == 200
                    result = await asyncio.wait_for(task, timeout=10.0)
                    assert "partial video" in result["result"][1]
                    resolved = [payload for event, payload, _client in sent
                                if event == "minimax_h3_context_loop_review_resolved"]
                    assert resolved and resolved[-1]["partial_video"]
                    item = resolved[-1]["partial_video"]
                    partial_path = pathlib.Path(
                        tempdir, item["subfolder"], item["filename"])
                    assert partial_path.is_file() and partial_path.stat().st_size > 0
                    streams = subprocess.check_output([
                        "ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type", "-of", "csv=p=0",
                        str(partial_path),
                    ], text=True, encoding="utf-8", errors="replace").splitlines()
                    assert "video" in streams and "audio" in streams
                finally:
                    chain.PromptServer = original_server

            asyncio.run(stop_with_partial_review())
            partial_manifest = pathlib.Path(
                tempdir, "h3_chains", "smoke", "partial",
                "through_clip_0002.manifest.json")
            assert partial_manifest.is_file()
            partial_data = json.loads(partial_manifest.read_text())
            assert partial_data["format"] == "h3_chain_partial_manifest_v3"
            assert partial_data["clip_count"] == 2
            assert partial_data["segments"][0]["prompt"] == "first"
            assert partial_data["segments"][1]["prompt"] == "second"
            assert partial_data["archives"]["workflow"].endswith(
                "/workflow.json")
            print("review stop: joined partial AV video and checkpoint manifest")

            class CheckpointRequest:
                query = {"run_name": "smoke"}

            checkpoint_response = asyncio.run(
                chain._list_saved_checkpoints(CheckpointRequest()))
            checkpoint_body = json.loads(checkpoint_response.text)
            assert [item["scene"] for item in checkpoint_body["checkpoints"]] == [1, 2]
            assert all(item["ready"] for item in checkpoint_body["checkpoints"])
            assert all(item["video"] for item in checkpoint_body["checkpoints"])
            assert checkpoint_body["checkpoints"][1]["partial_video"]
            print("checkpoint browser: discovered both saved resume slots")

            complete = dict(state2)
            complete["segments"] = state2["segments"] + [segment2]
            manifest = chain._manifest_from_state(complete)

            loaded_manifest = chain.MiniMaxH3ChainManifestLoad().load(
                plan, source)[0]
            assert loaded_manifest["plan_hash"] == manifest["plan_hash"]
            assert len(loaded_manifest["segments"]) == 2
            assert pathlib.Path(tempdir, "h3_chains", "smoke",
                                "manifest.json").is_file()
            manifest = loaded_manifest
            print("manifest load: completed chain restored without rerender")

            class FakeVideoVAE:
                def __init__(self):
                    self.calls = 0

                def decode(self, _video):
                    self.calls += 1
                    images = torch.zeros(
                        (1, 5, 4, 4, 3), dtype=torch.float32)
                    for frame in range(5):
                        images[:, frame, ..., 0] = (
                            self.calls * 10 + frame) / 255.0
                    return images

            fake_vae = FakeVideoVAE()
            png_result = chain.MiniMaxH3ChainExportPNG().export(
                manifest, fake_vae, "archive", 1, 1, True)
            png_dir = pathlib.Path(png_result["result"][0])
            png_files = sorted(png_dir.glob("frame_*.png"))
            assert png_result["result"][1] == 9
            assert len(png_files) == 9
            assert [path.name for path in (png_files[0], png_files[-1])] == [
                "frame_00000001.png", "frame_00000009.png"]
            png_export = json.loads(
                (png_dir / "export.json").read_text(encoding="utf-8"))
            assert png_export["complete"]
            assert png_export["frame_count"] == 9
            assert png_export["clips"][0]["first_frame_number"] == 1
            assert png_export["clips"][1]["first_frame_number"] == 6
            assert png_export["clips"][1]["trim_frames"] == 1
            with PILImage.open(png_files[0]) as first_png:
                assert json.loads(first_png.text["workflow"])["nodes"][0][
                    "type"] == "MiniMaxH3ChainPlan"
                assert json.loads(first_png.text["h3_manifest"])[
                    "clip_count"] == 2
                assert json.loads(first_png.text["h3_scene"])[
                    "prompt"] == "first"
            with PILImage.open(png_files[5]) as second_scene_png:
                assert second_scene_png.text["h3_clip_index"] == "2"
                assert json.loads(second_scene_png.text["h3_scene"])[
                    "prompt"] == "second"
            # The second clip's raw frame 1 is overlap and must be absent: its
            # first delivered PNG therefore carries fake decoded value 21.
            with PILImage.open(png_files[5]) as trimmed_png:
                assert trimmed_png.getpixel((0, 0))[0] == 21
            assert fake_vae.calls == 2
            print("PNG export: checkpoints re-decoded one scene at a time; "
                  "overlap trimmed and workflow metadata preserved")

            assembler = chain.MiniMaxH3ChainAssemble()
            source_result = assembler.assemble(
                manifest, "source", "source_final", 96, source)
            source_path = pathlib.Path(source_result["result"][0])
            assert source_path.is_file() and source_path.stat().st_size > 0
            generated_sidecar = source_path.with_suffix(".generated.wav")
            assert generated_sidecar.is_file()
            with wave.open(str(generated_sidecar), "rb") as saved_audio:
                assert saved_audio.getframerate() == 8000
                assert saved_audio.getnchannels() == 2
                assert saved_audio.getnframes() == round(9 / 24 * 8000)
            assert "generated audio ->" in source_result["ui"]["text"][0]
            source_tags = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format_tags",
                "-of", "json", str(source_path),
            ], text=True, encoding="utf-8", errors="replace"))["format"]["tags"]
            assert json.loads(source_tags["workflow"])["nodes"][0][
                "type"] == "MiniMaxH3ChainPlan"
            assert json.loads(source_tags["h3_manifest"])["clip_count"] == 2
            duration = float(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(source_path),
            ], text=True, encoding="utf-8", errors="replace").strip())
            assert abs(duration - 9 / 24) < 0.05
            short_silent_manifest = dict(manifest)
            short_silent_manifest["compatibility"] = dict(
                short_started[1]["plan"]["compatibility"])
            # Exercise legacy AUDIO padding, without the different source
            # timeline recovered from the original full-length fixture.
            short_silent_manifest.pop("source_timeline", None)
            short_silent_manifest["plan"] = dict(manifest.get("plan") or {})
            short_silent_manifest["plan"].pop("source_timeline", None)
            short_silent_result = assembler.assemble(
                short_silent_manifest, "source", "short_silent_final", 96,
                short_source)
            short_silent_path = pathlib.Path(short_silent_result["result"][0])
            assert short_silent_path.is_file() and short_silent_path.stat().st_size > 0
            try:
                assembler.assemble(
                    manifest, "source", "wrong_source", 96, changed_source)
            except ValueError as exc:
                assert "different source waveform" in str(exc)
            else:
                raise AssertionError("Assemble accepted a different source song")

            generated_result = assembler.assemble(
                manifest, "generated", "generated_final", 96)
            generated_path = pathlib.Path(generated_result["result"][0])
            assert generated_path.is_file() and generated_path.stat().st_size > 0

            original_which = chain.shutil.which
            chain.shutil.which = lambda executable: (
                None if executable == "ffmpeg" else original_which(executable))
            try:
                fallback_result = assembler.assemble(
                    manifest, "generated", "pyav_fallback_final", 96)
            finally:
                chain.shutil.which = original_which
            fallback_path = pathlib.Path(fallback_result["result"][0])
            assert fallback_path.is_file() and fallback_path.stat().st_size > 0
            assert "PyAV fallback" in fallback_result["ui"]["text"][0]
            with chain.av.open(str(fallback_path), mode="r") as fallback_media:
                assert len(fallback_media.streams.video) == 1
                assert len(fallback_media.streams.audio) == 1
                assert json.loads(fallback_media.metadata["h3_manifest"])[
                    "clip_count"] == 2
                fallback_duration = (
                    float(fallback_media.duration) / float(chain.av.time_base))
                assert abs(fallback_duration - 9 / 24) < 0.05
                assert sum(1 for _frame in fallback_media.decode(video=0)) == 9
            print("segments: H.264 save + per-scene/combined generated WAVs + "
                  "source/generated audio assembly and PyAV fallback pass")

            changed = json.loads(json.dumps({"shots": [
                {"id": "one", "prompt": "changed", "length": 5, "seed": 1},
                {"id": "two", "prompt": "second", "length": 5, "seed": 2},
            ]}))
            changed_plan = chain._normalize_plan(
                json.dumps(changed), "smoke", 32, 32, 1, "video", "head",
                "disabled", "source_track", 1, 1, 2, 1, 30)
            try:
                chain._initial_state(
                    chain._plan_with_source_audio(changed_plan, source), 2)
            except ValueError as exc:
                assert "scene_generation.prompt_hash" in str(exc)
            else:
                raise AssertionError("resume accepted a changed predecessor")
            print("resume guard: changed predecessor rejected")
            unsafe_state = chain._initial_state(
                chain._plan_with_source_audio(changed_plan, source), 2,
                verify_resume_history=False)
            assert unsafe_state["resumed_from"] == 1
            assert unsafe_state["resume_history_verification_disabled"]
            assert unsafe_state["segments"][0]["prompt"] == "first"
            print(
                "resume override: incompatible Plan history reused the intact "
                "saved predecessor explicitly")

            changed_generation_plan = chain._normalize_plan(
                json.dumps({"shots": [
                    {"id": "one", "prompt": "first", "length": 5, "seed": 1},
                    {"id": "two", "prompt": "second", "length": 5, "seed": 2},
                ]}),
                "smoke", 32, 32, 1, "video", "head", "disabled",
                "source_track", 1, 1, 2, 1, 30, "model-and-refs-v2")
            try:
                chain._initial_state(chain._plan_with_source_audio(
                    changed_generation_plan, source), 2)
            except ValueError as exc:
                assert "global_generation.generation_fingerprint" in str(exc)
            else:
                raise AssertionError(
                    "resume accepted a changed generation fingerprint")
            print("resume guard: external generation fingerprint enforced")

            try:
                chain._initial_state(
                    chain._plan_with_source_audio(plan, changed_source), 2)
            except ValueError as exc:
                assert "scene_generation.source_reference_window" in str(exc)
            else:
                raise AssertionError("resume accepted changed source audio")
            print("resume guard: changed source track rejected")
        finally:
            folder_paths.set_output_directory(previous_output)

    print("chain smoke test passed")


if __name__ == "__main__":
    main()
