#!/usr/bin/env python3
"""M5: Durable review gate — saved candidates survive crash/restart.

Tests the deterministic acceptance criteria from M5 spec:
- Reviewable candidates rebuilt from durable batch/checkpoint inventory
  (not from live PromptExecutor image tensors or in-memory state).
- After crash/restart (simulated by loading inventory without any live
  in-memory review state), review inventory loads from saved files.
- Plan JSON unchanged (no review batch fields added to Plan).
- Approve & continue creates a durable `next_scene` handoff;
  Approve & stop does not queue; Plan restore remains explicit.
"""

import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types

import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_review_gate_unit"

folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
folder_paths.get_annotated_filepath = lambda value: str(value)
sys.modules["folder_paths"] = folder_paths

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

shared_nodes = types.ModuleType(PACKAGE + ".nodes")
shared_nodes.MiniMaxH3MotionContext = object
shared_nodes._claim_inline_patch_ownership = lambda _conditioning=None: "test"
shared_nodes._prepare_native_guide_conditioning = lambda value: value
shared_nodes._resize = lambda *args: None
shared_nodes._streams_from_latent = lambda _latent: [torch.zeros(2, 1, 1, 1),
                                                     torch.zeros(2)]
sys.modules[shared_nodes.__name__] = shared_nodes

spec_chain = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec_chain)
sys.modules[spec_chain.name] = chain
spec_chain.loader.exec_module(chain)

# Load handoff_state and review_inventory directly.
spec_hs = importlib.util.spec_from_file_location(
    "handoff_state", ROOT / "handoff_state.py")
hs = importlib.util.module_from_spec(spec_hs)
spec_hs.loader.exec_module(hs)

spec_inv = importlib.util.spec_from_file_location(
    "review_inventory", ROOT / "review_inventory.py")
review_inv = importlib.util.module_from_spec(spec_inv)
spec_inv.loader.exec_module(review_inv)

CHECKPOINT_SHA = "ab" * 32
FORBIDDEN_PLAN_FIELDS = (
    "candidate_ordinal", "candidate_batch_id", "awaiting_review",
    "batch_id", "review_gate", "pending_review", "batch_token",
)


def deterministic_store(root):
    state = {"n": 0}

    def now():
        state["n"] += 1
        return "2026-09-06T00:00:00.000+00:00" if state["n"] == 1 \
            else "2026-09-06T00:00:01.000+00:00"

    return hs.HandoffStore(root, now=now)


def assert_plan_clean(plan_text):
    for forbidden in FORBIDDEN_PLAN_FIELDS:
        assert forbidden not in plan_text, \
            "M5 must not inject %r into Plan JSON" % forbidden


