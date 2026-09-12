#!/usr/bin/env python3
"""Standalone scheduler compiler test without importing a ComfyUI checkout."""

import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_reference_scheduler_unit"

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
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test patch owner"
shared_nodes._prepare_native_guide_conditioning = lambda *args: None
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda *args: None
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


exact_362_audio = {
    "waveform": chain.torch.ones((1, 2, 482667)),
    "sample_rate": 32000,
}
aligned_362_audio, aligned_362_status = (
    chain._align_audio_reference_to_h3_grid(exact_362_audio, 362))
assert int(aligned_362_audio["waveform"].shape[-1]) == 482240
assert "target 603 steps, safe 15.070000s" in aligned_362_status

exact_362_audio_44k = {
    "waveform": chain.torch.ones((1, 2, round(362 / 24 * 44100))),
    "sample_rate": 44100,
}
aligned_362_audio_44k, _ = chain._align_audio_reference_to_h3_grid(
    exact_362_audio_44k, 362)
aligned_44k_samples = int(aligned_362_audio_44k["waveform"].shape[-1])
assert aligned_44k_samples == 664587
assert math.ceil(aligned_44k_samples * 32000 / 44100) == 482240

short_audio = {
    "waveform": chain.torch.ones((1, 2, 480000)),
    "sample_rate": 32000,
}
unchanged_audio, unchanged_status = (
    chain._align_audio_reference_to_h3_grid(short_audio, 362))
assert unchanged_audio is short_audio
assert "unchanged" in unchanged_status


class LazyAudio:
    """Minimal non-dict ComfyUI AUDIO proxy for compatibility testing."""

    def __init__(self, value):
        self.value = value
        self.reads = 0

    def __getitem__(self, key):
        self.reads += 1
        return self.value[key]


class FakeDynamicPrompt:
    def __init__(self, nodes):
        self.nodes = nodes

    def all_node_ids(self):
        return set(self.nodes)

    def get_node(self, node_id):
        return self.nodes[node_id]

def schedule():
    return chain._make_reference_schedule([
        {
            "kind": "picture", "tag": "hero_face", "scenes": "1:7",
            "ranges": ((1, 7),), "value": object(), "content_hash": "face",
            "declaration": "THIS LEGACY TEXT MUST NEVER BE INSERTED",
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
            "audio_declaration": "NOR THIS LEGACY TEXT",
        },
        {
            "kind": "audio", "tag": "song", "scenes": "all",
            "ranges": (), "value": object(), "content_hash": "song",
        },
    ])


workflow = json.loads((
    ROOT / "example_workflows" / "Archive" /
    "Looping Seamless Chain V2 - Scheduled Refs - MiniMax H3.json"
).read_text(encoding="utf-8"))
plan_node = next(node for node in workflow["nodes"]
                 if node.get("type") == "MiniMaxH3ChainPlan")
plan = json.loads(plan_node["widgets_values"][0])

for scene in (1, 4, 8):
    source = "\n".join(plan["shots"][scene - 1]["prompt"])
    compiled, mapping, _bindings = chain._compile_scheduled_reference_prompt(
        schedule(), scene, 14, source)
    assert "@hero" not in compiled
    assert "@performance" not in compiled
    assert "@song" not in compiled
    assert "LEGACY TEXT" not in compiled
    assert "{ref}" not in compiled
    assert compiled.startswith("subject_definitions:\n<Subject 1>")
    if scene == 1:
        assert "<Picture 1>" in compiled and "<Picture 2>" in compiled
        assert "<Audio 1> is the current frame-exact" in compiled
    elif scene == 4:
        assert "<Video 1> provides a weak reference" in compiled
        assert "<Audio 1> is the synchronized soundtrack" in compiled
        assert "<Audio 2> is the current frame-exact" in compiled
    else:
        assert "defined by <Picture 1>" in compiled
        assert "<Picture 2>" not in compiled
        assert "<Audio 1> is the current frame-exact" in compiled
    assert mapping.startswith("scene %d/14:" % scene)

warning_prompt, warning_summary, warning_bindings = (
    chain._compile_scheduled_reference_prompt(
        schedule(), 1, 14,
        "Resolve @hero_face; preserve @missing and @missing.",
        compliance_mode="soft"))
assert warning_prompt == (
    "Resolve <Picture 1>; preserve @missing and @missing.")
assert len(warning_bindings["compliance_warnings"]) == 1
assert "unknown scheduled reference tag @missing" in warning_summary
compliance_options = (
    chain.MiniMaxH3ScheduledReferenceToVideo.INPUT_TYPES()["optional"]
    ["prompt_compliance"])
assert compliance_options[0] == ["strict", "soft", "disabled"]
assert compliance_options[1]["default"] == "strict"
disabled_prompt, disabled_summary, disabled_bindings = (
    chain._compile_scheduled_reference_prompt(
        schedule(), 1, 14,
        "Leave @hero_face and @missing entirely to the user.",
        compliance_mode="disabled"))
assert disabled_prompt == "Leave @hero_face and @missing entirely to the user."
assert disabled_bindings["compliance_warnings"] == []
assert disabled_bindings["compliance_mode"] == "disabled"
assert "@tags passed unchanged" in disabled_summary
assert chain._reference_compliance_mode(True) == "strict"
assert chain._reference_compliance_mode(False) == "soft"
assert chain.MiniMaxH3ScheduledReferenceToVideo.VALIDATE_INPUTS(True) is True
assert chain.MiniMaxH3ScheduledReferenceToVideo.VALIDATE_INPUTS(False) is True
assert "must be strict, soft, or disabled" in (
    chain.MiniMaxH3ScheduledReferenceToVideo.VALIDATE_INPUTS("unknown"))

disabled_graph = FakeDynamicPrompt({
    "audio": {
        "class_type": "MiniMaxH3ScheduledAudioReference", "inputs": {}},
    "wrapper": {
        "class_type": "MiniMaxH3ScheduledReferenceToVideo",
        "inputs": {
            "reference_schedule": ["audio", 0],
            "prompt_compliance": "disabled",
        },
    },
})
skipped_schedule, skipped_fingerprint, skipped_status = (
    chain.MiniMaxH3ScheduledAudioReference().add(
        None, "song", "all", dynprompt=disabled_graph, unique_id="audio"))
assert skipped_schedule["entries"] == []
assert chain._generation_fingerprint_value(skipped_fingerprint)[0] == (
    skipped_schedule["fingerprint"])
assert "skipped because compliance is disabled" in skipped_status

disabled_picture_graph = FakeDynamicPrompt({
    "picture": {
        "class_type": "MiniMaxH3ScheduledPictureReference", "inputs": {}},
    "wrapper": disabled_graph.nodes["wrapper"] | {
        "inputs": {
            "reference_schedule": ["picture", 0],
            "prompt_compliance": "disabled",
        },
    },
})
unchecked_picture = chain.MiniMaxH3ScheduledPictureReference().add(
    chain.torch.zeros((1, 4, 4, 3)), "!!!", "not-a-selector",
    dynprompt=disabled_picture_graph, unique_id="picture")[0]
assert len(unchecked_picture["entries"]) == 1
assert unchecked_picture["entries"][0]["tag"].startswith("reference_")
assert unchecked_picture["entries"][0]["scenes"] == "all"

too_many_audio = chain._make_reference_schedule([
    {
        "kind": "audio", "tag": "audio_%d" % index, "scenes": "all",
        "ranges": (), "value": object(), "content_hash": str(index),
    }
    for index in range(4)
])
capacity_prompt, capacity_summary, capacity_bindings = (
    chain._compile_scheduled_reference_prompt(
        too_many_audio, 1, 1, "User-managed <Audio 1>.",
        compliance_mode="disabled"))
assert capacity_prompt == "User-managed <Audio 1>."
assert len(capacity_bindings["audios"]) == 3
assert "only the first 3 were kept" in capacity_summary

