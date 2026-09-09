#!/usr/bin/env python3
"""Durable H3 handoff state: atomic, exactly-once, Plan-independent."""

import contextlib
import io
import json
import pathlib
import runpy
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from handoff_state import (  # noqa: E402
    HANDOFF_FORMAT_VERSION,
    DEFAULT_MAX_ATTEMPTS,
    HandoffClaimError,
    HandoffCorruptError,
    HandoffError,
    HandoffExistsError,
    HandoffNotFoundError,
    HandoffStore,
    IllegalHandoffTransitionError,
)
import handoff_state as handoff_state_module

T0 = "2026-09-06T00:00:00.000+00:00"
T1 = "2026-09-06T00:00:01.000+00:00"

CHECKPOINT_SHA = "ab" * 32


def new_store(root):
    """Store with a deterministic clock: first call T0, then T1 forever."""
    state = {"n": 0}

    def now():
        state["n"] += 1
        return T0 if state["n"] == 1 else T1

    return HandoffStore(root, now=now)


def main():
    with tempfile.TemporaryDirectory() as raw_root:
        root = pathlib.Path(raw_root)
        store = new_store(root)

        # --- atomic create/read/update -------------------------------------
        record = store.create(
            "film_run", action="next_scene", scene=6, start_clip=7,
            source_prompt_id="prompt-abc",
            source_revision="f3" * 16,
            source_checkpoint_sha256=CHECKPOINT_SHA,
            workflow_fingerprint="workflow-identity")
        assert record["format"] == HANDOFF_FORMAT_VERSION
        assert record["status"] == "pending"
        assert record["attempt"] == 0
        assert record["max_attempts"] == DEFAULT_MAX_ATTEMPTS
        assert record["created_at"] == T0
        assert record["updated_at"] == T0
        assert record["handoff_id"]
        assert record["candidate_ordinal"] is None
        assert record["seed"] is None
        assert record["source_checkpoint_sha256"] == CHECKPOINT_SHA

        path = root / "h3_chains" / "film_run" / "orchestration" / \
            ("%s.json" % record["handoff_id"])
        assert path.is_file(), "record must live under the run-local " \
            "orchestration directory"
        on_disk = path.read_text(encoding="utf-8")
        assert on_disk.endswith("\n")
        parsed = json.loads(on_disk)
        assert parsed == record
        # Atomic replacement leaves no temp litter in the orchestration dir.
        leftovers = [name for name in path.parent.iterdir()
                     if name.name.endswith(".tmp")]
        assert leftovers == []
        # Keys are written sorted, so the on-disk order is stable.
        assert list(parsed) == sorted(record)

        # Re-read matches; reads never mutate timestamps.
        assert store.load("film_run", record["handoff_id"]) == record
        branch_record = store.create("film_run", action="next_scene", scene=6,
                                     working_branch_id="a" * 32)
        assert store.load("film_run", branch_record["handoff_id"])["working_branch_id"] == "a" * 32
        assert "working_branch_id" not in record, "Legacy handoff shape must stay unchanged"
        try:
            store.load("film_run", "never-created")
        except HandoffNotFoundError:
            pass
        else:
            raise AssertionError("missing record must raise")

        # Every update keeps the file a complete valid JSON document.
        claimed = store.claim("film_run", record["handoff_id"],
                              claimant="test-claimant")
        assert claimed["status"] == "claimed"
        assert claimed["attempt"] == 1
        assert claimed["claimant"] == "test-claimant"
        assert claimed["created_at"] == T0
        assert claimed["updated_at"] == T1
        assert json.loads(path.read_text(encoding="utf-8")) == claimed
        queued = store.transition("film_run", record["handoff_id"], "queued")
        assert queued["status"] == "queued"
        consumed = store.transition("film_run", record["handoff_id"],
                                    "consumed")
        assert consumed["status"] == "consumed"
        assert consumed["updated_at"] == T1

        # --- legal transition matrix -----------------------------------------
        for source, target in (
                ("pending", "cancelled"),
                ("claimed", "queued"),
                ("claimed", "consumed"),
                ("claimed", "uncertain"),
                ("queued", "failed")):
            hop = store.create("film_run", action="next_scene",
                               handoff_id="hop-%s" % target)
            if source != "pending":
                store.claim("film_run", hop["handoff_id"])
            moved = store.transition("film_run", hop["handoff_id"], target)
            assert moved["status"] == target

        # --- illegal transitions ---------------------------------------------
        def reach(run, hid, source):
            if source == "claimed":
                store.claim(run, hid)
            elif source == "queued":
                store.claim(run, hid)
                store.transition(run, hid, "queued")
            elif source == "consumed":
                store.claim(run, hid)
                store.transition(run, hid, "queued")
                store.transition(run, hid, "consumed")
            elif source == "cancelled":
                store.transition(run, hid, "cancelled")
            elif source == "failed":
                store.claim(run, hid)
                store.release(run, hid)
            elif source == "uncertain":
                store.claim(run, hid)
                store.transition(run, hid, "uncertain")
            assert store.load(run, hid)["status"] == source

        for source, target in (
                ("pending", "queued"),
                ("pending", "consumed"),
                ("pending", "failed"),
                ("claimed", "pending"),
                ("consumed", "pending"),
                ("consumed", "failed"),
                ("cancelled", "pending"),
                ("failed", "pending"),
                ("uncertain", "pending"),
                ("uncertain", "queued")):
            hop = store.create("film_run", action="next_scene",
                               handoff_id="bad-%s-%s" % (source, target),
                               max_attempts=1)
            reach("film_run", hop["handoff_id"], source)
            before = store.load("film_run", hop["handoff_id"])
            try:
                store.transition("film_run", hop["handoff_id"], target)
            except IllegalHandoffTransitionError as exc:
                assert source in str(exc) and target in str(exc)
            else:
                raise AssertionError(
                    "transition %s -> %s must be illegal" % (source, target))
            assert store.load("film_run", hop["handoff_id"]) == before, \
                "illegal transition must leave the record untouched"

        # --- exactly-once claim ------------------------------------------------
        fresh = store.create("film_run", action="next_candidate",
                             candidate_batch_id="batch-1",
                             candidate_ordinal=2, candidate_count=3,
                             seed=17)
        assert fresh["candidate_ordinal"] == 2
        first = store.claim("film_run", fresh["handoff_id"])
        assert first["status"] == "claimed" and first["attempt"] == 1
        for _ in range(2):
            try:
                store.claim("film_run", fresh["handoff_id"])
            except HandoffClaimError as exc:
                assert "not pending" in str(exc)
            else:
                raise AssertionError("duplicate claim must fail")
        assert store.load("film_run",
                          fresh["handoff_id"])["attempt"] == 1, \
            "duplicate claims must not consume attempts"
        store.transition("film_run", fresh["handoff_id"], "consumed")
        try:
            store.claim("film_run", fresh["handoff_id"])
        except HandoffClaimError as exc:
            assert "already consumed" in str(exc)
        else:
            raise AssertionError("terminal handoff must never re-claim")

        # --- retry bounds -------------------------------------------------------
        bounded = store.create("film_run", action="next_scene",
                               handoff_id="bounded", max_attempts=2)
        assert bounded["max_attempts"] == 2
        assert store.claim("film_run", "bounded")["attempt"] == 1
        released = store.release("film_run", "bounded",
                                 reason="queue busy")
        assert released["status"] == "pending"
        assert released["attempt"] == 1
        assert released["last_release_reason"] == "queue busy"
        assert store.claim("film_run", "bounded")["attempt"] == 2
        failed = store.release("film_run", "bounded",
                               reason="queue busy again")
        assert failed["status"] == "failed", "exhausted budget must fail, " \
            "not retry forever"
        try:
            store.claim("film_run", "bounded")
        except HandoffClaimError as exc:
            assert "already failed" in str(exc)
        else:
            raise AssertionError("failed handoff must never re-claim")
        try:
            store.release("film_run", "bounded")
        except IllegalHandoffTransitionError:
            pass
        else:
            raise AssertionError("releasing a failed handoff must fail")
        try:
            store.create("film_run", action="next_scene", max_attempts=0)
        except HandoffError:
            pass
        else:
            raise AssertionError("max_attempts must be a positive integer")

        # --- corrupt JSON --------------------------------------------------------
        store.create("film_run", action="next_scene", handoff_id="corrupt")
        victim_path = root / "h3_chains" / "film_run" / "orchestration" / \
            "corrupt.json"
        corrupt_bytes = b"not json {{{"
        victim_path.write_bytes(corrupt_bytes)
        try:
            store.load("film_run", "corrupt")
        except HandoffCorruptError as exc:
            assert "corrupt" in str(exc).lower()
        else:
            raise AssertionError("corrupt JSON must raise")
        assert victim_path.read_bytes() == corrupt_bytes, \
            "corrupt record must be left untouched for manual recovery"
        victim_path.write_text(json.dumps({
            "format": "h3_top_level_handoff_v999",
            "handoff_id": "corrupt", "run_name": "film_run",
            "action": "next_scene", "status": "pending", "attempt": 0,
            "max_attempts": 3, "created_at": T0, "updated_at": T0,
        }), encoding="utf-8")
        try:
            store.load("film_run", "corrupt")
        except HandoffCorruptError as exc:
            assert "Unknown handoff format" in str(exc)
        else:
            raise AssertionError("unknown format must raise")
        corrupt_entries = [r for r in store.list("film_run")
                           if r.get("handoff_id") == "corrupt"]
        assert len(corrupt_entries) == 1
        assert corrupt_entries[0]["_corrupt"] is True
        assert corrupt_entries[0]["_corrupt_reason"]

        # --- run-path safety -------------------------------------------------------
        for unsafe in ("../evil", "a/b", "a\\b", " ", "", "....",
                       "x" * 97, "no spaces allowed", "..", ".hidden"):
            try:
                store.create(unsafe, action="next_scene")
            except HandoffError:
                pass
            else:
                raise AssertionError("unsafe run_name %r must be rejected"
                                     % unsafe)
        for unsafe in ("../evil", "a/b", "", "x" * 129, "has space"):
            try:
                store.load("film_run", unsafe)
            except HandoffError:
                pass
            else:
                raise AssertionError("unsafe handoff_id %r must be rejected"
                                     % unsafe)
        store.create("film_run", action="next_scene",
                     handoff_id="ok.name-1_2")
        assert store.load("film_run", "ok.name-1_2")["handoff_id"] == \
            "ok.name-1_2"
        try:
            store.create("film_run", action="next_scene",
                         handoff_id="ok.name-1_2")
        except HandoffExistsError:
            pass
        else:
            raise AssertionError("duplicate handoff_id must raise")

        # --- no tensors or models ----------------------------------------------------
        for kwargs in ({"seed": "not-an-int"},
                       {"scene": 1.5},
                       {"candidate_batch_id": types.SimpleNamespace()}):
            try:
                store.create("film_run", action="next_scene",
                             handoff_id="heavy-%s" % list(kwargs)[0],
                             **kwargs)
            except HandoffError:
                pass
            else:
                raise AssertionError("field %r must be rejected"
                                     % list(kwargs)[0])

        def assert_lightweight_rejected(key, value):
            try:
                handoff_state_module._assert_lightweight_value(key, value)
            except HandoffError as exc:
                assert "lightweight" in str(exc)
            else:
                raise AssertionError("value for %r must be rejected" % key)

        assert_lightweight_rejected(
            "model", types.SimpleNamespace(tower="MODEL"))
        assert_lightweight_rejected(
            "latent", [1, {"nested": object()}])
        assert_lightweight_rejected(
            "conditioning", {"positive": [object()]})
        # Legal lightweight payloads pass.
        handoff_state_module._assert_lightweight_value(
            "ok", {"a": [1, 2, "s", None, True, 3.5], "b": None})

        # --- bounded retry metadata stays sane --------------------------------------
        for entry in store.list("film_run"):
            if entry.get("_corrupt"):
                continue
            assert entry["attempt"] <= entry["max_attempts"]
            assert entry["status"] in (
                "pending", "claimed", "queued", "consumed",
                "cancelled", "failed", "uncertain")

    # --- M3 additive: end_clip + claim-time source_prompt_id ----------------
    range_record = store.create(
        "film_run", action="next_scene", scene=2, start_clip=2,
        end_clip=5, handoff_id="range_handoff")
    assert range_record["end_clip"] == 5
    assert store.load("film_run", "range_handoff")["end_clip"] == 5
    for bad_end, handoff_id in ((1, "bad_end_low"), (5.5, "bad_end_f")):
        try:
            store.create("film_run", action="next_scene", scene=2,
                         start_clip=2, end_clip=bad_end,
                         handoff_id=handoff_id)
        except HandoffError:
            pass
        else:
            raise AssertionError("end_clip %r must raise "
                                 "(below start_clip or non-integer)" % bad_end)
    # end_clip stays optional: records without it remain valid.
    assert store.create("film_run", action="next_scene", scene=4,
                        handoff_id="no_end_clip")["end_clip"] is None

    claim_id = store.create("film_run", action="next_scene", scene=3,
                            start_clip=3, end_clip=5,
                            handoff_id="claim_pid")["handoff_id"]
    claimed_pid = store.claim("film_run", claim_id, "coordinator",
                              source_prompt_id="prompt-source-n")
    assert claimed_pid["status"] == "claimed"
    assert claimed_pid["source_prompt_id"] == "prompt-source-n"
    # First writer wins: a duplicate claim can never re-stamp the id.
    try:
        store.claim("film_run", claim_id, "rival",
                    source_prompt_id="prompt-rival")
    except HandoffClaimError:
        pass
    else:
        raise AssertionError("duplicate claim must raise")
    assert store.load("film_run", claim_id)["source_prompt_id"] \
        == "prompt-source-n"

    # --- no Plan JSON modification ---------------------------------------------------
    plan_invariance()

    print("H3 Handoff State: atomic, exactly-once, Plan-independent "
          "durable handoffs pass")


