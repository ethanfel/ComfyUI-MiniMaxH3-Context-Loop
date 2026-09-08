# Changelog

Newest first. This file keeps release history out of the onboarding README.

## Unreleased — Deferred checkpoint upscaling

- Harden the native-first PR #15988 mask conversion guard: recognize real
  assignments and transparent wrappers, ignore comment/docstring examples,
  and correct only missing video/audio streams on partially patched cores.
  CPU regressions cover audio carry, binary/fractional masks, idempotence,
  source preservation, and upstream in-place multiplication. The proposed
  shared model_base follow-up still needs review once its API exists.
- Added semantic-anchor size and presentation-mode overrides to latent, pixel,
  and inherited VIDEO upscale conditioning, matching Tagged Scene Options plus
  `inherit`. Cached, rebuilt, and connected references honor the settings;
  changed anchors rebuild presentation and prompt labels into a separate cache
  without changing source checkpoints. Existing widget positions are preserved.
- Checkpoint Manager's output summary now names the saved branch, available
  scenes, Original/DeRoPE source and exact Original fallback scenes, plus whether
  the source is pinned to this workflow. Preview browsing does not change output;
  the downstream range still determines what gets processed. Added regression
  tests for both the anchor overrides and source-summary behavior.
- Recover saved Match/Max sizing through static input links and modern Tagged
  Scene Options. Upscale sizing overrides now apply to cached and reconstructed
  references too. Match-to-Max re-encodes original picture masters (recovering
  them from saved media for legacy caches); source caches and unrelated reference
  roles stay unchanged. Added CPU policy/geometry regressions and accurate logs.
- DeRoPE/upscale saves retain media after uncertain checkpoint publication;
  failed manifest refreshes no longer delete saved scenes. VIDEO PNG exports
  recover journaled frame/index publication after cancellation or process exit,
  and accept recreated containers only when delivered pixels match. Earlier
  scenes and conflicting interrupted bytes are preserved. Added CPU failure
  and process-exit tests; see [cancellation and resume](docs/processing-resume.md).
- Added grouped full-mix/vocal/instrumental sources in the Carousel and a
  reusable Audio Tracks node. Vocals drive source-locked generation; delivery
  keeps the full mix, or auto-mixes stems if none is supplied. Audio archives
  preserve each track. Per-scene Lip-sync Inherit/On/Off retains existing
  single-track behavior and uses the established audio policy fields.
- Added nightly chapter resolution controls to Plan Studio: inherit the Plan
  default or set chapter-local width/height. Existing locked saved scenes pin
  their chapter's original geometry, including legacy checkpoints, without
  bypassing generation-identity or artifact-integrity checks. Conflicting
  explicit sizes and late lock changes stop before checkpoint replacement.
- Current Shot, conditioning checks, reference caches, and chapter recovery
  now use the resolved scene size. Chapter exports retain their saved size;
  mixed-size whole-run assembly and native AV/latent cross-size continuation
  are rejected. Automatic review previews use the current chapter when sizes
  differ. No existing media is resized or rewritten.

- Fixed Plan Studio dropping and disabling Source Timeline audio after scene
  frame-count edits. The source track stays available as scene windows resize,
  with updated waveform coverage and seek positions; the underlying audio and
  generation settings remain unchanged. Waveforms no longer stretch visually
  across silent space beyond the source end.

- Chapter Delivery now exports already-generated scenes of unfinished chapters
  instead of requiring their planned final scene. Immutable recovery snapshots
  distinguish the available scene range from the planned chapter end.
- Chapter PNG/WAV exports can reuse verified unchanged PNGs and append newly
  generated scenes in the same folder. Identical exports skip VAE decoding;
  growing soundtracks are rebuilt atomically to preserve audio joins. Changed
  takes, trims, placements, settings, damaged/missing files, and interrupted or
  unverifiable legacy exports use a new folder without replacing prior frames.
  A fresh-export switch, per-export process lock, file integrity records, and
  export-index history preserve explicit recovery and whole-Run behaviour.

- Fixed Load Manifest and checkpoint recovery rejecting legacy shared archive
  references as invalid immutable revision IDs. Older Runs now use their
  existing root-archive fallback; newer per-revision snapshots retain strict
  revision, location, and file-existence validation.

- Fixed Checkpoint Manager failing to list older Runs whose checkpoints refer
  to shared Run-level Plan, workflow, and API-prompt snapshots. These legacy
  references are readable again and remain permanently excluded from
  individual checkpoint deletion; revision-specific recovery archives keep
  their existing ownership checks.

