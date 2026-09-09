# Version 0.5 workflow architecture

This document freezes the contracts and migration boundaries for the 0.5
workflow-UX release. Implementations may evolve behind these contracts, but a
later step must not silently change their meaning.

## Release invariant

A normal workflow selects source media once, chooses its audio intent once,
chooses an incoming transition preset per scene, and can validate or resume
without wiring the same full media value to several nodes.

Version 0.5 remains compatible with saved 0.4 workflow JSON and checkpoint
metadata. Existing node class ids and positional output slots are not removed
or reordered during this release.

## Source Timeline contract

`H3_SOURCE_TIMELINE` is the single source-media contract. Its serialized
version is `h3_source_timeline_v1`.

It records:

- a file-backed video descriptor when picture is present;
- embedded, external-path, deferred-tensor, or absent audio;
- native FPS, stream PTS origin, duration, and the derived 24-fps extent;
- a native-frame skip offset and its exact time origin;
- independent video, audio, and combined timeline fingerprints;
- original, archived, and run-owned recovery locations.

The descriptor never requires a decoded full-video IMAGE batch. Scene consumers
request one overlap-aware 24-fps window. Path-backed audio is also decoded only
for the requested scene. If the only available audio is a ComfyUI tensor, Loop
Start materializes one normalized run-owned file and places only its descriptor
in recursive state and manifests.

Primary consumers are Loop Start and Tagged Motion Ref. Current Shot obtains
scene slices from chain state; Review, Manifest Load, and Assemble obtain the
descriptor from the saved manifest. Legacy VIDEO/AUDIO sockets remain adapters.

## Audio intent contract

Audio intent uses `h3_audio_policy_v1`, three independent axes, and an optional
exact-target switch:

| Axis | Values | Meaning |
|---|---|---|
| Final audio | `generated`, `source`, `none` | What Assemble places in the final MP4 |
| Source reference | `on`, `off` | Whether the active source window guides H3 generation |
| Generated continuity | `on`, `off` | Whether the prior sampled audio latent continues into the next scene, including AV-mask prefixes |
| Source audio target | omitted, `locked` | Whether the scene-local source window replaces and fully protects H3's complete target audio latent |

The compact **Lock source audio** switch emits `source_audio_target=locked` and
canonically resolves Source reference and Generated continuity to `off`. This
prevents a loose Ref2VA audio bank or predecessor prefix from competing with the
exact target clock. Chain Context audio-VAE encodes the current source window,
crops only right-side encoder padding to the target's authoritative 40 Hz grid,
and composes an all-zero audio denoise mask with the selected video boundary.
Final-audio selection remains independent; `source` uses the Source Timeline
track for assembly.

Paired audio on a tagged motion reference is a fourth, reference-local decision:
`embedded` or `off`. It never selects the final soundtrack implicitly.

Legacy Plan modes migrate exactly:

| 0.4 `audio_mode` | Final | Source reference | Generated continuity |
|---|---|---|---|
| `source_track` | source | on | off |
| `generated_audio` | generated | off | on |
| `source_plus_timeline` | source | on | on |

New Plan nodes default to generated final audio, no source reference, generated
continuity, and no source target lock. Saved 0.4 widget values retain their old
behavior.

Normal workflows author both contracts through `h3_chain_policy_v1`, a
one-wire wrapper. Plan immediately expands it back into the canonical Audio
Policy and Transition Policy records before hashing, so compatibility follows
resolved behavior rather than the authoring graph. The compact node derives
audio overlap from the selected boundary (Cut 0, Guide 22, AV 39). An
Advanced Policy Override composes a named experimental transition over that
policy without replacing its audio record. An independent numeric audio
overlap is available only through the Legacy 0.4 Policy Adapter.
Policy nodes are composable wrappers over the same `h3_chain_policy_v1`
record. Chain Policy owns audio intent and a normal transition. Advanced
Policy replaces only the transition with a named semantic recipe. A connected
Legacy Adapter preserves the incoming audio record and replaces only its raw
transition/audio-context fields; standalone, it also translates 0.4
`audio_mode`. The policy layer nearest Plan owns the effective boundary.

## Incoming transition contract

Transitions use `h3_transition_policy_v1`. A scene setting always describes
how that scene consumes its predecessor; it does not retroactively redefine
the completed predecessor.

| Preset | Continuation implementation | Context |
|---|---|---:|
| `cut` | guide with no carried picture | 0 frames |
| `guide` | guide rows | 22 frames |
| `hard_av` | protected AV prefix | 39 frames |
| `soft_av` | exact picture prefix with feathered audio release | 39 frames |

The normal selector intentionally exposes only those four tested choices.
Advanced Policy Override selects named semantic recipes including
`tone_guide`, `latent_guide`, `detail_guide`, `detail_av`, `drift_av`, and
`color_drift_av` while preserving the upstream audio intent. The Legacy 0.4
Policy Adapter owns raw implementation, visual-context, and audio-context
values, including the old dual-stream `feathered_av` implementation.
`latent_guide` requires video encode mode and at least five positive context
frames. `tapered_guide` accepts the listed Guide context lengths; only the
22-frame preset has published validation, so other lengths remain
experimental. The resolved values, not merely the preset name, enter scene
metadata.

