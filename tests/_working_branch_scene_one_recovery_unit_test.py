"""Seven-scene secondary branch stays recoverable after a scene-one rerender."""
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


class Request(helpers.JsonRequest):
    method = "POST"
    query = {}


async def check():
    with tempfile.TemporaryDirectory() as temporary:
        helpers.folder_paths.output_directory = temporary
        run = "branch_scene_one"
        root = Path(temporary) / "h3_chains" / run
        predecessor = None
        lineage = []
        immutable = {}
        for scene in range(1, 8):
            revision = str(scene) * 32
            predecessor, files = helpers.write_revision(root, scene, revision, scene,
                active=True, predecessor=predecessor, run_name=run)
            immutable.update({path: path.read_bytes() for path in files})
            lineage.append({"scene": scene, "revision": revision})
        authored = {"plan_json": json.dumps({"shots": [
            {"id": f"scene_{i}", "prompt": f"scene {i}", "seed": i} for i in range(1, 8)]}),
            "width": 960, "height": 544}
        store = chain.WorkingBranches(temporary, run)
        store.save("main", authored, "")
        branch = store.create("main", "960x544", authored, 7)
        same_name = store.create("main", "960x544", authored)
        new_revision = "a" * 32
        rerender, files = helpers.write_revision(root, 1, new_revision, 99, run_name=run)
        immutable.update({path: path.read_bytes() for path in files})
        # Exact reported state: new scene 1, old scene 2–7 pointers still present.
        pointer = root / "branches" / branch["id"] / "checkpoints/clip_0001.json"
        pointer.write_text(json.dumps(rerender))
        manager = chain.CheckpointGraphManager(temporary)
        original_pointers = {path: path.read_bytes() for path in (root / "checkpoints").glob("clip_????.json")}
        with chain.branch_scope(run, branch["id"]):
            assert manager.active_selection(run)[0] == {1: new_revision}
        preview = manager.deletion_preview(run, 1, new_revision)
        assert not preview["allowed"], "the secondary branch's active take must be protected from Original"
        assigned = await chain._restore_checkpoint_revisions(Request({
            "run_name": run, "branch_id": branch["id"], "activate_only": True,
            "resume_scene": 8, "scope_start_scene": 1, "scope_end_scene": 7,
            "revisions": lineage}))
        assert assigned.status == 200, assigned.text
        with chain.branch_scope(run, branch["id"]):
            active, _ = manager.active_selection(run)
            assert active == {item["scene"]: item["revision"] for item in lineage}
            preview = manager.deletion_preview(run, 1, new_revision)
            assert preview["allowed"], preview.get("blockers")
        with chain.branch_scope(run, same_name["id"]):
            assert manager.active_selection(run)[0] == {}, "duplicate branch label must not receive the assignment"
        assert all(path.read_bytes() == content for path, content in original_pointers.items())
        assert all(path.read_bytes() == content for path, content in immutable.items())
        assert len(store.listing()["branches"]) == 3, "restoring a path must not create another named branch"
    print("Scene-one rerender: seven-scene recovery, duplicate-label isolation, deletability and immutable media preservation pass")


if __name__ == "__main__":
    asyncio.run(check())
