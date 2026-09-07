# Runs, review, and recovery

## Review Gate

Place **Review Gate** between Segment + Checkpoint and Loop End. Each scene is
persisted before the gate waits, then the gate offers:

- **Approve & continue**
- **Generate next candidate** when a multi-take batch is not complete
- **Retry scene / seed / length**
- **Reroll seed**
- **Approve & stop**, optionally assembling a partial video

Set Review Gate's optional **candidate_count** above 1 to collect several
different-seed takes. By default it generates the full batch and pauses once;
enable per-candidate inspection only when you want **Generate next candidate**
between takes. During an automatic batch you can still select a completed take
early and interrupt the remaining batch. The
carousel previews every completed take with synchronized sound; arrow keys and
the visible dots move between them. Mark one or more takes to keep, then choose
the take that becomes the active continuation. Its saved video frames and AV
tensors—not the last generated take—become the context for the following scene.
The active take and checked alternatives remain in checkpoint history; unkept
alternatives are deleted only after the active checkpoint is promoted safely.
The default value of 1 keeps the normal one-take review behavior. The widget can
be converted to an input and driven by a regular INT node; the safety limit is
20 candidates per scene.

Connect **MiniMax H3 Pending Review** to Review Gate when the batch should not
hold a live ComfyUI execution. Its connection overrides a disabled Gate and
per-candidate inspection: all requested candidates are checkpointed, a small
record is written under `pending_reviews/`, and execution stops at that scene.
The media is not duplicated. Later, load the same run in the Plan/Project Asset
Carousel, use **Pending candidate reviews → Refresh** in Review Gate, select the
batch and click **Review**. Choosing **Use this take & prepare resume** activates
that candidate's exact checkpoint lineage, keeps the checked alternatives,
deletes only unkept candidates, and arms Loop Start for the following scene.

Notification sound, automatic timeout, and model unloading while waiting are
optional. Drag the bar below the player to resize it; double-click to restore
the default height.

When a Scene Prompt Editor or Rich Scene Prompt Editor is bound to the same
Plan, Review Gate selects the scene under review there automatically. Editor
changes are used by **Retry prompt / seed** or **Reroll seed** through the live
Plan prompt. In 0.5, Review Gate's own prompt field is disabled by default.
Restore it under **Settings → MiniMax H3 Context Loop → Interface → Review Gate
→ Enable prompt editing inside Review Gate**. When enabled, text explicitly
typed in that field wins for the submitted retry and is synchronized back to
the Plan and connected editor after the server accepts it.

During sampling, the optional floating **Cancel & reroll scene N** control
targets only the active H3 prompt. It waits for confirmed interruption, writes a
new explicit scene seed, moves Loop Start to that scene, preserves a bounded
range end, and queues normally. Once saving or review begins, Review Gate owns
the retry instead.

Disable the floating control under **Settings → MiniMax H3 Context Loop →
Interface → Cancel & reroll** without affecting Review Gate.

## Between-scene memory cleanup

Loop End can apply a runtime-only `between_scene_cleanup` policy after the
current scene and checkpoint are durable, immediately before it starts the next
scene or retry:

- `off` keeps ComfyUI's normal caches.
- `unload_models` unloads model weights and empties the device allocator.
- `fresh_scene` first asks ComfyUI's active RAM-pressure cache to evict reusable
  execution outputs and runs Python garbage collection, then releases remaining
  pinned model pages, unloads models, and empties the device allocator. Use it
  for chains that switch large models between scenes; the next scene will reload
  anything that was evicted.

The policy does not enter Plan or resume hashes. It deliberately preserves the
small recursive carry and dynamic graph result because ComfyUI has no supported
way to reset the entire executor while that graph is still resolving. On cache
types other than RAM pressure, `fresh_scene` still unloads models and clears
allocators, but no executor-output eviction callback is available.

## Resume

For a fresh run:

```text
run_name: choose a new name
start_clip: 1
scene_range: blank
```

To resume scene N, keep the original `run_name` and dependency settings, then
set `start_clip: N`. The loop loads checkpoint N−1 and validates all completed
predecessors that the selected scene actually consumes. Editing scene N or
later is safe; changing an earlier prompt,
seed, timing, source waveform, Plan compatibility setting, or
`generation_fingerprint` invalidates the dependent resume.

A visually new scene can still consume the immediately previous generated
audio when its video context is `0` but its audio context is positive. That
audio-only edge validates the predecessor's prompt/model/source identity and
saved artifacts, but ignores its unrelated incoming visual-boundary recipe.
Set both scene video context and audio context to `0` (or select Visual Cut)
for a completely independent scene.

Loop Start's `verify_resume_history` switch is enabled by default. Disable it
only when you intentionally want scene N to consume the existing saved scene
N−1 despite a changed Plan. The override skips Plan/history matching; it does
not skip missing-file checks, SHA-256 artifact validation, checkpoint tensor
validation, or metadata's own recorded-history consistency. Consequently, any
new settings that describe the saved predecessor are not retroactively present
in its pixels or AV latent.

Plan-wide continuation mode and context length are the exceptions: they choose
how the next scene consumes its saved predecessor. Changing either does not
alter completed frames or their saved AV latent, so it does not invalidate the
prefix. If the checkpoint's cached decoded tail is shorter than the newly
requested context, the loop re-decodes its complete saved video latent and
extracts the longer tail without regenerating the scene. Explicit per-scene
continuation and context overrides remain part of that scene's history.

Review Gate's checkpoint browser can set up this resume and preview the joined
partial through the selected predecessor.

**Manifest Load** also supports interrupted runs. It discovers the longest
contiguous active checkpoint prefix beginning at scene 1, verifies every scene
and artifact through that point, and emits a partial manifest when later scenes
have not been saved. Connect that output directly to **Assemble** to recover the
finished prefix without sampling again. A missing scene ends the prefix; an
orphaned later checkpoint is never joined across the gap. When every planned
scene is present, the same node emits the normal completed manifest.

