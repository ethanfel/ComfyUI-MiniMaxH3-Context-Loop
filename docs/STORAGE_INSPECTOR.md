# Storage Inspector — first implementation batch

Status: read-only foundation on `nightly`, 2026-09-10. This implements the initial
inventory/baseline slice of [the migration plan](STORAGE_MIGRATION_PLAN.md), **not
the storage migration or the complete P0/P1 exit gates**.

## Use

1. Select a project in **Checkpoint Manager** and click **Storage**.
2. Review category sizes, notices and longest paths. The file table supports
   search, category filtering and 50-file pages.
3. Use **Download inventory JSON** to save a report in the browser. Take another
   report after a change to compare the same project-relative addresses.

The view covers the entire project, including Original and every named branch;
it does not follow the currently previewed clip or the manager's output pin.
It never switches a branch, changes the Plan, selects an ALT or edits a workflow
widget. It runs only on request, not on normal checkpoint refreshes. Closing the
view aborts the browser request and ignores late responses; the bounded server
scan may finish in the background. Switching projects invalidates the report.

## What the evidence means

| Evidence | Meaning / limit |
| --- | --- |
| Logical bytes | Sum of file lengths, counted once per path |
| Reported allocation | Filesystem `st_blocks × 512` when available; unknown counts shown separately. Hardlinks can count twice; backend compression/deduplication is not measured |
| Storage scope / pass | Location of a file; **not** proof that only that branch owns it. Original/shared generation artifacts live at project level |
| Shared reference | More than one inspected metadata document references the address. Historical records and pointer copies count; this is not live ownership accounting |
| Unreferenced candidate | No direct reference was observed by this limited scanner. **Never a safe-to-delete verdict**, including media implicitly owned by an export directory |
| Unverified | No content checksum, decoding or tensor inspection performed |
| Possible edit | Recorded PNG size differs from current size. Preserve current bytes; do not replace them from the saved digest |
| Timestamp changed | PNG timestamp differs; a copy can cause this without changing pixels |
| Retired | File is in `retired_manifests`; this does not authorize deletion |
| Declared saved / omitted latent | Processing metadata's `latent_saved` flag, not inspection of tensor contents. A marker/audio-only checkpoint file does not prove full-latent availability |
| Missing or unscanned | A recognized project-relative reference has no scanned target. It may be historical, optional, removed, or outside a partial scan; execution/deletion policies remain authoritative |
| Scan completed | The bounded inventory finished and did not detect concurrent file changes. This is **not** integrity verification or migration readiness |

The project-wide inventory includes takes, processing artifacts, exports,
recovery, project asset backups, conditioning cache, state and unclassified
files. Preview-cache categories are supported inside a project, but current
shared preview caches under `h3_chains/.plan_studio_*` are **outside this scope**.
Input-side project assets, global reference caches and custom PNG destinations
outside this project are not scanned. Catalog addresses outside the project are
reported, not followed. Unknown integration files are counted, not treated as
garbage.

## Strict read-only boundary

Implementation: `storage_inventory.py`, with a GET-only
`/minimax_h3_context_loop/storage-inventory?run_name=...` route. No use of
checkpoint adoption, branch load/recovery, deletion previews, ownership mutation
guards, cache repair or lock helpers: some of those create files while reading.

- Only directory entries, stat data and selected JSON metadata are read.
- No media/tensor file opens, checksum cache updates or model imports.
- No project file/directory creation, writes, renames or deletion. The OS can
  still update access times as with any read; these are not a preservation check.
- Symlinks, Windows junctions and special files are not followed. Project names
  and artifact addresses are confined. Windows separators are normalized only
  for lookup; saved JSON bytes stay unchanged.
- Plan/workflow/API prompt files, authoring records and prompt-history content
  are opaque. Prompt/seed values are not included in the inventory. Reports do
  contain paths/revision IDs and should be reviewed before sharing publicly.
- Pending transactions are reported, never recovered or discarded.
- A running writer may invalidate the scan. Rescan while idle before using it
  as baseline evidence; there is no atomic snapshot or maintenance lock.

Budgets: 100,000 directory entries, 16 MiB per JSON document, 128 MiB total JSON
reads, 200,000 reference observations, 500 detailed notices. Limits produce an
explicit partial report, not an implicit success. Notice counts remain available
when individual notices are omitted.

## Inventory format and comparisons

