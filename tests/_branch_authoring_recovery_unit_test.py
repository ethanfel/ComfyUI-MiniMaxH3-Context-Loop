"""Legacy profile repair, assignment invalidation, exact seeds and draft safety."""
import asyncio
import copy
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
        run = "authoring_recovery"
        root = Path(temporary) / "h3_chains" / run
        store = chain.WorkingBranches(temporary, run)
        seeds = [2948817042231741711, 7520697338425493968, 0, 5496650660172090794,
                 161084066092525474, 17115579879135537167, 18446744073709551615]
        predecessor = None
        lineage = []
        for scene, seed in enumerate(seeds, 1):
            token = str(scene) * 32
            predecessor, _ = helpers.write_revision(root, scene, token, seed, active=True,
                predecessor=predecessor, run_name=run,
                compatibility={"width": 960, "height": 544, "context_length": 39,
                               "audio_context_length": 39, "audio_mode": "generated_audio"})
            lineage.append({"scene": scene, "revision": token})
        plan = {"shots": [{"id": f"scene_{i}", "prompt": f"wrong {i}", "seed": "1",
                            "note": "keep my note", "duration_seconds": 1} for i in range(1, 9)],
                "chapters": [{"id": "one", "start_scene_id": "scene_1"},
                             {"id": "two", "start_scene_id": "scene_8"}],
                "prompt_prefix": "wrong shared", "custom_notes": "keep these"}
        authored = {"plan_json": json.dumps(plan), "width": 1344, "height": 768}
        initial = store.save("main", authored, "")
        fork = store.create("main", "960x544", authored, 7)
        # Simulate files produced before authoring/assignment tracking existed.
        path = root / "branches" / fork["id"] / "branch.json"
        legacy = copy.deepcopy(fork)
        legacy.pop("authoring_version")
        legacy.pop("authoring_assignments")
        path.write_text(json.dumps(legacy))
        before = {p: p.read_bytes() for p in root.rglob("*.json")}
        restored = store.load(fork["id"])
        result = json.loads(restored["authoring"]["plan_json"])
        assert restored["authoring"]["width"] == 960
        assert restored["authoring"]["height"] == 544
        assert result["chapters"][0]["resolution"] == {"width": 960, "height": 544}
        assert result["chapters"][1]["resolution"] == {"width": 1344, "height": 768}
        for scene, seed in enumerate(seeds, 1):
            shot = result["shots"][scene - 1]
            assert shot["seed"] == str(seed)
            assert shot["prompt"] == f"prompt {scene} {seed}"
            assert shot["steps"] == 8 and shot["length"] == 362
            assert shot["note"] == "keep my note" and "duration_seconds" not in shot
        assert result["shots"][7] == plan["shots"][7], "unsampled scene must remain authored"
        assert result["custom_notes"] == "keep these"
        assert restored["revision"] != legacy["revision"]
        assert store.load(fork["id"]) == restored, "recovery token must be stable"
        assert all(p.read_bytes() == data for p, data in before.items()), "load must not rewrite JSON"
        try:
            store.save(fork["id"], authored, legacy["revision"])
        except ValueError as exc:
            assert "Reload saved branch" in str(exc)
        else:
            raise AssertionError("old tab overwrote recovered authoring")
        operation = "a" * 32
        saved = store.save(fork["id"], restored["authoring"], restored["revision"], operation)
        assert json.loads((root / saved["authoring_backup"]).read_text()) == legacy
        assert store.save(fork["id"], restored["authoring"], restored["revision"], operation) == saved
        assert store.load("main") == initial, "recovery must not touch Original"

        # Deliberately edited future renders are not replaced on every switch.
        edited = copy.deepcopy(saved["authoring"])
        edited_plan = json.loads(edited["plan_json"])
        edited_plan["shots"][0]["seed"] = "42"
        edited_plan["shots"][0]["prompt"] = "intentional unsampled edit"
        edited["plan_json"] = json.dumps(edited_plan)
        edited["width"] = 512
        saved = store.save(fork["id"], edited, saved["revision"])
        assert store.load(fork["id"])["authoring"] == saved["authoring"]

        # Assignment works without a connected Plan and invalidates stale tabs.
        for branch in [fork["id"], "main"]:
            previous = store.load(branch)
            previous_input = copy.deepcopy(previous["authoring"])
            previous_revision = previous["revision"]
            previous = store.save(branch, previous_input, previous_revision, "c" * 32)
            response = await chain._restore_checkpoint_revisions(Request({
                "run_name": run, "branch_id": branch, "activate_only": True,
                "resume_scene": 8, "scope_end_scene": 7, "revisions": lineage}))
            assert response.status == 200, response.text
            current = store.load(branch)
            assert current["revision"] != previous["revision"]
            assert current["authoring"]["width"] == 960
            assert json.loads(current["authoring"]["plan_json"])["shots"][0]["seed"] == str(seeds[0])
            try:
                store.save(branch, previous["authoring"], previous["revision"])
            except ValueError:
                pass
            else:
                raise AssertionError("pre-assignment save was accepted")
            try:
                store.save(branch, previous_input, previous_revision, "c" * 32)
            except ValueError:
                pass
            else:
                raise AssertionError("old save receipt acknowledged a later assignment")
        # A new render is not an assignment and must not reset author's edits.
        current = store.load(fork["id"])
        current = store.save(fork["id"], edited, current["revision"])
        generated, _ = helpers.write_revision(root, 1, "b" * 32, 123, run_name=run)
        (root / "branches" / fork["id"] / "checkpoints/clip_0001.json").write_text(json.dumps(generated))
        assert store.load(fork["id"])["authoring"] == current["authoring"]
        # Recovery must not rename/reorder scenes or silently guess geometry.
        recover = __import__(chain.__package__ + ".branch_authoring_recovery", fromlist=["recover_authoring"]).recover_authoring
        wrong_order = copy.deepcopy(authored)
        wrong_order["plan_json"] = json.dumps({"shots": [{"id": "different"}]})
        try:
            recover(wrong_order, {1: generated})
        except ValueError as exc:
            assert "scene order" in str(exc)
        else:
            raise AssertionError("recovery silently remapped scene IDs")
        mixed = {1: copy.deepcopy(generated), 2: copy.deepcopy(generated)}
        mixed[1]["segment"].update(index=1, id="scene_1", resolution={"width":960,"height":544})
        mixed[2]["segment"].update(index=2, id="scene_2", resolution={"width":1344,"height":768})
        try:
            recover(authored, mixed)
        except ValueError as exc:
            assert "different resolutions" in str(exc)
        else:
            raise AssertionError("mixed chapter geometry was silently accepted")
    print("Branch authoring: legacy recovery, exact uint64 seeds, prompts, chapter resolution, "
          "unsampled edits, backups, assignment CAS and generation isolation pass")


if __name__ == "__main__":
    asyncio.run(check())
