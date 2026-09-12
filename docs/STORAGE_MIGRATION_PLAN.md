# Chain storage simplification and compatibility plan

Status: read-only inventory delivered; legacy path centralization, organized
layout policy and a [copy-only relocation bridge](STORAGE_BRIDGE_REHEARSAL.md)
implemented for rehearsals, including opt-in organized payload reservations,
save/export fencing, reviewed workflow-copy handling and independent legacy
recovery of post-relocation work. Legacy control schemas and paths remain.
An isolated [control-state transaction rehearsal](STORAGE_CONTROL_STATE.md) now
versions exact documents under one atomic root. Actual branch load/save/fork/
default operations can use an explicit copy-only document port. A
[combined-store rehearsal](STORAGE_PROJECT_REHEARSAL.md) has now joined all
copied controls/media, tested pinned graph/catalogue reads, and recovered new
work to an independent V1 copy. Normal UI/node activation and remaining feature
transactions are outstanding. Real CIFS testing found a pointer-replacement
failure: that mount must not be qualified from local filesystem results.
An opt-in [immutable commit log](STORAGE_COMMIT_LOG.md) now passes actual server
CIFS tests and full-copy state/media checks. Copy migration/recovery journals
now use that protocol when explicitly selected, with interrupted-copy and
failed-marker recovery tests. Remaining runtime boundaries and production
preview/quiesce/cutover still need integration.
**No production V2 activation or migration yet**.
Date: 2026-09-11. Implementation baseline: nightly `326453d`.
Evidence: [chain storage audit](STORAGE_LAYOUT_AUDIT.md).
Current layout contract: [organized storage layout](STORAGE_LAYOUT_V2.md).

## 1. Objective and release policy

Make the project folder understandable and shallower without changing what a
saved scene, branch, cut, checkpoint or processing pass means. Existing projects
must remain usable without migration. Migration must be explicit, restartable,
verified and reversible at a defined boundary.

Work on nightly in small independently testable changes. Do not combine this
with generation algorithm changes, new upscale behavior or another branch-UI
redesign. Main receives a reviewed compatibility reader/guard before it receives
new-format writers or a migrator; publishing either branch is a separate action.

The central rule is:

> Separate the identity of saved work from its physical location. Preserve
> historical identities; resolve their locations through a validated adapter.

“All features preserved” is a release gate, not an assumption. Unsupported cases
block the relevant migration step; they must not silently fall back to another
take, discard settings, or disable a feature that previously worked.

### Explicit non-goals

- No automatic migration on startup, update, listing, branch selection or render.
- No automatic deletion of old takes, ALTs, exports or recovery records.
- No re-encoding of media, repacking of safetensors or dtype changes.
- No changing model/reference-node connections or external workflow settings.
- No project-folder rename in the initial migration. Keep `run_name` and the
  existing folder name; project renaming/import rebinding needs its own tests.
- No promise that an arbitrary old node-pack version can read or write V2.
- No global deduplication across projects, database server or hard-linked
  editable PNG sequences.

## 2. Compatibility invariants

These become executable assertions, not just documentation:

1. **Revision identity stays stable.** Preserve original scene IDs, revision IDs,
   parent links, attribution origins, recorded creation dates and save ordering.
   Never infer identity from a filename label, resolution or newest timestamp.
2. **Saved bytes stay stable.** Existing MP4/WAV/PNG/safetensors and immutable
   metadata retain their bytes. Do not rewrite historical JSON to normalize
   separators, remove redundancy or insert new paths.
3. **Generation inputs stay stable.** Preserve prompts, templates, exact seeds,
   steps, geometry, frame counts, reference descriptors and context recipes.
   Test seeds above JavaScript's exact-integer range without conversion through
   a floating-point number. Storage version must not enter generation hashes.
4. **Branch operations keep their meaning.** Switch restores authored state;
   assign changes only that branch's selected path; fork shares the prefix;
   empty branch has no generated fallback; project default does not redirect
   existing workflow pins or queued work.
5. **Final cut is separate from generation.** A selected ALT provides its picture
   and applicable prompt/seed for deferred processing. Original audio and later
   generation-context ancestry stay as currently specified. Preserve explicit
   `final_cut_branch_id` and current exact-path resolution rules.
6. **Pins remain pins.** Workflow-local selections and sealed chapter manifests
   preserve exact revisions and frozen cut semantics. Missing/corrupt pinned
   inputs fail; they never select the current active take as a replacement.
7. **Saved processing remains resumable.** Preserve source hashes, recipes,
   full/partial ranges, original scene/frame numbering and context dependencies.
   Starting at scene 5 with no dependency on 1–4 must remain supported.
8. **Availability is honest.** A marker/audio/tail checkpoint is not a full video
   latent. Capabilities survive migration; no feature gains an invented fallback.
9. **Retention stays at least as safe.** Preserve references from other branches,
   ALTs, snapshots, processing descendants, registered PNG owners and recovery
   state. A `supersedes` history edge remains distinct from a required input.
10. **Read-only stays read-only.** Browsing and local output selection must not
    write project files, claim ownership, adopt assets or migrate caches. An
    optional derived index is rebuilt only by an explicit authorized operation,
    or outside the project in a disposable application cache.