malformed_prompt, malformed_summary, malformed_bindings = (
    chain._compile_scheduled_reference_prompt(
        {"version": -1, "entries": "broken"}, 99, 0,
        "Entirely user-managed prompt.", compliance_mode="disabled"))
assert malformed_prompt == "Entirely user-managed prompt."
assert malformed_bindings["pictures"] == []
assert "Reference schedule ignored" in malformed_summary

soft_graph = FakeDynamicPrompt({
    "audio": disabled_graph.nodes["audio"],
    "wrapper": {
        "class_type": "MiniMaxH3ScheduledReferenceToVideo",
        "inputs": {
            "reference_schedule": ["audio", 0],
            "prompt_compliance": "soft",
        },
    },
})
try:
    chain.MiniMaxH3ScheduledAudioReference().add(
        None, "song", "all", dynprompt=soft_graph, unique_id="audio")
except ValueError as exc:
    assert "received no audio (None)" in str(exc)
else:
    raise AssertionError("soft compliance accepted missing scheduled audio")

picture_inputs = chain.MiniMaxH3ScheduledPictureReference.INPUT_TYPES()[
    "required"]
video_inputs = chain.MiniMaxH3ScheduledVideoReference.INPUT_TYPES()["required"]
audio_inputs = chain.MiniMaxH3ScheduledAudioReference.INPUT_TYPES()["required"]
assert "declaration" not in picture_inputs
assert "declaration" not in video_inputs
assert "audio_declaration" not in video_inputs
assert "declaration" not in audio_inputs

lazy_audio = LazyAudio({
    "waveform": chain.torch.zeros((1, 2, 8000), dtype=chain.torch.float32),
    "sample_rate": 8000,
})
lazy_schedule, lazy_fingerprint, lazy_status = (
    chain.MiniMaxH3ScheduledAudioReference().add(
        lazy_audio, "lazy_voice", "1:2"))
assert lazy_audio.reads > 0
assert lazy_schedule["entries"][0]["value"] is lazy_audio
assert len(lazy_schedule["entries"][0]["content_hash"]) == 64
assert chain._generation_fingerprint_value(lazy_fingerprint)[0] == (
    lazy_schedule["fingerprint"])
assert "@lazy_voice audio on 1:2" in lazy_status
try:
    chain.MiniMaxH3ScheduledAudioReference().add(
        None, "missing_voice", "1")
except ValueError as exc:
    message = str(exc)
    assert "received no audio (None)" in message
    assert "source_audio_slice" in message
    assert "generated_audio" in message
    assert "connect Load Audio directly" in message
    assert "source_plus_timeline" in message
    assert "muted or bypassed" in message
    assert "playable browser preview" in message
else:
    raise AssertionError("missing scheduled audio was accepted")
try:
    chain.MiniMaxH3ScheduledAudioReference().add(
        lambda: b"legacy VHS_AUDIO", "legacy", "1")
except ValueError as exc:
    assert "ComfyUI AUDIO" in str(exc)
else:
    raise AssertionError("legacy callable VHS_AUDIO was accepted")

plan_inputs = chain.MiniMaxH3ChainPlan.INPUT_TYPES()["required"]
plan_optional_inputs = chain.MiniMaxH3ChainPlan.INPUT_TYPES()["optional"]
assert plan_optional_inputs["plan_json_input"][0] == "STRING"
assert plan_optional_inputs["plan_json_input"][1]["forceInput"] is True
assert "non-empty" in plan_optional_inputs[
    "plan_json_input"][1]["tooltip"]
audio_mode_help = plan_inputs["audio_mode"][1]["tooltip"]
assert "does NOT enable or disable @voice/<Audio N> references" in audio_mode_help
assert "finished prerecorded voice" in audio_mode_help
assert "short @voice identity/timbre reference" in audio_mode_help
assert "generated_audio" in audio_mode_help
assert "experimental" in audio_mode_help
assert "output/h3_chains" in plan_inputs["run_name"][1]["tooltip"]
base_seed_help = plan_inputs["base_seed"][1]["tooltip"]
assert "Reroll seed does NOT change base_seed" in base_seed_help
assert "always-visible Scene seed" in base_seed_help
assert "audio_tag" in video_inputs
assert video_inputs["timeline_mode"][0] == [
    "restart_each_scene", "sequential"]
assert "state" in chain.MiniMaxH3ScheduledReferenceToVideo.INPUT_TYPES()[
    "optional"]
apply_arguments = (
    chain.MiniMaxH3ScheduledReferenceToVideo.apply.__code__.co_varnames[
        :chain.MiniMaxH3ScheduledReferenceToVideo.apply.__code__.co_argcount])
assert "state" in apply_arguments and "prompt_compliance" in apply_arguments
assert "timeline_mode" not in chain._reference_entry_contract({
    "kind": "video", "tag": "motion", "scenes": "all",
    "content_hash": "video", "timeline_mode": "restart_each_scene",
})
assert chain._reference_entry_contract({
    "kind": "video", "tag": "motion", "scenes": "all",
    "content_hash": "video", "timeline_mode": "sequential",
})["timeline_mode"] == "sequential"

lazy_restart_descriptor = {
    "version": chain.LAZY_MOTION_SOURCE_VERSION,
    "kind": "lazy_motion_path",
    "path": "/virtual/reference.mp4",
    "frame_count": 73,
}
lazy_restart_calls = []
original_decode_lazy_video = chain._decode_lazy_motion_video
original_decode_lazy_audio = chain._decode_lazy_motion_audio
try:
    chain._decode_lazy_motion_video = (
        lambda descriptor, start, end:
        lazy_restart_calls.append(("video", descriptor, start, end)) or
        "full-reference-video")
    chain._decode_lazy_motion_audio = (
        lambda descriptor, start, end:
        lazy_restart_calls.append(("audio", descriptor, start, end)) or
        "full-reference-audio")
    lazy_restart_video, lazy_restart_audio, lazy_restart_detail = (
        chain._scheduled_video_reference_slice({
            "tag": "walk",
            "value": lazy_restart_descriptor,
            "audio": lazy_restart_descriptor,
            "timeline_mode": "restart_each_scene",
        }, None, 1, 1, 243))
    assert lazy_restart_video == "full-reference-video"
    assert lazy_restart_audio == "full-reference-audio"
    assert [(kind, start, end) for kind, _descriptor, start, end
            in lazy_restart_calls] == [
                ("video", 0, 73), ("audio", 0, 73)]
    assert lazy_restart_detail == "@walk lazy restart frames 0:73"
finally:
    chain._decode_lazy_motion_video = original_decode_lazy_video
    chain._decode_lazy_motion_audio = original_decode_lazy_audio

source_restart_timeline = {"extent": {"frame_count": 91}}
source_restart_calls = []
original_is_source_timeline = chain._is_source_timeline
original_source_timeline_video = chain._source_timeline_scene_video
original_source_timeline_audio = chain._source_timeline_scene_audio
try:
    chain._is_source_timeline = (
        lambda value: value is source_restart_timeline)
    chain._source_timeline_scene_video = (
        lambda timeline, start, end:
        source_restart_calls.append(("video", timeline, start, end)) or
        "full-source-video")
    chain._source_timeline_scene_audio = (
        lambda timeline, start, end:
        source_restart_calls.append(("audio", timeline, start, end)) or
        "full-source-audio")
    source_restart_video, source_restart_audio, source_restart_detail = (
        chain._scheduled_video_reference_slice({
            "tag": "performance",
            "value": source_restart_timeline,
            "audio": source_restart_timeline,
            "timeline_mode": "restart_each_scene",
        }, None, 1, 1, 243))
    assert source_restart_video == "full-source-video"
    assert source_restart_audio == "full-source-audio"
    assert [(kind, start, end) for kind, _timeline, start, end
            in source_restart_calls] == [
                ("video", 0, 91), ("audio", 0, 91)]
    assert source_restart_detail == (
        "@performance Source Timeline restart frames 0:91")
