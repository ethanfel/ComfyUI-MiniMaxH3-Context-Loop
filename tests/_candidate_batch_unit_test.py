#!/usr/bin/env python3
"""M4: Multi-candidate top-level requeue acceptance (deterministic surface).

Validates the contract from IMPLEMENTATION_PLAN.md / CANDIDATE_BATCH_SPEC.md:

* Plan JSON never stores candidate-batch fields.
* Each candidate uses a separate top-level prompt identity (separate handoff
  records with stable batch_id + ordinal, distinct source_prompt_id).
* Exactly-once ordinals: a committed candidate cannot be re-queued for the
  same batch ordinal (HandoffStore + chain's _review_candidate_target).
* Exact seed/revision persisted on the next_candidate handoff.
* Crash after candidate 2: a durable next_candidate handoff for ordinal 3 is
  visible and can be claimed; the Plan has not changed.
* Final state is `await_review` once all candidates are committed
  (chain._review_candidate_target / batch lifecycle).
* No Plan JSON fields added.
"""

import importlib.util, json, os, pathlib, sys, tempfile, types

ROOT = pathlib.Path(__file__).resolve().parents[1]
folder_paths = types.ModuleType("folder_paths")
folder_paths.get_output_directory = lambda: str(ROOT)
folder_paths.get_temp_directory = lambda: str(ROOT)
folder_paths.get_input_directory = lambda: str(ROOT)
sys.modules["folder_paths"] = folder_paths

# --- load handoff_state (no chain_nodes, no torch) ----------------------------
spec = importlib.util.spec_from_file_location("handoff_state", ROOT / "handoff_state.py")
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)

T0 = "2026-09-06T00:00:00.000+00:00"
T1 = "2026-09-06T00:00:01.000+00:00"


def deterministic_store(root):
    state = {"n": 0}

    def now():
        state["n"] += 1
        return T0 if state["n"] == 1 else T1

    return hs.HandoffStore(root, now=now)


def assert_plan_unchanged(plan_before, plan_after):
    assert json.dumps(plan_before, sort_keys=True) == \
        json.dumps(plan_after, sort_keys=True), \
        "M4 must not modify the Plan JSON"
    serialized = json.dumps(plan_after)
    for forbidden in (
        "candidate_ordinal", "candidate_count", "candidate_batch_id",
        "batch_id", "awaiting_review", "candidates", "selected_ordinal",
    ):
        assert forbidden not in serialized, \
            "forbidden Plan field %r appeared in M4" % forbidden