11. **Physical sharing does not change ownership.** Attributed takes or shared
    prefixes may refer to the same bytes without becoming the same logical take.
    Deleting one reference must not remove another's media.
12. **No hidden automation changes.** Reviews, candidate retention and handoff
    states keep their current behavior. Migration never auto-queues a render,
    approves a review or resumes an uncertain delivery.
13. **Ownership and path safety remain enforced.** Lookup aliases do not grant
    access to another project or authorize deletion. Reject traversal, unexpected
    roots, drive/ADS addresses and symlink/junction escapes as appropriate.
14. **Old behavior has an explicit support boundary.** Updated H3 readers can
    resolve legacy references. Arbitrary external tools that open literal paths
    require those files to remain physically present, or an explicit relink.

## 3. Target storage model

### 3.1 Keep relationships out of the directory hierarchy

The new model has six concepts:

| Record | Identity / responsibility |
|---|---|
| Project | Stable project ID, unchanged run name, active storage generation, default branch |
| Branch state | Authored Plan/settings, selected generation takes, editorial choices and revision/CAS token |
| Take | One original, ALT or processed result, with stage, parents, artifact roles and capabilities |
| Cut snapshot | Immutable chapter/project presentation, exact take choices, trims, placement and audio rules |
| Processing pass | Recipe, source cut/branch/chapter/range, result-take lineage and resume contract |
| Export | Deliverable files, numbering, owners, original/current hashes where applicable, publication state |

Branch, chapter and profile labels are metadata. Changing a label does not move
media. Original, ALT, DeRoPE, latent-upscale and pixel-upscale takes use the same
storage service, but retain distinct semantics and capabilities.

### 3.2 Organized physical layout

```text
<existing-project-folder>/
  storage.json                       # small atomic storage-generation pointer
  media/
    generation/<storage-id>/          # video/checkpoint/audio/overlap roles
    alternate/<storage-id>/           # ALT picture; original audio stays shared
    derope/<pass-id>/<storage-id>/
    latent_upscale/<pass-id>/<storage-id>/
    pixel_upscale/<pass-id>/<storage-id>/
    video_refine/<pass-id>/<storage-id>/
    custom/<pass-id>/<storage-id>/
  exports/
    png/<export-id>/                  # export.json + frame_00000001.png, etc.
    video/<export-id>/                # export.json + video.mp4/audio.wav/subtitles.srt
  project/
    takes/<storage-id>.json           # immutable descriptor and original identity
    takes/<storage-id>.prompt.txt
    passes/<pass-id>/                 # immutable recipe/source and resume states
    cuts/<cut-id>.json                # immutable chapter/project delivery snapshots
    assets/                          # essential imported reference media
    reference_cache/                 # essential saved conditioning objects
    recovery/<snapshot-id>/           # original recovery documents, byte preserved
    history/                         # prompt/branch authoring history and indexes
    reviews/                         # durable review state, not a preview cache
    roots/<generation-id>.json        # immutable state/index root
    branches/<branch-id>/<state-id>.json
    aliases/<map-id>.json             # durable compatibility maps
    legacy/<document-id>.json         # preserved historical document bytes
    jobs/<job-id>/                    # migration/publication journals and staging
    tombstones/                      # retirement/deletion receipts
    optional/                        # only genuinely disposable derived data
      previews/
      thumbnails/
      diagnostics/
```

The three main areas are **media, exports and project data**. Do not create empty
stage/support folders preemptively. Optional features do not scatter folders in
the project root. Recovery, references and saved conditioning are supporting data
but **not disposable**; they must not move into `project/optional`.

This supersedes the audit's initial flat `takes/passes/cuts/state` sketch. New
branch state goes under `project/branches`, not the occupied legacy
`branches/main.json`. `storage.json` is separate from authored Plan JSON. Do not
add storage settings to the Plan schema.

Media roles have fixed leaf names (`video.mp4`, `checkpoint.safetensors`,
`audio.wav`, `overlap.mp4`) only when that take actually owns those artifacts.
Capabilities, not filename presence, determine full-latent availability. Stage
comes from recorded generation/processing semantics, never from a profile label.
Keep outputs of a processing pass together; a later pass referencing an earlier
take does not copy it into its own folder. A combined upscale/DeRoPE pass retains
both operations in its recipe even though its primary stage is DeRoPE.

Whole-video workflows using third-party savers need an explicit export boundary.
For example, the shipped SeedVR2 full-chain workflow does not currently publish
H3 per-scene processing checkpoints. Do not pretend it has scene-level resume or
reroute arbitrary third-party saver paths implicitly. See the layout contract's
workflow coverage table.

Existing root names used by unknown integrations are never overwritten. Reserve
and validate new namespaces; collision is a preflight blocker. Initial upgrades
retain the old reference-cache layout and descriptors rather than also attempting
cache conversion during storage migration.

Use full collision-resistant IDs. A new storage ID may differ from the old
revision ID: legacy processing scopes can contain the same revision token, and
attributed records may share artifacts. Preserve every old identity in the take
descriptor and map it explicitly. Do not merge two records merely because their
short IDs, scene number or pixels match.

An artifact may be owned by one take and referenced by others. The service must
resolve that ownership without copying the payload into every take directory.
General content deduplication is deferred; preserve known existing sharing first.

