# Deferred Upscale - DLSS5 + LMS Guide - EXPERIMENTAL - MiniMax H3 0.6

New **nightly-only experiment**, based on Alissonerdx's LMS guide workflow. CPU contract/wiring checks are not a GPU quality or memory validation.

Setup controls come first, followed by numbered generation columns.

EXPERIMENTAL — DLSS5 → LMS GUIDE REFINEMENT

Select a saved checkpoint lineage, not a source movie. Pixel Current Scene decodes the clean saved video latent including the RAW overlap prefix. DLSS5 enlarges it; LMS Upscale Guide encodes those enlarged frames as a native H3 guide at frame zero and allocates an empty target at exactly the same dimensions and frame count. The source scene is not re-noised. No original Ref2VA cache or Qwen image presentation is reconstructed. Only the enhancement caption goes to Qwen. This replaces the CAT/USDU refinement stage, rather than adding another pass after it.

The example starts at one scene with DLSS5 1.5x. Actual width AND height must be multiples of 32. A fractional scale can violate this for some sources: adjust the upscaler or explicitly align its output before the guide. No implicit resize, frame truncation or FPS conversion is performed here. Our node VAE-encodes the unchanged RGB tensor and builds the same minimax_keyframes payload as native AddGuide, with no PackedLayout patch. It deliberately skips AddGuide's unconditional PIL/Lanczos resize, which would round already-matching float pixels through RGB8. The incoming VIDEO decoder and VAE still determine the available pixel/latent precision. Any IMAGE upscaler that preserves the full RAW frame sequence can replace DLSS5.

The new node also accepts VIDEO instead of images, for a file-backed video upscaler. Supply the same scene's RAW 24 fps result, not a trimmed saved MP4 or an assembled chapter. VIDEO is decoded into a full-scene batch: this input does not make LMS sampling streaming or tiled. Connect exactly one of images/video. No AIToolkitMiniMaxH3RefVideo, VHS loader, or video resampling is necessary for our latent-backed scene clock.

RECIPE

Adapted from Alissonerdx's published LMS workflow at revision 37bd61a3880640e0b5601954a2721d214c5f13b7:
https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/blob/37bd61a3880640e0b5601954a2721d214c5f13b7/workflows/minimax_h3_lms_workflow.json
Model card / guide mechanism: https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI

Ref2VA base → Sigma Shift 12 video / 3 audio → Ref2V Turbo 4step v0.1 at 1.0 → LMS v1.0 r64 at 1.0. BasicGuider is CFG 1.0. Euler, simple, 8 steps, denoise 1.0, fresh RandomNoise seeded from the saved scene. These are the author's sampling settings, not the existing low-denoise CAT recipe. The author's workflow used an older local sharpness LoRA filename; this example points to the published minimax_h3_lms_v1.0_r64.safetensors. The published LoRA must be installed separately. This adaptation uses the catalog's H3 Qwen INT8 encoder instead of the author's NVFP4/AWQ file.

SAVE / AUDIO / PNG

Separate AV Latent takes denoised_output. Only video_latent is decoded and connected to Segment Save and Loop End. The sampled audio is deliberately discarded. Segment Save automatically recovers the exact source checkpoint's delivered audio and trims the repeated video prefix once. Leave recovered_audio and Assemble source_audio disconnected: they have different clock contracts. save_latent=true retains the complete LMS video latent as a full-resolution checkpoint, with source audio saved separately; disable it only if you accept losing that full-latent checkpoint. Original checkpoints remain untouched. Completed scene saves use the existing transactional save/resume and checkpoint-manager Pixel Upscale tab.

For PNG output from a VIDEO workflow, use your existing file-backed H3 decode → Export PNG Sequence + Audio passthrough → VIDEO Segment Save / Loop End path after sampling. Export the refined output, not the input DLSS5 guide. Keep the current pixel state connected for exact RAW trimming and PNG ownership. This IMAGE example saves video plus a full video latent; it does not turn CreateVideo's in-memory VIDEO into a file-backed PNG input. Do not connect this upscale manifest directly to the older manifest PNG exporter: that route expects original-generation checkpoint keys. For PNGs from this experiment, use the per-scene file-backed VIDEO passthrough route described above.

RESUME / SAFETY

Choose a new profile (default h3_lms_experimental). Start with one short scene, inspect detail, motion and transitions, then extend end_clip (0 means all selected scenes). To resume after cancellation, keep the same source/profile/recipe and set start_clip to the first unfinished scene. Completed saves survive; sampling interrupted inside a scene restarts that scene. The profile recipe_json is provenance, not automatic tracking of every connected model/widget edit: update it or use a new profile when changing the recipe, seed, prompt, LoRAs, or upscaler settings. Do not mix CAT results and LMS results under the same profile.

MEMORY AND TEST LIMITS

This is FULL-SCENE H3 guide sampling, not spatially tiled CAT. It holds both guide and target video tokens. A 362-frame 2016×1152 RGB float32 batch alone is about 9.4 GiB, before VAE work, model weights, latents and attention. A VIDEO input still decodes one entire scene. Processing scenes separately avoids allocating the whole chapter, but does not guarantee a scene fits RAM/VRAM. Lower the upscale canvas or choose a shorter source scene if needed; do not silently drop frames. No GPU quality, motion fidelity, or peak-memory result is claimed.

DEPENDENCIES

- Current ComfyUI with native EmptyMiniMaxH3LatentAV, MiniMaxH3AddGuide, MiniMaxH3SigmaShift and Separate AV Latent. No custom layout monkey-patch is added.
- H3 Ref2VA model, matching video VAE and Qwen encoder: https://huggingface.co/Comfy-Org/MiniMax-H3
- Ref2V Turbo 4step v0.1 LoRA (select its installed filename, not the FL2V Turbo LoRA): https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
- LMS LoRA: https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/tree/main/loras
- DLSS5 pack and its platform-specific NVIDIA runtime: https://github.com/Blueforcer/ComfyUI-DLSS5-Enhancer

No weights, runtime installer, live render, or optional external pack is downloaded by this workflow. Check the base model's license separately from the LMS repository's license.
