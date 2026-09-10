"""Real routes with CPU-only Comfy stubs, ownership and named-branch scope."""
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
        self.query = query
        self.method = "GET" if body is None else "POST"
        self.headers = {}
        self.body = body
    async def json(self):
        return self.body

async def main():
    with tempfile.TemporaryDirectory() as temporary:
        chain._output_root = lambda: temporary
        branch = "1" * 32
        path = pathlib.Path(temporary, "h3_chains", "film", "branches", branch)
        path.mkdir(parents=True); (path / "branch.json").write_text("{}")
        read = await chain._get_prompt_history(Request(run_name="film", scene_id="one", branch_id=branch))
        state = json.loads(read.text)
        assert state["working_branch_id"] == branch and state["revisions"] == []
        body = dict(command_version=1, action="save", operation_id=uuid.uuid4().hex,
                    base_revision=state["history_revision"], run_name="film", scene_id="one", prompt="Named branch draft")
        result = await chain._update_prompt_history(Request(body, branch_id=branch))
        assert result.status == 200, result.text
        saved = json.loads(result.text)
        assert saved["history"]["working_branch_id"] == branch
        assert json.loads((await chain._get_prompt_history(Request(run_name="film", scene_id="one"))).text)["revisions"] == []
        revision = saved["history"]["active_revision"]
        content = json.loads((await chain._get_prompt_history(Request(run_name="film", scene_id="one", branch_id=branch, revision=revision))).text)
        assert content["prompt"] == body["prompt"] and content["working_branch_id"] == branch
        retry = await chain._update_prompt_history(Request(body, branch_id=branch))
        assert json.loads(retry.text)["replayed"]
        status = await chain._get_prompt_history(Request(run_name="film", scene_id="one", branch_id=branch, operation_id=body["operation_id"]))
        assert json.loads(status.text)["receipt"]["operation_id"] == body["operation_id"]
        stale = await chain._update_prompt_history(Request({**body, "operation_id": uuid.uuid4().hex}, branch_id=branch))
        assert stale.status == 409, stale.text
        # A write may have committed before an I/O error. Report 500, retain
        # its receipt, and allow a status read to finish journal recovery.
        module = sys.modules[chain.PromptHistoryStore.__module__]
        original_write = module._atomic_json
        interrupted = {**body, "operation_id": uuid.uuid4().hex,
                       "base_revision": saved["history"]["history_revision"],
                       "prompt": "Recovered through API"}
        def interrupted_write(path, value):
            original_write(path, value)
            if path.endswith(revision + ".json"):
                raise OSError("Interrupted prompt write")
        module._atomic_json = interrupted_write
        try:
            result = await chain._update_prompt_history(Request(interrupted, branch_id=branch))
            assert result.status == 500, result.text
        finally:
            module._atomic_json = original_write
        recovered = json.loads((await chain._get_prompt_history(Request(
            run_name="film", scene_id="one", branch_id=branch,
            operation_id=interrupted["operation_id"]))).text)
        assert recovered["receipt"]["operation_id"] == interrupted["operation_id"]
        # Use the real ownership guard: a non-owner cannot write history.
        chain.claim_project_ownership(temporary, "film", "workflow-owner-a-1234567890", "Owner")
        denied = await chain._update_prompt_history(Request({**body, "operation_id": uuid.uuid4().hex}, branch_id=branch))
        assert denied.status == 423, denied.text
        after = json.loads((await chain._get_prompt_history(Request(run_name="film", scene_id="one", branch_id=branch))).text)
        assert after["history_revision"] == recovered["history"]["history_revision"]
    print("Prompt history API: scope echo, revision reads, retries, 409 conflicts and 423 ownership passed")

asyncio.run(main())