finally:
    chain._is_source_timeline = original_is_source_timeline
    chain._source_timeline_scene_video = original_source_timeline_video
    chain._source_timeline_scene_audio = original_source_timeline_audio

sequential_video = chain.torch.arange(
    700, dtype=chain.torch.float32).reshape(700, 1, 1, 1).expand(-1, 2, 2, 3)
sequential_audio = {
    "waveform": chain.torch.arange(
        7000, dtype=chain.torch.float32).reshape(1, 1, 7000),
    "sample_rate": 240,
}
sequential_schedule = chain.MiniMaxH3ScheduledVideoReference().add(
    sequential_video, "motion", "", "motion_audio", "sequential",
    audio=sequential_audio)[0]
sequential_entry = sequential_schedule["entries"][0]
assert sequential_entry["timeline_mode"] == "sequential"
sequential_state = {
    "index": 2,
    "plan": {
        "shots": [
            {"raw_frames": 243, "generation_start_frame": 0},
            {"raw_frames": 243, "generation_start_frame": 221},
        ],
    },
}
video_slice, audio_slice, slice_detail = (
    chain._scheduled_video_reference_slice(
        sequential_entry, sequential_state, 2, 2, 243))
assert tuple(video_slice.shape) == (243, 2, 2, 3)
assert float(video_slice[0, 0, 0, 0]) == 221
assert float(video_slice[-1, 0, 0, 0]) == 463
assert tuple(audio_slice["waveform"].shape) == (1, 1, 2430)
assert float(audio_slice["waveform"][0, 0, 0]) == 2210
assert slice_detail == "@motion sequential frames 221:464 (origin scene 1)"
try:
    chain._scheduled_video_reference_slice(
        sequential_entry, None, 2, 2, 243)
except ValueError as exc:
    assert "Current Shot state" in str(exc)
else:
    raise AssertionError("sequential reference accepted missing state")

tagged_picture_inputs = chain.MiniMaxH3TaggedPictureReference.INPUT_TYPES()[
    "required"]
tagged_video_inputs = chain.MiniMaxH3TaggedVideoReference.INPUT_TYPES()[
    "required"]
tagged_audio_inputs = chain.MiniMaxH3TaggedAudioReference.INPUT_TYPES()[
    "required"]
assert "scenes" not in tagged_picture_inputs
assert "scenes" not in tagged_video_inputs
assert "scenes" not in tagged_audio_inputs
assert tagged_audio_inputs["timeline_mode"][0] == [
    "standalone", "source_timeline"]
tagged_picture = chain.torch.zeros((1, 4, 4, 3))
tagged_audio = {
    "waveform": chain.torch.zeros((1, 1, 8000)),
    "sample_rate": 8000,
}
tagged = chain.MiniMaxH3TaggedPictureReference().add(
    tagged_picture, "face")[0]
tagged = chain.MiniMaxH3TaggedPictureReference().add(
    tagged_picture, "look", previous=tagged)[0]
tagged = chain.MiniMaxH3TaggedAudioReference().add(
    tagged_audio, "voice", previous=tagged)[0]
assert tagged["activation"] == "prompt"
assert all(entry["activation"] == "prompt" for entry in tagged["entries"])
tagged_compiled, tagged_summary, tagged_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 2, 3,
        "<Subject 1> @S1 uses @look and speaks with @voice; keep @custom."))
assert tagged_compiled == (
    "<Subject 1> @S1 uses <Picture 1> and speaks with <Audio 1>; "
    "keep @custom.")
assert tagged_summary == (
    "scene 2/3: @look -> <Picture 1>; @voice -> <Audio 1>")
assert [entry["tag"] for entry in tagged_bindings["pictures"]] == ["look"]
assert "face" not in tagged_bindings["aliases"]

# Dedicated semantic pictures are collected behind one bundle, centralize
# scale/mode there, and never consume native H3 Picture capacity.
semantic_draft = None
for index in range(10):
    semantic_draft = chain.MiniMaxH3SemanticPictureAnchor().add(
        tagged_picture, "anchor_%d" % index,
        previous=semantic_draft)[0]
native_capacity = None
for index in range(9):
    native_capacity = chain.MiniMaxH3TaggedPictureReference().add(
        tagged_picture, "native_%d" % index,
        previous=native_capacity)[0]
native_passthrough, combined_token, bundle_status = (
    chain.MiniMaxH3SemanticAnchorBundle().bundle(
        semantic_draft, "768", "picture_storyboard",
        references=native_capacity))
semantic_bundle = native_passthrough["semantic_anchors"]
assert native_passthrough is not native_capacity
assert native_passthrough["entries"] is native_capacity["entries"]
assert native_passthrough["semantic_anchors"] is semantic_bundle
assert semantic_bundle["kind"] == "bundle"
assert semantic_bundle["semantic_anchor_size"] == "768"
assert semantic_bundle["semantic_anchor_mode"] == "picture_storyboard"
assert len(semantic_bundle["entries"]) == 10
assert "10 semantic pictures" in bundle_status
assert chain._generation_fingerprint_value(combined_token)[0] == (
    semantic_bundle["combined_reference_fingerprint"])
capacity_prompt = " ".join(
    ["@native_%d" % index for index in range(9)] +
    ["#anchor_%d[0.00s]" % index for index in range(10)])
_capacity_compiled, _capacity_summary, capacity_bindings = (
    chain._compile_tagged_reference_prompt(
        native_passthrough, 1, 1, capacity_prompt,
        semantic_anchor_bundle=native_passthrough["semantic_anchors"]))
assert len(capacity_bindings["pictures"]) == 9
assert len(capacity_bindings["semantic_anchors"]) == 10
assert capacity_bindings["semantic_anchor_mode"] == "picture_storyboard"
native_overflow = chain.MiniMaxH3TaggedPictureReference().add(
    tagged_picture, "native_9", previous=native_capacity)[0]
try:
    chain._compile_tagged_reference_prompt(
        native_overflow, 1, 1,
        " ".join("@native_%d" % index for index in range(10)),
        semantic_anchor_bundle=semantic_bundle)
except ValueError as exc:
    assert "stock H3 Ref2VA supports 9" in str(exc)
else:
    raise AssertionError("ten native pictures bypassed stock H3 capacity")

semantic_compiled, semantic_summary, semantic_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1,
        "Use #face[0.00s], #face[2.50s], and #face[2.50s] while "
        "@look remains native."))
assert semantic_compiled == (
    "Use <Video 1>, <Video 1>, and <Video 1> while <Picture 1> "
    "remains native.")
assert len(semantic_bindings["semantic_anchors"]) == 1
semantic_face = semantic_bindings["semantic_anchors"][0]
assert semantic_face["tag"] == "face"
assert semantic_face["label"] == "<Video 1>"
assert semantic_face["entry"]["value"] is tagged["entries"][0]["value"]
assert semantic_face["timestamps"] == [
    chain.Fraction("0.00"), chain.Fraction("2.50")]
assert "#face[0s,2.5s] -> <Video 1> Qwen-only semantic anchors" in (
    semantic_summary)

mixed_semantic, mixed_summary, mixed_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1,
        "Use #face untimed and #face[2.50s] later while @look is native."))
assert mixed_semantic == (
    "Use <Picture 2> untimed and <Video 1> later while <Picture 1> is native.")
assert [(item["label"], item["untimed"], item["timestamps"])
        for item in mixed_bindings["semantic_anchors"]] == [
    ("<Picture 2>", True, []),
    ("<Video 1>", False, [chain.Fraction("2.50")]),
]
assert "#face -> <Picture 2> Qwen-only semantic picture" in mixed_summary
assert "#face[2.5s] -> <Video 1> Qwen-only semantic anchors" in mixed_summary
hybrid_mixed, hybrid_mixed_anchors = chain._compile_refmod_hybrid_prompt(
    "Use #face untimed and #face[2.50s] later while @look is native.",
    mixed_bindings, mixed_bindings["semantic_anchors"],
    "timestamped_video")
