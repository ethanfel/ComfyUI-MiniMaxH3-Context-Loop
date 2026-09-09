"""Working branch isolation, shared media, assignment and execution regression."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("branch_helpers", ROOT / "tests/_checkpoint_revision_unit_test.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
chain = helpers.chain
Store = chain.WorkingBranches


class Request(helpers.JsonRequest):
    method = "POST"
    query = {}


async def check():
    with tempfile.TemporaryDirectory() as temporary:
        helpers.folder_paths.output_directory = temporary
        root = Path(temporary) / "h3_chains/branches_test"
        one = "1" * 32
        two = "2" * 32
        other = "3" * 32
        first, _ = helpers.write_revision(root, 1, one, 1, active=True, run_name="branches_test")
        helpers.write_revision(root, 2, two, 2, active=True, predecessor=first, run_name="branches_test")
        helpers.write_revision(root, 2, other, 3, predecessor=first, run_name="branches_test")
        authored = {"plan_json": json.dumps({"shots":[
            {"id":"scene_1", "prompt":"first", "seed":1},
            {"id":"scene_2", "prompt":"second", "seed":2}],
            "chapters":[{"id":"chapter_1", "start_scene_id":"scene_1", "title":"Chapter 1"}]}),
            "width":64, "height":64}
        store = Store(temporary, "branches_test")
        (root / "plan.json").write_text(json.dumps({"run_name": "branches_test",
            "shots": [{"id": "scene_1"}, {"id": "scene_2"}]}))
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        saved = store.save("main", authored, "")
        try:
            store.save("main", authored, "")
        except ValueError as exc:
            assert "another workflow" in str(exc)
        else:
            raise AssertionError("Stale authoring overwrite accepted")
        empty = store.create("main", "Empty", authored)
        fork = store.create("main", "Fork", authored, 1)
        assert json.loads(empty["authoring"]["plan_json"])["shots"] == json.loads(authored["plan_json"])["shots"]
        assert all(path.read_bytes() == contents for path, contents in before.items())
        assert not list((root / "branches" / empty["id"]).rglob("*.mp4"))
        manager = chain.CheckpointGraphManager(temporary)
        assert manager.active_selection("branches_test")[0] == {1:one, 2:two}
        with chain.branch_scope("branches_test", empty["id"]):
            assert manager.active_selection("branches_test")[0] == {}
            assert chain._saved_checkpoint_listing("branches_test")["checkpoints"] == []
        with chain.branch_scope("branches_test", fork["id"]):
            assert manager.active_selection("branches_test")[0] == {1:one}
            listing = chain._saved_checkpoint_listing("branches_test")
            assert [item["revision"] for item in listing["checkpoints"]] == [one]
            assert listing["working_branch_id"] == fork["id"]
            assert any(item["revision"] == other for item in listing["revisions"]), "Assign must still see saved alternatives"
        # Existing assign/restore route: only the target working branch changes.
        assigned = await chain._restore_checkpoint_revisions(Request({
            "run_name":"branches_test", "branch_id":fork["id"], "activate_only":True,
            "resume_scene":3, "revisions":[{"scene":1,"revision":one},{"scene":2,"revision":other}]}))
        assert assigned.status == 200, assigned.text
        with chain.branch_scope("branches_test", fork["id"]):
            assert manager.active_selection("branches_test")[0] == {1:one, 2:other}
            paths = chain._artifact_paths({"run_name":"branches_test"}, 2)
            assert str(root / "branches" / fork["id"]) in paths["metadata"]
            assert str(root / "checkpoints") in paths["checkpoint"]
        assert manager.active_selection("branches_test")[0] == {1:one, 2:two}
        assert all(path.read_bytes() == contents for path, contents in before.items())
        # A manager used as a source carries the selected branch all the way
        # to downstream processing, even when its shared takes predate branches.
        source = chain.MiniMaxH3ChainCheckpointManager().passthrough(json.dumps({
            "run_name": "branches_test", "_branch_id": fork["id"], "output_mode": "workflow_local",
            "lineage": [{"scene": 1, "revision": one}, {"scene": 2, "revision": other}]}))[0]
        assert source["_branch_id"] == fork["id"]
        assert [segment["revision"] for segment in source["segments"]] == [one, other]
        assert all(path.read_bytes() == contents for path, contents in before.items())
        # Shared selections pin immutable artifacts even outside the default.
        preview = manager.deletion_preview("branches_test", 2, other)
        assert not preview["allowed"]
        assert any("Working branch" in reason for reason in preview["blockers"])
        store.make_default(empty["id"])
        assert store.listing()["default_branch"] == empty["id"]
        assert manager.active_selection("branches_test")[0] == {1:one, 2:two}
        for invalid in ("../escape", "x", "0" * 32):
            try:
                chain._run_dir({"run_name":"branches_test", "_branch_id":invalid})
            except (ValueError, FileNotFoundError):
                pass
            else:
                raise AssertionError("Invalid/unavailable branch accepted")
        # Exercise real Plan build wrappers before Studio and downstream nodes.
        options = dict(plan_json=empty["authoring"]["plan_json"], run_name="branches_test",
            generation_fingerprint="test", width=64,height=64, context_length=22,
            encode_mode="video",anchor_mode="head",crop="disabled",audio_mode="generated_audio",
            audio_context_length=22,default_duration_seconds=2,default_steps=8,base_seed=1,segment_crf=18)
        plan = chain.MiniMaxH3ChainPlan().build(**options)[0]
        assert plan["_branch_id"] == empty["id"]
        assert chain._run_dir(plan) == str(root / "branches" / empty["id"])
        assert chain.current_branch("branches_test") == "main", "scope leaked after node execution"
        # Compatibility must depend on authored generation settings, not branch id.
        options["plan_json"] = authored["plan_json"]
        original = chain.MiniMaxH3ChainPlan().build(**options)[0]
        assert original["plan_hash"] == plan["plan_hash"]
        assert "_branch_id" not in original
        # Separate async executions must not borrow each other's branch scope.
        scope_module = importlib.import_module(chain.__package__ + ".branch_scope")
        @scope_module.scoped_node
        async def probe(plan):
            await asyncio.sleep(0)
            return chain.current_branch(plan["run_name"])
        assert await asyncio.gather(probe(plan), probe(original)) == [empty["id"], "main"]
        # Durable and deferred reviews remain visible on their own branch;
        # accepting them uses the token's branch rather than a browser default.
        token = "a" * 32
        with chain.branch_scope("branches_test", empty["id"]):
            chain._write_review_snapshot(chain._run_dir(plan), token, "branches_test", 1, [], None, 0)
            chain._persist_deferred_review(plan, {"token": token, "clip_index": 1}, [])
        pending = json.loads((await chain._list_pending_reviews(Request({}))).text)["reviews"]
        assert next(item for item in pending if item["token"] == token)["_branch_id"] == empty["id"]
        query = Request({})
        query.method = "GET"
        query.query = {"run_name": "branches_test", "branch_id": empty["id"]}
        deferred = json.loads((await chain._list_deferred_reviews(query)).text)["reviews"]
        assert deferred[0]["_branch_id"] == empty["id"]
        assert chain._list_deferred_review_records("branches_test") == []
        # Request validation: reject a huge prefix before iterating it.
        result = await chain._working_branch_command(Request({"action":"create", "run_name":"branches_test",
            "name":"bad", "authoring":authored, "through_scene":10**12}))
        assert result.status == 400
        # Branch metadata writes are still subject to project ownership.
        chain.claim_project_ownership(temporary, "branches_test", "another-workflow")
        blocked = await chain._working_branch_command(Request({"action": "create", "run_name": "branches_test",
            "name": "Unauthorized", "authoring": authored}))
        assert blocked.status == 423, blocked.text
    print("Working branches: empty/fork isolation, assign/reuse, shared-media protection, scope and legacy compatibility pass")


if __name__ == "__main__":
    asyncio.run(check())
