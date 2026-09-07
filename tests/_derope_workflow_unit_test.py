#!/usr/bin/env python3
"""Regression contracts for native-resolution and combined De-Rope graphs."""
import json
from pathlib import Path

from _workflow_catalog_unit_test import load, nodes, one, origin, input_socket, link

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "Deferred De-Rope Only - MiniMax H3 0.6.json",
    "Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6.json",
    "Deferred Upscale + De-Rope - H3 LBH 3D - MiniMax H3 0.6.json",
)


def main():
    for name in NAMES:
        workflow = load(ROOT / "example_workflows" / name)
        recipe = load(ROOT / "tools/v06/recipes" / name)
        records = {n["type"]: n for n in recipe["nodes"]}
        fast = "Fast Turbo" in name
        native = "Only" in name
        current = one(workflow, "MiniMaxH3ChainUpscaleCurrent")
        oracle = one(workflow, "H3JerkOracle")
        ranges = one(workflow, "H3ManualHoldMap")
        guard = one(workflow, "MiniMaxH3ChainDeropeGuard")
        budget = one(workflow, "MiniMaxH3ChainDeropeBudget")
        smear = one(workflow, "H3TimeSmear")
        freeze = one(workflow, "MiniMaxH3ChainDeropeFreezeMask")
        continuity = one(workflow, "MiniMaxH3ChainDeropeContinuity")
        init = one(workflow, "H3V2VInit")
        schedule = one(workflow, "H3InjectSchedule")
        sample = one(workflow, "SamplerCustomAdvanced")
        recover = one(workflow, "H3ExactRecover")
        audio_recover = one(workflow, "H3AudioRecover")
        packed = one(workflow, "MiniMaxH3ChainRecoveredAV")
        assert origin(workflow, oracle, "samples") == current
        assert origin(workflow, oracle, "length") == current
        assert origin(workflow, ranges, "oracle_hold_map") == oracle
        assert origin(workflow, ranges, "length") == current
        assert records["H3ManualHoldMap"]["settings"]["ranges"] == ""
        assert origin(workflow, guard, "hold_map") == ranges
        assert origin(workflow, budget, "hold_map") == guard
        assert origin(workflow, budget, "sigmas") == schedule
        assert origin(workflow, smear, "hold_map") == budget
        assert origin(workflow, smear, "expand_to_end") == guard
        assert origin(workflow, smear, "est_steps") == budget
        assert origin(workflow, freeze, "hold_map_used") == smear
        for node, field in ((recover, "hold_map"), (audio_recover, "hold_map"),
                            (one(workflow, "H3AudioSmear"), "hold_map")):
            assert origin(workflow, node, field) == smear
            wire = link(workflow, input_socket(node, field)["link"])
            assert smear["outputs"][wire[2]]["name"] == "hold_map_used"
        assert origin(workflow, init, "mask") == freeze
        assert origin(workflow, init, "samples") == continuity
        assert records["H3V2VInit"]["settings"]["time_varying"] is True
        assert records["H3V2VInit"]["settings"]["audio_strength"] == 0.5
        assert origin(workflow, origin(workflow, init, "audio_latent"), "audio")["type"] == "H3AudioSmear"
        assert records["H3AudioRecover"]["settings"]["audio_source"] == "keep the original performance (safe default)"
        assert origin(workflow, origin(workflow, packed, "video_latent"), "pixels") == recover
        assert origin(workflow, origin(workflow, packed, "audio_latent"), "audio") == audio_recover
        for kind in ("MiniMaxH3ChainUpscaleSegmentSave", "MiniMaxH3ChainUpscaleLoopEnd"):
            assert origin(workflow, one(workflow, kind), "upscaled_latent") == packed
            assert origin(workflow, one(workflow, kind), "images") == recover
        adapter = records["MiniMaxH3ChainUpscaleAdapter"]["settings"]
        assert adapter["save_latent"] is True
        meta = json.loads(adapter["recipe_json"])
        settings = records["H3InjectSchedule"]["settings"]
        assert settings["preset"] == "custom"
        steps = round(settings["total_steps"] * settings["inject"])
        assert steps == meta["pass2_steps"] == (3 if fast else 10)
        assert records["H3JerkOracle"]["settings"]["est_steps"] == steps
        assert records["H3TimeSmear"]["settings"]["est_steps"] == steps
        previews = nodes(workflow, "PreviewAny")
        assert {origin(workflow, p, "source")["type"] for p in previews} == {
            "MiniMaxH3ChainDeropeBudget", "H3TimeSmear"}
        if native:
            assert not nodes(workflow, "MinimaxH3LatentUpscaler3D")
            encode = origin(workflow, continuity, "video_latent")
            assert encode["type"] == "VAEEncode"
            assert origin(workflow, encode, "pixels") == smear
            assert meta["resolution"] == "source" and meta["upscaler"] == "none"
            assert records["H3JerkOracle"]["settings"]["preset"] == "custom"
            assert records["H3JerkOracle"]["settings"]["q"] == 0.85
        else:
            assert origin(workflow, continuity, "video_latent")["type"] == "MinimaxH3LatentUpscaler3D"
        # The same patched model feeds guidance and schedule. Turbo must never
        # be added only to one side or accidentally left bypassed.
        model = origin(workflow, schedule, "model")
        assert origin(workflow, one(workflow, "BasicGuider"), "model") == model
        assert origin(workflow, sample, "sigmas") == schedule
        loras = nodes(workflow, "LoraLoaderModelOnly")
        assert len(loras) == int(fast)
        if fast:
            attention = origin(workflow, model, "model")
            assert origin(workflow, attention, "model") == loras[0]
            assert loras[0]["mode"] == 0
            assert loras[0]["widgets_values"] == [
                "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", 1]
            assert settings["scheduler"] == "beta" and settings["total_steps"] == 6
            assert one(workflow, "KSamplerSelect")["widgets_values"] == ["gradient_estimation"]
    print("De-Rope graphs: native canvas, complete turbo recipe, guarded ranges, actual-step reporting and recovered AV saving pass")


if __name__ == "__main__":
    main()
