#!/usr/bin/env python3
"""Project asset tag-type, tag prefix, and LLM description checks."""

import asyncio
import base64
import importlib.util
import pathlib
import sys
import tempfile
import types
import wave

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
sys.modules.setdefault("folder_paths", folder_paths)

from project_assets import ProjectAssetStore, normalize_asset_tag_type  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "h3_prompt_optimizer_description_unit", ROOT / "prompt_optimizer.py")
optimizer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = optimizer
spec.loader.exec_module(optimizer)


def rejected(function, *args):
    try:
        function(*args)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("Expected ValueError")


def store_checks(root):
    input_root = root / "input"
    loose = input_root / "loose"
    loose.mkdir(parents=True)
    picture = loose / "alice.png"
    Image.new("RGB", (16, 16), (200, 10, 10)).save(picture)
    store = ProjectAssetStore(str(input_root), str(root / "output"))
    asset_id = store.import_file(
        "episode_1", picture, role="picture", tag="alice")["asset"]["id"]
    revision = store.load("episode_1")["revision"]

    assert normalize_asset_tag_type(" Char ") == "char"
    assert normalize_asset_tag_type("Hero Prop!") == "hero-prop"
    assert normalize_asset_tag_type("") == ""
    assert "begin with a letter" in rejected(normalize_asset_tag_type, "9x")

    entry = store.update("episode_1", asset_id, {"tag_type": "char"})["asset"]
    assert entry["tag_type"] == "char" and entry["tag"] == "alice"
    entry = store.update(
        "episode_1", asset_id, {"tag_type_prefix": True})["asset"]
    assert entry["tag"] == "char_alice"
    # Switching type swaps the prefix instead of stacking it.
    entry = store.update("episode_1", asset_id, {"tag_type": "style"})["asset"]
    assert entry["tag"] == "style_alice"
    # A typed tag keeps the prefix whether or not the user typed it.
    assert store.update("episode_1", asset_id, {"tag": "bob"})[
        "asset"]["tag"] == "style_bob"
    assert store.update("episode_1", asset_id, {"tag": "style_bob"})[
        "asset"]["tag"] == "style_bob"
    entry = store.update(
        "episode_1", asset_id, {"tag_type_prefix": False})["asset"]
    assert entry["tag"] == "bob" and "tag_type_prefix" not in entry
    entry = store.update("episode_1", asset_id, {"tag_type": ""})["asset"]
    assert "tag_type" not in entry

    entry = store.update("episode_1", asset_id, {
        "description": "Red square.\r\nFlat color."})["asset"]
    assert entry["description"] == "Red square.\nFlat color."
    # The description is persisted with the asset in the project catalog.
    reloaded = ProjectAssetStore(str(input_root), str(root / "output"))
    stored = next(item for item in reloaded.public_catalog("episode_1")[
        "assets"] if item["id"] == asset_id)
    assert stored["description"] == "Red square.\nFlat color."
    # Description and tag-type are notes; they never change the reference
    # fingerprint the way options do (tag renames still do).
    store.update("episode_1", asset_id, {"tag": "alice"})
    assert store.load("episode_1")["revision"] == revision
    entry = store.update("episode_1", asset_id, {"description": "  "})["asset"]
    assert "description" not in entry
    entry = store.update(
        "episode_1", asset_id, {"subject": "  the red\n umbrella "})["asset"]
    assert entry["subject"] == "the red umbrella"
    assert "subject" not in store.update(
        "episode_1", asset_id, {"subject": ""})["asset"]

    second = store.import_file(
        "episode_1", picture, role="picture", tag="char_alice")["asset"]["id"]
    store.update("episode_1", second, {"tag_type": "char"})
    assert "already uses @char_alice" in rejected(
        store.update, "episode_1", asset_id,
        {"tag_type": "char", "tag_type_prefix": True})

    # A cross-project import keeps notes without doubling the prefix.
    store.update("episode_1", second, {
        "tag_type_prefix": True, "description": "Hero.",
        "subject": "the scarf"})
    imported = store.import_project_asset(
        "episode_2", "episode_1", second)["asset"]
    assert imported["tag"] == "char_alice"
    assert imported["tag_type"] == "char"
    assert imported["tag_type_prefix"] is True
    assert imported["description"] == "Hero."
    assert imported["subject"] == "the scarf"
    return picture


def describe_checks(root, picture):
    for tag_type, needle in (
            ("char", "Ignore every prop"), ("scene", "Ignore all characters"),
            ("object", "Describe only the specific prop"),
            ("style", "not what it depicts")):
        assert needle in optimizer.asset_describe_instruction(tag_type, "image")
    assert "character voice" in optimizer.asset_describe_instruction(
        "char", "audio")
    custom = optimizer.asset_describe_instruction("vehicle", "image", "car")
    assert "Category: vehicle" in custom and "@car" in custom
    seeded = optimizer.asset_describe_instruction(
        "object", "image", subject="the brass watch")
    assert "The subject to describe is: the brass watch." in seeded
    assert "confirmed it is present" in seeded
    # No "not visible" escape hatch for weak vision models to take.
    assert "not visible" not in seeded

    parts = optimizer.asset_describe_media_parts(
        "image", str(picture), "openai")
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert optimizer.asset_describe_media_parts(
        "image", str(picture), "gemini")[0]["inlineData"]["mimeType"] \
        == "image/png"
    video = root / "clip.mp4"
    video.write_bytes(b"not really a video")
    still = optimizer.asset_describe_media_parts(
        "video", str(video), "responses", lambda: str(picture))
    assert still[0]["type"] == "input_image"
    inline = optimizer.asset_describe_media_parts(
        "video", str(video), "gemini", lambda: str(picture))
    assert inline[0]["inlineData"]["mimeType"] == "video/mp4"
    audio = root / "voice.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 80)
    sound = optimizer.asset_describe_media_parts(
        "audio", str(audio), "openai")[0]["input_audio"]
    assert sound["format"] == "wav" and base64.b64decode(sound["data"])
    assert "Gemini Native" in rejected(
        optimizer.asset_describe_media_parts, "audio", str(audio),
        "responses")

    calls = []

    def fake_call(*args):
        calls.append(args)
        return "A red square."

    original = optimizer.call_direct_optimizer
    optimizer.call_direct_optimizer = fake_call
    try:
        result = asyncio.run(optimizer.describe_asset_payload(
            {"api_url": "https://api.openai.com/v1", "model": "m",
             "notes": "Not visible.", "subject": "the red block"},
            {"kind": "image", "tag": "alice"}, str(picture),
            tag_type="object"))
    finally:
        optimizer.call_direct_optimizer = original
    assert result["description"] == "A red square."
    (_url, _key, _model, api_format, instruction, media, system) = calls[0]
    assert api_format == "openai" and len(media) == 1
    # A previous description is never fed back as a hint.
    assert "Category: object" in instruction
    assert "Not visible." not in instruction
    assert "the red block" in instruction
    assert system == optimizer.ASSET_DESCRIBE_SYSTEM


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        picture = store_checks(root)
        describe_checks(root, picture)
    print("H3 Project Asset descriptions: tag-type, prefix naming, subject, "
          "and Direct API describe requests pass")


if __name__ == "__main__":
    main()