def plan_invariance():
    """Orchestration operations must leave the Plan byte-identical."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ns = runpy.run_path(str(ROOT / "tests" / "_preflight_unit_test.py"))
    chain = ns["chain"]
    # Shot lengths stay above the MiniMax H3 minimum (>= 2 seconds);
    # 124 frames at 24 fps is ~5 seconds per shot.
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        chain._contract_transition_policy(
            "guide", expert_override=True,
            continuation_mode="guide", context_length=5),
        audio_context_length=5)
    plan = chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "Opening action.", "length": 124},
            {"id": "two", "prompt": "Continuation action.",
             "length": 124},
        ]}),
        "handoff_invariance", 64, 64, 5, "video", "head", "disabled",
        "source_track", 5, 1.0, 8, 7, 18, "stack:auto:v1", 0,
        "guide", policy)
    fixture = ROOT / "tests" / "fixtures" / "v0_4_public_contract.json"
    fixture_before = fixture.read_text(encoding="utf-8")
    before = json.dumps(plan, sort_keys=True)
    record_text = None
    with tempfile.TemporaryDirectory() as raw_root:
        store = new_store(raw_root)
        record = store.create(
            "handoff_invariance", action="next_scene", scene=1,
            start_clip=2, source_prompt_id="prompt-x",
            source_revision="f3" * 16,
            source_checkpoint_sha256=CHECKPOINT_SHA,
            workflow_fingerprint="wf-1")
        store.claim("handoff_invariance", record["handoff_id"])
        store.transition("handoff_invariance", record["handoff_id"],
                         "queued")
        store.transition("handoff_invariance", record["handoff_id"],
                         "consumed")
        record_text = next(
            pathlib.Path(store.orchestration_dir("handoff_invariance"))
            .glob("*.json")
        ).read_text(encoding="utf-8")
    after = json.dumps(plan, sort_keys=True)
    assert before == after, "orchestration must not modify the Plan"
    assert fixture.read_text(encoding="utf-8") == fixture_before
    plan_text = json.dumps(plan)
    for forbidden in ("orchestration_mode", "handoff_id", "prompt_id",
                      "candidate_batch", "candidate_ordinal",
                      "cleanup_delay", "queue_state", "pending_action",
                      "retry_attempt", "browser_session"):
        assert forbidden not in plan_text, \
            "forbidden Plan field %r appeared" % forbidden
    # The record never borrows Plan internals (no prompt text, no shots).
    for plan_internal in ("Opening action.", "Continuation action.",
                          "shots", "prompt_prefix", "generation_fingerprint"):
        assert plan_internal not in record_text, \
            "handoff record duplicated Plan data %r" % plan_internal


if __name__ == "__main__":
    main()
