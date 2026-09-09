#!/usr/bin/env python3
"""Keep the learned latent-upscale examples on a fixed 2x spatial scale."""
import json
import sys
from pathlib import Path

from _workflow_catalog_unit_test import load, one

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_v06_workflows import make_node

NAMES = (
    "Deferred Upscale - H3 LBH 3D - MiniMax H3 0.6.json",
    "Deferred Upscale + De-Rope - H3 LBH 3D - MiniMax H3 0.6.json",
)


def main():
    schema = load(ROOT / "tools/v06/external_schemas.json")["nodes"][
        "MinimaxH3LatentUpscaler3D"]
    for name in NAMES:
        recipe = load(ROOT / "tools/v06/recipes" / name)
        workflow = load(ROOT / "example_workflows" / name)
        settings = one(recipe, "MinimaxH3LatentUpscaler3D")["settings"]
        assert settings["mode"] == "scale by multiplier", name
        assert settings["mode.scale"] == 2.0, name
        assert "mode.megapixels" not in settings, name
        assert settings["align"] == 32, name
        widgets = one(workflow, "MinimaxH3LatentUpscaler3D")["widgets_values"]
        assert widgets[1:4] == ["scale by multiplier", 2.0, 32], name
        compiled = make_node(one(recipe, "MinimaxH3LatentUpscaler3D"), schema, 1)
        assert compiled["widgets_values"] == widgets, name
        metadata = json.loads(one(recipe, "MiniMaxH3ChainUpscaleAdapter")
                              ["settings"]["recipe_json"])
        assert metadata["scale_multiplier"] == 2.0, name
        assert "target_megapixels" not in metadata, name
        adapter = one(workflow, "MiniMaxH3ChainUpscaleAdapter")
        assert any(isinstance(value, str) and value.startswith("{")
                   and json.loads(value) == metadata
                   for value in adapter["widgets_values"]), name
    print("Latent-upscale examples: fixed 2x scaling and matching recipe metadata pass")


if __name__ == "__main__":
    main()