assert hybrid_mixed == (
    "Use <Picture 1> untimed and <Video 1> later while look is native.")
assert [item["hybrid_label"] for item in hybrid_mixed_anchors] == [
    "<Picture 1>", "<Video 1>"]

storyboard_compiled, storyboard_summary, storyboard_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1,
        "Use #face[0.00s] then #face[2.50s] while @look remains native.",
        semantic_anchor_mode="picture_storyboard"))
assert storyboard_compiled == (
    "For the target video, around 0 seconds into this scene, <Picture 2> "
    "is an approximate visual storyboard reference.\n"
    "For the target video, around 2.5 seconds into this scene, <Picture 2> "
    "is an approximate visual storyboard reference.\n\n"
    "Use <Picture 2> then <Picture 2> while <Picture 1> remains native.")
assert storyboard_bindings["semantic_anchor_mode"] == "picture_storyboard"
assert storyboard_bindings["semantic_anchors"][0]["label"] == "<Picture 2>"
assert (
    "#face[0s,2.5s] -> <Picture 2> Qwen-only approximate storyboard picture"
    in storyboard_summary)
chronological_storyboard, _summary, _bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1,
        "Use #face[2.50s] after #look[0.25s].",
        semantic_anchor_mode="picture_storyboard"))
assert chronological_storyboard.startswith(
    "For the target video, around 0.25 seconds into this scene, <Picture 2> "
    "is an approximate visual storyboard reference.\n"
    "For the target video, around 2.5 seconds into this scene, <Picture 1> "
    "is an approximate visual storyboard reference.\n\n")
assert chain._semantic_anchor_specs(
    "@look #face and #face[0.00s] and #face[2.50]") == [
        {"tag": "face", "timestamp_seconds": None},
        {"tag": "face", "timestamp_seconds": 0.0},
        {"tag": "face", "timestamp_seconds": 2.5},
    ]
assert chain._prompt_reference_tags(
    "@look #face") == {"look", "face"}

try:
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1, "Use #missing[1.00s].")
except ValueError as exc:
    assert "unknown semantic anchor #missing" in str(exc)
else:
    raise AssertionError("unknown semantic anchor was accepted in strict mode")

soft_semantic, soft_semantic_summary, soft_semantic_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1, "Keep #missing[1.00s].", compliance_mode="soft"))
assert soft_semantic == "Keep #missing[1.00s]."
assert soft_semantic_bindings["semantic_anchors"] == []
assert "unknown semantic anchor #missing" in soft_semantic_summary

disabled_semantic, disabled_semantic_summary, disabled_semantic_bindings = (
    chain._compile_tagged_reference_prompt(
        tagged, 1, 1, "Keep #face[1.00s].", compliance_mode="disabled"))
assert disabled_semantic == "Keep #face[1.00s]."
assert disabled_semantic_bindings["semantic_anchors"] == []
assert "@tags and #anchors passed unchanged" in disabled_semantic_summary

video_for_semantic = chain.MiniMaxH3TaggedVideoReference().add(
    chain.torch.zeros((5, 4, 4, 3)), "motion", "", "restart_each_scene")[0]
try:
    chain._compile_tagged_reference_prompt(
        video_for_semantic, 1, 1, "Use #motion[0.00s].")
except ValueError as exc:
    assert "must resolve to a Tagged Picture Ref" in str(exc)
else:
    raise AssertionError("semantic anchor accepted a tagged video")


class FakeSemanticClip:
    def __init__(self):
        self.items = None
        self.prompt = None

    def tokenize(self, prompt, minimax_ref_items=None):
        self.prompt = prompt
        self.items = minimax_ref_items
        return {"tokens": "semantic"}

    def encode_from_tokens_scheduled(self, tokens):
        assert tokens == {"tokens": "semantic"}
        return [["semantic-conditioning", {
            "minimax_token_tags": "semantic-tags",
            "pooled_output": "semantic-pooled",
        }]]


original_resize = chain._resize
chain._resize = lambda image, width, height, _crop: chain.torch.zeros(
    (int(image.shape[0]), int(height), int(width), 3),
    dtype=image.dtype)
try:
    semantic_clip = FakeSemanticClip()
    base_conditioning = [["native-conditioning", {
        "minimax_refs": ["native-ref-payload"],
        "minimax_token_tags": "native-tags",
    }]]
    anchor_picture = chain.torch.zeros((1, 64, 32, 3))
    native_picture = chain.torch.zeros((1, 32, 64, 3))
    native_video = chain.torch.zeros((5, 32, 64, 3))
    semantic_result, semantic_status = (
        chain._replace_conditioning_presentation(
            base_conditioning, semantic_clip, "compiled prompt", {
                "version": chain.SEMANTIC_PRESENTATION_VERSION,
                "width": 64,
                "height": 32,
                "length": 22,
                "ref_image_size": "match",
                "semantic_anchor_size": "384",
                "pictures": [native_picture],
                "videos": [{
                    "video": native_video,
                    "paired_audio": True,
                }],
                "standalone_audio_count": 1,
                "anchors": [{
                    "tag": "face",
                    "image": anchor_picture,
                    "timestamps": (
                        chain.Fraction("0.00"), chain.Fraction("0.75")),
                }],
            }))
    assert semantic_clip.prompt == "compiled prompt"
    assert [item["type"] for item in semantic_clip.items] == [
        "image", "audio", "video", "audio", "video"]
    semantic_video = semantic_clip.items[-1]
    assert tuple(semantic_video["data"].shape) == (4, 544, 256, 3)
    assert semantic_video["timestamps"] == [0.0, 0.0, 0.75, 0.75]
    assert semantic_result[0][0] == "semantic-conditioning"
    assert semantic_result[0][1]["minimax_refs"] == ["native-ref-payload"]
    assert semantic_result[0][1]["minimax_token_tags"] == "semantic-tags"
    assert semantic_status == "2 semantic checkpoints across 1 tagged pictures"

    untimed_clip = FakeSemanticClip()
    _untimed_result, untimed_status = (
        chain._replace_conditioning_presentation(
            base_conditioning, untimed_clip, "untimed prompt", {
                "version": chain.SEMANTIC_PRESENTATION_VERSION,
                "width": 64,
                "height": 32,
                "length": 22,
                "ref_image_size": "match",
                "semantic_anchor_size": "384",
                "semantic_anchor_mode": "timestamped_video",
                "pictures": [],
                "videos": [],
                "standalone_audio_count": 0,
                "anchors": [{
                    "tag": "face",
                    "image": anchor_picture,
                    "timestamps": (),
                    "untimed": True,
                }],
            }))
    assert [item["type"] for item in untimed_clip.items] == ["image"]
    assert "timestamps" not in untimed_clip.items[0]
    assert untimed_status == (
        "1 untimed semantic picture across 1 tagged pictures")

    storyboard_clip = FakeSemanticClip()
    storyboard_result, storyboard_status = (
        chain._replace_conditioning_presentation(
            base_conditioning, storyboard_clip, "storyboard prompt", {
                "version": chain.SEMANTIC_PRESENTATION_VERSION,
                "width": 64,
                "height": 32,
                "length": 22,
                "ref_image_size": "match",
                "semantic_anchor_size": "1024",
                "semantic_anchor_mode": "picture_storyboard",
                "pictures": [native_picture],
                "videos": [{
                    "video": native_video,
                    "paired_audio": True,
                }],
                "standalone_audio_count": 1,
                "anchors": [{
                    "tag": "face",
                    "image": anchor_picture,
                    "timestamps": (
                        chain.Fraction("0.00"), chain.Fraction("0.75")),
                }],
            }))
    assert [item["type"] for item in storyboard_clip.items] == [
        "image", "audio", "video", "audio", "image"]
    storyboard_picture = storyboard_clip.items[-1]
    assert tuple(storyboard_picture["data"].shape) == (1, 1440, 736, 3)
    assert "timestamps" not in storyboard_picture
    assert storyboard_result[0][1]["minimax_refs"] == [
        "native-ref-payload"]
    assert storyboard_status == (
        "2 approximate storyboard cues across 1 tagged pictures")

    hybrid_clip = FakeSemanticClip()
    hybrid_result, hybrid_status = (
        chain._replace_conditioning_presentation(
            base_conditioning, hybrid_clip, "hybrid prompt", {
                "version": chain.SEMANTIC_PRESENTATION_VERSION,
                "width": 64,
                "height": 32,
                "length": 22,
                "ref_image_size": "match",
                "semantic_anchor_size": "384",
                "semantic_anchor_mode": "timestamped_video",
                "pictures": [],
                "videos": [],
                "standalone_audio_count": 0,
                "anchors": [{
                    "tag": "face",
                    "image": anchor_picture,
                    "timestamps": (chain.Fraction("0.00"),),
                }],
            }))
    assert [item["type"] for item in hybrid_clip.items] == ["video"]
    assert hybrid_clip.items[0]["timestamps"] == [0.0, 0.0]
    assert hybrid_result[0][1]["minimax_refs"] == ["native-ref-payload"]
    assert hybrid_status == "1 semantic checkpoints across 1 tagged pictures"

    high_resolution = chain._h3_semantic_anchor_image(
        anchor_picture, "1280")
    assert tuple(high_resolution.shape) == (1, 1824, 896, 3)

    try:
        chain._semantic_presentation_items({
            "version": chain.SEMANTIC_PRESENTATION_VERSION,
            "width": 64,
            "height": 32,
            "length": 22,
            "ref_image_size": "match",
            "semantic_anchor_size": "384",
            "pictures": [],
            "videos": [],
            "standalone_audio_count": 0,
            "anchors": [{
                "tag": "face",
                "image": anchor_picture,
                "timestamps": (chain.Fraction("1.00"),),
            }],
        })
    except ValueError as exc:
        assert "scene's 0.917s output duration" in str(exc)
    else:
        raise AssertionError("out-of-scene semantic timestamp was accepted")
