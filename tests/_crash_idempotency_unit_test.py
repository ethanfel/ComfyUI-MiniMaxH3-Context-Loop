#!/usr/bin/env python3
"""M6 crash / idempotency hardening.

Deterministic coverage for the crash points in
CRASH_RECOVERY_IDEMPOTENCY_SPEC.md plus no-blind-auto-run after restart.
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
import types

import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "h3_crash_idempotency_unit"

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
shared_nodes._streams_from_latent = lambda _latent: [
    torch.zeros(2, 1, 1, 1), torch.zeros(2)
]
sys.modules[shared_nodes.__name__] = shared_nodes

spec_chain = importlib.util.spec_from_file_location(
    PACKAGE + ".chain_nodes", ROOT / "chain_nodes.py")
chain = importlib.util.module_from_spec(spec_chain)
sys.modules[spec_chain.name] = chain
spec_chain.loader.exec_module(chain)

spec_hs = importlib.util.spec_from_file_location(
    PACKAGE + ".handoff_state", ROOT / "handoff_state.py")
hs = importlib.util.module_from_spec(spec_hs)
sys.modules[spec_hs.name] = hs
spec_hs.loader.exec_module(hs)


def make_plan(run_name):
    policy = chain._contract_compose_chain_policy(
        chain._contract_audio_policy("source", "on", "off"),
        chain._contract_transition_policy(
            "guide", expert_override=True,
            continuation_mode="guide", context_length=5),
        audio_context_length=5)
    return chain._normalize_plan(
        json.dumps({"shots": [
            {"id": "one", "prompt": "Opening action.", "length": 124},
            {"id": "two", "prompt": "Continuation action.",
             "length": 124},
            {"id": "three", "prompt": "Final action.",
             "length": 124},
        ]}),
        run_name, 64, 64, 5, "video", "head", "disabled",
        "source_track", 5, 1.0, 8, 7, 18, "stack:auto:v1", 0,
        "guide", policy)


def make_segment(plan):
    return {
        "index": 1,
        "id": "one",
        "revision": "ab" * 16,
        "raw_frames": 124,
        "delivered_frames": 124,
        "history_hash": "hist",
        "seed": plan["shots"][0]["seed"],
    }


def plan_invariance(plan):
    serialized = json.dumps(plan)
    for forbidden in (
        "orchestration_mode", "handoff_id", "prompt_id", "candidate_batch",
        "candidate_ordinal", "cleanup_delay", "queue_state", "pending_action",
        "retry_attempt", "browser_session", "candidate_batch_id",
        "candidate_count", "awaiting_review",
    ):
        assert forbidden not in serialized


def main():
    # 1. Before checkpoint save -> nothing committed.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        plan = make_plan("crash_1")
        assert not (pathlib.Path(raw_root) / "h3_chains" / "crash_1").exists()
        plan_invariance(plan)

    # 2. After checkpoint save, before orchestration metadata.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        plan = make_plan("crash_2")
        run_dir = pathlib.Path(raw_root) / "h3_chains" / "crash_2"
        (run_dir / "segments").mkdir(parents=True, exist_ok=True)
        seg = make_segment(plan)
        h1 = chain._write_next_scene_handoff(plan, 1, 3, seg)
        h2 = chain._write_next_scene_handoff(plan, 1, 3, seg)
        assert h1["handoff_id"] == h2["handoff_id"]
        assert len(list((run_dir / "orchestration").glob("*.json"))) == 1
        plan_invariance(plan)

    # 3. After handoff write, before terminal event.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        store = hs.HandoffStore(raw_root)
        rec = store.create(
            "crash_3", action="next_scene", scene=2, start_clip=2,
            end_clip=3, seed=123, source_revision="rev-1",
            source_checkpoint_sha256="ab" * 32,
            workflow_fingerprint="wf", handoff_id="h3")
        assert rec["status"] == "pending"
        assert store.load("crash_3", "h3")["status"] == "pending"

    # 4. After terminal, before auto-queue.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        store = hs.HandoffStore(raw_root)
        store.create(
            "crash_4", action="next_scene", scene=2, start_clip=2,
            end_clip=3, seed=123, source_revision="rev-1",
            source_checkpoint_sha256="ab" * 32,
            workflow_fingerprint="wf", handoff_id="h4")
        assert store.load("crash_4", "h4")["status"] == "pending"

    # 5. After claim, before queue acceptance.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        store = hs.HandoffStore(raw_root)
        rec = store.create(
            "crash_5", action="next_scene", scene=2, start_clip=2,
            end_clip=3, seed=123, source_revision="rev-1",
            source_checkpoint_sha256="ab" * 32,
            workflow_fingerprint="wf", handoff_id="h5", max_attempts=2)
        assert store.claim("crash_5", rec["handoff_id"],
                           source_prompt_id="p1")["status"] == "claimed"
        assert store.release("crash_5", rec["handoff_id"], reason="busy")["status"] == "pending"
        assert store.claim("crash_5", rec["handoff_id"],
                           source_prompt_id="p2")["attempt"] == 2
        assert store.release("crash_5", rec["handoff_id"],
                             reason="busy again")["status"] == "failed"

    # 6. Browser reload -> reconstruct from backend durable state.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        store = hs.HandoffStore(raw_root)
        store.create(
            "crash_6", action="next_scene", scene=2, start_clip=2,
            end_clip=3, seed=123, source_revision="rev-1",
            source_checkpoint_sha256="ab" * 32,
            workflow_fingerprint="wf", handoff_id="h6")
        assert any(item["handoff_id"] == "h6" for item in store.list("crash_6"))

    # 7. Server restart -> no blind auto-run.
    with tempfile.TemporaryDirectory() as raw_root:
        chain._output_root = lambda: str(raw_root)
        store = hs.HandoffStore(raw_root)
        store.create(
            "crash_7", action="next_scene", scene=2, start_clip=2,
            end_clip=3, seed=123, source_revision="rev-1",
            source_checkpoint_sha256="ab" * 32,
            workflow_fingerprint="wf", handoff_id="h7")
        fresh = hs.HandoffStore(raw_root)
        assert fresh.load("crash_7", "h7")["status"] == "pending"
        assert any(item["handoff_id"] == "h7" for item in fresh.list("crash_7"))

    print("M6 crash/idempotency: 7 crash points, bounded retry, reload/restart, Plan invariant pass")


if __name__ == "__main__":
    main()