For reference, `<project>/exports/png/<32-hex>/frame_00000001.png` removes branch,
chapter, profile and redundant `frames` folders from PNG delivery paths. Enforce a configurable
full-path budget, including staging suffixes and the actual output root; report
over-budget paths before writing. Do not silently truncate identity keys or user
labels. Human-readable naming remains available for explicit external deliveries.

### 3.3 Authority and indexes

- Immutable take/cut/pass/recovery records and durable alias maps are authority.
- `storage.json` atomically selects one fully published state-root generation.
  A logical multi-file change is prepared first and becomes visible through one
  root-pointer commit, rather than several independently changing branch files.
- Root records select branch-state versions, pass-state versions and current
  alias/index generations. Unchanged immutable files are reused.
- Keep three counters distinct: the storage epoch fences migration/maintenance;
  the root generation advances for committed state changes; each branch/pass
  retains its own optimistic-concurrency revision. A save on another branch
  must not invalidate a queued job's storage epoch. Concurrent root publication
  retries merge only unchanged, independently validated branch/pass updates.
- Search/UI caches are disposable and rebuildable. They cannot authorize
  deletion, replace a missing authority record or choose a different take.
- Durable alias maps are **not** disposable caches. Historical bytes alone may
  not reveal relocated files after old paths are removed. Back them up and pin
  them with the records they serve; missing maps must fail visibly.
- Use regular JSON/files initially. Do not require SQLite WAL or a daemon on the
  shared output mount. Any later local accelerator stays non-authoritative.

## 4. Compatibility layer before new writers

Proposed modules and responsibilities; names can be finalized in the schema PR:

| Module | Responsibility |
|---|---|
| `storage_layout.py` | Pure organized-layout paths, stage/workflow routing policy and full-path budgets; no activation or writes |
| `storage_contract.py` | Versioned record schemas, IDs, artifact roles and capabilities |
| `storage_legacy.py` | Existing layout discovery and exact legacy-document adapters |
| `storage_resolver.py` | Validated logical/legacy lookup to a physical file |
| `storage_state.py` | Atomic root/state publication, revision checks and write fencing |
| `storage_inventory.py` | Read-only inventory, usage and dependency summaries |
| `storage_migration.py` | Preview, copy, verification, cutover, restart and rollback |
| `storage_retention.py` | Typed references, deletion previews, quarantine and receipts |

Keep modules bounded; avoid another all-purpose path utility with implicit branch
context. Existing functions become compatibility facades during conversion.

### 4.1 Read path

1. Determine project and storage generation explicitly. No marker means legacy;
   an unknown/newer marker is an error, not permission to fall back to legacy.
2. Validate the requested logical ID or legacy address and its expected scope.
3. For legacy input, read the original document bytes and validate the existing
   identity/hash contract unchanged.
4. Resolve each artifact through the committed alias map, including expected
   role, owner and recorded content identity. Avoid basename searches.
5. Open a verified physical path for the actual operation. Pass real paths to
   ffmpeg, safetensors, image readers and ComfyUI video APIs; those tools cannot
   resolve an H3-only virtual alias by themselves.
6. Keep logical addresses and original hash inputs in execution carriers.
   Physical location must not be injected into a legacy source fingerprint.

Avoid a global monkey-patch of `open()` or `Path`. Audit and convert direct path
checks, directory scans, media endpoints, preview URLs and deletion routines.
Replacing `_absolute_output_path()` alone is insufficient.

### 4.2 Old workflows and third-party consumers

Preserve node class names, socket types, serialized widget positions and current
legacy `selection_json` fields. New storage references are optional versioned
fields behind the existing carriers; old selections resolve through the adapter.
Test API prompts as well as frontend workflow JSON, embedded recovery workflows
and workflows reopened after migration.

The compatibility contract distinguishes:

- **Updated H3 readers:** resolve old manifests and pins using the alias map.
- **Core/third-party nodes receiving paths from H3:** receive an existing physical
  path at execution. Browser file requests must likewise use a verified endpoint
  or physical output address, not an unresolved legacy string.
- **External literal-path consumers:** unchanged only while that literal path
  remains present. Keep existing external PNG/video deliveries by default.
  Provide explicit relinking reports if a user opts to move them later.
- **Old node-pack binaries:** unsupported for V2 writes. A bridge release can
  reject them cleanly; software predating that guard cannot be made safe by a
  new marker it never reads. Operational isolation is required during migration.

Do not promise that path aliases make arbitrary external tools compatible.

### 4.3 Legacy hash and address handling

Keep two separate representations: original validation documents and resolved
runtime file handles/paths. Preserve original chapter IDs, source-manifest hashes,
per-scene source contracts, PNG owner keys and restore snapshots. Do not recompute
legacy identity using relocated addresses.

Legacy path-shaped validators must validate the **logical legacy address** and
its identity witness, then separately validate the resolved physical target.
Checking a V2 file against a V1 `upscaled/<profile>/checkpoints/clip_...` pattern
would wrongly reject a valid migration. Conversely, accepting an arbitrary
target solely because an alias exists would weaken the existing safety contract.

