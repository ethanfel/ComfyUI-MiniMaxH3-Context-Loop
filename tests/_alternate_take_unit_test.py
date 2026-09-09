#!/usr/bin/env python3
"""Prompt-only editorial alternate lineage and presentation checks."""

import asyncio
import importlib.util
import json
import pathlib
import sys
import tempfile
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_alternate_take_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.output_directory = str(ROOT)
folder_paths.get_output_directory = lambda: folder_paths.output_directory
folder_paths.get_temp_directory = lambda: folder_paths.output_directory
folder_paths.get_input_directory = lambda: folder_paths.output_directory
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

server = types.ModuleType("server")
server.PromptServer = type("PromptServer", (), {"instance": None})
sys.modules["server"] = server

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


class JsonRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def write_take(run, scene, scene_id, revision, prompt, *, base=None,
               predecessor=None, active=False):
    checkpoints = run / "checkpoints"
    segments = run / "segments"
    checkpoints.mkdir(parents=True, exist_ok=True)
    segments.mkdir(parents=True, exist_ok=True)
    video = segments / f"clip_{scene:04d}.{revision}.mp4"
    latent = checkpoints / f"clip_{scene:04d}.{revision}.safetensors"
    prompt_file = segments / f"clip_{scene:04d}.{revision}.prompt.txt"
    metadata_path = checkpoints / f"clip_{scene:04d}.{revision}.json"
    video.write_bytes(("video-" + revision).encode())
    latent.write_bytes(("latent-" + revision).encode())
    prompt_file.write_text(prompt, encoding="utf-8")
    segment = {
        "index": scene,
        "id": scene_id,
        "revision": revision,
        "segment": str(video.relative_to(folder_paths.output_directory)),
        "checkpoint": str(latent.relative_to(folder_paths.output_directory)),
        "prompt_file": str(prompt_file.relative_to(
            folder_paths.output_directory)),
        "revision_metadata": str(metadata_path.relative_to(
            folder_paths.output_directory)),
        "raw_frames": 39,
        "delivered_frames": 39,
        "prompt": prompt,
        "scene_prompt": prompt,
        "seed": scene,
        "steps": 8,
        "segment_sha256": chain._file_sha256(str(video)),
        "checkpoint_sha256": chain._file_sha256(str(latent)),
        "prompt_file_sha256": chain._file_sha256(str(prompt_file)),
        "branch_id": predecessor or revision,
        "resolved_context_length": 0,
        "resolved_audio_context_length": 0,
    }
    if predecessor:
        segment["predecessor_revision"] = predecessor
    if base:
        segment.update({
            "take_kind": "editorial_alternate",
            "alternate_of_revision": base,
            "alternate_media_mode": "picture_only",
        })
    metadata = {
        "format": "h3_chain_segment_v3",
        "run_name": "alternate_test",
        "compatibility": {
            "width": 64, "height": 64, "fps": 24,
            "context_length": 0, "audio_context_length": 0,
            "audio_mode": "generated_audio",
        },
        "segment": segment,
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    if active:
        (checkpoints / f"clip_{scene:04d}.json").write_text(
            json.dumps(metadata), encoding="utf-8")
    return segment


async def check():
    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        run = pathlib.Path(temporary) / "h3_chains" / "alternate_test"
        base_one = "1" * 32
        base_two = "2" * 32
        alternate_one = "a" * 32
        first = write_take(
            run, 1, "scene_1", base_one, "the red folder", active=True)
        second = write_take(
            run, 2, "scene_2", base_two, "read the folder",
            predecessor=base_one, active=True)

        plan = chain._normalize_plan(
            json.dumps({"shots": [
                {"id": "scene_1", "prompt": "the red folder", "length": 39},
                {"id": "scene_2", "prompt": "read the folder", "length": 39,
                 "context_length": 0},
            ]}),
            "alternate_test", 64, 64, 22, "video", "head", "disabled",
            "generated_audio", 22, 2.0, 8, 7, 18, "model-stack", 0,
            "guide", None)
        canonical_hash = plan["plan_hash"]
        (run / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        editorial = chain._save_run_editorial_document({
            "run_name": "alternate_test",
            "scene_order": [
                {"scene": 1, "scene_id": "scene_1"},
                {"scene": 2, "scene_id": "scene_2"},
            ],
            "alternate_draft": {
                "enabled": True,
                "scene": 1,
                "scene_id": "scene_1",
                "base_revision": base_one,
                "prompt": "the blue folder",
                "seed": "42",
                "media_mode": "picture_only",
            },
        })
        derived = chain._alternate_take_plan(plan, editorial)
        assert plan["plan_hash"] == canonical_hash
        assert plan["shots"][0]["scene_prompt"] == "the red folder"
        assert derived["shots"][0]["scene_prompt"] == "the blue folder"
        assert derived["shots"][1]["scene_prompt"] == "read the folder"
        assert derived["alternate_take"]["base_revision"] == base_one
        assert chain._alternate_take_descriptor(derived)["scene"] == 1

        latest_queued = dict(editorial["alternate_draft"])
        latest_queued["prompt"] = "the latest blue folder"
        merged = chain._merge_queued_alternate_draft(
            editorial, latest_queued)
        assert merged["alternate_draft"]["prompt"] == \
            "the latest blue folder"
        consumed = dict(editorial)
        consumed["alternate_draft"] = None
        consumed["replacements"] = [{
            "scene": 1,
            "scene_id": "scene_1",
            "base_revision": base_one,
            "alternate_revision": "a" * 32,
            "media_mode": "picture_only",
        }]
        stale = chain._merge_queued_alternate_draft(
            consumed, editorial["alternate_draft"])
        assert stale["alternate_draft"] is None
        assert stale["replacements"] == consumed["replacements"]
        disarmed = chain._merge_queued_alternate_draft(editorial, None)
        assert disarmed["alternate_draft"] is None

        alternate = write_take(
            run, 1, "scene_1", alternate_one, "the blue folder",
            base=base_one)
        selected = chain._save_run_editorial_document({
            "run_name": "alternate_test",
            "scene_order": [
                {"scene": 1, "scene_id": "scene_1"},
                {"scene": 2, "scene_id": "scene_2"},
            ],
            "replacements": [{
                "scene": 1,
                "scene_id": "scene_1",
                "base_revision": base_one,
                "alternate_revision": alternate_one,
                "media_mode": "picture_only",
            }],
        })
        presentation = chain._editorial_presentation_segments(
            "alternate_test", [first, second], selected)
        assert presentation[0]["revision"] == alternate_one
        assert presentation[0]["presentation_base_revision"] == base_one
        assert presentation[1]["revision"] == base_two

        listing = chain._saved_checkpoint_listing(
            "alternate_test", include_graph=False)
        scene_one = listing["checkpoints"][0]
        assert scene_one["revision"] == base_one
        assert scene_one["presentation_revision"] == alternate_one
        assert scene_one["alternates"][0]["used_in_final_cut"] is True

        regenerated_base = "3" * 32
        regenerated = write_take(
            run, 1, "scene_1", regenerated_base, "the green folder",
            active=True)
        stale_presentation = chain._editorial_presentation_segments(
            "alternate_test", [regenerated, second], selected)
        assert stale_presentation[0]["revision"] == regenerated_base
        recovered_editorial, recovered_records, recovered_frames = (
            chain._editorial_timeline_records(
                "alternate_test", [regenerated]))
        assert recovered_editorial["replacements"] == []
        assert recovered_records[0]["segment"]["revision"] == regenerated_base
        assert recovered_frames == regenerated["delivered_frames"]
        stale_listing = chain._saved_checkpoint_listing(
            "alternate_test", include_graph=False)
        stale_scene_one = stale_listing["checkpoints"][0]
        assert "presentation_revision" not in stale_scene_one
        assert stale_scene_one["alternates"][0]["used_in_final_cut"] is False
        assert stale_listing["editorial"]["replacements"] == []
        assert stale_listing["editorial_notices"] == ["scene 1 final-cut alternate"]
        assert chain._load_run_editorial("alternate_test")["replacements"] == selected["replacements"], (
            "Read-only listing must keep the saved ALT choice for its original base")
        stale_graph = chain.CheckpointGraphManager(temporary).graph(
            "alternate_test")
        stale_base_record = next(
            item for item in stale_graph["revisions"]
            if item["revision"] == base_one)
        assert stale_base_record["alternates"][0]["used_in_final_cut"] is False
        stale_alternate_preview = chain.CheckpointGraphManager(
            temporary).deletion_preview(
                "alternate_test", 1, alternate_one)
        assert stale_alternate_preview["allowed"] is True

        reconciled, cleared = chain._editorial_after_base_revision_change(
            selected, 1, "scene_1", regenerated_base)
        assert cleared == ["final-cut alternate"]
        assert reconciled["replacements"] == []
        assert reconciled["alternate_draft"] is None
        valid_again, cleared = chain._editorial_after_base_revision_change(
            selected, 1, "scene_1", base_one)
        assert cleared == []
        assert valid_again["replacements"] == selected["replacements"]

        # Restore the original base for graph/deletion assertions below.
        write_take(run, 1, "scene_1", base_one, "the red folder", active=True)

        graph = chain.CheckpointGraphManager(temporary).graph("alternate_test")
        assert graph["summary"]["revision_count"] == 4
        assert graph["summary"]["branch_count"] == 2
        base_record = next(item for item in graph["revisions"]
                           if item["revision"] == base_one)
        assert base_record["alternates"][0]["revision"] == alternate_one
        assert base_record["alternates"][0]["used_in_final_cut"] is True
        assert all(child["revision"] != alternate_one
                   for child in base_record["children"])
        base_preview = chain.CheckpointGraphManager(temporary).deletion_preview(
            "alternate_test", 1, base_one)
        assert any(item["revision"] == alternate_one
                   for item in base_preview["dependents"])
        preview = chain.CheckpointGraphManager(temporary).deletion_preview(
            "alternate_test", 1, alternate_one)
        assert preview["allowed"] is False
        assert "selected in the final cut" in " ".join(preview["blockers"])

        try:
            chain._checkpoint_selection_manifest({
                "run_name": "alternate_test",
                "lineage": [{"scene": 1, "revision": alternate_one}],
                "scope_start_scene": 1,
                "scope_end_scene": 2,
            })
        except ValueError as exc:
            assert "editorial alternate" in str(exc)
        else:
            raise AssertionError(
                "An editorial alternate was accepted as checkpoint lineage")

        activation = await chain._restore_checkpoint_revisions(JsonRequest({
            "run_name": "alternate_test",
            "resume_scene": 2,
            "scope_start_scene": 1,
            "scope_end_scene": 2,
            "activate_only": True,
            "revisions": [{"scene": 1, "revision": alternate_one}],
        }))
        assert activation.status == 400
        assert "cannot become an active generation checkpoint" in json.loads(
            activation.text)["error"]


if __name__ == "__main__":
    asyncio.run(check())
    print("alternate take checks passed")