Detail AV is deliberately narrower than the generic AV implementations. Its
v2 recipe requires exactly 39 frames / 12 video-latent steps, applies
matched-standard-deviation Gaussian noise only to a disposable copy of the
carried video prefix, and tapers 0.30 -> 0.225 -> 0.15 -> 0.075 -> 0.00 over
the final four steps. Audio and masks retain Hard AV semantics. The recipe
version,
parameters, and deterministic seed rule are part of `incoming_boundary`, so a
resume cannot silently cross an implementation change.

Drift-Control AV keeps the predecessor latent clean and changes only the
disposable incoming prefix mask. For each model evaluation it resolves the next
strictly lower scheduler sigma, applies `next/current` to the oldest eight
video steps, and tapers the final four `.75/.50/.25/.00`. Chain Context's MODEL
output couples the sampler-side inpaint blend to H3's per-row timestep mask;
using only one of those paths would give the model a mislabeled prefix. The
recipe is dependency-hashed and initially validated at 20 steps.

Color-Stable Drift AV keeps that exact mask/sampler behavior and adds a
video-only scene-one color anchor to the disposable copied prefix. It measures
the retained RGB tail of the first generated scene and the current predecessor,
decodes the 12-step prefix, and computes
`E(weakly graded decode) - E(original decode)`. Only that delta is added to the
sampled prefix, so ordinary VAE round-trip bias is cancelled instead of
replacing the sampled latent with a re-encode. A 3x3 latent low-pass limits the
change to broad tone/chroma, and a full-prefix smoothstep keeps the oldest
overlap step exact while reaching the bounded correction beside the generated
future. Brightness is capped at six code values, saturation at six percent,
and both use half the measured correction. The predecessor checkpoint and
audio latent are never modified. The complete recipe is dependency-hashed.

When a workflow splits sigmas and switches H3 models, Chain Context receives
the original unsplit schedule but no MODEL. Each raw model passes through its
own lightweight Drift-Control Model Patch with the same Current Shot state,
Chain Context latent, and full schedule. This keeps next-sigma selection
continuous across the sampler boundary without making Chain Context own either
model loader or retaining duplicate weight tensors.

## Scene dependency contract

Each accepted scene stores `h3_scene_dependency_v1`. Dependencies have four
scopes:

1. `global_generation`: model, VAE, LoRA, sampler, scheduler, CFG, and other
   generation-body configuration.
2. `scene_generation`: prompt, seed, raw length, steps, active references, and
   scene-local media windows.
3. `incoming_boundary`: the transition and context used to enter that scene.
4. `assembly_only`: final mux media that did not guide generation.

Resume through scene N compares scopes 1–3 only for accepted scenes 1..N.
Changing scene N+1 or its incoming boundary cannot invalidate scene N. Changing
an assembly-only source track does not invalidate sampled video. If source
audio guided a scene, only that scene's canonical PCM window is a generation
dependency; unrelated future audio is not.

Every mismatch report identifies its scope, scene, field, saved value, current
value, and whether regeneration is required.

## Compatibility rules

- Existing node ids remain registered.
- Existing outputs retain their positional indices.
- Existing required widget order remains readable.
- Plan's retired `audio_mode`, `continuation_mode`, `context_length`, and
  `audio_context_length` widgets remain serialized for 0.4 compatibility but
  are hidden in the normal 0.5 presentation. Legacy 0.4 Policy Adapter exposes
  those choices as an explicit compatibility route and emits one combined
  policy output.
- New policy fields are appended or introduced through frontend-backed
  migration rather than inserted into old positional layouts.
- Legacy `audio_mode`, full AUDIO fan-out, direct media paths, and manual
  generation fingerprints remain accepted for 0.5.
- Existing checkpoint formats retain their current generic hash fallback when
  structured dependency records are unavailable.
- Diagnostic and legacy sockets may be visually hidden, but the backend slots
  remain until a later breaking release.

## Preflight contract

The same pure preflight implementation serves Loop Start, Chain Preflight, and
Plan Studio. Connect the active Tagged reference registry (or the legacy
Scheduled registry, but never both) to every preflight entry point used by the
graph. Loop Start receives the registry as run-local graph input; references
are not copied into the Plan and prompt `@tags` are not rewritten. Preflight
runs before model-dependent sampling and reports:

- resolved scene frame counts, durations, source windows, and overlap trims;
- native-FPS/PTS to 24-fps mapping and skip origin;
- source duration, required duration, exact shortfall, and last complete scene;
- active/unresolved reference tags and their scene windows;
- source and archive availability;
- runtime guide, mask, and known attention-wrapper compatibility;
- automatic generation-body fingerprint coverage;
- resume eligibility plus structured mismatches.

