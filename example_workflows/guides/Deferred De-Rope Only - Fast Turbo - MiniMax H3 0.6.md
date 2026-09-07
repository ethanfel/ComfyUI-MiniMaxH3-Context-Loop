# Deferred De-Rope Only - Fast Turbo - MiniMax H3 0.6

Adapted for **nightly** from the released **0.6 workflow catalog**. H3 settings and sockets are validated against nightly.

Setup controls come first, followed by numbered generation columns.

DEFERRED DE-ROPE ONLY — FAST TURBO / SOURCE RESOLUTION

Repair existing checkpoint scenes without generating the source again or spatially upscaling them. Each scene keeps its source canvas; no LBH, pixel upscaler or learned spatial-upscale model is needed.

Select the Original branch in Checkpoint Manager. Use a new h3_derope_native_turbo profile and the Adapter start/end range. Full recovered latents are saved before the loop advances and appear in the DeRoPE tab. For a later upscale select that DeRoPE branch locally, then use a DIFFERENT output profile; scenes without a DeRoPE take fall back to their original source.

The source clean latent drives Jerk Oracle. Optional manual ranges gate it; Chain De-Rope Guard then protects the disposable prefix and continuation-side closing frames. De-Rope Budget passes that protected map to Time Smear. VAE Encode returns the held pixels to latent space without changing canvas size. De-Rope Continuity restores the prior processed prefix where needed, and Freeze Mask stays connected with time_varying ON.

The audio seed follows the SAME final hold_map_used at strength 0.5. Exact Recover and Audio Recover return to the original RAW clock; safe recovery retains the original performance. Re-encoded recovered video/audio feed Chain Recovered AV, Segment Save and Loop End. Keep save_latent ON.

Defaults favor tighter repair spans: custom q=0.85, d_max=4, ramp ON, bridge=8. For clearly fast action try q=0.75; for pose drift or separate peaks merged too broadly, compare bridge=0. The oracle ranks motion; it does not prove that a calm scene needs repair. Process only scenes you intend to refine.

---

REQUIREMENTS / SAMPLING / MEMORY

Requires ComfyUI-MAINodes v1.1.3 or newer: https://github.com/matlowai/ComfyUI-MAINodes . Ref2VA INT8 is the supplied model default; choose the matching H3 model for your source workflow. No new diffusion pass is performed for the original source.

FAST/TURBO recipe: gradient_estimation + beta, total_steps=6, inject=0.50, preset=custom: 3 actual sampling steps. The LightX2V 4-step v1.0 768p ComfyUI bf16 LoRA is enabled at strength 1.0. Select this specific compatible file in the loader; Turbo v4 is not an interchangeable default. The supplied recipe follows upstream motion_pipeline_ref2va_audioinit.json. Prefer a base-generated source; a turbo-generated source can develop mosaic artifacts with a deeper turbo repair. If that happens, use the separate base-model De-Rope workflow rather than stacking more turbo passes. Numeric inject/q controls take effect only when their preset is custom. Recipe metadata describes defaults, not executable controls.

Manual ranges are comma-separated zero-based RAW scene frames or 24 fps seconds (36-60, 3s-4s), including the carried prefix. Blank ranges keep the automatic oracle. The same ranges apply to every scene in the Adapter range: select one scene for a specific repair. Token snapping/ramp shoulders can extend the selected spans; the chain guard runs afterward. This gates temporal expansion only: unheld frames still participate in the refinement pass, so it is NOT a pixel-exact masked splice.

Budget Preview reports planned expansion and actual SIGMAS intervals before Time Smear; Final Frame Preview shows the actual endpoint/grid-padded length. Time Smear's step estimate is connected to the actual schedule. Reports do not pause sampling. A source-resolution float32 RGB estimate is only one buffer, not total RAM/VRAM. Full expanded image batches still live in CPU RAM; long scenes can still be expensive. Use tighter ranges/q before reducing dilation; d_max=3 is a speed/quality trade-off, not the same repair strength.

Keep original audio recovery and save_latent ON. MP4 previews are encoded; the full saved latent supports later deferred upscale but is not a pixel-lossless master. No experimental adapter, DyRoPE, global background-freeze or streamed-block patch is active.

Source guidance (checked 2026-09-07): https://github.com/matlowai/ComfyUI-MAINodes/blob/main/TUNING.md . Separate gentle pixel refinement after De-Rope has encouraging community results, not a guarantee; compare playback before processing a whole branch.

Upstream fast recipe: https://github.com/matlowai/ComfyUI-MAINodes/blob/main/examples/motion_pipeline_ref2va_audioinit.json . This variant has offline graph tests, not a local GPU quality certification.
