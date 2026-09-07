# Building nightly's 0.6 workflow baseline

The recipes and layouts come from the released 0.6 catalog at `1663c42`.
This branch compiles them against **nightly**, not against a cached 0.6 H3
schema. Filenames keep the shared `0.6` baseline suffix; in-canvas notes and
guides identify nightly.

Nightly additions are explicit recipe settings: Context's
`visual_cond_noise_aug = 0.999`, `future_end_anchor = false`, and Carousel's
empty `ownership_json`. Experimental guidance stays off, and templates never
include a session's ownership proof. Other nightly-only nodes remain optional.

`recipes/` is the source of truth. Each recipe names its node settings and
connections; it contains no UI widget arrays, node dimensions, link-slot numbers,
or frontend metadata. Existing example prompts and intended connections were
reviewed as reference material. The old archive-based builder is no longer used.
The files under `example_workflows/Archive/` are never read or modified by this
builder.

From this checkout, run:

```sh
python3 tools/build_v06_workflows.py
python3 tools/build_v06_workflows.py --check
python3 tests/_workflow_schema_unit_test.py
python3 tests/_workflow_catalog_unit_test.py
python3 tests/_derope_workflow_unit_test.py
```

The builder imports this branch's H3 `INPUT_TYPES` / output definitions in an
offline schema environment. Run it in its own Python process, not inside a
running ComfyUI server. The normal package dependencies, including torch, must
be available; no model weights or GPU inference are used.

Use repeatable `--workflow '<exact recipe filename>.json'` to build or check
only the recipes being edited. This avoids rewriting unrelated generated
workflows. The De-Rope-only base/fast recipes and combined LBH recipe are checked
for source-canvas preservation, range/guard order, actual sigma-step reporting,
the complete turbo model path, and recovered AV checkpoint wiring. MAINodes'
manual gate schema follows v1.1.3; blank ranges retain the connected oracle.

`external_schemas.json` contains only dependency contracts, with provenance and
installation-specific file inventories removed. It is not a snapshot of nightly
H3 nodes. Update these external contracts deliberately when changing the
documented dependency requirements; do not import an entire server inventory.

## Serialization rules

- Name every widget explicitly. A new/missing field fails the build until its
  recipe has been reviewed.
- Connected widgets still occupy their normal position in `widgets_values`.
  They also need the input's `widget.name` metadata.
- Seed controls include `control_after_generate` in frontend order.
- V3 dynamic combos include their selected child settings; autogrow reference
  sockets are generated from named connections.
- VHS uses named widget serialization. Preview state and uploaded-file browser
  metadata are not embedded.
- Studio's connected Plan values initialize its mirror consistently.

Tests cover widget types, enums and ranges, required inputs, converted-widget
metadata, bidirectional links, output slots, Studio plan wiring, title-inclusive
node bounds, group containment, preserved assets, and deterministic generation.
The reported two-slot Tagged Ref2VA shift is an explicit negative regression test.

## Layout and validation history

Project controls precede numbered dependency columns. Titles are short enough
not to force automatic width expansion. Stacks reserve body height, the 30-pixel
title bar and a 70-pixel gap. DOM editors and image/video previews get explicit
space. Recovery is isolated and disabled; detailed Notes become companion guides.

The source catalog's September 5 audit opened all 18 files in an isolated ComfyUI 1.51.9 frontend
with the 0.6 branch's schemas and JavaScript (plus VHS), and checked serialized
API prompts. All project writes and queue requests were blocked. This checks
loading, settings and wiring, not numerical model output or GPU memory use.
Before release, also test representative real renders with installed models and
assets, particularly the optional external upscale packs.

The 0.6 catalog's September 5–6 local execution pass completed all 15 non-upscale workflows
with bounded smoke settings (two steps, reduced canvas and loop-scene lengths).
The seven corrected workflows were rerun and their final audio/video streams and
expected checkpoints verified. The unavailable Ref2VA model was substituted with
FL2VA for these execution tests, and a compatible installed H3 text encoder was
used. This does not replace full-resolution or genuine Ref2VA quality testing.
Those results describe the source release, not a GPU execution test of the
nightly adaptations. Nightly validation checks local schemas, topology, clean
serialization, assets, and layout bounds offline.

Maintained media examples now use core Load Video/native VIDEO paths. In the
tested frontend, VHS could reinterpret named widget values as its old positional
layout, silently changing frame caps and frame selection; its lazy audio mapping
also failed strict AUDIO-dictionary consumers. Check that serialized API values
equal the recipe values, not merely that they satisfy a type/range schema.
The native bridge preserves the 114-frame gap and uses a 311-frame H3-safe source
span, dropping only the former demo's final two frames.
