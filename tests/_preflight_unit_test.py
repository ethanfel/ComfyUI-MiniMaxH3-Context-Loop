#!/usr/bin/env python3
"""0.5 preflight is model-free, structured, and shared by Studio/Start."""

import importlib.util
import json
import pathlib
import sys
import tempfile
import types

import torch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_preflight_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test"
shared_nodes._prepare_native_guide_conditioning = lambda value: value
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


def make_plan(run_name):
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        chain._contract_transition_policy(
            "guide", expert_override=True,
            continuation_mode="guide", context_length=5),
        audio_context_length=5)
    return chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "Opening action.", "length": 22},
            {"id": "two", "prompt": "Continuation action.", "length": 22},
        ]}),
        run_name, 64, 64, 5, "video", "head", "disabled",
        "source_track", 5, 1.0, 8, 7, 18, "stack:auto:v1", 0,
        "guide", policy)


def deferred_timeline(frames):
    sample_rate = 48000
    samples = round(frames / 24 * sample_rate)
    audio = {
        "waveform": torch.linspace(-0.5, 0.5, samples).reshape(1, 1, -1),
        "sample_rate": sample_rate,
    }
    audio_hash = chain._audio_fingerprint(audio)
    timeline_hash = chain._fingerprint({"audio": audio_hash, "frames": frames})
    return {
        "version": chain.SOURCE_TIMELINE_VERSION,
        "kind": "source_timeline",
        "fps": 24,
        "origin": {"source_fps": 24.0, "skip_first_frames": 0,
                   "skip_seconds": 0.0},
        "extent": {"frame_count": frames,
                   "duration_seconds": frames / 24.0},
        "video": None,
        "audio": {"kind": "deferred_tensor", "value": audio,
                  "sample_rate": sample_rate, "channels": 1,
                  "duration_seconds": frames / 24.0,
                  "timeline_offset_seconds": 0.0,
                  "content_sha256": audio_hash},
        "fingerprints": {"video": "", "audio": audio_hash,
                         "timeline": timeline_hash},
        "recovery": {"video_path": "", "audio_path": "",
                     "deferred_audio_requires_materialization": True},
    }


def semantic_plan(prompt):
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("generated", "off", "on"),
        chain._contract_transition_policy("cut"),
        audio_context_length=0)
    return chain._normalize_plan(
        json.dumps({"shots": [{
            "id": "semantic", "prompt": prompt, "length": 124,
        }]}),
        "semantic_preflight", 64, 64, 1, "video", "head", "disabled",
        "generated_audio", 0, 5.0, 8, 7, 18, "stack:auto:v1", 0,
        "guide", policy)