### Restore an earlier scene revision

**Refresh** in Review Gate discovers the active checkpoint and every immutable
revision retained for that scene. Choose the scene to resume, then select the
desired version of each predecessor under **Checkpoint history**. Clicking
**Restore & load** validates the selected MP4, safetensors checkpoint, hashes,
shared prompt, and compatibility contract before atomically promoting the
selected prefix. The corresponding prompts, seeds, lengths, steps, and scene
identifiers are restored into the connected Plan, and Loop Start is armed for
the next scene.

The active versions are selected by default. Restoring an earlier version does
not delete the current one, so another revision can be promoted later. Exact
continuation requires the revision's checkpoint metadata and safetensors file;
an MP4 copied from `segments/` or `reviews/` alone cannot recreate the saved AV
latent. When only video survives, use Existing Video Context as a re-encoded
continuation instead.

Retrying, rerolling, and candidate collection intentionally retain earlier
files as immutable revisions; they are recovery points rather than abandoned
temporary files. Inactive leaf revisions can be deleted from the same panel or
the dedicated Checkpoint Manager to reclaim space.
Review Gate now retrieves a fresh server-side deletion preview before asking
for confirmation. Active revisions and revisions with dependent later scenes
cannot be deleted. Cleanup is limited to that revision's segment, safetensors
checkpoint, prompt/audio/blend sidecars, unshared preview, and versioned
metadata; Plan archives, assets, prompt history, assembled exports, and other
revisions are never included.

## Checkpoint Manager

Connect the active Plan to **MiniMax H3 Checkpoint Manager** when you want its
run preselected or want **Load selected branch** to restore saved Plan values.
The node outputs only `selected_manifest`; it is not a Plan pass-through and is
normally kept beside the generation route. Its run selector can inspect any
other folder under `output/h3_chains`.

Select a **branch heading** to choose the whole generated branch for output.
Clicking an individual clip only previews it; it does not shorten the output
or switch its branch. Use the downstream upscale/export range controls to
choose which scenes to process.

For an experiment, click **Use branch locally**. This saves the entire browsed
branch (the row containing the previewed clip), not just its prefix through
that clip. It pins the exact lineage on `selected_manifest` in this workflow
only, including the earlier chapters included in that selection. Browsing
other takes, refreshing, reopening the workflow, or activating another branch
in a different tab cannot move this pin. Saved files are still verified on
execution: a missing or corrupt pinned take fails instead of falling back to
the project's active branch. The output row and **local output** badges show
which revisions are pinned. **Follow branch selection** releases the pin;
output then follows branch-heading selections, never clip previews. An explicit
run switch asks before releasing it. Save the workflow to keep the pin on disk.

Older workflows can contain a partial pin saved while previewing an ancestor.
The output row flags this. Choose the intended branch heading and click
**Use branch locally** once to replace that old selection. Shared ancestors
can lead to multiple branches, so reopening a workflow never guesses which
descendant you intended or silently switches an existing snapshot.

**Make branch active (project)**, **Roll active branch back (project)** and
**Load selected branch** remain project-wide actions. Local selection neither
restores connected Plan settings nor arms generation/resume. It does not require
project ownership; downstream nodes retain their own write protections. Use the
manager's manifest output for the experiment, not Manifest Load (which reads the
project's active checkpoints).

The manager groups immutable scene revisions into inferred branches. A revision
can appear in more than one branch when it is their shared ancestor. Selecting
a revision shows its saved preview, prompt, seed, timing, canvas, storage,
parent, following scenes, and the exact video/audio frame context those
following scenes consume. Older checkpoints derive this graph from predecessor
revision and checkpoint hashes; newly saved checkpoints also carry a stable
branch id and effective context fields.

Plan chapters are checkpoint-management boundaries. The **All scenes** tab
shows a separate graph for each chapter. Project-wide activation of a Chapter 2
branch changes only Chapter 2 pointers and connected Plan scene values; Chapter
1 keeps its current active branch. The chapter-start checkpoint can retain its
original predecessor as provenance, but that structural edge does not choose
the preceding chapter. Explicit visual or audio context recorded by a scene is
still preserved as a generation dependency.

### Seal, export, and recover a particular chapter

Put **Chapter Delivery** after Loop End, Manifest Load, or Checkpoint Manager
and feed its `delivery_manifest` to Assemble, Export PNG Sequence + Audio, or
Full-Chain Latent Video Adapter. The chapter boundaries come directly from Plan
Studio; there is no second chapter definition to keep synchronized.

- `chapter_number = 0` selects the chapter containing the manifest's last
  generated scene. The chapter does not have to be complete.
- `chapter_number = 1`, `2`, `3`, and so on selects that particular chapter
  even when later chapters already exist. If it is unfinished, only its
  contiguous generated scenes are selected. A chapter with no generated scenes
  still reports an error.
- `enabled = false` passes the complete incoming manifest through and preserves
  the traditional whole-Run final.

Each selection creates a content-addressed, immutable recovery manifest. It freezes
the selected scenes, checkpoint revisions, trims, placements, final-cut
alternates, subtitle origin, and source-audio offset at that moment. A later
chapter therefore cannot silently grow or replace Chapter 1. Re-sealing changed
content creates another snapshot; it does not destroy the previous one.
Partial snapshots record both their exported `end_scene` and the chapter's
`planned_end_scene`, with `chapter.complete = false`. For example, a chapter
planned through scene 10 can be exported through scene 8 now and extended later.

Use **Chapter Recovery Load** with the Run name and chapter number to recover
the newest sealed snapshot. Paste the full 32-character snapshot id when you
need a particular older delivery. Its output is the same manifest type used by
the finishing nodes, so recovery does not require loading or extending every
other chapter.

Chapter output remains inside the original Run but is isolated by number and
chapter id:

