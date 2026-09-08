# Compatibility

## Runtime scope

Installing the pack does not globally alter ordinary ComfyUI workflows. Its H3
conditioning patches activate when a Context Loop Context node executes and
self-check the live model/layout assumptions before use.

The two continuation engines and public masking path are capability-gated:

- `guide`, `latent_guide`, and `tapered_guide` prefer native Add Guide /
  MultiRef behavior from ComfyUI PR #15439 and use the existing guarded guide
  fallback only on older builds. Latent Guide directly slices a compatible
  generated predecessor's saved video latent and otherwise falls back to the
  RGB/VAE route. Tapered Guide alters only its disposable RGB context before
  VAE encoding;
- `masked_av`, `tapered_av`, `feathered_av`, and `drift_control_av` prefer native per-token H3 AV masks from PR
  #15375 and lazily install only missing mask-engine, payload, token-aligned
  inpaint-scale, and legacy sampler-bridge behavior when the masked path
  executes. It follows
  PR #15375 commit `989e7a9`: pixel masks stay intact for final sampler
  blending while internal H3 timesteps use pooled token-grid values. It still
  requires the native #15439 Add Guide / MultiRef core baseline; masked mode
  is not enabled on the older guide-fallback architecture.
- `drift_control_av` additionally needs current ModelPatcher dynamic-mask and
  apply-model-wrapper APIs. Chain Context installs both hooks on a cloned MODEL
  and refuses an existing dynamic denoise-mask function rather than composing
  two incompatible owners. A sigma-split model switch uses the dedicated
  one-model Drift-Control Model Patch for each raw branch and shares the
  original unsplit sigma schedule; the patch does not copy model weights.
- `MiniMaxH3ContexMaskedTarget` uses the same native-first per-token AV mask
  layer for arbitrary manual targets. It activates compatibility only when the
  node executes and does not require a separate MODEL patch node.

Importing the node pack or running a guide-only workflow does not activate the
masked runtime compatibility. A partially updated native mask engine is
rejected rather than mixed with the vendored snapshot.

The general masking IDs are deliberately different from
`ComfyUI-MiniMaxH3-PerRowMasking`, so both folders may be installed during
migration without one pack replacing the other's registered nodes.

After updating ComfyUI or H3 optimization packs, restart the process fully so
patch ownership is rebuilt cleanly.

## H3 Motion Context copies

This pack no longer registers the historical public Motion Context ids, so
NikoDemon80's original pack or seitanism's maintained MultiRef pack may coexist
without a node-id collision. When seitanism's registered
`MiniMaxH3MotionContext` is present on native-guide ComfyUI, Chain Context calls
it for compatible Guide scenes while retaining the same Chain node UI and
workflow contract.

Loop-only capabilities currently remain internal: direct saved-video-latent
Guide reuse, audio-only continuation, a timeline-audio window longer than the
visual Guide, future end anchors, and the guarded pre-native-core fallback.
The adapter also resolves carried-head keyframe collisions and restores exact
latent-audio placement after the upstream call.

The retained engine follows the current provider contract for arbitrary exact
Guide lengths, interior `target_start` placement, native-run batching, and
end-aligned 40 Hz source-audio encoding. This keeps a capability-gated fallback
scene behaviorally consistent instead of silently reverting to the older
snap-to-grid implementation.

Compatible legacy patch copies share ownership markers; the second copy
normally stands down.

If an older compatible copy owns the process first, wire **MiniMax H3 Patch
Priority** before Context Loop Context. It can replace only a recognized sibling
implementation. Unknown wrappers fail with an ownership explanation rather
than being overwritten.

## Native MiniMax H3 tokenizer tokens

[ComfyUI PR #15808](https://github.com/Comfy-Org/ComfyUI/pull/15808)
registers the seven additional tokens declared by MiniMax H3's released
`tokenizer_config.json`: dialogue open/close, cutoff, lyrics open/close, and
caption open/close. On an older ComfyUI build, this pack installs the same
token list on the module-local MiniMax Qwen tokenizer during startup and
refreshes its inverse vocabulary. General Qwen tokenizers are not changed.

When core exposes its native `MiniMaxQwenSDTokenizer`, the compatibility hook
stands down. Restart ComfyUI after updating core so tokenizer ownership is
detected before any H3 text encoder is loaded.

## Native MiniMax H3 Add Guide

When ComfyUI provides native **MiniMax H3 Add Guide**, core owns arbitrary
video/audio guide records and payload merging. This pack retains only the
marker-gated target-alignment behavior needed for Ref2VA continuation.

Place official Add Guide nodes after Loop Context so they append scene-local
anchors to the already constructed continuation guides.

## H3-Multishot and SolAttn

- H3-Multishot's recognized AV-bank payload is reused rather than wrapped a
  second time.
- Kijai's SolAttn H3 Morton observer composes safely in either activation order.
  Recognition is independent of the custom-node folder name: the upstream
  module, path-loaded PR helpers such as `sol_attn_minimax_v2`, renamed copies,
  and nested observer copies are identified by their audited `original_init`
  closure plus read-only `_video_span`/`_SPANS` registration behavior. Merely
  having a similar name or constructor closure is insufficient, so unknown
  layout-mutating wrappers remain rejected.
- Ref2VA media remains intact when continuation guides are merged.
- Changed layout assumptions and unknown wrappers fail loudly.

Keep Spectrum and other step-skipping systems disabled for baseline continuity
tests. KJ preview bridging is scoped to the active loop.

## Legacy widget widths

While any Context Loop node is present on a legacy LiteGraph canvas, the pack
works around
[ComfyUI frontend issue #12443](https://github.com/Comfy-Org/ComfyUI_frontend/issues/12443)
for all visible nodes. A separate Legacy Widget Width Fix node is unnecessary
but remains compatible.

Disable the embedded workaround under **Settings → MiniMax H3 Context Loop →
Compatibility → Widget widths** if another frontend or extension handles it.

## Platform notes

- Review and Assemble prefer `ffmpeg` but fall back to bundled PyAV.
- The Plan's Output action opens paths on the ComfyUI host. On headless or
  remote servers it copies the host path; the browser cannot open an arbitrary
  remote filesystem path on the client machine.
- Run Manager operates against the ComfyUI host's input/output folders and is
  therefore suitable for Docker and remote deployments.
- On WSL2, `--disable-pinned-memory` can help host pinning behavior, but it is
  separate from the top-level job-boundary fix documented in this release.
