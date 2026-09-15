#!/usr/bin/env python3
"""Project Asset Carousel storage, backup, and catalog checks."""

import pathlib
import tempfile
import wave

from PIL import Image

from project_assets import PROJECT_ASSET_FORMAT, ProjectAssetStore


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        input_root = root / "input"
        output_root = root / "output"
        loose = input_root / "loose"
        loose.mkdir(parents=True)
        picture = loose / "Hero Face.png"
        Image.new("RGB", (64, 48), (120, 80, 40)).save(picture)
        nested = loose / "chapter_2" / "hallway.png"
        nested.parent.mkdir()
        Image.new("RGB", (32, 32), (20, 40, 60)).save(nested)
        clipspace = input_root / "Clipspace" / "clipboard.png"
        clipspace.parent.mkdir()
        Image.new("RGB", (32, 32), (80, 60, 40)).save(clipspace)
        audio = loose / "voice.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x00\x00" * 2400)

        store = ProjectAssetStore(str(input_root), str(output_root))
        first = store.import_file(
            "episode_1", picture, role="picture", tag="hero")
        second = store.import_file(
            "episode_1", audio, role="source_track", tag="music")
        assert first["catalog"]["format"] == PROJECT_ASSET_FORMAT
        assert len(second["catalog"]["assets"]) == 2
        assert pathlib.Path(store.asset(
            "episode_1", first["asset"]["id"])[1]).is_file()
        thumbnail = pathlib.Path(store.ensure_thumbnail(
            "episode_1", first["asset"]["id"]))
        assert thumbnail.is_file()
        first_project_copy = pathlib.Path(store.asset(
            "episode_1", first["asset"]["id"])[1])
        first_backup_copy = (
            output_root / "h3_chains" / "episode_1" / "project_assets" /
            first["asset"]["relative_path"])
        with Image.open(thumbnail) as image:
            assert image.width <= 320 and image.height <= 180
        assert (output_root / "h3_chains" / "episode_1" /
                "project_assets" / "catalog.json").is_file()
        assert (output_root / "h3_chains" / "episode_1" /
                "project_assets" / first["asset"]["relative_path"]).is_file()
        listed = store.input_media()
        assert {item["path"] for item in listed} == {
            "loose/Hero Face.png", "loose/chapter_2/hallway.png",
            "loose/voice.wav"}
        assert all(not item["path"].startswith("h3_projects/")
                   for item in listed)
        assert all(not item["path"].lower().startswith("clipspace/")
                   for item in listed)

        store.public_catalog("empty_project")
        project_catalogs = store.project_catalogs()
        project_summaries = {
            item["project"]: item for item in project_catalogs}
        assert project_summaries["episode_1"]["asset_count"] == 2
        assert project_summaries["episode_1"]["unassigned_count"] == 0
        assert project_summaries["episode_1"]["folder_count"] == 0
        assert project_summaries["empty_project"]["asset_count"] == 0
        assert [item["project"] for item in store.project_catalogs("empty")] == [
            "empty_project"]

        changed = store.update(
            "episode_1", first["asset"]["id"],
            {"role": "semantic_anchor", "tag": "hero_semantic"})
        assert changed["asset"]["role"] == "semantic_anchor"
        assert changed["asset"]["tag"] == "hero_semantic"
        assert changed["catalog"]["revision"] != first["catalog"]["revision"]
        revision_before_lyrics = changed["catalog"]["revision"]
        lyrics = store.update(
            "episode_1", second["asset"]["id"],
            {"lyrics": "First line\r\nSecond line"})
        assert lyrics["asset"]["lyrics"] == "First line\nSecond line"
        assert lyrics["catalog"]["revision"] == revision_before_lyrics
        assert store.load("episode_1")["assets"][1]["lyrics"] == (
            "First line\nSecond line")
        backup_catalog = output_root / "h3_chains" / "episode_1" / (
            "project_assets/catalog.json")
        assert "First line\\nSecond line" in backup_catalog.read_text(
            encoding="utf-8")
        try:
            store.update(
                "episode_1", first["asset"]["id"], {"lyrics": "No"})
        except ValueError as exc:
            assert "only be attached to audio assets" in str(exc)
        else:
            raise AssertionError("Lyrics were attached to a non-audio asset")

        projects = store.projects("hero_semantic", exclude_project="episode_2")
        assert len(projects) == 1
        assert projects[0]["project"] == "episode_1"
        assert [item["id"] for item in projects[0]["assets"]] == [
            first["asset"]["id"]]
        copied_picture = store.import_project_asset(
            "episode_2", "episode_1", first["asset"]["id"])
        assert copied_picture["asset"]["role"] == "semantic_anchor"
        assert copied_picture["asset"]["tag"] == "hero_semantic"
        assert copied_picture["asset"]["source_kind"] == "project"
        assert copied_picture["source_project"] == "episode_1"
        assert pathlib.Path(store.asset(
            "episode_2", copied_picture["asset"]["id"])[1]) != (
                first_project_copy)
        copied_audio = store.import_project_asset(
            "episode_2", "episode_1", second["asset"]["id"])
        assert copied_audio["asset"]["lyrics"] == "First line\nSecond line"
        assert copied_audio["asset"]["role"] == "source_track"

        cast = store.create_folder("episode_1", "Cast")["folder"]
        store.update(
            "episode_1", first["asset"]["id"], {"folder_id": cast["id"]})
        duplicated_card = store.duplicate(
            "episode_1", first["asset"]["id"])["asset"]
        source_with_slot = store.sync_reference_slots("episode_1", [{
            "kind": "image", "role": "picture", "tag": "future_prop",
            "content_hash": "future-prop-content", "options": {},
        }])
        source_render = output_root / "h3_chains" / "episode_1" / (
            "scene_0001/generated.mp4")
        source_render.parent.mkdir(parents=True)
        source_render.write_bytes(b"generated render must not be copied")
        upload_scratch = input_root / "h3_projects" / "episode_1" / (
            ".uploads/stale-upload.tmp")
        upload_scratch.write_bytes(b"upload scratch")

        project_copy = store.duplicate_project(
            "episode_1", "episode_1_asset_copy")
        copied_catalog = project_copy["catalog"]
        assert project_copy["source_project"] == "episode_1"
        assert project_copy["target_project"] == "episode_1_asset_copy"
        assert project_copy["asset_count"] == 3
        assert project_copy["media_file_count"] == 2
        assert project_copy["render_outputs_copied"] is False
        assert copied_catalog["project"] == "episode_1_asset_copy"
        assert copied_catalog["revision"] == source_with_slot["revision"]
        assert [item["id"] for item in copied_catalog["assets"]] == [
            item["id"] for item in source_with_slot["assets"]]
        assert copied_catalog["folders"] == source_with_slot["folders"]
        assert copied_catalog["reference_slots"] == (
            source_with_slot["reference_slots"])
        assert copied_catalog["assets"][0]["folder_id"] == cast["id"]
        assert copied_catalog["assets"][1]["lyrics"] == (
            "First line\nSecond line")
        assert all(item["input_path"].startswith(
            "h3_projects/episode_1_asset_copy/")
            for item in copied_catalog["assets"])
        copied_project_media = pathlib.Path(store.asset(
            "episode_1_asset_copy", first["asset"]["id"])[1])
        copied_backup_media = output_root / "h3_chains" / (
            "episode_1_asset_copy/project_assets") / (
            first["asset"]["relative_path"])
        assert copied_project_media.is_file()
        assert copied_backup_media.is_file()
        assert copied_project_media.stat().st_ino != first_project_copy.stat().st_ino
        copied_run_items = {
            item.name for item in (output_root / "h3_chains" /
                                   "episode_1_asset_copy").iterdir()}
        assert copied_run_items == {"project_assets"}
        assert list((input_root / "h3_projects" / "episode_1_asset_copy" /
                     "previews").iterdir()) == []
        assert list((input_root / "h3_projects" / "episode_1_asset_copy" /
                     ".uploads").iterdir()) == []
        assert not (output_root / "h3_chains" / "episode_1_asset_copy" /
                    "scene_0001/generated.mp4").exists()
        try:
            store.duplicate_project("episode_1", "episode_1_asset_copy")
        except FileExistsError as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("An existing asset project was overwritten")
        try:
            store.duplicate_project("episode_1", "episode_1")
        except ValueError as exc:
            assert "different Run name" in str(exc)
        else:
            raise AssertionError("An asset project duplicated over itself")
        reserved_output = output_root / "h3_chains" / "reserved_run"
        reserved_output.mkdir()
        try:
            store.duplicate_project("episode_1", "reserved_run")
        except FileExistsError as exc:
            assert "renders and checkpoints remain separate" in str(exc)
        else:
            raise AssertionError("An existing output Run received asset copies")
        assert not (input_root / "h3_projects" / "reserved_run").exists()
        store.delete("episode_1", duplicated_card["id"])

        assert all(item["project"] != "episode_1"
                   for item in store.projects(exclude_project="episode_1"))
        try:
            store.import_project_asset(
                "episode_1", "episode_1", first["asset"]["id"])
        except ValueError as exc:
            assert "Use Duplicate" in str(exc)
        else:
            raise AssertionError("A same-project import bypassed Duplicate")

        project_slot_catalog = store.sync_reference_slots(
            "episode_binding", [{
                "kind": "image", "role": "picture", "tag": "bound_hero",
                "content_hash": "project-copy", "options": {},
            }])
        project_bound = store.import_project_asset(
            "episode_binding", "episode_1", first["asset"]["id"],
            slot_id=project_slot_catalog["reference_slots"][0]["id"])
        assert project_bound["asset"]["tag"] == "bound_hero"
        assert project_bound["asset"]["role"] == "picture"
        assert project_bound["catalog"]["reference_slots"] == []

        reordered = store.reorder("episode_1", [
            second["asset"]["id"], first["asset"]["id"],
        ])
        assert [item["id"] for item in reordered["catalog"]["assets"]] == [
            second["asset"]["id"], first["asset"]["id"],
        ]
        try:
            store.reorder("episode_1", [first["asset"]["id"]])
        except ValueError as exc:
            assert "every current asset ID exactly once" in str(exc)
        else:
            raise AssertionError("Incomplete project asset order was accepted")
        deleted = store.delete("episode_1", first["asset"]["id"])
        assert deleted["asset"]["tag"] == "hero_semantic"
        assert [item["id"] for item in deleted["catalog"]["assets"]] == [
            second["asset"]["id"]]
        assert not first_project_copy.exists()
        assert not first_backup_copy.exists()
        assert not thumbnail.exists()
        assert picture.is_file()
        assert copied_project_media.is_file()
        assert copied_backup_media.is_file()

        templated = store.sync_reference_slots("episode_migration", [{
            "kind": "image",
            "role": "picture",
            "tag": "legacy_hero",
            "content_hash": "abc123",
            "options": {},
        }])
        assert templated["assets"] == []
        assert len(templated["reference_slots"]) == 1
        slot = templated["reference_slots"][0]
        assert slot["tag"] == "legacy_hero"
        assert slot["available"] is True
        assert list((input_root / "h3_projects" / "episode_migration" /
                     "images").glob("*.png")) == []
        stale = store.sync_reference_slots("episode_migration", [])
        assert stale["reference_slots"][0]["available"] is False
        refreshed = store.sync_reference_slots("episode_migration", [{
            "kind": "image", "role": "picture", "tag": "legacy_hero",
            "content_hash": "abc123", "options": {},
        }])
        bound = store.bind_reference_slot(
            "episode_migration", refreshed["reference_slots"][0]["id"],
            picture, source_kind="input")
        assert bound["asset"]["tag"] == "legacy_hero"
        assert bound["catalog"]["reference_slots"] == []
        assert len(bound["catalog"]["assets"]) == 1

        # Review Gate's frame-capture path re-uses a tag as an "updated take"
        # of the same subject: @char_bob -> @char_bob-v1 -> @char_bob-v2.
        capture_1 = store.import_file(
            "capture_family", picture, role="picture", tag="char_bob",
            source_kind="frame_capture")
        assert capture_1["asset"]["tag"] == "char_bob"
        capture_2 = store.import_file(
            "capture_family", picture, role="picture", tag="char_bob",
            source_kind="frame_capture")
        assert capture_2["asset"]["tag"] == "char_bob-v1"
        capture_3 = store.import_file(
            "capture_family", picture, role="picture", tag="char_bob",
            source_kind="frame_capture")
        assert capture_3["asset"]["tag"] == "char_bob-v2"
        # An unrelated tag that merely shares the "char_bob" prefix must
        # never be swept into the family's ordinal scan.
        unrelated = store.import_file(
            "capture_family", nested, role="picture", tag="char_bob_house",
            source_kind="frame_capture")
        assert unrelated["asset"]["tag"] == "char_bob_house"

    print("H3 Project Asset Store: import, whole-project duplication, cross-project copy, backup, metadata sync, binding, listing, ordering, deletion, and edit pass")


if __name__ == "__main__":
    main()
