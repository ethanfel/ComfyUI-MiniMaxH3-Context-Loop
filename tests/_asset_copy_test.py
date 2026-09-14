"""Temporary real catalogs/media exercise atomic group publication and recovery."""
import copy
import pathlib
import tempfile
import uuid
import wave

from PIL import Image
import project_assets as native
import asset_copy
from asset_library import command_library, inspect_library


def request(store, source, asset, *, enabled=True, target="film"):
    review = asset_copy.preview_copy(store, target, source, asset, enabled)
    return {"command_version": 1, "project": target, "action": "asset_copy",
        "operation_id": uuid.uuid4().hex, "source_project": source, "asset_id": asset,
        "enabled": enabled, "folder_id": "", "base_revision": review["base_revision"],
        "preview_revision": review["preview_revision"]}


def fails(kind, fn):
    try:
        fn()
        raise AssertionError("Expected %s" % kind)
    except kind:
        pass


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    image = root / "hero.png"
    Image.new("RGB", (24, 16), (80, 120, 160)).save(image)
    audio = root / "song.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 8000)
    store = native.ProjectAssetStore(str(root / "input"), str(root / "output"))
    original = store.import_file("source", image, tag="hero")["asset"]
    derived = store.duplicate("source", original["id"])["asset"]
    mix = store.import_file("source", audio, tag="score", role="source_track")["asset"]
    vocals = store.import_file("source", audio, tag="vocals", role="audio_reference")["asset"]
    store.update("source", mix["id"], {"lyrics": "Full lyrics", "options": {"audio_tracks": {"full_mix": mix["id"], "vocals": vocals["id"], "instrumental": ""}}})
    store.update("source", vocals["id"], {"enabled": False, "lyrics": "Stem lyrics"})
    before = copy.deepcopy(store.load("source"))
    group = request(store, "source", mix["id"])
    atomic, commits = native._atomic_json, []
    def record(path, value):
        if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
            commits.append(copy.deepcopy(value))
        atomic(path, value)
    native._atomic_json = record
    try:
        result = command_library(store, "film", group)
    finally:
        native._atomic_json = atomic
    assert len(commits) == 1 and len(commits[0]["assets"]) == 2
    assert group["operation_id"] in commits[0]["library_receipts"]
    first, stem = result["catalog"]["assets"]
    assert first["options"]["audio_tracks"] == {"full_mix": first["id"], "vocals": stem["id"], "instrumental": ""}
    assert first["lyrics"] == "Full lyrics" and stem["lyrics"] == "Stem lyrics" and not stem["enabled"]
    assert first["source_origin"]["asset_id"] == mix["id"] and stem["source_origin"]["asset_id"] == vocals["id"]
    assert store.load("source") == before
    assert command_library(store, "film", group)["replayed"]
    fails(ValueError, lambda: command_library(store, "film", {**group, "enabled": False}))
    assert not asset_copy.preview_copy(store, "film", "source", mix["id"])["copyable"]
    disabled = command_library(store, "film", request(store, "source", mix["id"], enabled=False))
    assert sum(item["role"] == "source_track" and item["enabled"] for item in disabled["catalog"]["assets"]) == 1
    picture = command_library(store, "film", request(store, "source", derived["id"]))
    entry = picture["catalog"]["assets"][-1]
    assert entry["source_origin"]["parent_asset_id"] == original["id"] and "parent_asset_id" not in entry
    stale = request(store, "source", original["id"])
    store.create_folder("source", "Changed source")
    fails(native.ProjectAssetConflictError, lambda: command_library(store, "film", stale))
    # Resolve missing group dependencies before any destination publication.
    missing = request(store, "source", mix["id"], enabled=False, target="empty")
    media = pathlib.Path(store.asset("source", vocals["id"])[1]); saved_bytes = media.read_bytes(); media.unlink()
    fails(ValueError, lambda: command_library(store, "empty", missing))
    assert not (root / "input/h3_projects/empty/catalog.json").exists()
    media.write_bytes(saved_bytes)
    # A crash after preparation retains frozen media and the exact request.
    interrupted = request(store, "source", original["id"])
    publish = asset_copy._publish_files
    asset_copy._publish_files = lambda *args: (_ for _ in ()).throw(OSError("Disconnected before publication"))
    try:
        fails(OSError, lambda: command_library(store, "film", interrupted))
    finally:
        asset_copy._publish_files = publish
    restarted = native.ProjectAssetStore(store.input_root, store.output_root)
    pending = inspect_library(restarted, "film", interrupted["operation_id"])["pending_copy"]
    assert pending["phase"] == "prepared" and pending["request"] == interrupted
    assert restarted.public_catalog("film")["library_pending_copies"][0]["operation_id"] == interrupted["operation_id"]
    store.update("source", original["id"], {"tag": "Changed_after_preparation"})
    result = command_library(restarted, "film", interrupted)
    assert not result["replayed"] and not result["catalog"]["library_pending_copies"]
    assert len(result["receipt"]["created_assets"]) == 1
    # Lost response after the atomic catalog is committed never copies twice.
    lost = request(store, "source", original["id"])
    def crash(path, value):
        atomic(path, value)
        if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
            raise OSError("Lost commit acknowledgement")
    native._atomic_json = crash
    try:
        fails(OSError, lambda: command_library(store, "film", lost))
    finally:
        native._atomic_json = atomic
    assert (pathlib.Path(asset_copy._directory(store, "film", lost["operation_id"])) / "stage").exists()
    assert inspect_library(restarted, "film", lost["operation_id"])["receipt"]
    assert not (pathlib.Path(asset_copy._directory(store, "film", lost["operation_id"])) / "stage").exists()
    result = command_library(restarted, "film", lost)
    assert result["replayed"] and len(result["receipt"]["created_assets"]) == 1
    # A competing catalog writer after file promotion wins; only our files
    # disappear, and the source and prior destination media remain intact.
    race = request(store, "source", original["id"])
    def raced(*args):
        publish(*args)
        restarted.create_folder("film", "Native concurrent folder")
    asset_copy._publish_files = raced
    try:
        fails(native.ProjectAssetConflictError, lambda: command_library(store, "film", race))
    finally:
        asset_copy._publish_files = publish
    assert not list((root / "input/h3_projects/film/images").glob(race["operation_id"] + "_*"))
    assert all(pathlib.Path(store.asset("film", item["id"])[1]).exists() for item in store.load("film")["assets"])
    assert inspect_library(store, "film", race["operation_id"])["pending_copy"] is None
    fails(ValueError, lambda: command_library(store, "other", group))
    # Existing bytes promoted before a server crash are reused on retry.
    promoted = request(store, "source", original["id"])
    def stop_after_files(*args):
        publish(*args)
        raise OSError("Server stopped after media promotion")
    asset_copy._publish_files = stop_after_files
    try:
        fails(OSError, lambda: command_library(store, "film", promoted))
    finally:
        asset_copy._publish_files = publish
    count = len(store.load("film")["assets"])
    assert command_library(restarted, "film", promoted)["receipt"]["created_assets"]
    assert len(store.load("film")["assets"]) == count + 1
    # In-place source changes cannot be copied under an earlier reviewed hash.
    drift = request(store, "source", original["id"], target="drift")
    source_path = pathlib.Path(store.asset("source", original["id"])[1])
    import os
    stat = source_path.stat(); original_bytes = source_path.read_bytes()
    changed_bytes = bytearray(original_bytes); changed_bytes[-1] ^= 1
    source_path.write_bytes(changed_bytes); os.utime(source_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    fails(native.ProjectAssetConflictError, lambda: command_library(store, "drift", drift))
    source_path.write_bytes(original_bytes)
    assert not (root / "input/h3_projects/drift/catalog.json").exists()
print("Cross-project copy: native groups/provenance, atomic publication, conflicts, frozen-stage and commit recovery passed")
