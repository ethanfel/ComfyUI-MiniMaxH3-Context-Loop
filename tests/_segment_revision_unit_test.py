#!/usr/bin/env python3
"""Standalone regression test for non-destructive scene regeneration."""

import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import wave

import torch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_segment_revision_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: folder_paths.output_directory
folder_paths.get_temp_directory = lambda: folder_paths.output_directory
folder_paths.get_input_directory = lambda: folder_paths.output_directory
folder_paths.get_annotated_filepath = lambda value: str(value)
folder_paths.output_directory = str(ROOT)
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


class FakeImages:
    shape = (5, 1, 1, 3)

    def __getitem__(self, _key):
        return self


class FakeBlendImages(FakeImages):
    shape = (7, 1, 1, 3)


def main():
    with tempfile.TemporaryDirectory() as tempdir:
        folder_paths.output_directory = tempdir
        chain._compact_latent = lambda _latent: {"samples": [b"video", b"audio"]}
        write_run_archives = chain._write_run_archives
        chain._write_run_archives = lambda *_args, **_kwargs: {}
        chain._archive_media_metadata = lambda _archives: {}

        saved_tensors = []

        def save_checkpoint(_tensors, path, metadata=None):
            saved_tensors.append(dict(_tensors))
            pathlib.Path(path).write_bytes(
                json.dumps(metadata or {}, sort_keys=True).encode("utf-8"))

        def save_video(_images, path, *_args, **_kwargs):
            pathlib.Path(path).write_bytes(
                ("video:" + pathlib.Path(path).name).encode("utf-8"))

        chain._st_save = save_checkpoint
        chain._write_segment_video = save_video

        plan = {
            "run_name": "revision_test",
            "plan_hash": "plan",
            "prompt_prefix": "",
            "segment_crf": 18,
            "source_timeline": {
                "kind": "source_timeline",
                "recovery": {"audio_path": "source.wav"},
            },
            "compatibility": {
                "audio_mode": "source_track",
                "width": 64,
                "height": 64,
                "context_length": 2,
                "audio_context_length": 2,
                "continuation_mode": "masked_av",
                "video_blend_frames": 2,
                "width": 960,
                "height": 544,
            },
            "shots": [{
                "id": "scene_one",
                "prompt": "first take",
                "scene_prompt": "first take",
                "prompt_hash": "prompt-hash",
                "seed": 1,
                "steps": 5,
                "raw_frames": 7,
                "delivered_frames": 5,
                "generation_start_frame": 0,
                "lora_route": "c",
                "prompt_seed_mode": "fixed",
                "prompt_seed": 9001,
                "source_audio_target": "off",
            }],
        }
        state = {"plan": plan, "index": 1}
        archive_one = write_run_archives(
            plan, revision="1" * 32, promote=False)
        assert pathlib.Path(chain._absolute_output_path(
            archive_one["plan"])).is_file()
        canonical_plan = pathlib.Path(tempdir) / "h3_chains" / (
            "revision_test") / "plan.json"
        assert not canonical_plan.exists()
        chain._promote_run_archive_snapshot(plan, archive_one)
        promoted_plan = canonical_plan.read_text(encoding="utf-8")
        changed_plan = dict(plan)
        changed_plan["prompt_prefix"] = "uncommitted edit"
        archive_two = write_run_archives(
            changed_plan, revision="2" * 32, promote=False)
        assert pathlib.Path(chain._absolute_output_path(
            archive_two["plan"])).is_file()
        assert canonical_plan.read_text(encoding="utf-8") == promoted_plan
        generated_audio = {
            "waveform": torch.zeros(
                (1, 2, round(5 / chain.FPS * 8000)), dtype=torch.float32),
            "sample_rate": 8000,
            chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY: torch.zeros(
                (1, 2, round(7 / chain.FPS * 8000)), dtype=torch.float32),
            chain.AUDIO_WITH_OVERLAP_FRAMES_KEY: 7,
            chain.AUDIO_TRIM_FRAMES_KEY: 2,
        }
        saver = chain.MiniMaxH3ChainSegmentSave()
        try:
            saver.save(state, FakeImages(), object(), generated_audio)
        except ValueError as exc:
            assert "images_with_overlap" in str(exc)
        else:
            raise AssertionError("enabled blending accepted no overlap input")
        first_result = saver.save(
            state, FakeImages(), object(), generated_audio,
            FakeBlendImages())
        first = first_result["result"][0]
        assert "audio_with_overlap" in saved_tensors[-1]
        assert saved_tensors[-1]["audio_with_overlap"].shape[-1] == round(
            7 / chain.FPS * 8000)
        first_paths = {
            key: pathlib.Path(chain._absolute_output_path(first[key]))
            for key in ("segment", "checkpoint", "prompt_file",
                        "revision_metadata", "generated_audio",
                        "blend_segment")
        }
        assert all(path.is_file() for path in first_paths.values())
        assert first_result["ui"]["images"] == [
            chain._video_output_item(str(first_paths["segment"]))]
        assert first_result["ui"]["animated"] == (True,)
        assert first["generated_audio_sha256"] == chain._file_sha256(
            str(first_paths["generated_audio"]))
        assert first["scene_dependency"]["version"] == (
            chain.SCENE_DEPENDENCY_VERSION)
        assert set(first["scene_dependency"]["scopes"]) == set(
            chain.DEPENDENCY_SCOPES)
        assert first["branch_id"] == first["revision"]
        assert first["resolved_context_length"] == 0
        assert first["resolved_audio_context_length"] == 0
        assert first["lora_route"] == "c"
        assert first["prompt_seed_mode"] == "fixed"
        assert first["prompt_seed"] == 9001
        assert first["source_audio_target"] == "off"
        assert chain._public_segment(first)["lora_route"] == "c"
        restored_revision = chain._checkpoint_plan_revision(first)
        assert restored_revision["lora_route"] == "c"
        assert restored_revision["prompt_seed_mode"] == "fixed"
        assert restored_revision["prompt_seed"] == "9001"
        assert restored_revision["source_audio_target"] == "off"
        checkpoint_metadata = json.loads(
            first_paths["checkpoint"].read_text(encoding="utf-8"))
        assert checkpoint_metadata["lora_route"] == "c"
        with wave.open(str(first_paths["generated_audio"]), "rb") as audio_file:
            assert audio_file.getnchannels() == 2
            assert audio_file.getframerate() == 8000
            assert audio_file.getnframes() == round(5 / chain.FPS * 8000)

        old_alternate = "a" * 32
        chain._atomic_json(chain._run_editorial_path("revision_test"), {
            "run_name": "revision_test",
            "scene_order": [{"scene": 1, "scene_id": "scene_one"}],
            "alternate_draft": {
                "enabled": True,
                "scene": 1,
                "scene_id": "scene_one",
                "base_revision": first["revision"],
                "prompt": "third take",
                "seed": "3",
            },
            "replacements": [{
                "scene": 1,
                "scene_id": "scene_one",
                "base_revision": first["revision"],
                "alternate_revision": old_alternate,
            }],
        })
        plan["shots"][0].update({
            "prompt": "second take",
            "scene_prompt": "second take",
            "prompt_hash": "replacement-hash",
            "seed": 2,
        })
        second_result = saver.save(
            state, FakeImages(), object(), generated_audio,
            FakeBlendImages())
        second = second_result["result"][0]
        assert second["revision"] != first["revision"]
        assert second["supersedes"] == first["revision_metadata"]
        assert second["branch_id"] == second["revision"]
        assert second["forked_from_branch_id"] == first["branch_id"]
        assert second["generated_audio"] != first["generated_audio"]
        assert second["blend_segment"] != first["blend_segment"]
        assert second["blend_frames"] == 2
        assert all(path.is_file() for path in first_paths.values())
        assert second_result["ui"]["images"] == [chain._video_output_item(
            chain._absolute_output_path(second["segment"]))]
        assert second_result["ui"]["animated"] == (True,)
        reconciled_editorial = chain._load_run_editorial("revision_test")
        assert reconciled_editorial["alternate_draft"] is None
        assert reconciled_editorial["replacements"] == []

        current = json.loads(pathlib.Path(
            chain._absolute_output_path(second["metadata"])
        ).read_text(encoding="utf-8"))
        archived = json.loads(first_paths["revision_metadata"].read_text(
            encoding="utf-8"))
        assert current["segment"]["revision"] == second["revision"]
        assert current["segment"]["prompt"] == "second take"
        assert current["scene_dependency"] == second["scene_dependency"]
        assert current["source_timeline"] == plan["source_timeline"]
        assert archived["segment"]["revision"] == first["revision"]
        assert archived["segment"]["prompt"] == "first take"

        # A direct alternate take starts a branch. Later scenes regenerated
        # because their parent changed inherit that branch instead of creating
        # a new branch at every scene.
        for index in (2, 3):
            plan["shots"].append({
                "id": "scene_%d" % index,
                "prompt": "scene %d" % index,
                "scene_prompt": "scene %d" % index,
                "prompt_hash": "hash-%d" % index,
                "seed": index,
                "steps": 5,
                "raw_frames": 7,
                "delivered_frames": 5,
                "generation_start_frame": (index - 1) * 5,
            })
        scene_two_old = saver.save(
            {"plan": plan, "index": 2, "segments": [second]},
            FakeImages(), object(), generated_audio, FakeBlendImages(),
        )["result"][0]
        scene_three_old = saver.save(
            {"plan": plan, "index": 3,
             "segments": [second, scene_two_old]},
            FakeImages(), object(), generated_audio, FakeBlendImages(),
        )["result"][0]
        assert scene_two_old["branch_id"] == second["branch_id"]
        assert scene_three_old["branch_id"] == second["branch_id"]

        plan["shots"][1]["seed"] = 22
        scene_two_new = saver.save(
            {"plan": plan, "index": 2, "segments": [second]},
            FakeImages(), object(), generated_audio, FakeBlendImages(),
        )["result"][0]
        assert scene_two_new["branch_id"] == scene_two_new["revision"]
        assert scene_two_new["forked_from_branch_id"] == second["branch_id"]

        plan["shots"][2]["seed"] = 33
        scene_three_new = saver.save(
            {"plan": plan, "index": 3,
             "segments": [second, scene_two_new]},
            FakeImages(), object(), generated_audio, FakeBlendImages(),
        )["result"][0]
        assert scene_three_new["branch_id"] == scene_two_new["branch_id"]
        assert "forked_from_branch_id" not in scene_three_new

        # AV with no picture prefix returns before consuming any generated
        # audio, even when the inherited audio setting is nonzero.
        plan["compatibility"]["audio_policy"] = chain._contract_audio_policy(
            "generated", "off", "on")
        plan["shots"][2].update({
            "context_length":0, "audio_context_length":39,
            "continuation_mode":"audio_feathered_av",
            "raw_frames":5, "delivered_frames":5})
        independent = saver.save(
            {"plan":plan, "index":3, "segments":[second, scene_two_new]},
            FakeImages(), object(), generated_audio)["result"][0]
        assert independent["audio_context_length"] == 39, "retain the generation recipe"
        assert independent["resolved_context_length"] == 0
        assert independent["resolved_audio_context_length"] == 0
        assert independent["generated_continuity"] == "on"

        # Named branch generation retains shared immutable files, while its
        # successful commit must never move Original's active pointer.
        before_pointer = pathlib.Path(chain._artifact_paths(plan, 1)["metadata"]).read_bytes()
        store = chain.WorkingBranches(tempdir, plan["run_name"])
        named = store.create("main", "New set", {"plan_json":json.dumps({"shots":plan["shots"]})})
        named_plan = dict(plan, _branch_id=named["id"])
        saved = saver.save({"plan":named_plan, "index":1}, FakeImages(), object(),
                           generated_audio, FakeBlendImages())["result"][0]
        assert pathlib.Path(chain._artifact_paths(plan, 1)["metadata"]).read_bytes() == before_pointer
        named_pointer = pathlib.Path(chain._artifact_paths(named_plan, 1)["metadata"])
        assert json.loads(named_pointer.read_text())["segment"]["revision"] == saved["revision"]
        assert "/branches/" not in saved["revision_metadata"]
        assert pathlib.Path(chain._absolute_output_path(saved["revision_metadata"])).is_file()
        with chain.branch_scope(plan["run_name"], named["id"]):
            active, _ = chain.CheckpointGraphManager(tempdir).active_selection(plan["run_name"])
            assert active == {1:saved["revision"]}

    print("H3 segment revisions: regeneration advances the active pointer and "
          "retains the previous take's video, checkpoint, prompt, and WAV")


if __name__ == "__main__":
    main()
