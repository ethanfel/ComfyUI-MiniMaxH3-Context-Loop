#!/usr/bin/env python3
"""Compare browser timing with the real Python compiler using CPU-only inputs."""

import json
import pathlib
import runpy
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "tests" / "_chapter_delivery_unit_test.py"))
chain = fixture["chain"]


def main():
    durations = [1, 6, 8, 10, 12, 15, 0, -1]
    durations += [frames / chain.FPS for frames in range(1, chain.MAX_H3_FRAMES + 2)]
    durations += [(frames + .25) / chain.FPS for frames in range(1, chain.MAX_H3_FRAMES + 1)]
    durations += [(frames / chain.FPS) + delta
                  for frames in range(5, chain.MAX_H3_FRAMES + 1, 17)
                  for delta in [-1e-12, 1e-12, 1e-7]]
    plans = [
        {"shots": [{"id": f"s{i}", "prompt": f"Scene {i}", "duration_seconds": seconds}
                   for i, seconds in enumerate([1, 6, 12], 1)]},
        {"defaults": {"duration_seconds": 6}, "shots": ["First", "Second"]},
        {"duration_seconds": 12, "shots": ["First", "Second"]},
    ]
    script = """
      import {h3FrameLength, parsePlanJson, calculatePlanTiming, setShotLengthMode} from './web/h3_chain_plan_core.mjs';
      import {readFileSync} from 'node:fs';
      const input = JSON.parse(readFileSync(0, 'utf8'));
      const lengths = input.durations.map(value => { try { return h3FrameLength(value); } catch { return null; } });
      const plans = input.plans.flatMap(plan => ['head', 'before'].map(anchorMode => {
        const result = calculatePlanTiming(parsePlanJson(JSON.stringify(plan)), {anchorMode});
        return {raw: result.shots.map(s => s.rawFrames), delivered: result.shots.map(s => s.deliveredFrames), total: result.totalFrames};
      }));
      const converted = [1, 6, 12].map(duration_seconds => {
        const shot = {duration_seconds}; setShotLengthMode(shot, 'frames'); return shot.length;
      });
      process.stdout.write(JSON.stringify({lengths, plans, converted}));
    """
    response = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                              input=json.dumps({"durations": durations, "plans": plans}),
                              text=True, capture_output=True, check=True)
    browser = json.loads(response.stdout)
    for seconds, actual in zip(durations, browser["lengths"], strict=True):
        try:
            expected = chain._h3_frame_length(seconds)
        except ValueError:
            expected = None
        assert actual == expected, (seconds, actual, expected)
    assert browser["converted"] == [chain._h3_frame_length(s) for s in [1, 6, 12]]
    with tempfile.TemporaryDirectory() as directory:
        fixture["folder_paths"].output_directory = directory
        for offset, (raw, anchor) in enumerate((plan, mode) for plan in plans for mode in ["head", "before"]):
            compiled = chain._normalize_plan(json.dumps(raw), "duration_parity", 64, 64,
                                              22, "video", anchor, "disabled", "generated_audio",
                                              22, 15, 20, 0, 18)
            expected = {"raw": [s["raw_frames"] for s in compiled["shots"]],
                        "delivered": [s["delivered_frames"] for s in compiled["shots"]],
                        "total": sum(s["delivered_frames"] for s in compiled["shots"])}
            assert browser["plans"][offset] == expected, (raw, anchor, browser["plans"][offset], expected)
        assert not list(pathlib.Path(directory).iterdir()), "Timing inspection wrote project files"
    print(f"Python/browser parity: {len(durations)} durations, 6 compiled plans, and exact-frame conversion passed")


if __name__ == "__main__":
    main()