Normalize Windows separators only for lookup/comparison where already allowed;
preserve saved bytes. Map by exact validated legacy address plus project/scope
and identity witness, not a filename pattern. Conflicting witnesses block the
mapping. Migrated mutable aliases have a defined current-state mapping, not
permanent mappings to their pre-migration selected take.

This distinction includes canonical `plan.json`, `workflow.json`, `api_prompt.json`
and current manifests, not just `clip_NNNN.json`. Preserve their existing scoped
recovery semantics through the adapter. Immutable recovery snapshots resolve to
their recorded bytes; mutable mirrors resolve to the appropriate current branch
state. Never silently convert a mutable legacy reference into a frozen snapshot
or an immutable reference into a moving pointer.

An immutable alias cannot redirect to newer content. A missing mapped file must
not trigger a search for another copy or active revision. An explicitly retained
verified replica may be used only under a recorded replica policy, not by guessing.

## 5. Feature coverage and acceptance tests

Run the same fixture through the legacy reader and the candidate reader, then
compare semantic outputs, exact source identities and the expected filesystem
effects. Approved differences are storage locations and new-format bookkeeping,
not prompts, selected media, dependencies or eligibility to resume.

| Feature | Must remain unchanged | Existing test anchors to extend |
|---|---|---|
| Original generation/checkpoint save | Per-scene commit, original bytes, prior take retention | `_checkpoint_revision_unit_test.py`, `_manifest_recovery_unit_test.py` |
| Branch switch/fork/empty/default | Authored state and exact selections; shared media; no fallback | `_working_branches_unit_test.py`, `_studio_branch_switch_js_test.mjs`, `_working_branches_recovery_js_test.mjs` |
| Assignment and attribution | Chapter-only assignment and original recovery provenance | `_checkpoint_activation_unit_test.py`, `_checkpoint_attributed_chapter_output_unit_test.py`, `_branch_authoring_recovery_unit_test.py` |
| Prompt/seed recovery | Exact prompt text, overrides, seed precision and stale-write checks | `_working_branch_scene_one_recovery_unit_test.py`, `_prompt_history_unit_test.py`, `_prompt_history_js_test.mjs` |
| Local pins and graph UI | Exact output; browsing/zoom do not modify it; no project writes | `_checkpoint_local_output_unit_test.py`, `_checkpoint_local_output_js_test.mjs`, `_checkpoint_manager_browser_js_test.mjs` |
| ALT presentation/upscale | Correct branch's ALT picture/prompt/seed; original audio and ancestry | `_alternate_take_unit_test.py`, `_upscale_alternate_unit_test.py`, `_checkpoint_final_cut_js_test.mjs` |
| Chapters and frozen cuts | Mixed geometry, scene clocks, unchanged seal identity and retirement | `_checkpoint_chapter_output_unit_test.py`, `_chapter_recovery_safety_unit_test.py` |
| Visual/audio continuity | Exact predecessor and selected context blocks, including audio-only edges | `_checkpoint_context_dependency_unit_test.py`, `_checkpoint_branch_selection_unit_test.py` |
| Processing/resume | Pixel, latent, DeRoPE, split/LMS paths; sparse starts; capability checks | `_upscale_chain_unit_test.py`, `_upscale_range_unit_test.py`, `_upscale_pixel_unit_test.py`, `_checkpoint_variants_unit_test.py`, `_lms_upscale_unit_test.py` |
| Processing deletion | Shared ancestors protected; independent outputs deletable; owned PNG cleanup | `_processing_checkpoint_delete_unit_test.py`, `_checkpoint_processing_branches_js_test.mjs` |
| Both PNG exporters | Numbers, bit depth, `_2` variants, edited pixels, custom paths and recovery | `_png_export_unit_test.py`, `_png_video_export_unit_test.py`, `_chapter_incremental_export_unit_test.py` |
| Recovery archives | Exact snapshot, legacy root fallback where currently permitted, no invented seed | `_checkpoint_legacy_archives_unit_test.py`, `_legacy_checkpoint_adoption_unit_test.py` |
| Reference conditioning | V1/V2/V3 cache reads, object sharing, source recovery and retirement gates | `_reference_cache_recovery_unit_test.py`, `_reference_cache_objects_unit_test.py`, `_reference_cache_migration_unit_test.py` |
| Imported assets/source timeline | Input bindings, backup recovery, exact frozen tracks and timing | `_project_asset_store_unit_test.py`, `_project_asset_manager_unit_test.py`, `_source_timeline_unit_test.py`, `_source_timeline_consumers_unit_test.py` |
| Review/requeue | Pending review and candidate retention; no duplicate or automatic uncertain handoff | `_handoff_state_unit_test.py`, `_requeue_ownership_unit_test.py`, `_top_level_requeue_unit_test.py` plus dedicated review fixtures |
| Ownership/portability | Read-only workflows, scope isolation, Windows addresses and escape rejection | `_project_ownership_unit_test.py`, `_artifact_paths_unit_test.py`, `_processing_persistence_unit_test.py` |

Names in the last column are under `tests/`; they are starting points, not a claim
that those tests already prove migration compatibility. Add storage-version
parameterization and new tests where existing fixtures bypass real I/O.

### New regression fixtures required

- Synthetic project reproducing the reported situation: old Original path,
  seven-scene 960×544 path, remade scenes 6–7, selected scene-1 ALT, multiple
  processed passes, chapter boundary, changed trims and an empty retained branch.