```text
output/h3_chains/<run_name>/chapters/02_<chapter_id>/
├── manifests/<snapshot_id>.json
├── final/<filename>.mp4
├── frames/<export_name>/
└── upscaled/seedvr2/source/
```

If a branch ends with an empty next-scene slot and saved candidates
exist elsewhere, the graph displays **Reuse saved clip**. Click it to preview
the available candidates and choose **Attach selected candidate**. This is
offered only when the candidate consumes neither predecessor video context nor
generated-audio continuity. The manager creates a new immutable lineage record
pointing at the chosen parent; the original video, audio, prompt, and checkpoint
files remain shared and are not regenerated or copied. Shared-file deletion is
reference-aware, so those files are retained until the last lineage record that
uses them is deleted.

Reuse is checked against the saved take's resolved context and audio policy,
not the current Plan. Candidates that cannot safely change parents show a
reason instead of disappearing. A rollback tip still appears as an active
branch even when its old descendants remain saved.

Rerendering an earlier scene can leave old pointer files on disk. Checkpoint
Manager, Plan Studio and recovery exclude those disconnected descendants from
the active chapter branch. They remain available as saved inactive takes; no
media, checkpoint, prompt, scene slot or chapter export is deleted. Use
**Make branch active / Roll active branch back** to explicitly reconcile those
old pointers. Recovery stops at the coherent saved prefix, and resuming through
an inactive tail requires selecting or attributing a matching lineage first,
even with history verification disabled.

If deleting the active branch tip rolls the run back while alternate leaf
revisions remain, select the surviving branch and click **Make branch active**.
The manager validates and promotes that revision's chapter lineage directly,
including when no Plan is connected. With an editable Plan connected, it also
restores the lineage's saved scene prompts, seeds, lengths, steps, context,
LoRA route, and boundary/audio overrides. Exact saved prompts reactivate their
existing Prompt Editor history revisions instead of adding duplicates.
Immutable revisions, workflow state, references, and assembled videos are left
untouched. You can then keep the chosen candidate active and continue deleting
the rejected inactive leaves.
**Load selected branch** remains the separate resume operation: it also restores
saved Plan inputs and arms Loop Start for the following scene.

Deletion is deliberately one scene revision at a time:

1. Select an inactive revision.
2. Inspect its complete file list, estimated reclaimed size, and preserved
   categories.
3. If later revisions depend on it, select and delete those leaf revisions
   first.
4. Confirm the now-safe leaf deletion. If anything changed after the preview,
   the server refuses it and asks for a fresh preview.

This first release does not bulk-delete branches. The leaf-first workflow makes
the exact context consequences visible and avoids silently orphaning later
checkpoints.

### Saved processing tabs

Checkpoint Manager has **Original**, **DeRoPE**, and **Latent Upscale** tabs.
**Pixel Upscale** and **Other processing** also appear when those saved profiles
exist. Chapter filtering applies to the processing views. The original branch
rows stay in place, with each clip's saved processing takes shown beneath its
source revision, including retained older takes and multiple profiles.

The catalogue reads the existing `upscaled/<profile>/checkpoints/` directories
at run and chapter scope. It matches both source revision and checkpoint hash;
scene number alone never attaches a take to another branch. Attributed aliases
with matching saved content can share versions. Unresolved sources remain
visible in a separate section. Stage labels come from the saved recipe/backend,
not the profile folder name; the bundled combined LBH + DeRoPE recipe appears
under DeRoPE at its actual saved resolution.

Switching tabs or inspecting a derivative does not rewrite `selected_manifest`,
move a local pin, activate a branch, restore a Plan, or delete a checkpoint.
For a later latent-upscale pass, select a saved take in **DeRoPE**, then click
**Use DeRoPE branch locally**. This pins the whole saved processing branch to
the browsed original branch; the downstream Adapter still controls start/end.
Scenes absent from that DeRoPE branch automatically use their selected original
take. A saved but incomplete/corrupt latent is an error, not an original fallback.
Return to **Original → Use branch locally** to explicitly use originals again.

New saves keep an immutable processing-lineage snapshot. Older saves can use
their existing full/partial profile manifest if it identifies the chosen take.
A shared take with multiple processing descendants is ambiguous: choose a later
take unique to the desired processing branch. No "latest scene" mixing occurs.

The inspector reports canvas, RAW/delivered frames, audio route, full-latent
save status, profile and metadata location. A continuation tail is explicitly
not a full latent. Listing checks file availability without loading tensors or
hashing every large checkpoint; availability is not execution validation.

To remove a saved processing take, select it in its processing tab and click
**Delete processed version**. The file/size preview and confirmation cover only
that take's video, checkpoint, audio and prompt sidecars, its revision metadata,
its current processed pointer (if still selected), and affected full/partial
branch manifests. Removing a pointer does not promote another take. Older takes,
original generation checkpoints, shared references/caches and assembled videos
are kept. This also works for processing takes whose original is unavailable.

Deletion is blocked while another saved take depends on that processing source
or continues its saved branch; the inspector lists clickable dependents to
delete first. Ownership is checked again on confirmation, and changed files or
dependencies require a fresh preview. An in-flight save cannot republish a
deleted processing dependency. A workflow-local pin is never silently redirected:
if it referenced the deleted take, explicitly select another source before running.
Deletion is permanent; the preview does not load or hash large tensor files.

### Alternate final-cut takes

Use an alternate when one accepted scene needs a prompt-level visual correction
but later scenes already depend on its original checkpoint.

1. Open that scene in Plan Studio and expand **Alternate final-cut take**.
2. Edit the alternate prompt and seed, then enable the draft.
3. Queue normally. Loop Start renders only that scene.
4. Approve the alternate in Review Gate.

Acceptance selects the alternate picture for preview, assembly, PNG export,
whole-chain latent finishing, and deferred latent/pixel/CAT upscale loops.
It does not replace the active generation
checkpoint: later scenes keep their original visual/audio ancestry, and final
audio for the corrected scene remains the original audio.

