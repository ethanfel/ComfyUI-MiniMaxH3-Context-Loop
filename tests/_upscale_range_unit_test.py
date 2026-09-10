#!/usr/bin/env python3
"""Fresh later ranges and durable resume, using tiny CPU-only media fixtures."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch

from _upscale_chain_unit_test import (
    audio_for_frames, av_latent, folder_paths, load_package, torch,
)


def main():
    _, chain, upscale = load_package()
    case = TestCase()
    adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
    saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
    end = upscale.MiniMaxH3ChainUpscaleLoopEnd()
    assert adapter.INPUT_TYPES()["optional"]["start_mode"][1]["default"] == "resume"
    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": f"range_{i}", "prompt": "A quiet room.",
                                    "length": 5, "steps": 2, "seed": str(i)}
                                   for i in range(1, 8)]}),
            "range_test", "range-test", 32, 32, 1,
            "video", "head", "disabled", "generated_audio", 1,
            5 / 24, 2, 7, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        lineage = []
        for i in range(1, 8):
            state = chain._initial_state(plan, i)
            count = int(plan["shots"][i - 1]["delivered_frames"])
            source = chain.MiniMaxH3ChainSegmentSave().save(
                state, torch.zeros(count, 32, 32, 3), av_latent(0.75),
                audio_for_frames(count), denoised_latent=av_latent(0.75))["result"][0]
            lineage.append({"scene": i, "revision": source["revision"]})
        source = chain.MiniMaxH3ChainCheckpointManager().passthrough({
            "run_name": plan["run_name"], "lineage": lineage})[0]
        source_before = copy.deepcopy(source)
        originals = {p: p.read_bytes() for p in Path(temporary).rglob("*") if p.is_file()}

        def adapt(start=5, stop=6, mode="fresh_range", profile="late", source=source):
            return adapter.adapt(source, profile, "pixel", "{}", start, stop,
                                 False, 18, start_mode=mode)

        # Legacy/API callers remain strict resume; a fresh range is explicit.
        with case.assertRaisesRegex(FileNotFoundError, "start_mode=fresh_range"):
            adapter.adapt(source, "late", "pixel", "{}", 5, 6, False, 18)
        with case.assertRaisesRegex(ValueError, "start_mode"):
            adapt(mode="invalid")
        flow, state, selected, status = adapt()
        assert [s["index"] for s in selected["segments"]] == [5, 6]
        assert state["segments"] == [] and state["previous_latent"] is None
        assert "fresh range only" in status
        assert upscale._source_scene_count(selected) == 7
        assert upscale.MiniMaxH3ChainUpscaleCurrent().current(state)[4:6] == (5, 7)
        assert upscale._source_hash(upscale._verified_source_manifest(selected)) == state["source_manifest_hash"]
        offset = sum(s["delivered_frames"] for s in source["segments"][:4])
        assert selected["upscale_range"]["source_start_frame"] == offset
        assert selected["editorial"]["placements"][0]["start_frame"] == 0
        hq = torch.zeros(5, 64, 64, 3)
        saved5 = saver.save(state, hq)["result"][0]
        assert saved5["index"] == 5 and "clip_0005." in saved5["segment"]
        paths = upscale._state_profile_paths(state, 5)
        metadata = chain._read_json(paths["metadata"])
        assert metadata["upscale_range_start"] == 5
        assert not Path(upscale._state_profile_paths(state, 1)["metadata"]).exists()
        partial = chain._read_json(paths["partial"])
        upscale._validate_upscale_manifest(partial)
        assert partial["scene_start"] == 5 and partial["scene_end"] == 5
        assert partial["clip_count"] == 2 and partial["completed_clip_count"] == 1
        converted = upscale._assembly_manifest(partial, partial["segments"])
        assert converted["upscale"]["source_start_frame"] == offset

        # Actual assembly starts with scene 5, with no padding for 1..4.
        output = chain.MiniMaxH3ChainAssemble().assemble(
            partial, "generated", "partial_range", 96)["result"][0]
        streams = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", output]))["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        assert int(video["nb_frames"]) == saved5["delivered_frames"]
        assert Path(output).with_suffix(".generated.wav").is_file()

        # Verify full-track source audio is cut at scene 5, not at frame zero.
        rate = 8000
        total_samples = chain.sample_boundary_from_frames(source["total_delivered_frames"], rate, chain.FPS)
        waveform = torch.linspace(-0.25, 0.25, total_samples).reshape(1, 1, -1).repeat(1, 2, 1)
        with patch.object(chain, "_validate_source_audio_hash"), patch.object(
                chain, "_audio_with_editorial_timeline", wraps=chain._audio_with_editorial_timeline) as align:
            chain.MiniMaxH3ChainAssemble().assemble(
                partial, "source", "source_range", 96,
                source_audio={"waveform": waveform, "sample_rate": rate})
        audio_call = next(c for c in align.call_args_list if c.args[-1] == "H3 source editorial audio")
        lo = chain.sample_boundary_from_frames(offset, rate, chain.FPS)
        hi = chain.sample_boundary_from_frames(offset + saved5["delivered_frames"], rate, chain.FPS)
        assert torch.equal(audio_call.args[0]["waveform"], waveform[..., lo:hi])

        # Bootstrap resume and recursive continuation agree. No manifest file
        # or in-memory state is needed to recover where this pass began.
        Path(paths["partial"]).unlink()
        _, resumed, _, _ = adapt(start=6, mode="resume")
        assert resumed["segments"] == [saved5]
        next_state = end._prepare_next_state(state, hq, saved5, None)
        _, recursive, _, _ = adapter.adapt(selected, "late", "pixel", "{}", 5, 6,
                                          False, 18, initial_state=next_state, start_mode="fresh_range")
        assert recursive["segments"] == resumed["segments"] and recursive["index"] == 6

        # Resume must not adopt incompatible/corrupt saves or another profile.
        for change, message in (({"profile": "other"}, "different run or profile"),
                                ({"profile_config_hash": "wrong"}, "different profile settings"),
                                ({"upscale_range_start": "5"}, "range start")):
            chain._atomic_json(paths["metadata"], {**metadata, **change})
            with case.assertRaisesRegex(ValueError, message):
                adapt(start=6, mode="resume")
        corrupt = copy.deepcopy(metadata)
        corrupt["segment"]["checkpoint_sha256"] = "f" * 64
        chain._atomic_json(paths["metadata"], corrupt)
        with case.assertRaisesRegex(ValueError, "SHA-256"):
            adapt(start=6, mode="resume")
        chain._atomic_json(paths["metadata"], metadata)
        changed = copy.deepcopy(source)
        changed["segments"][4]["revision"] = "f" * 32
        with case.assertRaisesRegex(ValueError, "different source revision"):
            adapt(start=6, mode="resume", source=changed)
        with case.assertRaisesRegex(FileNotFoundError, "metadata is missing"):
            adapt(start=6, mode="resume", profile="another-profile")

        saved6 = saver.save(resumed, hq)["result"][0]
        final = end.end(flow, resumed, hq, saved6)[0]
        upscale._validate_upscale_manifest(final)
        assert final["format"] == "h3_chain_upscale_manifest_v1"
        assert [s["index"] for s in final["segments"]] == [5, 6]
        # Extend a completed two-scene range to the next original scene.
        _, extended, _, _ = adapt(start=7, stop=0, mode="resume")
        assert [s["index"] for s in extended["segments"]] == [5, 6]
        assert upscale._source_bounds(extended["source_manifest"]) == (5, 7)
        # A conflicting range boundary within the prefix is not silently mixed.
        chain._atomic_json(paths["metadata"], {**metadata, "upscale_range_start": 4})
        with case.assertRaisesRegex(ValueError, "different upscale range"):
            adapt(start=7, stop=0, mode="resume")
        chain._atomic_json(paths["metadata"], metadata)
        # A fresh range never reads a pre-existing HQ prefix, even if corrupt.
        with patch.object(upscale, "_load_upscale_prefix", side_effect=AssertionError("must not load prefix")):
            assert adapt(start=6)[1]["segments"] == []

        # Chapter-scoped paths are stable, and both audio origins are rebased.
        chapter = copy.deepcopy(source)
        chapter.update(format=chain.CHAPTER_MANIFEST_FORMAT, source_scene_count=12,
                       chapter={"number": 2, "id": "two", "title": "Chapter 2",
                                "start_scene": 1, "end_scene": 7, "planned_end_scene": 7,
                                "complete": True, "source_start_frame": 120,
                                "editorial_origin_frame": 100})
        _, ch_state, ch_source, _ = adapt(source=chapter, profile="chapter-range")
        assert ch_source["chapter"]["source_start_frame"] == 120 + offset
        assert ch_source["chapter"]["start_scene"] == 5
        assert not ch_source["chapter"]["complete"]
        assert upscale._source_scene_count(ch_source) == 12
        assert "chapters/02_two/upscaled/chapter-range" in upscale._state_profile_paths(ch_state, 5)["root"]
        ch_saved = saver.save(ch_state, hq)["result"][0]
        _, ch_resumed, _, _ = adapt(start=6, source=chapter, profile="chapter-range", mode="resume")
        assert ch_resumed["segments"] == [ch_saved]
        ch_assembly = upscale._assembly_manifest(upscale._upscale_manifest(ch_state, [ch_saved], False), [ch_saved])
        assert ch_assembly["chapter"]["source_start_frame"] == 120 + offset
        assert ch_assembly["scene_start"] == 5

        # Latent fresh starts protect their original prefix if HQ carry is absent.
        latent_state = copy.deepcopy(state)
        latent_state["profile_config"]["backend"] = "h3_latent"
        latent_state["source_manifest"]["segments"][0].update(
            raw_frames=44, delivered_frames=5, continuation_mode="drift_control_av")
        _, steps, note = upscale._drift_continuation_video(torch.zeros(1, 24, 14, 4, 4), latent_state)
        assert steps > 0 and "source prefix protected" in note
        assert source == source_before
        assert all(p.read_bytes() == data for p, data in originals.items())
    print("Fresh upscale range tests passed: numbering, save/resume, assembly/audio, chapter scope, strict integrity.")


if __name__ == "__main__":
    main()
