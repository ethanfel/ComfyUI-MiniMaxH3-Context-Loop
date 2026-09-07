#!/usr/bin/env python3
"""CPU integration test for deferred checkpoint upscale child runs."""

import importlib.util
import json
import pathlib
import os
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
candidates = [pathlib.Path(os.environ["COMFYUI_PATH"])] if os.environ.get("COMFYUI_PATH") else []
candidates += [ROOT.parent / "Comfyui", ROOT.parent / "ComfyUI"]
COMFY = next(path for path in candidates
             if (path / "comfy" / "options.py").is_file())
sys.path.insert(0, str(COMFY))
sys.argv = ["h3-upscale-test", "--cpu"]

import comfy.options  # noqa: E402

comfy.options.enable_args_parsing()
import folder_paths  # noqa: E402
import torch  # noqa: E402
from safetensors import safe_open  # noqa: E402


def load_package():
    spec = importlib.util.spec_from_file_location(
        "h3_upscale_test_package", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    return (package, sys.modules[spec.name + ".chain_nodes"],
            sys.modules[spec.name + ".upscale_nodes"])


def av_latent(value=0.0):
    return {"samples": [
        torch.full((1, 24, 2, 2, 2), value, dtype=torch.float32),
        torch.full((1, 32, 2, 9), value, dtype=torch.float32),
    ]}


def audio_for_frames(frames, sample_rate=8000):
    samples = round(frames / 24.0 * sample_rate)
    return {
        "waveform": torch.zeros((1, 2, samples), dtype=torch.float32),
        "sample_rate": sample_rate,
    }


def legacy_cache_fixture(chain, metadata, version="h3_reference_cache_v2"):
    """Materialize a real old bundle so legacy reader tests stay meaningful."""
    tensors = chain._reference_cache_tensors(metadata)
    legacy = dict(metadata)
    legacy.pop("tensor_objects", None)
    legacy["format"] = version
    legacy["signature"] = chain._fingerprint({
        "format": version, "reference_fingerprint": legacy["reference_fingerprint"],
        **{key: legacy[key] for key in ("scene", "scene_count", "prompt", "compiled_prompt",
                                       "width", "height", "length", "ref_image_size")},
        "presentation": legacy.get("presentation_contract"),
    })
    metadata_path = pathlib.Path(chain._absolute_output_path(legacy["metadata"])).with_name(
        "scene_%04d.%s.json" % (legacy["scene"], legacy["signature"][:24]))
    legacy["metadata"] = chain._relative_output_path(str(metadata_path))
    path = metadata_path.with_suffix(".safetensors")
    chain._st_save({key: value.clone() for key, value in tensors.items()}, str(path))
    legacy["tensors"] = chain._relative_output_path(str(path))
    legacy["tensors_sha256"] = chain._file_sha256(str(path))
    chain._atomic_json(chain._absolute_output_path(legacy["metadata"]), legacy)
    return legacy


def main():
    package, chain, upscale = load_package()
    required = {
        "MiniMaxH3ChainUpscaleAdapter",
        "MiniMaxH3ChainUpscaleCurrent",
        "MiniMaxH3ChainDeropeGuard",
        "MiniMaxH3ChainDeropeFreezeMask",
        "MiniMaxH3ChainDeropeContinuity",
        "MiniMaxH3ChainRecoveredAV",
        "MiniMaxH3UpscaleReferencePromptOverride",
        "MiniMaxH3ChainUpscaleReferenceConditioning",
        "H3ConditioningSyncFromLatents",
        "MiniMaxH3ChainPass2Prepare",
        "MiniMaxH3ChainUpscaleSegmentSave",
        "MiniMaxH3ChainUpscaleLoopEnd",
        "MiniMaxH3ChainUpscaleMerge",
    }
    assert required <= set(package.NODE_CLASS_MAPPINGS)
    assert upscale.UPSCALE_MANIFEST_TYPE == chain.MANIFEST_TYPE
    assert upscale.MiniMaxH3ChainUpscaleLoopEnd.RETURN_TYPES[0] == (
        chain.MANIFEST_TYPE)
    assert upscale.MiniMaxH3ChainUpscaleMerge.DEPRECATED is True
    legacy_chain_nodes = (
        chain.MiniMaxH3ScheduledPictureReference,
        chain.MiniMaxH3ScheduledVideoReference,
        chain.MiniMaxH3ScheduledAudioReference,
        chain.MiniMaxH3ScheduledReferenceToVideo,
        chain.MiniMaxH3Legacy04PolicyAdapter,
        chain.MiniMaxH3LazyMotionAVLoader,
    )
    assert all(node.DEPRECATED is True for node in legacy_chain_nodes)
    expected_derope_union = "H3_CHAIN_UPSCALE_STATE,H3_CHAIN_STATE"
    for node_name in (
            "MiniMaxH3ChainDeropeGuard",
            "MiniMaxH3ChainDeropeFreezeMask",
            "MiniMaxH3ChainDeropeContinuity",
            "MiniMaxH3ChainRecoveredAV"):
        assert str(package.NODE_CLASS_MAPPINGS[node_name].INPUT_TYPES()[
            "required"]["state"][0]) == expected_derope_union
    for name in required:
        node = package.NODE_CLASS_MAPPINGS[name]
        schema = node.INPUT_TYPES()
        for section in ("required", "optional"):
            for input_name, spec in schema.get(section, {}).items():
                options = spec[1] if len(spec) > 1 else {}
                assert str(options.get("tooltip") or "").strip(), (
                    name, input_name)
        assert len(node.OUTPUT_TOOLTIPS) == len(node.RETURN_TYPES), name
    manager_schema = package.NODE_CLASS_MAPPINGS[
        "MiniMaxH3ChainCheckpointManager"].INPUT_TYPES()
    assert "selection_json" in manager_schema["required"]
    assert set(manager_schema["optional"]) == {"plan"}
    assert "plan" not in upscale.MiniMaxH3ChainUpscaleAdapter.INPUT_TYPES()[
        "required"]

    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{
                "id": "upscale_scene_1",
                "prompt": "A clean deferred upscale test begins.",
                "length": 5,
                "steps": 2,
                "seed": "7",
            }, {
                "id": "upscale_scene_2",
                "prompt": "The same test continues without a cut.",
                "length": 5,
                "steps": 2,
                "seed": "8",
            }]}),
            "upscale_test", "unit-test", 32, 32, 1,
            "video", "head", "disabled", "generated_audio", 1,
            5 / 24, 2, 7, 18, 0, "guide")[0]
        prepared_plan = chain._plan_with_source_audio(
            chain._plan_with_external_context(plan, None), None)
        state = chain._initial_state(prepared_plan, 1)
        source_images = torch.zeros((5, 32, 32, 3), dtype=torch.float32)
        source = chain.MiniMaxH3ChainSegmentSave().save(
            state, source_images, av_latent(0.25), audio_for_frames(5),
            denoised_latent=av_latent(0.75))["result"][0]
        second_state = chain._initial_state(prepared_plan, 2)
        second_frames = int(prepared_plan["shots"][1]["delivered_frames"])
        source_2 = chain.MiniMaxH3ChainSegmentSave().save(
            second_state,
            torch.zeros((second_frames, 32, 32, 3), dtype=torch.float32),
            av_latent(0.35), audio_for_frames(second_frames),
            denoised_latent=av_latent(0.85))["result"][0]
        source_checkpoint = pathlib.Path(
            chain._absolute_output_path(source["checkpoint"]))
        with safe_open(source_checkpoint, framework="pt", device="cpu") as saved:
            assert {"video", "audio", "denoised_video", "denoised_audio"} <= set(
                saved.keys())

        selection = json.dumps({
            "run_name": "upscale_test",
            "lineage": [
                {"scene": 1, "revision": source["revision"]},
                {"scene": 2, "revision": source_2["revision"]},
            ],
        })
        manager = chain.MiniMaxH3ChainCheckpointManager()
        assert manager.passthrough("")[0] is None
        partial_selection = json.dumps({
            "run_name": "upscale_test",
            "lineage": [
                {"scene": 1, "revision": source["revision"]},
            ],
        })
        partial_manifest = manager.passthrough(partial_selection)[0]
        assert partial_manifest["clip_count"] == 1
        assert partial_manifest["planned_clip_count"] == 2
        assert partial_manifest["selection_complete"] is False
        assert [item["revision"] for item in partial_manifest["segments"]] == [
            source["revision"]]
        selected_manifest = manager.passthrough(selection)[0]
        assert selected_manifest["clip_count"] == 2
        assert selected_manifest["planned_clip_count"] == 2
        assert selected_manifest["selection_complete"] is True

        adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
        _partial_flow, partial_state, partial_source, partial_status = (
            adapter.adapt(
                partial_manifest, "partial", "h3_latent", '{"scale":2}',
                1, 0, False, 18))
        assert len(partial_state["source_manifest"]["segments"]) == 1
        assert partial_source["planned_clip_count"] == 2
        assert "scene 1/1" in partial_status
        flow, upscale_state, source_manifest, _status = adapter.adapt(
            selected_manifest, "quality", "h3_latent", '{"scale":2}',
            1, 1, False, 18)
        assert source_manifest["segments"][0]["revision"] == source["revision"]
        assert source_manifest["segments"][1]["revision"] == source_2["revision"]
        current = upscale.MiniMaxH3ChainUpscaleCurrent().current(upscale_state)
        assert torch.all(chain._streams_from_latent(current[1])[0] == 0.75)
        assert getattr(current[1]["samples"], "is_nested", False)
        assert torch.all(current[2]["samples"] == 0.75)
        assert torch.all(current[3]["samples"] == 0.75)
        assert current[7:10] == (32, 32, 7)
        assert "saved denoised x0" in current[-1]

        class FakeVideoVAE:
            def encode(self, images):
                return torch.full(
                    (1, 24, 2, int(images.shape[1]) // 16,
                     int(images.shape[2]) // 16),
                    0.625, dtype=torch.float32)

        class FakeClip:
            def tokenize(self, prompt, **kwargs):
                return {"prompt": prompt, "presentation": kwargs.get(
                    "minimax_ref_items", [])}

            def encode_from_tokens_scheduled(self, tokens):
                return [[torch.zeros((1, 1, 4)), {"tokens": tokens}]]

        first_override_refs = chain.MiniMaxH3TaggedPictureReference().add(
            torch.ones((1, 80, 96, 3), dtype=torch.float32),
            "detail_face")[0]
        override_refs = chain.MiniMaxH3TaggedPictureReference().add(
            torch.zeros((1, 64, 64, 3), dtype=torch.float32),
            "optional_style", previous=first_override_refs)[0]
        inline_override = (
            upscale.MiniMaxH3UpscaleReferencePromptOverride().override(
                prompt_override="@detail_face preserves the subject.",
                disabled_tags="@optional_style",
                references=override_refs))
        assert len(chain._tagged_reference_entries(inline_override[0])) == 1
        assert inline_override[1] == "@detail_face preserves the subject."
        assert inline_override[2] == inline_override[0]["fingerprint"]
        assert "disabled @optional_style" in inline_override[3]
        no_refs_override = (
            upscale.MiniMaxH3UpscaleReferencePromptOverride().override())
        assert no_refs_override[:3] == (None, "", "")
        assert "automatic cache remains active" in no_refs_override[3]

        cache_status = chain._cache_reference_scene(
            fingerprint="unit-test", scene=1, scene_count=2,
            prompt=source["prompt"],
            compiled_prompt=source["prompt"].replace(
                "deferred upscale test", "<Picture 1> test"),
            width=32, height=32, length=5, ref_image_size="match",
            vae=FakeVideoVAE(), audio_vae=None,
            pictures=[torch.ones((1, 64, 128, 3))], videos=[], audios=[])
        assert "reference cache saved" in cache_status
        cache_metadata = chain._find_reference_cache(
            "unit-test", 1, 2, source["prompt"], 32, 32, 5)
        assert cache_metadata["format"] == "h3_reference_cache_v3"
        # Exercise backward compatibility with an actual V2 tensor bundle.
        cache_metadata = legacy_cache_fixture(chain, cache_metadata)
        cache_descriptor = chain._reference_cache_descriptor(cache_metadata)
        assert cache_descriptor is not None
        assert cache_metadata["format"] == "h3_reference_cache_v2"
        assert len(cache_metadata["source_images"]) == 1
        assert chain._load_reference_cache_descriptor(
            cache_descriptor)["signature"] == cache_metadata["signature"]
        cached_source = chain.MiniMaxH3ChainSegmentSave().save(
            state, source_images, av_latent(0.25), audio_for_frames(5),
            denoised_latent=av_latent(0.75))["result"][0]
        adopted_cache = cached_source["reference_cache"]
        assert adopted_cache != cache_descriptor
        assert adopted_cache["metadata"].startswith(
            "h3_chains/upscale_test/reference_cache/")
        assert adopted_cache["tensors"].startswith(
            "h3_chains/upscale_test/reference_cache/")
        assert chain._load_reference_cache_descriptor(
            adopted_cache)["run_name"] == "upscale_test"
        cached_second_state = chain._initial_state(prepared_plan, 2)
        cached_source_2 = chain.MiniMaxH3ChainSegmentSave().save(
            cached_second_state,
            torch.zeros((second_frames, 32, 32, 3), dtype=torch.float32),
            av_latent(0.35), audio_for_frames(second_frames),
            denoised_latent=av_latent(0.85))["result"][0]

        # Simulate a checkpoint created by the first cache implementation:
        # its immutable revision points at the global staging object and no
        # run-local object exists yet.
        legacy_revision_path = pathlib.Path(chain._absolute_output_path(
            cached_source["revision_metadata"]))
        legacy_revision = chain._read_json(str(legacy_revision_path))
        legacy_revision["segment"]["reference_cache"] = cache_descriptor
        chain._atomic_json(str(legacy_revision_path), legacy_revision)
        pathlib.Path(chain._absolute_output_path(
            adopted_cache["metadata"])).unlink()
        pathlib.Path(chain._absolute_output_path(
            adopted_cache["tensors"])).unlink()

        legacy_selection = json.dumps({
            "run_name": "upscale_test",
            "lineage": [
                {"scene": 1, "revision": cached_source["revision"]},
                {"scene": 2, "revision": cached_source_2["revision"]},
            ],
        })
        migrated_manifest = manager.passthrough(legacy_selection)[0]
        migrated_cache = migrated_manifest["segments"][0]["reference_cache"]
        assert migrated_cache["metadata"].startswith(
            "h3_chains/upscale_test/reference_cache/")
        assert migrated_cache["tensors"].startswith(
            "h3_chains/upscale_test/reference_cache/")
        assert pathlib.Path(chain._absolute_output_path(
            cache_descriptor["metadata"])).is_file()
        assert pathlib.Path(chain._absolute_output_path(
            cache_descriptor["tensors"])).is_file()

        # Once one selection has adopted and verified the cache, the original
        # global staging pair may disappear without breaking future selection.
        pathlib.Path(chain._absolute_output_path(
            cache_descriptor["metadata"])).unlink()
        pathlib.Path(chain._absolute_output_path(
            cache_descriptor["tensors"])).unlink()
        relocated_manifest = manager.passthrough(legacy_selection)[0]
        assert (relocated_manifest["segments"][0]["reference_cache"] ==
                migrated_cache)
        cached_upscale_state = dict(upscale_state)
        cached_upscale_state["source_manifest"] = relocated_manifest
        live_override_conditioning = (
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                cached_upscale_state, FakeClip(), "error",
                video_vae=FakeVideoVAE(),
                prompt_override=inline_override[1],
                tagged_references=inline_override[0],
                override_ref_image_size="max"))
        assert live_override_conditioning[1] == (
            "<Picture 1> preserves the subject.")
        assert live_override_conditioning[2] is False
        live_metadata = live_override_conditioning[0][0][1]
        assert len(live_metadata["minimax_refs"]) == 1
        assert live_metadata["_h3_upscale_ref_image_size"] == "max"
        assert live_metadata["_h3_upscale_picture_refs_target_sized"] is False
        assert "connected Tagged refs replace cached refs" in (
            live_override_conditioning[3])
        cached_conditioning = (
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                cached_upscale_state, FakeClip(), "error"))
        assert cached_conditioning[2] is True
        assert "restored Ref2VA cache" in cached_conditioning[3]
        cached_refs = cached_conditioning[0][0][1]["minimax_refs"]
        assert len(cached_refs) == 1
        assert cached_refs[0]["kind"] == "image"
        assert torch.all(cached_refs[0]["latent"] == 0.625)
        assert cached_conditioning[0][0][1][
            "_h3_upscale_motion_ref_mode"] == "exclude_video_keep_audio"
        assert cached_conditioning[0][0][1][
            "_h3_upscale_ref_image_size"] == "match"
        assert cached_conditioning[0][0][1][
            "_h3_upscale_picture_refs_target_sized"] is False
        override_conditioning = (
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                cached_upscale_state, FakeClip(), "error",
                prompt_override="Preserve identity and fine detail."))
        assert override_conditioning[1] == "Preserve identity and fine detail."
        assert override_conditioning[0][0][1]["tokens"]["prompt"] == (
            "Preserve identity and fine detail.")
        assert "custom pass-2 prompt override" in override_conditioning[3]

        target_video = {
            "samples": torch.zeros((1, 24, 2, 4, 4), dtype=torch.float32)}
        target_conditioning = (
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                cached_upscale_state, FakeClip(), "error",
                target_video, FakeVideoVAE()))
        target_refs = target_conditioning[0][0][1]["minimax_refs"]
        assert tuple(target_refs[0]["latent"].shape[-2:]) == (2, 6)
        target_presentation = target_conditioning[0][0][1]["tokens"][
            "presentation"]
        assert tuple(target_presentation[0]["data"].shape[1:3]) == (32, 96)
        assert "pass-2 64x64 policy=match" in target_conditioning[3]
        assert "1 source masters" in target_conditioning[3]
        assert target_conditioning[0][0][1][
            "_h3_upscale_ref_image_size"] == "match"
        assert target_conditioning[0][0][1][
            "_h3_upscale_picture_refs_target_sized"] is True
        synced_target = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            target_video, target_conditioning[0], "bilinear")[0][0][1]
        assert tuple(synced_target["minimax_refs"][0][
            "latent"].shape[-2:]) == (2, 6)

        legacy_cache = dict(chain._load_reference_cache_descriptor(
            migrated_cache))
        legacy_cache["format"] = "h3_reference_cache_v1"
        legacy_cache.pop("source_images", None)
        legacy_cache["presentation"] = [
            {key: value for key, value in item.items() if key != "role"}
            for item in legacy_cache["presentation"]]
        assert chain._reference_cache_descriptor(legacy_cache) is not None
        legacy_conditioning, legacy_detail = (
            chain._conditioning_from_reference_cache_target(
                FakeClip(), FakeVideoVAE(), legacy_cache, 64, 64))
        assert legacy_detail["master_rebuilds"] == 0
        assert legacy_detail["fallback_rebuilds"] == 1
        legacy_refs = legacy_conditioning[0][1]["minimax_refs"]
        assert tuple(legacy_refs[0]["latent"].shape[-2:]) == (4, 4)

        max_cache = dict(chain._load_reference_cache_descriptor(
            migrated_cache))
        max_cache["ref_image_size"] = "max"
        max_conditioning, max_detail = (
            chain._conditioning_from_reference_cache_target(
                FakeClip(), FakeVideoVAE(), max_cache, 64, 64))
        max_refs = max_conditioning[0][1]["minimax_refs"]
        assert max_detail["rebuilt_images"] == 0
        assert tuple(max_refs[0]["latent"].shape[-2:]) == (2, 2)
        marked_max = upscale._mark_h3_upscale_motion_policy(
            max_conditioning, "exclude_video_keep_audio", "max")
        synced_max = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 2, 4, 6))},
            marked_max, "bilinear")[0][0][1]
        assert tuple(synced_max["minimax_refs"][0][
            "latent"].shape[-2:]) == (2, 2)
        assert synced_max["minimax_refs"][0]["latent_h"] == 2
        assert synced_max["minimax_refs"][0]["latent_w"] == 2

        sync_positive = [[torch.zeros((1, 1, 4)), {
            "minimax_refs": [{
                "kind": "image",
                "latent_h": 2,
                "latent_w": 3,
                "latent": torch.arange(
                    1 * 24 * 1 * 2 * 3, dtype=torch.float32).reshape(
                        1, 24, 1, 2, 3),
            }, {
                "kind": "video_audio",
                "latent_t": 2,
                "latent_h": 4,
                "latent_w": 5,
                "ref_audio_t": 2,
                "latent": torch.zeros((1, 24, 2, 4, 5)),
                "audio_latent": torch.ones((1, 32, 2, 9)),
            }, {
                "kind": "audio",
                "ref_audio_t": 2,
                "audio_latent": torch.ones((1, 32, 2, 9)),
            }],
            "minimax_keyframes": [{
                "resolved_frame_index": 0,
                "latent": torch.zeros((1, 24, 1, 2, 2)),
                "audio_latent": torch.ones((1, 32, 2, 9)),
            }],
            "unchanged": "metadata",
        }]]
        synced = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 2, 4, 6))},
            sync_positive, "bilinear")
        synced_meta = synced[0][0][1]
        assert synced[1] is None
        assert synced[2:] == (96, 64, 3.0, 2.0)
        assert tuple(synced_meta["minimax_refs"][0]["latent"].shape) == (
            1, 24, 1, 4, 10)
        assert synced_meta["minimax_refs"][0]["latent_h"] == 4
        assert synced_meta["minimax_refs"][0]["latent_w"] == 10
        assert synced_meta["minimax_refs"][1]["kind"] == "audio"
        assert "latent" not in synced_meta["minimax_refs"][1]
        assert "latent_t" not in synced_meta["minimax_refs"][1]
        assert "latent_h" not in synced_meta["minimax_refs"][1]
        assert "latent_w" not in synced_meta["minimax_refs"][1]
        assert tuple(synced_meta["minimax_refs"][1][
            "audio_latent"].shape) == (1, 32, 2, 9)
        assert "latent" not in synced_meta["minimax_refs"][2]
        assert tuple(synced_meta["minimax_keyframes"][0][
            "latent"].shape) == (1, 24, 1, 4, 6)
        assert tuple(synced_meta["minimax_keyframes"][0][
            "audio_latent"].shape) == (1, 32, 2, 9)
        assert synced_meta["unchanged"] == "metadata"
        assert tuple(sync_positive[0][1]["minimax_refs"][0][
            "latent"].shape) == (1, 24, 1, 2, 3)

        max_sync_positive = upscale._mark_h3_upscale_motion_policy(
            sync_positive, "exclude_video_keep_audio", "max")
        max_synced_meta = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 2, 4, 6))},
            max_sync_positive, "bilinear")[0][0][1]
        assert tuple(max_synced_meta["minimax_refs"][0][
            "latent"].shape) == (1, 24, 1, 2, 3)
        assert max_synced_meta["minimax_refs"][0]["latent_h"] == 2
        assert max_synced_meta["minimax_refs"][0]["latent_w"] == 3
        assert tuple(max_synced_meta["minimax_keyframes"][0][
            "latent"].shape) == (1, 24, 1, 4, 6)

        filtered_presentation, filtered_blocks = (
            chain._h3_motion_reference_policy([{
                "type": "image", "role": "native_picture",
            }, {
                "type": "video", "role": "native_video",
            }, {
                "type": "audio", "role": "native_audio",
            }, {
                "type": "video", "role": "native_video",
            }, {
                "type": "video", "role": "semantic_presentation",
            }], [{
                "kind": "video_audio", "latent": torch.zeros(1),
                "latent_t": 2, "latent_h": 4, "latent_w": 5,
                "ref_audio_t": 2, "audio_latent": torch.ones(1),
            }, {
                "kind": "video", "latent": torch.zeros(1),
                "latent_t": 2, "latent_h": 4, "latent_w": 5,
                "ref_audio_t": 0, "audio_latent": None,
            }, {
                "kind": "image", "latent": torch.zeros(1),
            }], "exclude_video_keep_audio"))
        assert [item["type"] for item in filtered_presentation] == [
            "image", "audio", "video"]
        assert [block["kind"] for block in filtered_blocks] == [
            "audio", "image"]
        assert "latent" not in filtered_blocks[0]

        native_motion = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 2, 4, 6))},
            sync_positive, "bilinear", "keep_video_native")[0][0][1]
        assert tuple(native_motion["minimax_refs"][1]["latent"].shape) == (
            1, 24, 2, 4, 5)

        resized_motion = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 2, 4, 6))},
            sync_positive, "bilinear", "resize_video")[0][0][1]
        assert tuple(resized_motion["minimax_refs"][1]["latent"].shape) == (
            1, 24, 2, 8, 16)
        assert resized_motion["minimax_refs"][1]["latent_t"] == 2

        # De-rope expands the pass-2 time axis. Spatial reference sync must
        # accept that new clock while leaving each reference's own time alone.
        derope_synced = upscale.H3ConditioningSyncFromLatents().sync(
            {"samples": torch.zeros((1, 24, 2, 2, 2))},
            {"samples": torch.zeros((1, 24, 7, 4, 6))},
            sync_positive, "bilinear", "resize_video")[0][0][1]
        assert tuple(derope_synced["minimax_refs"][1]["latent"].shape) == (
            1, 24, 2, 8, 16)

        derope_manifest = json.loads(json.dumps(source_manifest))
        derope_manifest["segments"][0].update({
            "raw_frames": 39,
            "delivered_frames": 22,
        })
        derope_manifest["segments"][1].update({
            "raw_frames": 39,
            "delivered_frames": 22,
            "visual_context_source_scene": 1,
        })
        derope_state = {
            **upscale_state,
            "source_manifest": derope_manifest,
            "source_manifest_hash": upscale._source_hash(derope_manifest),
        }
        guarded = upscale.MiniMaxH3ChainDeropeGuard().guard(
            derope_state, json.dumps({
                "holds": [4] * 39,
                "world_len": 39,
            }))
        guarded_map = json.loads(guarded[0])
        assert guarded[1:4] == (False, 17, 17)
        assert guarded_map["holds"][:17] == [1] * 17
        assert guarded_map["holds"][-17:] == [1] * 17
        assert guarded_map["holds"][17:22] == [4] * 5
        # Simulate H3 Time Smear's legal-grid tail pad. It lives on the final
        # hold and does not disturb the protected incoming prefix.
        used_map = dict(guarded_map)
        used_map["holds"] = list(used_map["holds"])
        used_map["holds"][-1] += 2
        freeze = upscale.MiniMaxH3ChainDeropeFreezeMask().mask(
            derope_state, json.dumps(used_map))
        assert freeze[1] == sum(used_map["holds"])
        assert tuple(freeze[0].shape) == (freeze[1], 8, 8)
        assert torch.all(freeze[0][:17] == 0)
        assert torch.all(freeze[0][17:] == 1)

        packed_recovered = upscale.MiniMaxH3ChainRecoveredAV().pack(
            upscale_state,
            {"samples": torch.ones((1, 24, 2, 4, 6))},
            {"samples": torch.ones((1, 32, 2, 9))})
        assert len(chain._streams_from_latent(packed_recovered[0])) == 2
        assert "joint AV" in packed_recovered[1]
        try:
            upscale.MiniMaxH3ChainRecoveredAV().pack(
                upscale_state,
                {"samples": torch.ones((1, 24, 7, 4, 6))})
        except ValueError as exc:
            assert "still has 7 latent steps" in str(exc)
        else:
            raise AssertionError("Recovered AV accepted a dilated time axis")

        class FakeSampling:
            @staticmethod
            def noise_scaling(sigma, generated, latent):
                return latent + sigma * generated

            @staticmethod
            def inverse_noise_scaling(_sigma, mixed):
                return mixed

        class FakeModel:
            objects = {
                "model_sampling": FakeSampling(),
                "process_latent_in": lambda samples: samples,
                "process_latent_out": lambda samples: samples,
            }

            def get_model_object(self, name):
                return self.objects[name]

        class FakeNoise:
            @staticmethod
            def generate_noise(latent):
                return torch.ones_like(latent["samples"])

        prepared = upscale.MiniMaxH3ChainPass2Prepare().prepare(
            {"samples": torch.ones(
                (1, 24, 2, 4, 6), dtype=torch.float32)},
            current[3], FakeModel(), FakeNoise(),
            torch.tensor([0.24, 0.0], dtype=torch.float32))
        prepared_streams = chain._streams_from_latent(prepared[0])
        prepared_masks = chain._streams_from_latent({
            "samples": prepared[0]["noise_mask"]})
        assert prepared[1:3] == (96, 64)
        assert torch.allclose(
            prepared_streams[0], torch.full_like(prepared_streams[0], 1.24))
        assert torch.all(prepared_streams[1] == 0.75)
        assert torch.all(prepared_masks[0] == 1)
        assert torch.all(prepared_masks[1] == 0)
        assert "audio" in prepared[3] and "locked" in prepared[3]

        drift_manifest = json.loads(json.dumps(source_manifest))
        drift_manifest["segments"][1].update({
            "raw_frames": 44,
            "delivered_frames": 5,
            "continuation_mode": "drift_control_av",
            "context_length": 39,
        })
        previous_hq_video = torch.arange(
            16, dtype=torch.float32).view(1, 1, 16, 1, 1).expand(
                1, 24, 16, 4, 4).clone()
        previous_hq = {"samples": [
            previous_hq_video,
            torch.full((1, 32, 2, 9), 0.75, dtype=torch.float32),
        ]}
        drift_save_state = {
            **upscale_state,
            "profile": "drift-quality",
            "source_manifest": drift_manifest,
            "source_manifest_hash": upscale._source_hash(drift_manifest),
            "segments": [],
        }
        drift_saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            drift_save_state,
            torch.zeros((5, 64, 64, 3), dtype=torch.float32),
            previous_hq)["result"][0]
        assert not drift_saved["latent_saved"]
        assert drift_saved["context_steps"] == 12
        drift_checkpoint = pathlib.Path(
            chain._absolute_output_path(drift_saved["checkpoint"]))
        with safe_open(drift_checkpoint, framework="pt", device="cpu") as saved:
            assert "upscaled_video_context" in saved.keys()
            assert "upscaled_video" not in saved.keys()
            assert tuple(saved.get_tensor(
                "upscaled_video_context").shape) == (1, 24, 12, 4, 4)

        resumed_drift_state = {
            **drift_save_state,
            "index": 2,
            "segments": [drift_saved],
            "previous_latent": None,
        }
        restored_context, restored_route = (
            upscale._load_previous_upscaled_context(
                resumed_drift_state, 2))
        assert "compact saved HQ context" in restored_route
        resumed_drift_state["previous_latent"] = restored_context
        drift_prepared = upscale.MiniMaxH3ChainPass2Prepare().prepare(
            {"samples": torch.ones(
                (1, 24, 16, 4, 4), dtype=torch.float32)},
            current[3], FakeModel(), FakeNoise(),
            torch.tensor([0.24, 0.0], dtype=torch.float32),
            resumed_drift_state)
        drift_streams = chain._streams_from_latent(drift_prepared[0])
        drift_masks = chain._streams_from_latent({
            "samples": drift_prepared[0]["noise_mask"]})
        assert torch.allclose(
            drift_streams[0][:, :, :12], previous_hq_video[:, :, -12:])
        assert torch.allclose(
            drift_streams[0][:, :, 12:],
            torch.full_like(drift_streams[0][:, :, 12:], 1.24))
        assert torch.all(drift_masks[0][:, :, :12] == 0)
        assert torch.all(drift_masks[0][:, :, 12:] == 1)
        assert "previous HQ latent tail spliced and protected" in (
            drift_prepared[3])
        derope_continuity = (
            upscale.MiniMaxH3ChainDeropeContinuity().splice(
                resumed_drift_state,
                {"samples": torch.ones(
                    (1, 24, 16, 4, 4), dtype=torch.float32)}))
        assert torch.allclose(
            derope_continuity[0]["samples"][:, :, :12],
            previous_hq_video[:, :, -12:])
        assert "previous HQ latent tail spliced and protected" in (
            derope_continuity[1])

        # The same nodes may run inline from Current Shot. Boundary guarding
        # follows actual future visual consumers rather than scene numbering,
        # and continuity uses Chain Context's resolved non-linear/composed
        # visual state instead of the immediate predecessor's video stream.
        direct_plan = {
            "compatibility": {
                "continuation_mode": "drift_control_av",
                "context_length": 39,
            },
            "shots": [{
                "id": "direct_1",
                "raw_frames": 56,
                "delivered_frames": 17,
                "continuation_mode": "drift_control_av",
            }, {
                "id": "direct_2_hard_cut",
                "raw_frames": 56,
                "delivered_frames": 17,
                "continuation_mode": "drift_control_av",
                "context_length": 0,
            }, {
                "id": "direct_3_from_1",
                "raw_frames": 56,
                "delivered_frames": 17,
                "continuation_mode": "drift_control_av",
                "context_length": 39,
                "visual_context_source": "direct_1",
            }],
        }
        direct_state_1 = {
            "plan": direct_plan,
            "index": 1,
            "previous_latent": None,
            "segments": [],
        }
        direct_guard_1 = upscale.MiniMaxH3ChainDeropeGuard().guard(
            direct_state_1, json.dumps({
                "holds": [4] * 56,
                "world_len": 56,
            }))
        assert direct_guard_1[1:4] == (False, 39, 17)
        assert json.loads(direct_guard_1[0])[
            "h3_chain_visual_consumers"] == [3]
        direct_state_2 = {**direct_state_1, "index": 2}
        direct_guard_2 = upscale.MiniMaxH3ChainDeropeGuard().guard(
            direct_state_2, json.dumps({
                "holds": [4] * 56,
                "world_len": 56,
            }))
        assert direct_guard_2[1:4] == (True, 39, 0)

        immediate_video = torch.full(
            (1, 24, 17, 4, 4), 9.0, dtype=torch.float32)
        selected_video = torch.full(
            (1, 24, 17, 4, 4), 3.0, dtype=torch.float32)
        context_audio = torch.zeros(
            (1, 32, 2, 9), dtype=torch.float32)
        direct_state_3 = {
            "plan": direct_plan,
            "index": 3,
            "previous_latent": {
                "samples": [immediate_video, context_audio]},
            "segments": [],
            "_visual_context_state": {
                # The nightly context builder validates the cached block
                # signature before reusing a pre-resolved context state.
                "_visual_context_signature": ((1, 39, -1, False),),
                "_visual_context_target": 3,
                "_visual_context_source": 1,
                "_visual_context_lead_source": 0,
                "_visual_context_lead_frames": 0,
                "previous_latent": {
                    "samples": [selected_video, context_audio]},
            },
        }
        direct_continuity = (
            upscale.MiniMaxH3ChainDeropeContinuity().splice(
                direct_state_3,
                {"samples": torch.ones(
                    (1, 24, 17, 4, 4), dtype=torch.float32)}))
        assert torch.allclose(
            direct_continuity[0]["samples"][:, :, :12],
            selected_video[:, :, -12:])
        assert "resolved chain visual context" in direct_continuity[1]
        try:
            upscale.MiniMaxH3ChainRecoveredAV().pack(
                direct_state_3,
                {"samples": torch.ones(
                    (1, 24, 17, 4, 4), dtype=torch.float32)})
        except ValueError as exc:
            assert "requires recovered audio_latent" in str(exc)
        else:
            raise AssertionError(
                "Live-chain Recovered AV accepted a video-only checkpoint")
        direct_recovered = upscale.MiniMaxH3ChainRecoveredAV().pack(
            direct_state_3,
            {"samples": torch.ones(
                (1, 24, 17, 4, 4), dtype=torch.float32)},
            {"samples": context_audio})
        assert len(chain._streams_from_latent(direct_recovered[0])) == 2
        fallback_state = {**resumed_drift_state, "previous_latent": None}
        fallback_prepared = upscale.MiniMaxH3ChainPass2Prepare().prepare(
            {"samples": torch.ones(
                (1, 24, 16, 4, 4), dtype=torch.float32)},
            current[3], FakeModel(), FakeNoise(),
            torch.tensor([0.24, 0.0], dtype=torch.float32), fallback_state)
        fallback_streams = chain._streams_from_latent(fallback_prepared[0])
        fallback_masks = chain._streams_from_latent({
            "samples": fallback_prepared[0]["noise_mask"]})
        assert torch.all(fallback_streams[0][:, :, :12] == 1)
        assert torch.all(fallback_masks[0][:, :, :12] == 0)
        assert "source prefix protected" in fallback_prepared[3]

        hq_images = torch.zeros((5, 64, 64, 3), dtype=torch.float32)
        saved_result = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            upscale_state, hq_images)
        hq_segment = saved_result["result"][0]
        assert not hq_segment["latent_saved"]
        hq_checkpoint = pathlib.Path(
            chain._absolute_output_path(hq_segment["checkpoint"]))
        with safe_open(hq_checkpoint, framework="pt", device="cpu") as saved:
            assert "delivered_audio" in saved.keys()
            assert "upscaled_video" not in saved.keys()

        recovered_audio_state = {
            **upscale_state,
            "profile": "recovered-audio",
        }
        recovered_audio = audio_for_frames(5)
        recovered_audio["waveform"].fill_(0.5)
        recovered_audio_segment = (
            upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
                recovered_audio_state, hq_images,
                recovered_audio=recovered_audio)["result"][0])
        assert recovered_audio_segment["audio_route"] == (
            "recovered de-rope audio")
        with safe_open(chain._absolute_output_path(
                recovered_audio_segment["checkpoint"]), framework="pt",
                device="cpu") as saved:
            assert torch.all(saved.get_tensor("delivered_audio") == 0.5)

        partial = upscale.MiniMaxH3ChainUpscaleLoopEnd().end(
            flow, upscale_state, hq_images, hq_segment)[0]
        assert partial["format"] == "h3_chain_upscale_partial_manifest_v1"

        flow, upscale_state, source_manifest, _status = adapter.adapt(
            selected_manifest, "quality", "h3_latent", '{"scale":2}',
            2, 0, False, 18)
        assert len(upscale_state["segments"]) == 1
        current = upscale.MiniMaxH3ChainUpscaleCurrent().current(upscale_state)
        assert current[4] == 2
        assert torch.all(current[2]["samples"] == 0.85)
        second_raw = int(source_2["raw_frames"])
        hq_images_2 = torch.zeros(
            (second_raw, 64, 64, 3), dtype=torch.float32)
        hq_segment_2 = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            upscale_state, hq_images_2)["result"][0]
        manifest = upscale.MiniMaxH3ChainUpscaleLoopEnd().end(
            flow, upscale_state, hq_images_2, hq_segment_2)[0]
        assert manifest["format"] == "h3_chain_upscale_manifest_v1"
        assert manifest["profile"] == "quality"
        assert len(manifest["segments"]) == 2
        assert not manifest["latent_saving"]
        merged_result = chain.MiniMaxH3ChainAssemble().assemble(
            manifest, "generated", "final", 96,
            copy_to_output=True, output_subfolder="published_upscale")
        merged = merged_result["result"][0]
        merged_path = pathlib.Path(merged)
        assert merged_path.is_file() and merged_path.stat().st_size > 0
        assert merged_path.parent == (
            pathlib.Path(temporary) / "h3_chains" / "upscale_test" /
            "upscaled" / "quality" / "final")
        output_copy = (pathlib.Path(temporary) / "published_upscale" /
                       merged_path.name)
        assert output_copy.is_file() and output_copy.stat().st_size > 0
        final_record = merged_path.with_suffix(".json")
        assert json.loads(final_record.read_text(encoding="utf-8"))[
            "format"] == "h3_chain_upscale_final_v1"
        legacy_merged = upscale.MiniMaxH3ChainUpscaleMerge().merge(
            manifest, "none", "legacy_wrapper", 96)["result"][0]
        assert pathlib.Path(legacy_merged).parent == merged_path.parent

        _flow, latent_state, _manifest, _ = adapter.adapt(
            selected_manifest, "archive_latent", "h3_latent", "{}",
            1, 0, True, 18)
        try:
            upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
                latent_state, hq_images)
        except ValueError as exc:
            assert "received no HQ latent" in str(exc)
        else:
            raise AssertionError("save_latent accepted a missing HQ latent")
        latent_segment = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(
            latent_state, hq_images, av_latent(1.0))["result"][0]
        with safe_open(chain._absolute_output_path(
                latent_segment["checkpoint"]), framework="pt",
                device="cpu") as saved:
            assert {"upscaled_video", "upscaled_audio"} <= set(saved.keys())

        # Deferred DeRoPE -> latent upscale, using actual CPU safetensors and
        # the same immutable selection contract emitted by Checkpoint Manager.
        from importlib import import_module
        catalogue = import_module(package.__name__ + ".checkpoint_variants")
        sources = import_module(package.__name__ + ".deferred_checkpoint_source")
        saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
        originals = [{"scene": item["index"], "revision": item["revision"],
                      "checkpoint_sha256": item["checkpoint_sha256"]}
                     for item in selected_manifest["segments"]]

        def processing_selection(profile):
            records = catalogue.saved_checkpoint_variants(temporary, "upscale_test", originals)["variants"]
            record = next(item for item in records if item["profile"] == profile)
            assert record["processing_branch"]
            return {"stage": "derope", "profile_path": record["profile_path"],
                    "branch": record["processing_branch"]}

        def rejected(call, message):
            try:
                call()
            except (ValueError, FileNotFoundError) as exc:
                assert message.lower() in str(exc).lower(), str(exc)
            else:
                raise AssertionError("Expected rejection: " + message)

        _flow, motion_state, _, _ = adapter.adapt(
            selected_manifest, "motion", "h3_latent", '{"derope":true}', 1, 0, True, 18)
        recovered = upscale.MiniMaxH3ChainRecoveredAV().pack(
            motion_state, {"samples": torch.full((1, 24, 2, 4, 4), 0.6)},
            {"samples": torch.full((1, 32, 2, 9), 0.4)})[0]
        recovered_wave = audio_for_frames(5)
        recovered_wave["waveform"].fill_(0.3)
        motion = saver.save(motion_state, hq_images, recovered, recovered_wave)["result"][0]
        choice = processing_selection("motion")
        processed = manager.passthrough(json.dumps({**json.loads(selection), "processing_source": choice}))[0]
        assert processed["clip_count"] == 2  # Never pin only the previewed prefix.
        assert processed["segments"][0]["revision"] == motion["revision"]
        assert processed["segments"][1] == selected_manifest["segments"][1]  # absent -> original
        assert processed["segments"][0]["processing_source"]["original"] == selected_manifest["segments"][0]
        assert selected_manifest["segments"][0]["revision"] == source["revision"]
        _, processed_state, _, _ = adapter.adapt(processed, "after_motion", "h3_latent", "{}", 1, 0, True, 18)
        read = upscale.MiniMaxH3ChainUpscaleCurrent().current(processed_state)
        assert torch.all(read[2]["samples"] == 0.6)
        assert torch.all(read[3]["samples"] == 0.4)
        assert torch.all(read[13]["waveform"] == 0.3)
        assert read[7:9] == (64, 64)
        assert "recovered DeRoPE" in read[-1]
        # Ref2VA's legacy lookup must use original generation geometry even
        # when the DeRoPE save already changed the canvas.
        from unittest.mock import patch
        with patch.object(chain, "_find_reference_cache", return_value=None) as lookup:
            upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                processed_state, FakeClip(), missing_cache="text_only")
            assert lookup.call_args.args[4:6] == (32, 32)
        rejected(lambda: adapter.adapt(processed, "motion", "h3_latent", "{}", 1, 0, True, 18), "new output profile")
        # Saving a second processing stage keeps transitive provenance.
        after = saver.save(processed_state, hq_images, recovered)["result"][0]
        after_record = next(item for item in catalogue.saved_checkpoint_variants(
            temporary, "upscale_test", originals)["variants"] if item["revision"] == after["revision"])
        assert after_record["originals"] == [{"scene": 1, "revision": source["revision"]}]
        _, resumed, _, _ = adapter.adapt(processed, "after_motion", "h3_latent", "{}", 2, 0, True, 18)
        assert resumed["index"] == 2 and len(resumed["segments"]) == 1
        assert torch.all(upscale.MiniMaxH3ChainUpscaleCurrent().current(resumed)[2]["samples"] == 0.85)
        # A video-only DeRoPE save reuses the exact original denoised audio,
        # while its delivered audio is read from the processing checkpoint.
        _, single_state, _, _ = adapter.adapt(selected_manifest, "motion_video", "h3_latent", '{"derope":true}', 1, 0, True, 18)
        single = {"samples": torch.full((1, 24, 2, 4, 4), 0.2)}
        saver.save(single_state, hq_images, single)
        single_manifest = sources.derope_source_manifest(selected_manifest, processing_selection("motion_video"), chain, upscale)
        loaded = upscale._load_source_tensors(single_manifest["segments"][0])
        assert torch.all(loaded["video"] == 0.2) and torch.all(loaded["audio"] == 0.75)
        assert set(upscale._load_source_tensors(single_manifest["segments"][0], ("delivered_audio",))) == {"delivered_audio"}
        saved_single, layout = upscale._latent_checkpoint_tensors({"samples": torch.zeros((2, 24, 2, 4, 4))})
        assert layout == "single" and tuple(saved_single["upscaled_samples"].shape) == (2, 24, 2, 4, 4)
        assert tuple(upscale._cpu_latent(single)["samples"].shape) == (1, 24, 2, 4, 4)
        # Recover the accidentally omitted B=1 axis in older video-only saves.
        legacy = json.loads(json.dumps(single_manifest["segments"][0]))
        legacy_path = pathlib.Path(temporary) / "legacy_single.safetensors"
        chain._st_save({"upscaled_samples": single["samples"][0].clone()}, str(legacy_path))
        legacy.update(checkpoint="legacy_single.safetensors", checkpoint_sha256=chain._file_sha256(str(legacy_path)))
        upscale._validate_processed_latent_header(legacy)
        assert tuple(upscale._load_source_tensors(legacy)["video"].shape) == (1, 24, 2, 4, 4)
        rejected(lambda: saver.save(single_state, hq_images, single, recovered_wave), "audio_latent")
        rejected(lambda: saver.save(single_state, hq_images, {"samples": torch.zeros((1, 24, 7, 4, 4))}), "RAW clock")
        rejected(lambda: saver.save(single_state, hq_images, {"samples": [single["samples"], torch.zeros((1, 32, 2, 100))]}), "audio latent")
        # Chapter-local sources retain original indexes and scheduling clocks.
        chapter = json.loads(json.dumps(selected_manifest))
        chapter.update(segments=[chapter["segments"][1]], clip_count=1,
                       total_delivered_frames=source_2["delivered_frames"], scene_start=2, scene_end=2,
                       source_scene_count=13, chapter={"number": 2, "id": "second", "start_scene": 2,
                       "end_scene": 2, "planned_end_scene": 2, "complete": True, "source_start_frame": 5})
        _, chapter_state, _, _ = adapter.adapt(chapter, "chapter_motion", "h3_latent", '{"derope":true}', 1, 0, True, 18)
        chapter_video = {"samples": torch.zeros((1, 24, (second_raw - 5) // 17 * 5 + 2, 4, 4))}
        chapter_child = saver.save(chapter_state, hq_images_2, chapter_video)["result"][0]
        chapter_choice = processing_selection("chapter_motion")
        assert "/chapters/" in chapter_choice["profile_path"]
        chapter_processed = sources.derope_source_manifest(chapter, chapter_choice, chain, upscale)
        _, chapter_next, _, _ = adapter.adapt(chapter_processed, "chapter_after", "h3_latent", "{}", 1, 0, True, 18)
        chapter_read = upscale.MiniMaxH3ChainUpscaleCurrent().current(chapter_next)
        assert chapter_read[4:6] == (2, 13)
        assert chapter_processed["chapter"]["source_start_frame"] == 5
        assert chapter_processed["segments"][0]["revision"] == chapter_child["revision"]
        # Availability/lineage failures must not silently substitute originals.
        _, preview_state, _, _ = adapter.adapt(selected_manifest, "motion_preview", "h3_latent", '{"derope":true}', 1, 0, False, 18)
        saver.save(preview_state, hq_images)
        rejected(lambda: sources.derope_source_manifest(selected_manifest, processing_selection("motion_preview"), chain, upscale), "no full latent")
        wrong = json.loads(json.dumps(selected_manifest))
        wrong["segments"][0]["revision"] = "f" * 32
        rejected(lambda: sources.derope_source_manifest(wrong, choice, chain, upscale), "different original take")
        wrong["segments"][0]["adopted_from_revision"] = source["revision"]
        assert sources.derope_source_manifest(wrong, choice, chain, upscale)["segments"][0]["revision"] == motion["revision"]
        rejected(lambda: sources.derope_source_manifest(selected_manifest, {**choice, "profile_path": "../escape"}, chain, upscale), "outside")
        changed = json.loads(json.dumps(choice))
        changed["branch"]["lineage"][0]["revision"] = "e" * 32
        rejected(lambda: sources.derope_source_manifest(selected_manifest, changed, chain, upscale), "branch changed")
        # Delete a real saved chapter take during a downstream encode. The
        # saver must not publish a new branch against its now-deleted source.
        deletion = import_module(package.__name__ + ".processing_checkpoint_delete")
        processing_manager = deletion.ProcessingCheckpointManager(temporary)
        delete_preview = processing_manager.deletion_preview(
            "upscale_test", chapter_child["revision_metadata"])
        assert delete_preview["allowed"]
        write_video = chain._write_segment_video

        def delete_source_during_encode(*args, **kwargs):
            result = write_video(*args, **kwargs)
            processing_manager.delete("upscale_test", chapter_child["revision_metadata"],
                                      delete_preview["snapshot"])
            return result

        with patch.object(chain, "_write_segment_video", side_effect=delete_source_during_encode):
            rejected(lambda: saver.save(chapter_next, hq_images_2, chapter_video), "deleted")
        failed_profile = pathlib.Path(upscale._state_profile_paths(chapter_next, 2)["metadata"]).parent.parent
        assert not any(path.is_file() for path in failed_profile.rglob("*"))
        assert pathlib.Path(chain._absolute_output_path(source_2["checkpoint"])).is_file()
        assert not any(item["revision"] == chapter_child["revision"] for item in
                       catalogue.saved_checkpoint_variants(temporary, "upscale_test", originals)["variants"])
        # Existing saves can use the exact full/partial profile manifest.
        motion_metadata = chain._read_json(chain._absolute_output_path(motion["revision_metadata"]))
        motion_metadata.pop("processing_lineage")
        chain._atomic_json(chain._absolute_output_path(motion["revision_metadata"]), motion_metadata)
        legacy_choice = processing_selection("motion")
        assert legacy_choice["branch"]["kind"] == "manifest"
        assert sources.derope_source_manifest(selected_manifest, legacy_choice, chain, upscale)["segments"][0]["revision"] == motion["revision"]
        pathlib.Path(chain._absolute_output_path(motion["checkpoint"])).unlink()
        rejected(lambda: sources.derope_source_manifest(selected_manifest, legacy_choice, chain, upscale), "missing")

    print("H3 upscale child run: denoised source preference, optional HQ latent, "
          "self-contained audio, unified manifest, assembler, and output copy pass")


if __name__ == "__main__":
    main()
