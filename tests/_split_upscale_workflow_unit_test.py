#!/usr/bin/env python3
"""Offline contracts for the optional LBH temporal/spatial refinement graph."""
import json
import sys
from pathlib import Path

from _workflow_catalog_unit_test import (
    input_socket, load, nodes, one, origin, validate_clean_metadata,
    validate_layout, validate_links,
)
from _workflow_schema_unit_test import validate_workflow

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_v06_workflows import build
from workflow_schema import load_schemas

NAME = "Deferred Upscale - H3 LBH 3D Split - EXPERIMENTAL - MiniMax H3 0.6.json"


def main():
    recipe = load(ROOT / "tools/v06/recipes" / NAME)
    workflow = load(ROOT / "example_workflows" / NAME)
    schemas = load_schemas()
    validate_workflow(workflow, schemas)
    validate_links(workflow)
    validate_layout(workflow)
    validate_clean_metadata(workflow)
    compiled, guide = build(recipe, NAME, schemas)
    assert compiled == workflow
    assert guide == (ROOT / "example_workflows/guides" / NAME.replace(".json", ".md")).read_text()

    by_key = {n["key"]: n for n in recipe["nodes"]}
    current = one(workflow, "MiniMaxH3ChainUpscaleCurrent")
    learned = one(workflow, "MinimaxH3LatentUpscaler3D")
    split = one(workflow, "MMH3SplitUpscale")
    separate = one(workflow, "LTXVSeparateAVLatent")
    joins = nodes(workflow, "LTXVConcatAVLatent")
    assert len(joins) == 2
    clean = origin(workflow, split, "latent")
    saved = origin(workflow, one(workflow, "MiniMaxH3ChainUpscaleSegmentSave"), "upscaled_latent")
    assert clean != saved and clean in joins and saved in joins
    assert origin(workflow, clean, "video_latent") == learned
    assert origin(workflow, clean, "audio_latent") == current
    assert origin(workflow, separate, "av_latent") == split
    assert origin(workflow, saved, "video_latent") == separate
    assert origin(workflow, saved, "audio_latent") == current
    assert origin(workflow, one(workflow, "VAEDecode"), "samples") == separate
    assert origin(workflow, one(workflow, "MiniMaxH3ChainUpscaleLoopEnd"), "upscaled_latent") == saved
    assert input_socket(one(workflow, "MiniMaxH3ChainUpscaleSegmentSave"), "recovered_audio")["link"] is None
    # Separate BEFORE the second concat: passing nested AV directly would fit
    # the source audio to upstream's potentially shorter result audio length.
    assert by_key["restore_audio"]["inputs"] == {
        "video_latent": ["separate_result", "video_latent"],
        "audio_latent": ["n3", "source_audio_latent"],
    }
    assert by_key["join_clean"]["inputs"]["audio_latent"] == ["n3", "source_audio_latent"]
    for forbidden in ("MiniMaxH3ChainPass2Prepare", "DisableNoise", "BasicGuider",
                      "SamplerCustomAdvanced", "LoraLoaderModelOnly"):
        assert not nodes(workflow, forbidden), forbidden
    assert origin(workflow, split, "noise")["type"] == "RandomNoise"
    assert by_key["n11"]["inputs"]["noise_seed"] == ["n3", "seed"]
    assert by_key["n11"]["settings"]["control_after_generate"] == "fixed"
    assert origin(workflow, split, "conditioning")["type"] == "H3ConditioningSyncFromLatents"
    assert origin(workflow, split, "temporal_split_param")["type"] == "MMH3TemporalSplitParamsV10"
    assert origin(workflow, split, "spatial_split_param")["type"] == "MMH3SpatialSplitParamsV10"
    assert input_socket(split, "negative")["link"] is None

    adapter = by_key["n2"]["settings"]
    assert adapter["profile"] == "h3_lbh_3d_split_experimental"
    assert (adapter["start_mode"], adapter["start_clip"], adapter["end_clip"]) == ("fresh_range", 1, 1)
    assert adapter["save_latent"] is False
    provenance = json.loads(adapter["recipe_json"])
    assert provenance["locked_hq_prefix"] is False
    assert provenance["audio_denoise"] == 0 and provenance["source_audio"] == "reattach_exact"
    assert provenance["scale_multiplier"] == by_key["n13"]["settings"]["mode.scale"] == 2.0
    assert provenance["pass2_steps"] == by_key["n12"]["settings"]["steps"] == 20
    for field in ("denoise", "scheduler"):
        assert provenance[field] == by_key["n12"]["settings"][field]
    assert provenance["sampler"] == by_key["n18"]["settings"]["sampler_name"]
    temporal = dict(by_key["time_params"]["settings"])
    temporal["motion_anchor_frames"] = int(temporal["motion_anchor_frames"])
    assert provenance["temporal"] == temporal
    assert provenance["spatial"] == by_key["space_params"]["settings"]
    for field in ("seam_polish", "color_match"):
        assert provenance[field] == by_key["split"]["settings"][field]
    assert by_key["split"]["settings"]["cfg"] == 1.0
    assert (temporal["chunk_frames"], temporal["temporal_overlap_frames"]) == (73, 22)
    # The initial preset's token hop remains phase-aligned on H3's five-token grid.
    tokens = lambda frames: (frames - 5) // 17 * 5 + 2
    assert (tokens(73) - tokens(22)) % 5 == 0
    note = one(workflow, "Note")["widgets_values"][0]
    assert "no locked previous-HQ scene prefix" in note
    assert "fresh_range" in note
    assert "not been GPU-render validated" in guide
    assert "20 sampler steps" in guide
    assert "Banodoco" in guide and "upstream" in guide
    print("Split upscale: reproducible graph, schemas, layout, clean input, scene seed, "
          "source-audio reattachment, bounded fresh range and honest recipe metadata pass")


if __name__ == "__main__":
    main()
