# Chain storage audit

Date: 2026-09-10. Code baseline: nightly `62948e3`.

## Conclusion

The complexity is real, but the main problem is **mixed ownership and path-based
identity**, not excessive recovery JSON. The filesystem simultaneously represents
project ownership, mutable branch assignments, immutable generations, chapter
deliveries, processing recipes and export versions. Different readers reconstruct
these relationships from different directory shapes.

Keep immutable revisions, exact source lineage, recovery snapshots, original/ALT
separation and deletion protection. Simplify where they live and how they are
addressed. Do not shorten this layout by deleting history or manually renaming
existing directories.

Recommended direction: **one project-wide take store; branch/chapter/pass
relationships in metadata; shallow export directories; explicit housekeeping.**

## Scope and evidence

- Inspected generation, branch, chapter, processing, PNG, reference-cache,
  recovery, asset and deletion code.
- Read-only filesystem inventory of the mounted `output/h3_chains`: 5,493 files.
- Detailed metadata inspection of one representative project: 4,779 files and
  367 JSON documents. Project names, prompts, workflow contents and source asset
  names are deliberately omitted here.
- Sizes below are logical file sizes; GiB/MiB use powers of 1024. Allocated space
  was also checked and was close to logical size on this mount.
- No media was moved, deleted, rewritten or rendered. No migration was run.
  Only this report and its documentation index entry were created in the repo.
- This was a live observational inventory, not a locked backup snapshot. JSON
  parsing succeeded for the inspected project. That does **not** establish that
  every media hash, dependency or model needed for recovery is intact.
- Large media files were not rehashed or decoded. Cross-file JSON duplication
  was checked by exact byte hashes; PNG comparisons used existing saved hashes.

## 1. What is on disk

All inspected chains occupy **40.57 GiB**. The representative project occupies
**21.27 GiB**:

| File type | Count | Logical size | Interpretation |
|---|---:|---:|---|
| PNG | 4,131 | 10.65 GiB | Mostly exported frame sequences; also asset images |
| Safetensors | 82 | 8.58 GiB | Generation/processed checkpoints and reference tensors |
| MP4 + MKV | 81 | 1.85 GiB | Saved segments, previews, final/partial deliveries and assets |
| WAV | 46 | 160.47 MiB | Generated/source/processed audio and delivery sidecars |
| JSON | 367 | 29.28 MiB | Branches, pointers, provenance, manifests and recovery |
| Other | 72 | About 1.54 MiB | Prompts, subtitles, other images and housekeeping |

PNGs and safetensors are approximately **90.4% of the bytes**. JSON is about
**0.13%**. Removing JSON would barely change storage use while potentially
destroying exact recovery.

Measured root-level subtree sizes in that project:

| Subtree | Size | Important distinction |
|---|---:|---|
| `checkpoints/` | 7.53 GiB | Original/ALT latent payloads, immutable JSON and active aliases |
| `chapters/` | 5.83 GiB | Includes chapter-scoped processing, PNGs and deliveries |
| `frames/` | 5.09 GiB | Project-level PNG exports, outside chapter processing |
| `final/` | 1.28 GiB | Includes review partial videos as well as final exports |
| `reference_cache/` | 1.03 GiB | Recovery/conditioning inputs, not disposable UI cache |
| `project_assets/` | 231.53 MiB | Backup of imported project assets |
| `branches/` | 97.56 MiB | Branch authoring, pointers and other branch-local products |

These subtree numbers overlap the file-type table; they must not be added to it.

### Path lengths

- Longest observed absolute path across chains: **244 characters**.
- Longest in the representative project: **222 characters**.
- Deepest observed file: **9 components below `h3_chains`**, including its name.
- The longest overall example was an older imported reference: a long project
  name plus a 64-character hash plus the original image name.
- A long representative-project path was a PNG routing receipt:
  `P/chapters/01_chapter_01/upscaled/<profile>/frames/<export>/.png_variants/<64-hex>.json`.
- Named-branch scoping adds `branches/<32-hex>/`, another **42 characters**.
  Temporary UUID suffixes can add still more while writing.

