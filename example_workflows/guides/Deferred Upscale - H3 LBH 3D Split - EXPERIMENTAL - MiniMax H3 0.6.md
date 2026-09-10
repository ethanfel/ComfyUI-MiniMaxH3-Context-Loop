# Deferred Upscale - H3 LBH 3D Split - EXPERIMENTAL - MiniMax H3 0.6

Experimental alternate for **nightly**. The ordinary LBH 3D workflow is unchanged. Offline contracts and wiring are checked; this example has not been GPU-render validated.

Setup controls come first, followed by numbered generation columns.

## Run a bounded comparison

Open this alternate, select the saved source lineage, and retain the distinct profile `h3_lbh_3d_split_experimental`. Nothing is regenerated in the original branch.

The default range is scene 1 only. To test scene 5 without upscaling 1-4, keep start_mode=fresh_range and set start_clip=5 and end_clip=5. After inspection, choose a wider range; end_clip=0 means the selection's last generated scene. These are original scene numbers, not chapter-local offsets. For a chapter starting at scene 8, set both to 8. To continue an interrupted range, select resume and the next unfinished scene; keep that profile and recipe unchanged.

Keep the same source, seed, model and refinement schedule when comparing against the ordinary LBH workflow. The scene's recorded seed feeds RandomNoise; this template contains no project-specific prompt or selection.

Install/update the [LBH node pack](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler) so MMH3SplitUpscale, MMH3TemporalSplitParamsV10 and MMH3SpatialSplitParamsV10 are present. Use current ComfyUI with MiniMax H3 AV masking support. Select an installed compatible 3D model from latent_upscale_models; the template keeps the existing LBH FP16 filename, 2x multiplier, align=32, temporal chunking and force_unload enabled. No new LoRA is required.

---

## What is different

Saved clean video latent -> LBH 3D -> Concat AV Latent with original audio -> MMH3 Split Upscale -> Separate AV Latent -> video decode.

A second Concat AV Latent combines the separated refined video with the **untouched source audio latent**, then feeds Segment Save and Loop End. Separating first avoids core Concat fitting the original audio to the split result's potentially shortened audio clock. Segment Save also retains the source checkpoint's delivered waveform; recovered_audio is deliberately disconnected. Frame counts and source trimming remain checked by Segment Save.

There is no Pass-2 AV Prepare, DisableNoise, BasicGuider or SamplerCustomAdvanced in this alternate. The split node creates a guider for each tile and applies RandomNoise itself; passing the existing pre-noised latent would use the wrong initialization.

The existing source-prompt/reference restore and conditioning geometry sync remain connected. Motion references stay excluded by default; this is not a new prompt-generation path.

## Important continuity limitation

**No locked previous-HQ scene prefix.** The inspected upstream split node creates its own video masks and does not merge an input noise_mask. Its temporal anchors are within the current scene; they are not our cross-scene Drift-Control protection. Carried source frames can be refined again. They are still trimmed on delivery, but boundary appearance can differ from the previous HQ scene. Do not use this alternate as a drop-in replacement for a mask-sensitive inpaint or an exact-prefix workflow.

Do not connect Pass-2 AV Prepare to try to restore that protection: its mask would be discarded and its pre-noising would conflict with split sampling. Use the ordinary LBH workflow when exact previous-HQ prefix protection is required.

The split operation is one node execution: an interruption restarts the current scene, not the last completed tile. Completed scene checkpoints remain available. VAE decoding still materializes the full scene; smaller sampler tiles do not guarantee that every stage fits memory. No model/backend patches or installed packs are modified by this example.

---

## Starting settings, not a validated quality preset

| Control | Value |
|---|---|
| Learned upscale | 2x, grid 32, FP16 |
| Temporal chunk / overlap | 73 / 22 frames |
| Anchor strength / motion frames / identity spacing | 0.999 / 22 / 24 |
| Spatial tile | 512 x 512 pixels |
| Tile overlap / overlap fade / minimum edge | 25% / 50% / 256 pixels |
| Seam denoise / polish / color match | 0.65 / off / on |
| Refinement | res_multistep, simple, 20 steps, denoise 0.24, CFG 1 |

Temporal and spatial values follow [upstream's node documentation](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler#node-reference--mmh3-split-upscale-combo), except seam_denoise=0.65, chosen within its suggested 0.5-0.8 range. These are conservative starting controls, not measured best settings for this chain.

The refinement schedule is inherited from the actual current base-model LBH graph: **20 sampler steps**, not the stale two-step description in its older notes. It does not use a Turbo LoRA. Adapter recipe metadata matches the visible settings in this alternate.

Start with the saved defaults and inspect identity, fast movement, spatial seams and temporal joins. With enough VRAM, test larger tiles or disconnect only Spatial Split Params for temporal-only refinement. Keep Temporal Split Params connected in that case; disconnecting both disables splitting. Treat seam polish as an additional experiment: it samples strips that can span a full image dimension, increasing peak memory. Change one variable at a time.

If you change sampling or split settings, use a new profile and update recipe_json to describe the experiment accurately; that field is a record, not a control for the downstream nodes.

---

## Research and validation record — 2026-09-10

- [Upstream implementation](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler/blob/main/nodes/MMH3_Split_Upscale.py) supplies the schemas and confirms clean-input sampling, replacement video masks, zero audio masks and per-tile guider construction. It returns a new samples dictionary rather than preserving all input metadata.
- [Banodoco: Illynir, August 29](https://discord.com/channels/1076117621407223829/1533923760984555875/1543269788518850613) mentions the spatial/split resampling addition, but supplies no settings or benchmark. The accessible H3-channel search did not establish a tested preset for this exact node. The curated-distillation endpoint was unavailable; raw message search worked.
- Local checks cover schema/widget serialization, links, layout, clean-input wiring, exact source-audio routing and metadata/settings agreement. They do **not** establish output quality, seamless joins, hardware memory requirements, or runtime compatibility with every upstream version.
- The existing MiniMax H3 model license still applies; this workflow adds no model weights. See the [vendor license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE), including its local-use territory restrictions (US/EU/UK/South Korea); the paid API is a separate product.