finally:
    chain._resize = original_resize


class FakeExpansionNode:
    def __init__(self, class_type, name):
        self.class_type = class_type
        self.name = name
        self.inputs = {}

    def set_input(self, name, value):
        self.inputs[name] = value

    def out(self, index):
        return [self.name, int(index)]


class FakeExpansionGraph:
    def __init__(self):
        self.nodes = []

    def node(self, class_type, name):
        node = FakeExpansionNode(class_type, name)
        self.nodes.append(node)
        return node

    def finalize(self):
        return {
            node.name: {
                "class_type": node.class_type,
                "inputs": node.inputs,
            }
            for node in self.nodes
        }


original_graph_builder = chain.GraphBuilder
chain.GraphBuilder = FakeExpansionGraph
try:
    semantic_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 1, 1,
        "Use @look natively and #face[0.00s] semantically.",
        64, 32, 22, "match", semantic_anchor_size="384")
    expanded = semantic_expansion["expand"]
    assert set(expanded) == {"TaggedRef2VA", "SemanticAnchors"}
    assert expanded["TaggedRef2VA"]["inputs"][
        "ref_images.ref_image_0"] is tagged["entries"][1]["value"]
    semantic_inputs = expanded["SemanticAnchors"]["inputs"]
    assert semantic_inputs["positive"] == ["TaggedRef2VA", 0]
    assert semantic_inputs["prompt"] == (
        "Use <Picture 1> natively and <Video 1> semantically.")
    assert semantic_inputs["presentation"]["anchors"][0]["tag"] == "face"
    assert semantic_inputs["presentation"]["semantic_anchor_size"] == "384"
    assert semantic_inputs["presentation"]["semantic_anchor_mode"] == (
        "timestamped_video")
    assert semantic_expansion["result"][0] == ["SemanticAnchors", 0]
    assert semantic_expansion["result"][1] == ["TaggedRef2VA", 1]
    assert semantic_expansion["result"][4] != tagged["fingerprint"]

    refmod_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 1, 1,
        "Use @look through the external RefMod path.",
        64, 32, 22, "match", conditioning_backend="external_refmod")
    refmod_graph = refmod_expansion["expand"]
    assert set(refmod_graph) == {"TaggedRefModBase"}
    assert refmod_graph["TaggedRefModBase"]["class_type"] == (
        "MiniMaxH3ImageToVideo")
    assert refmod_graph["TaggedRefModBase"]["inputs"] == {
        "clip": "clip",
        "vae": "video-vae",
        "prompt": "Use look through the external RefMod path.",
        "width": 64,
        "height": 32,
        "length": 22,
    }
    assert refmod_expansion["result"][0] == ["TaggedRefModBase", 0]
    assert refmod_expansion["result"][1] == ["TaggedRefModBase", 1]
    assert len(refmod_expansion["result"][5]) == 1
    assert chain.torch.equal(
        refmod_expansion["result"][5][0], tagged_picture)
    assert "text-only conditioning" in refmod_expansion["result"][3]
    assert chain._generation_fingerprint_value(
        refmod_expansion["result"][4])[0] != tagged["fingerprint"]

    hybrid_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 1, 1,
        "Use @look through RefMod and #face[0.00s] semantically.",
        64, 32, 22, "match", conditioning_backend="external_refmod",
        semantic_anchor_size="384")
    hybrid_graph = hybrid_expansion["expand"]
    assert set(hybrid_graph) == {"TaggedRefModBase", "SemanticAnchors"}
    hybrid_prompt = (
        "Use look through RefMod and <Video 1> semantically.")
    assert hybrid_graph["TaggedRefModBase"]["inputs"]["prompt"] == (
        hybrid_prompt)
    hybrid_inputs = hybrid_graph["SemanticAnchors"]["inputs"]
    assert hybrid_inputs["positive"] == ["TaggedRefModBase", 0]
    assert hybrid_inputs["prompt"] == hybrid_prompt
    assert hybrid_inputs["presentation"]["pictures"] == []
    assert hybrid_inputs["presentation"]["videos"] == []
    assert hybrid_inputs["presentation"]["standalone_audio_count"] == 0
    assert [item["tag"] for item in hybrid_inputs[
        "presentation"]["anchors"]] == ["face"]
    assert len(hybrid_expansion["result"][5]) == 1
    assert chain.torch.equal(
        hybrid_expansion["result"][5][0], tagged_picture)
    assert hybrid_expansion["result"][2] == hybrid_prompt
    assert "RefMod @look" in hybrid_expansion["result"][3]
    assert "#face -> <Video 1>" in hybrid_expansion["result"][3]
    assert "hybrid Qwen: 1 semantic anchor source(s) only" in (
        hybrid_expansion["result"][3])

    hybrid_storyboard = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 1, 1,
        "Use @look and #face[0.00s].",
        64, 32, 22, "match", conditioning_backend="external_refmod",
        semantic_anchor_mode="picture_storyboard")
    hybrid_storyboard_inputs = hybrid_storyboard["expand"][
        "SemanticAnchors"]["inputs"]
    assert hybrid_storyboard_inputs["prompt"] == (
        "For the target video, around 0 seconds into this scene, <Picture 1> "
        "is an approximate visual storyboard reference.\n\n"
        "Use look and <Picture 1>.")
    assert hybrid_storyboard_inputs["presentation"]["pictures"] == []
    assert hybrid_storyboard_inputs["presentation"]["anchors"][0][
        "tag"] == "face"

    try:
        chain.MiniMaxH3TaggedReferenceToVideo().apply(
            "clip", "video-vae", "audio-vae", tagged, 1, 1,
            "Use @voice through RefMod.", 64, 32, 22, "match",
            conditioning_backend="external_refmod")
    except ValueError as exc:
        assert "visual-only" in str(exc)
        assert "standalone tagged audio" in str(exc)
    else:
        raise AssertionError("external RefMod accepted active tagged audio")

    storyboard_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", tagged, 1, 1,
        "Use @look natively and #face[0.00s] as a storyboard cue.",
        64, 32, 22, "match", semantic_anchor_size="1024",
        semantic_anchor_mode="picture_storyboard")
    storyboard_inputs = storyboard_expansion["expand"][
        "SemanticAnchors"]["inputs"]
    assert storyboard_inputs["presentation"]["semantic_anchor_mode"] == (
        "picture_storyboard")
    assert storyboard_inputs["presentation"]["semantic_anchor_size"] == (
        "1024")
    assert storyboard_inputs["prompt"].startswith(
        "For the target video, around 0 seconds into this scene, "
        "<Picture 2> is an approximate visual storyboard reference.")
    assert storyboard_expansion["result"][4] != semantic_expansion["result"][4]

    dedicated_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", native_passthrough, 1, 1,
        "Use @native_0 and #anchor_0[0.00s].",
        64, 32, 22, "match", semantic_anchor_size="384",
        semantic_anchor_mode="timestamped_video")
    dedicated_inputs = dedicated_expansion["expand"][
        "SemanticAnchors"]["inputs"]
    assert dedicated_inputs["presentation"]["semantic_anchor_size"] == "768"
    assert dedicated_inputs["presentation"]["semantic_anchor_mode"] == (
        "picture_storyboard")
    assert dedicated_expansion["result"][4] == combined_token

    inherited_passthrough = dict(native_passthrough)
    inherited_passthrough["semantic_anchors"] = (
        chain._make_semantic_anchor_bundle(
            semantic_bundle["entries"], "inherit", "inherit"))
    inherited_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", inherited_passthrough, 1, 1,
        "Use @native_0 and #anchor_0[0.00s].",
        64, 32, 22, "match", semantic_anchor_size="384",
        semantic_anchor_mode="timestamped_video")
    inherited_inputs = inherited_expansion["expand"][
        "SemanticAnchors"]["inputs"]
    assert inherited_inputs["presentation"]["semantic_anchor_size"] == "384"
    assert inherited_inputs["presentation"]["semantic_anchor_mode"] == (
        "timestamped_video")
    assert inherited_expansion["result"][4] != combined_token

    partial_inherit = dict(native_passthrough)
    partial_inherit["semantic_anchors"] = (
        chain._make_semantic_anchor_bundle(
            semantic_bundle["entries"], "inherit", "picture_storyboard"))
    partial_expansion = chain.MiniMaxH3TaggedReferenceToVideo().apply(
        "clip", "video-vae", "audio-vae", partial_inherit, 1, 1,
        "Use @native_0 and #anchor_0[0.00s].",
        64, 32, 22, "match", semantic_anchor_size="1024",
        semantic_anchor_mode="timestamped_video")
    partial_inputs = partial_expansion["expand"][
        "SemanticAnchors"]["inputs"]
    assert partial_inputs["presentation"]["semantic_anchor_size"] == "1024"
    assert partial_inputs["presentation"]["semantic_anchor_mode"] == (
        "picture_storyboard")
    assert partial_expansion["result"][4] != inherited_expansion["result"][4]

    modern_state = {
        "index": 1,
        "plan": {
            "version": chain.PLAN_VERSION,
            "shots": [{"id": "scene_01"}],
        },
    }
    modern_options = chain.MiniMaxH3TaggedSceneOptions().options(
        "max", "soft", "768", "picture_storyboard", False,
        "external_refmod", True)[0]
    merged = chain.MiniMaxH3CurrentTaggedReferenceScene().expand(
        modern_state, "clip", "video-vae", "audio-vae", tagged,
        modern_options)
    merged_graph = merged["expand"]
    assert set(merged_graph) == {"CurrentScene", "TaggedRef2VA", "SceneData"}
    assert merged_graph["CurrentScene"]["class_type"] == (
        "MiniMaxH3ChainCurrent")
    assert merged_graph["CurrentScene"]["inputs"] == {
        "state": modern_state,
        "align_audio_reference": True,
    }
    assert "source_audio" not in merged_graph["CurrentScene"]["inputs"]
    merged_tagged = merged_graph["TaggedRef2VA"]["inputs"]
    assert merged_tagged["state"] == ["CurrentScene", 0]
    assert merged_tagged["clip_index"] == ["CurrentScene", 1]
    assert merged_tagged["prompt"] == ["CurrentScene", 4]
    assert merged_tagged["length"] == ["CurrentScene", 6]
    assert merged_tagged["ref_image_size"] == "max"
    assert merged_tagged["reference_policy"] == "soft"
    assert merged_tagged["semantic_anchor_size"] == "768"
    assert merged_tagged["semantic_anchor_mode"] == "picture_storyboard"
    assert merged_tagged["cache_for_upscale"] is False
    assert merged_tagged["conditioning_backend"] == "external_refmod"
    merged_pack = merged_graph["SceneData"]["inputs"]
    assert merged_pack["source_audio_slice"] == ["CurrentScene", 12]
    assert merged_pack["refmod_sources"] == ["TaggedRef2VA", 5]
    assert merged["result"] == (
        ["CurrentScene", 0], ["TaggedRef2VA", 0],
        ["TaggedRef2VA", 1], ["SceneData", 0])