Errors include a user action. Sample counts and latent-grid details may appear
under diagnostics, not as the primary explanation.

Plan Studio also consumes those same reference-window results for its motion
comparison track. It does not decode the full reference into IMAGE tensors.
The server seeks and transcodes only the selected scene window to a cached
low-resolution MP4; the comparison player offsets Guide windows past the
incoming context that is removed from the delivered scene.

## Durable handoff state

Top-level job boundaries between heavyweight H3 prompts are tracked in a
separate durable handoff store (`handoff_state.py`), never in the JSON Plan.
Records are stored under
`output/h3_chains/<run_name>/orchestration/<handoff_id>.json` with format
`h3_top_level_handoff_v1` and hold only lightweight identity values:
run name, scene number, start clip, candidate batch/ordinal/count, seed,
source prompt ID, source revision and checkpoint SHA-256, workflow
fingerprint, status, attempt counters, and timestamps. Tensors, models,
conditioning, VAEs, CLIP, samplers, and live object references are rejected
on write and on read, and prompt text, shots, references, or model settings
are never copied in; the Plan stays authoritative for generation semantics.

Allowed actions are `next_scene`, `next_candidate`, `await_review`,
`complete`, and `manual_resume`. Statuses follow the machine
`pending -> claimed -> queued -> consumed`, with `cancelled` and `failed`
as terminal stops and `claimed -> pending` reserved for the bounded
`release` retry path (`attempt` increments per claim and an exhausted
`max_attempts` budget fails the record instead of retrying forever).

Every update is written temp-file + flush + fsync + atomic replace behind a
per-run lock, so the claim primitive is exactly-once: the first pending to
claimed wins and duplicate terminal events or concurrent listeners never
re-claim. Corrupt or unknown-version records raise, are listed with a
corruption reason, and are never auto-queued, auto-repaired, or reverted;
they are left exactly as found for manual recovery.

## Durable review gate

A pending Review Gate also persists a lightweight identity snapshot
(`review_inventory.py`, format `h3_review_snapshot_v1`) under
`output/h3_chains/<run_name>/orchestration/review_<token>.json`: token, run
name, scene, the public candidate list (number, revision, seed, created_at,
has_audio, warning), and the deadline. No IMAGE tensors or media bytes are
stored; previews always come from the saved segment/checkpoint inventory,
which remains authoritative. Snapshots are written when a review becomes
pending, marked `decided` (with the decision action) when it resolves, and
re-surfaced by the review list route with `durable: true` after a browser
refresh or a ComfyUI crash/restart, so saved candidates stay reviewable
without a live PromptExecutor. If that executor is lost, a recovered
snapshot is **read-only recovery inventory** (`actionable: false`): it
identifies saved candidates/checkpoints for manual resume, but cannot
approve/retry through a vanished future. Tensor-like values are rejected on
write, and the Plan JSON is never touched.

## Top-level scene requeue mode

`MiniMax H3 Context Loop End` exposes an optional `execution_mode` widget
(default `recursive`, unchanged behavior). In `top_level_requeue`
mode the loop stops immediately after the scene checkpoint is persisted: it
writes a durable `next_scene` handoff and a partial through-clip manifest
instead of recursively expanding the next H3 scene inside the same prompt.
The frontend coordinator waits for queue-safe state plus a configurable
cleanup interval, validates the workflow identity and the predecessor
checkpoint, claims the handoff exactly once, sets the existing Loop Start
widgets, and queues the same workflow as a new top-level prompt. Errors and
interruptions never auto-queue; a pending handoff can be resumed or
cancelled manually. The mode lives on the node (workflow JSON), never in
the Plan JSON.

## Socket presentation rules

The primary graph displays only generation-bearing connections. Status,
manifest JSON, booleans used only for inspection, legacy passthroughs, and
conditional audio sockets are Advanced. Conditional inputs appear when their
policy needs them. Superseded Plan policy and layout widgets are also hidden;
the node menu can reveal them for diagnosis. Hiding a socket or widget must not
change its backend index or serialized position.

## Delivery order

1. Freeze contracts and 0.4 fixtures.
2. Implement Source Timeline and legacy adapters.
3. Introduce independent audio policies.
4. Add transition presets.
5. Migrate all consumers to the timeline.
6. Add preflight.
7. Store and compare structured scene dependencies.
8. Hide redundant sockets without changing positions.
9. Clarify Run Manager and prompt-history state.
10. Run migration, integration, and release validation.

## Release validation

All ten delivery stages are implemented. The maintained workflow catalog uses
one-wire Chain Policy, Source Timeline, model-free preflight, and the durable
top-level prompt lifecycle described above. The migration tool is idempotent,
the frozen 0.4 positional contract is covered by regression tests, and
backend/frontend release checks enforce a single package version. Archived 0.4
workflows remain unchanged examples of the supported compatibility route.