Deferred upscale resolves the selected ALT before reading source tensors or
reference conditioning, for both full-branch and chapter output. It uses the
ALT's video latent, prompt, seed and reference identity, but reads the original
audio separately without loading another full video tensor. Adapter/current
status names the selected ALT, and saved processing takes remain linked to the
base scene in Checkpoint Manager. A sealed chapter uses its frozen final-cut
selection; an unsealed full-branch input uses the current selection when the
loop starts.

Resume checks the actual picture source: an upscale previously made from the
original cannot be reused for a newly selected ALT (or vice versa). Restart at
the affected scene, or use a new profile. Saved DeRoPE must likewise belong to
the selected picture; scenes absent from a partial DeRoPE branch use their
selected ALT/original. Changing an ALT never overwrites generation checkpoints
or silently swaps an already selected DeRoPE latent for unprocessed media.

Plan Studio marks the selection `ALT`. Checkpoint Manager nests the immutable
alternate under its base take rather than drawing a new continuation branch.
Choose **Original** in Plan Studio to restore the base picture at any time.

An alternate must preserve the scene identity and duration. If a later scene
exists, set the blend entering that scene to `0`: its saved overlap still shows
the original base take, so assembly rejects a crossfade from an alternate.

### Whole-chain SeedVR2 finishing

SeedVR2 is best treated as a final whole-video backend, not as another
scene-recursive H3 pass. Select the final scene of a complete branch in
Checkpoint Manager and use this graph:

```text
Checkpoint Manager.selected_manifest
        + original H3 video VAE
        ↓
Full-Chain Latent Video Adapter
        ↓ native, file-backed VIDEO
SeedVR2 Direct Video Upscaler
        ↓
core Save Video
```

**Full-Chain Latent Video Adapter** verifies and decodes the original
safetensors video latent for every selected scene; it never reads the saved
H.264 scene previews. It trims repeated context, applies the saved incoming
blend schedule, and writes one lossless RGB movie before SeedVR2 starts.
SeedVR2 therefore chunks a continuous timeline rather than restarting at H3
scene boundaries. A chain with Existing Video Context also streams its saved
prelude into the same movie.

`decode_buffer=disk-backed` is the recommended default. MiniMax H3's VAE keeps
its native temporal chunking but writes the decoded float scene into a
temporary mmap. The adapter converts and encodes one frame at a time, retaining
only the next boundary window in normal RAM, then deletes that scene buffer.
Peak ordinary RAM is therefore bounded by latent/model overhead and the blend
window instead of the decoded duration of the full chain. `memory` is a
compatibility fallback that holds one decoded scene at a time; neither mode
holds the whole production.

The continuous source is content-addressed and reused at:

```text
output/h3_chains/<run_name>/upscaled/seedvr2/source/<cache_key>.mkv
```

The cache key includes the immutable checkpoint lineage, VAE implementation,
blend schedule, prelude, and selected audio policy. Disable `reuse_cache` to
force a fresh VAE decode. `audio_source=plan` recovers the saved final-audio
policy and source audio directly from the manifest. Both an explicit Source
Timeline and a legacy AUDIO connected once at Loop Start are now materialized
as path-backed run state. Only manifests created before that legacy promotion,
and therefore lacking a recovery descriptor, need the optional `source_audio`
socket.