finally:
    chain.GraphBuilder = original_graph_builder

timeline_audio = {
    "waveform": chain.torch.arange(
        1000, dtype=chain.torch.float32).reshape(1, 1, 1000),
    "sample_rate": 100,
}
timeline_tagged = chain.MiniMaxH3TaggedAudioReference().add(
    timeline_audio, "song", "source_timeline", False)[0]
timeline_entry = timeline_tagged["entries"][0]
assert timeline_entry["timeline_mode"] == "source_timeline"
assert chain._reference_entry_contract(timeline_entry)[
    "timeline_mode"] == "source_timeline"
timeline_state = {
    "index": 2,
    "plan": {
        "compatibility": {
            "audio_mode": "source_track",
            "source_audio_hash": chain._audio_fingerprint(timeline_audio),
            "source_audio_silent_padding": False,
        },
        "shots": [
            {"raw_frames": 22, "audio_start_seconds": 0.0,
             "audio_duration_seconds": 22 / 24},
            {"raw_frames": 22, "audio_start_seconds": 0.5,
             "audio_duration_seconds": 22 / 24},
        ],
    },
}
timeline_slice, timeline_detail = chain._tagged_audio_reference_value(
    timeline_entry, timeline_state, 2, 2, 22)
assert int(timeline_slice["waveform"].shape[-1]) == 92
assert float(timeline_slice["waveform"][0, 0, 0]) == 50
assert "@song source timeline 0.500..1.417s" in timeline_detail

# The shipped Source Timeline workflow connects one Load Audio output to both
# Source Timeline and Tagged Audio. Loop Start records the timeline route
# fingerprint, while Tagged Audio records the original waveform fingerprint;
# both must validate as the same source without weakening mismatch detection.
deferred_timeline = chain._make_source_timeline(
    source_audio=timeline_audio, embedded_audio="ignore")
timeline_route_state = {
    **timeline_state,
    "source_timeline": deferred_timeline,
    "plan": {
        **timeline_state["plan"],
        "compatibility": {
            **timeline_state["plan"]["compatibility"],
            "source_audio_hash": deferred_timeline[
                "fingerprints"]["audio"],
            "source_timeline_fingerprint": deferred_timeline[
                "fingerprints"]["timeline"],
        },
    },
}
timeline_route_slice, timeline_route_detail = (
    chain._tagged_audio_reference_value(
        timeline_entry, timeline_route_state, 2, 2, 22))
assert chain.torch.equal(
    timeline_route_slice["waveform"], timeline_slice["waveform"])
assert "@song source timeline 0.500..1.417s" in timeline_route_detail
different_route_audio = {
    "waveform": timeline_audio["waveform"] + 1,
    "sample_rate": timeline_audio["sample_rate"],
}
different_route_entry = chain.MiniMaxH3TaggedAudioReference().add(
    different_route_audio, "song", "source_timeline", False)[0]["entries"][0]
