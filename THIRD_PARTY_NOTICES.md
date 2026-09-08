# Third-party notices

For a user-facing map from features to upstream sources and local
implementation files, see [Feature traceability](docs/FEATURE_TRACEABILITY.md).
This file remains the authoritative attribution, revision, and license record.

## ComfyUI-H3-Prompt-IDE

The nightly prompt-editor completion, marker-interaction, tokenizer diagnostic
and preference improvements are selectively adapted from
[ComfyUI-H3-Prompt-IDE](https://github.com/ethanfel/ComfyUI-H3-Prompt-IDE)
at revision `8d3bccf11291096da4e0150e2e6fc23b014967d1`, GPL-3.0.
That standalone editor originally derives from this repository's rich editor.

The adaptations live in `web/h3_prompt_completion_core.mjs`,
`web/h3_prompt_schema_core.mjs`, `web/h3_prompt_marker_ui.mjs`,
`web/h3_prompt_editor_settings_core.mjs`, and both embedded editor surfaces.
Chain-specific tagged/semantic reference mapping, per-scene undo, Plan
synchronization and ownership protection remain local. The standalone
task-aware Edit templates and native-undo replacement are not incorporated.

## ComfyUI-LegacyWidgetWidthFix

The canvas-wide LiteGraph widget-width compatibility layer is adapted with
permission from
[ComfyUI-LegacyWidgetWidthFix](https://github.com/pekkAi-dev/ComfyUI-LegacyWidgetWidthFix)
by **pekkAi-dev**. This pack embeds the repair behind its existing H3 nodes and
does not claim or register the standalone project's `LegacyWidgetWidthFix`
node id. Shared legacy widget marker names are retained deliberately so both
extensions can coexist without stacking incompatible widget descriptors.

## ComfyUI-H3-Motion-Context

This repository grew from the original H3 Motion Context implementation by
**NikoDemon80**:

https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context

The shared initial implementation and research remain under this repository's
GPL-3.0 license and are preserved in its Git history. Niko's project owns the
original `MiniMaxH3MotionContext*` public node ids. This specialized loop pack
uses separate registrations while vendoring upstream's shared marker and
patch-ownership ABI, allowing both projects to be installed without registering
the same nodes or double-wrapping ComfyUI.

`patch_layout.py` and `patch_payload.py` retain the shared ownership markers
from upstream revision `c140ae99b8c3` (`0.2.0`, 2026-08-09). This loop fork
extends the layout implementation with native-guide capability detection while
preserving that ownership ABI for safe co-installation on legacy ComfyUI.

Upstream revision `658ba11ae917` (`0.3.0`, 2026-08-12) by **NikoDemon80** is
also the source of the retained `last_frame` design, 56-frame context option,
and the in-graph Seam Probe adapted here. This fork gives the probe a distinct
public node id so both packs can remain installed together, and retains its own
native-guide and recursive-chain integration.

## ComfyUI-H3-Motion-Context-MultiRef

The cumulative audio-sample budgeting approach was inspired by
[ComfyUI-H3-Motion-Context-MultiRef](https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef)
by **seitanism**. This repository implements the idea independently for its
checkpointed generated-audio and saved-prelude assembly, using cumulative
delivered video-frame boundaries so per-scene rounding cannot accumulate into
long-run A/V drift.

On the nightly integration, `motion_context_upstream.py` dynamically discovers
that pack's public `MiniMaxH3MotionContext` registration and calls it for
compatible Guide scenes. No upstream source is copied by this adapter. The
visible Chain node and saved workflow remain local; capability checks retain
the internal implementation for Loop-only modes the upstream contract does not
yet expose. This runtime integration was reviewed against upstream revision
`5839d453efa0346c1da49acc39fc65050c5c48c0` (2026-08-30), GPL-3.0.

The retained internal fallback was also brought to that revision's arbitrary
Guide-length and `target_start` placement behavior. `h3_audio_grid.py` adapts
the same revision's exact 40 Hz PCM-grid helper so source-audio continuation
cannot be shifted by a generic VAE wrapper's normal center crop. Local
keyframe arbitration, latent Guide, audio-only and longer-audio continuity,
signed fractional audio placement, and legacy-core support
remain extensions of this loop pack.

The experimental masked AV chain mode directly adapts that GPL-3.0 project's
masked existing-video extension and its capability-aware runtime compatibility
for ComfyUI PR #15375. Its masked-velocity x0 conversion also integrates the
correction proposed in ComfyUI PR #15988. In this loop integration the
preserved source is the previous accepted scene checkpoint: decoded tail
frames are VAE-encoded into the next target video latent, its sampled
audio-latent tail is copied directly, and both target prefixes receive
`0 = preserve`, `1 = generate` masks.

Adapted source files and integration:

- `h3_mask_compat.py` — PR #15375 model/sampler capability detection and
  fallback, with native-first PR #15988 masked-velocity conversion;
- `h3_mask_payload_compat.py` — native-first AV mask payload extraction;
- `masked_context.py` — recursive target-prefix construction derived from the
  MultiRef existing-video implementation, including Update 6's audio-only
  eight-tick half-cosine handoff as a distinct continuation mode.
- `master_audio_context.py` — exact master-audio target replacement and
  optional protected video-prefix construction adapted from MultiRef Update 4,
  plus Update 6's direct sampled-latent video continuation path.
- `av_timing.py` and `nodes.py` — Update 6's exact absolute PCM-boundary and
  small decoded-audio time-conformance approach, adapted to the recursive
  checkpointed trim path.
- `masked_bridge.py` — two-ended protected AV target construction adapted
  from MultiRef Update 5's masked bridge.

The referenced MultiRef revision is `4b484a3` (2026-08-14), GPL-3.0.
Its v2 mask-engine marker is recognized for safe migration. This pack uses a
v3 model, sampler, and payload ABI for PR #15375 commit `989e7a9`, replacing
only recognized older blend/payload wrappers whose semantics changed.

The master-audio node adapts MultiRef Update 4 at merged revision `9118251`
(2026-08-14) and its Update 5 target-audio-grid boundary correction at merged
revision `31e6cb7` (2026-08-15). Absolute master-audio slice endpoints and
small decoded-audio time-conformance follow Update 6 revision
`56f7586597929c43a6373ef28f6f84f26411b223` (2026-08-17).

The two-ended masked bridge adapts MultiRef Update 5 at merged revision
`31e6cb7` (2026-08-15).

## ComfyUI MiniMax H3 per-token AV masks

The underlying per-token video/audio latent masking design and upstream
implementation are from
[ComfyUI PR #15375](https://github.com/Comfy-Org/ComfyUI/pull/15375), authored
by **drozbay**. The vendored fallback is scoped to masked target-latent
operations, prefers native equivalent behavior automatically, and is not
activated by ordinary guide-mode chains. It tracks the post-review mask-blend
design through upstream commit `989e7a9bb79a370d20f63674b54dead993f6f4a1`
(2026-08-15).

## MaskVidExperiments causal mask conversion

The exact H3 pixel-mask conversion in `masking_ops.py` adapts the causal
frame-group derivation, conservative max reduction, and 2×2 latent-token
snapping from
[MaskVidExperiments](https://github.com/drozbay/MaskVidExperiments) by
**drozbay**. This integration specializes that general VAE-aware algorithm to
MiniMax H3's fixed `1,4,4,4,4` frame cycle and existing joint-AV masked target;
it does not copy the upstream crop, uncrop, or workflow nodes.

The referenced revision is
`d98cc899c1fac718acf81cde1735bf57281097cf` (2026-08-17), GPL-3.0.

## ComfyUI-MiniMaxH3-PerRowMasking

The public general-masking workflow and its source-media/mask-grid utilities
adapt the earlier experimental
[ComfyUI-MiniMaxH3-PerRowMasking](https://github.com/ethanfel/ComfyUI-MiniMaxH3-PerRowMasking)
repository by **ethanfel**. That GPL-3.0 project established the source AV
`17k+5` trim, exact 32×32 effective-cell preview, mask-convention controls,
source-latent workflow, and audio-preservation UI used as the starting point.

This integration uses revision `d6a7964b9fd64f65f1773f84cba3b29665128e59`
(2026-08-07). It does not copy that project's temporary per-model forward
wrapper. Instead, Apply Target Mask reuses this pack's newer capability-aware
PR #15375 engine, composes existing nested masks, and exposes additional
temporal/custom audio-mask modes under distinct node IDs for safe co-install.

## ComfyUI MiniMax H3 Add Guide

Native-guide compatibility targets
[ComfyUI PR #15439](https://github.com/Comfy-Org/ComfyUI/pull/15439), authored
by **drozbay**. That contribution introduces arbitrary-position image, video,
and audio guides plus native keyframe/reference payload merging in ComfyUI.
This repository does not copy its node implementation; it detects the exposed
core layout API and emits compatible guide records from Loop Context.

## MiniMax H3 Chained Character Swap

The opt-in `tapered_guide` continuation adapts the tapered chroma-noise context
recipe, palette, validated default strengths, and deterministic-noise design
from [minimax-h3-chained-character-swap](https://github.com/MacroSony/minimax-h3-chained-character-swap)
by **MacroSony**. This integration applies the transformation in memory to an
immutable clone of the exact recursive context tail, derives its noise seed
from the scene seed, and generalizes the published 22-frame baseline to other
explicit Guide lengths as an experimental option.

The referenced revision is `609e74233445067afa06bd5a3428bc645d555a01`
(2026-08-16), distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 MacroSony

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ComfyUI-H3-Context-Noise

The experimental `tapered_av` / Detail AV transition adapts the latent
Gaussian context-noise recipe, matched-latent-standard-deviation scaling,
validated 0.45 to 0.10 two-step taper, and deterministic CPU-noise design from
[ComfyUI-H3-Context-Noise](https://github.com/beijinren/ComfyUI-H3-Context-Noise)
by **beijinren**. This integration applies the treatment only to an immutable,
disposable copy of the recursive 39-frame video-latent prefix, leaves the
audio latent and denoise masks unchanged, and fingerprints the recipe for safe
checkpoint resume.

The referenced revision is `7e5531233b42dadd19c40d86770521a36508c358`
(2026-08-18), distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 beijinren

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## ComfyUI-MiniMaxH3-Easy

The H3 Chain Plan editors' quick `@` reference-tag and `#` dialogue-tag
interactions, rich reference chips, media miniatures, compact optimizer
presentation, and direct-provider transport pattern were inspired by
[ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy)
by **nkxx188**. The graph-aware scheduled-reference mapping, scene-card editor,
plan serializer, prompt revision storage, MCP agent bridge integration,
H3-scoped instruction/context contract, timing calculations, and chain
integration in this repository are an original implementation.

ComfyUI-MiniMaxH3-Easy is distributed under the MIT License:

```text
MIT License

Copyright (c) 2026 nkxx188

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
