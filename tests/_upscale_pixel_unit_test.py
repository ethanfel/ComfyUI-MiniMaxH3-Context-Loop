#!/usr/bin/env python3
"""CPU pixel-loop integration: real checkpoints/media, fake VAE and CLIP.

Set COMFYUI_PATH when ComfyUI is not adjacent to the checkout. No weights,
external image upscalers, live projects or GPU sampling are used.
"""
import copy
import gc
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import weakref
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
candidates = [Path(os.environ["COMFYUI_PATH"])] if os.environ.get("COMFYUI_PATH") else []
candidates += [ROOT.parent / "Comfyui", ROOT.parent / "ComfyUI"]
COMFY = next(path for path in candidates if (path / "comfy/options.py").is_file())
sys.path.insert(0, str(COMFY))
sys.argv = ["h3-pixel-test", "--cpu"]
import comfy.options
comfy.options.enable_args_parsing()
import folder_paths
import torch


def fails(call, text):
    try:
        call()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("Expected failure: " + text)


class VideoVAE:
    def decode(self, video):
        assert video.ndim == 5 and video.shape[1] == 24
        assert torch.all(video == 0.75), "must use saved clean x0, not noisy AV"
        return torch.linspace(0, 1, 5).view(5, 1, 1, 1).expand(5, 32, 32, 3)

    def encode(self, images):
        return torch.ones(1, 24, 2, images.shape[1] // 16, images.shape[2] // 16)


class Clip:
    def tokenize(self, prompt, **kwargs):
        return {"prompt": prompt, "presentation": kwargs.get("minimax_ref_items", [])}

    def encode_from_tokens_scheduled(self, tokens):
        return [[torch.zeros(1, 1, 4), {"tokens": tokens}]]


def assert_extended_selection_resume(chain, upscale, chapter_source, frames):
    """Save only scene 8, extend to 8..10, resume 9 without redoing 8."""
    adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
    short = copy.deepcopy(chapter_source)
    short.update(scene_end=8, clip_count=1, segments=short["segments"][:1])
    short["chapter"]["end_scene"] = 8
    short["total_delivered_frames"] = short["segments"][0]["delivered_frames"]
    short["duration_seconds"] = short["total_delivered_frames"] / 24
    _, state, _, _ = adapter.adapt(short, "extended-prefix", "pixel", "{}", 1, 0, False, 18)
    saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(state, frames)["result"][0]
    metadata_path = chain._absolute_output_path(saved["metadata"])
    metadata = chain._read_json(metadata_path)
    assert metadata["source_scene_contract"]
    artifacts = {Path(chain._absolute_output_path(saved[key])) for key in (
        "checkpoint", "segment", "revision_metadata")}
    original = {path:path.read_bytes() for path in artifacts}

    def resume(source=chapter_source, backend="pixel", recipe="{}", crf=18):
        return adapter.adapt(source, "extended-prefix", backend, recipe, 9, 0, False, crf)[1]

    extended = resume()
    assert extended["index"] == 9 and extended["end_clip"] == 10
    assert extended["source_manifest_hash"] != state["source_manifest_hash"]
    assert extended["segments"][0]["revision"] == saved["revision"]
    assert all(path.read_bytes() == data for path, data in original.items())
    # Only completed sources matter; later scenes can be added/replaced.
    future_changed = copy.deepcopy(chapter_source)
    future_changed["segments"][2]["revision"] = "f" * 32
    assert resume(future_changed)["segments"][0]["revision"] == saved["revision"]
    for key, changed in (("revision", "f" * 32), ("checkpoint_sha256", "f" * 64),
                         ("segment_sha256", "f" * 64), ("prompt", "Changed prompt"),
                         ("seed", 123456), ("raw_frames", 22),
                         ("delivered_frames", 4), ("context_length", 17)):
        source = copy.deepcopy(chapter_source["segments"][0])
        source[key] = changed
        fails(lambda: upscale._verify_upscale_source(metadata, source, 8), "source")
    fails(lambda: resume(backend="h3_latent"), "different profile settings")
    fails(lambda: resume(recipe='{"denoise":0.5}'), "different profile settings")
    fails(lambda: resume(crf=20), "different profile settings")

    # Existing saves have no per-scene contract; the retained source hashes,
    # frame clock, prompt, seed and steps are sufficient for this migration.
    legacy = copy.deepcopy(metadata)
    legacy.pop("source_scene_contract")
    chain._atomic_json(metadata_path, legacy)
    assert resume()["segments"][0]["revision"] == saved["revision"]
    changed = copy.deepcopy(chapter_source)
    changed["segments"][0]["revision"] = "f" * 32
    fails(lambda: resume(changed), "different source revision")
    for key, value in (("run_name", "other"), ("profile", "other")):
        chain._atomic_json(metadata_path, {**legacy, key:value})
        fails(resume, "different run or profile")
    chain._atomic_json(metadata_path, legacy)
    # Existing artifact integrity checks still run, even on an extended range.
    damaged = copy.deepcopy(legacy)
    damaged["segment"]["checkpoint_sha256"] = "f" * 64
    chain._atomic_json(metadata_path, damaged)
    fails(resume, "checkpoint")
    chain._atomic_json(metadata_path, legacy)
    assert all(path.read_bytes() == data for path, data in original.items())


def assert_independent_pixel_cleanup(chain, upscale, state, manifest, frames):
    """Real saver metadata, safe deletion, and gap repair without changing S10."""
    processing = importlib.import_module(upscale.__package__ + ".processing_checkpoint_delete")
    manager = processing.ProcessingCheckpointManager(chain._output_root())
    first, middle, last = manifest["segments"]
    preview = manager.deletion_preview(state["run_name"], middle["revision_metadata"])
    assert preview["allowed"], preview["dependents"]
    assert preview["retained_independent_takes"][0]["revision"] == last["revision"]
    last_files = {Path(chain._absolute_output_path(last[key])) for key in
                  ("segment", "checkpoint", "generated_audio", "prompt_file", "metadata", "revision_metadata")}
    last_bytes = {path:path.read_bytes() for path in last_files}
    manager.delete(state["run_name"], middle["revision_metadata"], preview["snapshot"])
    upscale._verify_upscale_segment(last, last["index"])
    assert all(path.read_bytes() == value for path, value in last_bytes.items())
    # The already-running saver still carries the deleted scene in its
    # prefix. Its transaction must fail without touching the retained S10.
    saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
    fails(lambda: saver.save(state, frames), "deleted")
    assert all(path.read_bytes() == value for path, value in last_bytes.items())
    try:
        upscale._load_upscale_prefix(state, last["index"])
    except FileNotFoundError as exc:
        assert "scene 9 metadata is missing" in str(exc)
    else:
        raise AssertionError("a missing middle scene must not be silently skipped")
    adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
    _, repair, _, _ = adapter.adapt(
        state["source_manifest"], state["profile"], "pixel", "{}", middle["index"], middle["index"], False, 18)
    assert repair["segments"][0]["revision"] == first["revision"]
    rebuilt = saver.save(repair, frames)["result"][0]
    assert rebuilt["revision"] != middle["revision"]
    assert all(path.read_bytes() == value for path, value in last_bytes.items())
    kept_sequence = upscale._load_upscale_prefix(repair, last["index"] + 1)
    assert [s["revision"] for s in kept_sequence] == [first["revision"], rebuilt["revision"], last["revision"]]
    validated = upscale._upscale_manifest(repair, kept_sequence, complete=True)
    upscale._validate_upscale_manifest(validated)


def assert_loop_releases_pixels(package, chain, upscale, manifest, ram_cache=False, png_export=False):
    """Run the real recursive Comfy executor, without models or a live server.

    NullCache deliberately evicts ordinary node outputs. Pending subgraph
    input references still survive there, so this detects the original leak
    independently of Comfy's discretionary RAM-pressure cache policy.
    """
    import execution
    import nodes
    old_frames = []
    rendered = []
    consumed = []

    def evict_finished_images():
        if not ram_cache:
            return
        # Model ordinary output-cache eviction independently of scheduler
        # ownership. The production RAM-pressure policy is free to retain
        # outputs until needed; it must be ABLE to release finished scenes.
        old_ids = {id(ref()) for ref in old_frames if ref() is not None}
        def contains_old(value):
            if isinstance(value, dict):
                return any(contains_old(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return any(contains_old(v) for v in value)
            return id(value) in old_ids
        cache = executor.caches.outputs
        for key, entry in list(cache.cache.items()):
            if contains_old(entry.outputs):
                cache.cache.pop(key)
                cache.timestamps.pop(key, None)
                cache.used_generation.pop(key, None)

    class Pixels:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {"state": (upscale.UPSCALE_STATE_TYPE,)}}
        RETURN_TYPES = (upscale.UPSCALE_STATE_TYPE, "IMAGE", "VIDEO")
        FUNCTION = "make"

        def make(self, state):
            evict_finished_images()
            gc.collect()
            assert all(ref() is None for ref in old_frames), (
                "A waiting loop node still pins a completed scene's pixels")
            scene = int(state["index"])
            source = upscale._source_segment(state)
            images = torch.full((source["raw_frames"], 64, 96, 3), scene / 10)
            old_frames.append(weakref.ref(images))
            rendered.append(scene)
            video = None
            if png_export:
                import av
                from comfy_api.latest import InputImpl
                path = Path(chain._output_root()) / ("memory_scene_%d.mkv" % scene)
                with av.open(str(path), "w") as container:
                    stream = container.add_stream("ffv1", rate=24)
                    stream.width, stream.height, stream.pix_fmt = 96, 64, "bgr0"
                    for number, image in enumerate(images):
                        frame = av.VideoFrame.from_ndarray((image * 255).round().byte().numpy(), format="rgb24")
                        frame.pts = number
                        container.mux(stream.encode(frame))
                    container.mux(stream.encode())
                video = InputImpl.VideoFromFile(str(path))
            return state, images, video

    class AfterPNG:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {"video": ("VIDEO",), "images": ("IMAGE",), "folder": ("STRING",)}}
        RETURN_TYPES = ("IMAGE",)
        FUNCTION = "ready"

        def ready(self, video, images, folder):
            # This dependency gate models the native VIDEO saver/loop boundary.
            # PNG publication must have finished before either may advance.
            record = json.loads((Path(folder) / "export.json").read_text())
            assert record["last_scene"] == rendered[-1]
            assert len(record["clips"]) == len(rendered)
            assert video is not None
            return (images,)

    class Result:
        @classmethod
        def INPUT_TYPES(cls):
            return {"required": {"manifest": (upscale.UPSCALE_MANIFEST_TYPE,),
                                 "tail": ("IMAGE",)}}
        RETURN_TYPES = ()
        FUNCTION = "consume"
        OUTPUT_NODE = True

        def consume(self, manifest, tail):
            assert len(manifest["segments"]) == len(rendered) == 2, rendered
            assert torch.all(tail == rendered[-1] / 10)
            consumed.append(manifest)
            return ()

    prompt = {
        "adapter": {"class_type": "MiniMaxH3ChainUpscaleAdapter", "inputs": {
            "source_manifest": manifest, "profile": "memory-test-" + str(ram_cache) + ("-png" if png_export else ""), "backend": "pixel",
            "recipe_json": "{}", "start_clip": 1, "end_clip": 0,
            "save_latent": False, "segment_crf": 18}},
        "pixels": {"class_type": "MemoryTestPixels", "inputs": {"state": ["adapter", 1]}},
        "save": {"class_type": "MiniMaxH3ChainUpscaleSegmentSave", "inputs": {
            "state": ["pixels", 0], "images": ["pixels", 1]}},
        "end": {"class_type": "MiniMaxH3ChainUpscaleLoopEnd", "inputs": {
            "flow": ["adapter", 0], "state": ["pixels", 0],
            "images": ["pixels", 1], "segment": ["save", 0]}},
        "result": {"class_type": "MemoryTestResult", "inputs": {
            "manifest": ["end", 0], "tail": ["end", 2]}},
    }
    if png_export:
        prompt["png"] = {"class_type": "MiniMaxH3ChainExportPNG", "inputs": {
            "video": ["pixels", 2], "state": ["pixels", 0], "export_name": "loop-sequence",
            "first_frame_number": 1, "png_compression": 1, "embed_workflow": False,
            "png_bit_depth": "16", "save_workers": 2}}
        prompt["after_png"] = {"class_type": "MemoryTestAfterPNG", "inputs": {
            "video": ["png", 4], "folder": ["png", 0], "images": ["pixels", 1]}}
        prompt["save"]["inputs"]["images"] = ["after_png", 0]
        prompt["end"]["inputs"]["images"] = ["after_png", 0]
    server = SimpleNamespace(client_id=None, last_node_id=None,
                             send_sync=lambda *a, **k: None)
    executor = execution.PromptExecutor(server, execution.CacheType.RAM_PRESSURE if ram_cache else execution.CacheType.NONE,
                                       {"ram": 0, "ram_inactive": 0})
    class SaveForTest(upscale.MiniMaxH3ChainUpscaleSegmentSave):
        # With no cache, eagerly scheduled OUTPUT_NODE saves can execute twice
        # before the lazy boundary attaches its dependencies. Test the same
        # saver through its explicit handoff dependency only.
        OUTPUT_NODE = False

    with patch.dict(nodes.NODE_CLASS_MAPPINGS, {
            **package.NODE_CLASS_MAPPINGS, "MemoryTestPixels": Pixels,
            "MemoryTestAfterPNG": AfterPNG,
            "MemoryTestResult": Result,
            "MiniMaxH3ChainUpscaleSegmentSave": (
                upscale.MiniMaxH3ChainUpscaleSegmentSave if ram_cache else SaveForTest)}):
        executor.execute(prompt, "pixel-memory-test", execute_outputs=["result"])
    assert executor.success, executor.status_messages
    assert len(consumed) == 1 and rendered == [1, 2]
    evict_finished_images()
    gc.collect()
    assert all(ref() is None for ref in old_frames)


def assert_attributed_branch_upscales(chain, upscale):
    """Real save -> attach -> select -> cached conditioning -> HQ save/resume."""
    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": f"attached_{i}", "prompt": "A quiet room.",
                                    "length": 5, "steps": 2, "seed": str(i),
                                    "context_length": 0, "audio_context_length": 0}
                                   for i in (1, 2)]}),
            "attached_pixel", "attached-pixel-cache", 32, 32, 1,
            "video", "head", "disabled", "generated_audio", 1,
            5 / 24, 2, 7, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        latent = {"samples": [torch.full((1, 24, 2, 2, 2), 0.75),
                               torch.full((1, 32, 2, 9), 0.75)]}
        audio = {"waveform": torch.full((1, 2, round(5 / 24 * 8000)), 0.25),
                 "sample_rate": 8000}
        sources = []
        # Save an original branch, then a new scene-1 tip to receive scene 2.
        for index in (1, 2, 1):
            sources.append(chain.MiniMaxH3ChainSegmentSave().save(
                chain._initial_state(plan, index), torch.zeros(5, 32, 32, 3),
                latent, audio, denoised_latent=latent)["result"][0])
        candidate, parent = sources[1:]
        for index, source in ((1, parent), (2, candidate)):
            chain._cache_reference_scene(
                fingerprint="attached-pixel-cache", scene=index, scene_count=2,
                prompt=source["prompt"], compiled_prompt="<Picture 1> in a quiet room.",
                width=32, height=32, length=5, ref_image_size="match",
                vae=VideoVAE(), audio_vae=None, pictures=[torch.ones(1, 32, 32, 3)],
                videos=[], audios=[])
        attached = chain.CheckpointGraphManager(temporary).attribute(
            plan["run_name"], 1, parent["revision"], 2, candidate["revision"])
        assert attached["created"]
        before = {p: p.read_bytes() for p in Path(temporary).rglob("*") if p.is_file()}
        manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough({
            "run_name": plan["run_name"], "output_mode": "workflow_local",
            "lineage": [{"scene": 1, "revision": parent["revision"]},
                        {"scene": 2, "revision": attached["revision"]}]})[0]
        assert candidate["revision"] in manifest["archives"]["plan"]
        assert manifest["segments"][1]["checkpoint"] == candidate["checkpoint"]
        adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
        for index in (1, 2):
            # Resuming the second scene verifies the HQ prefix too.
            flow, state, _, _ = adapter.adapt(
                manifest, "attached", "pixel", "{}", index, index, False, 18)
            assert len(state["segments"]) == index - 1
            current = upscale.MiniMaxH3ChainUpscalePixelCurrent().current(state, VideoVAE())
            target = current[1].repeat_interleave(2, 1).repeat_interleave(2, 2)
            conditioned = upscale.MiniMaxH3ChainUpscalePixelConditioning().condition(
                state, Clip(), target, VideoVAE(), missing_cache="error")
            assert conditioned[5] is True, "attached take must retain its source reference cache"
            saved = upscale.MiniMaxH3ChainUpscaleSegmentSave().save(state, target)["result"][0]
            assert saved["source_revision"] == (parent["revision"] if index == 1 else attached["revision"])
            result = upscale.MiniMaxH3ChainUpscaleLoopEnd().end(flow, state, target, saved)[0]
        upscale._validate_upscale_manifest(result)
        assert len(result["segments"]) == 2
        assembled = chain.MiniMaxH3ChainAssemble().assemble(
            result, "plan", "attached_final", 128, False, "")["result"][0]
        assert Path(assembled).is_file()
        assert all(p.read_bytes() == content for p, content in before.items()), \
            "upscaling must not rewrite either branch, its recovery snapshots or caches"