try:
    chain._tagged_audio_reference_value(
        different_route_entry, timeline_route_state, 2, 2, 22)
except ValueError as exc:
    assert "different full source track" in str(exc)
else:
    raise AssertionError(
        "source-timeline route accepted different tagged audio content")
different_timeline_state = {
    **timeline_state,
    "plan": {
        **timeline_state["plan"],
        "compatibility": {
            **timeline_state["plan"]["compatibility"],
            "source_audio_hash": "different-track",
        },
    },
}
try:
    chain._tagged_audio_reference_value(
        timeline_entry, different_timeline_state, 2, 2, 22)
except ValueError as exc:
    assert "different full source track" in str(exc)
else:
    raise AssertionError("source-timeline audio accepted a mismatched track")
try:
    chain._tagged_audio_reference_value(timeline_entry, None, 2, 2, 22)
except ValueError as exc:
    assert "fingerprint-to-Plan" in str(exc)
    assert "source_audio_slice" in str(exc)
else:
    raise AssertionError("source-timeline audio accepted missing state")

tagged_sequential = chain.MiniMaxH3TaggedVideoReference().add(
    sequential_video, "motion", "motion_audio", "sequential",
    audio=sequential_audio)[0]
tagged_sequential_entry = tagged_sequential["entries"][0]
tagged_state = {
    "index": 3,
    "plan": {"shots": [
        {"raw_frames": 243, "generation_start_frame": 0,
         "prompt": "Opening without a motion tag."},
        {"raw_frames": 243, "generation_start_frame": 221,
         "prompt": "Begin @motion."},
        {"raw_frames": 243, "generation_start_frame": 442,
         "prompt": "Continue @motion_audio."},
    ]},
}
tagged_video_slice, tagged_audio_slice, tagged_detail = (
    chain._scheduled_video_reference_slice(
        tagged_sequential_entry, tagged_state, 3, 3, 243))
assert float(tagged_video_slice[0, 0, 0, 0]) == 221
assert float(tagged_audio_slice["waveform"][0, 0, 0]) == 2210
assert tagged_detail.endswith("(origin scene 2)")

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
assert "without importing the source identity, wardrobe, setting, lighting, " \
       "or composition" in motion_role_compiled
assert motion_role_bindings["aliases"]["performance"] == "<Subject 3>"
assert "@performance -> <Subject 3> motion from <Video 1>" in \
       motion_role_summary
motion_role_contract = chain._reference_entry_contract(
    tagged_motion_role["entries"][0])
assert motion_role_contract["semantic_role"] == "motion"
assert motion_role_contract["motion_target"] == "<Subject 1> and <Subject 2>"
assert motion_role_contract["motion_short_edge"] == "384"

sequential_motion_role = chain.MiniMaxH3TaggedMotionReference().add(
    sequential_video, "performance", "<Subject 1>",
    "the source performer's pose sequence and action timing", "source",
    "performance_audio", "sequential", audio=sequential_audio)[0]
masked_motion_state = {
    "index": 2,
    "plan": {
        "compatibility": {"continuation_mode": "masked_av"},
        "shots": [
            {"raw_frames": 362, "delivered_frames": 362,
             "generation_start_frame": 0,
             "prompt": "Begin @performance."},
            {"raw_frames": 345, "delivered_frames": 306,
             "generation_start_frame": 323,
             "prompt": "Continue @performance."},
        ],
    },
}
masked_motion_video, masked_motion_audio, masked_motion_detail = (
    chain._scheduled_video_reference_slice(
        sequential_motion_role["entries"][0], masked_motion_state,
        2, 2, 345))
assert tuple(masked_motion_video.shape) == (306, 2, 2, 3)
assert float(masked_motion_video[0, 0, 0, 0]) == 362
assert float(masked_motion_video[-1, 0, 0, 0]) == 667
assert tuple(masked_motion_audio["waveform"].shape) == (1, 1, 3450)
assert float(masked_motion_audio["waveform"][0, 0, 0]) == 3230
assert masked_motion_detail == (
    "@performance sequential delivered video frames 362:668; paired audio "
    "raw frames 323:668 (origin scene 1)")

guide_motion_state = {
    **masked_motion_state,
    "plan": {
        **masked_motion_state["plan"],
        "compatibility": {"continuation_mode": "guide"},
    },
}
guide_motion_video, guide_motion_audio, guide_motion_detail = (
    chain._scheduled_video_reference_slice(
        sequential_motion_role["entries"][0], guide_motion_state,
        2, 2, 345))
assert tuple(guide_motion_video.shape) == (345, 2, 2, 3)
assert float(guide_motion_video[0, 0, 0, 0]) == 323
assert float(guide_motion_audio["waveform"][0, 0, 0]) == 3230
assert guide_motion_detail == (
    "@performance sequential frames 323:668 (origin scene 1)")

large_motion_video = chain.torch.zeros((5, 512, 640, 3))
compact_motion = chain.MiniMaxH3TaggedMotionReference().add(
    large_motion_video, "compact_motion", "<Subject 1>",
    "coarse full-body motion", "384", "", "restart_each_scene")[0]
assert tuple(compact_motion["entries"][0]["value"].shape) == (
    5, 384, 480, 3)

assert "references" in chain.MiniMaxH3TaggedReferenceToVideo.INPUT_TYPES()[
    "required"]
tagged_optional = chain.MiniMaxH3TaggedReferenceToVideo.INPUT_TYPES()["optional"]
assert tagged_optional["semantic_anchor_mode"][0] == [
    "timestamped_video", "picture_storyboard"]
assert list(tagged_optional).index("semantic_anchor_size") < list(
    tagged_optional).index("semantic_anchor_mode")
assert chain.MiniMaxH3TaggedReferenceToVideo.VALIDATE_INPUTS(
    semantic_anchor_mode="unsupported") == (
        "Semantic anchor mode must be timestamped_video or "
        "picture_storyboard; got 'unsupported'.")
assert tagged_optional["semantic_anchor_size"][0] == [
    "384", "512", "768", "1024", "1280", "source"]
assert tagged_optional["conditioning_backend"][0] == [
    "native_ref2va", "external_refmod"]
assert tagged_optional["conditioning_backend"][1]["default"] == (
    "native_ref2va")
assert chain.MiniMaxH3TaggedReferenceToVideo.RETURN_NAMES[-1] == (
    "refmod_sources")
assert chain.MiniMaxH3TaggedReferenceToVideo.RETURN_TYPES[-1] == "H3_REF_LIST"
assert chain.MiniMaxH3TaggedReferenceToVideo.VALIDATE_INPUTS(
    conditioning_backend="unknown") == (
        "Tagged conditioning backend must be native_ref2va or "
        "external_refmod; got 'unknown'.")
assert "reference_schedule" not in (
    chain.MiniMaxH3TaggedReferenceToVideo.INPUT_TYPES()["required"])
merged_schema = chain.MiniMaxH3CurrentTaggedReferenceScene.INPUT_TYPES()
assert list(merged_schema["required"]) == [
    "state", "clip", "vae", "audio_vae", "references"]
assert list(merged_schema["optional"]) == ["options"]
assert "source_audio" not in merged_schema["required"]
assert "source_audio" not in merged_schema["optional"]
assert chain.MiniMaxH3CurrentTaggedReferenceScene.RETURN_NAMES == (
    "state", "positive", "latent", "scene_data")
assert chain.MiniMaxH3CurrentTaggedReferenceScene.RETURN_TYPES[-1] == (
    "H3_SCENE_DATA")
assert chain.MiniMaxH3SceneDataExtract.RETURN_TYPES == ("*",)
assert chain.MiniMaxH3SceneDataExtract.INPUT_TYPES()["required"]["field"][0][
    0] == "RefMod sources"