These are measurements on the current Linux mount, not a Windows compatibility
test or a universal path-length limit. A different output prefix, longer labels
or staging paths change the total. Current name sanitization commonly caps each
name at 96 characters, not the entire constructed path.
See [name handling](../chain_nodes.py#L694),
[branch directories](../branch_scope.py#L40) and
[PNG variant routing](../png_export_variants.py#L23).

## 2. Current folder map

`P` means `output/h3_chains/<project>`. Optional folders need not exist in every
project. This map distinguishes the storage contract from current disk contents.

| Location | Purpose | Ownership / lifecycle |
|---|---|---|
| `P/plan.json`, `workflow.json`, `api_prompt.json` | Current recovery mirrors | Original branch; not immutable per-take truth |
| `P/editorial.json`, `plan_studio_presentation.json` | Cut choices and Studio presentation | Mutable Original-branch state |
| `P/segments/clip_NNNN.<revision>.mp4` and `.prompt.txt` | Delivered picture and prompt sidecar | Immutable, shared between working branches |
| `P/checkpoints/clip_NNNN.<revision>.safetensors` and `.json` | AV checkpoint and authoritative take metadata | Immutable, shared; originals and picture-only ALTs coexist |
| `P/checkpoints/clip_NNNN.json` | Assigned take for a scene | Mutable Original-branch alias containing a metadata copy |
| `P/generated_audio/`, `blend_segments/` | Sound and optional overlap-preserving picture | Revision-associated media, not miscellaneous copies |
| `P/recovery_archives/<revision>/` | Exact Plan/workflow/API snapshot | Immutable generation recovery |
| `P/branches/main.json`, `branches/default.json` | Original authoring record and project default | Small mutable branch-control records |
| `P/branches/<branch-id>/` | `branch.json`, assigned checkpoint aliases, editorial, recovery mirrors, history and branch-local products | Named branch; generation media remains shared in `P` |
| `P/branches/authoring_backups/<branch-id>/` | Previous branch-authoring documents keyed by digest | Retained recovery history, not additional generated clips |
| `<branch-root>/manifest.json` | Published source scene selection | Mutable completed-run manifest; not an immutable take object |
| `<branch-root>/prompt_history/<scene-id>/` | Prompt revisions and index | Authoring history; branch-scoped |
| `<branch-root>/reviews/`, `pending_reviews/` | Preview media and saved review state | Mixed derived previews and retained recovery records |
| `<branch-root>/partial/` | Through-scene manifests | Mutable named snapshots; corresponding review videos go under `final/` |
| `<branch-root>/alternates/` | Alternate-only manifests | Not the authoritative storage location for ALT media |
| `<branch-root>/chapters/<number>_<id>/manifests/` | Sealed chapter/cut snapshots | Immutable recovery pins on the referenced takes |
| Same chapter's `retired_manifests/` | Retired snapshot receipts | Retained history; no longer active chapter recovery pins |
| `<branch-root>/upscaled/<profile>/` | Processed scenes and manifests | Recipe/pass output namespace; also used for DeRoPE |
| `<branch-root>/chapters/<chapter>/upscaled/<profile>/` | Chapter-scoped processed scenes | Same concept, different physical path shape |
| Processing profile's `segments/`, `checkpoints/`, `prompts/`, `audio/`, `partial/`, `final/` | Processed media, full or minimal checkpoints, aliases, lineage and deliveries | Several file families for each processed revision |
| `<branch-or-chapter-root>/frames/<name>/` | Manifest/latent PNG exporter | Own schema, numbering, checkpoints/hash cache and locks |
| `<profile>/frames/<name>/` | In-loop VIDEO PNG exporter | Different schema, exact take ownership, numbered variants and publication journal |
| `P/png_exports.json` | Registered VIDEO PNG destinations | Includes explicit custom destinations elsewhere under output |
| `P/reference_cache/` | Run-local reference descriptors and tensor objects | Required for exact cached-conditioning recovery when referenced |
| `P/project_assets/` | Catalog and imported asset backup | Project-shared; primary copy lives under `input/h3_projects/<project>` |
| `P/references/`, `source/`, `<branch-root>/source_timeline/` | Older imported inputs and frozen source-timeline media | Not interchangeable with project assets or preview caches |
| `P/orchestration/` | Durable top-level requeue handoffs | Project-root storage; records carry execution identity |
| `h3_chains/.project_ownership/`, `.run_locks/` | Shared write ownership and synchronization | Outside the individual project folder |
| `h3_chains/.plan_studio_thumbnails/`, `.plan_studio_source_previews/` | UI preview caches | Derived, distinct from reference-conditioning caches |
| Checkpoint `.transactions/`, branch `.branch-*`, export staging/journals | Publication and recovery in progress | Must not be swept solely because a name looks temporary |

Optional integrations can add their own products, such as `grok_bridge/`.
Those are not generation checkpoints; a central inventory should label unknown
or integration-owned files rather than treat them as garbage.

Principal layout sources:
[generation paths](../chain_nodes.py#L8480),
[recovery archives](../chain_nodes.py#L9859),
[branch creation](../working_branches.py#L213),
[chapter paths](../chain_nodes.py#L22793),
[processing paths](../upscale_nodes.py#L65),
[project assets](../project_assets.py#L480),
[prompt history](../prompt_history.py#L101),
[handoffs](../handoff_state.py#L380).

## 3. Findings, in priority order

### A. High: project ownership and working-branch scope are too easy to confuse

`_project_run_dir()` returns the shared project; `_run_dir()` can instead return
a named branch directory. Generation artifact construction intentionally uses
both in the same returned dictionary: media is shared, the active metadata
pointer is branch-local. The meaning also depends on an execution-local branch
context when no explicit branch is passed.

This is directly related to the ALT failure corrected in `62948e3`: a selected
picture path could read another branch's editorial data, and a branch-local
metadata alias was used to derive the wrong immutable ALT address.

**Recommendation:** explicit, distinct project-store and branch-state APIs.
Immutable take lookup should take a project identity and revision ID, never
derive its address from a mutable alias's directory. Keep full IDs internally.
See [project/branch roots](../chain_nodes.py#L8168),
[mixed artifact paths](../chain_nodes.py#L8480),
[branch scope](../branch_scope.py#L82).

### B. High: processing has four directory shapes for one concept

Project, chapter, named branch, and named-branch chapter processing each have a
different root. DeRoPE, latent upscaling and pixel upscaling all use `upscaled`,
although DeRoPE need not upscale anything. Meanwhile originals remain shared,
so generated and processed revisions follow different ownership models.

Readers, listing, deletion and deferred-source validation all know about these
shapes. Some validators check exact component counts and filenames. Renaming
`upscaled` to `processing` alone is not a safe fix.

**Recommendation:** a project-wide processed-take store, with a pass record
containing its name, recipe, source cut, branch, chapter and resulting revisions.
Display these relationships in the UI rather than nesting storage by them.
See [profile routing](../upscale_nodes.py#L65),
[listing](../checkpoint_variants.py#L60),
[deletion path contract](../processing_checkpoint_delete.py#L71),
[DeRoPE source path contract](../deferred_checkpoint_source.py#L51).

### C. High: addresses are embedded in integrity and recovery contracts

The representative project's parsed JSON contains 3,353 occurrences of 465
distinct chain-path strings, including directory references. This count excludes
addresses hidden inside serialized JSON strings or media metadata.

Saved chapter identity hashes include its referenced document content; processing
source contracts include reference descriptors and nested source records.
Recovery readers also validate exact revision-directory locations. Moving files
and search/replacing strings can therefore invalidate identities or pinned
workflows, even when the underlying pixels are unchanged.

**Recommendation:** introduce logical take/artifact IDs and a versioned resolver
before moving anything. Keep a legacy-address compatibility mapping; never
rewrite historical documents just to make their paths prettier.
See [chapter identity](../chain_nodes.py#L22876),
[archive validation](../chain_nodes.py#L9885),
[processing source contract](../upscale_nodes.py#L1037),
[portable address validation](../artifact_paths.py#L6).

### D. Medium: export identity, routing and deletion history are mixed with frames

There are two PNG storage contracts: `h3_chain_png_export_v1` and
`h3_video_png_sequence_v1`. Manifest PNG suffixes use forms such as `_0002`;
VIDEO PNG variants use `_2`. VIDEO exports also have `.png_variants`,
`.png_variant.json`, locks, staging and possibly `.png_pending.json`.

The inspected project contains a VIDEO export record with zero surviving
frames and `deleted_scenes: [1]`. That is a deliberate tombstone, not evidence of
failed cleanup. It prevents a subsequent retry from resurrecting deleted scenes.
Another seven-scene VIDEO export records exact processing ownership for only
two scenes. Older/unattributed frames must be preserved rather than guessed to
belong to a deleted upscale. Manifest PNG exports have a different lifecycle.

**Recommendation:** one export registry and a common lifecycle model; keep
export media shallow and move internal routing/tombstone records into a defined
state area in a new format. Retain deletion history and exact ownership.
See [manifest PNG naming](../chain_nodes.py#L25691),
[VIDEO variants](../png_export_variants.py#L23),
[PNG deletion rules](../png_export_cleanup.py#L46),
[publication recovery](../png_export_transaction.py#L66).

### E. Medium: no single project-wide retention inventory

Deletion already checks important dependencies: other branch assignments,
ALTs, saved chapter snapshots, processing lineage and exact PNG owners. Reference
bundle conversion has separate successful-render receipts. These protections
should remain.

However, the rules live across several managers. A user cannot infer from a
folder's name whether it is essential, regenerable, shared, retired or orphaned.
Ordinary review partials and processing partial manifests use through-scene
filenames, so not every file is immutable merely because it describes a past run.

**Recommendation:** a read-only Storage Inspector that lists owner, references,
size, lifecycle and the reason deletion is blocked. An eventual cleanup action
must use fresh dependency validation, explicit confirmation and a recoverable
quarantine. Never equate “inactive” or “not currently visible” with “unused”.
See [original artifact ownership](../checkpoint_manager.py#L847),
[chapter pins](../checkpoint_manager.py#L1378),
[processed dependencies](../processing_checkpoint_delete.py#L86),
[snapshot retirement](../chapter_snapshot_retirement.py#L34),
[reference retirement](../reference_cache_migration.py#L252),
[review partials](../chain_nodes.py#L27969).

### F. Medium: temporary assembly filenames can collide

The assembler uses fixed `.concat.txt`, `.video.tmp.mp4`, `.final.tmp.mp4`,
`.audio.tmp.wav` and `.metadata.tmp.txt` in the destination's `final/` folder.
Startup and cleanup unlink those names. I did not find a destination-wide lock
around this assembler path; branch-context wrapping does not provide one.

**Static concurrency risk, not an observed corruption:** two writers sharing
that destination could interfere even if their final filenames differ. Normal
single-writer execution may never encounter this.

**Recommendation:** per-job temporary directories and atomic final-name
reservation/publication. Validate with a concurrent-writer regression before
claiming support for multiple processes sharing the output store.
See [assembly setup](../chain_nodes.py#L27713),
[temporary cleanup](../chain_nodes.py#L27757),
[branch wrapper](../branch_scope.py#L82).

### G. Low for capacity, medium for clarity: repeated metadata and mismatched names

The project has 26 generation/processing checkpoint aliases containing about
1.51 MiB of metadata copies. There are 35 groups of byte-identical JSON files,
representing **5.57 MiB** beyond one copy per group. These include canonical
mirrors and immutable recovery snapshots: duplicated bytes do not make their
separate recovery roles disposable.

Names also conceal distinctions:

- `checkpoints` can mean an active JSON alias, immutable metadata, a full latent,
  or a small processing checkpoint with audio/marker but no full video latent.
- Original prompts are beside video as `.prompt.txt`; processed prompts have a
  separate `prompts/` directory.
- Original audio is under `generated_audio/`; processed audio is under `audio/`.
- `alternates/` holds manifests, while ALT media is beside original media.
- `final/` also holds review partial videos.

**Recommendation:** thin branch selection records, shared immutable snapshot
objects where byte-identical, explicit take capabilities and consistent terms.
Do this for correctness/readability, not as a meaningful disk-space saving.
See [generation publication](../chain_nodes.py#L20765),
[processed publication](../upscale_nodes.py#L2712),
[processed checkpoint contents](../upscale_nodes.py#L2655),
[recovery mirrors](../chain_nodes.py#L9984).

### H. Boundary: the project folder is not the entire environment

Project assets have both an input-side primary and output-side backup. Reference
caches can exist in global `output/h3_reference_cache` and run-local storage;
V3 adopts objects with hard links where available, otherwise copies. Older V2
bundles remain readable. This project contains 19 V2 and 29 V3 reference-cache
JSON documents, so old and new cache layouts demonstrably coexist.

No files in the inspected project reported multiple hard links on this mount;
that alone does not prove physical duplication, because filesystem/server
deduplication or sharing elsewhere was not measured. Among 2,167 PNG file-hash
entries inspected in manifests, no cross-path duplicate identities were found;
this is not a hash audit of every PNG on disk.

A project backup also does not include installed models, custom-node code,
browser-local drafts or necessarily every workflow input. Ownership records
live outside the project. A future export/import command needs an explicit
dependency inventory and ownership-rebinding policy.
See [asset backup](../project_assets.py#L996),
[reference adoption](../chain_nodes.py#L4686),
[tensor-object sharing](../reference_cache_store.py#L118),
[existing cache compatibility](RUNS_AND_RECOVERY.md#resume).

## 4. Proposed simpler layout — design, not implemented

Keep the existing project folder for compatibility. For a new storage version:

```text
<project>/
  project.json                 # schema, label, default branch, index references
  branches/<branch-id>.json     # authored state, assignments and cut references
  takes/<take-id>/              # original, ALT, DeRoPE or upscale; stored once
    take.json                  # scene/stage, exact parents, capabilities, hashes
    video.mp4
    latent.safetensors         # optional; no claim of latent recovery when absent
    audio.wav                  # optional
    prompt.txt
  passes/<pass-id>.json         # recipe, branch/chapter/source cut, result take IDs
  exports/<export-id>/          # final MP4 / PNG sequence and its export manifest
  recovery/<snapshot-id>/      # immutable authored/workflow recovery documents
  assets/                      # self-contained imported assets and catalog
  reference_cache/             # preserved conditioning recovery objects
  cache/                       # genuinely regenerable previews only
  state/                       # jobs, transactions, tombstones and authoring history
```

The key change is not the spelling of these folder names. A chapter or branch
does not physically contain the take; its metadata selects it. A pass name or
resolution change does not rename a storage directory. Human labels and save
order belong in metadata/UI; full revision IDs remain the lookup identity.
Short IDs may be displayed, but must not silently become collision-prone storage
keys. PNG deliveries keep contiguous frame names for external editors.

This is **not** a recommendation to put all frames in a database or to hard-link
editable exports together. Existing PNG prefix copying preserves isolation;
editing one exported sequence must not mutate another. Keep image files usable
by ordinary tools.

## 5. Safe implementation sequence

1. **Storage Inspector first, no migration.** Inventory existing layouts and
   expose ownership, references, file roles, full-latent availability, path
   lengths and protected deletion reasons. Do not infer safe-to-delete from age.
2. **Centralize the legacy path resolver.** Separate shared takes, branch state,
   processing passes, chapter snapshots and exports without moving old files.
   Replace repeated path-shape parsing with typed validated lookups. Isolate
   assembly temporary files as a separately tested safety fix.
3. **Introduce a versioned logical index.** Keep immutable take IDs and content
   hashes distinct from storage locations. Use a rebuildable lookup index;
   immutable records remain the recovery authority. Preserve current branch
   assignment, final-cut and source contracts in the compatibility layer.
4. **Write new-format projects on nightly, opt-in.** Keep old readers for old
   projects. Initially avoid mixed-layout projects unless their resolver and
   recovery tests explicitly support them. Do not migrate merely on update,
   listing, selection or startup.
5. **Offer explicit migration only after validation.** Preview files, space and
   path changes; require a backup and quiescent writers. Journal each operation,
   verify media hashes and metadata relationships, keep legacy bytes/addresses
   resolvable, and support restart/rollback after interruption. Account for
   external PNG destinations and input-side asset bindings.
6. **Add cleanup/compaction separately.** Protect active and inactive retained
   branches, ALT/base audio dependencies, saved cuts, resume checkpoints,
   processed descendants and referenced caches. Retire snapshots explicitly;
   quarantine approved unreferenced files before permanent deletion.

Required regression matrix: legacy and named branches; empty/forked branches;
shared prefixes; attributed takes; original and ALT cuts; mixed chapter geometry;
pixel/latent/DeRoPE passes; missing full latents; resume from a middle scene;
chapter seals/retirement; old and new PNGs; custom destinations and manual PNG
edits; Windows-style addresses; junction/symlink escape rejection; Linux/network
storage; concurrent publication; interruption/retry; backup relocation and
restore; exact prompt/seed recovery; and stale workflows with pinned old paths.

## Decision

Start with the **read-only Storage Inspector and centralized legacy resolver**,
then build the shallow layout as a new version. The current safety data is worth
keeping. The architecture should make its ownership explicit, instead of making
users and every reader rediscover it from long folder paths.
