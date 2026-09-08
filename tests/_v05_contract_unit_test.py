#!/usr/bin/env python3
"""Freeze the 0.5 contracts and the public 0.4 compatibility surface."""

import ast
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = json.loads((
    ROOT / "tests" / "fixtures" / "v0_4_public_contract.json"
).read_text(encoding="utf-8"))

spec = importlib.util.spec_from_file_location(
    "h3_contracts_v05", ROOT / "contracts_v05.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


def class_node(module, name):
    return next(item for item in module.body
                if isinstance(item, ast.ClassDef) and item.name == name)


def literal_string_tuple(value):
    assert isinstance(value, (ast.Tuple, ast.List))
    return [ast.literal_eval(item) for item in value.elts]


def return_names(module, name):
    node = class_node(module, name)
    assignment = next(item for item in node.body
                      if isinstance(item, ast.Assign)
                      and any(isinstance(target, ast.Name)
                              and target.id == "RETURN_NAMES"
                              for target in item.targets))
    return literal_string_tuple(assignment.value)


def plan_required_input_order(module):
    node = class_node(module, "MiniMaxH3ChainPlan")
    method = next(item for item in node.body
                  if isinstance(item, ast.FunctionDef)
                  and item.name == "INPUT_TYPES")
    returned = next(item.value for item in ast.walk(method)
                    if isinstance(item, ast.Return))
    assert isinstance(returned, ast.Dict)
    outer = {ast.literal_eval(key): value
             for key, value in zip(returned.keys, returned.values)}
    required = outer["required"]
    assert isinstance(required, ast.Dict)
    return [ast.literal_eval(key) for key in required.keys]


def method_input_order(module, class_name, group):
    node = class_node(module, class_name)
    method = next(item for item in node.body
                  if isinstance(item, ast.FunctionDef)
                  and item.name == "INPUT_TYPES")
    returned = next(item.value for item in ast.walk(method)
                    if isinstance(item, ast.Return))
    assert isinstance(returned, ast.Dict)
    outer = {ast.literal_eval(key): value
             for key, value in zip(returned.keys, returned.values)}
    values = outer[group]
    assert isinstance(values, ast.Dict)
    return [ast.literal_eval(key) for key in values.keys]


def main():
    assert FIXTURE["format"] == "h3_v0_4_public_contract_fixture_v1"
    assert contracts.SOURCE_TIMELINE_VERSION == "h3_source_timeline_v1"
    assert contracts.AUDIO_POLICY_VERSION == "h3_audio_policy_v1"
    assert contracts.TRANSITION_POLICY_VERSION == "h3_transition_policy_v1"
    assert contracts.CHAIN_POLICY_VERSION == "h3_chain_policy_v1"
    assert contracts.LIP_SYNC_OPTIONS_VERSION == "h3_lip_sync_options_v1"
    assert contracts.SCENE_DEPENDENCY_VERSION == "h3_scene_dependency_v1"
    assert contracts.PREFLIGHT_VERSION == "h3_preflight_v1"
    assert contracts.ADVANCED_TRANSITION_PRESETS == (
        "cut", "guide", "tone_guide", "latent_guide", "detail_guide",
        "detail_av", "drift_av", "color_drift_av", "hard_av", "soft_av")
    assert contracts.GENERATION_SCENE_PROFILES == {
        "Visual continuity": "guide",
        "Independent scenes": "cut",
        "Hard picture + protected audio": "hard_av",
        "Hard picture + smooth audio": "soft_av",
    }
    assert contracts.GENERATION_AUDIO_PROFILES[
        "Generate audio"] == ("generated", "off", "on", False)
    assert contracts.GENERATION_AUDIO_PROFILES[
        "Lip-sync to source audio"] == ("source", "off", "off", True)
    assert contracts.LATENT_COLOR_CARRY_RECIPE == {
        "version": "h3_latent_color_delta_v1",
        "context_frames": 39,
        "video_steps": 12,
        "anchor": "first_generated_scene_delivered_tail",
        "source": "current_predecessor_delivered_tail",
        "strength": 0.50,
        "max_luma_shift_code_values": 6.0,
        "max_saturation_change": 0.06,
        "spatial_lowpass_kernel": 3,
        "temporal_taper": "full_prefix_smoothstep_zero_to_one",
        "delta_rule": "E(graded_D(z))-E(D(z))",
        "audio": "unchanged",
    }
    assert contracts.CONTEXT_SPATIAL_PROXY_MODES == (
        "off", "rgb_5_6", "latent_5_6")
    assert contracts.CONTEXT_SPATIAL_PROXY_RECIPE[
        "version"] == "h3_context_spatial_proxy_v2"
    dependency_shape = contracts.scene_dependency_shape()
    assert dependency_shape["version"] == contracts.SCENE_DEPENDENCY_VERSION
    assert tuple(dependency_shape["scopes"]) == contracts.DEPENDENCY_SCOPES

    for legacy, expected in FIXTURE["legacy_audio_modes"].items():
        migrated = contracts.migrate_legacy_audio_mode(legacy)
        assert migrated.pop("version") == contracts.AUDIO_POLICY_VERSION
        assert migrated == expected
    try:
        contracts.migrate_legacy_audio_mode("ambiguous")
    except ValueError as exc:
        assert "Unknown legacy H3 audio mode" in str(exc)
    else:
        raise AssertionError("unknown legacy audio mode was accepted")

    for final_audio in contracts.FINAL_AUDIO_POLICIES:
        for source_reference in contracts.SOURCE_REFERENCE_POLICIES:
            for continuity in contracts.GENERATED_CONTINUITY_POLICIES:
                policy = contracts.audio_policy(
                    final_audio, source_reference, continuity)
                assert policy == {
                    "version": contracts.AUDIO_POLICY_VERSION,
                    "final_audio": final_audio,
                    "source_reference": source_reference,
                    "generated_continuity": continuity,
                }
    locked_audio = contracts.audio_policy(
        "source", "on", "on", "locked")
    assert locked_audio == {
        "version": contracts.AUDIO_POLICY_VERSION,
        "final_audio": "source",
        "source_reference": "off",
        "generated_continuity": "off",
        "source_audio_target": "locked",
    }
    song_options = contracts.masked_song_options()
    assert song_options == {
        "version": contracts.LIP_SYNC_OPTIONS_VERSION,
        "preroll_seconds": 1.0,
        "lookahead_seconds": 0.2,
        "audio_denoise": 0.0,
        "gap_denoise": 0.15,
        "gate_hold_seconds": 0.2,
    }
    vocal_hash = "ab" * 32
    vocal_options = contracts.masked_song_options(
        {**song_options, "voice_fingerprint": vocal_hash})
    assert vocal_options["voice_fingerprint"] == vocal_hash
    contextual_audio = contracts.audio_policy(
        "source", "on", "on", "locked", vocal_options)
    assert contextual_audio["lip_sync_options"] == vocal_options
    assert "lip_sync_options" not in contracts.audio_policy(
        "generated", "off", "on", "off", vocal_options)
    try:
        contracts.masked_song_options({**song_options, "gap_denoise": 1.1})
    except ValueError as exc:
        assert "gap denoise" in str(exc)
    else:
        raise AssertionError("out-of-range lip-sync denoise was accepted")
    assert contracts.paired_audio_policy(True) == "embedded"
    assert contracts.paired_audio_policy(False) == "off"
    assert contracts.paired_audio_policy("embedded") == "embedded"
    try:
        contracts.audio_policy("copied", "off", "on")
    except ValueError as exc:
        assert "final-audio" in str(exc)
    else:
        raise AssertionError("unknown final-audio policy was accepted")
    try:
        contracts.paired_audio_policy("timeline")
    except ValueError as exc:
        assert "paired-audio" in str(exc)
    else:
        raise AssertionError("unknown paired-audio policy was accepted")

    assert contracts.PRIMARY_TRANSITION_PRESETS == (
        "cut", "guide", "hard_av", "soft_av")
    compact = contracts.chain_policy(
        "soft_av", "source", "on", "off")
    assert compact == {
        "version": contracts.CHAIN_POLICY_VERSION,
        "audio_policy": {
            "version": contracts.AUDIO_POLICY_VERSION,
            "final_audio": "source",
            "source_reference": "on",
            "generated_continuity": "off",
        },
        "transition_policy": {
            "version": contracts.TRANSITION_POLICY_VERSION,
            "preset": "soft_av",
            "label": "Soft continuation (hard picture, soft audio)",
            "continuation_mode": "audio_feathered_av",
            "context_length": 39,
            "expert_override": False,
        },
        "audio_context_length": 39,
    }
    # Compact authoring derives the hidden audio overlap from the tested
    # transition pair. The independent audio-policy axis gates it at runtime.
    assert compact["audio_context_length"] == 39
    locked_compact = contracts.chain_policy(
        "soft_av", "source", "on", "on", True)
    assert locked_compact["audio_policy"] == locked_audio
    assert locked_compact["audio_context_length"] == 39
    assert contracts.chain_policy(
        "cut", "generated", "off", "on")["audio_context_length"] == 0
    assert contracts.chain_policy(
        "guide", "generated", "off", "on")["audio_context_length"] == 22
    expert_transition = contracts.transition_policy(
        "guide", expert_override=True,
        continuation_mode="feathered_av", context_length=39)
    composed = contracts.compose_chain_policy(
        contracts.audio_policy("generated", "off", "on"),
        expert_transition, audio_context_length=0)
    assert composed["transition_policy"] == expert_transition
    assert composed["audio_context_length"] == 0
    try:
        contracts.chain_policy(
            "detail_av", "generated", "off", "on")
    except ValueError as exc:
        assert "incoming transition" in str(exc)
    else:
        raise AssertionError("advanced transition leaked into compact policy")
    try:
        contracts.compose_chain_policy(
            compact["audio_policy"], compact["transition_policy"],
            audio_context_length=241)
    except ValueError as exc:
        assert "between 0 and 240" in str(exc)
    else:
        raise AssertionError("invalid combined audio context was accepted")

    expected_presets = {
        "cut": ("guide", 0),
        "guide": ("guide", 22),
        "tone_guide": ("tone_carry_guide", 22),
        "latent_guide": ("latent_guide", 22),
        "detail_guide": ("tapered_guide", 22),
        "detail_av": ("tapered_av", 39),
        "drift_av": ("drift_control_av", 39),
        "color_drift_av": ("color_stable_drift_av", 39),
        "hard_av": ("masked_av", 39),
        "soft_av": ("audio_feathered_av", 39),
        "audio_feather_av": ("audio_feathered_av", 39),
    }
    for name, (mode, context) in expected_presets.items():
        resolved = contracts.transition_preset(name)
        assert resolved["preset"] == name
        assert resolved["continuation_mode"] == mode
        assert resolved["context_length"] == context
        resolved["context_length"] = 999
        assert contracts.transition_preset(name)["context_length"] == context
        policy = contracts.transition_policy(name)
        assert policy["continuation_mode"] == mode
        assert policy["context_length"] == context
        assert policy["expert_override"] is False
    expert = contracts.transition_policy(
        "guide", expert_override=True,
        continuation_mode="feathered_av", context_length=39)
    assert expert["preset"] == "guide"
    assert expert["continuation_mode"] == "feathered_av"
    assert expert["context_length"] == 39
    assert expert["expert_override"] is True
    migrated_expert = contracts.transition_policy(
        "soft_av", expert_override=True,
        continuation_mode="feathered_av_rgb", context_length=39)
    assert migrated_expert["continuation_mode"] == "feathered_av"
    assert migrated_expert["context_length"] == 39
    video_only_five = contracts.transition_policy(
        "soft_av", expert_override=True,
        continuation_mode="audio_feathered_av", context_length=5)
    assert video_only_five["context_length"] == 5
    source_audio_policy = contracts.audio_policy("source", "on", "off")
    video_only_policy = contracts.compose_chain_policy(
        source_audio_policy, video_only_five, audio_context_length=5)
    assert video_only_policy["transition_policy"]["context_length"] == 5
    try:
        contracts.compose_chain_policy(
            contracts.audio_policy("generated", "off", "on"),
            video_only_five, audio_context_length=5)
    except ValueError as exc:
        assert "exact shared" in str(exc)
    else:
        raise AssertionError(
            "five-frame AV accepted generated predecessor audio carry")
    try:
        contracts.transition_policy(
            "guide", expert_override=True,
            continuation_mode="masked_av", context_length=1)
    except ValueError as exc:
        assert "at least 5" in str(exc)
    else:
        raise AssertionError("one-frame AV transition was accepted")
    try:
        contracts.transition_policy(
            "guide", expert_override=True,
            continuation_mode="audio_feathered_av", context_length=1)
    except ValueError as exc:
        assert "at least 5" in str(exc)
    else:
        raise AssertionError("one-frame audio-feather AV transition was accepted")
    try:
        contracts.transition_policy(
            "detail_av", expert_override=True,
            continuation_mode="tapered_av", context_length=90)
    except ValueError as exc:
        assert "exactly 39" in str(exc)
    else:
        raise AssertionError("90-frame Detail AV transition was accepted")
    try:
        contracts.transition_policy(
            "drift_av", expert_override=True,
            continuation_mode="drift_control_av", context_length=90)
    except ValueError as exc:
        assert "exactly 39" in str(exc)
    else:
        raise AssertionError("90-frame Drift-Control AV transition was accepted")
    try:
        contracts.transition_policy(
            "color_drift_av", expert_override=True,
            continuation_mode="color_stable_drift_av", context_length=90)
    except ValueError as exc:
        assert "exactly 39" in str(exc)
    else:
        raise AssertionError(
            "90-frame Color-Stable Drift AV transition was accepted")
    try:
        contracts.transition_policy(
            "guide", expert_override=True,
            continuation_mode="latent_guide", context_length=1)
    except ValueError as exc:
        assert "at least 5" in str(exc)
    else:
        raise AssertionError("one-frame Latent Guide transition was accepted")
    shape = contracts.source_timeline_shape()
    assert shape["version"] == contracts.SOURCE_TIMELINE_VERSION
    assert set(shape) == {
        "version", "video", "audio", "origin", "fingerprints", "recovery"
    }
    assert set(contracts.DEPENDENCY_SCOPES) == {
        "global_generation", "scene_generation", "incoming_boundary",
        "assembly_only",
    }

    source = (ROOT / "chain_nodes.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assert plan_required_input_order(module) == FIXTURE[
        "plan_required_input_order"]
    loop_optional = method_input_order(
        module, "MiniMaxH3ChainLoopStart", "optional")
    assert loop_optional[:4] == FIXTURE["loop_start_optional_input_order"]
    assert loop_optional[4:] == [
        "source_timeline", "tagged_references", "reference_schedule"]
    appended_outputs = {
        "MiniMaxH3ChainCurrent": ["video_blend_frames"],
        "MiniMaxH3ChainContext": ["model"],
    }
    for node_name, output_names in FIXTURE["positional_outputs"].items():
        observed = return_names(module, node_name)
        assert observed[:len(output_names)] == output_names, node_name
        assert observed[len(output_names):] == appended_outputs.get(
            node_name, []), node_name
    for format_name in FIXTURE["checkpoint_formats"]:
        assert format_name in source, format_name

    print("v0.5 contracts and v0.4 compatibility fixture passed")


if __name__ == "__main__":
    main()