for modern_class in (
        chain.MiniMaxH3TaggedSceneOptions,
        chain.MiniMaxH3CurrentTaggedScenePack,
        chain.MiniMaxH3CurrentTaggedReferenceScene,
        chain.MiniMaxH3SceneDataExtract):
    for section in ("required", "optional"):
        for input_spec in modern_class.INPUT_TYPES().get(section, {}).values():
            assert str(input_spec[1].get("tooltip") or "").strip()
    assert len(modern_class.OUTPUT_TOOLTIPS) == len(modern_class.RETURN_TYPES)

scene_data_state = {
    "index": 1,
    "plan": {
        "version": chain.PLAN_VERSION,
        "shots": [{"id": "scene_01"}],
    },
}
scene_data = chain.MiniMaxH3CurrentTaggedScenePack().pack(
    scene_data_state, 1, 2, "scene_01", "Scene prompt", 42, 124, 20,
    1344, 768, 0.0, 5.0, None, "ready", "Compiled prompt",
    "@look -> <Picture 1>", "fingerprint", ["visual"],
    chain.MiniMaxH3TaggedSceneOptions().options()[0])[0]
assert scene_data["version"] == chain.SCENE_DATA_VERSION
assert scene_data["refmod_sources"] == ["visual"]
assert scene_data["conditioning_backend"] == "native_ref2va"
assert chain.MiniMaxH3SceneDataExtract().extract(
    scene_data, "RefMod sources") == (["visual"],)
assert chain.MiniMaxH3SceneDataExtract().extract(
    scene_data, "Noise seed") == (42,)
try:
    chain.MiniMaxH3CurrentTaggedReferenceScene().expand(
        {"index": 1, "plan": {"version": 1, "shots": [{}]}},
        "clip", "video-vae", "audio-vae", tagged)
except ValueError as exc:
    assert "legacy 0.4" in str(exc)
else:
    raise AssertionError("merged scene node accepted a legacy Plan contract")
conditioning = object()
priority_result = chain.MiniMaxH3PatchPriority().claim(conditioning)
assert priority_result == (conditioning, "test patch owner")

original_output_root = chain._output_root
original_launch_directory = chain._launch_directory
try:
    with tempfile.TemporaryDirectory() as output_root:
        opened_paths = []
        chain._output_root = lambda: output_root
        chain._launch_directory = lambda path: (
            opened_paths.append(path) or True, None)
        folder_result = chain._open_run_output_directory("Project_Name")
        expected_folder = pathlib.Path(
            output_root, "h3_chains", "Project_Name")
        assert folder_result["opened"] is True
        assert pathlib.Path(folder_result["path"]) == expected_folder
        assert expected_folder.is_dir()
        assert opened_paths == [str(expected_folder)]
        chain._launch_directory = lambda _path: (False, "headless host")
        fallback_result = chain._open_run_output_directory("Project_Name")
        assert fallback_result["opened"] is False
        assert fallback_result["error"] == "headless host"
        try:
            chain._open_run_output_directory("Project Name")
        except ValueError as exc:
            assert "Project_Name" in str(exc)
        else:
            raise AssertionError("aliased run_name was accepted")
        try:
            chain._open_run_output_directory("../../")
        except ValueError as exc:
            assert "run_name" in str(exc)
        else:
            raise AssertionError("unsafe empty run_name was accepted")
finally:
    chain._output_root = original_output_root
    chain._launch_directory = original_launch_directory

i2va_workflow = json.loads((
    ROOT / "example_workflows" / "Archive" /
    "Looping Single Image I2VA 20s V2 - MiniMax H3.json"
).read_text(encoding="utf-8"))
i2va_plan_node = next(node for node in i2va_workflow["nodes"]
                       if node.get("type") == "MiniMaxH3ChainPlan")
context_choices = chain.MiniMaxH3ChainPlan.INPUT_TYPES()["required"][
    "context_length"][0]
assert context_choices == [
    1, 5, 22, 39, 56, 73, 90, 107, 124,
    141, 158, 175, 192, 209, 226, 243,
]
normalized = chain.MiniMaxH3ChainPlan().build(
    *i2va_plan_node["widgets_values"])[0]
assert [shot["raw_frames"] for shot in normalized["shots"]] == [243, 243]
assert [shot["delivered_frames"] for shot in normalized["shots"]] == [243, 238]
assert normalized["total_delivered_frames"] == 481
assert normalized["total_delivered_frames"] / chain.FPS > 20
assert normalized["compatibility"]["context_length"] == 5
assert "<Picture 1>" in normalized["shots"][0]["scene_prompt"]
assert "<Picture" not in normalized["shots"][1]["scene_prompt"]

external_plan_json = json.dumps({
    "prompt_prefix": "Externally directed continuity.",
    "shots": [{
        "id": "external_scene",
        "prompt": "This scene came from the connected STRING input.",
        "length": 124,
        "seed": 987654321,
    }],
})
external_normalized = chain.MiniMaxH3ChainPlan().build(
    *i2va_plan_node["widgets_values"],
    plan_json_input=external_plan_json,
)[0]
assert [shot["id"] for shot in external_normalized["shots"]] == [
    "external_scene"]
assert external_normalized["shots"][0]["seed"] == 987654321
assert external_normalized["shots"][0]["prompt"].startswith(
    "Externally directed continuity.")

empty_external_fallback = chain.MiniMaxH3ChainPlan().build(
    *i2va_plan_node["widgets_values"],
    plan_json_input="  \n\t",
)[0]
assert empty_external_fallback["plan_hash"] == normalized["plan_hash"]
try:
    chain.MiniMaxH3ChainPlan().build(
        *i2va_plan_node["widgets_values"],
        plan_json_input="not valid JSON",
    )
except ValueError as exc:
    assert "Plan JSON is invalid" in str(exc)
else:
    raise AssertionError("invalid external plan JSON bypassed Plan validation")

gate_node = next(node for node in i2va_workflow["nodes"]
                 if node.get("type") == "MiniMaxH3ChainFirstSceneImage")
assert gate_node["inputs"][0]["name"] == "state"
assert gate_node["inputs"][1]["name"] == "image"
opening_image = object()
gate = chain.MiniMaxH3ChainFirstSceneImage()
first_result = gate.select({"index": 1}, opening_image)
later_result = gate.select({"index": 2}, opening_image)
assert first_result[:2] == (opening_image, True)
assert later_result[:2] == (None, False)
last_target = object()
assert gate.select({"index": 1}, opening_image, last_target)[3] is last_target
assert gate.select({"index": 2}, opening_image, last_target)[3] is last_target
assert gate.INPUT_TYPES()["optional"]["last_frame"][0] == "IMAGE"
assert gate.RETURN_NAMES[-1] == "last_frame"

frame_a = object()
frame_b = object()
switch = chain.MiniMaxH3ChainFrameIndexSwitch()
assert switch.select(1, frame_b, frame_2=frame_a)[:2] == (frame_b, 1)
assert switch.select(2, frame_b, frame_2=frame_a)[:2] == (frame_a, 2)
assert switch.select(3, frame_b, frame_2=frame_a)[:2] == (frame_b, 1)
assert switch.INPUT_TYPES()["optional"]["frame_8"][0] == "IMAGE"

links = {link[0]: link for link in i2va_workflow["links"]}
nodes = {node["id"]: node for node in i2va_workflow["nodes"]}
for node in nodes.values():
    for slot, input_spec in enumerate(node.get("inputs", [])):
        link_id = input_spec.get("link")
        if link_id is None:
            continue
        assert link_id in links
        assert links[link_id][3:5] == [node["id"], slot]
    for slot, output_spec in enumerate(node.get("outputs", [])):
        for link_id in output_spec.get("links") or []:
            assert link_id in links
            assert links[link_id][1:3] == [node["id"], slot]
i2v_node = next(node for node in nodes.values()
                 if node.get("type") == "MiniMaxH3ImageToVideo")
assert next(item for item in i2v_node["inputs"]
            if item["name"] == "first_frame")["link"] is not None
assert next(item for item in i2v_node["inputs"]
            if item["name"] == "last_frame")["link"] is None

print("H3 scheduler: aliases, Plan guidance, and looping I2VA workflow pass")
