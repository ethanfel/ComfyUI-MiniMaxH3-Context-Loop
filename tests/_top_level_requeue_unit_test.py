#!/usr/bin/env python3
"""Top-level scene requeue: Loop End stops, handoff is durable, no recursion.

Covers Milestone 3 acceptance for the deterministic (model-free) surface:

- Loop End's new opt-in ``execution_mode`` socket/param contract (default
  ``recursive_legacy`` keeps the old behavior; outputs and required inputs
  are unchanged).
- In ``top_level_requeue`` mode, a completed scene N writes a lightweight
  durable ``next_scene`` handoff and returns the existing manifest tuple in a
  ComfyUI result/UI envelope, enabling an ``executed`` completion event
  without recursively expanding scene N+1.
- The Plan JSON is byte-identical before/after (no orchestration fields).
- The handoff is idempotent per (run, scene): re-running Loop End for the
  same scene never creates a second record.
- The handoff routes list/claim/transition/release with exactly-once claim
  semantics, safe manual-resume hints, and no backend auto-queueing.
"""

import asyncio
import importlib.util
import inspect
import json
import os
import pathlib
import sys
import tempfile
import types

import torch


ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_requeue_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

LATENT_FRAMES = torch.zeros(2, 1, 1, 1)
LATENT_AUDIO = torch.zeros(2)
shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test"
shared_nodes._prepare_native_guide_conditioning = lambda value: value
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda _latent: [LATENT_FRAMES,
                                                     LATENT_AUDIO]
sys.modules[shared_nodes.__name__] = shared_nodes

spec = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = chain
spec.loader.exec_module(chain)


def make_plan(run_name):
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        chain._contract_transition_policy(
            "guide", expert_override=True,
            continuation_mode="guide", context_length=5),
        audio_context_length=5)
    # Shot lengths stay above the MiniMax H3 minimum (>= 2 seconds);
    # 124 frames at 24 fps is ~5 seconds per shot.
    return chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "Opening action.", "length": 124},
            {"id": "two", "prompt": "Continuation action.", "length": 124},
        ]}),
        run_name, 64, 64, 5, "video", "head", "disabled",
        "source_track", 5, 1.0, 8, 7, 18, "stack:auto:v1", 0,
        "guide", policy)


def make_inputs(plan):
    state = {
        "plan": plan,
        "index": 1,
        "range_start": 1,
        "end_clip": 2,
        "segments": [],
        "resumed_from": 0,
    }
    images = torch.zeros(5, 1, 1, 3)
    sampled_latent = {"samples": [LATENT_FRAMES, LATENT_AUDIO]}
    segment = {
        "index": 1,
        "id": "one",
        "revision": "ab" * 16,
        "raw_frames": 124,
        "delivered_frames": 124,
        "history_hash": "hist",
        "seed": plan["shots"][0]["seed"],
    }
    return state, images, sampled_latent, segment


def handoff_path(root, run_name, handoff_id):
    return (pathlib.Path(root) / "h3_chains" / run_name / "orchestration"
            / ("%s.json" % handoff_id))


def contract():
    """Public node contract: additive only, default is legacy."""
    modes = chain.LOOP_EXECUTION_MODES
    assert modes == ("recursive_legacy", "top_level_requeue")
    spec_doc = chain.MiniMaxH3ChainLoopEnd.INPUT_TYPES()
    required = list(spec_doc["required"])
    assert required == ["flow", "state", "images", "sampled_latent",
                        "segment"], \
        "required inputs must keep their order"
    optional = list(spec_doc["optional"])
    assert optional == ["between_scene_cleanup", "execution_mode"], \
        "execution_mode must be appended after between_scene_cleanup"
    modes_tuple, widget = spec_doc["optional"]["execution_mode"]
    assert modes_tuple == list(modes)
    assert widget["default"] == "recursive_legacy", \
        "default mode must keep the recursive legacy behavior"
    assert chain.MiniMaxH3ChainLoopEnd.RETURN_TYPES == \
        (chain.MANIFEST_TYPE, "STRING", "IMAGE", "LATENT")
    assert chain.MiniMaxH3ChainLoopEnd.FUNCTION == "end"
    params = inspect.signature(chain.MiniMaxH3ChainLoopEnd.end).parameters
    assert params["execution_mode"].default == "recursive_legacy"