- Preserve a second unrelated workflow's local pin while switching the Plan.
- Include seeds larger than `2^53`, Unicode prompts, both path separators,
  same-number scenes across branches, attributed shared media and deliberately
  colliding legacy revision tokens in different processing scopes.
- Original/ALT audio latents and delivered WAVs must remain separately correct.
- A pixel-only take has no full latent; a dependent context tail may still be
  required. Capability errors must match before and after migration.
- Include both PNG schemas, existing `_2` exports, hand-edited PNGs, shared
  owners, an export tombstone, an interrupted publication and a custom destination.
- Include retired/sealed cuts, prompt-history drafts, missing optional previews,
  legacy mutable archive references, missing required media and unknown files.
- Record a synthetic dependency graph with historical `supersedes` edges that
  must not become false deletion blockers.

Use CPU fixtures and stubbed encoders for broad tests; use a small real-media
integration set to exercise decode, mux, PNG and safetensors readers. Do not
regenerate the user's large project as a test. GPU smoke renders are explicit
release validation on disposable projects, not a prerequisite to inspect data.
Compare saved-file bytes and loaded sampler inputs exactly; do not mistake
cross-platform sampling nondeterminism for a storage identity change.

## 6. Implementation phases and exit gates

Each phase is separately reviewable and can stop without forcing the next.

### P0 — Freeze behavior and establish fixtures

Initial implementation: see [Storage Inspector](STORAGE_INSPECTOR.md) for the
read-only inventory, first composite fixture and consumer checklist. The full
migration fixture matrix and P0/P1 exit gates are not yet complete.

Tasks:

- Record current public wire formats, path constructors, direct file accesses,
  mutation/lock ordering and generation/hash inputs.
- Build the composite fixtures above and a feature-consumer checklist covering
  Python nodes, HTTP handlers, frontend serialization and external media paths.
- Capture baseline outputs and dependency/deletion decisions for each fixture.
- Document supported legacy versions and expected errors for broken records.

Gate: baseline tests pass; no production behavior, writes or paths change.

### P1 — Read-only Storage Inspector and isolated safety fixes

Tasks:

- Show categories: takes, processing, exports, recovery, assets, conditioning
  cache, preview cache, state and unclassified files.
- Show logical versus allocated size where measurable, shared references,
  longest paths, owner branch/pass, source revision, full-latent availability,
  migration readiness and concrete deletion blockers.
- Separate “recoverable from cache/source”, “not verified”, “missing”, “edited”,
  “retired”, “shared” and “unreferenced candidate”. None authorizes deletion.
- Keep listing free of tensor loads and full media hashing. Offer a separate
  read-only deep verification action with progress and cancellation.
- Fix assembly temporary-name isolation in a separate change: per-job staging,
  atomic output reservation, independent cleanup and interruption recovery.

Gate: inspector creates no files in protected projects; ordinary listing does
not decode media. Two overlapping assembly jobs cannot delete each other's
temporary files. No storage migration is included.

### P2 — Centralize existing storage without moving it

In progress: `storage_legacy.py` now supplies generation/pointer/archive/chapter
paths, processing profile directories and both PNG export roots. Processing
catalogue/deletion directory lookups and DeRoPE profile validation share the
legacy boundary. `storage_layout.py` defines the organized physical policy, but
does not activate it. See [implementation scope](STORAGE_LAYOUT_V2.md).

Tasks:

- Implement typed legacy project, branch, take, pass, cut and export lookups.
- Route generation, aliases, archives and final-cut resolution through them.
- Route processing discovery/resume/deletion and both PNG exporters next.
- Route references, assets, review/handoffs and browser preview endpoints.
- Replace exact directory-shape tests with equivalent typed validation where
  appropriate; do not weaken confinement or ownership checks.
- Keep compatibility facades and current file/wire outputs unchanged.

Gate: all existing tests plus P0 fixtures pass; legacy output document bytes and
fingerprints are unchanged where previously stable. No old source reader is
left relying on an untracked path join or raw directory scan.

### P3 — Versioned state, alias records and bridge support

Tasks:

- Finalize schemas with independent layout, reader-compatibility and record
  versions. Add explicit unknown-version rejection.
- Implement immutable state-root publication and branch compare-and-swap using
  existing project synchronization/ownership rules.
- Add a storage epoch to operational jobs/leases, never to authored generation
  settings or source fingerprints. Recheck epoch before publishing results.
- Add a maintenance/read-only gate to every mutating node, API and handoff path.
- Implement exact-address aliases, original-document preservation and physical
  file resolution for all media consumers.
- Add an optional catalogue-only import that does not move files or change
  which store is authoritative. Its index must detect stale legacy state.
- Prepare the bridge release needed to recognize V2/maintenance state before
  allowing V2 projects in normal use. Older unguarded binaries remain excluded.

Gate: V1 remains fully supported; unknown/newer state fails cleanly; stale jobs
cannot commit across an epoch change; simulated lost acknowledgements are
reconciled by operation IDs instead of duplicated writes.

### P4 — V2 writers for new disposable projects

Tasks:

- Enable V2 only through explicit new-project creation on nightly.
- Implement take/pass/cut/export publication and thin branch state using the
  same service and dependency model as the V1 adapter.
