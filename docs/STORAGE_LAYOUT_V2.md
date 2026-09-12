# Organized chain storage layout

Status: explicit organized migration and normal runtime integration are
implemented on nightly. Follow [Migrate a chain](MIGRATE_STORAGE.md). The command
creates, verifies and enables an independent copy; legacy projects keep their
current paths until you deliberately switch to the new output directory.
Importing path modules or constructing a layout never migrates a project.

The bridge, control-state and combined-store rehearsal documents describe the
implementation history. Current verification and platform limits are in the
[completion checklist](STORAGE_COMPLETION_CHECKLIST.md).

This is the physical contract implemented by the
[migration plan](STORAGE_MIGRATION_PLAN.md), not a second migration mechanism.
It supersedes the audit's initial sketch of many folders at the project root.

## Three main areas

```text
<project>/
  storage.json                      # storage authority, not Plan settings
  media/                            # saved working results, by workflow stage
    generation/<take>/
    alternate/<take>/
    derope/<pass>/<take>/
    latent_upscale/<pass>/<take>/
    pixel_upscale/<pass>/<take>/
    video_refine/<pass>/<take>/
    custom/<pass>/<take>/
  exports/                          # finished deliverables
    png/<export-name>/
    video/<export-name>.mp4
    audio/<export-name>.wav          # optional standalone/versioned soundtracks
  project/                          # settings, relationships and supporting data
    takes/                          # immutable descriptors and prompt snapshots
    branches/                       # authored settings and selected takes
    passes/                         # processing recipes, sources, resume state
    cuts/                           # frozen chapter/project presentations
    assets/                         # imported reference media
    reference_cache/                # saved conditioning objects: preserve
    recovery/                       # saved Plan/workflow/API snapshots: preserve
    history/
    reviews/
    roots/
    aliases/                        # compatibility lookup: preserve
    legacy/                         # unchanged historical documents: preserve
    jobs/                           # durable journals and per-job staging
    tombstones/
    optional/                       # only disposable derived material
      previews/
      thumbnails/
      diagnostics/
```

Folders are created only when their feature writes something. An unused ALT,
upscale method or optional feature does not leave empty folders in the root.
`project/optional` is not a shortcut for storing every infrequently used file:
references, conditioning, recovery, review decisions and interrupted-job journals
are essential state. No automatic purge is part of this change.

## Media and delivery names

Each take owns whichever artifacts it actually has: `video.mp4`,
`checkpoint.safetensors`, `audio.wav`, `overlap.mp4`. Its metadata lives in
`project/takes/<take>.json`, with an optional `<take>.prompt.txt` alongside it.
A filename does not prove a full latent exists; preserve the take's capabilities.
For example, a pixel-upscale checkpoint may hold only audio/marker information.

Processing outputs are grouped **stage → pass → take**. Chapters and branches do
not add directory levels. A processing pass records its chapter/range, source cut,
recipe, profile label and exact input revisions in metadata. A combined operation
keeps its whole recipe even when filed under one primary stage. Changing a label
or assigning a shared take to a branch does not relocate the media.

PNG delivery has no redundant `frames` subfolder:

```text
exports/png/<export-name>/
  frame_00000001.png
  frame_00000002.png
```

Video delivery uses `exports/video/<export-name>.mp4`, with matching
`<export-name>.generated.wav` and `<export-name>.srt` sidecars when present.
Standalone audio uses `exports/audio/<export-name>.wav`. Export names come from
the chosen label; collisions add `_2`, `_3`, etc. Accepted indexes and ownership
stay under `project/`; the finished-PNG exporter also publishes `export.json`.
Both project and chapter deliveries use these locations; scope belongs in the
export record, not another nested directory. Internal job/take IDs are separate
from these readable names. Older ID-based exports remain supported.

The long legacy example becomes a directory like:

```text
Before: chapters/01_chapter_01/upscaled/h3_video_dlss5/frames/DLSS_upscale_dmd_2/
Target: exports/png/DLSS_upscale_dmd_2/
```

This is a **target**, not a change to the existing folder today. Existing `_2`
variants remain independent exports. Frame numbering, edited PNG bytes, owners
and tombstones must survive migration; a variant is never folded into another
export just because their labels or pixels match. External/custom output paths
stay in place unless explicitly relinked.

## Workflow coverage

The path policy classifies recorded semantics, not names such as `h3_lbh_3d` or
`DLSS_upscale`. It does not change samplers, conditioning or workflow connections.

