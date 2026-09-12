#!/usr/bin/env python3
"""Standalone saved-segment discovery checks for Plan Studio."""

import asyncio
import concurrent.futures
import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import threading
import time
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_plan_studio_backend_unit"

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


async def cooperative_test_thread(function, *args, **kwargs):
    # Keep this standalone test independent of the host runner's executor
    # wakeup implementation while preserving the scheduling point needed to
    # verify concurrent request deduplication.
    await asyncio.sleep(0)
    return function(*args, **kwargs)


chain.asyncio.to_thread = cooperative_test_thread


class Request:
    query = {"run_name": "studio"}


class ActiveOnlyRequest:
    query = {"run_name": "studio", "include_graph": "false"}


async def check():
    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        run = pathlib.Path(temporary) / "h3_chains" / "studio"
        segments = run / "segments"
        checkpoints = run / "checkpoints"
        reviews = run / "reviews"
        segments.mkdir(parents=True)
        checkpoints.mkdir(parents=True)
        reviews.mkdir(parents=True)

        try:
            chain._confined_media_path(
                "/etc/hosts", "unsafe Plan Studio media")
        except ValueError as exc:
            assert "configured ComfyUI" in str(exc)
        else:
            raise AssertionError("Plan Studio accepted media outside ComfyUI roots")
        escaped = run / "escaped-media"
        escaped.symlink_to("/etc/hosts")
        try:
            chain._absolute_output_path(str(escaped.relative_to(temporary)))
        except ValueError as exc:
            assert "escapes" in str(exc)
        else:
            raise AssertionError("Output path confinement followed an escaping symlink")

        opened = []
        original_open_run = chain._open_run_output_directory
        chain._open_run_output_directory = lambda name: (
            opened.append(name) or {"ok": True, "opened": True})
        try:
            class FolderRequest:
                def __init__(self, remote):
                    self.remote = remote

                async def json(self):
                    return {"run_name": "studio"}

            denied = await chain._open_run_folder(FolderRequest("192.0.2.8"))
            assert denied.status == 403
            assert opened == []
            allowed = await chain._open_run_folder(FolderRequest("127.0.0.1"))
            assert allowed.status == 200
            assert opened == ["studio"]
            assert chain._request_is_loopback(
                types.SimpleNamespace(remote="::1", transport=None))
            assert not chain._request_is_loopback(types.SimpleNamespace(
                remote=None, transport=None))
        finally:
            chain._open_run_output_directory = original_open_run

        class ImportRequest:
            async def json(self):
                return {
                    "project": "studio", "source": "path",
                    "path": "/etc/hosts",
                }

        arbitrary_import = await chain._project_asset_import(ImportRequest())
        assert arbitrary_import.status == 400
        assert "ComfyUI input directory" in arbitrary_import.text

        video_hash = "abcdef1234567890"
        segment = segments / "clip_0001.revision.mp4"
        generated_audio = run / "generated_audio" / "clip_0001.revision.wav"
        checkpoint = checkpoints / "clip_0001.revision.safetensors"
        segment.write_bytes(b"video")
        generated_audio.parent.mkdir(parents=True)
        generated_audio.write_bytes(b"audio")
        checkpoint.write_bytes(b"checkpoint")
        (checkpoints / "clip_0001.json").write_text(json.dumps({
            "segment": {
                "index": 1,
                "id": "intro",
                "segment": str(segment.relative_to(temporary)),
                "generated_audio": str(generated_audio.relative_to(temporary)),
                "checkpoint": str(checkpoint.relative_to(temporary)),
                "segment_sha256": video_hash,
                "raw_frames": 362,
                "delivered_frames": 340,
            },
        }), encoding="utf-8")

        first = await chain._list_saved_checkpoints(Request())
        first_payload = json.loads(first.text)
        assert first_payload["checkpoints"][0]["ready"] is True
        assert first_payload["checkpoints"][0]["delivered_frames"] == 340
        assert first_payload["checkpoints"][0]["audio"]["filename"] == (
            generated_audio.name)
        assert "preview_video" not in first_payload["checkpoints"][0]
        assert "revisions" in first_payload

        original_graph = chain.CheckpointGraphManager.graph

        def reject_graph_build(*_args):
            raise AssertionError(
                "active-only Plan Studio polling built the graph")

        try:
            chain.CheckpointGraphManager.graph = reject_graph_build
            active_only = json.loads(
                (await chain._list_saved_checkpoints(ActiveOnlyRequest())).text)
        finally:
            chain.CheckpointGraphManager.graph = original_graph
        assert active_only["checkpoints"][0]["ready"] is True
        assert "revisions" not in active_only

        # Empty editorial order entries may be reconciled, but not when any
        # active/retained checkpoint (even broken or under another slot) exists.
        unused_run = pathlib.Path(temporary) / "h3_chains" / "unused_scene"
        unused_checkpoints = unused_run / "checkpoints"
        unused_checkpoints.mkdir(parents=True)
        unused_editorial = {
            "run_name": "unused_scene",
            "scene_order": [{"scene": 2, "scene_id": "old_empty"}],
        }
        chain._atomic_json(str(unused_run / "editorial.json"), unused_editorial)

        def unused_ids():
            return chain._saved_checkpoint_listing(
                "unused_scene", include_graph=False)["editorial_unused_scene_ids"]

        assert unused_ids() == ["old_empty"]
        retained_metadata = {
            "segment": {
                "index": 3, "id": "old_empty",
                "segment": str(segment.relative_to(temporary)),
                "checkpoint": str(checkpoint.relative_to(temporary)),
            },
        }
        retained_path = unused_checkpoints / ("clip_0003.%s.json" % ("a" * 32))
        retained_path.write_text(json.dumps(retained_metadata), encoding="utf-8")
        assert unused_ids() == [], "retained revisions protect scene IDs, not just slot numbers"
        retained_path.unlink()
        orphan = unused_checkpoints / ("clip_0002.%s.safetensors" % ("b" * 32))
        orphan.write_bytes(b"orphaned saved checkpoint")
        assert unused_ids() == [], "orphaned artifacts are not unused placeholders"
        orphan.unlink()
        orphan_video = unused_run / "segments" / "clip_0002.retained.mp4"
        orphan_video.parent.mkdir()
        orphan_video.write_bytes(b"saved video without checkpoint metadata")
        assert unused_ids() == [], "a saved segment without a checkpoint is still protected"
        orphan_video.unlink()
        retained_path.write_text("invalid JSON", encoding="utf-8")
        assert unused_ids() == [], "incomplete inventory must fail closed"
        retained_path.unlink()
        active_path = unused_checkpoints / "clip_0002.json"
        active_path.write_text("[]", encoding="utf-8")
        assert unused_ids() == [], "malformed metadata must not claim scenes are unused"
        active_path.write_text(json.dumps(retained_metadata), encoding="utf-8")
        assert unused_ids() == [], "mismatched active pointers must fail closed"
        active_path.unlink()
        assert unused_ids() == ["old_empty"]
        assert json.loads((unused_run / "editorial.json").read_text()) == unused_editorial

        preview = reviews / (
            "clip_0001.%s.audiohash.review.mp4" % video_hash[:12])
        preview.write_bytes(b"synchronized preview")
        second = await chain._list_saved_checkpoints(Request())
        item = json.loads(second.text)["checkpoints"][0]
        assert item["video"]["filename"] == segment.name
        assert item["audio"]["filename"] == generated_audio.name
        assert item["preview_video"]["filename"] == preview.name

        motion_file = pathlib.Path(temporary) / "motion.mp4"
        motion_file.write_bytes(b"motion source")
        descriptor = {
            "version": chain.LAZY_MOTION_SOURCE_VERSION,
            "kind": "lazy_motion_path",
            "path": str(motion_file),
            "skip_seconds": 2.0,
            "file_sha256": "1" * 64,
            "frame_count": 1000,
            "audio": None,
        }
        references = chain._append_tagged_reference(
            None, kind="video", tag="motion", value=descriptor,
            content_hash="2" * 64, timeline_mode="sequential")
        references = chain._decorate_motion_reference(
            references, "<Subject 1>", "walk cycle", "384")
        plan = {
            "run_name": "studio",
            "shots": [{
                "index": 1, "id": "intro", "delivered_frames": 340,
            }],
        }
        editorial = chain._save_run_editorial_document({
            "run_name": "studio",
            "scene_order": [
                {"scene": 1, "scene_id": "intro"},
                {"scene": 2, "scene_id": "outro"},
            ],
            "chapters": [],
            "placements": [{
                "scene": 2, "scene_id": "outro", "start_frame": 500,
            }],
            "locked_scene_ids": ["intro"],
            "subtitles": {"mode": "off"},
        })
        editorial_loaded, timeline_records, editorial_frames = (
            chain._editorial_timeline_records("studio", [
                {"index": 1, "id": "intro", "delivered_frames": 340},
                {"index": 2, "id": "outro", "delivered_frames": 340},
            ]))
        assert editorial_loaded["placements"] == editorial["placements"]
        assert editorial_loaded["locked_scene_ids"] == ["intro"]
        assert [item["kind"] for item in timeline_records] == [
            "scene", "gap", "scene"]
        assert timeline_records[1]["frame_count"] == 160
        assert editorial_frames == 840

        newer_editorial = chain._save_run_editorial_document({
            "run_name": "studio",
            "base_revision": editorial["revision"],
            "scene_order": [
                {"scene": 1, "scene_id": "intro"},
                {"scene": 2, "scene_id": "outro"},
            ],
            "chapters": [],
            "placements": [{
                "scene": 2, "scene_id": "outro", "start_frame": 501,
            }],
        })
        assert newer_editorial["revision"] != editorial["revision"]
        try:
            chain._save_run_editorial_document({
                "run_name": "studio",
                "base_revision": editorial["revision"],
                "scene_order": [
                    {"scene": 1, "scene_id": "intro"},
                    {"scene": 2, "scene_id": "outro"},
                ],
                "chapters": [],
                "placements": [{
                    "scene": 2, "scene_id": "outro", "start_frame": 502,
                }],
            })
        except chain.EditorialConflictError as exc:
            assert "was not overwritten" in str(exc)
        else:
            raise AssertionError("a stale Plan Studio final-cut write succeeded")
        assert chain._load_run_editorial("studio")["placements"][0][
            "start_frame"] == 501

        # A legacy file without revision/timestamp has a stable read token,
        # and discovery never upgrades or rewrites it on disk.
        legacy = {"run_name": "legacy_editorial", "scene_order": [
            {"scene": 1, "scene_id": "intro"}]}
        legacy_path = pathlib.Path(chain._run_editorial_path("legacy_editorial"))
        chain._atomic_json(str(legacy_path), legacy)
        before_legacy = legacy_path.read_bytes()
        original_datetime = chain.datetime
        try:
            tokens = []
            for second in (0, 1):
                fixed_time = original_datetime(2026, 1, 1, 0, 0, second)
                chain.datetime = types.SimpleNamespace(now=lambda _zone: fixed_time)
                tokens.append(chain._load_run_editorial("legacy_editorial")["revision"])
        finally:
            chain.datetime = original_datetime
        assert tokens[0] == tokens[1], "Legacy revision must not depend on read time"
        assert legacy_path.read_bytes() == before_legacy
        legacy_saved = chain._save_run_editorial_document({
            **legacy, "base_revision": tokens[0], "placements": [
                {"scene": 1, "scene_id": "intro", "start_frame": 24}]})
        assert legacy_saved["revision"] != tokens[0]

        # Concurrent tabs reading the same revision cannot both publish.
        # Exercise the real check/write lock, not an async test stub.
        race = chain._save_run_editorial_document({
            "run_name": "revision_race", "base_revision": "",
            "scene_order": [{"scene": 1, "scene_id": "intro"}]})
        barrier = threading.Barrier(2)

        def save_competing_edit(frame):
            barrier.wait(timeout=10)
            try:
                chain._save_run_editorial_document({
                    **race, "base_revision": race["revision"],
                    "placements": [{"scene": 1, "scene_id": "intro",
                                    "start_frame": frame}]})
                return frame
            except chain.EditorialConflictError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save_competing_edit, (24, 48)))
        winners = [frame for frame in results if frame is not None]
        assert len(winners) == 1
        assert chain._load_run_editorial("revision_race")["placements"][0][
            "start_frame"] == winners[0]

        class StaleEditorialRequest:
            headers = {}

            async def json(self):
                return {**race, "base_revision": race["revision"]}

        rejected = await chain._update_run_editorial(StaleEditorialRequest())
        assert rejected.status == 409
        assert json.loads(rejected.text)["code"] == "h3_editorial_conflict"
        assert chain._load_run_editorial("revision_race")["placements"][0][
            "start_frame"] == winners[0]

        latent_editorial = chain._save_run_editorial_document({
            "run_name": "studio",
            "scene_order": [{"scene": 1, "scene_id": "intro"}],
            "chapters": [],
            "placements": [],
            "trims": [{
                "scene": 1, "scene_id": "intro", "out_frame": 72,
            }],
        })
        assert latent_editorial["trims"][0]["out_frame"] == 72
        _, trimmed_records, trimmed_frames = (
            chain._editorial_timeline_records("studio", [{
                "index": 1, "id": "intro", "raw_frames": 362,
                "delivered_frames": 340,
            }]))
        assert trimmed_records[0]["frame_count"] == 72
        assert trimmed_records[0]["source_frame_count"] == 340
        assert trimmed_frames == 72
        transient = chain._editorial_trimmed_segment({
            "index": 1, "id": "intro", "raw_frames": 362,
            "delivered_frames": 340,
        }, latent_editorial)
        assert transient["_editorial_raw_out_frames"] == 94
        assert transient["_editorial_video_steps"] == 28
        chain._streams_from_latent = lambda latent: latent["samples"]
        trimmed_latent = chain._editorial_trim_latent({"samples": [
            chain.torch.zeros(1, 4, 107, 2, 2),
            chain.torch.zeros(1, 4, 2, 603),
        ]}, transient)
        assert trimmed_latent["samples"][0].shape[2] == 28
        assert trimmed_latent["samples"][1].shape[-1] == 157
        saved_tail = chain.torch.arange(100).reshape(100, 1, 1, 1)
        assert chain._editorial_context_tail(
            saved_tail, transient, 100).shape[0] == 0
        source_view = dict(transient)
        current = {
            "index": 2, "resolved_context_length": 22,
            "resolved_audio_context_length": 0,
            "delivered_frames": 340,
        }
        assert chain._editorial_dependency_mismatches(
            current, 2, {1: source_view, 2: current},
        ) == ["previous scene endpoint changed from 340f to 72f"]
        downstream = {
            "index": 3, "resolved_context_length": 22,
            "resolved_audio_context_length": 0,
            "predecessor_editorial_out_frames": 340,
            "delivered_frames": 340,
        }
        stale = chain._editorial_stale_dependencies({
            1: source_view, 2: current, 3: downstream,
        })
        assert list(stale) == [2, 3]
        assert stale[3] == ["depends on stale scene 2"]
        current["predecessor_editorial_out_frames"] = 72
        assert not chain._editorial_dependency_mismatches(
            current, 2, {1: source_view, 2: current},
        )
        assembly_records = chain._apply_editorial_timeline_records([{
            "kind": "segment", "scene_index": 1, "path": str(segment),
            "input_frames": 340, "delivered_frames": 340,
            "blend_frames": 0, "skip_frames": 0,
        }], trimmed_records, [{
            "index": 1, "id": "intro", "segment": str(
                segment.relative_to(temporary)),
            "delivered_frames": 340,
        }], {"width": 64, "height": 64})
        assert assembly_records[0]["input_frames"] == 72
        assert assembly_records[0]["delivered_frames"] == 72
        trimmed_audio = chain._audio_with_editorial_timeline({
            "waveform": chain.torch.ones(1, 2, 3400),
            "sample_rate": 240,
        }, trimmed_records, 340, 72, "trimmed editorial unit audio")
        assert trimmed_audio["waveform"].shape[-1] == 720

        lrc = chain._parse_timed_lyrics(
            "[00:01.5]First line\n[00:03.25]Second line")
        assert lrc[0] == {"start": 1.5, "end": 3.25,
                          "text": "First line"}
        srt = chain._parse_timed_lyrics(
            "1\n00:00:02,000 --> 00:00:04,500\nA subtitle\n")
        assert srt == [{"start": 2.0, "end": 4.5,
                        "text": "A subtitle"}]

        sample_rate = 240
        samples_per_frame = sample_rate // chain.FPS
        waveform = chain.torch.cat((
            chain.torch.ones(1, 2, 340 * samples_per_frame),
            chain.torch.full(
                (1, 2, 340 * samples_per_frame), 2.0),
        ), dim=-1)
        spaced = chain._audio_with_editorial_timeline(
            {"waveform": waveform, "sample_rate": sample_rate},
            timeline_records, 680, editorial_frames,
            "editorial unit audio")
        spaced_waveform = spaced["waveform"]
        assert tuple(spaced_waveform.shape) == (
            1, 2, editorial_frames * samples_per_frame)
        assert chain.torch.all(spaced_waveform[
            ..., 340 * samples_per_frame:500 * samples_per_frame] == 0)
        assert chain.torch.all(spaced_waveform[
            ..., 500 * samples_per_frame:] == 2)

        # An absolute placement can move a later generated scene earlier in
        # the edit without changing manifest/generation order. Its owned
        # generated audio follows the same resolved editorial order.
        chain._save_run_editorial_document({
            "run_name": "studio",
            "scene_order": [
                {"scene": 1, "scene_id": "intro"},
                {"scene": 2, "scene_id": "outro"},
            ],
            "chapters": [],
            "placements": [{
                "scene": 2, "scene_id": "outro", "start_frame": 0,
            }],
        })
        _, reordered_records, reordered_frames = (
            chain._editorial_timeline_records("studio", [
                {"index": 1, "id": "intro", "delivered_frames": 340},
                {"index": 2, "id": "outro", "delivered_frames": 340},
            ]))
        assert [item["scene_id"] for item in reordered_records] == [
            "outro", "intro"]
        assert reordered_frames == 680
        reordered_audio_result = chain._audio_with_editorial_timeline(
            {
                "waveform": waveform,
                "sample_rate": sample_rate,
                chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY: waveform[..., :10],
                chain.AUDIO_WITH_OVERLAP_FRAMES_KEY: 1,
                chain.AUDIO_TRIM_FRAMES_KEY: 1,
            },
            reordered_records, 680, reordered_frames,
            "reordered editorial unit audio")
        reordered_audio = reordered_audio_result["waveform"]
        assert chain.torch.all(reordered_audio[
            ..., :340 * samples_per_frame] == 2)
        assert chain.torch.all(reordered_audio[
            ..., 340 * samples_per_frame:] == 1)
        assert (chain.AUDIO_WITH_OVERLAP_WAVEFORM_KEY
                not in reordered_audio_result)
        report = {"scenes": [{
            "index": 1, "id": "intro", "references": [{
                "tag": "motion", "kind": "video",
                "semantic_role": "motion",
                "window": {
                    "mode": "sequential", "start_frame": 100,
                    "end_frame": 462, "frame_count": 362,
                },
            }],
        }]}
        source_payload = chain._register_plan_studio_source_previews(
            plan, report, references, None)
        assert source_payload["token"]
        source_scene = source_payload["scenes"][0]
        assert source_scene["delivered_frames"] == 340
        assert source_scene["references"][0]["compare_offset_frames"] == 22
        record = chain._PLAN_STUDIO_SOURCE_PREVIEWS[
            source_payload["token"]]["records"]["1:0"]
        assert record["video_seek_seconds"] == 2.0 + 100 / 24
        presentation_path = run / "plan_studio_presentation.json"
        saved_presentation = json.loads(
            presentation_path.read_text(encoding="utf-8"))
        assert saved_presentation["public"]["token"] == ""
        assert saved_presentation["records"]["1:0"] == record
        chain._PLAN_STUDIO_SOURCE_PREVIEWS.clear()
        restored_presentation = chain._restore_plan_studio_presentation(
            "studio")
        assert restored_presentation["token"]
        assert restored_presentation["scenes"] == source_payload["scenes"]
        assert chain._PLAN_STUDIO_SOURCE_PREVIEWS[
            restored_presentation["token"]]["records"]["1:0"] == record

        unsafe_presentation = dict(saved_presentation)
        unsafe_presentation["records"] = {
            "1:0": {**record, "video_path": "/etc/hosts"},
        }
        unsafe_presentation["source_audio"] = {
            "audio_path": "/etc/hosts",
        }
        presentation_path.write_text(
            json.dumps(unsafe_presentation), encoding="utf-8")
        chain._PLAN_STUDIO_SOURCE_PREVIEWS.clear()
        rejected_presentation = chain._restore_plan_studio_presentation(
            "studio")
        assert rejected_presentation["token"] == ""

        source_audio_file = pathlib.Path(temporary) / "source_audio.m4a"
        source_audio_file.write_bytes(b"source audio")
        source_audio_record = {
            "audio_path": str(source_audio_file),
            "audio_seek_seconds": 1.25,
            "audio_kind": "external_path",
            "frame_count": 340,
            "duration_seconds": 340 / 24,
            "available_frame_count": 1200,
            "available_duration_seconds": 50.0,
            "media_fingerprint": "3" * 64,
        }
        original_runtime_source_timeline = (
            chain._plan_studio_runtime_source_timeline)
        original_source_audio_media = chain._plan_studio_source_audio_media
        try:
            chain._plan_studio_runtime_source_timeline = (
                lambda *_args, **_kwargs: {
                    "audio": {"kind": "external_path"},
                })
            chain._plan_studio_source_audio_media = (
                lambda *_args, **_kwargs: dict(source_audio_record))
            audio_payload = chain._register_plan_studio_source_previews(
                plan, report, None, None, object())
        finally:
            chain._plan_studio_runtime_source_timeline = (
                original_runtime_source_timeline)
            chain._plan_studio_source_audio_media = original_source_audio_media
        assert audio_payload["token"]
        assert audio_payload["scenes"] == []
        assert audio_payload["source_audio"]["available"] is True
        assert audio_payload["source_audio"]["seek_seconds"] == 1.25
        assert audio_payload["source_audio"]["available_frame_count"] == 1200
        assert chain._PLAN_STUDIO_SOURCE_PREVIEWS[
            audio_payload["token"]]["source_audio"] == source_audio_record

        captured_waveform_record = {}
        original_waveform = chain._ensure_plan_studio_source_waveform
        try:
            async def fake_waveform(record):
                captured_waveform_record.update(record)
                return str(source_audio_file)

            chain._ensure_plan_studio_source_waveform = fake_waveform
            waveform_request = types.SimpleNamespace(query={
                "token": audio_payload["token"],
                "frame_count": "840",
            })
            waveform_response = await chain._plan_studio_source_waveform(
                waveform_request)
        finally:
            chain._ensure_plan_studio_source_waveform = original_waveform
        assert waveform_response.status == 200
        assert captured_waveform_record["frame_count"] == 840
        # Extending editor-only waveform coverage must not mutate the stored
        # presentation identity used to match the generated Plan.
        assert source_audio_record["frame_count"] == 340

        try:
            chain._plan_studio_runtime_source_timeline = (
                lambda *_args, **_kwargs: {"audio": {"kind": "none"}})
            chain._plan_studio_source_audio_media = (
                lambda *_args, **_kwargs: None)
            no_audio_payload = chain._register_plan_studio_source_previews(
                plan, {"scenes": []}, None, None, object())
        finally:
            chain._plan_studio_runtime_source_timeline = (
                original_runtime_source_timeline)
            chain._plan_studio_source_audio_media = original_source_audio_media
        assert no_audio_payload["token"] == ""
        assert no_audio_payload["scenes"] == []
        assert no_audio_payload["source_audio"] == {
            "available": False,
            "timeline_available": True,
            "has_audio": False,
            "kind": "none",
        }
        assert "connected without audio" in no_audio_payload["status"]
        restored_no_audio = chain._restore_plan_studio_presentation("studio")
        assert restored_no_audio["token"] == ""
        assert restored_no_audio["source_audio"]["timeline_available"] is True

        revision = "a" * 32
        revision_metadata = checkpoints / (
            "clip_0001.%s.json" % revision)
        revision_metadata.write_text(json.dumps({
            "run_name": "studio",
            "segment": {
                "index": 1,
                "revision": revision,
                "segment": str(segment.relative_to(temporary)),
            },
        }), encoding="utf-8")
        thumbnail_record = chain._plan_studio_checkpoint_thumbnail_record(
            "studio", 1, revision)
        thumbnail_commands = []
        original_usable = chain._usable_ffmpeg
        original_run = chain._run_ffmpeg
        try:
            chain._usable_ffmpeg = lambda: "/fake/ffmpeg"

            def fake_thumbnail(command, timeout_seconds=None):
                thumbnail_commands.append((command, timeout_seconds))
                pathlib.Path(command[-1]).write_bytes(b"thumbnail jpeg")

            chain._run_ffmpeg = fake_thumbnail
            thumbnail_path = pathlib.Path(
                chain._build_plan_studio_checkpoint_thumbnail(
                    thumbnail_record))
            cached_thumbnail_path = pathlib.Path(
                chain._build_plan_studio_checkpoint_thumbnail(
                    thumbnail_record))
        finally:
            chain._usable_ffmpeg = original_usable
            chain._run_ffmpeg = original_run
        assert thumbnail_path == cached_thumbnail_path
        assert thumbnail_path.read_bytes() == b"thumbnail jpeg"
        assert len(thumbnail_commands) == 1
        thumbnail_command = thumbnail_commands[0][0]
        assert thumbnail_command[thumbnail_command.index("-frames:v") + 1] == "1"
        assert "scale=320:-2" in thumbnail_command[
            thumbnail_command.index("-vf") + 1]

        original_usable = chain._usable_ffmpeg
        original_capture = chain._run_ffmpeg_capture
        try:
            chain._usable_ffmpeg = lambda: "/fake/ffmpeg"
            chain._run_ffmpeg_capture = lambda *_args, **_kwargs: struct.pack(
                "<%df" % 2720, *(
                    [0.1] * 1360 + [0.8] * 1360))
            waveform_path = pathlib.Path(
                chain._build_plan_studio_source_waveform(source_audio_record))
        finally:
            chain._usable_ffmpeg = original_usable
            chain._run_ffmpeg_capture = original_capture
        waveform = json.loads(waveform_path.read_text(encoding="utf-8"))
        assert waveform["points_per_second"] == 12
        assert len(waveform["samples"]) == 170
        assert max(waveform["samples"]) == 1.0

        captured = []
        original_usable = chain._usable_ffmpeg
        original_run = chain._run_ffmpeg
        try:
            chain._usable_ffmpeg = lambda: "/fake/ffmpeg"

            def fake_run(command, timeout_seconds=None):
                captured.append((command, timeout_seconds))
                pathlib.Path(command[-1]).write_bytes(b"preview mp4")

            chain._run_ffmpeg = fake_run
            cached = pathlib.Path(
                chain._build_plan_studio_source_preview(record))
        finally:
            chain._usable_ffmpeg = original_usable
            chain._run_ffmpeg = original_run
        assert cached.read_bytes() == b"preview mp4"
        command = captured[0][0]
        assert "fps=24" in command[command.index("-vf") + 1]
        assert command[command.index("-t") + 1] == "15.083333333"

        deduplicated_record = dict(record)
        deduplicated_record.update({
            "video_seek_seconds": record["video_seek_seconds"] + 1 / 24,
            "start_frame": record["start_frame"] + 1,
            "end_frame": record["end_frame"] + 1,
        })
        captured.clear()
        original_usable = chain._usable_ffmpeg
        original_run = chain._run_ffmpeg
        try:
            chain._usable_ffmpeg = lambda: "/fake/ffmpeg"

            def slow_fake_run(command, timeout_seconds=None):
                captured.append((command, timeout_seconds))
                time.sleep(.05)
                pathlib.Path(command[-1]).write_bytes(b"deduplicated preview")

            chain._run_ffmpeg = slow_fake_run
            paths = await asyncio.gather(*(
                chain._ensure_plan_studio_source_preview(deduplicated_record)
                for _index in range(4)
            ))
        finally:
            chain._usable_ffmpeg = original_usable
            chain._run_ffmpeg = original_run
        assert len(set(paths)) == 1
        assert len(captured) == 1


if __name__ == "__main__":
    asyncio.run(check())
    print("H3 Plan Studio backend: saved segment and synchronized preview discovery pass")