Use the audio-preserving **SeedVR2 Direct Video Upscaler** from
[ethanfel/ComfyUI-SeedVR2_VideoUpscaler](https://github.com/ethanfel/ComfyUI-SeedVR2_VideoUpscaler).
It reads the adapter movie in `chunk_size` batches, returns one file-backed
H.264 VIDEO, and embeds the source audio in that VIDEO so it can connect
straight to core Save Video. Its separate AUDIO output remains available for
alternate muxing graphs.

### Deferred H3 upscale child runs

Select the generated branch heading you want in Checkpoint Manager and click
**Use branch locally** to keep that source fixed while experimenting,
then connect its **selected_manifest** output to **MiniMax H3 Checkpoint Upscale
Adapter**. The manager verifies the immutable lineage and embeds recovery-only
compatibility and Source Timeline metadata directly. No source Plan, Chain
Policy, or decoded source media is retained by the recursive upscale graph.

Use a separate adapter **profile** name for each upscale experiment. Results
live under `output/h3_chains/<run>/upscaled/<profile>/`; a local source pin is
not a separate output folder and does not isolate experiments sharing a profile.

To upscale or export just one chapter, set the manager's output scope to
**Selected chapter only**, select that chapter's branch heading, and click
**Use branch locally**. Changing the scope of an existing local pin keeps its
pinned tip; browsing another take does not move it. Earlier chapters may have
different resolutions: only the selected chapter's media and compatibility are
validated. Earlier pinned revision metadata is still read to preserve exact
source-audio and editorial timing. Selecting output does not seal a chapter or
change project-wide active branches.

Chapter output keeps original scene numbers: a Chapter 2 containing saved
scenes 8–10 outputs scenes 8–10, even if scenes 11–12 are still planned.
On Upscale Adapter, `start_clip=1` starts at the first selected scene (8 here),
`end_clip=0` means the last selected scene (10), and `start_clip=9` resumes after
verifying scene 8's saved HQ output. No HQ prefix from Chapter 1 is required.
Reference schedules retain their original Plan scene numbering.

Extending the selected branch from scene 8 to scenes 8–10 does not invalidate
an unchanged scene 8 upscale. Resume validates every completed scene's source
revision and media hashes, RAW/delivered frame clock, saved prompt/sampling
settings, and upscale profile, plus its saved output artifacts. New saves also
record a per-scene source contract. Older saves use their existing source
provenance without rewriting the files. The whole-manifest hash remains
provenance, not a reason to reject an unchanged prefix when later clips are
added. Changed completed sources or profile settings still require a new pass.

Chapter upscale profiles and finals are isolated under
`output/h3_chains/<run>/chapters/<number>_<chapter_id>/upscaled/<profile>/`.
Connect the upscale manifest to **H3 Chain Assemble**; no Chapter Delivery
filter is needed after a chapter-only selection. Use Chapter Delivery separately
if you also want to seal an immutable original-quality chapter snapshot.

```text
Checkpoint Manager → Upscale Adapter → Upscale Current Scene
                                      → backend graph
                                      → Upscale Segment Save
                                      → Upscale Loop End → H3 Chain Assemble
```

**Upscale Current Scene** prefers the optional `denoised_output` saved by
Segment + Checkpoint and falls back to the terminal sampler latent in older
checkpoints. It exposes the joint H3 AV latent as well as separate video and
audio latents:

- Combined-style nodes can consume `source_latent` directly.
- Video-only LBH nodes consume `source_video_latent`. **MiniMax H3 Pass-2 AV
  Prepare** recombines their output with `source_audio_latent`, performs
  NestedTensor-safe CONST re-noise on video only, and locks the saved audio
  with a zero denoise mask.
- LTX 2.5 is a decoded-video V2V path, not an H3-latent path. Decode the H3
  source latent, run the LTX refinement/upscale graph, and send its raw frame
  batch to Upscale Segment Save.

For H3 pass-2 conditioning, **Upscale Reference Conditioning** first reads the
exact cache descriptor recorded on the selected source revision. Tagged and
Scheduled Ref2VA create that cache automatically: native H3 reference latents
remain in safetensors while compact Qwen presentation frames allow the saved
compiled prompt to be tokenized again. **H3 Conditioning Sync From Latents**
then compares the original scene video latent with the actual LBH output. It
applies the exact horizontal and vertical scale to `match` picture
`minimax_refs` and `minimax_keyframes`, while preserving `max` picture refs at
their Core H3 capped geometry. Pictures already rebuilt from a cache-v2 RGB
master at the pass-2 canvas are not scaled twice. It updates changed reference
H/W metadata and deliberately leaves text, temporal positions, and audio
conditioning untouched. A de-rope target may have a longer video time axis;
sync accepts that deliberate difference because it changes only spatial
reference geometry. Upscale Reference
Conditioning's default `exclude_video_keep_audio` policy removes both the Qwen
motion-video presentation and native motion-video latent because the pass-2
source latent already contains the generated motion; audio paired with a video
reference is converted to an audio-only reference. `keep_video_native` and
`resize_video` remain available for comparison, and the sync node follows the
selected conditioning policy automatically. Build sampler 2's new
Guider from the returned conditioning rather than reusing the original Guider.
Thus the child graph needs no reference registry or original picture/video/audio
connections. Both cache versions retain the encoded native reference blocks
used by sync; cache v2 additionally keeps original picture masters for
workflows that choose target-resolution VAE re-encoding instead.

The bundled **Deferred Upscale + De-Rope - H3 LBH 3D** workflow wraps
[ComfyUI-MAINodes](https://github.com/matlowai/ComfyUI-MAINodes)' stable
decoded time-smear recipe in the child loop:

```text
source x0 → H3 Jerk Oracle → Chain De-Rope Guard → H3 Time Smear
          → H3 video VAE encode → LBH 3D → Chain De-Rope Continuity
          → H3 V2V Init + Inject Schedule → sampler
          → Exact/Audio Recover → re-encode → Chain Recovered AV
```

Guard forces the parent scene's repeated prefix to rate 1 and protects the
last 17 frames when another selected scene follows. It enables Time Smear's
`expand_to_end` only at the branch tip. Freeze Mask consumes Time Smear's final
`hold_map_used` (including legal-grid padding) and freezes the protected prefix
inside H3 V2V Init. For Drift-Control scene 2+, Continuity then replaces that
target-resolution prefix with the prior HQ tail before sampling.

Audio uses the identical hold map. Decode the source RAW audio latent, run H3
Audio Smear, VAE-encode it into H3 V2V Init, and recover the pass-2 audio after
sampling. The workflow seeds audio at strength 0.5 to keep dialogue timing
aligned but uses MAINodes' safe final preset that retains the original
performance. Segment Save's optional `recovered_audio` input trims the same
repeated RAW prefix as video and records the recovered result. Chain Recovered
AV rejects a still-dilated latent and packs the re-encoded world-clock video
and audio for optional full-latent saving and compact Drift-Control resume.

This is the stable pixel-smear route, not MAINodes' experimental temporal
latent insertion. The expanded IMAGE batch stays on CPU, but high-motion
scenes can still become two to three times longer internally. Narrow the
adapter's scene range or reduce oracle aggressiveness when RAM or wall time is
too high. Spatial upscale and de-rope remain in the same regeneration pass so
a later independent upscale cannot undo the recovered motion timing.

#### Saving DeRoPE for a later deferred pass

The current combined example already wires recovered frames through VAE Encode
and **Chain Recovered AV**, and sends that recovered latent to Upscale Segment
Save and Loop End. The example now has **save_latent ON**, so new renders retain
the complete recovered latent for a later pass. Existing workflows keep their
stored setting; preview-only results do not gain a latent by opening the tab.

For a separate later latent-upscale pass, a DeRoPE save needs:

- `save_latent` enabled **before rendering**, with Recovered AV connected to
  Segment Save's `upscaled_latent`. Enabling it changes the profile configuration;
  use a new profile instead of resuming a prefix saved with the old setting.
- A full video latent re-encoded **after Exact Recover** on the original RAW
  clock, including the continuation head (the saver trims delivered pixels
  separately). Recovered AV checks the H3 temporal length; a stretched pass-2
  intermediate is not a recovered output.
- Aligned recovered audio where appropriate. Joint AV saves reuse recovered
  audio latents and the saved delivered waveform. Video-only saves that preserve
  the original performance reuse the exact original take's audio latent, loading
  only audio from that checkpoint. If recovered audio replaces the performance,
  connect its re-encoded `audio_latent` to Recovered AV before saving.
- Source revision/hash and reference-cache provenance retained through each
  stage. Saved child tensors use `upscaled_video`/`upscaled_audio` or
  `upscaled_samples`; Current Scene translates these into its source AV streams.
  Reference-cache lookup retains the original generation fingerprint and canvas,
  then rebuilds conditioning for the processing canvas. New upscale results
  remain linked through their DeRoPE parent to the original checkpoint.

Select a **different output profile** for the later pass. The original generation
and DeRoPE source files are not overwritten. Full latent headers and checksums
are checked at execution; a continuation tail or time-stretched intermediate is
not a valid recovered source.

When pass 2 should use a different reference set, insert **Upscale Reference +
Prompt Override** on the normal `H3_TAGGED_REFERENCES` line and connect its
`references` and `prompt_override` outputs to **Upscale Reference
Conditioning**. An explicit registry replaces, rather than ambiguously merging
with, the compiled cache. The node can remove comma-separated tags without
renumbering references by hand; the conditioning node recompiles the resulting
prompt, Qwen presentation, and native blocks together. Leave its reference
input unconnected to retain the automatic cache route. Connected picture/video
refs require the original H3 video VAE, while activated standalone or paired
audio additionally requires the H3 audio VAE. No Plan or Source Timeline is
introduced; sequential/source-timeline live refs should be converted to a
scene-local restart/standalone reference before use in deferred upscale.

Upscale Reference Conditioning encodes the exact saved compiled prompt by
default. Its optional `prompt_override` is encoded instead when supplied, so a
user can provide a short appearance/detail-only pass-2 prompt without repeating
the source scene's motion or camera instructions. Automatic natural-language
motion stripping is intentionally avoided because it cannot reliably separate
action from identity, framing, or continuity clauses. The bundled LBH workflow
therefore leaves the override blank and uses the original compiled prompt by
default. Its How To Run note retains a neutral preservation/detail replacement
prompt for an explicit copy/paste A/B test.
Missing reference caches are rebuilt automatically before applying
`missing_cache`, including in Pixel Conditioning and the CAT video wrapper.
Recovery uses the selected generation take's saved reference lineage (also
through DeRoPE), not the current Plan or current tag assignments. It verifies
archived/input project media against saved content hashes, decodes only active
references, and re-encodes them with the connected H3 VAEs. Native pictures,
video/audio references, and Qwen-only semantic anchors retain their separate
roles. No diffusion sampling or source-scene regeneration is needed.

Recovered caches use the existing deduplicated V3 tensor store and a run-local
`reference_cache/rebuilt_<identity>.json` index. Subsequent upscales reuse them;
original checkpoint metadata, source media and legacy bundles are not rewritten
or deleted. An absent cache bundle/object is recoverable; an existing corrupt
payload still fails integrity validation.

Recovery keeps presentation settings from an exact surviving cache manifest or
immutable saved recipe/reference lineage. Older takes without those settings
use `match`, semantic size `512`, and `timestamped_video`; the status explicitly
lists these defaults. Sequential/source-timeline references additionally need
their immutable saved Plan timing. Recovery never executes archived workflows.

Connect `video_vae` for native visual reference rebuilds and `audio_vae` for
native audio. If an original file, saved reference identity, or required VAE is
unavailable, `error` names the recovery requirement; `text_only` explicitly
falls back without reference conditioning. Connected Tagged references remain
an intentional override and bypass automatic recovery.

Segment Save adopts each verified cache object into
`output/h3_chains/<run_name>/reference_cache/` and records only that run-local
descriptor. Copying or backing up the parent run therefore preserves everything
required to rebuild pass-2 Ref2VA conditioning.

New V3 scene caches are small JSON manifests pointing into
`reference_cache/objects/<tensor-content-hash>.safetensors`. Identical reference
originals, previews, and encoded latents are stored once across scenes/revisions
within the project. Shared staging objects are hard-linked into the project
where supported, otherwise copied once. V1/V2 scene-sized bundles remain readable
and unchanged until explicitly converted. Updating the nodes does not compact
or delete existing bundles. The [reference-cache converter](SCHEDULED_REFERENCES.md#converting-existing-bundles)
can split them losslessly, then retire each old bundle after a verified render
using the conversion successfully commits through an H3 saver. Small legacy JSON
addresses and conversion/retirement receipts remain for checkpoint compatibility.
Objects must not be deleted individually while any scene still references them.

Legacy checkpoints that still point into `output/h3_reference_cache/` migrate
without a rerender. Selecting their complete branch in Checkpoint Manager
hard-links the verified cache into the corresponding run (or copies it when a
hard link is unavailable) and returns a run-local descriptor. Migration never
deletes the global object or rewrites immutable revision metadata. Later branch
loads resolve the verified run-local equivalent first, so the old staging copy
can be archived or removed after a successful selection and upscale check.
Read-only workflow-local/chapter-only selections do not perform this migration;
checkpoints without an exact cache descriptor still use shared-cache discovery.

Send the backend's decoded **raw** frame batch to both Segment Save and Loop
End. They remove the parent scene's repeated context head exactly once, persist
the delivered HQ segment, and carry optional HQ context to the next iteration.
Set a distinct `profile` for each recipe so settings and outputs cannot collide.
The profile folder is:

```text
output/h3_chains/<run_name>/upscaled/<profile>/
├── segments/
├── checkpoints/
├── prompts/
├── audio/
├── partial/
├── upscale_manifest.json
└── final/
```

`save_latent` defaults off. Segment Save still writes a small verified
safetensors checkpoint containing the assembly audio, so the child run remains
resumable and mergeable without duplicating the much larger HQ sampler latent.
When the following source scene uses Drift-Control AV, that checkpoint also
contains only the preceding scene's 12-step HQ video tail. Pass-2 AV Prepare
splices this tail into scene 2+'s prefix, gives it zero added noise and a zero
denoise mask, and refines only the new video region. This compact context is
enough for exact interruption-safe HQ continuation without enabling full
latent saving. Older child checkpoints without the tail safely protect their
independently upscaled source prefix and report that fallback in node status.
Enable full latent saving only when you want to reopen/refine the HQ latent
itself. The transient `upscaled_latent` connection on Loop End remains usable
for scene continuity whether or not persistence is enabled.

For new parent renders, connect SamplerCustomAdvanced `denoised_output` to
Segment + Checkpoint's optional `denoised_latent` input. Existing checkpoints
remain valid and use their terminal sampler output. Keep the parent branch
until every selected child scene has been persisted; a completed child profile
contains its own HQ video segments and audio needed by H3 Chain Assemble.
Assemble recognizes the upscale manifest, reconstructs a recoverable Source
Timeline from the embedded parent manifest, and keeps the canonical HQ result
under `upscaled/<profile>/final/`. Its normal `copy_to_output` and
`output_subfolder` controls can additionally publish the MP4 in ComfyUI's
regular output tree. Legacy source-track runs without the embedded descriptor
still need their original full AUDIO connected to Assemble.

## Run Manager

Connect the active Plan output to **MiniMax H3 Run Manager**. It discovers runs
under the ComfyUI host's `output/h3_chains`, including remote Docker hosts.
Select a run and choose **Load selected archive into Plan**; after confirmation
it restores archived prompts and Plan controls without changing graph links.

The two names at the top are deliberately separate:

- **Active Plan** is the connected Plan's current `run_name`. Generation and
  **Save assets to active Plan** use this name.
- **Selected archive** is only the folder highlighted in the browser. Selecting
  it does not change the Plan. **Load selected archive into Plan** is the only
  action that applies its archived prompts and settings.

When both names match, the archive is marked **ACTIVE PLAN**. When they differ,
the selected archive is labeled **not loaded**, so opening an old folder or
inspecting it cannot be mistaken for switching the generation run.

Restore prefers:

1. `api_prompt.json`;
2. `workflow.json`;
3. effective settings derived from `plan.json` for older runs.

The fallback retains exact scene lengths, steps, and seeds even when an old run
did not archive unused default-widget values.

## Archive reference assets

Connect loader outputs to Run Manager's dynamic **Connect loader asset** socket,
up to 12 assets. Classify each as Picture, Video, Audio reference, or Source
track so a short voice reference cannot be confused with a project soundtrack.

Wire all Semantic Picture Anchor nodes into one Semantic Anchor Bundle, then
connect the Bundle's **references** output to Run Manager's
**tagged_references** socket. The same line can feed Tagged Ref2VA and Plan
Studio. The interface discovers the Bundle's upstream image loaders and
archives/restores them as individual picture assets without consuming the 12
direct loader sockets.

- Archive images and audio default on.
- Archive video defaults off because video references can be large.
- Only files inside ComfyUI's input directory are eligible for fallback copies.
- Content-addressing deduplicates unchanged media and retains changed versions.

Restore first uses the original input-relative path. If it is missing and a
fallback exists, Run Manager copies the archived asset into a unique ComfyUI
input filename and updates a compatible loader. Targets are matched by persistent
binding identity, archived node ID/type, then unambiguous compatible loaders.
Ambiguous targets remain unchanged and are reported.

## Run folder contents

```text
output/h3_chains/<run_name>/
├── plan.json
├── workflow.json
├── api_prompt.json
├── manifest.json
├── prompt_history/<scene_id>/
├── segments/clip_0001.<revision>.mp4
├── segments/clip_0001.<revision>.prompt.txt
├── checkpoints/clip_0001.json
├── checkpoints/clip_0001.<revision>.json
├── checkpoints/clip_0001.<revision>.safetensors
├── generated_audio/
├── reference_cache/
│   ├── scene_0001.<scene-contract>.json
│   └── objects/<tensor-content-hash>.safetensors
├── chapters/<number>_<chapter_id>/
│   ├── manifests/<snapshot_id>.json
│   ├── final/
│   ├── frames/
│   └── upscaled/<profile>/  (including seedvr2/source/)
├── upscaled/<profile>/
└── final/<filename>.mp4
```

Regenerating a scene updates its active checkpoint pointer but retains all
earlier MP4s, prompt sidecars, metadata, safetensors, and generated WAVs. Each
revision records what it supersedes.

Workflow and API graph metadata are embedded in segment/final files using
ComfyUI's standard tags. `workflow.json` is the preferred file to drag back
into ComfyUI; `plan.json` remains the authoritative effective render record.
Keep run folders private when workflows contain credentials.

## Assembly

Assemble accepts completed or partial manifests, including an interrupted
prefix reconstructed by Manifest Load. Its filename supports date
tokens such as `%date:yyyy-MM-dd%`, `%year%`, `%month%`, `%day%`, `%hour%`,
`%minute%`, and `%second%`. Existing files are never overwritten; numbered
suffixes are added automatically.

When Assemble receives a Chapter Delivery or Chapter Recovery Load manifest,
it writes under that chapter's `final/` folder and includes only the selected
scene range. Source audio and timed subtitles are shifted to the chapter's
original timeline origin. Other chapter finals and the whole-Run `final/`
remain untouched.

### Recovery blend schedules

`blend_schedule` can override the Plan's global visual blend only during
assembly. `plan` preserves the recorded setting. A comma-separated schedule is
applied to scene boundaries in timeline order: `5,30` uses five frames for the
first join and thirty for every later join because the last value repeats.
`0` produces hard cuts. This does not change checkpoints, prompts, seeds, or
generated frames.

When the requested boundary fits inside the saved blend MP4, assembly reuses
that artifact directly. If it requests more overlap than Segment Save retained,
connect the original MiniMax H3 video VAE to `blend_video_vae`. Recovery then
re-decodes the existing safetensors checkpoint into a temporary lossless RGB
video and deletes it after assembly. Diffusion is never rerun. The final video
still receives the one H.264 encode required by any pixel-space crossfade.

Each scheduled value must not exceed that incoming scene's repeated context.
For example, a chain whose first join has five context frames and later joins
have thirty-nine can use `5,30`; requesting thirty at the first join is rejected.

### Optional scene-one color stabilization

Set Assemble's `color_stabilization` to `scene_1_anchor` to counter gradual
exposure or saturation drift in a completed chain. Assembly measures a
center-weighted sample of the first generated scene, then fits only a weak,
bounded luma/saturation correction for each later scene. A correction is capped
at six code values of luma and six percent of saturation, at half the measured
strength.

The exact join inherits the preceding scene's accepted correction. Starting
after the retained overlap, that correction moves smoothly to the next scene's
target over 72 frames. Consequently the option cannot introduce a new grade
step at the boundary and does not change motion, timing, or audio. A prelude is
not used as the reference: the first generated scene remains the anchor.

This is an assembly grade, not diffusion conditioning. It does not change
checkpoints or future continuation input. It is disabled by default. Enabling
it uses the same single pixel-processing encode as a visual blend; on a hard-cut
assembly it replaces the otherwise lossless stream copy with one encode.

For an experimental correction that can influence later generation instead of
only the final MP4, choose **Color-Stable Drift AV** through Advanced Policy or
on a scene's incoming transition. It applies a bounded scene-one correction to
the disposable copied video latent as a VAE delta, tapering from zero to full
strength across the 39-frame context. Scene 1 remains the anchor, scene 2 is
neutral, and scene 3 onward can receive a corrected predecessor tail. It does
not alter the saved predecessor checkpoint or audio. Because this changes
generation conditioning, it is recorded in the incoming-boundary dependency
and is intentionally separate from this assembly-only option.

Enable `copy_to_output` to keep the canonical final in the run folder and also
publish an MP4 into the regular ComfyUI output tree. `output_subfolder` is
relative to that output root, supports nested folders and the same date tokens,
and may be empty to place the copy directly in `output/`. The existing
`filename` value is used for both copies, and collisions are versioned.

## Re-decode checkpoints to PNG and WAV

Connect a manifest to **Export PNG Sequence + Audio**, then connect the original
H3 video VAE, audio VAE, or both. Video-only writes PNGs, audio-only writes one
continuous `audio.wav`, and both inputs produce a synchronized image sequence
and soundtrack in the same folder. The WAV is decoded from the checkpointed H3
audio latents, preserves the generated AV boundary ownership, and follows the
same selected scene order and latent-safe trims as the gap-free PNG sequence.

The node verifies each safetensors checkpoint, decodes one scene at a time,
removes repeated overlap, converts small frame chunks to 8-bit RGB in one
operation, and writes each chunk through bounded parallel atomic PNG workers.
ComfyUI's progress bar covers verification, GPU decode, and saving. The server
log reports verify, checkpoint-load, decode, conversion, and save timings. The
deliverables and `export.json` are written under:

```text
output/h3_chains/<run_name>/frames/<export_name>/
```

For a Chapter Delivery manifest the equivalent path is
`chapters/<number>_<chapter_id>/frames/<export_name>/`; `export.json` records
the chapter number and immutable snapshot id.

Chapter exports default to `reuse_existing = true`. A later export using the
same export name looks for a successful matching export and reuses its unchanged
PNG prefix. New scenes continue the existing frame numbering in that folder,
without decoding or rewriting the earlier PNGs. An identical export skips both
video and audio decoding. When new scenes extend the chapter, the synchronized
`audio.wav` is rebuilt and atomically replaced so audio joins remain correct;
WAV bytes are not blindly appended.

Changed checkpoint revisions, trims, placements, frame numbering, or relevant
export settings create a new numbered folder instead of mixing old and new
output. A shorter/older chapter snapshot never truncates a longer export.
Missing, modified, or untracked PNGs, interrupted exports, and legacy exports
without the new verification records also use a fresh folder; their existing
files are left untouched. `cached` checks recorded size and modification time;
`strict` additionally re-hashes the reusable PNGs and WAV. Concurrent appenders
to the same chapter/export name are rejected while the first export is running.

Set `reuse_existing = false` to force a fresh folder, especially after changing
VAE weights, precision, ComfyUI version, or decode settings. The reuse signature
identifies the VAE class, not the full weights. Whole-Run exports retain their
existing fresh-folder behaviour regardless of this switch.

Reused PNGs retain their original embedded workflow/manifest metadata.
`export.json` is the current export index and points to the latest selected
immutable chapter snapshot; earlier indexes remain in `export.history/`.
`complete` means that this export succeeded; `chapter_complete` separately
indicates whether all planned chapter scenes have been generated. The node's
frame count includes reused frames, and its status reports reused/new counts.
MP4 assembly still creates a new assembled movie; incremental PNG reuse does
not imply in-place MP4 appending.

PNG compression is lossless. Use the same VAE, ComfyUI version, precision, and
decode settings for the closest reconstruction. The checkpointed latent is
exact, but a new VAE decode is not guaranteed to be bit-identical to an older
decode made under different settings. New nodes use compression level 1 and
`save_workers = 0`, which automatically selects at most eight workers. Set the
worker count to 1 for serial saving or raise it explicitly only when the output
storage can sustain more concurrent writes.

`checkpoint_verification = cached` still performs a complete SHA-256 check the
first time. Later exports skip re-hashing only while the checkpoint's recorded
hash, byte size, and nanosecond modification time all match. Select `strict` to
re-hash every checkpoint on every export. During execution,
`export.partial.json` is updated after every converted chunk and retained if the
export fails or is interrupted; a successful export replaces it with
`export.json`, including the effective settings and per-scene timings.