| Workflow | Working media stage | Persistence boundary |
| --- | --- | --- |
| Original generation and remakes | `generation` | Immutable scene take; branch selection is separate |
| Final-cut ALT generation | `alternate` | ALT picture; base audio and generation-context ancestry remain distinct |
| DeRoPE-only / fast turbo | `derope` | H3 processing take and recipe |
| Combined LBH upscale + DeRoPE | `derope` | Both operations preserved in the saved recipe |
| Native LBH latent upscale / split experiment | `latent_upscale` | H3 processing take; latent/context capabilities retained |
| DLSS5 + USDU pixel processing | `pixel_upscale` | H3 processing take; no invented full video latent |
| DLSS5 + experimental LMS guide refinement | `pixel_upscale` | Existing pixel backend and LMS recipe remain unchanged |
| LTX 2.5 adapter processing | `video_refine` | Saved profile/source contract |
| Custom adapter processing | `custom` | Explicit backend/recipe, not guessed from a model name |
| SeedVR2 full-chain VIDEO example | No H3 per-scene processing stage currently | Third-party saver requires an explicit video-export integration |
| Chapter/project final assembly and PNG export | `exports/video` or `exports/png` | Export records own deliverables, separate from source takes |

The SeedVR2 distinction matters: its shipped workflow connects a full-chain VIDEO
adapter to a third-party saver. It does **not** currently publish H3 per-scene
upscale checkpoints. A future managed export must register the returned files
explicitly; do not claim per-scene resume or silently change that saver's chosen
destination. The policy reports this as an unresolved integration boundary.

All eight shipped `Deferred*.json` workflows are checked against the routing
policy. Generation/ALT, LTX and custom backends have separate policy tests. These
are storage-route checks, not GPU render or migrated-workflow conformance tests.

## Identity and path safety

- Use full 32-character hexadecimal storage/pass/export IDs. Do not shorten them
  to the eight-character UI labels. Legacy revision identities remain recorded
  unchanged; new storage IDs can distinguish identical tokens in old scopes.
- Media shared by branches or later passes remains at its owning take's address.
  Do not copy it into every branch. ALT audio can reference its base's artifact.
- Check the full path, including the real output/project prefix and staging
  suffixes, against a configurable budget (default 240). Windows UTF-16 units
  count too. An overlong destination blocks allocation; never truncate an ID.
- Paths are project-relative, traversal/drive/stream addresses are rejected, and
  native lookups reject symlink/junction components. Path construction grants no
  write/deletion authority; the existing ownership and publication guards remain
  required. This is not a substitute for native Windows/filesystem testing.

## Implemented in this batch

`storage_layout.py` is an executable, side-effect-free layout/routing policy.
It allocates **addresses only**, not IDs or files. There is no marker detection,
cutover, fallback to legacy after a V2 error, or active new-format writer.

`storage_legacy.py` centralizes existing physical addresses. Current generation
paths, branch-local checkpoint pointers, mutable/immutable recovery snapshots,
chapter roots, processing profile paths and both PNG export paths use it.
Processing catalogue/deletion directory lookups and DeRoPE scope validation also
use the legacy boundary. Existing confinement checks stay with their callers;
file names, serialized addresses, hashes and selection semantics are unchanged.

The newer copy-only bridge adds exercised graph/processing readers, assembly,
asset/cache resolution, explicit alias authority, save/history/handoff fences
and opt-in organized payload reservations. See its
[current scope and tests](STORAGE_BRIDGE_REHEARSAL.md). It is not completed
migration support: complete feature transactions over the combined root,
browser endpoints and external saver integration remain release gates. Native
Windows has not been run. CIFS replacement failed; the opt-in
[immutable commit log](STORAGE_COMMIT_LOG.md) now passes live server checks,
but migration journals and remaining runtime boundaries still need porting.
A separately verified legacy-layout copy preserves
post-migration work from both rehearsal formats; in-place reverse migration and
production recovery are not enabled.

Existing-project migration follows the same gated sequence: read-only preview →
quiesce with ownership → verified copy → atomic cutover → validation. Keep old
paths for compatibility/rollback. Removing those retained paths is a separate,
explicit consolidation operation; the initial copy will temporarily use more
space and leave both layouts present.

## Validation

The new layout tests verify exact legacy paths, workflow routing, pass grouping,
optional-data isolation, full IDs, path budgets, unchanged scene/frame numbers
and side-effect-free constructors. Run them with:

```sh
python tests/_storage_layout_unit_test.py
```

Validation on 2026-09-10:

- All 46 migration-plan baseline entrypoints completed successfully, plus the
  layout, inventory and inspector-controller entrypoints (49 total).
- The browser entrypoint's default mode only announces availability; it was also
  run explicitly with `--browser --storage`: 84 real-browser checks passed.
- The new layout suite contains 11 tests. Inventory byte/mtime preservation tests
  and processing deletion/resume tests passed with the shared path adapters.
- Tests used the local ComfyUI source checkout and a disposable `/tmp` Python
  environment with PyAV 17, satisfying that checkout's `av>=17` requirement.
  Production ComfyUI dependencies and live project files were not changed.
- `git diff --check` passed. No GPU rendering, native Windows migration or shared
  filesystem cutover was performed. Passing legacy tests does not mean V2 saves
  or migration are available.
