"""Library commands use native metadata/media semantics with durable receipts."""
import pathlib
import json
import tempfile
import uuid

from PIL import Image
import project_assets as native
from asset_library import command_library, inspect_library


def request(store, action, **fields):
    return {"command_version": 1, "operation_id": uuid.uuid4().hex,
            "action": action, "base_revision": store.public_catalog("film", create=False)["library_revision"], **fields}


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    loose = root / "input" / "loose.png"; loose.parent.mkdir()
    Image.new("RGB", (24, 16), (80, 120, 160)).save(loose)
    store = native.ProjectAssetStore(str(root / "input"), str(root / "output"))
    fresh = request(store, "folder_create", name="Cast")
    assert fresh["base_revision"] == "empty" and not list(root.rglob("catalog.json"))
    result = command_library(store, "film", fresh)
    folder = result["receipt"]["created_folders"][0]
    assert result["catalog"]["folders"][0]["id"] == folder
    assert "library_receipts" not in result["catalog"]
    assert command_library(store, "film", fresh)["replayed"]
    original = store.import_file("film", loose, tag="hero")["asset"]
    # A legacy import retains the earlier command receipt.
    assert inspect_library(store, "film", fresh["operation_id"])["receipt"]
    move = request(store, "asset_update", asset_id=original["id"], changes={"folder_id": folder})
    command_library(store, "film", move)
    before = store.public_catalog("film")
    stale = request(store, "asset_duplicate", asset_id=original["id"])
    command_library(store, "film", request(store, "folder_update", folder_id=folder, changes={"color": "#336699"}))
    after = store.public_catalog("film")
    assert after["revision"] == before["revision"] and after["library_revision"] != before["library_revision"]
    try:
        command_library(store, "film", stale)
        raise AssertionError("Folder-only change did not invalidate review")
    except native.ProjectAssetConflictError:
        pass
    # Stop after authoritative catalog commit but before a response/mirror.
    duplicate = request(store, "asset_duplicate", asset_id=original["id"])
    atomic = native._atomic_json
    def crash(path, value):
        atomic(path, value)
        if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
            raise OSError("Lost catalog acknowledgement")
    native._atomic_json = crash
    try:
        command_library(store, "film", duplicate)
        raise AssertionError("Expected interrupted response")
    except OSError:
        pass
    finally:
        native._atomic_json = atomic
    restarted = native.ProjectAssetStore(str(root / "input"), str(root / "output"))
    result = command_library(restarted, "film", duplicate)
    assert result["replayed"] and len(result["catalog"]["assets"]) == 2
    clone = next(item for item in result["catalog"]["assets"] if item["id"] != original["id"])
    assert clone["parent_asset_id"] == original["id"] and clone["relative_path"] == original["relative_path"]
    try:
        command_library(store, "film", {**duplicate, "tag": "Different"})
        raise AssertionError("Changed retry accepted")
    except ValueError:
        pass
    command_library(store, "film", request(store, "asset_reorder", asset_ids=[clone["id"], original["id"]]))
    assert store.load("film")["assets"][0]["id"] == clone["id"]
    command_library(store, "film", request(store, "folder_delete", folder_id=folder))
    assert all(not item["folder_id"] for item in store.load("film")["assets"])
    command_library(store, "film", request(store, "asset_delete", asset_id=original["id"]))
    owned_path = pathlib.Path(store.asset("film", clone["id"])[1])
    assert owned_path.exists(), "Deleting one card removed another card's shared media"
    removal = request(store, "asset_delete", asset_id=clone["id"])
    cleanup = store._delete_asset_files
    store._delete_asset_files = lambda *args: (_ for _ in ()).throw(OSError("Interrupted cleanup"))
    try:
        command_library(store, "film", removal)
        raise AssertionError("Expected interrupted cleanup")
    except OSError:
        pass
    finally:
        store._delete_asset_files = cleanup
    assert owned_path.exists() and store.load("film")["assets"] == []
    assert inspect_library(restarted, "film", removal["operation_id"])["receipt"]
    assert not owned_path.exists(), "Status confirmation left committed deletion cleanup unfinished"
    assert command_library(restarted, "film", removal)["replayed"]
    assert not owned_path.exists() and loose.exists()
    # A mutation between outer review validation and a native method's reload
    # must still fail before committing, as can happen in another process.
    value = store.create_folder("film", "Locations")["folder"]
    pending = request(store, "folder_update", folder_id=value["id"], changes={"name": "Stale rename"})
    load, calls = store.load, [0]
    def racing_load(project, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            restarted.update_folder(project, value["id"], {"name": "New native name"})
        return load(project, **kwargs)
    store.load = racing_load
    try:
        command_library(store, "film", pending)
        raise AssertionError("Intervening native reload was overwritten")
    except native.ProjectAssetConflictError:
        pass
    finally:
        store.load = load
    assert store.load("film")["folders"][0]["name"] == "New native name"
    # A legacy/public snapshot cannot discard retained operation identities.
    store._save_catalog(store.public_catalog("film", create=False))
    assert inspect_library(store, "film", fresh["operation_id"])["receipt"]
    # Even a copied catalog cannot authorize the source project's operation.
    copied = root / "input/h3_projects/other/catalog.json"
    copied.parent.mkdir(parents=True)
    copied.write_text(json.dumps(store.load("film")))
    assert inspect_library(store, "other", fresh["operation_id"])["receipt"] is None
    try:
        command_library(store, "other", fresh)
        raise AssertionError("Copied source-project receipt was replayed")
    except ValueError:
        pass
print("Asset library: folders, membership, ordering, native duplication/deletion, receipts, restart and stale edits passed")
