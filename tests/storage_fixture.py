"""Small composite legacy layout; media are sentinel bytes, never real tensors.

Only test setup writes files. Shared by inventory and compatibility baselines.
"""

import hashlib
import json

RUN = "storage_fixture"
BASE = "1" * 32
ALT = "2" * 32
TIP = "3" * 32
BRANCH = "a" * 32
EMPTY_BRANCH = "b" * 32
PASS = "4" * 32
SEED = "18446744073709551613"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def composite_project(output):
    root = output / "h3_chains" / RUN
    root.mkdir(parents=True)

    def address(relative):
        return "h3_chains/" + RUN + "/" + relative

    def artifact(relative, content=b"MEDIA MUST NOT BE OPENED BY INSPECTOR"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return address(relative)

    def take(scene, revision, parent=None, alternate=None):
        stem = "clip_%04d.%s" % (scene, revision)
        segment = {"index": scene, "id": "scene_%d" % scene, "revision": revision,
                   "seed": SEED, "prompt": "PRIVATE fixture prompt /keep/these/bytes",
                   "width": 960, "height": 544, "raw_frames": 175, "delivered_frames": 175,
                   "context_length": 0, "audio_context_length": 0,
                   "segment": artifact("segments/" + stem + ".mp4"),
                   "checkpoint": artifact("checkpoints/" + stem + ".safetensors"),
                   "generated_audio": artifact("generated_audio/" + stem + ".wav"),
                   "revision_metadata": address("checkpoints/" + stem + ".json")}
        segment["checkpoint_sha256"] = hashlib.sha256((output / segment["checkpoint"]).read_bytes()).hexdigest()
        if parent:
            segment["predecessor_revision"] = parent
        if alternate:
            segment.update(take_kind="editorial_alternate", alternate_of_revision=alternate)
        metadata = {"format": "h3_chain_segment_v3", "run_name": RUN,
                    "compatibility": {"width": 960, "height": 544}, "segment": segment,
                    "archives": {key: address("recovery_archives/" + revision + "/" + key + ".json")
                                 for key in ("plan", "workflow", "api_prompt")}}
        for key in metadata["archives"]:
            write_json(output / metadata["archives"][key], {"seed": SEED, "prompt": "PRIVATE " + key})
        write_json(output / segment["revision_metadata"], metadata)
        return metadata

    base = take(1, BASE)
    alt = take(1, ALT, alternate=BASE)
    tip = take(2, TIP, parent=BASE)
    write_json(root / "checkpoints/clip_0001.json", base)
    write_json(root / "checkpoints/clip_0002.json", tip)
    for branch, name in ((BRANCH, "960x544"), (EMPTY_BRANCH, "Empty")):
        folder = root / "branches" / branch
        write_json(folder / "branch.json", {"format": "h3_working_branch_v1", "run_name": RUN,
                   "id": branch, "name": name, "revision": "c" * 32,
                   "authoring": {"plan": {"seed": SEED, "prompt": "PRIVATE authoring"}}})
    # Backslashes intentionally retained to protect historical metadata bytes.
    assigned = json.loads(json.dumps(base))
    assigned["segment"]["checkpoint"] = assigned["segment"]["checkpoint"].replace("/", "\\")
    write_json(root / "branches" / BRANCH / "checkpoints/clip_0001.json", assigned)
    write_json(root / "branches" / BRANCH / "editorial.json", {
        "run_name": RUN, "final_cut": {"1": {"revision": ALT, "base_revision": BASE,
            "revision_metadata": alt["segment"]["revision_metadata"]}}})
    chapter = root / "chapters/01_first"
    write_json(chapter / "manifests" / ("d" * 32 + ".json"), {
        "run_name": RUN, "segments": [base["segment"], tip["segment"]], "sealed": True})
    write_json(chapter / "retired_manifests" / ("e" * 32 + ".json"), {
        "run_name": RUN, "segments": [base["segment"]]})
    profile = "chapters/01_first/upscaled/hq"
    processing = {"format": "h3_chain_upscale_segment_v1", "run_name": RUN, "profile": "hq",
        "profile_config": {"backend": "pixel", "recipe": {}}, "segment": {
            **base["segment"], "index": 5, "revision": PASS, "source_revision": ALT,
            "source_checkpoint_sha256": alt["segment"]["checkpoint_sha256"],
            "latent_saved": False, "latent_layout": "omitted", "context_steps": 0,
            "segment": artifact(profile + "/segments/clip_0005." + PASS + ".mp4"),
            "generated_audio": artifact(profile + "/audio/clip_0005." + PASS + ".wav"),
            "checkpoint": artifact(profile + "/checkpoints/clip_0005." + PASS + ".safetensors", b"MARKER ONLY"),
            "revision_metadata": address(profile + "/checkpoints/clip_0005." + PASS + ".json")}}
    write_json(output / processing["segment"]["revision_metadata"], processing)
    write_json(root / profile / "checkpoints/clip_0005.json", processing)
    frame = "frames/export_2/frame_00000001.png"
    artifact(frame, b"manually edited png bytes")
    write_json(root / "frames/export_2/export.json", {"format": "h3_video_png_sequence_v1",
        "clips": [{"index": 5, "owner": "f" * 64, "source_revision": ALT,
                   "files": [{"file": "frame_00000001.png", "size": 3, "sha256": "0" * 64}]}]})
    write_json(root / "frames/export/export.json", {"format": "h3_video_png_sequence_v1",
               "clips": [], "deleted_scenes": [5]})
    write_json(root / "png_exports.json", {"format": "h3_png_export_catalog_v1", "run_name": RUN,
               "directories": [address("frames/export"), address("frames/export_2"), "custom_png/outside_project"]})
    artifact("reference_cache/objects/" + "f" * 64 + ".safetensors")
    write_json(root / "reference_cache/scene_0001.test.json", {"format": "h3_ref_cache_v3", "objects": [{
        "path": address("reference_cache/objects/" + "f" * 64 + ".safetensors")}]})
    artifact("project_assets/face.png")
    artifact("custom_diagnostics/notes.txt", b"UNCLASSIFIED, NOT TRASH")
    write_json(root / "plan.json", {"width": 960, "height": 544, "seed": SEED, "prompt": "PRIVATE plan"})
    write_json(root / "manifest.json", {"run_name": RUN, "segments": [base["segment"], tip["segment"]]})
    return root


def snapshot(root):
    return {p.relative_to(root).as_posix():
            (p.read_bytes() if p.is_file() and not p.is_symlink() else None, p.lstat().st_mtime_ns)
            for p in [root, *root.rglob("*")]}
