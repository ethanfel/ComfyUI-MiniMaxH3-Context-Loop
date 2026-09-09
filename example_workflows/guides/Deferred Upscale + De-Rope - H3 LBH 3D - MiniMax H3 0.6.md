# Deferred Upscale + De-Rope - H3 LBH 3D - MiniMax H3 0.6

Adapted for **nightly** from the released **0.6 workflow catalog**. H3 settings and sockets are validated against nightly.

Setup controls come first, followed by numbered generation columns.

CHAIN-AWARE H3 UPSCALE + DE-ROPE

1. Select the generated branch tip in Checkpoint Manager. Partial generated branches are valid.
2. Keep a unique profile name. Each recovered HQ scene is saved before the loop advances.
3. H3 Jerk Oracle finds overloaded motion on the saved clean source latent. Chain De-Rope Guard prevents it from retiming the disposable continuation prefix and the last 17 frames when a later scene consumes this scene as visual context. Its expand_to_end output must stay connected to H3 Time Smear.
4. Time Smear expands decoded source frames; VAE Encode returns them to clean H3 latent space; LBH 3D performs the spatial upscale. Chain De-Rope Continuity then splices the previous HQ Drift-Control tail at target resolution.
5. Chain De-Rope Freeze Mask must stay connected to H3 V2V Init with time_varying ON. The stretched source audio is seeded at strength 0.5 so dialogue timing does not drag the mouth back to natural speed.
6. Exact Recover and Audio Recover return to the original RAW clock. The bundled safe audio default keeps the original performance; change Audio Recover only for an intentional pass-2 foley test. Recovered AV is re-encoded for Drift-Control continuity and full latent saving.

No Plan, Source Timeline, source media, or manual references are required. Automatic reference-cache restore remains active and excludes motion-video refs by default because the source latent already contains the motion. Functional nodes intentionally use their original registered names.

---

REQUIRES
https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler
https://github.com/matlowai/ComfyUI-MAINodes

This is the combined spatial-upscale + De-Rope option. For motion repair at source resolution, use Deferred De-Rope Only instead. A later gentle pixel upscale may preserve repaired motion, but high-denoise resampling can reintroduce artifacts; compare playback on your footage.

Defaults: balanced oracle q=0.75 / d_max=4 / ramp ON / bridge=8; 2x spatial scale with LBH 3D; res_multistep / simple, 20 total schedule steps and custom injection 0.50 = 10 actual sampling steps. Non-custom presets override numeric widgets. Recipe metadata describes these defaults; the visible graph controls execution.

Manual Hold Map gates the oracle before Chain De-Rope Guard. Blank ranges preserve automatic planning. Non-empty ranges use zero-based RAW scene frames or seconds at 24 fps, including the carried prefix (e.g. 36-60, 3s-4s). They apply to every selected scene: narrow Adapter start/end to one scene for a targeted edit. Token snapping and ramp shoulders can extend beyond a typed endpoint. Guard still protects chain boundaries.

De-Rope Budget reports the protected map before pixel expansion and derives actual steps from SIGMAS. Its source-resolution pixel-buffer estimate is not a total-RAM or VRAM estimate. Preview Any displays the budget and Time Smear's final padded-frame report. Changing the schedule also updates Time Smear est_steps. Reports do not pause a queued render. The full held IMAGE batch remains in CPU RAM; neither these reports nor scene-by-scene execution make a long scene file-backed or windowed.

Audio init follows the original performance at 0.5; Audio Recover delivers the original performance. Recovered video/audio are encoded on the original RAW clock, and save_latent is ON for later deferred processing. Use a new profile when changing recipe/settings; do not mix revised settings into an existing profile.

Motion-video refs stay excluded by default. Reference cache recovery uses saved media when necessary. The optional attention override remains bypassed; no experimental motion adapter, DyRoPE patch or streamed-block patch is enabled.