`h3_storage_inventory_v1` is a diagnostic report, **not a new project storage
format**. It includes:

- `run_name`, `scanned_at`, `scope`, `scan_complete`, `verification`;
- `totals`, `categories`, `longest_paths`;
- `files`: project-relative address, size, allocation, decimal-string nanosecond
  timestamp, storage scope/pass, observed incoming references and evidence flags;
- `records`: metadata address, revision, scene, source/ALT revision and declared
  full-latent status; assignment aliases and immutable metadata remain distinct;
- `references`: source document/field, target address, scope and observed status;
- `observed_formats`, `issue_counts`, bounded `issues` and `limitations`.

`inventory_stat_id` hashes sorted **paths/sizes/timestamps only**. Identical IDs
are useful for a quick unchanged-stat comparison, not proof of identical file
contents. They do not authorize a migration, deletion, repair or cutover. A future
migration verifier must compare immutable bytes and consumer behavior separately.
`migration.enabled` is always false and `migration.status` is `not_assessed`.

## Baseline and consumer checklist

`tests/storage_fixture.py` supplies an initial reusable legacy project fixture:
shared immutable generation/ALT takes, named and empty branches, Windows pointer
addresses, an exact >2^53 seed, archives, sealed/retired snapshots, a middle-scene
pixel save with marker-only latent, edited `_2` PNGs, an export tombstone, custom
destination catalog, conditioning cache, assets and unknown integration files.
The inventory suite compares every fixture file's bytes and modification time,
plus existing graph, branch-listing and processing-catalogue results before and
after inspection. Corruption, missing references, symlinks/junctions, hardlinks,
concurrent writes, transaction journals, resource limits and HTTP errors have
dedicated tests. Media opens and write-capable opens are forbidden in the test.

The existing 46 test entry points in the migration plan remain the broader
compatibility baseline. The browser fixture exercises the real mounted manager
and Storage panel; the controller test covers stale results, error/retry and
download invalidation. **None of these are migration conformance tests yet.**

| Consumer | Must remain unchanged now; required again before migration |
| --- | --- |
| Generation/recovery/authoring | Immutable revision IDs, prompt/seed bytes, hashes, source paths and recovery precedence |
| Working branches / local pins | Assignments, empty-branch behavior, Plan switching, authoring restoration and workflow-local output isolation |
| ALT / chapters / assembly | Selected ALT picture/prompt/seed, original audio, generation ancestry, trims, sealed and retired cut identity |
| Processing / resume | Source contracts, partial-range semantics, stage detection, full-latent/context capability errors |
| PNG / cleanup | Both export formats, numbered variants, edited bytes, owners, independent/shared prefixes and tombstones |
| Assets / caches / previews | Existing locations and content identities; no adoption or repair as a side effect of inspection |
| Review / requeue / ownership | No new queue, approval, adoption, owner claim or transaction recovery |
| HTTP / browser / external files | Existing public wire formats and literal media paths; GET inventory adds no fields to Plan or generation hashes |

## Validation recorded for this batch

- New inventory suite: 10 tests passed, including unchanged bytes/mtime and
  unchanged existing consumer results. Controller isolation tests passed.
- Isolated real-browser fixture: 84 checks passed, including Storage rendering,
  pagination/filtering, filename escaping and width checks.
- Broader baseline: 42 of the plan's 46 entry points passed. Four async tests
  required retries outside the sandbox because of thread-pool wakeup/socket
  restrictions; all four retries passed on temporary fixtures.
- Four integration entry points remain blocked before execution by the local
  PyAV/ComfyUI dependency mismatch (`av.video.reformatter.ColorPrimaries`):
  `_lms_upscale_unit_test.py`, `_png_video_export_unit_test.py`,
  `_reference_cache_migration_unit_test.py`, `_upscale_pixel_unit_test.py`.
  Dependencies were not changed to make these pass.
- Read-only scan of the mounted project: 5,874 files in approximately four
  seconds. These are live inventory observations, not a frozen migration fixture
  or verification of file contents.

## Still gated

The complete multi-generation/multi-pass migration fixture matrix, authoritative
dependency/retention verdicts, cancellable deep checksum verification, isolated
assembly staging fix, central path adapter, V2 writers and migration/rollback
tools are subsequent work. No automatic migration or cleanup is enabled by this
batch, even if an inventory reports no notices.