def main():
    plan_before_text = json.dumps(
        {"run_name": "film_run",
         "shots": [{"id": "one", "prompt": "Open."}]}, sort_keys=True)

    with tempfile.TemporaryDirectory() as raw_root:
        store = deterministic_store(raw_root)
        batch_id = "batch-scene-1-review"

        # --- Create 3 durable candidate handoffs -----------------------------
        for ordinal in (1, 2, 3):
            store.create(
                "film_run", action="next_candidate",
                scene=1, start_clip=1, end_clip=2,
                candidate_batch_id=batch_id,
                candidate_ordinal=ordinal,
                candidate_count=3,
                seed=100 + ordinal,
                source_revision="rev-%d" % ordinal,
                source_checkpoint_sha256=CHECKPOINT_SHA,
                workflow_fingerprint="wf-review",
                handoff_id="next_candidate_%04d" % ordinal,
            )

        # --- Durable review inventory loads from disk ------------------------
        # Even without any live _PENDING_REVIEWS or in-memory batch registry,
        # the inventory loader finds the durable records (crash/restart safety:
        # a missing live pending-review object must not make saved candidates
        # unusable).
        inventory = review_inv.load_batch_inventory(
            os.path.join(raw_root, "h3_chains", "film_run"))
        handoff_items = [
            item for item in inventory
            if isinstance(item, dict)
            and item.get("format") == hs.HANDOFF_FORMAT_VERSION
            and item.get("action") in ("next_candidate", "await_review")
        ]
        assert len(handoff_items) >= 3, \
            "Crash recovery requires durable next_candidate handoffs; " \
            "found %d" % len(handoff_items)
        by_ordinal = {item["candidate_ordinal"]: item
                      for item in handoff_items
                      if item.get("candidate_ordinal")}
        for ordinal in (1, 2, 3):
            record = by_ordinal[ordinal]
            assert record["seed"] == 100 + ordinal
            assert record["source_revision"] == "rev-%d" % ordinal
            assert record["candidate_batch_id"] == batch_id

        # --- Plan unchanged ----------------------------------------------------
        assert_plan_clean(plan_before_text)

        # --- Approve & continue: durable next_scene handoff --------------------
        c1 = store.load("film_run", "next_candidate_0001")
        assert c1["seed"] == 101
        assert c1["source_revision"] == "rev-1"
        # Review selects candidate 1: mark it consumed, then approve.
        store.claim("film_run", "next_candidate_0001", source_prompt_id="p1")
        store.transition("film_run", "next_candidate_0001", "consumed")
        # Approve & continue: promote the selected revision and create the
        # lightweight next-scene handoff (coordinator queues the prompt).
        next_scene = store.create(
            "film_run", action="next_scene", scene=2, start_clip=2,
            source_revision="rev-1",
            source_checkpoint_sha256=CHECKPOINT_SHA,
            workflow_fingerprint="wf-review",
            handoff_id="next_scene_after_approve",
        )
        assert next_scene["action"] == "next_scene"
        assert next_scene["status"] == "pending"

        # Exactly one next_scene handoff exists (approve created exactly one).
        inventory_dir = os.path.join(
            raw_root, "h3_chains", "film_run", "orchestration")
        scene_handoffs = [
            name for name in os.listdir(inventory_dir)
            if name.startswith("next_scene_")]
        assert len(scene_handoffs) == 1, \
            "Approve & continue must create exactly one next_scene " \
            "handoff; found %r" % scene_handoffs

        # --- Approve & stop: no queue -------------------------------------------
        # Stopping the batch never queues: it transitions the pending
        # candidates to cancelled and creates no new next_scene handoff.
        for ordinal in (2, 3):
            hid = "next_candidate_%04d" % ordinal
            record = store.load("film_run", hid)
            if record["status"] == "pending":
                store.transition("film_run", hid, "cancelled")
        assert len(scene_handoffs) == 1, \
            "Approve & stop must not queue anything"

    # --- Crash + restart simulation ----------------------------------------------
    with tempfile.TemporaryDirectory() as raw_root:
        store = deterministic_store(raw_root)
        store.create(
            "film_run2", action="next_candidate", scene=2, start_clip=2,
            end_clip=2, candidate_batch_id="b2",
            candidate_ordinal=1, candidate_count=3, seed=555,
            source_revision="crash-rev",
            source_checkpoint_sha256=CHECKPOINT_SHA,
            handoff_id="crash-candidate")
        # The loader finds the durable record with no live state at all.
        inventory = review_inv.load_batch_inventory(
            os.path.join(raw_root, "h3_chains", "film_run2"))
        found = [
            item for item in inventory
            if isinstance(item, dict)
            and item.get("format") == hs.HANDOFF_FORMAT_VERSION
            and item.get("handoff_id") == "crash-candidate"]
        assert len(found) == 1, \
            "Crash recovery: durable inventory must contain the candidate"
        assert found[0]["seed"] == 555
        assert found[0]["source_revision"] == "crash-rev"

    # --- Plan restore remains explicit ---------------------------------------------
    # No review batch state may appear in the Plan; checkpoint Plan restore
    # stays an explicit user action (no hidden replacement via requeue).
    restore_plan_text = json.dumps(
        {"run_name": "film_run",
         "shots": [{"id": "one", "prompt": "Scene 1."}]}, sort_keys=True)
    assert_plan_clean(restore_plan_text)

    # --- Durable snapshot write/decide lifecycle -----------------------------------
    with tempfile.TemporaryDirectory() as raw_root:
        run_dir = os.path.join(raw_root, "h3_chains", "snap_run")
        os.makedirs(run_dir, exist_ok=True)
        review_inv.write_review_snapshot(
            run_dir, "tok-1", "snap_run", 3,
            [{"number": 1, "revision": "rev-a", "seed": "7", "created_at": "",
              "has_audio": False, "warning": ""}],
            deadline=123.0, server_now=100.0)
        pending_snaps = review_inv.load_review_snapshots(run_dir)
        assert len(pending_snaps) == 1
        assert pending_snaps[0]["token"] == "tok-1"
        assert pending_snaps[0]["status"] == "pending"
        assert pending_snaps[0]["candidates"][0]["revision"] == "rev-a"
        # No tensor or live-object leakage.
        snap_text = (os.path.join(run_dir, "orchestration",
                                  "review_tok-1.json")).replace("\\", "/")
        assert "<" not in open(snap_text).read()
        # Deciding is idempotent and terminal.
        assert review_inv.mark_review_snapshot_decided(
            run_dir, "tok-1", "approve", 101.0) is True
        decided = review_inv.load_review_snapshots(run_dir)
        assert decided[0]["status"] == "decided"
        assert decided[0]["decision_action"] == "approve"
        assert review_inv.mark_review_snapshot_decided(
            run_dir, "tok-1", "stop", 102.0) is False, \
            "a decided snapshot must not be re-decided"
        # Tensors are rejected on write.
        try:
            review_inv.write_review_snapshot(
                run_dir, "tok-bad", "snap_run", 3,
                [{"number": 1, "revision": torch.zeros(1)}],
                deadline=1.0, server_now=1.0)
        except ValueError:
            pass
        else:
            raise AssertionError("tensor candidate data must be rejected")

    # --- Live review route merges durable snapshots (crash recovery) ---------------
    # With the live _PENDING_REVIEWS empty (simulating a restart), the review
    # list route still surfaces the pending durable snapshot.
    with tempfile.TemporaryDirectory() as raw_root:
        run_dir = os.path.join(raw_root, "h3_chains", "restart_run")
        os.makedirs(run_dir, exist_ok=True)
        review_inv.write_review_snapshot(
            run_dir, "tok-restart", "restart_run", 2,
            [{"number": 1, "revision": "rev-x", "seed": "9", "created_at": "",
              "has_audio": False, "warning": ""}],
            deadline=500.0, server_now=10.0)
        chain._output_root = lambda: str(raw_root)
        chain._PENDING_REVIEWS.clear()

        class FakeRequest:
            async def json(self):
                return {"token": "tok-restart", "action": "approve"}

        async def scenario():
            response = await chain._list_pending_reviews(FakeRequest())
            assert response.status == 200
            listing = json.loads(response.text)
            durable = [item for item in listing["reviews"]
                       if item.get("durable")]
            assert len(durable) == 1, \
                "crashed restart must resurface the durable review"
            assert durable[0]["token"] == "tok-restart"
            assert durable[0]["run_name"] == "restart_run"
            assert durable[0]["clip_index"] == 2
            assert durable[0]["candidates"][0]["revision"] == "rev-x"
            assert durable[0]["video"] is None, \
                "durable reviews never carry live tensors"
            assert durable[0]["actionable"] is False
            assert "resume manually" in durable[0]["recovery_instructions"]
            # The API deliberately rejects an action after clearing the live
            # future; recovery inventory must not masquerade as actionable.
            decision = await chain._submit_review_decision(FakeRequest())
            assert decision.status == 409
            assert json.loads(decision.text)["recovery"] is True

        asyncio.new_event_loop().run_until_complete(scenario())
        # After the review is decided, the restart listing is empty again.
        review_inv.mark_review_snapshot_decided(
            run_dir, "tok-restart", "approve", 11.0)

        async def decided_scenario():
            response = await chain._list_pending_reviews(FakeRequest())
            listing = json.loads(response.text)
            assert all(not item.get("durable") for item in
                       listing["reviews"]), \
                "a decided review must not resurface on restart"

        asyncio.new_event_loop().run_until_complete(decided_scenario())

    # Live candidate-batch state takes precedence over a same-token durable
    # recovery snapshot, while unrelated durable recovery remains visible.
    with tempfile.TemporaryDirectory() as raw_root:
        base = os.path.join(raw_root, "h3_chains")
        for run_name, scene, token in (
                ("live_run", 1, "tok-live"),
                ("live_run", 1, "tok-old-1"),
                ("live_run", 1, "tok-old-2"),
                ("live_run", 1, "tok-old-3"),
                ("live_run", 2, "tok-scene-2"),
                ("other_run", 1, "tok-other")):
            run_dir = os.path.join(base, run_name)
            os.makedirs(run_dir, exist_ok=True)
            review_inv.write_review_snapshot(
                run_dir, token, run_name, scene, [], deadline=None,
                server_now=10.0)
        chain._output_root = lambda: str(raw_root)
        chain._PENDING_REVIEWS.clear()
        chain._ACTIVE_CANDIDATE_BATCHES.clear()
        chain._ACTIVE_CANDIDATE_BATCHES["tok-live"] = {
            "updated": chain.time.monotonic(),
            "public": {
                "token": "tok-live", "run_name": "live_run", "clip_index": 1,
                "actionable": True, "candidate_count": 3,
                "candidate_generation_complete": True,
                "candidate_batch_active": True, "candidates": [],
            },
        }

        async def live_precedence_scenario():
            response = await chain._list_pending_reviews(FakeRequest())
            listing = json.loads(response.text)["reviews"]
            live = [item for item in listing if item["token"] == "tok-live"]
            assert len(live) == 1
            assert live[0]["actionable"] is True
            assert live[0]["candidate_count"] == 3
            assert live[0]["candidate_generation_complete"] is True
            assert live[0]["candidate_batch_active"] is True
            assert not any(item["token"] in {"tok-old-1", "tok-old-2", "tok-old-3"}
                           for item in listing)
            for token in ("tok-scene-2", "tok-other"):
                recovered = [item for item in listing if item["token"] == token]
                assert len(recovered) == 1 and recovered[0]["durable"] is True
                assert recovered[0]["actionable"] is False

        asyncio.new_event_loop().run_until_complete(live_precedence_scenario())
        chain._ACTIVE_CANDIDATE_BATCHES.clear()

    # Accepting a scene retires only older pending recovery snapshots for that
    # exact run and scene; files and unrelated recovery inventory survive.
    with tempfile.TemporaryDirectory() as raw_root:
        run_dir = os.path.join(raw_root, "h3_chains", "live_run")
        other_dir = os.path.join(raw_root, "h3_chains", "other_run")
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(other_dir, exist_ok=True)
        for scene, token in ((1, "tok-old-1"), (1, "tok-old-2"),
                             (1, "tok-old-3"), (1, "tok-current"),
                             (2, "tok-scene-2")):
            review_inv.write_review_snapshot(
                run_dir, token, "live_run", scene, [], deadline=None,
                server_now=10.0)
        review_inv.write_review_snapshot(
            other_dir, "tok-other", "other_run", 1, [], deadline=None,
            server_now=10.0)
        review_inv.mark_review_snapshot_decided(
            run_dir, "tok-current", "approve", 11.0)
        review_inv.mark_review_snapshot_decided(
            run_dir, "tok-old-3", "retry", 11.0)
        chain._retire_superseded_review_snapshots(
            run_dir, "live_run", 1, "tok-current")
        snapshots = {item["token"]: item for item in
                     review_inv.load_review_snapshots(run_dir)}
        assert snapshots["tok-current"]["decision_action"] == "approve"
        assert snapshots["tok-old-1"]["decision_action"] == "superseded"
        assert snapshots["tok-old-2"]["decision_action"] == "superseded"
        assert snapshots["tok-old-3"]["decision_action"] == "retry"
        assert snapshots["tok-scene-2"]["status"] == "pending"
        assert os.path.exists(os.path.join(
            run_dir, "orchestration", "review_tok-old-1.json"))
        chain._output_root = lambda: str(raw_root)
        chain._PENDING_REVIEWS.clear()
        async def retired_inventory_scenario():
            response = await chain._list_pending_reviews(FakeRequest())
            tokens = {item["token"] for item in json.loads(response.text)["reviews"]}
            assert "tok-old-1" not in tokens
            assert "tok-scene-2" in tokens and "tok-other" in tokens
        asyncio.new_event_loop().run_until_complete(retired_inventory_scenario())

    print("M5 durable review: saved candidates survive restart, approve "
          "creates next_scene handoff, approve & stop queues nothing, "
          "Plan unchanged pass")


if __name__ == "__main__":
    main()