- Fixed Windows mapped network drives used as ComfyUI input/output directories
  (#45). Root and artifact paths now resolve consistently before calculating
  relative paths, preventing drive-letter/UNC mount errors during segment and
  recovery-archive saving, Run Manager access, and Asset Carousel imports.

- Fixed Checkpoint Manager rendering blank after a nightly update reused an
  older browser-cached helper module. Restored monotonic cache keys across the
  manager, Plan Studio, prompt editors, Run Manager, and Asset Carousel, with a
  regression test that rejects future cache-key rollbacks.

- Added immutable per-chapter delivery and recovery. **Chapter Delivery** uses
  Plan Studio's existing chapter boundaries, can auto-select the chapter that
  just finished or explicitly select any numbered completed chapter, and
  routes MP4, PNG/WAV, and full-chain SeedVR2 preparation into isolated chapter
  folders. **Chapter Recovery Load** restores the newest or an exact older
  content-addressed chapter snapshot without loading later chapters. Disabling
  Chapter Delivery preserves the existing whole-Run final path.

- Fixed Prompt Editor reference discovery in Studio workflows whose render
  engine lives inside a subgraph. Scene Prompt Editor, Rich Prompt Editor,
  Plan Editor, and Plan Studio can now read the connected upstream Project
  Asset Carousel directly, so its image, video, motion, and audio tags remain
  visible even when no downstream Ref2VA wrapper is traversable.

- Added workflow ownership for Carousel-backed projects. The first workflow
  that opens a Run becomes its writer and maintains a liveness heartbeat;
  other tabs and computers connected to that server remain read-only until the
  owner releases it or **Force ownership** explicitly fences the previous
  workflow. Backend checks protect asset edits, prompt history, editorial
  state, checkpoint/review operations, generation start, and checkpoint
  publication. Ownership proofs are session-only and are scrubbed from saved
  Plan, API-prompt, workflow, and deferred-review recovery archives. Read-only
  workflows can still inspect, preview, export, assemble, and fork the project
  into a new Run.

- Added **MiniMax H3 Pending Review**, an optional one-wire Review Gate mode.
  It generates the complete candidate batch, stores a durable pending-review
  record beside the existing immutable checkpoints, and stops cleanly instead
  of keeping a ComfyUI execution waiting. Review Gate can later filter the
  connected run's pending batches, preview every saved take, activate the exact
  selected lineage, retain checked alternatives, prune unkept candidates, and
  arm Loop Start at the following scene. An unconnected Gate behaves exactly as
  before.

- Fixed Project Asset Carousel Run-name editing so pauses after a space,
  underscore, or hyphen no longer let an asynchronous catalog refresh erase
  the separator. The field now preserves the exact draft while typing and
  commits its folder-safe project name on Enter or blur.

- Fixed Checkpoint Manager hiding the safe rollback operation for an earlier
  checkpoint that was already on the active branch. It now offers **Roll
  active branch back**, retires later active pointers in one operation, and
  keeps all immutable revisions. The deletion inspector now distinguishes
  this non-destructive rollback from leaf-by-leaf permanent deletion.

- Hardened Scene Prompt Editor and Rich Scene Prompt Editor workflow teardown.
  Closing a workflow now cancels pending Plan propagation, prompt analysis,
  history saves, timers, and listeners without repainting or mutating a graph
  while ComfyUI is removing it. Cleanup failures are isolated so one editor
  cannot prevent the remaining workflow nodes from closing.

- Replaced the Project Asset Carousel's filtered browser suggestion popup with
  a real project picker attached to the editable Run name field. The arrow now
  always lists every existing project with its asset and unassigned counts,
  while the text field still accepts a new Run name.

- Fixed Review Gate delivery when Project Assets owns the Plan run name. The
  hidden, stale Plan widget no longer rejects the exact pending review or
  participates in run-name fallback matching. Report and original fix by
  **Psylent_Gamer (4090)** via Banodoco, adapted to the current Plan/Modern Plan
  routing implementation.

- Fixed path-backed video and motion references in `restart_each_scene` mode
  being cropped or padded to the generated scene length. They now retain
  their own complete 24 fps reference duration; only `sequential` references
  use scene-sized timeline windows.

- Extended **Export PNG Sequence** into an independent picture/audio exporter.
  Its video and audio VAE sockets are now both optional: connect either one for
  PNG-only or WAV-only output, or both for a continuous PNG sequence plus a
  synchronized `audio.wav`. The WAV is decoded from the saved H3 audio latents,
  preserves AV overlap ownership, and follows the same selected scene order and
  latent-safe trims as the exported frames. Existing video-VAE connections and
  output indexes remain valid; `audio_path` is appended as a new output.

- Accelerated **Export PNG Sequence** with chunked batch conversion and up to
  eight bounded parallel atomic PNG writers, inspired by JSON-Dynamic's Fast
  Absolute Saver. New nodes default to lossless compression level 1; existing
  saved compression values remain unchanged. The exporter now shows ComfyUI
  progress, logs per-scene verify/load/GPU-decode/convert/save timings, updates
  `export.partial.json` after every chunk, honors cancellation between chunks,
  and caches successful checkpoint hashes by immutable hash, size, and mtime.
  Strict verification remains available as an optional input.

- Corrected the user-visible **Contex Loop** typo to **Context Loop** across
  node titles, Add Node categories, settings, documentation, branding, and
  bundled workflow titles. Serialized node IDs, settings keys, and the public
  repository slug remain unchanged for compatibility.

- Fixed Review candidate acceptance and rerolls leaving Plan Studio and
  prompt-editor companions on stale scene metadata. Selecting a 10x candidate
  now refreshes the complete Plan—including seed, length, steps, context, and
  scene order—while prompt-only edits retain their selection- and undo-safe
  fast path. The shared companion module now has an explicit cache revision,
  with safe fallbacks for partially refreshed browser sessions.

- Added the nightly **Picture Context Builder** to Plan Studio. Scene Context
  now owns picture/audio totals, boundary implementation, and spatial proxy;
  selecting 1–N picture blocks exposes every H3-native repartition through
  ordered boundary selectors plus an independent earlier-scene source and
  latent window for every block. Repeated sources are supported, audio stays
  locked to its continuous predecessor path by default, and checkpoint,
  resume, trim, review, and dependency metadata track every selected block.
  Released one/two-block Plan fields remain a read-only compatibility path.

- Added **MiniMax H3 Plan (Modern)** as a clean, opt-in replacement for the
  original Plan node. It preserves the familiar vertical scene-column editor
  and exact Plan output sockets, but organizes its controls into Project,
  Canvas, Generation defaults, and Delivery while requiring Generation Profile
  and exposing no legacy context/audio/continuation fallbacks. The original
  Plan remains unchanged; its **Upgrade to Modern Plan…** action preserves
  scenes, supported settings, placement, and graph connections. Plan Studio,
  prompt editors, Review, reroll, Run/Checkpoint Managers, project assets, and
  recovery archives recognize both Plan types.

- Fixed zero-picture-context resume validation. When a visually new scene
  carries only the preceding generated audio, strict history checks now ignore
  that predecessor's unrelated incoming visual-boundary recipe while retaining
  prompt/model/source identity and artifact-integrity checks. Resume errors now
  report the consumed streams and selected scene's effective video/audio
  context lengths.

- Hardened the Manager-facing HTTP surface. Direct prompt optimization now
  uses a server-owned exact-origin allow-list and validates every redirect;
  custom or local providers require `H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS`.
  Carousel imports can read only enumerated ComfyUI input, project, or chain
  media; Plan Studio browser media is confined to configured ComfyUI roots
  with real-path/symlink checks; and host folder opening is limited to
  loopback clients.

- Composed visual context now supports two independently positioned native
  latent windows from the same saved scene. A split such as 5+34 can reuse one
  source movie for both blocks, including on Scene 2, while generated-audio
  continuity remains attached to the immediate timeline predecessor.

- Completed the post-Issue-42 payload audit. Delegated Motion Context now
  verifies the live video/audio Guide merge before calling every upstream
  provider version, including older providers that do not fail preflight.
  Future and joint boundary anchors activate the payload-only compatibility
  gate without opting into normal context scheduling. The behavioral probe
  now uses real tiny tensors, and release cache, workflow, and regression
  fixtures are synchronized.

- Fixed Issue #42 on partially updated ComfyUI builds and internal Motion
  Context fallback paths. Chain now behaviorally verifies both video and
  audio keyframe/reference payload merging before sampling and installs its
  marker-gated compatibility merge only when native Guide layout exists but
  the payload still drops Guide tensors. This covers the reported 37-step
  stereo mismatch (`714` reserved rows versus `640` supplied rows), Patch
  Priority, longer/audio-only carry, and other internal-engine routes.

- Made prompt reference tags case-sensitive from display through conversion.
  The rich editor no longer presents a wrong-case alias as a valid asset, so
  `@Maison` and `@maison` remain distinct. Completion search is still
  case-insensitive and inserts the asset's canonical spelling.

- Fixed a Loop Start runtime crash introduced by structured source-duration
  diagnostics. Source Timeline binding now derives the active audio-source
  requirements before calculating required frames, matching the preflight
  path and eliminating the `source_requirements` NameError.

- Completed the structured preflight diagnostic contract. All hard preflight
  categories now carry their precise trigger, including scene ID, setting,
  value, origin, and tag where applicable; duration/capacity failures report
  required versus available frames, the last complete scene, and the first
  affected scene. Diagnostics retain the compatible primary `action` while
  adding ranked alternative solutions, and validation failures are separated
  by Plan, scene selection, source, reference, runtime, and resume stage rather
  than collapsing into one generic internal error. Source-duration validation
  now measures only through the last scene that actually consumes source audio
  unless the final soundtrack itself is sourced.

- Made blocking preflight errors identify their actionable origin. Missing
  source-audio diagnostics now list every triggering scene ID, the exact
  effective setting (`source_reference`, `source_audio_target`, or final
  output audio), and whether it came from a scene override or Chain Policy;
  they also name the disconnected Loop Start sockets. Other structured
  preflight errors now render their available scene, setting, origin, and tag
  context on separate readable lines before the corrective action.

- Fixed Issue #38's remaining Scene 2 failure on installations where
  ComfyUI retains a compatible H3 payload wrapper after its temporary module
  alias is removed. Patch Priority now recovers that wrapper's captured stock
  method from the function's own execution globals, allowing the guarded
  Guide-audio plus Ref2VA-audio merge to take ownership without requiring the
  user to remove a valid node pack. Unknown payload wrappers remain refused.

- Fixed a selected final-cut ALT incorrectly retargeting later queues back to
  its original scene. The server-side editorial record now owns whether an
  alternate generation draft is armed; the hidden Plan Studio widget may only
  update an already-armed matching draft and cannot resurrect one cleared by
  Review. Selecting Original/ALT explicitly disarms that scene's draft, and
  Plan Studio synchronizes the one-shot queue widget on every workflow load,
  including Plans without chapters.

- Fixed delayed and inconsistent workflow hydration. Project Asset Carousel
  now paints its serialized catalog immediately while refreshing in the
  background, and Plan Studio keeps a lightweight serialized presentation
  cache so saved thumbnails, placements, and latent-safe trims appear on its
  first render instead of snapping into place after the checkpoint scan.
  Player transport now advances at the trimmed endpoint instead of continuing
  through discarded media. Scene renaming is atomic, rejects duplicate IDs,
  and migrates chapters, visual-context links, placements, trims, locks, and
  alternate final-cut metadata so an ungenerated rename cannot hide a scene.

- Plan Studio can now create a prompt-word alternate for an already accepted
  scene. Queueing an enabled draft renders only that scene and saves an
  immutable `editorial_alternate` revision without changing the active
  generation checkpoint. Accepting it selects its picture for preview,
  assembly, PNG export, and whole-chain latent finishing, while later scenes
  continue to depend on the original take and final audio remains original.
  The timeline marks selected clips `ALT`; Checkpoint Manager nests alternates
  under their immutable base and distinguishes generation lineage from the
  final-cut choice. Selected base/alternate revisions are deletion-protected.

- Plan Studio can now shorten a rendered scene non-destructively at a
  latent-safe endpoint. Its right edge and Scene panel expose only cuts shared
  by H3's native video-latent cycle and the delivered 24 fps / 40 Hz audio
  clock; the original complete checkpoint and scene movie remain untouched.
  Continuation state, generated audio, standard assembly, PNG export, and the
  Full-Chain Latent Video adapter use the shortened prefix. If an earlier cut
  changes after dependent scenes were generated, those checkpoints are marked
  stale and resume requires regeneration from the first affected scene instead
  of silently mixing incompatible endpoints.

- Fixed scene-2 continuation with simultaneous Ref2VA/source audio. On a
  partially native ComfyUI runtime that drops Guide audio when reference audio
  is also present, Chain now activates its marker-gated payload merge and uses
  the retained internal Motion Context engine. The compatibility merge now
  preserves keyframe audio followed by reference audio, matching H3 layout
  order, instead of accidentally retaining only the reference-audio rows.

- Scene LoRA routing now grows with the workflow instead of exposing four
  fixed branches. A new scheduler shows one empty LoRA A socket; connecting it
  reveals B, then C, through Z. Plan Editor and Plan Studio offer only routes
  connected to their scheduler plus a scene's already-saved route. Existing
  A-D plans and scheduler links remain compatible, and every route stays lazy.

- Run name now suggests existing live Asset Carousel projects. Choosing a
  suggestion switches the Carousel and its connected Plan, while the same
  field still accepts a new Run name and remains the only serialized source of
  truth. Empty projects are included in the history; output-only H3 backups
  remain available through Import instead of appearing as switch targets.

- Project Asset Carousel can duplicate its complete asset project under a new
  Run name. The copy keeps asset order, folders, roles, tags, lyrics, options,
  reference slots, and independent project-owned media files plus their asset
  recovery mirror; generated clips, checkpoints, assembled renders, previews,
  and upload scratch files are deliberately excluded. Existing Run names are
  never overwritten, and the source Carousel remains selected.

- Made both scene prompt editors responsive while typing long prompts. Each
  keystroke still updates the connected Plan value immediately, but expensive
  Plan-card rebuilding, canvas redraw, companion broadcasting, and strict
  schema analysis are now coalesced after a short idle interval and flushed on
  blur, navigation, or removal. Consecutive keystrokes also avoid reparsing an
  unchanged full Plan JSON.

- Fixed Tagged Audio `source_timeline` identity validation for the shipped
  full-track workflow. Loop Start's route fingerprint and Tagged Audio's
  waveform-content fingerprint are now recognized as two identities of the
  same Source Timeline audio, while genuinely different tracks remain blocked.

- Plan Studio Player now uses its preloaded media as a real second playback
  buffer. At adjacent saved-scene boundaries the decoded video and separate
  generated-audio elements are promoted immediately, the previous elements
  become the following preload buffers, and the existing last-frame veil fades
  only while the promoted player starts. This removes the redundant same-URL
  reload pause without changing clips, editorial timing, or final assembly.

- Project Asset Carousel can now import from another Run's live Carousel, not
  only from recovery backups. The copy preserves the source role, tag,
  reference options, and audio lyrics, receives an independent project-owned
  media copy and catalog identity, and never modifies the source Run. Selecting
  an Unassigned card binds the imported media to that slot instead.

- Fixed Plan Studio transport synchronization. Ruler ticks, scrubbing, scene
  widths, and the red play line now use the same pixels-per-second scale, so
  extending the open timeline or changing zoom cannot skew the playhead.
  Generated-video and Source Track playback also refresh the visible clock and
  line every animation frame; the browser's coarse `timeupdate` event remains
  only as a fallback.

- Updated the retained internal Motion Context engine to current upstream
  Guide behavior: arbitrary off-grid visual lengths remain exact, head-mode
  Guides can be placed at an interior `target_start`, unrelated anchors are
  preserved outside that interval, and decoded source audio is end-aligned on
  an exact 40 Hz PCM grid before VAE encoding. Native H3 run lengths still use
  one efficient video Guide. Loop-only latent Guide, audio-only carry,
  longer-audio windows, future anchors, fractional audio correction, and the
  guarded legacy-core path remain available.

- Chain Context now transparently delegates compatible Guide scenes to
  seitanism's installed **H3 Motion Context** node. Existing workflows and
  sockets do not change. The adapter preserves Loop keyframe arbitration,
  visual-condition metadata, and exact latent-audio placement; Latent Guide,
  audio-only or longer-audio continuity, future anchors, and legacy ComfyUI
  remain on the internal compatibility engine until upstream exposes those
  capabilities.

- Fixed the bundled Tagged Source Audio workflow's `source_timeline` path.
  Tagged Audio now receives the same full Load Audio track as Source Timeline,
  derives its aligned current-scene slice internally, and returns the complete
  static picture-and-audio fingerprint to Plan. The example also serializes
  Tagged Ref2VA's current `native_ref2va` widget and `refmod_sources` output so
  its controls no longer appear shifted after node updates.

- Checkpoint Manager can now delete an entire selected
  `output/h3_chains/<run>` folder. It previews the file, folder, and byte
  impact, requires both an explicit warning confirmation and the exact Run
  name, then revalidates the folder snapshot immediately before deletion.
  Original assets under `input/h3_projects/<run>` are deliberately kept.

- Semantic pictures now accept bare `#tag` as an untimed Qwen-only visual;
  append `[time]` only when explicit approximate scene placement is wanted.
  Prompt completion, reference conversion, rich chips, preflight, native
  Ref2VA hybrid conditioning, and external RefMod hybrid conditioning share
  the same syntax. `@tag` remains the native Ref2VA/RefMod namespace, and
  neither `#` form creates a VAE reference.

- Added optional **Lip-Sync Options** for Generation Profile's exact-source
  audio mode. Scene audio can be encoded with discarded real-song pre-roll and
  lookahead instead of artificial hard boundaries. An aligned isolated vocal
  stem can drive a conservative 40 Hz voice gate so vocal regions remain exact
  while instrumental gaps receive a configurable amount of denoising. The
  options and stem fingerprint are stored in generation dependencies; leaving
  the node disconnected preserves the previous hard-cut/full-freeze result.

- Checkpoint Manager now adopts active checkpoints written before immutable
  revision sidecars were introduced. It recovers the original transaction id
  from the existing versioned MP4 and safetensors filenames, writes only the
  missing lightweight JSON revision records, and links the legacy linear
  lineage without copying or rewriting media, latents, or active pointers.

- Added **Generation Profile**, a two-control replacement for the cryptic Chain
  Policy switches. Clear scene-continuity and audio choices include Generate
  audio, fresh per-scene audio, source-guided generation, source soundtrack,
  silence, and exact source-audio lip-sync. The former node remains compatible
  as deprecated **Manual Chain Policy (Legacy)**.

- Plan Studio's horizontal timeline position is now inert during passive
  rerenders, workspace growth, and prompt-editor scene synchronization. It
  restores the exact pixel position, cancels stale layout callbacks, and only
  reveals a scene after an explicit timeline selection; automatic restoration
  can no longer recursively grow or throw the scrollbar to either end.

- Both prompt editors now discover tagged and Project Asset references through
  the compact **Current Tagged Ref2VA Scene** node, just as they already did
  through the original standalone Tagged Ref2VA wrapper.

- Added a modern **Current Tagged Ref2VA Scene** composite that replaces the
  visible Current Shot plus Tagged Ref2VA pair while reusing both established
  execution paths internally. Its public outputs are only state, conditioning,
  latent, and typed scene data. A separate versioned options node carries
  reference/semantic/backend settings, and **Scene Data Extract** selects former
  secondary outputs—including `refmod_sources`—with a concrete UI socket type.
  The new route deliberately rejects the legacy 0.4 state contract and exposes
  no legacy source-audio or blend socket; both original nodes remain unchanged.

- Tagged Ref2VA now has an opt-in `external_refmod` conditioning backend for
  testing ComfyUI-MiniMaxH3Mod without duplicating native references. It keeps
  scene-local `@tag` selection and video slicing, emits text-only H3
  conditioning/latent, and exposes the active pictures/videos as the upstream
  `H3_REF_LIST` contract through `refmod_sources`. Native Ref2VA remains the
  default. Active semantic `#anchors` now use a hybrid path: ordinary `@visuals`
  remain compressed RefMods, while an anchor-only Qwen presentation preserves
  each anchor's timestamp and storyboard mode without re-encoding all ordinary
  reference media. Active reference audio still fails explicitly because the
  external format is visual-only. External Extract/Apply settings remain
  outside Plan fingerprinting and native deferred-upscale caching.

- Plan Studio's generated track is now an editorial timeline rather than a
  delayed view of generation order. Absolute scene placements can move any
  scene—including the terminal scene—before or after other clips; collisions
  resolve as non-overlapping inserts, uncovered time stays black, and raw
  placement requests are no longer rewritten during layout. Playback, motion
  references, source-audio waveforms, generated scene audio, and final video
  assembly share the resolved editorial order. Generation lineage and saved
  checkpoints remain unchanged, while boundaries whose generation predecessor
  changed assemble as safe hard cuts.

- Audio assets in the Project Asset Carousel now open a lyrics workspace beside
  the audio player. Lyrics can be pasted or edited in place, are persisted in
  both the project catalog and its H3-chain backup, and remain presentation-only:
  they do not alter prompts, reference fingerprints, or generation behavior.

- Chapter sections in Checkpoint Manager's **All scenes** view can now be
  collapsed from their headers. Expanded/collapsed state is remembered per
  run and chapter in workflow presentation state, and collapsed chapters skip
  rendering their branch cards until reopened.

- Checkpoint Manager now treats editorial chapters as independent revision
  scopes. **All scenes** renders a separate branch graph per chapter, and
  making a branch active rewrites or retires pointers only inside that
  chapter. Earlier and later chapters retain their active takes; a selected
  manifest combines the selected chapter with the active takes from preceding
  chapters without requiring a structural parent edge across the chapter
  boundary. Explicit visual/audio context metadata remains intact.

- Carousel folders now behave like compact Discord folders. Each folder is a
  card in the asset strip with a four-item miniature and count; clicking
  expands or collapses its assets inline, and dropping an asset onto the card
  moves it into that folder. Expansion persists as workflow presentation state
  without affecting generation, prompts, or fingerprints.

- Project Asset previews now resolve correctly in both prompt editors. The
  Carousel media URL is retained as a preview record instead of being mistaken
  for a preview object, restoring hover players, inline picture thumbnails,
  reference-tray thumbnails, and the owning Carousel source label.

- The Project Asset Carousel is now a direct media drop target. Dropping one
  or several image, video, or audio files creates ordered project assets
  immediately. External file drops are isolated from internal card dragging,
  thumbnails no longer start native browser drags, and the compact drop cue
  leaves reorder targets visible. The
  source browser now defaults explicitly to ComfyUI input, so existing input
  files can be turned into assets through **Import** without uploading them
  again.

- Project Asset image variants now accept exact megapixel targets and presets
  without requiring a crop. Output aspect locking couples width and height in
  both directions, **Use full image** preserves the megapixel target, and a
  connected upscale model reports its exact input and final saved dimensions.
  Output snapping now offers Off/8/16/32/64 (default 8), preserves the locked
  aspect ratio instead of producing arbitrary odd sizes, and **Reset all**
  restores the full editor state. Megapixel and multiple presets use compact
  fixed grids so their final choices no longer stretch across wrapped rows.

- Reference discovery in Plan Studio and both scene prompt editors now crosses
  ComfyUI subgraph input/output rails. Tagged references, Semantic Anchor
  Bundles, their internal semantic picture anchors, and externally supplied
  preview images remain visible when the reference registry is packed into a
  subgraph for presentation or reusable storage.

- Plan editing now exposes only two timing levels: the Plan-wide default
  seconds/steps widgets and optional per-scene overrides. The redundant
  JSON-level default controls were removed from both Scene Plan and Plan
  Studio, and scene placeholders now inherit directly from the Plan widgets.

- Checkpoint Manager branch rows keep their compact shared-revision colors and
  badges without drawing long SVG lineage rails across the branch list.

- Plan Studio now persists its discovered motion-reference and Source Timeline
  audio presentation beside the run, then restores it automatically after a
  workflow, browser, or server reload. This UI-only record does not participate
  in generation fingerprints, checkpoint branches, or resume validation.

- Long Plan timelines no longer rebuild one live video element per saved scene
  on every checkpoint poll. Generated cards use immutable, lazy, disk-cached
  JPEG thumbnails and update in place, substantially reducing browser media
  churn and the associated Windows Proactor `WinError 10054` disconnect noise.

- The two-clip masked AV bridge now accepts silent source videos. Start and end
  audio are independent optional inputs: an available endpoint is encoded and
  protected, while a missing endpoint remains unmasked so H3 generates audio
  there instead of failing or forcing artificial silence.

- **Use this take & stop batch** now pins the accepted candidate preview across
  targeted cancellation and the automatic next-scene queue. Late payloads from
  an earlier scene can no longer replace it with the predecessor preview; a new
  run of the same scene, the next scene, or an explicit checkpoint choice
  releases the pin normally.

- Review Gate candidate polling no longer reloads an unchanged preview or
  jumps back to the newest take when a live candidate batch reaches its final
  review token. The selected take, playback position, and playing/paused state
  survive progress refreshes and synchronized-audio preview upgrades.

- Plan Studio's scene lanes now behave as one horizontally zoomable timeline.
  **Fit** compresses the complete Plan into the available width; zooming in
  expands duration-proportional scene spans beneath a shared ruler and single
  scrollbar. Generated clips, motion references, Source Timeline audio, and
  the playhead remain aligned while resizing, zooming around the pointer, and
  navigating long Plans.

- Live generation can now route **Current Shot** directly through the four
  H3 de-rope adapter nodes, recover the exact AV clock, and save that recovered
  latent back into the normal chain. The same nodes still accept deferred
  Upscale Loop state. Inline continuity resolves the Plan's actual linear,
  non-linear, or composed visual-context source, and a recovered scene tail is
  protected only when a later scene really consumes it.

- Added ordered **Composed visual context** in Plan and Plan Studio. A
  continuation can assemble one native H3 context total from two saved scene
  tails. The UI exposes both orientations of every valid split—for example
  `39 = 17+22` as well as `22+17`, then all splits for `56`, `73`, and the
  longer native totals. Native-phase layouts splice latent blocks directly;
  inverse layouts are normalized through the connected video VAE only when a
  latent continuation consumes them. Plan Studio also remembers whether its
  Advanced Boundary controls are open while settings re-render. The complete
  generated-audio latent stays
  continuous from the immediately previous timeline scene, the two visual
  sources are tracked independently by checkpoint preflight, and checkpoint
  activation restores the exact source IDs, total, and split.

- Unified source and deferred-upscale finishing in **H3 Chain Assemble**.
  Upscale Loop End now emits the common manifest wire; Assemble dispatches by
  document format, keeps HQ finals under the child profile, and exposes the
  same optional regular-output copy, filename, audio, and assembly controls.
  The former Upscale Merger remains as a deprecated compatibility wrapper.

- Ported the strict authoring layer from the standalone H3 Prompt IDE into
  both scene prompt editors. They now share Auto/T2VA/I2VA/FL2VA/L2VA/Ref2VA
  schema selection, ordered-section diagnostics and repair, exact keyframe
  alignment helpers, live word/character counts, and mode-aware completion.
  Completion and rich presentation now also understand 12 shot markers,
  multilingual dialogue markers, stable speaker IDs, `<scenetrans>`, and
  `<cutoff>`; the Rich Scene Prompt Editor gained the same Plain/Rich source
  toggle as the focused editor.

- Reference insertion from the Scene Prompt Editor's top menu now preserves
  the last rich-text or plain-text selection, so a chosen reference replaces
  the selection or appears at the requested caret instead of jumping to the
  prompt start. `#` autocomplete and the reference tray now also include
  dedicated semantic-only picture anchors and select their timestamp for
  immediate editing.

- Added deterministic alternatives in scene prompts with ComfyUI-style
  `{first|second}` syntax. Every scene now has a **Prompt alternatives** policy:
  derive a stable scene seed, use an exact fixed scene seed, or generate a fresh
  choice each time that Plan is queued. The redundant Plan-wide prompt seed was
  removed. Sampler seeds remain completely
  independent. The authored template, selected text, and exact choice seed are
  saved together. Resume treats a new choice or policy from the same template
  as neutral for completed predecessors, but still rejects an actual template
  edit.

- Reference chips in both rich scene-prompt editors are now directly
  editable. Clicking a chip can replace that exact occurrence with another
  compatible connected reference, switch a tagged picture between native
  `@tag` and semantic `#tag[time]` syntax, and edit the semantic timestamp in
  a dedicated seconds field without rewriting the prompt by hand.

- Split Qwen-only `#semantic[timestamp]` pictures from native H3 `@reference`
  media. New Semantic Picture Anchor nodes feed one Semantic Anchor Bundle
  with centralized scale/mode controls; the bundle connects once to Tagged
  Ref2VA, Plan Studio, and Run Manager. Semantic pictures no longer consume
  native picture/video/audio capacity, while their source loaders remain
  recoverable through the saved-run asset manifest and their combined
  incremental fingerprint remains resume-safe.

- Added **MiniMax H3 Scene LoRA Scheduler**, a lazy per-scene MODEL router
  that deliberately does not load or patch LoRAs. Plan and Plan Studio expose
  Base plus LoRA A-D on every scene; each branch comes from ordinary ComfyUI
  LoRA loaders and may contain a stack or strength variant. Only the selected
  input is evaluated. Route choices are saved as Plan/revision provenance but
  deliberately excluded from checkpoint/resume verification, and Base remains
  absent from serialization for compatibility with existing plans.

- The bundled LBH 3D deferred-upscale workflow now leaves the pass-2 prompt
  override blank, preserving the exact compiled scene prompt by default. The
  former neutral replacement text remains available in the How To Run note
  for explicit copy/paste comparisons.

- Added an opt-in `future_end_anchor` Guide/AV experiment. It reuses the
  predecessor context's final prepared latent step as one stock-clean visual
  condition immediately after the target timeline. In AV modes the preserved
  prefix remains in the masked target latent and the suffix is copied from
  that already-prepared prefix, including its exact proxy/tone treatment.
  The normal prefix, mask, output length, and Loop Trim stay unchanged. This
  tests whether one background/camera cue can suppress one-sided composition
  and colour drift without restoring the complete Guide prefix to `0.999`.
  The Visual Context Schedule patch now has a selective
  `future_anchor_only` scope. A manual full-sigma schedule such as
  `0.999, 0.999, 0` keeps the suffix clean for composition-setting steps one
  and two, then replaces only that suffix with timestep-matched stable noise;
  the packed layout stays fixed and split sampler branches share one absolute
  cutoff.

- Added an opt-in **Guide Late Reveal** research MODEL patch. During an active
  Guide-family continuation, it replaces H3's static near-clean visual
  predecessor condition with a coherent late-reveal schedule. The recommended
  first A/B, `matched`, follows the target's current `t = 1 - sigma` exactly,
  keeping condition content and its timestep label synchronized throughout
  denoising. Experimental `next_step` follows the next lower endpoint of the
  original full sigma schedule, remaining nearly noise-only during early
  composition while reaching a clean Guide on the final model call. Chain
  Context marks only its own
  video Guide records, so the hash-gated selective scope leaves character,
  authored-keyframe, and Ref2VA visual rows at their original strength. The
  all-visual public-wrapper scope remains available as a diagnostic. Both use
  absolute sigma; `next_step` accepts the unsplit schedule so switched/split
  model branches cannot reset progress. The selective path uses a normal
  diffusion wrapper when the proposed native ComfyUI per-condition contract is
  detected; current reviewed core retains the hash-gated compatibility
  forward, while unknown core changes still fail closed. A secondary
  `dependent_latent` noise backend reproduces the latent-space draw, temporal
  extent, seed restart, slice, and patch order used by public H3 runtimes only
  for recursive Chain Context rows; `comfy_rows` remains the recommended first
  A/B and preserves current ComfyUI behavior exactly.
  Added a split-safe `manual` preset for exact per-step experiments. It reads
  clean fractions such as `0, 0.999` against the original unsplit sigma
  schedule and holds the final supplied value for all remaining steps, rather
  than relying on a model-call counter.
  Selective scheduling can target either the recursive Guide prefix or the
  separately marked future suffix anchor; the latter also activates for AV
  continuation modes without modifying their preserved latent prefix.

- Expanded Chain Context's Guide-only `visual_cond_noise_aug` diagnostic to
  the complete `0.000`–`1.000` range. A `0.000` test now preserves the packed
  condition layout while replacing its visual latent content with seeded
  noise, allowing content-driven drift to be separated from structural
  condition-row effects.

- Hid the Upscale Adapter's provenance-only `recipe_json` in the default
  presentation. It remains serialized for resume validation and can be edited
  through **Show advanced H3 controls**.

- Fixed Checkpoint Manager branch clicks so the selected lineage is committed
  through the hidden Comfy widget callback and immediately available to
  `selected_manifest`. Branch headers now select their final revision, and
  the UI distinguishes the current selection from the saved active lineage.

- Added an inline **Upscale Reference + Prompt Override** on the existing
  `H3_TAGGED_REFERENCES` line. Connected Tagged refs replace the automatic
  cache for pass 2, optional tag filtering can remove unwanted refs, and the
  paired prompt output recompiles their native/Qwen conditioning coherently.
  Blank reference input keeps automatic cache restore active.
- Fixed deferred H3 conditioning sync enlarging `max` picture-reference
  latents with the output canvas. Max refs now retain their Core H3 capped
  geometry, while `match` refs remain canvas-aware and cache-v2 masters that
  were already rebuilt at pass-2 size are protected from double scaling.

- Added a guarded compatibility backport for ComfyUI PR #15808. Older core
  builds now register MiniMax H3's seven released dialogue, cutoff, lyrics,
  and caption tokens on the MiniMax-only Qwen tokenizer; updated ComfyUI
  builds are detected and remain fully core-owned.

- Added an opt-in **Reference Video Fade** MODEL patch for native H3 Ref2VA
  video blocks. It keeps the complete 24 fps reference at full early
  influence, then applies a full-schedule half-cosine attention-value fade
  without touching still refs, Qwen presentation, reference audio,
  continuation guides, or target streams. Split samplers can share the
  original unsplit sigma schedule, and existing SolAttn/Comfy Kitchen
  attention overrides remain chained. This complements rather than replaces
  Color-Stable Drift AV: one controls external native video references, while
  the other controls the previous-scene continuation prefix.
- Added a whole-branch SeedVR2 route: Checkpoint Manager → Full-Chain Latent
  Video Adapter → SeedVR2 Direct. It decodes the immutable H3 video latents,
  resolves scene overlap blends before upscaling, and exposes one continuous
  native file-backed VIDEO instead of restarting an upscaler per scene.
- Added a disk-backed MiniMax VAE output buffer and content-addressed source
  cache under `upscaled/seedvr2/source/`. The decoder retains its native
  temporal chunks while ordinary RAM holds only the boundary window; saved
  source/generated audio is embedded automatically for the SeedVR2 pass.
- Added the standalone whole-chain SeedVR2 workflow and the LBH 3D H3 latent
  workflow with video-only learned upscale, locked source audio, and a
  conservative two-step 0.24 refinement pass.
- Added a backend-neutral recursive upscale child run: Checkpoint Manager
  selected manifest → Upscale Adapter → Current Scene → H3/LTX/custom backend →
  Segment Save → Loop End → Merger. Each profile is isolated under the parent
  run's `upscaled/<profile>` folder with verified resume metadata.
- Upscale Current Scene prefers an explicitly saved denoised H3 x0 and exposes
  joint AV plus split video/audio latent routes for combined and video-only
  learned H3 upscalers. Older terminal-latent checkpoints remain supported,
  and decoded-video LTX 2.5 passes use the same orchestration.
- Made persistence of the large HQ latent optional and off by default. A small
  self-contained assembly/audio checkpoint is still written for reliable
  resume and final merge.
- Checkpoint Manager now serializes its selected immutable lineage and emits a
  verified generated-tip manifest, including partial runs whose later planned
  scenes do not exist yet. The standalone upscale workflow therefore needs
  no source Plan, Chain Policy, or live Source Timeline connection.
- Added cache-v2 pass-2 conditioning: original picture masters are retained so
  `match` image refs and their Qwen presentation can be rebuilt at the exact
  LBH target canvas. V1 caches remain readable; max/video/audio refs preserve
  their native geometry instead of being blindly scaled.

## v0.5.5 — Modern editable-install metadata

- Added an explicit setuptools build backend and disabled accidental discovery
  of asset and workflow folders as Python packages, so `pip install -e .`
  succeeds with current setuptools releases.
- Replaced deprecated license metadata with its SPDX form while leaving the
  repository's GPL v3 license text unchanged. Thanks to @ed45626 in PR #27.

## v0.5.4 — Cleaner shared-checkpoint links

- Moved shared-revision connectors into a dedicated side gutter with thin
  solid rails and short taps to each matching card, keeping lineage marks away
  from branch names, revision text, and status labels.

## v0.5.3 — Review candidate batches and checkpoint refresh

- Review Gate can now generate 1–20 different-seed takes for each scene,
  present them together, and continue from the exact saved video/audio
  checkpoint selected by the user. The default remains one take.
- Selecting an earlier take atomically promotes its checkpoint and recovery
  Plan before the loop continues, so later scenes and interrupted-run recovery
  follow the chosen branch rather than the last generated take.
- Fixed overlapping checkpoint refresh requests adding duplicate choices after
  workflow reload, and clarified that rejected, rerolled, and candidate takes
  remain as intentional immutable recovery revisions until explicitly deleted.

## v0.5.2 — Shared checkpoint lineage visibility

- Repeated checkpoint revisions now keep the existing branch layout while
  receiving a consistent color label and a vertical connector between every
  branch line that shares the same clip.
- Loop Trim now resolves `video_blend_frames` from Current Shot state. This
  removes the ambiguous Plan-default versus per-scene integer wiring that could
  discard the requested overlap and fail Segment Save only after sampling.
  Legacy integer sockets remain compatible, while maintained 0.5 workflows and
  the migration tool use the authoritative state route.

## v0.5.1 — Checkpoint Manager and workflow clarity

- Added experimental **Drift-Control AV** for recursive same-shot chains. It
  keeps predecessor checkpoints clean, but at every sampler evaluation moves
  the disposable 39-frame video prefix to the next scheduler sigma using the
  sampler's existing noise field. Eight video-latent steps receive the full
  matched ratio and the final four taper `.75/.50/.25/.00` to an exact seam.
  Chain Context now exposes an optional MODEL passthrough so the sampler blend
  and H3 per-row timestep labels receive the same live mask. The 20-step path
  is the initial validated baseline; existing continuation modes are unchanged.

- Corrected the scheduled Guide 5/6 spatial proxy to reproduce the observed
  mixed-resolution chain operation: reduce the complete saved predecessor
  video latent, VAE-decode it on the 5/6 grid, select the delivered RGB tail,
  and only then restore it through Motion Context. The UI now calls this
  **Low-grid 5/6 · Guide** instead of implying a simple RGB resize. Existing
  AV latent-prefix proxy behavior is unchanged.

- Added a Plan-passthrough Checkpoint Manager that browses every saved run by
  scene and inferred revision branch, previews saved video/audio, and exposes
  prompts, seeds, frame counts, compatibility data, storage, lineage, and the
  exact incoming video/audio context for each revision.
- Added dependency-aware cleanup. Active revisions and revisions used by later
  scenes are protected; the manager identifies every dependent scene and lets
  users work backward from a leaf one revision at a time.
- Added a two-step deletion contract shared with Review Gate. The server
  previews every owned/shared file and preserved archive category, then rejects
  confirmation if files, active pointers, or descendants changed in between.
- New checkpoints persist creation time, effective continuation context, and a
  stable branch identity so future runs need less lineage inference while old
  checkpoint folders remain fully discoverable.

- Made titles, active controls, and rich reference tags derive their semantic
  colors from the active ComfyUI foreground. Both scene prompt editors retain
  their pastel dark-theme palette while gaining readable contrast in light
  themes.

- Added shared text-level Ctrl/Cmd+Z and redo history to both dedicated scene
  prompt editors. Undo now survives rich-tag DOM decoration, plain-text paste,
  toolbar insertion, Plan synchronization, and switching between rich and
  plain presentation.

- Added a maintained two-scene Ref2V masked-inpaint demo that uses the bundled
  CC0 crab picture as replacement appearance while keeping the source movie as
  the authoritative joint AV target. Documented its exact mask-contract
  compatibility with Ablejones/droz's v3.1 MaskVidExperiments workflow and the
  separate, optional roles of crop/uncrop, tracking, and mask growth.
- Reworked the README as a task-oriented quick start and workflow chooser,
  moving implementation detail into focused guides. Added a feature
  traceability matrix that distinguishes original, adapted, inspired,
  integrated, and compatibility work with upstream, code, and commit links.
- Added exact H3 causal/token mask conversion to Apply Target Mask. Static or
  correctly sliced tracked masks now max-reduce through the VAE's repeating
  `1,4,4,4,4` source-frame groups and the model's 2×2 latent-token cells,
  avoiding temporal interpolation drift and loss of thin moving regions. The
  bundled inpaint workflow uses the exact mode; legacy trilinear conversion
  remains selectable for controlled compatibility comparisons. Ordinary AV
  extension remains unchanged and needs no user-supplied mask.
- Revised experimental Detail AV to a clean-boundary v2 taper. Video-latent
  noise now starts at 0.30 and falls through 0.225, 0.15, and 0.075 to an exact
  zero on the boundary-adjacent step, reducing seam displacement while keeping
  the predecessor, carried audio, denoise masks, and final overlap clean.
- Recognized the final merged ComfyUI PR #15375 helper-based mask API and left
  it fully native. The old-build fallback now mirrors merge-time commit
  `c676536`, including pooled token-grid masks and ceil-quantized 8-bit mask
  strengths, and removes obsolete pre-merge wrappers before capability checks.
- Restored saved prompts, Plan widgets, and connected 0.5 audio/transition
  policy nodes together when loading a run or checkpoint revision. Revision
  recovery now uses the selected checkpoint chain's policy metadata instead of
  silently retaining newer controls from the active workflow.
- Disabled the legacy Review Gate prompt textarea by default and added an
  Interface setting to restore it. While disabled, Retry and Reroll use the
  active Plan prompt authored in Scene Prompt Editor or Rich Scene Prompt
  Editor.
- Added the typed Source Timeline contract so path-backed picture and audio are
  registered once, remain lazy, and are sliced per scene without full-track
  AUDIO fan-out.
- Split audio intent into final soundtrack, source-reference, and generated-
  continuity axes while preserving the exact behavior of saved 0.4 modes.
- Added Cut, Guide, Latent Guide, Detail Guide, Hard AV, and Soft AV incoming-
  transition policies with advanced access to the existing low-level controls.
- Aligned Soft AV with the upstream tested recipe: an exact 39-frame picture
  prefix plus a half-cosine audio release. The older dual-stream
  `feathered_av` implementation remains available through Expert override for
  compatibility, and the former Audio Feather AV preset remains as an alias.
- AV continuation now validates the shared 24 fps picture / 40 Hz audio clock
  before model loading. Only 39, 90, 141, 192, or 243 context frames are
  accepted, eliminating silently rounded prefixes such as 22 or 56 frames.
- Preserved masked-AV decoded overlap audio through Loop Trim and Segment Save
  without adding a public socket. Generated-audio assembly now gives the
  incoming AV scene ownership of that overlap at both generated-scene and
  external-prelude boundaries, so Soft AV's audio feather reaches the final
  track; older checkpoints retain delivered-only fallback behavior.
- Updated both maintained soldier-crab extension workflows to the 0.5 Soft AV
  policy with a full 39-frame visual seam blend, matching the tested upstream
  extension topology while retaining recursive Plan/Run Manager operation.
- Added opt-in Latent Guide continuation. Generated boundaries reuse the
  phase-aligned tail of the accepted predecessor's sampled video latent as
  persistent Guide conditioning without an RGB/VAE round trip; imported or
  incompatible context retains the original Guide fallback.
- Added opt-in Detail Guide continuation, adapting MacroSony's deterministic
  tapered chroma-noise context recipe. The 22-frame preset uses 19 frames at
  0.45 and a three-frame taper to 0.10; expert mode supports other Guide
  lengths without modifying the accepted predecessor checkpoint.
- Added experimental Detail AV continuation, adapting beijinren's latent
  context-noise recipe to the recursive hard-AV path. It treats a disposable
  copy of the 39-frame / 12-step video prefix with matched-variance Gaussian
  noise using a boundary-clean taper, leaves audio and masks unchanged, trims
  the complete treated overlap, and fingerprints the exact recipe for resume.
- Added model-free Chain Preflight and shared Plan Studio validation for source
  duration, scene windows, references, runtime compatibility, and resume
  eligibility.
- Added a second Plan Studio track for the exact active motion-reference
  windows. Selected scenes are transcoded lazily to cached low-resolution
  previews, and the player provides synchronized generated/reference wipe
  comparison with incoming-context offsets applied automatically.
- Replaced opaque whole-Plan resume hashes with structured scene dependencies
  and actionable diffs. Future-scene, incoming-boundary, and assembly-only
  changes no longer force regeneration of an accepted predecessor.
- Simplified conditional socket presentation, clarified active Plan versus
  selected Run Manager archive state, and made prompt-history activation
  explicit.
- Migrated every maintained workflow to explicit policies and preflight. The
  source-audio demo now uses canonical Source Timeline wiring and Current
  Shot's scene-local reference slice.
- Added an idempotent workflow migration tool, frozen 0.4 positional fixtures,
  and backend/frontend release validation. Existing nodes, workflow JSON,
  manifests, and checkpoints remain supported.
- Completed input and output tooltips across the 0.5 policy, Source Timeline,
  prompt-driven reference, semantic-anchor, masking, and bridge nodes. The
  integration smoke fixture now follows the real Current Shot state wire so
  strict per-scene source-window provenance is exercised during recovery.

## v0.4.9 — Run Manager active asset target

- Made the Run Manager show the connected Plan `run_name` beside Reference
  assets so the destination of **Save/update assets** is explicit.
- After a manual asset save, the saved-run browser now selects the actual
  destination run instead of remaining on a previously browsed run.
- Kept asset-only run folders selectable for inspection and opening while
  leaving **Load into Plan** disabled until a restorable Plan archive exists.

## v0.4.8 — Feathered AV continuation

- Added `feathered_av` as a third continuation mode. It uses the same aligned
  video/audio target prefix as `masked_av`, but ramps the end of that prefix
  from preservation toward generation to soften the temporal handoff.
- For the recommended 39-frame prefix, the first 8 video latent steps and 42
  audio steps remain fully protected. The final 4 video steps and 23 audio
  steps use monotonic denoise ramps; all future rows remain fully denoisable.
- Added the mode to Plan, per-scene overrides, Plan Studio, timing validation,
  resume provenance, and the native-first PR #15375 compatibility path.
- Added Loop Start `verify_resume_history`, enabled by default. Disabling it
  explicitly permits reuse of a saved predecessor after intentional Plan
  changes while retaining file, SHA-256, tensor-shape, and internal metadata
  integrity checks.
- Corrected sequential Tagged Motion Ref timing under masked AV continuation.
  Scene 2+ now excludes the repeated prefix from the unpositioned native video
  bank and applies the identical delivered-frame window to paired audio,
  removing the context-length motion delay without changing generic video refs.
- Added path-backed Tagged Motion Ref for long CFR control videos. It keeps
  compressed media on disk and decodes/resizes only the active scene, converts
  that window to 24 fps, and extracts its exact embedded-audio time range. A
  separate Plan-aware scene-counter preview blocks media output when no Plan is
  connected or the tag is inactive. A native-frame start offset seeks near the
  requested source point and shifts video and audio together without building
  tensors for the skipped prefix. It also accepts native file-backed `VIDEO`
  from core Load Video, allowing that loader to fan out normally to both the
  tagged reference and Run Manager asset registration. A dedicated Lazy
  Motion AV Loader now exposes that same disk-backed native VIDEO plus the
  complete post-skip AUDIO track without decoding any video frames. The tagged
  node accepts and passes through that full track, while retaining direct-path
  audio extraction for compatibility.

## v0.4.7 — General masked targets

- Updated the vendored PR #15375 fallback through upstream commit `989e7a9`.
  Arbitrary masks now retain their original per-pixel sampler blend while H3
  pools only its internal video/audio timestep labels to the token grid. Older
  ComfyUI builds receive a scoped per-step sampler bridge; post-PR builds keep
  their native implementation untouched.
- Made Plan-wide continuation mode and context length next-scene choices for
  resume validation. Switching either after a completed scene now reuses that
  verified predecessor instead of demanding a pointless regeneration. When a
  longer tail was not cached, it is recovered by re-decoding the saved full
  video latent. Explicit per-scene overrides remain generation-significant,
  and new segment records retain their effective values for provenance.
- Fixed master-audio encoding when H3's rounded 40 Hz target requires one
  latent step beyond a floor-style VAE encode of the exact picture duration.
  The node uses real timeline-audio lookahead, crops to the authoritative
  target length, and keeps `clip_audio` at exact picture duration.
- Added an exact master-audio target composer for timeline-driven FL2VA. It
  VAE-encodes the current interval from music, dialogue, narration, or effects
  into the complete H3 audio target, protects every audio row, and optionally
  protects a native previous-video prefix while future video rows remain
  denoisable.
- Added public source-AV trim, 32px mask-grid preview, and arbitrary H3 masked
  target nodes for static or tracked video inpainting.
- Added preserve, generate, follow-video, and custom audio-mask modes. New
  masks intersect existing nested masks, allowing spatial edits to compose
  with the chain's protected `masked_av` prefix.
- Adapted the complete masked-video inpaint workflow to the existing
  native-first PR #15375 compatibility. The old per-row MODEL patch is not
  required, and distinct public IDs allow both packs to remain installed.
- Documented how the same mask contract extends to prepared outpaint canvases,
  temporal repairs, and two-ended clip-bridge targets.

## v0.4.10 — Final-output publishing

- Fixed scene and final MP4 publication in ComfyUI's global Media Assets panel
  by using the native animated-video output descriptor recognized by job
  history.
- Added optional Final Assemble controls to copy the completed MP4 into the
  regular ComfyUI output tree. The relative subfolder supports nested folders
  and date tokens, an empty value targets the output root, and collisions are
  versioned without replacing the canonical chain final.

## v0.4.9 — Defensive Sol-Attn observer detection

- Fixed folder-independent Sol-Attn recognition for the defensive
  `getattr(self, "segments", ...)` observer used by the Kitchen PR helper,
  while continuing to require the complete `_video_span`, `_SPANS`,
  `position_ids`, and `segments` fingerprint. Thanks to @tsolful in PR #16
  for identifying the bytecode lookup difference.

## v0.4.8 — Renamed Sol-Attn observer compatibility

- Made Sol-Attn H3 layout-observer compatibility independent of the custom
  node's install-folder name. Renamed PR helpers such as
  `sol_attn_minimax_v2`, lazy installation between scene 1 and scene 2, and
  nested read-only observer copies now preserve native Add Guide detection;
  unrelated layout-mutating wrappers remain refused.

## v0.4.7 — Review/editor synchronization and compatibility

- Synchronized Review Gate with prompt editors bound to the same Plan. A new
  review activates its scene in the connected editor, live editor changes are
  reflected in the Gate, and retry/reroll reads the current Plan prompt unless
  the Gate field was explicitly edited as a fallback.
- Removed the core Ref2VA right-click conversion into Scheduled Ref2VA and its
  graph-rewriting implementation. Scheduled and Tagged Ref2VA remain available
  as explicit nodes.
- Recognized the audited path-valued H3 layout observer installed by the
  `ComfyUI-SolAttn-CUDA-PR117` development checkout, while continuing to reject
  unknown wrappers.
- Rotated every browser helper-module cache token with the release so the new
  Review Gate/core export contract cannot be mixed with cached v0.4.6 modules.

## v0.4.6 — Asset publishing and media fallbacks

- Scene Segment Save and Final Assemble now publish their MP4 paths through
  ComfyUI's standard output descriptor contract, so newly produced scene clips
  and final videos appear promptly in the global Assets sidebar when enabled.
- FFmpeg executables found on `PATH` are now launch-tested once before use. A
  broken Windows build (including `0xC0000139` DLL entry-point failures) is
  treated as unavailable so review muxing and final assembly use PyAV instead.
- Versioned every production `.mjs` import with the package release and added
  a consistency regression check, preventing stale browser helper modules from
  disabling the Plan DOM editor after an update.

## v0.4.5 — Scene context and checkpoint Plan recovery

- Added per-scene context-length overrides to the Plan's existing Advanced
  controls and Plan Studio. Blank inherits the Plan default; `0` creates a
  visually independent scene.
- Added an independent per-scene generated-audio context override. A guide
  scene can now use zero video context while retaining preceding dialogue,
  ambience, or music; explicit audio `0` disables the carry, while masked AV
  remains locked to one synchronized prefix.
- Made timing, masked/guide behavior, imported-video scene 1 handling, resume
  hashes, checkpoint tail storage, Run Manager recovery, and checkpoint
  revision recovery preserve the effective per-scene context.

- Made Review Gate's ordinary **Load checkpoint** action restore the saved
  run's complete Plan before arming Loop Start. Prompts and Plan settings no
  longer remain from whichever workflow happened to be open.
- Reapplied the exact selected checkpoint metadata for every saved predecessor
  scene, including active revisions, while keeping plan-only recovery free of
  archived-asset materialization side effects.

## v0.4.4 — Review retry persistence

- Kept each Review Gate retry's edited scene prompt, seed, and length bound to
  the same submitted scene while the server request is in flight.
- Made the server return the accepted prompt and synchronized it into the Plan
  and any open prompt companion editor, preventing stale UI state from
  replacing the retry prompt.

## v0.4.3 — Per-scene continuation modes

- Added per-scene `continuation_mode` overrides in Plan JSON, the compact Plan
  editor advanced controls, and Plan Studio. The Plan node setting remains the
  inherited default, so one chain can use flexible `guide` transitions for new
  shots and exact `masked_av` transitions for continued shots.
- Fixed ComfyUI Stop/Cancel while execution is waiting indefinitely at Review
  Gate; the gate heartbeat now observes the processing-interrupted flag and
  resolves its browser controls before propagating the normal interruption.
- Added experimental Plan `continuation_mode=masked_av`. Chain Context
  VAE-encodes the preceding scene's decoded tail into the next target video
  latent, copies the matching sampled audio-latent tail, and emits nested
  per-stream masks where `0` preserves the prefix and `1` generates the future.
- Appended a sampler-ready LATENT output to Chain Context without changing its
  existing output indices; guide mode and scene 1 pass the original latent
  through unchanged.
- Added lazy native-first PR #15375 compatibility for H3 mask payloads,
  preprocessing, inpaint scaling, and per-row diffusion timesteps.
- Converted Studio Tagged into the wired 39-frame / 65-audio-step masked AV
  example while retaining Tagged Ref2VA and Plan Studio authoring.

## v0.4.2 — Checkpoint revision recovery

- Extended the existing Review Gate checkpoint browser to discover every
  retained scene revision, preview it, and restore a selected predecessor
  chain before resuming the next scene.
- Restoring revisions updates the editable Plan's scene prompts, seeds,
  lengths, steps, identifiers, and shared prompt before Loop Start is armed.
- Added guarded cleanup for inactive revisions. Review Gate shows each
  revision's estimated storage and requires an explicit permanent-delete
  confirmation; active pointers and unrelated run files cannot be removed.

## v0.4.1 — Tagged source audio and UI fixes

- Added `source_timeline` playback to Tagged Audio Ref. It fingerprints the full
  Loop source track while Tagged Ref2VA derives each exact scene slice from
  Current Shot state, avoiding the circular dependency caused by returning a
  dynamically sliced audio fingerprint to Plan.
- Corrected every maintained Ref2V example to load the Ref2VA diffusion model
  instead of the FL2VA checkpoint inherited from the workflow template.
- Kept the Run Manager's serialized asset-binding state hidden across legacy
  and current canvas renderers so its internal JSON field cannot leak into the
  visible node layout.
- Constrained the main Plan editor to its assigned DOM-widget bounds and fully
  suppressed its internal `plan_json` canvas widget, preventing an intermittent
  invisible hit layer from blocking the native Plan settings.

## v0.4.0 — Prompt-driven Ref2VA and Studio authoring

- Added Tagged Picture, Video, and Audio references. Register stable aliases
  such as `@hero`, `@motion`, and `@voice`; only tags present in the resolved
  scene prompt are sent to H3 and compacted to native media labels.
- Retained numeric-range Scheduled Ref2VA under the legacy-schedule category
  for workflows that need explicit scene selectors.
- Added optional Plan Studio and Rich Scene Prompt Editor authoring, including
  synchronized scene selection, rich reference chips and previews, prompt
  revisions, and configurable Direct API or MCP prompt optimization.
- Added maintained T2V and I2V Normal/Studio pairs, indexed A→B→A FL2V, Basic /
  Tagged / Studio Tagged Ref2V, and an experimental advancing motion-reference
  workflow. Previous examples remain archived rather than deleted.
- Added cumulative disk-backed visual blending, final-assembly playback in
  Review Gate, editable retry duration, and exact scene retiming.
- Added native first/last-frame reference previews that follow the active frame
  index, plus prompt-driven image, video, and audio miniatures.
- Added portable Run Manager asset restoration to the Studio Tagged example.
- Updated compatibility for merged ComfyUI PR #15439: current ComfyUI owns H3
  guide placement and payload merging; older builds receive one warning before
  the guarded fallback is used.
- Added an optional external Plan JSON STRING input for provider-independent
  story-director and LLM workflows.

Credit: native H3 guide support is by **drozbay**; cumulative audio budgeting
was inspired by **seitanism**; the editor interaction pattern was inspired by
**nkxx188's ComfyUI-MiniMaxH3-Easy**. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the complete attribution.

## v0.3.28 — PyAV audio rounding tolerance

Final PyAV muxing tolerates and zero-pads a single missing sample caused by
frame-to-sample rounding while continuing to reject larger audio deficits.

## v0.3.27 — True disabled scheduler compliance

Disabled policy reaches upstream Schedule nodes, converts scheduler-owned
validation into warnings, and omits unusable media. An empty
`source_audio_slice` left wired in `generated_audio` mode no longer stops a
render.

## v0.3.26 — Three-level prompt compliance

Scheduled Ref2VA offers strict, soft, and disabled policy. Strict blocks
scheduler mistakes; soft relaxes prompt-alias failures; disabled passes prompt
text through unchanged and makes scheduler checks non-blocking.

## v0.3.25 — Portable run assets and optional tag warnings

Run Manager accepts dynamic loader-asset connections, records persistent
binding identities and original input paths, and can retain content-addressed
image/audio/video fallbacks under the run folder. Restore prefers the original
input file and materializes an archived fallback only when needed. Scheduled
Ref2VA can downgrade unresolved prompt-tag failures to visible log warnings.

## v0.3.24 — Saved Run Manager

A companion node browses projects under `output/h3_chains`, reports scene and
checkpoint details, and restores archived prompts and Plan settings after
confirmation. Exact API/workflow inputs are preferred, with `plan.json` as the
older-run fallback.

## v0.3.23 — Branching scene-prompt history

The Scene Prompt Editor keeps lazy per-scene revisions outside workflow and
Plan JSON. Its compact `‹ 2 / 5 ›` control navigates versions, shows execution
state and timestamp, and creates a child branch when an executed revision is
edited.

## v0.3.22 — Optional floating reroll control

A ComfyUI setting under **MiniMax H3 Context Loop → Interface → Cancel &
reroll** can hide the floating in-progress action. Review Gate controls remain
available.

## v0.3.21 — Upstream continuity update and exact assembly

Motion Context preserves a stock H3 `last_frame` target while replacing a
conflicting first-frame anchor with its carried head. Added 56-frame context,
the in-graph Seam Probe, cumulative generated-audio sample budgeting, and
stitcher-ready retained visual overlap. The cumulative-audio approach was
inspired by **seitanism's**
[MultiRef implementation](https://github.com/seitanism/ComfyUI-H3-Motion-Context-MultiRef).

## v0.3.20 — Cancel and reroll the active scene

During generation, a guarded floating action can cancel only the active prompt,
assign a new explicit scene seed, preserve the selected range end, and requeue
through ComfyUI's normal queue.

## v0.3.19 — Plan and review UX pass

Plan controls retain pointer input, editor and preview sizes persist, scene
seeds remain visible, and reference menus show only sources active in the
selected scene. Documentation clarifies that `@aliases` are optional.

## v0.3.14 — Explicit compatible patch priority

The optional wired **MiniMax H3 Patch Priority** pass-through can promote this
pack over an older compatible Motion Context copy while retaining recognized
H3-Multishot and SolAttn behavior.

## v0.3.13 — Open a Plan's output folder

A compact **Output** action creates and opens
`output/h3_chains/<run_name>` on the ComfyUI host. Headless hosts fall back to
copying the host path into the browser clipboard.

## v0.3.12 — Clearer Plan guidance and looping I2VA

Expanded Plan tooltips, clarified audio modes and seed rerolls, and added a
single-image I2VA example plus First-Scene Image Gate.

## v0.3.11 — Invisible legacy widget-width repair

While a Context Loop node is on the canvas, the pack repairs the LiteGraph
widget-width regression across all nodes. Regenerated scenes retain previous
segment and checkpoint revisions instead of deleting the superseded take.

## v0.3.10 — Scene-scheduled Ref2VA

Added chained picture, video, paired-video-audio, and standalone-audio
references under stable `@tags`, with per-scene activation and compact native
label numbering. A right-click converter migrates an already-wired core Ref2VA
node.

## v0.3.8 — One-pass performance re-filming

Reference Video Prep converts native VIDEO or decoded IMAGE/AUDIO to exact
24 fps Ref2VA input, copies its soundtrack without padding or time-stretching,
and powers the experimental three-angle guitar workflow.

## v0.3.7 — Flexible video loaders

Existing Video Context accepts either native ComfyUI VIDEO or separate
IMAGE + AUDIO + FPS outputs.

## v0.3.6 — Extend an existing video

A typed adapter turns decoded video and optional audio into scene 1 context,
with optional normalized-source prepend for partial and final output.

## v0.3.5 — Native guides and portable assembly

Added automatic support for ComfyUI's native arbitrary-position AV guides,
retained the guarded legacy path, and added PyAV fallback when `ffmpeg` is not
available.

## v0.3.4 — Scene Prompt Editor

Added the synchronized large-format scene editor with navigation, reference and
dialogue shortcuts, and adjustable type size.

## v0.3.3 — Reliable preview resizing

Review video sizing remains stable when the ComfyUI canvas is zoomed.

## v0.3.2 — Resizable review video

The bar beneath Review Gate's player adjusts preview height.

## v0.3.1 — Friendlier JSON defaults

Top-level `duration_seconds` and `steps` shorthand populate visual Plan defaults
correctly.

## v0.3.0 — Archival PNG export

Saved scene checkpoints can be re-decoded into a continuous lossless PNG
sequence without holding the complete production in RAM.

## v0.2.0 — Recovery, metadata, and compatibility

- Persisted each scene prompt, effective plan, workflow, and API prompt beside
  the rendered chain.
- Added scene-range rendering, resumable review checkpoints, partial assembly,
  notification/timeout controls, and Firefox-safe Review Gate recovery.
- Added guarded compatibility with H3-Multishot, SolAttn, Ref2VA, and upstream
  H3 Motion Context.
- Added Comfy Registry publishing and a shorter project-focused README.

## v0.1.0 — The production loop takes shape

- Introduced the visual scene-plan editor, multiline prompts, automatic scene
  colors, responsive layout, and collapsible raw JSON.
- Added the recursive one-body chain, frame-locked audio trimming, per-scene
  checkpoints, interactive review/retry, and looping Ref2VA example.
- Renamed the expanded project **MiniMax H3 Context Loop** so it could coexist
  clearly with NikoDemon80's manual Motion Context tools.

## Origins — Motion Context and Ref2VA continuation

The project began with MiniMax H3 clip chaining and generated-audio
continuation, then added Ref2VA Motion Context, opt-in compatibility patches,
and the resumable disk-backed production loop.