def assert_complete_diagnostic(issue):
    assert issue["message"]
    assert issue["action"]
    assert issue["solutions"]
    assert issue["solutions"][0] == issue["action"]
    assert issue["triggers"]


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    chain._output_root = lambda: str(root)
    plan = make_plan("preflight")
    timeline = deferred_timeline(39)

    _prepared, inherited_missing = chain._preflight_chain(plan)
    inherited_text = chain._preflight_failure_text(inherited_missing)
    assert ("Trigger: final output: final_audio=source (Chain Policy)"
            in inherited_text)
    assert ('Trigger: Scene 1 "one": source_reference=on (Chain Policy)'
            in inherited_text)

    prepared, report = chain._preflight_chain(
        plan, source_timeline=timeline)
    assert report["ok"] is True
    assert report["status"] == "warning"  # isolated test lacks Comfy runtime
    assert report["source"]["required_frames"] == 39
    assert report["source"]["last_complete_scene"] == 2
    assert [scene["overlap_trim_frames"] for scene in report["scenes"]] == [0, 5]
    assert report["scenes"][1]["source_start_frame"] == 17
    assert prepared["compatibility"]["source_timeline_fingerprint"] == (
        timeline["fingerprints"]["timeline"])
    assert not root.exists() or not any(root.iterdir())

    studio = chain.MiniMaxH3ChainPlanStudio().passthrough(
        plan, source_timeline=timeline)
    assert studio["result"][0] is plan and studio["result"][2] is True
    assert json.loads(studio["result"][4])["version"] == chain.PREFLIGHT_VERSION
    assert "h3_plan_studio_source_timeline" in studio["ui"]

    studio_inputs = chain.MiniMaxH3ChainPlanStudio.INPUT_TYPES()
    assert studio_inputs["required"] == {}
    assert next(iter(studio_inputs["optional"])) == "plan"
    assert all(name in studio_inputs["optional"] for name in (
        "plan_json", "run_name", "generation_fingerprint", "width",
        "height", "base_seed", "segment_crf", "chain_policy"))
    assert chain.MiniMaxH3ChainPlanStudio.RETURN_NAMES[-5:] == (
        "plan_summary", "clip_count", "width", "height",
        "video_blend_frames")
    preflight_inputs = chain.MiniMaxH3ChainPreflight.INPUT_TYPES()
    assert tuple(preflight_inputs["required"]) == ("plan",)
    assert "plan_json" not in preflight_inputs["optional"]
    assert len(chain.MiniMaxH3ChainPreflight.RETURN_TYPES) == 5
    loop_start_inputs = chain.MiniMaxH3ChainLoopStart.INPUT_TYPES()
    assert tuple(loop_start_inputs["optional"])[-3:] == (
        "source_timeline", "tagged_references", "reference_schedule")
    assert loop_start_inputs["optional"]["tagged_references"][0] == (
        chain.TAGGED_REFERENCE_TYPE)
    assert loop_start_inputs["optional"]["reference_schedule"][0] == (
        chain.REFERENCE_SCHEDULE_TYPE)

    standalone_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("generated", "off", "on"),
        chain._contract_transition_policy("cut"),
        audio_context_length=0)
    standalone = chain.MiniMaxH3ChainPlanStudio().passthrough(
        plan_json=json.dumps({"shots": [{
            "id": "standalone", "prompt": "Independent shot.",
            "length": 22,
        }]}),
        run_name="studio_standalone", generation_fingerprint="studio:v1",
        width=64, height=96, context_length=1,
        encode_mode="video", anchor_mode="head", crop="disabled",
        audio_mode="generated_audio", audio_context_length=0,
        default_duration_seconds=1.0, default_steps=8,
        base_seed=11, segment_crf=17, video_blend_frames=0,
        continuation_mode="guide", chain_policy=standalone_policy,
    )
    standalone_plan = standalone["result"][0]
    assert standalone_plan["run_name"] == "studio_standalone"
    assert standalone_plan["compatibility"]["width"] == 64
    assert standalone_plan["compatibility"]["height"] == 96
    assert standalone_plan["shots"][0]["id"] == "standalone"
    assert standalone["result"][-4:] == (1, 64, 96, 0)

    tagged = chain._append_tagged_reference(
        None, kind="picture", tag="future", value="future-picture",
        content_hash="future-picture-v1")
    semantic_draft = chain.MiniMaxH3SemanticPictureAnchor().add(
        torch.zeros((1, 2, 2, 3)), "story_beat")[0]
    tagged_with_semantic = chain.MiniMaxH3SemanticAnchorBundle().bundle(
        semantic_draft, "512", "timestamped_video",
        references=tagged)[0]
    semantic_token = chain._plan_studio_generation_fingerprint(
        "", tagged_with_semantic)
    semantic_current, semantic_lineage = (
        chain._generation_fingerprint_value(semantic_token))
    assert semantic_current == tagged_with_semantic[
        "combined_reference_fingerprint"]
    assert [entry["tag"] for entry in semantic_lineage["entries"]] == [
        "future", "story_beat"]
    automatic = chain.MiniMaxH3ChainPlanStudio().passthrough(
        plan_json=json.dumps({"shots": [{
            "id": "automatic", "prompt": "Independent shot.",
            "length": 22,
        }]}),
        run_name="studio_automatic_fingerprint",
        generation_fingerprint="", tagged_references=tagged,
        width=64, height=96, context_length=1,
        encode_mode="video", anchor_mode="head", crop="disabled",
        audio_mode="generated_audio", audio_context_length=0,
        default_duration_seconds=1.0, default_steps=8,
        base_seed=11, segment_crf=17, video_blend_frames=0,
        continuation_mode="guide", chain_policy=standalone_policy,
    )["result"][0]
    assert automatic["compatibility"]["generation_fingerprint"] == (
        tagged["fingerprint"])
    assert automatic["compatibility"][
        "generation_fingerprint_lineage"]["entries"][0]["tag"] == "future"

    combined = chain.MiniMaxH3ChainPlanStudio().passthrough(
        plan_json=json.dumps({"shots": [{
            "id": "combined", "prompt": "Independent shot.", "length": 22,
        }]}),
        run_name="studio_combined_fingerprint",
        generation_fingerprint="model-stack:v2", tagged_references=tagged,
        width=64, height=96, context_length=1,
        encode_mode="video", anchor_mode="head", crop="disabled",
        audio_mode="generated_audio", audio_context_length=0,
        default_duration_seconds=1.0, default_steps=8,
        base_seed=11, segment_crf=17, video_blend_frames=0,
        continuation_mode="guide", chain_policy=standalone_policy,
    )["result"][0]
    combined_compatibility = combined["compatibility"]
    assert combined_compatibility["generation_fingerprint"] != (
        tagged["fingerprint"])
    assert combined_compatibility["generation_fingerprint_lineage"][
        "wrapper"]["contract"]["external_generation_fingerprint"] == (
            "model-stack:v2")

    empty_tagged = chain._make_tagged_references([])
    assert chain._plan_studio_generation_fingerprint("", empty_tagged) == ""

    connected = chain.MiniMaxH3ChainPlanStudio().passthrough(
        plan, source_timeline=timeline, tagged_references=tagged,
        generation_fingerprint="must-not-replace-connected-plan")
    assert connected["result"][0] is plan

    short = deferred_timeline(30)
    _prepared, failed = chain._preflight_chain(
        plan, source_timeline=short)
    assert failed["ok"] is False
    assert failed["source"]["shortfall_frames"] == 9
    assert failed["source"]["last_complete_scene"] == 1
    issue = next(item for item in failed["errors"]
                 if item["code"] == "source_audio_too_short")
    assert_complete_diagnostic(issue)
    assert issue["measurements"] == {
        "required_frames": 39,
        "required_seconds": 39 / 24,
        "available_frames": 30,
        "available_seconds": 30 / 24,
        "shortfall_frames": 9,
        "shortfall_seconds": 9 / 24,
        "last_complete_scene": {"scene": 1, "scene_id": "one"},
        "first_affected_scene": {"scene": 2, "scene_id": "two"},
    }
    assert len(issue["solutions"]) == 3
    short_text = chain._preflight_failure_text(failed)
    assert ('last_complete_scene=Scene 1 "one"' in short_text)
    assert ('first_affected_scene=Scene 2 "two"' in short_text)
    assert "Alternative:" in short_text

    materialized = []
    original = chain._materialize_source_timeline_audio
    chain._materialize_source_timeline_audio = (
        lambda *args, **kwargs: materialized.append(True))
    try:
        try:
            chain.MiniMaxH3ChainLoopStart().start(
                plan, 1, source_timeline=short)
        except ValueError as exc:
            assert "source_audio_too_short" in str(exc)
        else:
            raise AssertionError("Loop Start accepted failed preflight")
    finally:
        chain._materialize_source_timeline_audio = original
    assert materialized == []

    # A later scene override must be named explicitly when it makes source
    # audio mandatory. Loop Start validates the complete Plan before Scene 1,
    # so a generic "active policy" error otherwise gives no clue where to fix.
    generated_policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("generated", "off", "on"),
        chain._contract_transition_policy("cut"),
        audio_context_length=0)
    scene_override_plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "1_5", "prompt": "Scene one.", "length": 22},
            {"id": "2_5", "prompt": "Scene two.", "length": 22},
            {"id": "3_5", "prompt": "Scene three.", "length": 22},
            {"id": "4_5", "prompt": "Scene four.", "length": 22,
             "source_reference": "on"},
            {"id": "5_5", "prompt": "Scene five.", "length": 22},
        ]}),
        "preflight-scene-source-override", 64, 64, 1,
        "video", "head", "disabled", "generated_audio", 0,
        1.0, 8, 7, 18, "stack:auto:v1", 0, "guide",
        generated_policy)
    _prepared, missing_source = chain._preflight_chain(
        scene_override_plan)
    missing_issue = next(
        item for item in missing_source["errors"]
        if item["code"] == "source_audio_missing")
    assert missing_issue["requirements"] == [{
        "scope": "scene", "scene": 4, "scene_id": "4_5",
        "setting": "source_reference", "value": "on",
        "origin": "scene override",
    }]
    assert_complete_diagnostic(missing_issue)
    scene_four = scene_override_plan["shots"][3]
    assert missing_issue["measurements"]["required_frames"] == (
        scene_four["generation_start_frame"] + scene_four["raw_frames"])
    assert missing_issue["measurements"]["last_complete_scene"] == {
        "scene": 3, "scene_id": "3_5"}
    assert missing_issue["measurements"]["first_affected_scene"] == {
        "scene": 4, "scene_id": "4_5"}
    failure_text = chain._preflight_failure_text(missing_source)
    assert ("Loop Start source_timeline and legacy source_audio inputs are "
            "both disconnected" in failure_text)
    assert ('Trigger: Scene 4 "4_5": source_reference=on '
            '(scene override)' in failure_text)

    _prepared, invalid_plan = chain._preflight_chain(None)
    invalid_plan_issue = invalid_plan["errors"][0]
    assert invalid_plan_issue["code"] == "invalid_plan"
    assert_complete_diagnostic(invalid_plan_issue)
    assert invalid_plan_issue["triggers"][0]["value"] == "plan"

    _prepared, invalid_selection = chain._preflight_chain(
        scene_override_plan, scene_range="99")
    selection_issue = invalid_selection["errors"][0]
    assert selection_issue["code"] == "invalid_scene_selection"
    assert_complete_diagnostic(selection_issue)

    _prepared, invalid_source = chain._preflight_chain(
        scene_override_plan, source_timeline=deferred_timeline(100),
        source_audio=deferred_timeline(100)["audio"]["value"])
    source_issue = invalid_source["errors"][0]
    assert source_issue["code"] == "invalid_source_input"
    assert_complete_diagnostic(source_issue)

    tagged_picture = chain.MiniMaxH3TaggedPictureReference().add(
        torch.zeros((1, 32, 32, 3)), "replacement")[0]
    _prepared, semantic = chain._preflight_chain(
        semantic_plan(
            "Use @replacement and #replacement[0.00s] plus "
            "#replacement[4.75s]."),
        tagged_references=tagged_picture)
    assert semantic["ok"] is True
    assert semantic["scenes"][0]["semantic_anchors"] == [
        {"tag": "replacement", "timestamp_seconds": 0.0},
        {"tag": "replacement", "timestamp_seconds": 4.75},
    ]
    assert semantic["scenes"][0]["references"][0]["tag"] == "replacement"

    _prepared, bare_semantic = chain._preflight_chain(
        semantic_plan("Use #replacement as an untimed semantic visual."),
        tagged_references=tagged_picture)
    assert bare_semantic["ok"] is True
    assert bare_semantic["scenes"][0]["semantic_anchors"] == [
        {"tag": "replacement", "timestamp_seconds": None},
    ]

    dedicated_draft = chain.MiniMaxH3SemanticPictureAnchor().add(
        torch.zeros((1, 32, 32, 3)), "semantic_only")[0]
    dedicated_references = chain.MiniMaxH3SemanticAnchorBundle().bundle(
        dedicated_draft, "512", "timestamped_video",
        references=tagged_picture)[0]
    _prepared, dedicated = chain._preflight_chain(
        semantic_plan("Use @replacement and #semantic_only[0.00s]."),
        tagged_references=dedicated_references)
    assert dedicated["ok"] is True
    assert dedicated["references"]["semantic_route"] == "bundle"
    assert dedicated["references"]["registered_semantic_tags"] == [
        "semantic_only"]
    assert [item["tag"] for item in dedicated["scenes"][0]["references"]] == [
        "replacement"]

    semantic_only_references = chain.MiniMaxH3SemanticAnchorBundle().bundle(
        dedicated_draft, "512", "timestamped_video")[0]
    _prepared, semantic_only_single_wire = chain._preflight_chain(
        semantic_plan("Use #semantic_only[0.00s]."),
        tagged_references=semantic_only_references)
    assert semantic_only_single_wire["ok"] is True
    assert semantic_only_single_wire["references"]["route"] == "tagged"
    assert semantic_only_single_wire["references"][
        "registered_semantic_tags"] == ["semantic_only"]

    # Loop Start owns its own model-free preflight invocation. Its reference
    # sockets must therefore forward the same active registry used downstream;
    # otherwise valid prompt @tags fail before Ref2VA can execute.
    tagged_loop_plan = semantic_plan("Keep @replacement unchanged.")
    tagged_prompt = tagged_loop_plan["shots"][0]["prompt"]
    tagged_plan_json = json.dumps(tagged_loop_plan, sort_keys=True)
    logged_warnings = []
    original_warning = chain._LOG.warning
    chain._LOG.warning = lambda message, *args: logged_warnings.append(
        message % args if args else message)
    try:
        tagged_started = chain.MiniMaxH3ChainLoopStart().start(
            tagged_loop_plan, 1, tagged_references=tagged_picture)
        assert tagged_started[0] == "h3_chain"
        assert not any("reference_registry_not_connected" in warning
                       for warning in logged_warnings)
        assert tagged_loop_plan["shots"][0]["prompt"] == tagged_prompt
        assert json.dumps(tagged_loop_plan, sort_keys=True) == tagged_plan_json

        _prepared, disconnected = chain._preflight_chain(tagged_loop_plan)
        assert any(item["code"] == "reference_registry_not_connected"
                   for item in disconnected["warnings"])
        logged_warnings.clear()
        disconnected_started = chain.MiniMaxH3ChainLoopStart().start(
            tagged_loop_plan, 1)
        assert disconnected_started[0] == "h3_chain"
        assert any("reference_registry_not_connected" in warning
                   for warning in logged_warnings)

        scheduled = chain._append_scheduled_reference(
            None, kind="picture", tag="replacement", scenes="",
            value=torch.zeros((1, 32, 32, 3)),
            content_hash="scheduled-v1")
        logged_warnings.clear()
        scheduled_started = chain.MiniMaxH3ChainLoopStart().start(
            tagged_loop_plan, 1, reference_schedule=scheduled)
        assert scheduled_started[0] == "h3_chain"
        assert not any("reference_registry_not_connected" in warning
                       for warning in logged_warnings)
    finally:
        chain._LOG.warning = original_warning
    try:
        chain.MiniMaxH3ChainLoopStart().start(
            tagged_loop_plan, 1, tagged_references=tagged_picture,
            reference_schedule=scheduled)
    except ValueError as exc:
        assert "multiple_reference_routes" in str(exc)
    else:
        raise AssertionError("Loop Start accepted both reference routes")

    _prepared, unknown_anchor = chain._preflight_chain(
        semantic_plan("Use #missing[1.00s]."),
        tagged_references=tagged_picture)
    assert any(item["code"] == "unresolved_semantic_anchor"
               for item in unknown_anchor["errors"])

    _prepared, late_anchor = chain._preflight_chain(
        semantic_plan("Use #replacement[9.00s]."),
        tagged_references=tagged_picture)
    assert any(item["code"] == "semantic_anchor_out_of_range"
               for item in late_anchor["errors"])

print("H3 preflight: exact timing, source shortfall, semantic anchors, Studio report, and early Loop Start block pass")