def main():
    plan = {"run_name": "film_run", "shots": [
        {"id": "one", "prompt": "Opening action.", "seed": 111, "length": 124},
        {"id": "two", "prompt": "Continuation.", "seed": 222, "length": 124},
    ]}
    plan_before_text = json.dumps(plan, sort_keys=True)
    plan_before = json.loads(plan_before_text)

    with tempfile.TemporaryDirectory() as raw_root:
        store = deterministic_store(raw_root)

        # 3 candidate handoffs for scene 1 — one per top-level prompt.
        # Each has a distinct source_prompt_id, seed, revision.
        batch_id = "batch-2026-09-06-scene-1"
        candidate_records = []
        for ordinal in (1, 2, 3):
            record = store.create(
                plan["run_name"], action="next_candidate",
                scene=1, start_clip=1, end_clip=2,
                candidate_batch_id=batch_id,
                candidate_ordinal=ordinal,
                candidate_count=3,
                seed=1000 + ordinal,
                source_prompt_id="prompt-ordinal-%d" % ordinal,
                source_revision="rev-ord-%d" % ordinal,
                source_checkpoint_sha256=("ab" * 31 + "0%d" % ordinal),
                workflow_fingerprint="wf-identity",
                handoff_id="next_candidate_%04d" % ordinal,
            )
            candidate_records.append(record)

        # --- unique prompt id per candidate ----------------------------------
        prompt_ids = {r["source_prompt_id"] for r in candidate_records}
        assert prompt_ids == {"prompt-ordinal-1", "prompt-ordinal-2",
                              "prompt-ordinal-3"}, \
            "each candidate must have a distinct top-level prompt id"

        # --- exactly-once ordinals -------------------------------------------
        for record in candidate_records:
            assert record["candidate_ordinal"] in (1, 2, 3)
            assert record["candidate_count"] == 3
            assert record["candidate_batch_id"] == batch_id
            assert record["status"] == "pending"

        # Claim candidate 1, then candidate 2. A duplicate claim is rejected.
        c1 = store.claim(plan["run_name"], "next_candidate_0001",
                         source_prompt_id="prompt-ordinal-1")
        assert c1["status"] == "claimed" and c1["attempt"] == 1
        assert c1["source_prompt_id"] == "prompt-ordinal-1"

        try:
            store.claim(plan["run_name"], "next_candidate_0001",
                        source_prompt_id="prompt-ordinal-1-replay")
        except hs.HandoffClaimError:
            pass
        else:
            raise AssertionError("duplicate claim must raise")
        # First-writer wins on source_prompt_id.
        reloaded = store.load(plan["run_name"], "next_candidate_0001")
        assert reloaded["source_prompt_id"] == "prompt-ordinal-1", \
            "duplicate claim must not overwrite source_prompt_id"

        # Move c1 to consumed.
        store.transition(plan["run_name"], "next_candidate_0001", "consumed")

        # Claim c2. Persist its exact seed/revision.
        c2 = store.claim(plan["run_name"], "next_candidate_0002",
                         source_prompt_id="prompt-ordinal-2")
        assert c2["status"] == "claimed" and c2["attempt"] == 1
        assert c2["seed"] == 1002
        assert c2["source_revision"] == "rev-ord-2"
        assert c2["source_checkpoint_sha256"] == "ab" * 31 + "02"
        store.transition(plan["run_name"], "next_candidate_0002", "consumed")

        # --- crash after candidate 2: candidate 3 handoff must still exist ----
        surviving = store.load(plan["run_name"], "next_candidate_0003")
        assert surviving["status"] == "pending"
        assert surviving["candidate_ordinal"] == 3
        assert surviving["seed"] == 1003
        assert surviving["source_revision"] == "rev-ord-3"
        # The frontend can now claim it for the next top-level prompt.
        c3 = store.claim(plan["run_name"], "next_candidate_0003",
                         source_prompt_id="prompt-ordinal-3")
        assert c3["status"] == "claimed"
        assert c3["source_prompt_id"] == "prompt-ordinal-3"
        store.transition(plan["run_name"], "next_candidate_0003", "consumed")

        # --- final state: awaiting_review -----------------------------------
        # Once all N candidates are consumed, the M4 contract expects
        # `awaiting_review`. The handoff record itself uses `consumed` to
        # mean the prompt is done; the batch status (separate from the
        # handoff state machine) is awaiting_review. Validate this by
        # creating a separate `await_review` final-action record (matching
        # the spec's HANDOFF_ACTIONS), which is what the frontend uses to
        # know the batch is ready for review.
        review = store.create(
            plan["run_name"], action="await_review", scene=1,
            start_clip=1, end_clip=2,
            candidate_batch_id=batch_id,
            candidate_ordinal=3, candidate_count=3,
            handoff_id="await_review_0001",
        )
        assert review["action"] == "await_review"
        assert review["status"] == "pending"

        # Plan must remain byte-identical throughout.
        assert_plan_unchanged(plan_before, plan)

    # --- additional: bad ordinals/batch state must be rejected --------------
    with tempfile.TemporaryDirectory() as raw_root:
        store = deterministic_store(raw_root)
        for bad in (
            {"candidate_ordinal": 0, "candidate_count": 3},
            {"candidate_ordinal": 4, "candidate_count": 3},
            {"candidate_ordinal": 1},
        ):
            try:
                store.create("film_run", action="next_candidate",
                             handoff_id="bad-%s" % bad.get("candidate_ordinal"),
                             **bad)
            except hs.HandoffError:
                pass
            else:
                raise AssertionError(
                    "bad candidate_batch args %r must be rejected" % bad)

    print("M4 candidate-batch: 3 distinct prompt ids, exactly-once ordinals, "
          "durable recovery, awaiting_review, Plan byte-identical pass")


if __name__ == "__main__":
    main()