- Preserve authored-state recovery, full checkpoint capabilities and original/ALT
  semantics. Keep pass recipes and source identities immutable.
- Standardize new internal naming without changing existing PNG suffixes or
  filenames in migrated legacy exports.
- Keep preview cache, orchestration, tombstones and staging distinct from media.
- Test export/import of a disposable project to a different output-root prefix.

Gate: entire feature matrix passes on V1 and V2, including real-media integration.
No migrations of existing projects are enabled yet. No independent V1/V2 dual
writers: each project has one authoritative writer path.

### P5 — Explicit migration in retain-legacy mode

Tasks:

- Implement the preview/verification/state machine in section 7.
- Copy and verify into new storage while retaining old files at their old paths.
- Preserve legacy bytes and exact-address maps; import all branches, not just
  the currently active or visible path.
- Migrate on synthetic clones, then an explicitly authorized backup clone of a
  representative real project. Do not silently reuse it as a live target.
- Exercise every crash boundary and rollback branch in section 8.

Gate: same semantic inventory and feature results before/after; every moved
payload hash matches; legacy bytes remain present; restart is idempotent; a
pre-cutover failure leaves V1 usable; post-cutover status is never ambiguous.

### P6 — Release compatibility and pilot real projects

Tasks:

- Verify frontend/backend version handshake; invalidate stale UI capabilities
  and restore bindings after migration without losing unsaved authoring drafts.
- Publish supported-version and rollback instructions, backup requirements,
  space estimates and explicit external-path limitations.
- Offer dry-run and clone-migration UI before in-place cutover.
- Run native Windows tests and Linux tests, plus the actual shared-filesystem
  deployment class where migration will be offered.
- Promote reviewed compatibility/new-format code to main only after the gates
  pass and release is authorized. Do not merge unrelated nightly experiments.

Gate: supported workflows open and execute unchanged on migrated data; the user
can identify current state and rollback boundary; no outstanding high-severity
storage-integrity or missing-consumer findings.

### P7 — Optional layout consolidation and cleanup

Tasks:

- Reinventory dependencies and external-path contracts; collect fresh approval.
- Move legacy metadata bytes into the protected legacy archive and preserve
  aliases where consumers no longer require their literal old file paths.
- Consolidate old media only when all applicable readers resolve it correctly.
  Keep external deliveries in place by default; report which consumers require
  relinking for a fully tidy tree.
- Quarantine approved old copies/unreferenced records with an undo manifest.
  Hold rollback and compatibility roots until explicitly released.
- Keep existing reference-cache conversion a separate operation with its own
  successful-render retirement receipts; do not trigger it as incidental cleanup.

Gate: cleanup cannot delete another owner's data, resurrect deleted PNGs, erase
hand edits or invalidate a retained pin. A second confirmation is required for
permanent removal. No orphan deletion based solely on age, suffix or visibility.

## 7. Migration procedure for an existing folder

### 7.1 Supported modes

| Mode | Physical changes | Purpose / compatibility |
|---|---|---|
| Inspect / dry-run | None | Complete manifest of proposed work and blockers |
| Catalogue-only | Explicit sidecar/index creation; no media moves | Improve UI and prepare mapping while V1 stays authoritative |
| Retain-legacy cutover | Verified V2 data/state created; old paths retained | Default migration; updated H3 uses V2; old files provide rollback and literal-path compatibility |
| Consolidate | Separately approved retirement/quarantine of old paths | Shallow final tree; requires external consumer assessment and accepted rollback limits |

The retain-legacy phase intentionally uses more space and does not immediately
make the physical folder pretty. It buys a verification/rollback window. The
Inspector presents the logical view without implying those old files are trash.

### 7.2 Durable state machine

```text
previewed → quiesced → copying → verified → ready → committed → validated
                ↘ abort/retry before commit: V1 remains authoritative
validated → optional consolidation preview → quarantine → optional purge
```

The journal records the operation ID, project ID, old/new root and epoch,
inventory digest, mapping digest, source/destination/size/hash for each artifact,
completed-copy receipts, verification results, backup identity and commit state.
It records only the data necessary for recovery; do not log prompt text, API
secrets or full workflows in public status output.

### Step 1 — Inventory and classify

- Enumerate every legacy layout, all working branches, inactive takes, ALTs,
  branch-authoring backups, prompt history, chapter seals/retirements, partials,
  passes, PNG variants, caches, assets and durable review/handoff state.
- Read referenced manifests recursively, including legacy root recovery files,
  attributed shared artifacts and nested presentation/processing sources.
- Record external registered PNG directories and input-side assets without
  recursively sweeping unrelated output/input folders.
- Resolve collisions and ambiguities explicitly. Unknown integration files stay
  untouched and are reported. Missing optional previews may be rebuildable;
  missing required input or conflicting immutable identity blocks full migration.
- Record filesystem metadata and deep hashes needed for the operation. A file
  with no historical checksum gets a new transfer checksum, not an invented
  claim that its generation provenance has been verified.

Preview output: exact file count/bytes by action, longest resulting paths,
required free space, incompatible consumers, unresolved dependencies, planned
compatibility aliases, preservation list and rollback boundary.

### Step 2 — Backup and quiesce

