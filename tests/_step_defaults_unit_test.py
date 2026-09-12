"""Issue 52/64: resolved sampler steps survive queue changes and scene recursion."""
import json
import tempfile
from pathlib import Path

from _modern_plan_unit_test import chain, folder_paths, policy


def build(document, default_steps):
    return chain.MiniMaxH3ChainPlanModern().build(
        plan_json=json.dumps(document), chain_policy=policy,
        run_name="step-regression", generation_fingerprint="test",
        width=960, height=544, encode_mode="video", crop="disabled",
        default_duration_seconds=3, default_steps=default_steps, base_seed=7,
        segment_crf=18, video_blend_frames=0,
    )[0]


with tempfile.TemporaryDirectory(prefix="h3-step-regression-") as temporary:
    folder_paths.get_output_directory = lambda: temporary
    document = {"shots": [
        {"id": "one", "prompt": "Opening.", "length": 73, "seed": 13},
        {"id": "two", "prompt": "Continue.", "length": 73, "seed": 17},
    ]}
    original = json.dumps(document)
    # A new queue's default is not latched by the previous run. Current Scene
    # exposes the same resolved value for both indices, including scene 2.
    for steps in (12, 6, 8):
        plan = build(document, steps)
        for index in (1, 2):
            result = chain.MiniMaxH3ChainCurrent().current(
                {"plan": plan, "index": index})["result"]
            assert result[7] == steps, (index, result[7], steps)
            assert result[5] == document["shots"][index - 1]["seed"]
    assert json.dumps(document) == original

    # Existing saved overrides retain their meaning until explicitly cleared.
    document["defaults"] = {"steps": 20}
    document["shots"][0]["steps"] = 12
    assert [s["steps"] for s in build(document, 8)["shots"]] == [12, 20]
    document["defaults"]["steps"] = 8  # actual frontend edit
    assert [s["steps"] for s in build(document, 8)["shots"]] == [12, 8]
    del document["shots"][0]["steps"]  # explicit Use default action
    assert [s["steps"] for s in build(document, 8)["shots"]] == [8, 8]

    # Updated shipped examples inherit the chosen default for every scene.
    root = Path(__file__).resolve().parents[1]
    for name in ("Ref2V Basic", "Ref2V Tagged", "Ref2V Studio",
                 "Ref2V Studio Source Audio", "Ref2V Tagged Source Audio"):
        recipe = json.loads((root / "tools" / "v06" / "recipes" /
                            (name + " - MiniMax H3 0.6.json")).read_text())
        author = next(n for n in recipe["nodes"]
                      if n["type"] == "MiniMaxH3ChainPlanModern")
        parsed = json.loads(author["settings"]["plan_json"])
        assert all(s["steps"] == 8 for s in build(parsed, 8)["shots"]), name
print("Step counts: actual Current Scene outputs, changed queues, saved overrides, and five recipes pass")
