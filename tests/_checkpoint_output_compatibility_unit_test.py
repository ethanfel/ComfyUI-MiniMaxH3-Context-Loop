#!/usr/bin/env python3
"""Saved-take output compatibility: actionable errors and safe mixed settings."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "output_compatibility_helpers", ROOT / "tests/_checkpoint_local_output_unit_test.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
chain = h.chain


def failure(call):
    try:
        call()
    except ValueError as exc:
        return str(exc)
    raise AssertionError("Expected incompatible output to be rejected")


def check_diagnostics():
    first = {"segment": {"index": 3, "revision": "a" * 32}, "compatibility": {
        "width": 960, "height": 544, "fps": 24,
        "audio_policy": {"final_audio": "generated"}, "future_flag": True,
    }}
    current = {"segment": {"index": 4, "revision": "b" * 32}, "compatibility": {
        "width": 1280, "height": 720, "fps": 30,
        "audio_policy": {"final_audio": "source"}, "future_flag": 1,
        "source_audio_hash": None, "segment_crf": 22,
    }}
    before = copy.deepcopy((first, current))
    message = failure(lambda: chain._require_checkpoint_output_compatibility(
        first, current, chapter_output=False))
    assert "scene 3 revision " + "a" * 32 in message
    assert "scene 4 revision " + "b" * 32 in message
    for line in (
        "width: scene 3 = 960; scene 4 = 1280",
        "height: scene 3 = 544; scene 4 = 720",
        "fps: scene 3 = 24; scene 4 = 30",
        'audio_policy.final_audio: scene 3 = "generated"; scene 4 = "source"',
        "future_flag: scene 3 = true; scene 4 = 1",
        "source_audio_hash: scene 3 = <missing>; scene 4 = null",
    ):
        assert line in message, message
    assert "Selected chapter only" in message
    blocked, allowed = message.split("Allowed generation/encoding differences (not blocking):")
    assert "Blocking output differences:" in blocked
    assert "segment_crf" not in blocked
    assert "segment_crf: scene 3 = <missing>; scene 4 = 22" in allowed
    assert (first, current) == before, "diagnostics modified saved metadata"
    message = failure(lambda: chain._require_checkpoint_output_compatibility(
        first, current, chapter_output=True))
    assert "compatible takes within this chapter" in message

    # Only the explicit allowlist is relaxed. Unknown and identity fields
    # fail closed, including missing versus null and nested policy changes.
    for key in (
        "width", "height", "fps", "audio_mode", "audio_policy",
        "transition_policy", "continuation_mode", "video_blend_frames",
        "source_audio_hash", "source_audio_silent_padding",
        "source_timeline_fingerprint", "external_context_hash",
        "external_context_frames", "future_contract_field",
    ):
        changed = {**first, "segment": current["segment"],
                   "compatibility": {**first["compatibility"], key: "changed"}}
        message = failure(lambda: chain._require_checkpoint_output_compatibility(
            first, changed, chapter_output=False))
        assert key + ":" in message, message
        if key.startswith("external_context_"):
            assert "Imported-video context" in message
        if key not in ("width", "height"):
            assert "differently sized chapters" not in message
    assert chain._checkpoint_compatibility_differences(
        {"policy": {"a": 1, "b": 2}}, {"policy": {"b": 2, "a": 1}}) == []


def check_selection():
    allowed = {
        "generation_fingerprint": "new-catalog",
        "generation_fingerprint_lineage": {"current": "new-catalog"},
        "encode_mode": "image", "crop": "center", "anchor_mode": "tail",
        "context_length": 39, "audio_context_length": 44,
        "context_storage_length": 73, "segment_crf": 22,
    }
    baseline = {
        "width": 960, "height": 544, "fps": 24, "audio_mode": "generated_audio",
        "generation_fingerprint": "old-catalog", "encode_mode": "video",
        "crop": "disabled", "anchor_mode": "head", "context_length": 22,
        "audio_context_length": 22, "segment_crf": 18,
    }
    assert set(allowed) == chain._CHECKPOINT_OUTPUT_IGNORED_KEYS
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        h.h.folder_paths.output_directory = temporary
        run = root / "h3_chains" / "output_compatibility"
        first, _ = h.h.write_revision(
            run, 1, "1" * 32, 11, run_name=run.name, compatibility=baseline)
        second, _ = h.h.write_revision(
            run, 2, "2" * 32, 12, run_name=run.name, predecessor=first,
            compatibility=copy.deepcopy(baseline))
        (run / "plan.json").write_text(json.dumps({
            "run_name": run.name, "shots": [{"id": "scene_1"}, {"id": "scene_2"}],
        }))
        selection = {"run_name": run.name, "output_mode": "workflow_local",
                     "lineage": [{"scene": 1, "revision": "1" * 32},
                                 {"scene": 2, "revision": "2" * 32}]}
        node = chain.MiniMaxH3ChainCheckpointManager()
        before = h.snapshot(root)
        original = node.passthrough(selection)[0]
        assert h.snapshot(root) == before
        # With the old comparison restored, homogeneous manifests must be
        # byte-for-byte equivalent: existing upscale source hashes stay valid.
        def old_contract(value):
            return {key: item for key, item in value.items() if key not in (
                "generation_fingerprint", "generation_fingerprint_lineage")}
        with patch.object(chain, "_checkpoint_output_compatibility", old_contract):
            assert node.passthrough(selection)[0] == original

        second_path = root / second["segment"]["revision_metadata"]
        for key, value in allowed.items():
            changed = copy.deepcopy(second)
            changed["compatibility"][key] = value
            second_path.write_text(json.dumps(changed))
            before = h.snapshot(root)
            with patch.object(chain, "_atomic_json", side_effect=AssertionError("project write")):
                result = node.passthrough(selection)[0]
            assert h.snapshot(root) == before, key
            assert result["compatibility"] == baseline, "output defaults must stay stable"
            for saved, output in zip((first, second), result["segments"]):
                for field in ("context_length", "audio_context_length", "raw_frames",
                              "delivered_frames", "revision", "checkpoint_sha256"):
                    assert output[field] == saved["segment"][field], (key, field)

        # Legacy takes without per-scene context must keep their own fallback
        # when mixed, without rewriting their immutable checkpoint sidecars.
        for item, settings in ((first, baseline), (second, {**baseline, **allowed})):
            item["compatibility"] = settings
            for key in ("context_length", "audio_context_length"):
                item["segment"].pop(key)
            (root / item["segment"]["revision_metadata"]).write_text(json.dumps(item))
        before = h.snapshot(root)
        mixed = node.passthrough(selection)[0]
        assert [s["context_length"] for s in mixed["segments"]] == [22, 39]
        assert [s["audio_context_length"] for s in mixed["segments"]] == [22, 44]
        assert [s["generation_fingerprint"] for s in mixed["segments"]] == [
            "old-catalog", "new-catalog"]
        assert h.snapshot(root) == before
        second["compatibility"] = copy.deepcopy(baseline)
        second_path.write_text(json.dumps(second))
        homogeneous = node.passthrough(selection)[0]
        assert all("context_length" not in s and "audio_context_length" not in s
                   for s in homogeneous["segments"])

        # A preliminary-video lineage mismatch stays blocked, with exact
        # values instead of the old unhelpful general compatibility error.
        second["compatibility"]["external_context_hash"] = "video-sha256"
        second_path.write_text(json.dumps(second))
        before = h.snapshot(root)
        message = failure(lambda: node.passthrough(selection))
        assert 'external_context_hash: scene 1 = <missing>; scene 2 = "video-sha256"' in message
        assert "Imported-video context" in message
        assert h.snapshot(root) == before


def check_upscale_context():
    upscale = importlib.import_module(chain.__package__ + ".upscale_nodes")
    torch = chain.torch
    images = torch.arange(12).reshape(12, 1, 1, 1)
    state = {"index": 8, "end_clip": 9, "segments": [], "source_manifest": {
        "compatibility": {"context_length": 2},
        "segments": [{"index": 8, "raw_frames": 12, "delivered_frames": 10},
                     {"index": 9, "context_length": 5}],
    }}
    node = upscale.MiniMaxH3ChainUpscaleLoopEnd()
    for requested, expected in ((5, 5), (0, 0), (17, 10)):
        state["source_manifest"]["segments"][1]["context_length"] = requested
        result = node._prepare_next_state(state, images, {"index": 8})
        assert result["previous_frames"].shape[0] == expected
        if expected:
            assert torch.equal(result["previous_frames"], images[-expected:])
        assert result["index"] == 9 and state["index"] == 8
        assert state["segments"] == []
    del state["source_manifest"]["segments"][1]["context_length"]
    assert node._prepare_next_state(state, images, {"index": 8})[
        "previous_frames"].shape[0] == 2
    # Stopping a partial upscale range cannot require a later scene.
    state["end_clip"] = 8
    state["source_manifest"]["segments"].pop()
    assert node._prepare_next_state(state, images, {"index": 8})[
        "previous_frames"].shape[0] == 2


def main():
    check_diagnostics()
    check_selection()
    check_upscale_context()
    print("Checkpoint output compatibility: exact field/revision diagnostics, safe generation differences, legacy context, stable manifests and upscale carry pass")


if __name__ == "__main__":
    main()
