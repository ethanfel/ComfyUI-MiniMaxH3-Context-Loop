"""Real library handlers, temporary projects and CPU-only ComfyUI stubs."""
import asyncio
import json
import pathlib
import runpy
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
chain = runpy.run_path(str(ROOT / "tests/_chapter_delivery_unit_test.py"))["chain"]


class Request:
    def __init__(self, body=None, **query):
        self.body, self.query, self.headers = body, query, {}
        self.method = "GET" if body is None else "POST"
    async def json(self):
        return self.body


async def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        chain._input_root = lambda: str(root / "input")
        chain._output_root = lambda: str(root / "output")
        catalog = json.loads((await chain._project_asset_catalog(Request(project="film", create="false"))).text)
        assert catalog["library_revision"] == "empty" and not list(root.rglob("catalog.json"))
        command = {"command_version": 1, "operation_id": uuid.uuid4().hex,
                   "base_revision": "empty", "action": "folder_create", "project": "film", "name": "Cast"}
        result = await chain._project_asset_library(Request(command))
        assert result.status == 200, result.text
        saved = json.loads(result.text)
        assert saved["catalog"]["folders"][0]["name"] == "Cast"
        assert json.loads((await chain._project_asset_library(Request(command))).text)["replayed"]
        stale = await chain._project_asset_library(Request({**command, "operation_id": uuid.uuid4().hex}))
        assert stale.status == 409, stale.text
        module = sys.modules[chain.ProjectAssetStore.__module__]
        atomic = module._atomic_json
        def crash(path, value):
            atomic(path, value)
            if pathlib.Path(path) == root / "input/h3_projects/film/catalog.json":
                raise OSError("Simulated interrupted response")
        second = {**command, "operation_id": uuid.uuid4().hex, "name": "Places", "base_revision": saved["catalog"]["library_revision"]}
        module._atomic_json = crash
        try:
            failure = await chain._project_asset_library(Request(second))
            assert failure.status == 500, failure.text
        finally:
            module._atomic_json = atomic
        status = json.loads((await chain._project_asset_catalog(Request(project="film", operation_id=second["operation_id"]))).text)
        assert status["receipt"]["operation_id"] == second["operation_id"] and len(status["catalog"]["folders"]) == 2
        from PIL import Image
        image = root / "picture.png"; Image.new("RGB", (24, 16)).save(image)
        source = chain._project_asset_store().import_file("source", image)["asset"]
        preview_response = await chain._project_asset_catalog(Request(project="film", copy_source="source", copy_asset=source["id"], enabled="false"))
        assert preview_response.status == 200, preview_response.text
        preview = json.loads(preview_response.text)
        copy_request = {"command_version": 1, "project": "film", "action": "asset_copy", "operation_id": uuid.uuid4().hex,
            "source_project": "source", "asset_id": source["id"], "enabled": False, "folder_id": "",
            "base_revision": preview["base_revision"], "preview_revision": preview["preview_revision"]}
        copied = await chain._project_asset_library(Request(copy_request))
        assert copied.status == 200, copied.text
        assert json.loads(copied.text)["catalog"]["assets"][0]["source_origin"]["project"] == "source"
        assert json.loads((await chain._project_asset_library(Request(copy_request))).text)["replayed"]
        parent_id = json.loads(copied.text)["catalog"]["assets"][0]["id"]
        image_edit = {"crop": {"x": 0, "y": 0, "width": 8, "height": 8}, "target": {"width": 4, "height": 4}, "resample": "nearest", "tag": "small_picture", "folder_id": ""}
        image_info = await chain._project_asset_catalog(Request(project="film", image_asset=parent_id, image_edit=json.dumps(image_edit)))
        assert image_info.status == 200, image_info.text
        image_info = json.loads(image_info.text)
        assert image_info["source"] == {"width": 24, "height": 16}
        image_request = {"command_version": 1, "project": "film", "action": "asset_derive", "operation_id": uuid.uuid4().hex, "asset_id": parent_id,
            "base_revision": image_info["base_revision"], "preview_revision": image_info["preview_revision"], **image_edit}
        variant = await chain._project_asset_library(Request(image_request))
        assert variant.status == 200, variant.text
        assert json.loads(variant.text)["catalog"]["assets"][-1]["parent_asset_id"] == parent_id
        assert json.loads((await chain._project_asset_library(Request(image_request))).text)["replayed"]
        chain.claim_project_ownership(str(root / "output"), "film", "workflow-owner-a-1234567890", "Owner")
        denied = await chain._project_asset_library(Request({**command, "operation_id": uuid.uuid4().hex}))
        assert denied.status == 423, denied.text
        assert (await chain._project_asset_library(Request({**copy_request, "operation_id": uuid.uuid4().hex}))).status == 423
        assert (await chain._project_asset_library(Request({**image_request, "operation_id": uuid.uuid4().hex}))).status == 423
        assert len(chain._project_asset_store().load("film")["folders"]) == 2
    print("Asset library HTTP: read without creation, native commands, receipts, 409/500 recovery and 423 ownership passed")


asyncio.run(main())