def requeue_mode(root):
    """Scene 1 completes: handoff written, no recursion, Plan untouched."""
    plan = make_plan("requeue_run")
    plan_before = json.dumps(plan, sort_keys=True)
    state, images, latent, segment = make_inputs(plan)
    calls = []
    original_recurse = chain.MiniMaxH3ChainLoopEnd._recurse

    def recorder(self, flow, next_state, dynprompt, unique_id):
        calls.append(next_state)
        return {"result": "expansion", "expand": None}

    chain.MiniMaxH3ChainLoopEnd._recurse = recorder
    try:
        # Simulate Segment Save having created the run directory.
        run_dir = pathlib.Path(root) / "h3_chains" / "requeue_run"
        (run_dir / "segments").mkdir(parents=True, exist_ok=True)
        result = chain.MiniMaxH3ChainLoopEnd().end(
            None, state, images, latent, segment,
            execution_mode="top_level_requeue")
    finally:
        chain.MiniMaxH3ChainLoopEnd._recurse = original_recurse

    assert calls == [], "top_level_requeue must not recurse into scene 2"
    assert isinstance(result, dict) and isinstance(result.get("result"), tuple)
    assert len(result["result"]) == 4, "public result slots stay unchanged"
    assert set(result.get("ui", {})) == {"h3_chain_top_level_requeue"}
    completions = result["ui"]["h3_chain_top_level_requeue"]
    assert len(completions) == 1
    manifest, manifest_json, _frames, _latent = result["result"]
    assert manifest["format"] == "h3_chain_partial_manifest_v3"
    assert manifest["last_completed_clip"] == 1
    assert manifest["planned_clip_count"] == 2
    assert json.loads(manifest_json) == manifest

    partial = run_dir / "partial" / "through_clip_0001.manifest.json"
    assert partial.is_file(), \
        "requeue mode persists a partial through-clip manifest"
    assert json.loads(partial.read_text(encoding="utf-8")) == manifest

    final_state, final_images, final_latent, final_segment = make_inputs(plan)
    final_state["index"] = 2
    final_state["segments"] = [chain._public_segment(segment)]
    final_segment.update({"index": 2, "id": "two", "seed": plan["shots"][1]["seed"]})
    final_result = chain.MiniMaxH3ChainLoopEnd().end(
        None, final_state, final_images, final_latent, final_segment,
        execution_mode="top_level_requeue")
    assert not (isinstance(final_result, dict) and
                "h3_chain_top_level_requeue" in final_result.get("ui", {}))

    records = list((run_dir / "orchestration").glob("next_scene_0002_*.json"))
    assert len(records) == 1, "next_scene handoff must be durable"
    record_path = records[0]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["predecessor_scene"] == 1
    assert record["transition_key"], "identity is bound to committed transition"
    assert record["format"] == "h3_top_level_handoff_v1"
    assert record["action"] == "next_scene"
    assert record["status"] == "pending"
    assert record["attempt"] == 0
    assert record["scene"] == 2
    assert record["start_clip"] == 2
    assert record["end_clip"] == 2
    assert record["seed"] == int(plan["shots"][1]["seed"])
    assert record["source_revision"] == "ab" * 16
    assert record["workflow_fingerprint"] == plan["plan_hash"]
    assert record["source_prompt_id"] is None, \
        "the backend never knows the source prompt id; the frontend " \
        "stamps it at claim time"
    completion = completions[0]
    for key in ("run_name", "predecessor_scene", "scene", "end_clip",
                "workflow_fingerprint", "handoff_id", "source_revision",
                "source_checkpoint_sha256", "transition_key"):
        assert completion[key] == record[key]
    # No tensors or Plan internals leaked into the record.
    for forbidden in ("shots", "prompt_prefix", "generation_fingerprint",
                      "scene_prompt", "previous_frames", "samples"):
        assert forbidden not in record and forbidden not in completion, \
            "handoff completion leaked Plan/tensor data %r" % forbidden

    assert json.dumps(plan, sort_keys=True) == plan_before, \
        "the Plan JSON must stay byte-identical"

    # Idempotency: re-running the same scene reuses the same record.
    state2, images2, latent2, segment2 = make_inputs(plan)
    chain.MiniMaxH3ChainLoopEnd._recurse = recorder
    try:
        chain.MiniMaxH3ChainLoopEnd().end(
            None, state2, images2, latent2, segment2,
            execution_mode="top_level_requeue")
    finally:
        chain.MiniMaxH3ChainLoopEnd._recurse = original_recurse
    assert calls == [], "re-run must not recurse either"
    records = list((run_dir / "orchestration").glob("*.json"))
    assert len(records) == 1, \
        "a re-timed Loop End must not create a second handoff"
    assert json.loads(records[0].read_text(encoding="utf-8"))["attempt"] == 0

    # A later rerender is new committed work, not a resurrection of the old
    # terminal/claimed transition. Exercise the owner's consumed-history case.
    old_id = json.loads(records[0].read_text())["handoff_id"]
    store = chain._HandoffStore(root)
    store.claim("requeue_run", old_id)
    store.transition("requeue_run", old_id, "queued")
    store.transition("requeue_run", old_id, "consumed")
    state3, images3, latent3, segment3 = make_inputs(plan)
    segment3["revision"] = "cd" * 16
    chain.MiniMaxH3ChainLoopEnd().end(
        None, state3, images3, latent3, segment3,
        execution_mode="top_level_requeue")
    records = list((run_dir / "orchestration").glob("*.json"))
    assert len(records) == 2
    by_revision = {json.loads(path.read_text())["source_revision"]:
                   json.loads(path.read_text()) for path in records}
    assert by_revision["ab" * 16]["status"] == "consumed"
    assert by_revision["cd" * 16]["status"] == "pending"
    assert by_revision["ab" * 16]["handoff_id"] != by_revision["cd" * 16]["handoff_id"]
    assert len({item["transition_key"] for item in by_revision.values()}) == 2
    # Exact retry of B remains idempotent.
    state4, images4, latent4, segment4 = make_inputs(plan)
    segment4["revision"] = "cd" * 16
    chain.MiniMaxH3ChainLoopEnd().end(None, state4, images4, latent4, segment4,
                                      execution_mode="top_level_requeue")
    assert len(list((run_dir / "orchestration").glob("*.json"))) == 2

    # Real Loop End writer: terminal history never blocks a new render.
    for status in ("queued", "consumed", "cancelled", "failed"):
        matrix_plan = make_plan("matrix_%s" % status)
        matrix_dir = pathlib.Path(root) / "h3_chains" / matrix_plan["run_name"]
        (matrix_dir / "segments").mkdir(parents=True, exist_ok=True)
        a_state, a_images, a_latent, a_segment = make_inputs(matrix_plan)
        a_segment["revision"] = "aa" * 16
        chain.MiniMaxH3ChainLoopEnd().end(None, a_state, a_images, a_latent, a_segment, execution_mode="top_level_requeue")
        matrix = chain._HandoffStore(root)
        a = matrix.list(matrix_plan["run_name"])[0]
        if status == "cancelled": matrix.transition(matrix_plan["run_name"], a["handoff_id"], "cancelled")
        else:
            matrix.claim(matrix_plan["run_name"], a["handoff_id"])
            if status == "failed": matrix.transition(matrix_plan["run_name"], a["handoff_id"], "failed")
            else:
                matrix.transition(matrix_plan["run_name"], a["handoff_id"], "queued")
                if status == "consumed": matrix.transition(matrix_plan["run_name"], a["handoff_id"], "consumed")
        b_state, b_images, b_latent, b_segment = make_inputs(matrix_plan)
        b_segment["revision"] = "bb" * 16
        chain.MiniMaxH3ChainLoopEnd().end(None, b_state, b_images, b_latent, b_segment, execution_mode="top_level_requeue")
        records = matrix.list(matrix_plan["run_name"])
        b = next(item for item in records if item["source_revision"] == "bb" * 16)
        assert matrix.load(matrix_plan["run_name"], a["handoff_id"])["status"] == status
        assert b["status"] == "pending" and b["handoff_id"] != a["handoff_id"]
        assert b["transition_key"] != a["transition_key"]
        chain.MiniMaxH3ChainLoopEnd().end(None, b_state, b_images, b_latent, b_segment, execution_mode="top_level_requeue")
        assert len(matrix.list(matrix_plan["run_name"])) == 2