- Require a verified backup or snapshot on separate recoverable storage; a
  second hard link on the same filesystem is not a backup.
- Save branch authoring and the workflow; retain browser-local drafts and stale
  revision bindings without publishing them over newer server state.
- Finish or deliberately stop active jobs through normal controls. Drain or
  cancel queued work only with the user's direction. Suspend automatic requeue.
- Reconcile pending checkpoint/PNG transactions and uncertain deliveries using
  their existing recovery mechanisms. Do not migrate an unresolved transaction.
- Acquire the project maintenance fence with existing ownership checks. Keep
  current lock ordering; audit new lock acquisition for deadlocks. Register jobs
  under an epoch and check it again at publication, so a late old job cannot
  write after cutover.
- Stop unguarded older processes and external writers. The software cannot prove
  safety against an unknown program that ignores the maintenance fence. Recheck
  the inventory before committing and stop on changes.

Migration cancellation never automatically cancels a render, deletes a review,
changes ownership, or requeues a pending scene.

### Step 3 — Copy and prepare

- Create per-operation staging on the destination filesystem. Copy immutable
  artifacts in streaming chunks; checkpoint/tensor loading is unnecessary.
- Default to independent copies. An optional verified copy-on-write filesystem
  clone can reduce space only where tested; do not assume support. Avoid hard
  links between retained legacy and V2 files in the default migration, especially
  for editable exports, mutable aliases or any path an older writer can modify.
- Preserve original timestamps where practical; store original recorded dates
  explicitly so save ordering never changes to migration time.
- Preserve historical JSON bytes and all needed V1 hash inputs. Build separate
  V2 records referencing them; never overwrite the originals with converted JSON.
- Import legacy mutable pointers as captured branch state. Define their
  post-cutover logical mapping to current V2 state; the physical old files remain
  frozen baseline copies and are not authoritative after cutover.
- For manually edited PNGs, preserve current bytes plus original recorded hashes
  and the existing edited/conflict status. Do not “repair” hashes to hide edits or
  restore saved pixels over them. Leave custom external destinations unchanged.
- Preserve reference-cache object mappings and ownership without converting
  bundles. Preserve actual full/missing latent capabilities.
- A stale or partial copy is never a published artifact. Verify each destination
  before marking it copied; resume by journal identity, not filename existence.

Space estimate must include retained originals, copied payloads, metadata and
staging headroom. The audited project's 21.27 GiB is an example, not a universal
free-space requirement. Recalculate for the actual chosen files and filesystem.
If space is insufficient, offer catalogue-only or a separate destination/backup;
do not silently switch to an in-place destructive move.

### Step 4 — Verify before activation

- Check source stability and every copied payload's hash; revalidate changed
  timestamps/stat identities rather than trusting cached checksums blindly.
- Compare counts, branch assignments, authored settings, exact seeds, cuts,
  dependencies, capabilities, pass contracts and export inventories.
- Resolve all V2 and legacy aliases and verify project/role boundaries. Scan for
  unresolved or ambiguous references. Validate old snapshots with old rules.
- Run representative read-only operations through both adapters: select a pin,
  load a checkpoint, resolve an ALT, rebuild conditioning descriptors, resume a
  processed range, inspect a chapter seal and build deletion previews.
- Compare all active and retained reference roots. A missing required dependency
  blocks cutover; an optional-cache exception must be explicit in the report.
- Persist the verified state-root/mapping documents before `ready` is reported.

### Step 5 — Atomic cutover

- Under the maintenance fence, check ownership, project epoch and inventory
  again. Publish one authority transition to the prepared immutable state root.
  For the tested CIFS mount this must use a write-once bootstrap plus immutable
  commit records; never replace an occupied `storage.json`. Migration gate and
  journal transitions must use the same no-overwrite discipline.
- Use flush/fsync/publication appropriate to the tested filesystem; do not
  claim durability based only on a successful rename. A failed acknowledgement
  leaves an uncertain commit requiring reread and reconciliation.
- Before this commit, V1 is authority; after it, V2 is authority. Never allow
  readers to combine half of each mutable state. Missing/corrupt V2 state after
  a V2 marker is an error, not an automatic fallback to stale V1.
- On restart, inspect the marker and journal to determine which side committed.
  Retry the same operation ID; never create a second migration or duplicate take.

### Step 6 — Validate and release

- Reopen via normal H3 nodes/UI and repeat the acceptance checks while writes
  remain fenced. Keep a concise before/after verification receipt.
- Refresh storage-generation bindings in connected workflows. Unsaved drafts
  remain available, but stale pre-migration mutation tokens must be rejected.
- Release maintenance only after validation passes. Resume reviews/handoffs
  manually with the same validated identities; never auto-replay old queued work.
- State clearly that old literal-path files are retained baseline copies; they
  do not receive new V2 branch changes. Do not run old writers against them.
- Do not compact or delete anything at the end of migration.

## 8. Rollback, interruption and uncertainty

