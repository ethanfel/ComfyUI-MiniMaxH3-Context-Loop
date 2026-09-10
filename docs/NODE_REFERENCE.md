# Node guide

This is a user-facing map of the nodes you normally see in a Context Loop
workflow. It focuses on what to connect and what comes out; exact expert fields
remain documented by the node tooltips and the linked specialist guides.

## Read a node at a glance

**MiniMax H3 Saved Delivery Source** accepts an API-prepared `snapshot_json` and
outputs a verified saved manifest for H3 Chain Assemble. See the
[saved delivery guide](SAVED_DELIVERY.md) for preparation, source verification and
recovery. It leaves the active checkpoint selection unchanged.

The tables use this direction:

```text
input socket or widget  ──>  NODE  ──>  output socket
```

- **Socket** means connect another node.
- **Widget** means choose or type a value on the node.
- Inputs marked optional can stay disconnected for the normal path.
- Outputs described as status or summary are for inspection; they do not need
  to be wired for generation.

The flow overview in the [README](../README.md#how-the-graph-is-organized) shows
where the core nodes sit.

## How disabled nodes are shown

Disabled nodes remain part of the saved workflow, so their sockets and purpose
still matter. These docs keep their node-shaped representation and add an
explicit state label:

| Representation | ComfyUI mode | Meaning |
|---|---:|---|
| **Solid card — ACTIVE** | `0` | Executes when its output is requested. |
| **Grey dashed card — MUTED** | `2` | Present, but never executes. No output is produced. |
| **Dashed amber card — BYPASSED** | `4` | Skips its operation and forwards a compatible input. |

This follows the useful behavior of disabled-pack schema previews: input
sockets, widgets, defaults, and outputs remain visible even though the node is
not live. It prevents a muted recovery branch from looking like missing or
broken documentation.

In the shipped generation examples, **Load Manifest → Assemble — recovery** is
muted. Unmute that pair only to assemble saved scenes without rendering. In
some deferred-upscale examples, an attention override is bypassed intentionally
for compatibility.

## Main loop

| Node | Inputs you normally use | Outputs you normally use | What it does |
|---|---|---|---|
| **MiniMax H3 Generation Profile** | Widgets: Scene continuity, Audio profile. Optional: `lip_sync_options`. | `chain_policy`, `status` | Turns two user choices into the complete policy expected by Plan. |
| **MiniMax H3 Plan (Modern)** | Required `chain_policy`; organized Project, Canvas, Generation defaults, and Delivery settings; optional `project_assets`. | `plan`, `summary`, scene count, width, height, blend frames | Recommended for new graphs. Keeps the familiar scene-column editor, removes all legacy fallback controls, and compiles the same Plan contract as the original node. |
| **MiniMax H3 Context Loop Plan** | Widgets: Scene Plan, `run_name`, size, duration, seed. Optional sockets: `chain_policy`, `project_assets`. | `plan`, `summary`, scene count, width, height, blend frames | Original compatibility-preserving Plan. Existing workflows keep it unchanged. Connect Generation Profile, then use **Upgrade to Modern Plan…** from its context menu when desired. |
| **MiniMax H3 Chain Preflight** | `plan`. Optional: source timeline/audio, active tagged references or legacy reference schedule, scene/range and resume checks. | checked `plan`, `preflight`, `ready`, `status`, report JSON | Validates the full plan before model-heavy nodes execute. Connect the same reference registry used by Ref2VA, then wire its Plan output to Loop Start. |
| **MiniMax H3 Context Loop Start** | `plan`. Optional: scene range, source timeline, source audio, external context, active tagged references or legacy reference schedule. | `flow`, `state`, `status` | Starts scene 1, a bounded range, or a compatible resume. Connect the same reference route used by Ref2VA so its internal preflight resolves prompt `@tags`; never connect both reference routes. |
| **MiniMax H3 Context Loop Current Shot** | `state`. Optional source-audio fallback. | state, scene index/count, prompt, seed, raw length, steps, size, source-audio slice, blend frames | Exposes the current scene as ordinary ComfyUI values. |
| **MiniMax H3 Current Tagged Ref2VA Scene** | `state`, CLIP, video VAE, audio VAE, tagged references. Optional: Tagged Scene Options. | state, positive conditioning, latent, typed scene data | Compact prompt-driven Ref2VA route. It replaces the visible Current Shot + Tagged Ref2VA pair in new reference graphs. |
| **MiniMax H3 Context Loop Context** | state, conditioning, video VAE, latent. Optional: audio VAE, model, drift sigmas, anchors, lip-sync voice. | conditioning, trim frames, continuation flag, latent, model | Adds the selected visual/audio continuation. Scene 1 is effectively a pass-through. |
| **MiniMax H3 Context Loop Trim** | decoded images, trim count. Optional: audio, FPS, overlap controls, state. | delivered images/audio, overlap-ready images, overlap count | Removes the repeated head context and retains any frames needed for assembly blending. |
| **MiniMax H3 Context Loop Segment + Checkpoint** | state, delivered images, sampled latent. Optional: audio, overlap-ready images, denoised latent. | `segment`, `status` | Saves the scene movie, latent continuation, dependency record, and immutable revision. |
| **MiniMax H3 Pending Review** | Widget: defer completed batch. | `pending_review` | Connects to Review Gate to persist a complete candidate batch and stop without holding an execution open. |
| **MiniMax H3 Context Loop Review Gate** | state, segment. Widgets: enabled, timeout, candidate count, memory cleanup, partial assembly. Optional: `pending_review`. | reviewed `segment`, `status` | Pauses for live approve/retry/reroll/candidate selection, or stores and later resolves a deferred batch when Pending Review is connected. |
| **MiniMax H3 Context Loop End** | flow, state, images, sampled latent, reviewed segment. | manifest, manifest JSON, final context frames, final context latent | Recurses into the next scene or finishes the selected range. |
| **MiniMax H3 Chapter Delivery** | manifest, enabled, chapter number. | delivery manifest, manifest JSON, resolved chapter, snapshot path, status | Snapshots the generated scenes of one Plan Studio chapter for isolated export, even when unfinished. `0` selects the chapter containing the last generated scene; a positive number selects a particular chapter. Disable it to preserve whole-Run output. |
| **MiniMax H3 Context Loop Assemble** | manifest. Widgets: audio source, filename, bitrate. Optional: output copy, blend/tone controls, recovery media. | `video_path` | Verifies selected segments, joins them, and muxes the chosen audio into the final MP4. |

The H3 model loader, scheduler, sampler, and VAE decoders are ordinary ComfyUI
or MiniMax H3 nodes. Context Loop provides the plan, continuity, checkpoint, and
recovery layer around them.

## Authoring and policy nodes

| Node | In → out | Use it when… |
|---|---|---|
| **Scene Prompt Editor** | Plan → Plan | You want the stable per-scene prompt editor and prompt history. |
| **Rich Scene Prompt Editor (Experimental)** | Plan → Plan | You want the experimental rich editor and optional optimizer UI. |
| **Plan Studio (Experimental)** | Plan and optional Source Timeline → Plan | You want editorial placement, gaps, chapter views, previews, timeline controls, or picture-only alternate final-cut takes. |
| **Project Asset Carousel** | Project name/catalog; optional existing tagged refs or upscale model → project assets, references, Source Timeline | You want one project library instead of many loader/tag nodes. It also owns the Run's workflow write lock and exposes release/force-takeover controls. See the [Carousel guide](PROJECT_ASSETS.md). |
| **Scene LoRA Scheduler** | Model plus lazily revealed LoRA inputs; state → scene model | Different scenes use different connected LoRA routes. |
| **Lip-Sync Options** | Timing/denoise widgets; optional vocal stem → lip-sync options and voice | The Generation Profile uses **Lip-sync to source audio**. |
| **Advanced Policy Override** | chain policy + advanced transition → chain policy | You need Tone, Latent, Detail, Drift-Control, or Color-Stable Drift behavior. |
| **Manual Chain Policy (Legacy)** | Low-level transition/audio widgets → chain policy | An existing workflow already uses it. Prefer Generation Profile for new graphs. |
| **Legacy 0.4 Policy Adapter** | Old 0.4 fields; optional current policy → chain policy | You are rebuilding or migrating an old control surface. |

See [Scene authoring](SCENE_AUTHORING.md), [Audio and
continuity](AUDIO_AND_CONTINUITY.md), and the [complete Plan format](../H3_CHAIN_FORMAT_GUIDE.md).

## References and source media

| Node group | Main inputs → outputs | Use it for |
|---|---|---|
| **Tagged Picture / Video / Motion / Audio Ref** | Media + tag/options → tagged references | Build a prompt-selected `@tag` reference collection. |
| **Tagged Ref2VA** | Tagged refs + CLIP/VAEs + current scene values → conditioning, latent, fingerprint | Compile only the references used by the current prompt. |
| **Tagged Scene Options** | Reference/backend widgets → options | Keep non-default reference settings off the compact Current Tagged Scene node. |
| **Scene Data Extract** | typed scene data + selected field → typed value | Recover a secondary Current Tagged Scene value such as source-audio slice or RefMod sources. |
| **Scheduled Picture / Video / Audio Ref** | Media + scene selector/options → schedule | Assign media by scene number or range instead of prompt tags. |
| **Scheduled Ref2VA** | Schedule + current scene + CLIP/VAEs → conditioning, latent, fingerprint | Compile the active scheduled references for one scene. |
| **Source Timeline** | Source media/path and timing options → Source Timeline | Register the reusable video/audio timeline once. |
| **Audio Tracks** | Optional full mix, vocals, instrumental AUDIO → Source Timeline | Use vocals for source-locked lip-sync while preserving the full mix for delivery; mix stems only when a full mix is absent. See [grouped audio](AUDIO_AND_CONTINUITY.md#grouped-songs-full-mix-vocals-and-instrumental). |
| **Reference Video Prep** | Video + fit/timing controls → prepared reference | Prepare motion/reference video for H3 timing. |
| **Frame Gate** | Opening image; optional last frame + scene index → first/last-frame outputs | Apply the opening image only to scene 1. Do not bypass it in I2V workflows. |
| **Frame Index Switch** | Scene index + image inputs → selected image | Alternate last-frame targets across scenes. |
| **Existing Video Context** | Plan plus source video or frames; optional audio → external context | Prepend an existing clip and continue from its tail. |

Lazy motion loaders, source-timeline previews, semantic anchors, boundary-anchor
prepasses, and Patch Priority are advanced reference tools. Their behavior is
covered by [Scheduled references](SCHEDULED_REFERENCES.md) and [Advanced
workflows](ADVANCED_WORKFLOWS.md).

## Runs, recovery, and output

| Node | Main inputs → outputs | Use it for |
|---|---|---|
| **Run Manager** | Plan, archive controls, optional loader assets/Source Timeline/tagged refs → Plan, Source Timeline | Browse runs, restore a saved Plan and loader bindings, and archive recovery assets. |
| **Checkpoint Manager** | Saved UI selection; optional Plan → selected manifest | Pin a workflow-local lineage or choose **Selected chapter only** for chapter export/upscaling. Chapter output preserves original scene numbers and timing, and permits different resolutions in earlier chapters. Local output leaves the project and connected Plan unchanged. |
| **Load Manifest** | Plan; optional source media/context → manifest, manifest JSON, status | Verify and load saved segments without executing the generation loop. Usually muted in generation examples. |
| **Chapter Delivery** | Full/partial manifest + enabled + chapter number → chapter manifest, JSON, number, immutable path, status | Freeze the available scenes of a chapter and route finishing into its own folder. `0` means the chapter containing the manifest tip; `1`, `2`, etc. select a particular chapter, including a partially generated one. |
| **Chapter Recovery Load** | Run name + chapter number + optional snapshot id → verified chapter manifest | Recover the newest snapshot of a particular chapter, or paste its 32-character id to recover an older sealed version. |
| **Export PNG Sequence + Audio** | Manifest + optional video/audio VAEs, save workers, cached/strict verification, reuse existing → output directory, PNG count, status, WAV path | Either VAE independently exports PNGs or WAV. Chapter exports append new scenes after an unchanged verified prefix by default; changed content uses a fresh folder. PNG count includes reused frames. |
| **Full-Chain Latent Video Adapter** | Generated manifest + video VAE → cached continuous video | Build a low-RAM, disk-backed whole-run input for SeedVR2. |
| **Assemble** | Manifest + audio/output choices → MP4 path | Join a complete or partial source/upscale manifest. |

See [Runs and recovery](RUNS_AND_RECOVERY.md) for review branches, resume rules,
folder layout, deferred upscaling, and assembly options.

## Masking nodes

| Node | Main inputs → outputs | Use it for |
|---|---|---|
| **Masking · Loop Source AV Target** | State + source video/audio + H3 target → scene target latent | Slice the exact source interval for the current loop scene. |
| **Masking · Loop Mask Slice** | State + mask batch → current scene masks | Keep a tracked mask aligned with recursive scene timing. |
| **Masking · Trim Source AV** | Source frames; optional audio → valid-length frames/audio, length, status | Trim the tail to H3's largest valid `17k+5` frame count without padding or resizing. |
| **Masking · Grid Preview** | Mask + H3 grid controls → snapped mask, preview, status | Inspect the actual 32×32 H3 cells that will change. |
| **Masking · Apply Target Mask** | H3 target latent + mask → masked target | Combine the spatial mask with any existing AV protection mask. |
| **Masking · Master Audio + Video Prefix** | H3 target + full source track + timing/prefix options → masked target, audio slice | Protect exact source audio while controlling the incoming video prefix. |
| **Masking · Two-Clip AV Bridge** | Start/end clips + H3 target → bridge target | Protect both ends and generate only the middle gap. |

Use [Masked editing](MASKED_EDITING.md) for the required wiring and mask/audio
semantics.

## Upscale and research nodes

Deferred-upscale workflows include a checkpoint adapter, current-scene node,
reference-conditioning recovery, pass-2 AV preparation, segment save, and an
upscale Loop End. The de-rope example adds Guard, Freeze Mask, Continuity, and
Recovered AV nodes. These nodes are designed to be used as a supplied group,
not assembled one at a time from this short reference.

Start from a [deferred-upscale example](../example_workflows/README.md#deferred-upscale)
and use [Runs and recovery](RUNS_AND_RECOVERY.md#whole-chain-seedvr2-finishing)
for the detailed contracts.

Nodes whose names end in **Experimental**, **Research**, **Internal**, or
**Legacy** are not the normal starting point. Keep their supplied wiring unless
you are following the matching specialist guide.
