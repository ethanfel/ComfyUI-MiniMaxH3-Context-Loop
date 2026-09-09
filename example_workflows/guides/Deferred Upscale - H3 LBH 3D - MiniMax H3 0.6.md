# Deferred Upscale - H3 LBH 3D - MiniMax H3 0.6

Adapted for **nightly** from the released **0.6 workflow catalog**. H3 settings and sockets are validated against nightly.

Setup controls come first, followed by numbered generation columns.

STANDALONE H3 CHECKPOINT UPSCALE

1. Open Checkpoint Manager and select the right-hand tip you want to finish. A partial generated branch is valid; no ungenerated scenes are required.
2. The selected_manifest cable is the complete parent-chain contract. No Plan, Source Timeline, source audio, or external context enters the upscale loop. Original reference media is unnecessary unless you deliberately connect a new Tagged Ref line to the override node.
3. Choose a unique profile in Upscale Adapter. start_clip=1/end_clip=0 processes every generated scene in the selection.
4. Keep LBH on scale by multiplier = 2.0 (2x width and height), with grid-32 alignment.
5. Queue. Each HQ scene is saved before the loop advances; Assemble writes under output/h3_chains/<run>/upscaled/<profile>/final/.

save_latent is OFF by default. Enable it only when you need to reopen the complete HQ latent later. Drift-Control compatibility still saves only the small 12-step HQ context tail needed by the following scene and by interrupted-run resume.

Upscale Reference Conditioning restores the scene's original Ref2VA conditioning from its automatic cache. Upscale Reference + Prompt Override stays inline on the normal H3 Tagged Ref line: leave its references input empty for automatic cache restore, or connect a new Tagged registry to replace the cached refs for pass 2. Use disabled_tags to omit selected connected refs. Its prompt override is blank by default, so pass 2 uses the exact compiled source prompt.

OPTIONAL NEUTRAL REPLACEMENT PROMPT — copy this into the override only for a controlled comparison:
Preserve the existing video's identity, appearance, composition, framing, lighting, color, action, motion timing, and audio. Use the supplied picture references only to maintain the identities and appearance already present. Improve only fine spatial detail and high-resolution texture fidelity. Do not introduce new motion, objects, cuts, camera moves, or scene changes.

The default motion policy excludes both the Qwen motion-video presentation and native motion-reference latent because the source latent already contains the generated motion; paired reference audio is retained. H3 Conditioning Sync From Latents follows that policy automatically, measures LBH's real X/Y latent scale, and resizes match picture minimax_refs plus minimax_keyframes while keeping max picture refs at their cached geometry. Its positive output creates a fresh pass-2 Basic Guider. Older non-reference runs fall back to text-only by default; choose error when a Ref2VA cache is mandatory.

---

REQUIRES
https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler
Download minimax_h3_latent_upscaler_3d_fp16.safetensors into ComfyUI/models/latent_upscale_models/.

LBH receives only the saved clean 24-channel VIDEO x0. MiniMax H3 Pass-2 AV Prepare rejoins the untouched 32-channel audio and reads the current upscale state. For Drift-Control AV scene 2+, it replaces the 12-step pass-2 prefix with the previous HQ latent tail, gives that prefix zero added noise and a zero denoise mask, refines only the new video region, and keeps audio locked. Other continuation modes retain the open-video behavior. H3 Conditioning Sync From Latents follows the community pass-2 method: original latent + LBH latent + original conditioning -> spatially synchronized conditioning -> new Guider -> sampler 2. Choose motion policy once on Upscale Reference Conditioning: exclude_video_keep_audio is the upscale default; keep_video_native and resize_video remain available for controlled comparisons. Leave the sync node on conditioning_policy. The default interpolation is bilinear.

Defaults: 2x spatial scale, align 32, FP16, 2 pass-2 steps, denoise 0.24, simple schedule, Euler. Raise denoise only after checking identity and scene boundaries. The Comfy Kitchen attention override remains bypassed because large H3 target sequences can exceed Sage prequantized int32 stride limits; this graph uses ComfyUI's PyTorch attention path. Functional nodes intentionally retain their original display names.