| Situation | Required behavior |
|---|---|
| Cancel/fail before root-pointer commit | V1 remains authoritative. Retain journal; clean only verified operation-owned staging after confirmation or safe retry |
| Crash during file copy | Resume incomplete copies from journal; validate complete ones; never assume a same-name destination is correct |
| Rename succeeded but response/fsync failed | Reread marker/root/receipt under the fence; report committed, uncommitted or blocked—never guess or delete published data |
| Crash after commit, before validation | Keep V2 writes fenced; complete validation or roll back to the retained V1 baseline |
| Roll back before any V2 edits/generation | Repoint under the fence to verified retained V1 state; keep V2 artifacts/journal for investigation |
| Roll back after new V2 work | A pointer flip would hide new work. Stop, preserve all V2 data, and offer export of that work or separately implemented reverse migration; no automatic rollback |
| Old path was consolidated/purged | In-place V1 rollback is no longer promised. Restore from the verified backup or an explicit retained compatibility copy |
| External source changes during migration | Invalidate readiness, retain both copies if needed, and request a fresh preview after writers are stopped |
| Disk full, permission error, disconnect or unsupported durability | Fail without changing authority; retain enough journal/state to reconcile any uncertain commit |

Never mark a project “migrated” merely because copying finished. Never claim
“rollback supported” without stating whether post-cutover work is included.

## 9. Cleanup and long-term retention

Use a typed reference graph: required input, selected assignment, frozen-cut pin,
processing dependency, external compatibility pin, recovery pin, historical-only
edge and regenerable preview. Do not treat every path string as an equivalent
dependency or every unreferenced cache as immediately deletable.

Deletion flow: fresh preview → confirmation → revalidate under the project lock
and storage epoch → transactional metadata update/quarantine → undo receipt.
Permanent purge is a separate confirmed operation. Quarantine on another volume
requires verified copying; a rename assumption is not portable across volumes.

Compatibility aliases do not make stale workflows discoverable: arbitrary saved
workflow files may live outside the project. Therefore retain migrated legacy
targets by default until the user explicitly releases that compatibility root.
Unknown consumers/files are protected, not silently declared orphaned.

Preserve the current behavior for deliberate deletion of a processed take and
its exclusively owned PNGs, including edited pixels; migration itself never
performs that deletion. Shared or legacy-unattributed frames remain protected.
Tombstones cannot be replayed as copy recipes or resurrect retired exports.

For explicit catalogue cleanup of an owned payload that is already missing, or
an exclusively owned PNG that was edited, record its exact observed condition in
the quarantine receipt alongside the original accepted descriptor/hash. Recheck
both under the publication fence. Do not normalize the output hash, accept an
unreadable file as missing, or extend PNG edit permission to arbitrary model or
checkpoint bytes. Such cleanup removes the current catalogue reference; it does
not physically purge data. Undo restores the pre-deletion catalogue and observed
condition: edited bytes stay edited and missing bytes remain missing. Surface
that limitation in preview/undo results, and reject undo if the retained physical
state changed again. Ordinary integrity verification must still report the
original mismatch after undo. A previously missing metadata control cannot be
used to infer ownership; it must first be recovered or handled by a separately
validated repair operation.

## 10. Release gates and operational validation

Before enabling existing-project migration:

- Every feature row in section 5 has a passing V1/V2/migrated fixture or an
  explicit supported-case restriction that blocks unsupported migrations.
- Exact legacy document bytes, IDs and recorded hashes survive migration.
- No source/settings/resume hash changes solely because a file moved.
- Native Windows and Linux filesystem tests cover long paths, locked-open files,
  Unicode/case differences, separator normalization, junctions/symlinks and crash
  recovery. Shared-filesystem support requires testing that deployment class;
  local POSIX success alone is insufficient.
- Inject failure before/after each publish, directory sync, pointer commit,
  metadata update and journal receipt. Restart must be idempotent.
- Exercise concurrent save/delete/export/migrate attempts and stale queued jobs.
- Deletion tests prove both protection and legitimate removability: safety must
  not reintroduce phantom branches or permanently undeletable abandoned outputs.
- Listing must not hash/decode large media. Benchmark equal inventories on the
  same machine/filesystem; investigate material regressions before release.
- Back up and restore a migrated disposable project. Verify current branch,
  authoring, ALT, source audio, reference objects, frozen chapters and resumed
  processing. Preserve model/plugin dependencies as an external inventory.
- Produce machine-readable validation receipts and human-readable migration and
  rollback summaries without exposing private prompt/workflow data in logs.

### Completion criteria

The project is not complete at “new folders created.” It is complete when:

1. Existing unmigrated projects continue working normally on supported releases.
2. New V2 projects support the full feature matrix through one storage service.
3. Existing projects can migrate through the explicit verified state machine.
4. Old pins/manifests work through the supported reader without changed meaning.
5. Rollback and consolidation boundaries are clear and tested.
6. Users can inspect ownership/retention and find deliverables without decoding
   branch/chapter/profile directory conventions.

## 11. Implementation status and next gates

The **P0 + read-only P1 slice** delivered an initial fixture, Storage Inspector,
inventory format and consumer checklist in `326453d`. The organized physical
policy and initial P2 adapters follow without changing existing paths.

Finish P2's remaining consumers and the isolated P1 assembly-staging fix. Complete
the fixture matrix and versioned/alias/write-fencing gates before enabling fresh
V2 projects, followed by explicit existing-folder migration. Do not infer that
these gates are complete from passing legacy regressions. No automatic migration
or cleanup is enabled by either foundation batch.