def legacy_mode(root):
    """Default mode keeps the recursive GraphBuilder expansion."""
    plan = make_plan("legacy_run")
    state, images, latent, segment = make_inputs(plan)
    calls = []
    original_recurse = chain.MiniMaxH3ChainLoopEnd._recurse

    def recorder(self, flow, next_state, dynprompt, unique_id):
        calls.append(next_state)
        return {"result": "expansion", "expand": None}

    chain.MiniMaxH3ChainLoopEnd._recurse = recorder
    try:
        result = chain.MiniMaxH3ChainLoopEnd().end(
            None, state, images, latent, segment)
    finally:
        chain.MiniMaxH3ChainLoopEnd._recurse = original_recurse
    assert len(calls) == 1, "legacy mode must recurse into the next scene"
    assert result == {"result": "expansion", "expand": None}
    orchestration = (pathlib.Path(root) / "h3_chains" / "legacy_run"
                     / "orchestration")
    assert not orchestration.exists(), \
        "legacy mode must not write orchestration handoffs"


class FakeRequest:
    def __init__(self, query=None, body=None, bad_json=False):
        self.query = query or {}
        self._body = body
        self._bad = bad_json

    async def json(self):
        if self._bad:
            raise json.JSONDecodeError("bad", "b", 0)
        return self._body or {}


def _status(response):
    return response.status


