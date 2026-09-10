"""Real crop pixels, native geometry/provenance and interrupted image commands."""
import copy
import pathlib
import tempfile
import uuid

from PIL import Image
import project_assets as native
import asset_copy
from asset_image import inspect_image
from asset_library import command_library, inspect_library


def fails(kind, fn):
    try:
        fn()
        raise AssertionError("Expected %s" % kind)
    except kind:
        pass


def request(store, asset, **changes):
    edit = {"crop": {"x": 8, "y": 0, "width": 8, "height": 16},
        "target": {"width": 8, "height": 8}, "resample": "nearest", "tag": "blue_variant", "folder_id": "", **changes}
    preview = inspect_image(store, "film", asset, edit)
    return {"command_version": 1, "project": "film", "action": "asset_derive", "asset_id": asset,
        "operation_id": uuid.uuid4().hex, "base_revision": preview["base_revision"],
        "preview_revision": preview["preview_revision"], **edit}


with tempfile.TemporaryDirectory() as temporary:
    root = pathlib.Path(temporary)
    image = Image.new("RGBA", (24, 16), (255, 0, 0, 255))
    image.paste((0, 0, 255, 90), (8, 0, 16, 16))
    path = root / "source.png"; image.save(path)
    store = native.ProjectAssetStore(str(root / "input"), str(root / "output"))
    parent = store.import_file("film", path, role="semantic_anchor", tag="hero")["asset"]
    source_bytes = pathlib.Path(store.asset("film", parent["id"])[1]).read_bytes()
    before = copy.deepcopy(store.load("film"))
    info = inspect_image(store, "film", parent["id"])
    assert info["source"] == {"width": 24, "height": 16} and info["max_pixels"] == native.MAX_DERIVED_IMAGE_PIXELS
    command = request(store, parent["id"])
    atomic, commits = native._atomic_json, []
    def record(path, value):
        if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
            commits.append(copy.deepcopy(value))
        atomic(path, value)
    native._atomic_json = record
    try:
        result = command_library(store, "film", command)
    finally:
        native._atomic_json = atomic
    assert len(commits) == 1 and len(commits[0]["assets"]) == 2
    entry = result["catalog"]["assets"][-1]
    assert entry["parent_asset_id"] == parent["id"] and entry["role"] == "semantic_anchor"
    assert entry["transform"]["crop"] == command["crop"] and entry["transform"]["resample"] == "nearest"
    assert entry["transform"]["operation_id"] == command["operation_id"]
    with Image.open(store.asset("film", entry["id"])[1]) as output:
        assert output.size == (8, 8) and output.mode == "RGBA"
        assert set(output.getdata()) == {(0, 0, 255, 90)}, "Crop placement/resampling lost the selected pixels or alpha"
    assert store.load("film")["assets"][0] == before["assets"][0]
    assert pathlib.Path(store.asset("film", parent["id"])[1]).read_bytes() == source_bytes
    assert command_library(store, "film", command)["replayed"]
    fails(ValueError, lambda: command_library(store, "film", {**command, "tag": "different"}))
    fails(ValueError, lambda: request(store, parent["id"], crop={"x": 24, "y": 0, "width": 1, "height": 1}))
    fails(ValueError, lambda: request(store, parent["id"], crop={"x": 1.5, "y": 0, "width": 1, "height": 1}))
    fails(ValueError, lambda: request(store, parent["id"], target={"width": 100000, "height": 100000}))
    fails(ValueError, lambda: request(store, parent["id"], resample="unknown"))
    fails(ValueError, lambda: request(store, parent["id"], folder_id="missing"))
    stale = request(store, parent["id"]); store.create_folder("film", "New native folder")
    fails(native.ProjectAssetConflictError, lambda: command_library(store, "film", stale))
    # EXIF-oriented dimensions are also used by the crop/resize engine.
    rotated = root / "rotated.jpg"; exif = Image.Exif(); exif[274] = 6
    Image.new("RGB", (30, 10), "green").save(rotated, exif=exif)
    orientation = store.import_file("film", rotated)["asset"]
    assert inspect_image(store, "film", orientation["id"])["source"] == {"width": 10, "height": 30}
    oriented = request(store, orientation["id"], crop={"x": 0, "y": 10, "width": 10, "height": 20}, target={"width": 5, "height": 10})
    result = command_library(store, "film", oriented)
    assert result["catalog"]["assets"][-1]["transform"]["source"] == {"width": 10, "height": 30}
    # Persisted image work remains distinguishable from cross-project imports.
    paused = request(store, parent["id"])
    publish = asset_copy._publish_files
    asset_copy._publish_files = lambda *args: (_ for _ in ()).throw(OSError("Server interrupted"))
    try:
        fails(OSError, lambda: command_library(store, "film", paused))
    finally:
        asset_copy._publish_files = publish
    restarted = native.ProjectAssetStore(store.input_root, store.output_root)
    status = inspect_library(restarted, "film", paused["operation_id"])
    assert status["pending_copy"] is None and status["pending_operation"]["request"] == paused
    assert not status["catalog"]["library_pending_copies"]
    assert status["catalog"]["library_pending_operations"][0]["action"] == "asset_derive"
    assert command_library(restarted, "film", paused)["receipt"]["created_assets"]
    # A lost acknowledgement reconciles the original image and clears its stage.
    lost = request(store, parent["id"])
    def crash(path, value):
        atomic(path, value)
        if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
            raise OSError("Lost catalog response")
    native._atomic_json = crash
    try:
        fails(OSError, lambda: command_library(store, "film", lost))
    finally:
        native._atomic_json = atomic
    count = len(store.load("film")["assets"])
    assert inspect_library(restarted, "film", lost["operation_id"])["receipt"]
    assert not (pathlib.Path(asset_copy._directory(store, "film", lost["operation_id"])) / "stage").exists()
    assert command_library(restarted, "film", lost)["replayed"]
    assert len(store.load("film")["assets"]) == count
    # Retention limits reject before allocating another operation directory.
    operations = pathlib.Path(asset_copy._directory(store, "film", lost["operation_id"])).parent
    for number in range(1024):
        if len(list(operations.iterdir())) >= 1024:
            break
        (operations / format(number, "032x")).mkdir(exist_ok=True)
    limited = request(store, parent["id"])
    fails(ValueError, lambda: command_library(store, "film", limited))
    assert len(list(operations.iterdir())) == 1024
    assert command_library(store, "film", lost)["replayed"], "The retention limit blocked a committed receipt"
print("Image variants: native crop pixels/alpha/orientation, one catalog commit, provenance, stale review and restart recovery passed")