def main():
    spec = importlib.util.spec_from_file_location(
        "h3_pixel_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    chain = sys.modules[spec.name + ".chain_nodes"]
    upscale = sys.modules[spec.name + ".upscale_nodes"]
    for name in ("MiniMaxH3ChainUpscalePixelCurrent", "MiniMaxH3ChainUpscalePixelConditioning"):
        cls = package.NODE_CLASS_MAPPINGS[name]
        assert cls.EXPERIMENTAL and len(cls.OUTPUT_TOOLTIPS) == len(cls.RETURN_TYPES)

    with tempfile.TemporaryDirectory() as temporary:
        folder_paths.output_directory = temporary
        plan = chain.MiniMaxH3ChainPlan().build(
            json.dumps({"shots": [{"id": "pixel_%d" % i, "prompt": "A quiet room.",
                                    "length": 5, "steps": 2, "seed": str(i)}
                                   for i in (1, 2)]}),
            "pixel_test", "pixel-test-cache", 32, 32, 1,
            "video", "head", "disabled", "generated_audio", 1,
            5 / 24, 2, 7, 18, 0, "guide")[0]
        plan = chain._plan_with_source_audio(chain._plan_with_external_context(plan, None), None)
        lineage = []
        source_segments = []
        for index in (1, 2):
            state = chain._initial_state(plan, index)
            delivered = int(plan["shots"][index - 1]["delivered_frames"])
            latent = {"samples": [torch.full((1, 24, 2, 2, 2), 0.75),
                                   torch.full((1, 32, 2, 9), 0.75)]}
            audio = {"waveform": torch.full((1, 2, round(delivered / 24 * 8000)), index * 0.1),
                     "sample_rate": 8000}
            segment = chain.MiniMaxH3ChainSegmentSave().save(
                state, torch.zeros(delivered, 32, 32, 3), latent, audio,
                denoised_latent=latent)["result"][0]
            source_segments.append(segment)
            lineage.append({"scene": index, "revision": segment["revision"]})
        manifest = chain.MiniMaxH3ChainCheckpointManager().passthrough(
            json.dumps({"run_name": "pixel_test", "lineage": lineage}))[0]
        original_files = {p: p.read_bytes() for p in Path(temporary).rglob("*") if p.is_file()}
        pinned = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps({
            "run_name": "pixel_test", "lineage": lineage,
            "output_mode": "workflow_local"}))[0]
        assert pinned == manifest, "local pin must preserve the source recipe and manifests"
        manifest = pinned
        assert_loop_releases_pixels(package, chain, upscale, manifest)
        assert_loop_releases_pixels(package, chain, upscale, manifest, ram_cache=True)
        assert_loop_releases_pixels(package, chain, upscale, manifest, ram_cache=True, png_export=True)
        # Chapter output can combine takes with different catalog histories.
        # Cache lookup must use the current take, including an empty legacy
        # fingerprint, not the first chapter scene's catalog.
        for fingerprint in ("second-scene-catalog", ""):
            mixed = copy.deepcopy(manifest)
            mixed["segments"][1]["generation_fingerprint"] = fingerprint
            mixed["segments"][1].pop("reference_cache", None)
            with patch.object(chain, "_find_reference_cache", return_value=None) as lookup:
                try:
                    upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                        {"source_manifest":mixed, "index":2}, Clip(), missing_cache="error")
                except FileNotFoundError:
                    pass
                else:
                    raise AssertionError("Expected a missing cache")
                assert lookup.call_args.args[:2] == (fingerprint, 2)
        adapter = upscale.MiniMaxH3ChainUpscaleAdapter()
        flow, state, _, _ = adapter.adapt(manifest, "pixel", "pixel", "{}", 1, 0, False, 18)
        reader = upscale.MiniMaxH3ChainUpscalePixelCurrent()
        conditioner = upscale.MiniMaxH3ChainUpscalePixelConditioning()
        schema = conditioner.INPUT_TYPES()
        anchors = ("override_semantic_anchor_size", "override_semantic_anchor_mode")
        assert list(schema["optional"])[-4:] == ["conditioning_width", "conditioning_height", *anchors]
        assert list(schema["optional"])[:-4] == [
            name for name in upscale.MiniMaxH3ChainUpscaleReferenceConditioning.INPUT_TYPES()["optional"]
            if name not in ("video_vae", "target_video_latent", *anchors)]
        for axis in ("width", "height"):
            assert schema["optional"][f"conditioning_{axis}"][1]["default"] == 0
        current = reader.current(state, VideoVAE())
        assert current[1].shape == (5, 32, 32, 3)
        assert current[2]["sample_rate"] == 8000 and current[4] == 1
        source_tensors = upscale._load_source_tensors(manifest["segments"][0])
        source_tensors.pop("delivered_audio")
        with patch.object(upscale, "_load_source_tensors", return_value=source_tensors):
            assert reader.current(state, VideoVAE())[2] is None
        fails(lambda: reader.current({**state, "profile_config": {"backend": "h3_latent"}}, VideoVAE()),
              "backend=pixel")
        target = torch.zeros(5, 64, 96, 3)
        result = conditioner.condition(state, Clip(), target, VideoVAE())
        assert result[1] is target and result[2:4] == (96, 64)
        assert "x3/y2" in result[-1] and result[5] is False
        for bad, message in ((target[:4], "RAW RGB"), (target[..., :2], "RAW RGB"),
                             (torch.zeros(5, 64, 80, 3), "multiples of 32")):
            fails(lambda: conditioner.condition(state, Clip(), bad, VideoVAE()), message)
        for axis in ("width", "height"):
            for invalid in (-32, 1, 1080, 32.5, "64", None, 16416):
                fails(lambda: conditioner.condition(state, Clip(), target, VideoVAE(),
                      **{f"conditioning_{axis}":invalid}), f"conditioning_{axis} must be 0")

        # Real cache-v2 RGB master rebuild, same canvas as latent counterpart,
        # and no double scaling during the integrated sync step.
        chain._cache_reference_scene(
            fingerprint="pixel-test-cache", scene=1, scene_count=2,
            prompt=source_segments[0]["prompt"], compiled_prompt="<Picture 1> in a quiet room.",
            width=32, height=32, length=5, ref_image_size="match",
            vae=VideoVAE(), audio_vae=None, pictures=[torch.ones(1, 64, 128, 3)],
            videos=[], audios=[])
        cached = conditioner.condition(state, Clip(), target, VideoVAE(), missing_cache="error")
        assert cached[5] is True
        latent_conditioning = upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
            state, Clip(), "error", {"samples": torch.zeros(1, 24, 2, 4, 6)}, VideoVAE())
        for actual, expected in zip(cached[0][0][1]["minimax_refs"],
                                    latent_conditioning[0][0][1]["minimax_refs"]):
            assert torch.equal(actual["latent"], expected["latent"])
        assert torch.equal(cached[0][0][1]["tokens"]["presentation"][0]["data"],
                           latent_conditioning[0][0][1]["tokens"]["presentation"][0]["data"])
        auto = conditioner.condition(state, Clip(), target, VideoVAE(), missing_cache="error",
                                     conditioning_width=0, conditioning_height=0)
        assert auto[1] is target and auto[2:] == cached[2:]
        assert torch.equal(auto[0][0][1]["minimax_refs"][0]["latent"],
                           cached[0][0][1]["minimax_refs"][0]["latent"])
        # Each zero is independent: manual dimensions size reference pictures
        # while all image outputs continue to report the true target canvas.
        for requested, expected_size in (((32, 32), (32, 32)),
                                         ((64, 0), (64, 64)),
                                         ((0, 32), (96, 32))):
            custom = conditioner.condition(state, Clip(), target, VideoVAE(), missing_cache="error",
                conditioning_width=requested[0], conditioning_height=requested[1])
            expected = upscale.MiniMaxH3ChainUpscaleReferenceConditioning().condition(
                state, Clip(), "error", video_vae=VideoVAE(), _target_size=expected_size)
            assert custom[1] is target and custom[2:4] == (96, 64)
            assert "conditioning canvas %dx%d" % expected_size in custom[-1]
            assert torch.equal(custom[0][0][1]["minimax_refs"][0]["latent"],
                               expected[0][0][1]["minimax_refs"][0]["latent"])
            assert torch.equal(custom[0][0][1]["tokens"]["presentation"][0]["data"],
                               expected[0][0][1]["tokens"]["presentation"][0]["data"])
        assert custom[0][0][1]["minimax_refs"][0]["latent"].shape != \
            cached[0][0][1]["minimax_refs"][0]["latent"].shape
        # Connected references follow the same target path, not source size.
        refs = chain.MiniMaxH3TaggedPictureReference().add(torch.ones(1, 64, 128, 3), "detail")[0]
        connected = conditioner.condition(state, Clip(), target, VideoVAE(),
            tagged_references=refs, prompt_override="@detail in a quiet room.", override_ref_image_size="match")
        assert "canvas=96x64" in connected[-1]
        assert torch.equal(connected[0][0][1]["minimax_refs"][0]["latent"],
                           cached[0][0][1]["minimax_refs"][0]["latent"])
        connected_custom = conditioner.condition(state, Clip(), target, VideoVAE(),
            tagged_references=refs, prompt_override="@detail in a quiet room.",
            override_ref_image_size="match", conditioning_width=32, conditioning_height=32)
        cached_custom = conditioner.condition(state, Clip(), target, VideoVAE(), missing_cache="error",
                                             conditioning_width=32, conditioning_height=32)
        assert connected_custom[1] is target and connected_custom[2:4] == (96, 64)
        assert "canvas=32x32" in connected_custom[-1]
        assert torch.equal(connected_custom[0][0][1]["minimax_refs"][0]["latent"],
                           cached_custom[0][0][1]["minimax_refs"][0]["latent"])
        maximum = conditioner.condition(state, Clip(), target, VideoVAE(),
            tagged_references=refs, prompt_override="@detail in a quiet room.", override_ref_image_size="max")
        maximum_large = conditioner.condition(state, Clip(), torch.zeros(5, 96, 128, 3), VideoVAE(),
            tagged_references=refs, prompt_override="@detail in a quiet room.", override_ref_image_size="max")
        assert torch.equal(maximum[0][0][1]["minimax_refs"][0]["latent"],
                           maximum_large[0][0][1]["minimax_refs"][0]["latent"])
        maximum_custom = conditioner.condition(state, Clip(), target, VideoVAE(),
            tagged_references=refs, prompt_override="@detail in a quiet room.", override_ref_image_size="max",
            conditioning_width=32, conditioning_height=32)
        assert torch.equal(maximum[0][0][1]["minimax_refs"][0]["latent"],
                           maximum_custom[0][0][1]["minimax_refs"][0]["latent"])
        # Keyframes/motion scale once; native audio tensor and time stay intact.
        audio_ref = torch.ones(1, 32, 2, 9)
        keyframe = torch.ones(1, 24, 2, 2, 2)
        conditioning = [[torch.zeros(1), {"minimax_keyframes": [{"frame": 3, "latent": keyframe}],
            "minimax_refs": [{"kind": "audio", "latent": audio_ref}]}]]
        with patch.object(upscale.MiniMaxH3ChainUpscaleReferenceConditioning, "condition",
                          return_value=(conditioning, "test", False, "test")):
            synced = conditioner.condition(state, Clip(), target, VideoVAE(),
                conditioning_width=32, conditioning_height=32)[0][0][1]
        assert synced["minimax_keyframes"][0]["latent"].shape == (1, 24, 2, 4, 6)
        assert synced["minimax_keyframes"][0]["frame"] == 3
        assert synced["minimax_refs"][0]["latent"] is audio_ref
        assert conditioning[0][1]["minimax_keyframes"][0]["latent"].shape[-2:] == (2, 2)

        # Drift-Control in a parent must not require an unused pixel HQ latent.
        drift = copy.deepcopy(state)
        drift["source_manifest"]["segments"][1].update(
            raw_frames=44, delivered_frames=5, context_length=39, continuation_mode="drift_control_av")
        drift["profile"] = "pixel-drift"
        assert upscale._next_drift_context_steps(drift, 1) == 0
        assert upscale._load_previous_upscaled_context(drift, 2)[0] is None
        upscale.MiniMaxH3ChainUpscaleSegmentSave().save(drift, target)
        drift["profile_config"]["backend"] = "h3_latent"
        fails(lambda: upscale.MiniMaxH3ChainUpscaleSegmentSave().save(drift, target), "must receive")

        saver = upscale.MiniMaxH3ChainUpscaleSegmentSave()
        saved = saver.save(state, target)["result"][0]
        assert saved["context_steps"] == 0 and saved["latent_saved"] is False
        end = upscale.MiniMaxH3ChainUpscaleLoopEnd()
        with patch.object(end, "_recurse", side_effect=lambda flow, next_state, *a: next_state):
            live_next = end.end(flow, state, target, saved)
        assert live_next["index"] == 2 and live_next["previous_latent"] is None
        # Exercise real GraphBuilder expansion with the distributed workflow.
        # Every scene-dependent pixel node must recurse; the manager and model
        # loaders stay bootstrap-only, and conditioning/images stay paired.
        from comfy_execution.graph import DynamicPrompt
        wf_path, = (ROOT / "example_workflows").glob(
            "Deferred Upscale - Pixel DLSS5 + USDU - EXPERIMENTAL - MiniMax H3*.json")
        wf = json.loads(wf_path.read_text())
        links = {link[0]: link for link in wf["links"]}
        prompt = {str(n["id"]): {"class_type": n["type"], "inputs": {
            s["name"]: [str(links[s["link"]][1]), links[s["link"]][2]]
            for s in n["inputs"] if s["link"] is not None}}
            for n in wf["nodes"] if n["type"] != "Note"}
        def node_id(kind):
            return next(k for k, v in prompt.items() if v["class_type"] == kind)
        expanded = end._recurse(
            [node_id("MiniMaxH3ChainUpscaleAdapter"), 0], live_next,
            DynamicPrompt(prompt), node_id("MiniMaxH3ChainUpscaleLoopEnd"))["expand"]
        kinds = {v["class_type"]: k for k, v in expanded.items()}
        assert {"MiniMaxH3ChainUpscalePixelCurrent", "MiniMaxH3ChainUpscalePixelConditioning",
                "DLSS5EnhanceImages", "BasicGuider", "UltimateSDUpscaleNoUpscaleGuider",
                "MiniMaxH3ChainUpscaleSegmentSave", "MiniMaxH3ChainUpscaleLoopEnd"} <= kinds.keys()
        assert "MiniMaxH3ChainCheckpointManager" not in kinds and "UNETLoader" not in kinds
        assert expanded[kinds["MiniMaxH3ChainUpscaleAdapter"]]["inputs"]["initial_state"]["index"] == 2
        refinement_inputs = expanded[kinds["UltimateSDUpscaleNoUpscaleGuider"]]["inputs"]
        assert refinement_inputs["upscaled_image"] == [kinds["MiniMaxH3ChainUpscalePixelConditioning"], 1]
        assert expanded[kinds["BasicGuider"]]["inputs"]["conditioning"] == [
            kinds["MiniMaxH3ChainUpscalePixelConditioning"], 0]
        _, resumed, _, _ = adapter.adapt(manifest, "pixel", "pixel", "{}", 2, 0, False, 18)
        assert resumed["segments"][0]["revision"] == saved["revision"]
        fails(lambda: adapter.adapt(manifest, "pixel", "h3_latent", "{}", 2, 0, False, 18),
              "different profile settings")
        second = reader.current(resumed, VideoVAE())
        assert second[7] > 0, "fixture must exercise repeated prefix trimming"
        frames = second[1].repeat_interleave(2, dim=1).repeat_interleave(3, dim=2)
        saved2 = saver.save(resumed, frames)["result"][0]
        expected_audio = upscale._load_source_tensors(manifest["segments"][1])["delivered_audio"]
        child_tensors = chain._st_load(chain._absolute_output_path(saved2["checkpoint"]))
        assert torch.equal(child_tensors["delivered_audio"], expected_audio)
        assert saved2["delivered_frames"] == 5 - second[7]
        final = end.end(flow, resumed, frames, saved2)[0]
        assert final["completed_clip_count"] == 2
        assert final["total_delivered_frames"] == sum(s["delivered_frames"] for s in manifest["segments"])
        assembled = chain.MiniMaxH3ChainAssemble().assemble(
            final, "plan", "pixel_test_final", 128, False, "")
        assert Path(chain._absolute_output_path(assembled["result"][0])).is_file()
        streams = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-of", "json",
            assembled["result"][0]]))["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == (96, 64)
        assert int(video["nb_frames"]) == final["total_delivered_frames"]
        assert any(s["codec_type"] == "audio" for s in streams)
        # No original prompt, checkpoint, audio or selection metadata changed.
        assert all(path.read_bytes() == content for path, content in original_files.items())
        # A chapter is a non-1-based view. Exercise scenes 8..10 through the
        # actual CPU decode/conditioning/save/resume/assembly path, reusing the
        # tiny immutable artifacts above as source fixtures.
        chapter_source = copy.deepcopy(manifest)
        chapter_source.update({
            "format":chain.CHAPTER_MANIFEST_FORMAT,
            "scene_start":8, "scene_end":10, "clip_count":3,
            "source_scene_count":12,
            "chapter":{"number":2, "id":"two", "title":"Chapter 2",
                       "start_scene":8, "end_scene":10, "planned_end_scene":12,
                       "complete":False, "source_start_frame":120,
                       "editorial_origin_frame":100},
            "segments":[copy.deepcopy(manifest["segments"][offset]) for offset in (0, 1, 1)],
        })
        for index, segment in enumerate(chapter_source["segments"], start=8):
            segment.update(index=index, id=f"chapter_scene_{index}")
            if index > 8:
                segment["visual_context_blocks"] = [{"source_scene":index - 1}]
        chapter_source["total_delivered_frames"] = sum(s["delivered_frames"] for s in chapter_source["segments"])
        chapter_source["duration_seconds"] = chapter_source["total_delivered_frames"] / 24
        chapter_source["editorial"] = chain._normalize_run_editorial({
            "scene_order":[{"scene":i,"scene_id":f"chapter_scene_{i}"} for i in (8, 9, 10)],
            "chapters":[{"id":"two","start_scene_id":"chapter_scene_8"}],
        }, manifest["run_name"])
        assert_extended_selection_resume(chain, upscale, chapter_source, target)
        before_chapter = {p:p.read_bytes() for p in Path(temporary).rglob("*") if p.is_file()}
        _, scoped_state, _, _ = adapter.adapt(chapter_source, "pixel", "pixel", "{}", 1, 0, False, 18)
        assert scoped_state["index"] == 8 and scoped_state["end_clip"] == 10
        assert upscale._derope_state_view(scoped_state)[1:3] == (8, 10)
        assert upscale._source_scene_count(chapter_source) == 12
        assert upscale.MiniMaxH3ChainUpscaleCurrent().current(scoped_state)[4:6] == (8, 12)
        assert upscale._derope_future_visual_consumers(scoped_state, 8) == [9]
        with patch.object(chain, "_compile_tagged_reference_prompt",
                          wraps=chain._compile_tagged_reference_prompt) as compile_prompt:
            conditioner.condition(scoped_state, Clip(), target, VideoVAE(),
                tagged_references=refs, prompt_override="@detail in a quiet room.")
            assert compile_prompt.call_args.args[1:3] == (8, 12)
        drift_chapter = copy.deepcopy(scoped_state)
        drift_chapter["profile_config"]["backend"] = "h3_latent"
        drift_chapter["source_manifest"]["segments"][1].update(
            raw_frames=44, delivered_frames=5, continuation_mode="drift_control_av")
        assert upscale._next_drift_context_steps(drift_chapter, 8) > 0
        assert upscale._next_drift_context_steps(drift_chapter, 10) == 0
        fails(lambda: adapter.adapt(chapter_source, "pixel", "pixel", "{}", 2, 0, False, 18), "start_clip")
        for index in (8, 9, 10):
            assert scoped_state["index"] == index
            decoded = reader.current(scoped_state, VideoVAE())
            assert decoded[5] == index
            hq = decoded[1].repeat_interleave(2, dim=1).repeat_interleave(3, dim=2)
            conditioned = conditioner.condition(scoped_state, Clip(), hq, VideoVAE())
            assert conditioned[2:4] == (96, 64)
            saved_chapter = saver.save(scoped_state, hq)["result"][0]
            assert saved_chapter["index"] == index
            assert f"chapters/02_two/upscaled/pixel/segments/clip_{index:04d}." in saved_chapter["segment"]
            assert saved_chapter["delivered_frames"] == chapter_source["segments"][index - 8]["delivered_frames"]
            if index == 10:
                chapter_final = end.end(flow, scoped_state, hq, saved_chapter)[0]
            else:
                with patch.object(end, "_recurse", side_effect=lambda flow, next_state, *a: next_state):
                    next_state = end.end(flow, scoped_state, hq, saved_chapter)
                _, scoped_state, _, _ = adapter.adapt(chapter_source, "pixel", "pixel", "{}", index + 1, 0, False, 18)
                assert scoped_state["index"] == next_state["index"]
                assert scoped_state["segments"] == next_state["segments"]
        assert chapter_final["format"] == "h3_chain_upscale_manifest_v1"
        assert chapter_final["completed_clip_count"] == 3
        assert (chapter_final["scene_start"], chapter_final["scene_end"]) == (8, 10)
        upscale._validate_upscale_manifest(chapter_final)
        broken = copy.deepcopy(chapter_final)
        broken["segments"][1]["index"] = 2
        fails(lambda: upscale._validate_upscale_manifest(broken), "wrong scene index")
        chapter_assembly = upscale._assembly_manifest(chapter_final, chapter_final["segments"])
        assert chapter_assembly["chapter"]["source_start_frame"] == 120
        assert chapter_assembly["chapter"]["editorial_origin_frame"] == 100
        assert chapter_assembly["chapter"]["complete"] is False
        assert chapter_assembly["chapter"]["resolution"] == {"width":96,"height":64}
        assert chapter_assembly["editorial"] == chapter_source["editorial"]
        # Pixel export geometry need not be a multiple of the H3 latent grid.
        arbitrary = [{**s, "width":1920, "height":1080} for s in chapter_final["segments"]]
        assert upscale._assembly_manifest(chapter_final, arbitrary)["chapter"]["resolution"] == {"width":1920,"height":1080}
        assembled_chapter = chain.MiniMaxH3ChainAssemble().assemble(
            chapter_final, "plan", "chapter_test_final", 128, False, "")
        final_path = assembled_chapter["result"][0]
        assert "chapters/02_two/upscaled/pixel/final/" in final_path
        streams = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", final_path]))["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        assert int(video["nb_frames"]) == chapter_source["total_delivered_frames"]
        assert (video["width"], video["height"]) == (96, 64)
        assert all(path.read_bytes() == content for path, content in before_chapter.items())
        assert_independent_pixel_cleanup(chain, upscale, scoped_state, chapter_final, hq)
    assert_attributed_branch_upscales(chain, upscale)
    print("Pixel upscale: geometry, conditioning, RAW trim, Drift-Control isolation, attributed save/resume/assembly, independent cleanup/gap repair and immutable source pass")


if __name__ == "__main__":
    main()