def routes(root):
    run = "route_run"
    run_dir = pathlib.Path(root) / "h3_chains" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "plan.json").write_text(
        json.dumps({"run_name": run,
                    "shots": [{} for _ in range(6)]}),
        encoding="utf-8")
    store = chain._HandoffStore(str(root))
    store.create(run, action="next_scene", scene=2, start_clip=2,
                 end_clip=6, handoff_id="rs_full",
                 workflow_fingerprint="wf-1")
    store.create(run, action="next_scene", scene=2, start_clip=2,
                 end_clip=4, handoff_id="rs_range",
                 workflow_fingerprint="wf-1")
    store.create(run, action="next_scene", scene=4, start_clip=4,
                 end_clip=4, handoff_id="rs_single",
                 workflow_fingerprint="wf-1")

    async def scenario():
        # --- list with safe manual-resume hints ----------------------------
        response = await chain._list_handoffs(
            FakeRequest(query={"run_name": run}))
        assert _status(response) == 200
        listing = json.loads(response.text)
        assert listing["run_name"] == run
        by_id = {item["handoff_id"]: item for item in listing["handoffs"]}
        assert set(by_id) == {"rs_full", "rs_range", "rs_single"}
        assert by_id["rs_full"]["resume"] == {
            "start_clip": 2, "scene_range": "", "end_clip": 6,
            "total_scenes": 6}
        assert by_id["rs_range"]["resume"]["scene_range"] == "2:4"
        assert by_id["rs_single"]["resume"]["scene_range"] == "4"
        # Listing never claims: everything stays pending (no auto-queue).
        assert all(item["status"] == "pending" for item in
                   listing["handoffs"])

        # --- missing run_name ------------------------------------------------
        response = await chain._list_handoffs(FakeRequest())
        assert _status(response) == 400

        # --- exactly-once claim with source prompt id ------------------------
        response = await chain._claim_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_full",
            "source_prompt_id": "11111111-2222-3333-4444-555555555555",
        }))
        assert _status(response) == 200
        claimed = json.loads(response.text)["handoff"]
        assert claimed["status"] == "claimed"
        assert claimed["attempt"] == 1
        assert claimed["source_prompt_id"] == \
            "11111111-2222-3333-4444-555555555555"

        response = await chain._claim_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_full",
            "source_prompt_id": "99999999-8888-7777-6666-555555555555",
        }))
        assert _status(response) == 409, \
            "a duplicate terminal event must never re-claim"
        record = store.load(run, "rs_full")
        assert record["source_prompt_id"] == \
            "11111111-2222-3333-4444-555555555555"

        # --- queued -> consumed lifecycle -------------------------------------
        response = await chain._transition_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_full", "status": "queued",
            "accepted_prompt_id": "accepted-prompt"}))
        assert _status(response) == 200
        assert store.load(run, "rs_full")["accepted_prompt_id"] == "accepted-prompt"
        response = await chain._transition_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_full",
            "status": "consumed"}))
        assert _status(response) == 200
        assert store.load(run, "rs_full")["status"] == "consumed"
        response = await chain._transition_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_full",
            "status": "consumed"}))
        assert _status(response) == 409, \
            "consumed is terminal; a second success queues nothing"

        # --- validation failures ------------------------------------------------
        response = await chain._transition_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_range", "status": "banana"}))
        assert _status(response) == 400
        response = await chain._release_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "rs_range",
            "reason": "not claimed"}))
        assert _status(response) == 409, \
            "releasing a pending handoff is illegal"
        response = await chain._claim_handoff(FakeRequest(bad_json=True))
        assert _status(response) == 400
        response = await chain._claim_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": ""}))
        assert _status(response) == 400
        response = await chain._claim_handoff(FakeRequest(body={
            "run_name": run, "handoff_id": "missing_id"}))
        assert _status(response) == 404
        # Path traversal in run_name is neutralized by the name policy.
        response = await chain._list_handoffs(FakeRequest(
            query={"run_name": "../../evil"}))
        assert _status(response) in (200, 400)
        assert not (pathlib.Path(root).parent / "evil").exists()

    asyncio.new_event_loop().run_until_complete(scenario())


def main():
    contract()
    with tempfile.TemporaryDirectory() as temporary:
        root = temporary
        chain._output_root = lambda: str(root)
        requeue_mode(root)
    with tempfile.TemporaryDirectory() as temporary:
        chain._output_root = lambda: str(temporary)
        legacy_mode(temporary)
    with tempfile.TemporaryDirectory() as temporary:
        chain._output_root = lambda: str(temporary)
        routes(temporary)
    print("H3 Top-Level Scene Requeue: no-recursion Loop End, durable "
          "exactly-once handoffs, unchanged Plan pass")


if __name__ == "__main__":
    main()
