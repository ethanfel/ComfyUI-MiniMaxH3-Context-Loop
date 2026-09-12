# Storage migration completion checklist

Scope: finish the organized layout, migrate an independent copy of the
real chain, and exercise every supported consumer/write/recovery path. Passing
one row does not establish completion of another. No live-chain activation.

## Current status — organized migration and normal runtime

The copy/verify/activate command and normal ComfyUI node/HTTP integration are
implemented. Use [Migrate a chain](MIGRATE_STORAGE.md) for the command, output
directory cutover and rollback. Activation is explicit and applies only to the
independent destination; existing legacy projects are not automatically changed.
No live project, backup, installed node pack, queue, commit or push was changed
in this final integration pass.

### Final integration evidence

- The public migration completed on the independent full-size chain: **746
  control files and 10,130 media payloads (29,638,833,792 bytes)**. Full content
  verification passed, including unchanged source identities, before activation.
- Normal HTTP handlers, without a test runtime host, passed **30/30 read checks**:
  all 14 branch authoring records matched the source, every branch checkpoint
  graph loaded, and the asset catalog loaded.
- A final separate **30/30 restore check** passed after the Run Manager port:
  normal run listing, all 14 saved-run restores and all 14 authoring comparisons.
  Both HTTP passes used the full independent migrated chain, not synthetic
  branch records. Their overlapping checks are not counted as 60 unique tests.
- Public migration followed by normal CPU node/HTTP execution covers Plan Studio
  save/fingerprint, Run Manager archive/restore, source audio, checkpoint listing,
  generation save/retry with an exact uint64 seed, streamed asset upload/delete,
  and actual PromptExecutor recursive pixel processing, PNG export and assembly.
  All **7 normal-host integration tests** pass, including restoring an archived
  loader input when the original input is absent. The last pipeline uses tiny
  encoded media and ffmpeg, not GPU sampling.
- The final five affected legacy/reference suites pass: asset store, Run Manager,
  Source Timeline, Source Timeline consumers and reference-cache recovery.
  Browser transport, asset synchronization and `git diff --check` also pass.
- Migrated reference-cache recovery passes **39 tests**, including normal
  conditioning rebuilding disposable references and reusing them on a second
  call without recreating legacy project folders.
- Existing asset import/edit, deferred Review/finalization, request pinning,
  reference recovery and browser transport regressions were rerun during the
  final integration. The older broad baseline below is historical evidence,
  not a claim that every script was rerun on this final source tree.

### Explicit limits

No full GPU generation/upscale quality run or native Windows run was performed
in this final pass. Full-size activation was tested on a local Linux filesystem;
the older CIFS protocol/retry tests are recorded separately below. Keep the old
chain and backup until the migrated project has passed your own first normal
job. Fresh jobs use the new storage binding; pre-migration browser edits and
automatic handoffs must not be replayed as new-storage work.

The original scene-6 archived Plan mismatch remains the user-accepted baseline.
Migration preserves it; it does not rewrite prompts, seeds or context. The
standalone legacy reference-cache conversion CLI is not part of this migration;
existing reference bundles are readable without converting them separately.

Private final evidence: `/var/tmp/h3-migration-release-jHLzZ6/job/verified.json`,
the destination's `project/activation.json`, and the normal host/read tests.

## Historical status before final integration — 2026-09-12

The following incomplete-status notes and release gates are superseded by the
current status above. They are retained as test history, not outstanding work.

**At this historical checkpoint, production migration was not finished or enabled.** Existing V1 projects stay
on their current paths. No live project, user backup, installed node pack,
queue, commit or push was changed. A read-only live check still loaded the
user's 960×544 branch and saved Plan successfully.

Scope is frozen to storage compatibility and safe migration. The historical
“Next”/“Remaining” lists below are not a mandate to add more administrative UI,
durable browser-reload features, repair-job management or unrelated cleanup.

### Completed verification

- The full 242-script baseline run found seven failing entrypoints. All seven
  were corrected; a final affected-surface run passed **38/38 scripts** on
  frozen source `63fe84e9acb7469480a2475feaf79580`. This is a full baseline plus
  targeted rerun, not a claim that all 242 were rerun on the final snapshot.
- All **23 bundled workflows** pass schema validation. Their recipes now
  explicitly serialize existing start mode, anchor overrides, branch selection
  and execution mode defaults. Existing settings and wires are retained.
- The final full-copy reader comparison passed **63/63 checks**: 14 branches,
  723 recovered controls and 12 closed retention/undo records. Authoring,
  generation/processing catalogues, Review inventory and recovered bytes match.
- A fresh independent real-media slice passed **55/55 checks**: 33 source media
  files (242,735,902 bytes), one copied scene video, all 14 branch authoring
  records, imports, previews, edits, interrupted/lost-reply retries, recovery and
  real local HTTP branch reads/catalog writes. It is not a full GPU render.
- Windows mapped-drive/UNC canonicalization and browser cache-token regressions
  are fixed. Windows path parsing is simulated on Linux; native Windows was
  not run. All copied evidence and the live project remain preserved.

### Changes in this verification pass

The HTTP wrapper now binds existing handlers to an explicitly granted copied
project, returns the exact accepted storage pin, preserves ownership checks and
revokes request access after completion. Real aiohttp tests cover branch
prompt/uint64-seed saves, exact retries, stale edits, asset worker threads and
ungranted/expired hosts. This is **not** automatic production host registration.

Cross-project asset listing no longer hashes every media file merely to display
the picker. Actual import still verifies full content hashes, including
same-size corruption tests. The ComfyUI core skill guided the node/widget and
actual HTTP-handler compatibility checks.

### Required before production use

1. Connect normal UI/queue execution and migration activation to the runtime
   binding; finish the preview/quiesce/copy/verify/cutover entrypoint. The new
   HTTP wrapper alone does not complete this.
2. Finish existing-feature gaps, including migrated asset deletion/retention
   and reference-cache conversion/recovery writes; do not silently weaken
   their guards or treat an unsupported writer as tested.
3. Run the complete normal-workflow acceptance on an independently migrated
   chain through that final integration. Record platform/render limitations
   separately; do not turn optional improvements into additional blockers.

Do not activate this format on the user's project while those gates remain.
The original scene-6 archived Plan mismatch remains the user-accepted baseline,
not a reason to rewrite their prompts, seeds or context.

Private evidence: `project-release-final-regressions/qualification.json`,
`w-mBYCdo/project-release-corrections-v2-asset-summary.json` and
`w-mBYCdo/project-release-final-current-read-summary.json` in the existing lab.
All test processes are terminal. Failed baseline/harness reports are retained.

## Historical checkpoint: explicit input repair and Carousel action

The preceding status-only goal turn was no progress. This continuation implements
read-only input inspection and a deliberate repair transaction, rather than
loosening `_verify_media` or enabling legacy auto-restoration during browsing.
Missing input catalog/media can be restored from their pinned verified backup.
Damaged bytes are copied independently before replacement and retained as
accepted recovery payloads. A different readable catalog is a conflict, including
unknown/newer schemas and another project's catalog. Unknown input files stay.

The repair uses the shared pending-input marker and catalog/ownership locks.
Its immutable request/result supports partial-file publication, root interruption
and lost-reply retries with the same pin/operation. It never alters the accepted
catalog or original Plan, seed, editorial and media descriptors. Missing/corrupt
backup or damaged recovery copy fails instead of silently adopting another file.
Both actual GET inspection and POST repair HTTP actions are wired through the
existing project store and ownership contract; production activation is unchanged.

28023 COMPLETE: 10 focused cases passed in2.863s. Expanded36655 COMPLETE:
13 cases passed in4.196s, including actual HTTP worker-thread/ownership handling,
whole missing input directory and changed recovery/input bytes.
Frozen `75208e59ab1d4ed68e5a5ef9eb680ce3`: COMPLETE91819 **29/29 regression
scripts passed**, sources unchanged. COMPLETE86653 **47/47 real-slice checks
passed in122.786s**, independent slice `a-2bb7d1`.33 original media files,
14 branches, five added assets and one copied real scene video were exercised.
Repair restored missing/corrupt copied inputs, preserved damaged bytes in
independent accepted payloads, survived partial publication and ordinary recovery,
refused a different valid catalog and left every pre-existing control exact.
Protected full evidence copies and live/backup paths were not modified.
Reports: `project-asset-repair-qualified-regressions/qualification.json` and
`w-mBYCdo/project-asset-repair-qualified-summary.json` in the private lab.

The actual Carousel now has Repair input: explicit inspect/confirm and mounted
exact-retry behavior through the normal ownership transport. Cancel/healthy/
conflicting catalogs do not POST. Failed requests keep the original inspection,
source pin and transport ID, including after partial publication. Project-switch
races cannot repair from a stale inspection or update another Carousel's status.
Actual production-action JS tests pass (transport/dialogs isolated). This is not
native-browser or durable browser-reload/production-job qualification.
Final frozen `f501c283e3f6482abe8291daf7796a86`, COMPLETE49687: **30/30 related
regression scripts passed**, sources unchanged, including the actual Carousel
repair action. Report: `project-asset-repair-ui-qualified-regressions/qualification.json`.
All handles terminal. Receipt
comparison confirms ALL Python/runtime sources are identical to the qualified
real replay; only two frontend files, their new JS test and two progress docs
changed. Do not overwrite earlier evidence or imply a second full-chain replay.

Remaining: durable repair resume/cancel administration; cross-project source pins and
operations; asset deletion/retention and pending-job cleanup; durable browser,
request and queue pins/production integration; reference conversion/recovery;
automatic import classification; remaining damaged/partial recovery and full
copied-chain all-workflow replay; GPU/native-Windows/shared-filesystem/platform
qualification. Existing scene6 mismatch stays the accepted baseline. No live,
backup, installed-pack, commit/push or activation changes.
The ComfyUI core skill guided the actual HTTP/ownership execution contract.

## Previous checkpoint: pinned, retryable frame capture

Previous goal turn made progress (qualified model publication). This continuation
ports the actual Review frame-capture helper/HTTP route into migrated storage.
`storage_asset_capture.py` resolves a saved /view logical or physical video from
the exact accepted payload; input project videos also resolve through that
catalog's verified backup. Other projects need independent source pins rather
than inheriting this project's authority. Loose input/output/temp sources remain
explicit choices within the requested root, with source-byte witnesses.

The request binds source selection/bytes, timestamp, tag, role, folder and input
pin before ffmpeg. Synced frame receipts support interrupted/lost-reply retries
without a second extraction. A completed external/temp source may disappear;
missing accepted source payloads or changed source bytes never fall back. Owner
takeover and source changes during extraction block publication. Registration
preserves capture-family tag numbering and provenance in one catalog transaction.
The real HTTP handler passes the stable ID through its worker thread. Review's
actual dialog reuses failed exact IDs while mounted; changed tag/source/time
requests get distinct IDs. This is not durable browser-reload/production pinning.

83375 completed8 focused cases in17.788s; 71186 completed16 legacy capture cases
in3.355s. Expanded54722 completed12 cases in14.764s, adding lost replies, request
changes, corrupted receipts, missing indexed sources and failed/mutated extraction.
JS actual-dialog and shared asset transport tests passed. Final frozen
`3abc41e2d4ec43fbb4256b8154abcdde`, COMPLETE8955: **28/28 related scripts passed**,
sources unchanged, including all prior model/import/preview/legacy coverage.

Real replay22074 TERMINAL1: its old asset-only count incorrectly omitted the
additional copied scene video. Failed report retained. The harness now compares
the complete payload maps before/after metadata edits, stronger than a count;
runtime unchanged. Fresh COMPLETE59417 passed **40/40 checks in120.858s** on the
same frozen source, independent slice `a-7b3c64`, report
`w-mBYCdo/project-asset-capture-qualified-v2-summary.json`. One4,057,115-byte real
scene6 video was independently copied alongside33 original media files totaling
242,735,902 bytes. Replay covered14 branches,33 previewed cards and5 added assets.
Relocated scene capture survived interruption without re-extraction or duplicate
cards; its source pin/provenance and ordinary recovery matched. Original media,
Plan/seed/editorial, the real scene video and protected full copies stayed exact.
All handles terminal; no live/backup writes. This is NOT full-workflow replay.

Next: explicit input repair/materialization (including missing/corrupt input
catalog/media, without browse-time writes or replacing newer valid state),
independent cross-project operations, media deletion/retention and pending job
cleanup. Durable browser/job/request pins, production host/UI/queue integration
(including model lazy status), reference conversion/recovery writers, automatic
import classification, remaining damaged/partial recovery, final full copied-chain
workflow replay and platform qualification remain open. Existing missing input
media still fences catalog publication even if a capture can read its accepted
backup: this is an explicit remaining repair case, not silently bypassed.
No live/backup/installed-pack writes, commit/push, GPU or native Windows claims.
The ComfyUI core skill guided the real endpoint and node contracts.

## Previous checkpoint: durable model-derived assets and successor reads

The previous user-status turn made no implementation progress. This continuation
ports the actual Carousel model-upscale save path and its post-write reads.
Legacy and migrated paths share the existing tiled UPSCALE_MODEL pixel helper,
crop geometry, final resize and alpha preservation. An immutable request binds
the original pin, parent, operation, reference templates and model weights/patches
before compute. Ownership is rechecked after compute. A synced PNG plus verified
render receipt permits restart without a second forward or connected model.

`register_model_image` nests derived registration and connected slot sync in ONE
input/catalog/backup transaction. This avoids separate model/slot commits with
ambiguous intermediate retries. `accepted_asset_read_access` then compiles the
Carousel from exactly that acknowledged successor, with no follow-on write grant;
the old reader is suspended and no unrelated latest root is followed.
Model provenance survives in the accepted asset's transform. Pending render
staging/receipts remain retained until an explicit job-retention policy.

Focused 48767 passed 5 tests (11.271s); expanded 76775 passed 10 (24.628s).
Final frozen `89618d4ac11f4dd6a9e6fa2f2f39cccf`: COMPLETE49482 passed **25/25
related regression scripts**, sources unchanged, including those ten actual
CPU Carousel/model tests. Cases cover model+slot single-commit output, exact
retry, six interruption boundaries, changed request/weights, takeover, source
corruption, concurrent lost-reply recovery, receipt/render corruption, alpha and
expired/read-only successor readers. Existing legacy asset, generation,
processing, ownership, JS/Plan and recovery regressions also passed.

Real asset replay 65554 TERMINAL1: 20 checks passed, then the standalone harness
lacked the local ComfyUI import path needed by the model-weight digest. This was
not waived. Its failed report/slice remain; only the harness import path changed.
Fresh replay COMPLETE96923 on the SAME frozen runtime source passed **36/36
checks in120.438s**, independent slice `a-2b0a23`, report
`w-mBYCdo/project-asset-model-qualified-v2-summary.json` in the private lab.
33 original media files/242,735,902 bytes, 14 branch reads, 33 previewed cards,
four added test assets. Model preparation left catalogs unchanged; resume read
the exact successor without another forward; model lineage+slots used one commit.
Input/backup media and ordinary recovery matched; original Plan/seed/editorial,
protected full copies and live chain stayed unchanged. All handles are terminal.
This is still an asset/control slice, not final all-workflow replay.

Next: capture producers (actual frame-capture route still needs stable operation
IDs, pinned source resolution and durable extracted pixels); input repair,
cross-project operations, asset deletion/retention, durable browser/request/job
pins and production routing. Lazy-status lookup can still ask the core loader
for a model even when a pending durable render can execute without one; production
job integration must carry the original pin/operation to that lookup. Reference
conversion/recovery writers, automatic import classification, remaining damaged/
partial recovery, final full copied-chain replay and platform qualification remain
open. No live/backup/installed-pack changes, commit/push, or GPU qualification.
The ComfyUI core skill guided the actual node/type/output contract.

## Previous checkpoint: disposable preview caches

Frozen `467f823a0cb6476a9b26b293be889a75` passed **24/24 related regression
scripts**, unchanged source. Ten focused preview cases exercise actual Pillow
and ffmpeg poster/thumbnail/browser-video generation, concurrent worker requests,
cache corruption, publication interruption, unavailable/corrupted source media,
closed readers, HTTP GETs and separate permissions. Tiny AVI-to-H.264 transcoding
was decoded/probed on CPU; no model/GPU qualification is implied.

`storage_asset_previews.py` uses only `project/optional/previews` and
`project/optional/thumbnails`. Source content plus the versioned preview recipe
identify a cache; its verified file receipt is not a source-media descriptor.
Default reads cannot generate or repair caches. An explicit host
`asset_previews=True` grant permits disposable cache work but no input/catalog,
branch, ownership or accepted-root writes. Already verified caches are readable
without that grant. Damaged cache files are preserved while a new file is
published; missing sources cannot be replaced by preview pixels or mutable input.
Production still needs an explicitly authorized rebuild action (or application
cache outside the project); this is not permission to grant ordinary browsing
automatic project writes. No default/production host behavior was changed.

Real replay **33/33 checks passed**, 97.768s, independent slice `a-87159f`:
33 original media files / 242,735,902 bytes, 14 branch reads, 33 visual cards with
both bounded thumbnails and posters, plus the prior metadata/import/crop/recovery
checks. Cache reuse and damaged-cache regeneration left the accepted root/input
catalog unchanged. Original prompts, seeds, editorial, media and protected full
evidence chains match. Reports in the private lab:
`project-asset-preview-qualified-regressions/qualification.json` and
`w-mBYCdo/project-asset-preview-qualified-summary.json`.
This remains an asset/control slice, not final all-workflow replay.

Next: model-derived image publication and acknowledged successor reads, capture
producers, input repair, cross-project operations, deletion/retention, durable
browser/request/job pins and production integration. Reference conversion/
recovery writers, automatic import classification, remaining damaged/partial
recovery, final full copied-chain replay and platform qualification remain open.
No live project, backup or installed-pack changes; no commit or push.

## Previous checkpoint: imported and derived media transactions

The metadata snapshot's previously running matrix is COMPLETE: 117/117 scripts
passed, frozen source `2bd049354a7344cebe2d4c4047caf0b0`. It is not evidence for
subsequent media changes. Those are separately qualified below.

Input `import_file`, `bind_reference_slot`, `register_derived_image` and real
Pillow `derive_image` now publish media/catalog/backups through the existing
recoverable edit transaction. Media is staged and verified before input catalog
publication. Nested derived-image import and lineage use one accepted commit.
An earlier immutable request fences changed parameters/source bytes before
catalog preparation. Interrupted rendering or re-upload may use a new temporary
filename only when the exact request and reserved media content agree. Reserved
but incomplete payload copies can finish without replacing unowned files.

Actual import, derive and multipart upload HTTP handlers carry ownership and
operation IDs. Upload staging has its own input-root/ownership checks; takeover
during upload blocks publication and cleans only the request's temporary file.
Browser upload retries use bounded 1 MiB content fingerprints, including LAN HTTP
without WebCrypto. Reselecting identical bytes preserves a pending request ID;
changed bytes or project do not. The server independently hashes uploaded bytes.
Retry IDs still are NOT persisted across browser reloads.

Final frozen source `f262af85a2264914bb43ad75e7e40ac7`: **23/23 related scripts
passed**, sources unchanged. Includes 15 media cases, metadata/Carousel tests,
actual worker-thread handlers, legacy asset tests, Plan/seed JS checks, runtime,
ownership, recovery, generation and processing-graph tests. Private evidence:
`project-asset-import-qualified-regressions/qualification.json`.

Real replay **29/29 checks passed**, 55.987s, independent slice `a-8ce620`:
33 original assets/media files (242,735,902 bytes), 14 branches and three added
test cards. Tests original-upload removal after preparation, early interrupted
Pillow render, slot binding/media reuse, independent input/backup copies and
ordinary recovery. Catalog restoration retains new media; all original Plans,
seeds, editorial, protected full copies and live paths remain unchanged.
Report: private lab `w-mBYCdo/project-asset-import-qualified-summary.json`.
This is an asset/control slice, not the final complete-chain workflow replay.

Still open: model-derived output integration/successor reads, capture producers,
preview caches, input repair, cross-project operations, media deletion/retention,
durable browser and production request/job pin integration, reference-cache
conversion/recovery writers, automatic initial import classification, remaining
damaged/partial-retention recovery, final full copied-chain workflow replay and
platform qualification. No installed-pack change, live activation, commit/push.

## Previous checkpoint: recoverable input asset edits and Carousel handoff

The preceding user-status turn was no progress (status only). This continuation
validated the previously untested patch and implemented the input-side metadata
transaction, HTTP edit routes, Carousel synchronization and shared-catalog Plan
handoff. No media production/deletion port or production cutover is claimed.

ProjectAssetStore now stages its existing update, duplicate, create/update/delete
folder, reorder folders/cards and sync_reference_slots domain methods without
touching input. An immutable intent preserves exact before/after catalogs and
the result (including generated IDs) before publication. Input and accepted
backup are coordinated by a pending marker; exact retries finish the same edit.
A separate absolute input-root grant, project ownership and stable operation ID
are required. Legacy readers/writers and independent backup refresh cannot step
across a pending migrated edit. Old roots/media remain available.

The initial 91019 run found TWO real issues: missing media was detected after
input publication, and a late retry after a newer same-catalog backup left a
blocking marker. Both were fixed with pre-publication checks. Its two other
errors were incorrect test expectations for sync_reference_slots' catalog return
shape. Fixed 99847 passed 11 tests. HTTP run 42632 needed the existing
ProjectAssetConflictError added to its AST harness; 4810 passed 13 tests.
COMPLETE7866: 14 tests passed, including shared catalog branch handoff.
Final focused COMPLETE16212: 15/15 tests, 8.994s, including empty-catalog creation
and legacy pending-edit fencing. No implementation failure was waived.

Real Carousel/Plan COMPLETE13582: 3/3 CPU integration cases, 1.835s, using the
actual imported package on tiny generated/copied scenes. Synchronization returns
the accepted new catalog and pin; exact node retries keep slot IDs; foreign-owner
builds remain read-only. Carousel now has hidden unique_id, exact asset node
writer/input grants and a separate operation namespace. A host-granted
plan_asset_inputs adapter validates the complete catalog at its saved root
before selecting another Plan branch; it never follows latest or changes input.
Normal mixed-root and ownership gates still apply. Node-host first-node retries
still require their original pin; production job/request pin persistence is open.

The four actual HTTP endpoints (update/duplicate/folder/reorder) pass the stable
storage_operation_id and header ownership proof through real worker threads.
The browser retains exact failed metadata-request IDs while the Carousel is
mounted, including LAN HTTP without randomUUID. JS sync/core and manager tests
passed. This is not durable browser-reload retry or general upload/derive wiring.

Frozen source: 2bd049354a7344cebe2d4c4047caf0b0.
Real replay COMPLETE54169: 23/23 checks passed in 64.811s. Independent slice
a-5dfe91 contains 33 assets / 33 media files (242,735,902 bytes), with reads across
14 branches. It exercises input-publication interruption, competing-edit fencing,
exact retry, all eight metadata operations, unchanged payload reuse, old pins,
original legacy catalog contract, exact catalog restoration and ordinary recovery.
Plans/seeds/editorial and protected full copies match; live/backup paths remain
write-protected. Report: w-mBYCdo/project-asset-input-edit-qualified-summary.json.
This is an asset/control slice, not the full latest-code workflow replay.

Full regression matrix RUNNING76399: 117 entrypoints on the frozen source above,
private lab project-asset-input-edit-regressions. Last observed 12 completed,
none failed. Poll that exact exec session; do not restart based on elapsed time
or these notes. No other launched job remains running. git diff --check passed
in the repository (an earlier invocation used the non-repository lab directory).
The frozen copy is immutable; only these progress documents change afterward.

Next implementation: media imports/bind_reference_slot/register_derived_image
must stage their bytes before catalog publication and retain generated IDs on
retry. register_derived_image nests import_file then updates lineage; both must
remain one accepted catalog transaction. Its asset() calls also need staged-media
lookup rather than accidentally reading the pre-edit catalog. Model upscale
writes via upload_path before registration; derived output compilation needs
read access to the acknowledged successor, not latest or the old input snapshot.
Preview GET routes need explicitly authorized reproducible-cache writes without
gaining catalog/ownership authority. Input repair, cross-project duplication,
media deletion/retention and durable browser reload retries remain open.

Still required for the full goal: reference conversion/recovery writers,
automatic initial import classification, production request/queue/UI host
integration, remaining damaged/partial-retention recovery, final full copied-chain
workflow replay and platform qualification. The ComfyUI core skill guided actual
node/API contracts. No GPU/native Windows claim, installed-pack changes, live
activation, commit or push.

## Previous checkpoint: project asset reads and atomic backup refresh

The previous user-status turn made no implementation changes. This continuation
ports ordinary ProjectAssetStore catalog/media/backup reads to one accepted
storage view, with exact catalog identity, payload checksums and historical pins.
Reads do not restore or rewrite missing/corrupt/newer input catalogs. Original
media reads use verified immutable recovery files, not mutable input or leftover
legacy paths. Missing catalog registration is explicit, not silent adoption.

An explicit refresh_backup operation captures the authoritative input catalog
and its referenced media into one accepted catalog/payload/receipt transaction.
It has a separate host capability, ownership checks, stable operation IDs,
source-change checks, interruption recovery and lost-reply retries. Files are
independently copied under project/assets/media; duplicate cards share their
already-owned media. Old roots/media are retained, not purged by a refresh.
This is not input-side catalog editing, preview generation or production UI wiring.
Those unported writes stay fenced before changing input files.

Focused tests COMPLETE93251: 19/19 asset reader/writer/API/recovery cases passed
in 3.064s. The original asset-store and asset-manager scripts also passed.
Initial 90060 had two harness errors (a wrong nested fixture field and a legacy
reader called inside another project's binding); corrected 8260 passed 15/15
before the four additional cases were added. No implementation failures waived.

Real asset replay COMPLETE18522: 17/17 checks passed in 48.225s, with 33 assets /
33 media files (242,735,902 bytes) and reads across 14 branches. Independent slice
a-54aba8 exercised a staged-media interruption, exact retry, real catalog edit,
old-pin reads, refresh without recopying, and ordinary recovery. Original catalog,
Plan, seed/editorial bytes and all media matched; complete evidence chains and
live/backup paths were protected against writes. This was an asset/control slice,
not the full generation/upscale workflow replay or automatic import classifier.
Report: private lab w-mBYCdo/project-asset-mirror-qualified-summary.json.

The complete imported-chain probe 98791 caught a REAL compatibility gap:
the old importer classified project_assets/catalog.json as immutable
legacy/archive:legacy, not mutable assets/project. Its failed report is retained
at w-mBYCdo/project-imported-asset-read-summary.json. The fix accepts that exact
import contract and versions it with a replacement witness retaining old bytes,
without silently reclassifying it or accepting unrelated control categories.

Focused COMPLETE81956: 20/20 asset cases passed in 3.537s. COMPLETE54459:
13/13 related regression entrypoints passed, all_passed=true and
sources_unchanged=true, frozen source 124be57c1a0d490d9f1145c51b4cd234;
report project-asset-legacy-qualified-regressions/qualification.json.
Real legacy-contract writer replay COMPLETE76900: 18/18 checks passed in
51.935s, same 33 assets / 242,735,902 bytes and 14 branches, slice a-5a7cc4.
It also proves the original immutable legacy catalog contract is retained
through refresh, ordinary recovery and exact catalog restoration. Report:
w-mBYCdo/project-asset-legacy-mirror-qualified-summary.json. Runtime/tests match
the 124be57 source copy exactly at the last comparison.

The complete 113-entrypoint matrix is COMPLETE9604: all 113/113 passed,
all_passed=true and sources_unchanged=true;
project-asset-mirror-regressions, on the earlier frozen 2247d42d source. It does
NOT include the newer imported-catalog fix (qualified separately above).
The fixed complete-evidence-chain read is COMPLETE80877: all 33 catalog/media
reads matched, all four checks passed, no input restoration/source-root change,
224.823s; w-mBYCdo/project-imported-asset-read-qualified-summary.json. That
exhaustive batch duration does not prove acceptable interactive latency.
COMPLETE67129 profiled one asset: 13 runtime checks made 27 full root copies
(4.75 million deepcopy calls), 14.110s under profiling. Its report is
w-mBYCdo/project-imported-asset-read-profile.json. This revealed avoidable
internal copies, not justification to skip epoch, owner or checksum checks.

The final optimization uses the already-validated private root only for scalar
gate checks and one payload-membership check. Public Snapshot.state/_root still
copy their returned dictionaries. COMPLETE83272: 21/21 asset tests, 3.200s,
including an assertion that a media read makes no full-index copies and cannot
mutate public storage authority. Frozen final code: 966f92f111cb44e1a8b151f36bd5a51e.
COMPLETE59246: the full imported-chain 33-asset read passed all four checks in
36.767s (previously 224.823s). COMPLETE65297: profiled single read 1.799s
(previously 14.110s), runtime entry 5.781s; remaining cost is repeated commit-log
authority reads. These are observed CPU-copy timings, not a production/browser
latency guarantee. Reports: w-mBYCdo/project-imported-asset-fast-read-summary.json
and project-imported-asset-fast-read-profile.json.
COMPLETE24267: final-code real legacy-catalog replay passed 18/18 checks in
35.319s, 33 real assets / 14 branch reads, slice a-e32fa4; report
w-mBYCdo/project-asset-fast-mirror-qualified-summary.json. Prior evidence and all
live paths remain protected. No full-chain media/workflow replay is claimed.

Final qualification COMPLETE71691: 23/23 related entrypoints passed,
all_passed=true and sources_unchanged=true, project-asset-fast-qualified-regressions,
frozen 966f92f. It includes the 21 asset cases, actual writer integration, core
state/commit-log/ownership/carrier fences, generation, processing graph, Review,
retention and ordinary recovery. Every launched handle from this continuation
is terminal; do not restart them. Runtime/tests match the final frozen copy;
only these two repo status documents differ. git diff --check passed.

Next: finish input-side asset mutations/uploads/derived media/previews with
explicit input authority, two-store interruption/retry handling and source
materialization; cross-project browsing/duplication needs independent pins.
The actual Carousel build calls sync_reference_slots when connected references
are writable, so full node execution still needs that mutation port (ordinary
catalog/media routes passing is not proof of every Carousel build mode).
Reference conversion/recovery writers, automatic import contracts, remaining
damaged/partial-retention cases, production request/queue/UI integration,
full latest-code copied-chain workflow replay and platform checks remain open.
The ComfyUI core skill guided real service/API tests. No GPU/browser/native
Windows claims, installed-pack changes, live activation, commit or push.

## Previous checkpoint: atomic prompt-history writes

The previous goal turn only reported status (no implementation progress).
This continuation ports all six PromptHistoryStore mutators to an explicit
history write grant and one revision/index/receipt transaction. The same legacy
editing methods are used. Executed prompt text, parent, creation identity and
first execution timestamp cannot be overwritten. Imported immutable revision
metadata gets a witnessed replacement retaining its old bytes; inactive draft
deletion retires only its accepted logical entry, with a descriptor receipt.

Prepared requests hold a stable timestamp/revision ID, and committed retries
return the exact accepted response without incrementing execution counts twice.
Stale edits, late retries after newer history changes, wrong branch/ownership,
missing grants/operation IDs and corrupt accepted/prepared data fail closed.
The real HTTP handler now forwards the request identity and ownership proof.
Current Shot has a hidden unique_id and a separate host-issued history operation
identity/grant. Its accepted storage pin is stamped downstream. The browser
history clients issue retry identities even on LAN HTTP without randomUUID.
This is still copy-only host wiring, not production host/cutover activation.

Focused terminal evidence:
- 69931: 16/16 writer/API/carrier/corruption tests, 4.097s.
- 20537: 2/2 actual Current Shot and ordinary-recovery tests, 1.334s.
- Prompt-history JavaScript navigation and LAN-safe operation-ID tests passed.
- Earlier 75157 passed Current Shot but had a test-only wrong recovery API;
  that harness call is fixed. Earlier 79214 used a nonexistent fault phase;
  the corrected lost-reply case interrupts after real publication instead.

COMPLETE96889: all 112/112 entrypoints passed in
project-history-writer-regressions, frozen source
c50b92bb4b3e4de4932f782808261eef. qualification.json confirms
all_passed=true and sources_unchanged=true. A recursive source comparison
found only the two status-document updates since the freeze; runtime code and
tests still match. git diff --check passed. No test processes remain running.

Real history write replay is COMPLETE: all 27 indexes / 110 saved revisions
underwent 220 accepted label/restore mutations in h-d0884a, an independent
metadata-only slice. No full media/workflow replay is claimed. Run 47253 passed
all per-scene value checks but failed an incorrect final raw-JSON-whitespace
comparison. Its report is retained. No runtime fix or repeated mutation was
needed: 85769 independently verified exact JSON values, retained original raw
history bytes, untouched Plan/editorial/branch bytes, all 220 accepted receipts,
and ordinary recovery/public history reads. All 13 qualification checks passed
in 15.11s (this is the completion audit/recovery time, not the 220-write time).
Reports: private lab w-mBYCdo/project-prompt-history-write-summary.json and
project-prompt-history-write-qualified-summary.json. Both use frozen c50b92b.
Complete evidence-chain roots/controls are unchanged; audit hooks rejected
writes to those chains, the live project and user backup.

Still open: automatic import contract classification; repeated import of old
history-operation receipts and production request/job pin integration; remaining
asset/reference-cache writers; the damaged/partial-retention cases below; full
latest-code real copied-chain workflow replay; production UI/queue/cutover and
GPU/native-Windows/shared-filesystem qualification. No commit/push/activation.

The ComfyUI core skill guided actual node/API tests, not GPU/model claims.

## Previous qualification: portable quarantine and cross-workflow undo

The goal is active; this is not production migration approval. The live chain,
user backup, installed pack and Git refs remain unchanged. All changes are
local/uncommitted on nightly. The preceding status-only turn did not advance
implementation; this continuation reproduced and fixed a closed-receipt error
and added actual processing/PNG/chapter roundtrip coverage.

Verified evidence:

- The formerly running 66812 matrix is COMPLETE: **106/106 entrypoints**,
  all_passed=true, sources_unchanged=true, frozen source
  26b977c833d8408b985eb7683dab3920. Private report:
  project-recovered-roundtrip-regressions/qualification.json. It predates the
  portable-custody code and must not qualify newer edits.
- Current 7671: **25/25** real CPU Review/Save/recovery/public-undo tests,
  90.159s. Includes project-only re-import, resumed review completion,
  active-leaf assignment restoration, named-branch isolation and lost replies.
- Current 66267: **52/52** recovery, ownership recovery and project recovery
  tests, 6.056s. Current 38971: **9/9** portable-custody tests, 2.262s.
- New cross-workflow suite: 73810 passed six cases and exposed an already
  restored native chapter receipt returning HTTP400 after re-import. The
  missing old operation was being looked up even though no undo was pending.
  The closed-state preview now returns allowed=false without traversing old
  authority or rewriting anything. Focused 88889 passed **1/1** in 2.916s.
  Earlier 23051 had two test-only old-reader access errors; 94008 caught a
  missing STATUS import in the fix. Both are corrected, not waived.

COMPLETE57424: project-portable-custody-regressions, 108/108 entrypoints,
all_passed=true, sources_unchanged=true, frozen source
f1539052af924c728519e1b21bf5dd1e. COMPLETE2590: full preserved-copy read parity,
63/63 checks, 14 branches, 723 controls and 12 closed quarantine receipts,
627.584s. Private report: w-mBYCdo/project-portable-current-read-summary.json.
Both full chains were guarded against writes; this is not a writer/GPU replay.

After that freeze, PromptHistoryStore received its missing pinned-reader port.
The actual reproduction returned Unsupported H3 storage version (not deleted
data); the normal constructor now uses the runtime's accepted branch controls.
List/get and the real HTTP handler preserve the exact executed prompt/revision,
and old pins do not follow newer labels. Missing history is empty; checksum
failure, wrong project and unaccepted physical leftovers cannot be mistaken
for valid history. Six tests pass (0.859s), plus the existing legacy history
script. Mutations remain fenced until their separate transaction/host grant is
ported; a read adapter is not permission to overwrite immutable slots. These
edits postdate the 108-entrypoint freeze. COMPLETE78419 independently qualified
all six targeted reader suites against frozen 77d8a9d1f07b47b5abfae5ff325fba9e
(project-history-reader-regressions/qualification.json). COMPLETE61434 compared
all 27 indexes and 110 saved prompt revisions in both real copies exactly,
157.556s; no failed checks. Report: w-mBYCdo/project-prompt-history-read-qualified-summary.json.
That reader-only restriction is superseded by the new writer work above.

Recovery now carries pending native quarantine proof and exact required bytes
inside the recovered project, deduplicated by content hash. A project-only copy
can re-import and undo healthy generation, processing/PNG or chapter retirement
without relying on the external authority archive. Before/after controls,
mutable pointers, PNG indexes and chapter archive metadata are checked against
the original transaction. Later edits block undo, and independent media copies
publish atomically with restored controls. Plans/prompts/seeds are not rewritten.
Already-completed cleanup and already-undone candidates remain completed.

Remaining requirements (not waived by these tests):

- Portable restoration of explicitly missing/edited payloads; capture already
  preserves their exact condition, but the runtime restore adapter still blocks
  them. Legacy-tree undo using only the new portable capsule is not yet wired.
- Repair of older imported pending quarantines made before portable capsules;
  partially moved ordinary quarantines before completion; repeated retirement
  of an already-restored review candidate. Preserve old evidence, never guess.
- Automatic import classification must retain mutable control contracts. The
  new integration fixtures explicitly carry known contracts; that is not proof
  of the end-user classifier or prerelease partial chapter-index upgrades.
- Remaining asset/history/reference writers, damaged-artifact delivery
  invalidation, public retention inspection/purge, production host/queue/UI
  connection, full latest-code workflow replay on the real independent chain,
  and GPU/native-Windows/shared-filesystem qualification remain open.

The ComfyUI core skill guided real node/API tests. These CPU fixtures are not
GPU/model/browser proof. There is still about 26 GiB free on /media/p5, less than
one full existing chain copy; no extra 32 GB copy or evidence-tree removal.

## Historical qualification: repeated import and recovered-quarantine undo

The goal remains active. The live chain, user backup, installed pack and Git
refs are unchanged. No commit, push, production activation or full 32 GB copy.

- The previously running 18175 broad regression is terminal: 105/105
  entrypoints, all_passed=true, sources_unchanged=true, frozen source
  e2b5a322d2224d90875d7c8ca4d70028. Report in the private lab:
  project-recovered-review-regressions/qualification.json. This predates the
  repeated-import code below and is not proof for those newer changes.
- Repeated-import API suite 46171 passed 15/15 in 55.591s. Two additional
  cases (named-branch isolation and forged unrelated restore inventory) passed
  2/2 in 6.649s, handle 13173, after honoring the store's configured path budget.
- The original undo regression failed with a missing retention receipt after
  re-import (56004). Intermediate runs found the separate payload-join custody
  boundary and misuse of the generic damaged-payload receipt. Both are fixed.
  33892 passed 12/13 with one incorrect test read-view invocation; fixing that
  test's logical address and pinned operation yielded the passing 15-case run.
- COMPLETE66812: 106/106 project-recovered-roundtrip-regressions, frozen
  source 26b977c833d8408b985eb7683dab3920. This includes all 17 roundtrip cases.
  The private lab keeps results.json, per-entrypoint logs and terminal
  qualification.json (all_passed=true, sources_unchanged=true).
  Receipt: test-source-copies/26b977c833d8408b985eb7683dab3920/receipt.json.

An imported immutable deferred choice is now adopted with a new import-bound
witness, not republished over the old decision or Plan. Already completed
cleanup remains complete. Ordinary recovered quarantines can be undone through
the public runtime HTTP route after another import: initial controls and the
atomic payload-join custody are verified; exact independent media/control
restoration and the portable restored marker publish together. Retained bytes
and older pins remain available, but the old quarantine identities leave the
current catalogue so a later ordinary recovery does not create duplicate files.
No damaged-payload exception, permanent deletion or reclaimed space is claimed.

The covered cases include lost approval/undo replies, interrupted staging,
changed branch settings, occupied restore destinations, changed retained bytes,
wrong ownership or missing retention grant, named-branch isolation, forged
unrelated file inventories, previously undone imports, and another ordinary
recovery after undo. A completed quarantine with a missing final review
acknowledgement resumes without deleting an already restored rejection again.
These use actual CPU Save/Review, migration/recovery and HTTP handlers on
independent disposable fixtures, not the full real user chain or GPU/browser.

Still open, and not covered by the new adapter:
- Earlier organized quarantine receipts refer to bytes under the recovery
  output's external .h3-storage-recovery/authority directory. A project-only
  re-import does not yet carry that custody/evidence closure. Generic native
  retirement undo (including assignment updates and chapter archives) needs a
  portable, explicitly inventoried recovery capsule before repeat migration is
  considered complete. Merely returning a completed review is not undo proof.
- Re-import after a partially moved ordinary quarantine, before its completion
  witness exists, still requires a resumable migration/runtime repair path.
- Remaining asset/history/reference writers; full current real copied-chain
  workflow replay; damaged-artifact cleanup/delivery invalidation; generic
  retention inspection and purge; prerelease chapter-index upgrade; production
  host/queue/UI/cutover; GPU/native-Windows/shared-filesystem qualification.

The ComfyUI core skill guided real node/API checks. Disk capacity is now about
26 GiB free on /media/p5, less than one full existing chain copy. Existing
completed evidence copies and the user's backup remain protected.

## Historical qualification: recovered deferred cleanup and undo

The full goal remains active. No live chain, backup, installed pack or Git refs
changed; no commit, push or production activation.

Completed evidence:
- The previous broad run 60844 is terminal: 104/104 entrypoints passed,
  all_passed=true and sources_unchanged=true. Frozen source
  717042bee31f4c3cb5834d420c4acb89; private lab report
  project-chapter-retirement-regressions/qualification.json.
- Recovered-review integration 94353: 16/16 passed in 47.969s. These use actual
  CPU Save/Review, migration, independent reverse recovery and HTTP handlers,
  with disposable media fixtures. No model/GPU or browser rendering is claimed.
  Earlier iterations passed 9/9 (72878), 13/13 (47857), and 14/14 (95810).
- Focused authority-I/O test 11943: 1/1 passed in 2.851s against frozen source
  e2b5a322d2224d90875d7c8ca4d70028. A temporarily unreadable recovery authority
  produces 503 with no automatic retry or file cleanup.
- git diff --check passed after the final authority-I/O response adjustment.

Accepted but unfinished review cleanup can now resume on an ordinary recovered
tree without another activation or a Plan/seed/prompt rewrite. It validates the
pending batch and immutable decision against the preserved accepted storage
head, then rechecks current branch settings and lineage. Only inactive owned
rejected artifacts move into a hash-bound quarantine. Branch, chapter, final-cut,
generation and processing dependencies remain protective; nothing is physically
purged or reported as reclaimed.

The public undo route now handles these new recovered quarantines AND accepted
inactive review quarantines created before reverse recovery. The latter restore
independent copies from verified archived slots, never consume/rewrite archived
authority. Generic pre-recovery retirement involving assignment updates or
chapter archives is NOT handled by this adapter. Restored candidates stay
restored when the old review finalization is retried. A three-candidate test
crosses recovery after the first cleanup, undoes that take, then finishes the
remaining rejection without deleting the restored one.

Tests also cover wrong choice/keep list, ownership, named-branch isolation,
shared branch retention, saved processing dependencies, forged unrelated file
plans, tampered accepted decisions, changed Plan seeds, missing/damaged metadata,
occupied undo destinations, interruptions before/within/after publication,
lost replies, and the portable no-replace link/unlink interruption. The latter
accepts two names only when they are verified links to the same file; matching
contents in different files do not permit an overwrite.

RUNNING18175: 105-entrypoint project-recovered-review-regressions against the
independent frozen source e2b5a322d2224d90875d7c8ca4d70028. Includes 17 recovered
review cases, current Stop notifications and all chapter changes. Poll this
same handle and its terminal qualification report; do not restart on an
observation timeout. Receipt:
test-source-copies/e2b5a322d2224d90875d7c8ca4d70028/receipt.json in the private lab.

Remaining requirements are not waived: repeat migration of newly written
ordinary quarantine/undo records; full real copied-chain runtime replay of
these changes; remaining asset/history/reference writers; generic retention
inspection/undo/purge, damaged-artifact handling and delivery invalidation;
partial prerelease chapter-index upgrade; production host/queue/UI/cutover;
and GPU/native-Windows/shared-filesystem qualification plus the complete
supported-feature matrix. Existing full-copy 61/61 recovery qualifies its older
source, not these new changes. The ordinary recovered tree must retain its
.h3-storage-recovery authority directory to verify/resume these old approvals.

The ComfyUI core skill guided actual node and HTTP integration checks. No new
full 32 GB chain copy was attempted: /media/p5 still has about 27 GiB free;
completed evidence trees and the user's backup remain protected.

## Historical qualification: portable chapter order, retirement and Stop retry

This is ongoing integration, not live-migration readiness. No live chain,
backup, installed pack or Git refs were changed; no commit, push or activation.

Completed evidence:
- The prior 102-entrypoint run (37003) is COMPLETE, all_passed=true and
  sources_unchanged=true, frozen 010db47b7a9b437f8ad21174a7d8948a.
  Report: private lab project-chapter-snapshots-regressions/qualification.json.
- Chapter index tests: 10/10 passed (0.866s). Exact legacy mtime/path ordering
  is frozen before migration; equal seal timestamps and equal mtimes are
  covered. A recovered tree can acquire new legacy seals, then migrate and
  recover again without copied mtimes replacing its accepted latest snapshot.
  The entire existing history is validated, including retired-only histories.
  Corruption, changed source mtimes, invalid selector ownership and incomplete
  early selectors fail before a join journal/gate is created.
- Chapter node integration 3284: 16/16 passed (24.671s), including actual
  Chapter Delivery/Load after recovery, a new ordinary legacy save becoming
  latest, retirement returning to the older unretired snapshot, and exact
  source preservation. This pass precedes the subsequent partial-index guard.
- Chapter retirement integration 55107: 8/8 passed (16.429s). Actual HTTP
  preview/retire/undo; ownership and separate grant checks; stale-preview and
  cross-branch rejection; interrupted publication/lost reply/exact retry;
  releasing chapter recovery pins; reverse recovery of retirement tombstones.
  Only the immutable metadata address moves into an archive, reusing the exact
  descriptor/bytes. No media is deleted; undo restores the original descriptor.
  The preceding 20962 run passed 7/8: one fixture omitted its explicit pinned
  reader. Fixing that test wiring established the recovery-pin assertion.
- Quarantine primitive 13034: 62/62 passed (4.472s), including typed archive
  controls, invalid destination rejection, forged descriptor rejection and undo.
- Stop partial-export retry 25125: 14/14 passed (38.511s) after restoring the
  resolved websocket event on retry. Original Stop token/node identity is now
  saved with the accepted decision; no new gate, render or output is required.

RUNNING60844: 104-entrypoint project-chapter-retirement-regressions, frozen
717042bee31f4c3cb5834d420c4acb89. Poll the same handle and terminal qualification.
This copy covers all chapter changes but PRECEDES the Stop notification edits.
Receipt: private lab test-source-copies/717042bee31f4c3cb5834d420c4acb89/receipt.json.
Newer focused runs are COMPLETE: 58232 passed 16/16 partial-export tests
(39.975s), including a lost websocket reply, changed current display ID,
original saved token/node/video redisplay, and forged-token rejection before
publication. 6202 passed 31/31 Review execution tests (69.968s). Stop retries
resend only their resolved event; approval, continuation and deferred paths
retain their previous behavior. These focused runs cover the newer notification
edits; the older frozen broad run does not. Browser rendering remains untested.

Remaining chapter gates: full real copied-chain chapter runtime replay and
shared-filesystem/native-platform qualification of these changes; initial
control-import classification of portable chapter indexes must retain their
branch-owned mutable cuts contract. Earlier prerelease four-field selectors
without complete history cannot safely order additional snapshots: explicit-ID
load remains available, ambiguous blank load/re-import fail closed. This is
a remaining upgrade case for earlier rehearsal copies, not silently rewritten
history. Ordinary pre-migration legacy folders have no such index.

Other open gates remain: accepted-but-unfinished deferred cleanup after reverse
recovery; remaining asset/history/reference writers; damaged-artifact cleanup
and delivery invalidation; public retention inspection/undo/purge; production
host/queue/cutover; actual GPU/native-Windows/shared-filesystem qualification;
and the complete supported-feature real copied-chain matrix. The Stop retry
transport fix still needs browser-render verification. Earlier 61/61 full-copy
recovery is evidence for its older frozen source, not all subsequent edits.

Disk still has about 27 GiB free on /media/p5; do not start a full 32 GB copy
without checking capacity again. Completed copied-chain evidence and the
user's backup are preserved. The ComfyUI core skill guided API node wiring and
actual CPU PromptExecutor tests; those tests do not claim GPU/browser proof.

## Historical qualification: Chapter Delivery/Load runtime port

The goal remains active. No live project, backup, installed pack or Git refs
were changed; no commit, push or live activation.

Completed evidence:
- 89562 is terminal success: 98/98 entrypoints, all_passed=true and
  sources_unchanged=true, frozen 3b19d470e3044cff9083203997d8468c.
  Private lab report: project-review-partial-regressions/qualification.json.
- Chapter integration 43216: 15/15 tests passed in 22.657s. Actual CPU
  PromptExecutor -> Chapter Delivery -> assembly; named-branch isolation;
  partial chapter growth; pinned ALT picture/base audio; DeRoPE video and AV;
  imported legacy snapshot IDs; disabled ALT-only passthrough; interrupted
  publication, lost acknowledgement and old retry preserving newer heads;
  damaged/retired snapshot rejection; independent reverse recovery, ordinary
  legacy load/assembly and legacy chapter retirement.
- Existing chapter recovery safety 25693: 13/13 passed (0.328s).
- Existing incremental chapter export 22220: 21/21 passed (2.872s).
- Earlier iterations: 5124 exposed missing source markers plus test fixture
  errors; 15556 passed 5/6 with an incorrect acknowledgement injection; 94200
  passed 10/10. Expanded 18313 passed 13/14: ALT-only Chapter Delivery was
  already unsupported. Its test now verifies explicit rejection without a
  write, disabled passthrough and the existing direct export route instead.
  No new ALT-only chapter feature is claimed.

The public nodes now use a branch-pinned ChapterSnapshots service. A new seal,
its latest selector and invocation receipt publish atomically. Exact retries
reuse the accepted seal; resealing an older saved chapter does not move the
newer selector. Chapter Load can receive an optional current Plan for branch/
pin/ownership, or an explicit read-only branch ID. Archived ownership is not
borrowed. New snapshot identities exclude invocation ownership/storage pins;
old stored snapshot identity rules are retained. Chapter selection preserves
resolved ALT/DeRoPE source markers. Raw saved settings are checked before
resolution can reload canonical metadata and hide a changed prompt or seed.

After the 15-test pass, the internal loader was also routed through the pinned
reader and the legacy writer explicitly rejects a runtime bypass. A sixteenth
integration test covers those guards and the public node schema. These final
changes are in the fresh broad run, not retroactively qualified by 43216.

RUNNING37003: 102-entrypoint project-chapter-snapshots-regressions, independent
frozen source 010db47b7a9b437f8ad21174a7d8948a. Poll that handle and inspect the
terminal qualification report; do not restart solely on an observation timeout.
Receipt: test-source-copies/010db47b7a9b437f8ad21174a7d8948a/receipt.json
under the private lab. The runner includes the new chapter suite and existing
chapter recovery, incremental export and chapter-resolution frontend tests.

Still required for chapter completion: preserve exact imported legacy "latest"
ordering (current imports lack publication selectors; equal saved seal times
fail closed, rather than guessing from copied blob mtimes), ensure latest
selection survives reverse recovery with multiple snapshots, and port public
chapter retirement to the combined storage transaction path. Test imported
historical/retired cases and full real copied-chain chapters. Current passing
legacy retirement test runs only on an independently reverse-recovered copy.

Other open gates remain: accepted-but-unfinished deferred cleanup after reverse
recovery; partial Stop retry UI notifications; remaining asset/history/reference
writers; damaged-artifact cleanup and delivery invalidation; public retention
inspection/undo/purge; production host/queue/cutover; GPU/native-Windows/shared-
filesystem qualification; and the full supported-feature copied-chain matrix.
The prior complete full-copy recovery (61/61, 14 branches, 27,796 files) remains
valid for its earlier frozen source, not later runtime changes.

Disk remains about 27 GiB free on /media/p5. Do not start another full 32 GB
chain copy without a fresh headroom check. CPU pixel fixtures/fake transport
are not GPU-model or browser-render proof. The ComfyUI core skill guided the
actual node/executor test wiring; it did not authorize live execution.

## Historical qualification: partial-stop exports and completed full-copy recovery

This section supersedes the historical running/pending notes below. The full
goal remains active. No live project, backup, installed pack or Git refs changed;
no commit, push or live activation.

Verified after the prior status checkpoint:

- Deferred-finalization broad qualification is COMPLETE: 95/95 entrypoints,
  all_passed=true and sources_unchanged=true, frozen
  764ff11532324ed593c7420a6a6437b8. Report in the private lab:
  project-deferred-finalization-regressions/qualification.json.
- Post-retention full independent reverse recovery is COMPLETE: 61/61 checks,
  14 branches, 27,796 files, 723 controls and 10,153 payloads, 1,456.984 seconds.
  Branch authoring/graph/processing catalogue parity, exact bytes, independent
  inodes, interruption before ownership activation, resumed publication and
  stable retry passed. production_modified=false, gpu_generation_tested=false.
  Report: w-mBYCdo/post-retention-recovery-summary.json, frozen d116 source.
  These historical passes do not qualify later code retroactively.

Current implementation publishes the accepted Stop/selected take first, then
assembles from an export-only successor pinned to that decision and any accepted
candidate cleanup. Parent readers keep their earlier pin; other writer grants
are not inherited. Video/sidecars, the partial manifest and its Review-bound
receipt publish together. Failed publication retains the prepared encoding;
lost replies and old retries do not re-render or roll back newer outputs.
Storage/ownership/I/O failures cannot authorize a silent-audio fallback.

The actual PromptExecutor exposed valid ComfyUI NaN cache markers in archived
API prompts. ProjectReadView now decodes only verified workflow/api_prompt
archive roles as opaque JSON. Plans/authority retain strict non-finite and
duplicate-key rejection; unaccepted leftovers and damaged archives cannot
satisfy reads. A second real failure showed mixed-chapter selection was still
using the legacy physical resolver. Its non-persisting helper now accepts an
explicit pinned reader; it does not grant legacy snapshot writes.

Focused evidence:

- 23560: 14/14 actual CPU partial-stop integration tests, 35.119s. Covers real
  PromptExecutor stopping before scene 2 with exact archived cache markers;
  selected earlier prompt/uint64 seed/pixels; original sound under a prior ALT;
  generated/silent audio, failed publication and lost acknowledgement; corrupt
  accepted output rejection; no-export-grant rejection; named-branch isolation;
  cleanup then export; old retry preserving a newer export; mixed-size chapters
  (32x32 then 64x32) selecting only the current chapter; independent reverse
  recovery and ordinary legacy assembly. Model pixels and UI transport are
  fixtures, not GPU generation or browser-render proof.
- 25295: 17/17 pinned-reader tests, 2.460s, including opaque archive roles,
  exact uint64 values, retained strict authority checks and damaged bytes.
- 63884: existing legacy chapter-delivery suite passed.
- Earlier focused iterations: 91579 6/6, 59566 12/12. Expanded 85599 found the
  mixed-chapter resolver defect and a recovery test that wrongly expected a
  pristine-copy retry after writing a new legacy export. Both are corrected:
  retry before new writes is stable; after new writes it refuses to overwrite
  untracked work. Unit-test fixture errors (invalid category and exception
  scope) were corrected without loosening production validation.
- git diff --check passed before freezing.

RUNNING89562: 98-entrypoint project-review-partial-regressions on frozen source
3b19d470e3044cff9083203997d8468c. Receipt:
test-source-copies/3b19d470e3044cff9083203997d8468c/receipt.json in the private lab.
The suite includes the new partial-stop tests plus existing chapter delivery
and chapter resolution tests. Poll the same handle; do not restart on timeout
or claim completion before its terminal result and qualification report.

Immediate remaining work includes the public Chapter Delivery node's persistent
snapshot path (the new partial helper is read-only, not that writer), resuming
accepted-but-unfinished deferred cleanup after reverse recovery, and browser
notification/redisplay on a retried partial Stop. Also open: remaining asset/
history/reference writers, damaged-artifact cleanup and delivery invalidation,
public retention inspection/undo/purge, production host/queue/cutover, new ports'
GPU/native-Windows/shared-filesystem qualification, and the full-feature real
copied-chain matrix. Historical coverage tables below are milestone evidence,
not proof those remaining requirements are complete.

Disk check: /media/p5 has about 27 GiB free (99% used). Do not start another full
32 GB chain copy without fresh capacity/headroom validation. Current tests use
small disposable fixtures and an independent code copy; neither the live chain
nor the user's backup was deleted or changed.

## Historical qualification: deferred approval and completed retention replay

This section supersedes running/pending statements in every historical section
below. The full objective is still active; no live-chain activation.

COMPLETED6317: 92/92 entrypoints passed, sources unchanged, on frozen
8dfeb267706c4467811a288f2e5433e0. Report:
project-deferred-review-regressions/qualification.json in the private lab.
This qualifies deferred save/read/list/prepare, not the later approval port.

COMPLETED55290: all24 checks passed on the full independent processing copy:
11 real profile deletion/undo cases, 549 owned PNGs exercised, all accepted
payloads verified, every original baseline file unchanged. Report:
w-mBYCdo/project-processing-retention-optimized-summary.json, frozen d116 source.
No physical purge; quarantine and exact undo retain immutable bytes.

RUNNING13850: full post-retention reverse recovery, frozen d116 and its qualified
91-suite report. It writes only post-retention-recovered-output and its private
journal/report in w-mBYCdo. Source is the completed processing-retention-output,
not the live chain. Before launch free space was60,568,621,056 bytes; the recovery
plan passed its independent-copy plus20GiB headroom check. It captured14 branch
baselines and is copying files. Poll the same handle; do not restart on timeout.

Later worktree code now publishes deferred candidate recovery archives and a
fixed approval decision together, then retires rejected candidates with exact
per-target previews and a completion receipt. Ownership and generation/review/
retention grants are separate. Interrupted cleanup resumes without selecting
again; changed keep lists or later branch work conflict rather than overwrite.
Completed inventory stays hidden after reverse recovery. Frontend retries skip
reactivation and report quarantine/undo accurately; accepted choices are locked.

Focused44981 passed5 tests. Expanded35734 passed10/11; the tampered private
preview correctly stopped writes but returned400, corrected to409. Focused64174
passed11/11 (32.870s). Prior deferred integration47876 passed14/14 (16.079s).
Frontend request-coordinator tests pass (actual coordinator with fake transport/
DOM), as does the existing Review JS suite after updating its stale assertion
for a pre-existing policy_inputs intermediate variable. Not browser-render proof.
COMPLETED38895 passed13/13 (39.652s), including cross-branch protection and
three-take interrupted cleanup. RUNNING84098:95-entrypoint broad qualification
(93 Python entrypoints plus two frontend tests), frozen source
764ff11532324ed593c7420a6a6437b8; private lab label
project-deferred-finalization-regressions. Poll that exact handle; no pass claim
until its qualification report is written and it exits successfully. The
full-chain recovery13850 uses the earlier d116 source, not this later port.

Remaining gates include finishing cleanup after reverse recovery of an already
accepted but unfinished batch: this currently fails closed instead of invoking
legacy destructive pruning. Completed restored reviews require no action.
Partial-stop assembly, remaining asset/history/reference writers, public
retention inspection/undo/purge, production host/browser/cutover, GPU/native
Windows/shared-filesystem qualification, and full-feature copied-chain coverage
remain open. These limitations are not a claim of completion.
No live chain, backup, installed pack, git refs, commit or push changed.

## Historical qualification: processing optimization and deferred Review

Historical checkpoint; see the latest qualification above. Both processing
regression runs are COMPLETE: 97697 qualified
91/91 entrypoints on frozen 11a29982c34f46d0b138af5d513d0ccf; 88908 qualified
91/91 on frozen d116423a1f7b4dc1b033792ca265abff. Both report all_passed=true
and sources_unchanged=true.

The first full writer replay44650 was gracefully interrupted after a verified
deletion and undo because its comparison deep-copied the whole catalogue once
per entry. Its failed report is retained. Production retention/custody code had
similar repeated copies; these now use a single private descriptor copy per
operation. No integrity check was removed. Focused tests55710 passed58/58
transactions and40445 passed18/18 actual processing integrations, including a
bounded catalogue-copy regression.

RUNNING55290 resumes only the verified, exactly restored independent copy:
processing-retention-output. The optimized report is
w-mBYCdo/project-processing-retention-optimized-summary.json. It rehashed all
27,563 original files (31,988,907,053 bytes), verified inode independence, and
validated the earlier accepted undo receipt before continuing. At last
observation seven of eleven profile deletion/undo cases had passed. Do not start reverse
recovery until this handle is terminal and the report says finished with all
checks passing. The prepared processing-retention recovery mode requires the
optimized d116 source and its 91-suite report; the newer Review code does not
retroactively qualify these full-chain operations. Fresh disk headroom was
57 GiB; recheck before making the recovery copy.

Later worktree changes add atomic deferred Review batch/stop publication,
accepted-index listing/loading, exact retry/lost-ack recovery, immutable
candidate provenance and portable logical preview addresses. The actual
PromptExecutor stops without advancing another scene. Named branches remain
isolated; missing accepted controls are errors rather than empty inventory.
Reverse recovery on disposable actual CPU fixtures preserves exact Plan/uint64
seed and playable synchronized previews. Runtime grants and ownership remain
separate. Focused34789 passed13/13 tests; earlier4512 passed10/10 and61264
passed12/12. Legacy Review-length/deferred coverage68815 also passed.
The actual prepare endpoint reads the exact candidate lineage through the
accepted view without promoting or pruning. Focused78699 passed14/14 tests
(30.937s), including that endpoint. Earlier31039 failed only a fixture assuming
an optional scene_prompt_template existed; the exact saved fallback assertion
was corrected. New92-entrypoint qualification is prepared under
project-deferred-review-regressions; see the latest launch note for its exact
frozen-source receipt and live handle. Do not claim a pending run passed.

Deferred finalization/cleanup is NOT ported by this change: the legacy
activate-then-finalize route still needs a typed atomic archive/decision
publication and durable cleanup successor. Partial-stop assembly, remaining
asset/history/reference writers, public retention inspection/undo/purge,
production host/browser/cutover, GPU and native Windows/shared-filesystem
qualification, and the final whole-feature copied-chain matrix still remain.
No live chain, user backup, installed pack or git refs changed. No commit,
push or live activation. The full goal remains active.

### Latest launch checkpoint

RUNNING6317:92-entrypoint project-deferred-review-regressions, frozen source
8dfeb267706c4467811a288f2e5433e0. The receipt is
test-source-copies/8dfeb267706c4467811a288f2e5433e0/receipt.json in the private
lab. RUNNING55290 remains the full processing-retention copy replay on d116;
seven of eleven profiles completed at the last observed report. These are
different frozen scopes. Poll these same handles; do not restart on a timeout.
Reverse recovery is still NOT launched. No source edits after freezing other
than this documentation checkpoint. No commit/push/live activation.

## Historical processing/PNG retention qualification

This section supersedes all running/pending statements in the historical notes
below. The earlier frozen be4ea3165e9d48cebdf519cf587e2c16 run is COMPLETE:
90/90 entrypoints, all_passed=true and sources_unchanged=true. Its read-only
real-chain replay is also COMPLETE: 18 checks, 28 generation deletion previews,
14 branches, 564.014s. Neither result qualifies the later processing changes.

Current code adds typed processing cleanup over accepted logical controls,
including canonical pointers, affected delivery manifests, PNG ownership and
variant tombstones in one reversible transaction. Independent pixel successors,
shared owners, other working branches and dependent processing sources are
checked. Imported immutable indexes are deliberately replaced by new versions;
old versions and exact undo descriptors remain retained. Public mutable writers
cannot acquire this replacement permission.

Explicit cleanup now witnesses missing owned payloads and edited owned PNGs.
It never edits media or rewrites original recorded output hashes. Undo restores
the pre-deletion catalogue/state: edited bytes stay edited; missing bytes remain
missing. Integrity checks still report those conditions after undo. Missing/
corrupt control metadata, unreadable files and corrupt non-PNG payloads are not
silently accepted. Physical purge/reclaimed space remains zero.

Focused results: 75425 passed46/46 transaction tests (3.342s), 19712 passed31/31
legacy processing deletion tests (4.271s). After custody support, 41851
passed58/58 transaction tests (7.552s), and 43349 passed17/17 actual CPU
processing integration tests (57.777s). These include actual HTTP ownership/
undo, named-branch isolation, imported-index replacement, shared PNG owners,
independent scene continuation, source-dependent DeRoPE protection, edited/
missing artifacts and reverse recovery without tombstone resurrection.
Earlier 21380/81337 failures were fixture category/PIL import mistakes, corrected
without relaxing invariants. The imported variant fixture now uses the real
variant format/settings and is included in the frozen broad run.

CURRENTLY RUNNING: 97697, 91-entrypoint broad qualification on frozen source
11a29982c34f46d0b138af5d513d0ccf, project-processing-retention-regressions.
CURRENTLY RUNNING: 44650, replay_processing_retention.py on that same frozen
source, copying the full real chain into processing-retention-output and
exercising one saved take per processing profile with actual deletion/undo.
The original full-chain baselines and external ownership records are write-
protected; only the existing Linux coordination lock may be opened.
The first replay7172 stopped before copying because the guard also blocked
that lock; its failed report is preserved. The retry writes
w-mBYCdo/project-processing-retention-qualified-summary.json.

No live-chain, user-backup, installed-pack, git-ref, commit/push or activation
changes. These jobs must be observed to terminal status; an observation timeout
is not completion. Full-copy recovery after the latest writer operations,
remaining Review/asset/history/reference writers, public cleanup inspection and
purge UX, production host/browser/cutover, GPU and native Windows/shared-filesystem
qualification still remain goal gates. Do not mark the overall goal complete.

At the latest observation, 24/91 regression entrypoints had passed with no
failures. The full-copy replay passed six setup checks: all27,563 files
(31,988,907,053 bytes) are hash-equal independent copies; the original baseline
root is unchanged. Processing-profile operations have started, but no completed
profile case was reported yet. Keep observing97697 and44650.

The next full recovery replay is prepared, NOT launched:
recover_post_export_copy.py with H3_RECOVERY_VARIANT=processing-retention.
It requires both current qualification reports to be finished/passing and the
same H3_TEST_SOURCE_RECEIPT11a29982c34f46d0b138af5d513d0ccf. It will create
post-retention-recovered-output, inject an interruption before ownership
activation, resume, and compare all14 branches, processing catalogues, exact
control bytes and payload independence. Check current disk headroom first.
Do not start it while the current full-copy writer is still active.

## Earlier generation retirement and Review cleanup

Supersedes the older progress notes below. Production code is frozen in
be4ea3165e9d48cebdf519cf587e2c16 for a new 90-entrypoint qualification,
project-review-cleanup-regressions (outside sandbox). This run includes the
new retention service and actual Review cleanup. Its result is not yet known.
The earlier 88-suite result does not qualify these later changes.

Generation previews/delete/undo now use typed references, fresh full-root
validation and a separate retention permission. The existing HTTP deletion
handler dispatches without entering a legacy layout lock. Undo endpoints are
present, but production host/UI wiring and live activation remain unqualified.
Invalid matching source addresses are protected as unknown. Shared attributed
media stays available; a processing reference to an alias itself still blocks
retiring that alias.

Actual Review now records the exact kept/rejected batch, publishes its selected
take first, then runs typed quarantine. Persisted previews, individual undo
receipts and a final cleanup witness support interrupted/lost-ack retry without
another gate, another deletion, or overwriting later work. Loop End verifies
that exact successor and the original Save independently. Immutable bytes stay
retained; reclaimed space is zero. Separate callable retention grants cannot be
borrowed by unrelated nodes or supplied through workflow JSON.

Fresh focused results: 84518 passed18/18 retention tests (29.101s); 90420 passed
21/21 expanded tests (25.948s); 61492 passed15/15 actual CPU Review cleanup tests
(60.021s), including PromptExecutor cache-none, named branches, stop, reversal
and ordinary Loop Start resume with the selected prompt/uint64 seed/latent.
A 22nd alias-identity safety test and an exact RGB fixture input correction are
included in the frozen broad run. Previous failures are retained: 96253 exposed
the shared-alias false blocker (fixed); 53580 exposed a fixture passing candidate
one's latent when checking candidate two (corrected, no invariant relaxed).

No live chain, backup, installed pack or git-ref changes. No full-chain writer
is running. Full copied-chain writer replay of this code remains required.
Still open: processing/PNG retirement with edited-owned frames, public cleanup
inspection/undo and separately confirmed permanent purge; Review deferred
finalization/partial-stop assembly; delivery invalidation and damaged-artifact
cleanup; remaining asset/history/reference writers; production host/cutover;
Windows/shared-filesystem qualification and final full feature matrix.

Current running handles: 81952 (90-entrypoint frozen-source qualification) and
53465 (read-only real copied-chain public retention previews, all14 branches).
Observe these exact handles/reports; an observation timeout is not completion.
The latter script guards both migrated and recovered trees against writes:
w-mBYCdo/project-generation-retention-previews.json. It tests representative
leaf/ALT targets per branch, not all revisions or full-chain deletion writes.
At the last observation19/90 regression entrypoints had passed with no failure,
and10 copied-chain previews across5 branches had passed with no error.

Concrete next port: processing_checkpoint_delete._documents/_target/_preview
still enumerate physical legacy directories and compare physical artifact paths.
Its delete loop uses os.replace/unlink and separate PNG JSON rewrites; never
route that path into immutable slots. Port domain discovery to accepted logical
controls and retire files, canonical pointers, affected manifests and PNG owner/
tombstone changes in one reversible transaction. Keep independent pixel
successors and shared owners. Edited-owned/missing PNG cleanup requires an
explicit custody/integrity policy, not merely suppressing checksum failures.
Review cleanup's returned status is accurate for its accepted historical pin;
public UI cleanup inspection and current undo-state presentation remain open.

## Earlier quarantine and retention progress

COMPLETED93851:88/88 entrypoints, all_passed and sources_unchanged true,
project-quarantine-regressions, frozen314df292f69c454fa338d69cdfe72f4a,
outside sandbox. The final PNG-video36-test entrypoint passed in121.88s;
no timeout/restart was needed. Subsequent source edits are documentation only.
No regression or copied-chain replay is currently running.

COMPLETED27457:18/18 checks,256.215s, replay_retention_references.py on that
same source. Actual sealed-chapter retention parity across14 branches,
406 revision checks and224 reference edges on the real copied/recovered chain.
Both chain trees reject writes through the audit guard. Root50f25640... stays
unchanged; all accepted controls and frozen sources verify. Report:
w-mBYCdo/project-retention-reference-reads.json. This is reference-reader
qualification, not public deletion permission or full-chain writer replay.

COMPLETED43863:85/85 entrypoints, all_passed and sources_unchanged true,
project-review-selection-regressions, frozen932bd77fb20d49b1a91a0a9af1df538f.
That qualification predates quarantine. The old1347 handle is gone; fresh53007
completed34/34 transaction tests (2.414s) on both storage protocols.

storage_quarantine.py now provides exact reversible catalogue retirement:
full-root revalidation, immutable undo receipts, interruption/lost-ack retry,
original descriptor restoration, and verification of payloads being removed
from the index. Bytes stay in immutable slots, so reclaimed_bytes is zero.
This primitive grants no domain deletion permission and exposes no public purge.

COMPLETED16311:2/2 real CPU Review/graph/Loop integration tests (6.450s).
A rejected candidate disappears from the current graph while old pins and the
approved take's exact prompt/uint64 seed/context remain usable. Undo restores
the complete graph; independent reverse recovery does not resurrect the
rejected take and ordinary Loop Start resumes the approved context.
These are disposable fixtures, not latest full-chain writer qualification.

CheckpointGraphManager's sealed-chapter retention check now reads all accepted
logical snapshot controls, including named branches, instead of absent legacy
directories. Owned artifact paths resolve after relocation, supersedes remains
history only, and unrelated external references are not false blockers.
COMPLETED60997:8/8 retention reference tests (1.271s).

Next: implement full typed generation/processing/PNG deletion permission,
public/runtime grants, Review
prune ordering, edited-owned PNG cleanup, permanent purge, deferred Review and
full-chain writer replay remain open. Do not enable legacy deletion against
combined paths. No live chain, backup, installed-pack or git-ref changes.
No copied-chain writer is active.

## Earlier candidate-selection progress (historical)

COMPLETED43863:85/85-entrypoint project-review-selection-regressions, frozen
932bd77fb20d49b1a91a0a9af1df538f, outside sandbox. COMPLETED79645:31/31 focused
Review execution tests on the same source (70.496s), including actual reverse
recovery and ordinary Loop Start resume. No copied-chain writer is running.
All subsequent worktree edits are documentation only; observe the existing
handles/reports rather than restarting them after an observation timeout.

Next: combined deletion/retention is still unported. CheckpointGraphManager
explicitly fences deletion_preview/delete on a combined reader; do not remove
that guard and feed immutable physical files into the legacy os.replace path.
Review cleanup must occur after selected assignment acceptance, with its own
receipt. Its provenance must not turn a rejected source Save into a permanent
false deletion blocker: the current Loop adapter verifies the original source
media, so retirement needs an explicit witnessed transition before removing it.
Consult STORAGE_MIGRATION_PLAN.md typed-retention requirements before that port.

Candidate-selection implementation is now present in storage_review_selection.py.
The actual Review node prepares selection read-only, then publishes canonical
checkpoint, exact recovery archives and the Review witness in one commit. A
separate generation grant is required. Returned prompt/seed/context tensors are
bound by a digest; durable records contain no tensors or ownership credentials.
Prepared/lost-ack retries reload the selected immutable take without reopening
the gate or assigning the rejected take again. Named branches and imported
prompt sidecars are covered; picture-only ALT cannot become generation context.

Focused passes:22261 24/24 (51.608s);24822 30/30 (74.907s), covering actual
PromptExecutor multi-candidate completion, selected-state tampering, publication
failure, lost acknowledgement, stop, named-branch isolation and imported media.
72586 independently recovered a selected fixture into the ordinary layout,
verified independent media bytes, and resumed the real Loop Start from that
take (2.348s). The final alternate-kind guard changed after24822 started; the
next frozen broad run must qualify all current code. Earlier50426 failed because
a legacy fixture loader lambda did not accept the new optional read-view keyword;
the legacy call signature is preserved again and57437 passes the existing Review
length/selection suite. No test invariant was relaxed.

60607 COMPLETED:85/85, all_passed and sources_unchanged true, frozen
47b879a6e17f46ed9e11940279cc82cc, project-review-execution-regressions.
That run predates candidate selection. Full current-source qualification and
real-chain Review writer replay remain required. Candidate pruning/deletion,
deferred review and partial stop assembly remain open, along with the other
full-goal gates below. No live chain, backup, installed pack or git-ref changes.
No full-chain writer is active. Tests use disposable independent CPU fixtures.

## Constraints and test environment

- Work on nightly; preserve existing uncommitted changes. No commit/push requested.
- Test data: private lab `/media/p5/test/h3-migration-sim-rAduWnmS`.
- Original chain and the user's `h3_chains (copy 1)` backup stay untouched.
- User explicitly permits live ComfyUI testing and necessary restarts while
  idle. Recheck queue before restart; do not interrupt unrelated new work.
- The user accepts the original branch's pre-existing scene-6 archived-Plan
  mismatch as a baseline, not a migration blocker. Preserve those files and
  ordinary strict-history validation; do not silently rewrite seeds/context.
- Live server observed: ComfyUI 0.35.0, Python 3.13.14, PyTorch 2.13.0+cu130,
  RTX PRO 6000; queue empty. Backup directory existence confirmed, not deep-verified.
- Real model execution must use an isolated test project, not production prompts
  or output assignments. Native Windows/SMB results must be recorded separately.

## Coverage / remaining work

### Latest qualification (supersedes historical run notes below)

NEWER implementation: actual Save -> async Review -> Loop End now carries an
immutable Review transition, independently verifying the source Save and full
incoming state (including predecessor tensors and candidate inventory). Host
handoff operation IDs make prepared/accepted results retryable without a new
gate, rerolled seed or pointer rollback. Approve, edited retry, automatic batch
retry and stop-without-assembly are ported; picture selection/pruning, deferred
review and partial stop assembly remain open. Optional audio previews use
indexed immutable media under project/optional/previews, separate export grants
and private encoder staging. No live layout activation.

Focused actual Review integration PASSED18/18 (16629,60.313s), including the real
ComfyUI PromptExecutor, two scenes, edited recursive retry and uncached execution.
A named-branch case has subsequently been added and needs qualification.
Earlier39268 failed two graph cases: Comfy cache is_changed=[NaN] reached the
generation save's strict input witness. SceneSaveRequest now preserves opaque
non-finite prompt/workflow metadata as exact strings, domain-separated from
strict state, and retains previous finite-input hashes. Save retry PASSED14/14
(49961,11.587s). Earlier54431 failed only a misplaced fixture assertion, since
corrected. Review inventory PASSED21/21 (69662,4.126s) after per-review scopes
replaced the unnecessarily conflicting per-branch append scope. This allows
a new gate at the same saved pin after interruption without reusing a decision.
Legacy Review length/final preview passes (54745).

RUNNING60607: fresh85-entrypoint qualification on frozen source
47b879a6e17f46ed9e11940279cc82cc, label project-review-execution-regressions,
outside sandbox. COMPLETED49823:19/19 focused Review execution tests pass
(56.746s), including the named branch, with identical production code.
Later edits are documentation only.
The current source still needs successful broad qualification and real-chain replay.
All full-chain writer replays still use older frozen code. No source/copied-chain
writer is currently active. Original, backup, installed pack and git refs unchanged.

COMPLETED1349:84/84 whole-source qualification passed, label
project-review-inventory-regressions, outside sandbox, frozen
cd849a962523474fab6603ca2a2fd772. Includes new21 Review inventory tests and legacy
Review Gate entrypoint. COMPLETED91083: replay_review_inventory_reads.py on the
same frozen source; read-only real copied/recovered chain parity across14
branches, actual reconnect handler, pending/decided/batch inventory all pass. Report
w-mBYCdo/project-review-inventory-reads.json. No full-copy writer is active.

Current continuation:39575 TERMINAL0, all82 entrypoints pass on frozen
430f9aa0db2e4cd49504c541b5bed72b, sources unchanged. See
project-delivery-assembly-qualified-regressions/qualification.json.

84218 TERMINAL0: post-export reverse recovery PASSED61/61,1470.293s.
post-export-recovery-summary.json records14 branches,687 current controls,
10,153 payloads and27,645 total recovered files including retained evidence.
Interruption before active ownership leaves the target gated, never guessing
ownership. Resume restores exact owner, all branch authoring/graphs/processing
catalogues, exact current control bytes and independent payloads. Ordinary
recovered readers and retry pass. Source root50f25640.../owner40 and ownership
history are unchanged; all payload hashes verified. This replay used qualified
cce sources and does not qualify subsequent production edits.

NEWER:storage_review_controls.py ports durable Review inventory under explicit
orchestration grants. Immutable snapshots plus exact-bound decision receipts
avoid rewriting imported archives. Old pins stay old; caller ownership is
rechecked at publication; lost acknowledgements fail; missing accepted decision
bytes cannot resurrect a pending gate. Actual reconnect listing reads both
branches from one pin, de-duplicates live gates, makes restart inventory
non-actionable and reports unavailable unhosted projects. Actual superseding
helper is branch/scene specific.21 tests PASSED72363 (3.865s), including actual
independent reverse recovery; legacy Review Gate78091 passed. Earlier33242
failed on two fixture mistakes (filename separator/receipt placement), fixed
without relaxing validation. Fresh broad and full-copy qualification are next.

This is inventory only. Actual async Save→Review→Loop End transition witnesses,
preview mux, candidate selection/pruning, deferred finalization and stop
assembly remain open. No active copied-project writer remains after84218.
No live chain, backup, installed-pack or git-ref changes.

2026-09-11 latest: **82/82 entrypoints pass**, sources unchanged, on frozen
`cceebeac7f4c4296be39c6ce065d615c` (83345 terminal0;
`project-assembly-graph-regressions/qualification.json`). This qualifies the
Merge/processing handoff/Review-preview fixes below, not subsequent edits.

Real copied assembly attempt 16706 stopped at an over-strict metadata assertion.
A separate read-only comparison reconstructed the original input at its exact
pin: ONLY the manifest's canvas changes from the Plan to the actual saved
960x544 clips; every other manifest value and archived tag matches. The MP4's
JSON object ordering also differs from the sorted prepared record, without a
value difference. No production correction was needed for that failure. The
failed report remains unchanged. A focused actual-encoder regression verifies
the saved-geometry normalization, all other tags, immutable segments and input
nonmutation; 26/26 assembly tests passed (84796,35.351s).

Separate `project-assembly-recovery-verification.json` on qualified cce sources
PASSED28/28 (26258 terminal0,404.576s), using the already accepted render without
a new owner claim. All719 RGB frames match source MP4s; exact WAV duration/PCM,
new numbered export, subtitle-first output-copy interruption/retry and every
one of10,153 current payload hashes pass. Original failed evidence is unchanged.
Copied owner remains40; accepted root50f25640b7e04bdca338e8d8e616705a.
Post-export reverse recovery is now RUNNING84218 into another independent
legacy tree, with all branch authoring/graphs/catalogues and external ownership
being compared. Do not start a writer on its source while recovery runs.

NEWER implementation: actual ALT Loop End→Assemble testing exposed lost caller
ownership on generation delivery too. `RuntimeDelivery` now returns caller
proof only in the execution envelope, not the archived manifest; Manifest Load
also passes through the caller's existing proof without replacing explicit
denials/stale credentials. Eight delivery tests pass (12884). Explicit ALT
manifests now retain generated WAV sidecars, and generated-audio assembly
resolves the immutable original soundtrack for picture-only ALT checkpoints.
The direct ALT test passes with visibly different candidate pixels AND a
different candidate soundtrack (27213). Expanded assembly28/28 (86244,39.058s),
generation31/31 (69864,26.757s) and ALT9/9 (68588,11.401s) all pass. This includes
read-only Manifest Load→Assemble with the original caller proof, and explicit
denial staying denied. These changes require fresh broad and real-copy
qualification. Fresh82 qualification PASSED (3942 terminal0, unchanged frozen
5679d1e6b023421fa8943eb71f03aaf0). A final defensive projection change drops any
proof already embedded in a manifest before copying the actual caller's proof;
it cannot recover authority from an old file. Eight focused delivery tests pass
(63145). Fresh whole-source qualification is RUNNING39575 on frozen
430f9aa0db2e4cd49504c541b5bed72b. Cce real-copy evidence predates these edits.

#### Previous qualification and assembly work

Latest result: **81/81 entrypoints pass**, sources unchanged (52587 terminal0),
on frozen `ea6eec76ec5648bab6fd72e6085ce7bb`. This includes output-copy13,
assembly21 and the partial-generation WAV correction. A new real copied-chain
replay is running on that exact snapshot; no final result yet.

Subsequent integration work is NEWER than that receipt: the deprecated Upscale
Merge node now has its own export grant/hidden UNIQUE_ID, using the common
renderer without borrowing Assemble's grant. All24 assembly tests pass
(75327,32.284s), including Merge media/HQ metadata, exact and prepared retries,
missing/wrong grants and node-ID rejection. Legacy upscale-chain entrypoint
67461 also passes.

Four new actual PromptExecutor graph cases exposed lost ownership on the final
processing delivery: archived manifests correctly omit proof, but the outgoing
envelope also omitted it. `storage_processing_continuation.delivery` now checks
the caller's proof against its accepted Save witness and adds a COPY only to
the returned envelope. It never substitutes current-owner credentials or writes
proof into archived manifests. Missing, changed and conflicting nested proofs
are rejected. Processing Save tests pass10/10 (15187,12.568s), retry tests13/13
(33702,15.775s). The complete CPU executor now passes17/17 (77876,43.874s),
including real PNG→VIDEO decode→Save→lazy End→Assemble→ordinary output copy,
uncached execution, fresh later ranges and the deprecated Merge node. Stored
delivery snapshots remain proof-free. Failed graph reports identified the
ownership gap; two additional harness assertions were corrected to inspect
revision-addressed delivery records and count only assembly (not PNG) receipts.

The final Review Gate notification also used the old input resolver for newly
accepted video. It now receives the same verified physical output item as the
ordinary UI. Both chain and optional-copy previews are covered; all25 assembly
tests pass (66307,33.963s), and legacy Review preview tests pass (7531).
A new **82-entrypoint** frozen-source qualification of these later edits is
next. Neither the older81 snapshot nor the still-running copied replay qualifies
the newer graph/Merge/Review changes.

2026-09-11 continuation: the assembly-core frozen source `f4d28aaa...` passed
**80/80 entrypoints**, sources unchanged, outside the sandbox (40757 terminal0).
The new optional output-copy port has **13/13 focused tests** (82860,21.124s).
It publishes only after chain acceptance, uses exclusive output basename claims
and no-replace file publication, writes SRT before MP4, and retries the copy
without rerendering or changing the chain root. Unrelated video/SRT collisions
choose a numbered name; an ownership change mid-copy denies publication.
The return socket remains the chain MP4; the UI previews the copied MP4.
These are two publications, NOT a cross-filesystem atomic transaction. Claims
live under output/.h3_export_copies; recoverable copy plans/receipts live under
the assembly job. Failed staging remains private, not deleted automatically.
Changed/deleted completed copies fail closed and are not silently overwritten.

The added race test found/fixed a claim collision before the immutable writer's
initial read (ValueError rather than FileExistsError). Real I/O failures still
propagate. Path preflight budgets include the longer subtitle staging names.
Assembly fixture coverage increased to **20/20** (52351,25.759s): accepted
existing-video prelude/audio, corrupt prelude rejection, recovered external
Source Timeline audio with exact identity and an independent base soundtrack.

Real-copy assembly is NOT qualified yet. First attempt 9871 stopped at a missing
branch argument in the LAB harness; preserved project-assembly-summary.json.
Corrected attempt 37522 rendered the real 719 frames and recovered an injected
publication failure without rerendering. It then exposed the shared renderer's
omission of generated WAV sidecars for partial-generation manifests. MP4/SRT
were safely accepted; error evidence remains in project-assembly-qualified-summary.json.
The renderer now includes partial v3 manifests in the generated-WAV contract,
with a new test for both generated and no-audio MP4 mux choices. This latest
21-test assembly suite passes (63698,28.837s). This change and the copy port
need fresh **81-entrypoint** qualification followed by
a new full-copy replay. No previous report is rewritten as successful.

All other gates below remain open, including output-copy full-chain/CIFS/native
Windows testing, deletion/retention, additional asset/review writers, reverse
recovery after new work, browser/job host integration, GPU and live cutover.
Original chain, user backup and installed pack remain untouched; no commit/push.

#### Historical assembly-core milestone

2026-09-11 assembly milestone (copy-only): `storage_assembly.py` now ports the
ordinary Assemble node with exact host-issued IDs, pinned input reads, private
encoder attempts and atomic MP4/WAV/SRT/HQ-record acceptance. Seventeen actual
CPU-renderer integration tests pass (96912, 23.798s). They cover real ffmpeg and
PyAV output, base audio under ALT pictures, partial HQ provenance, chapter-only
starts, subtitle catalogue reads, reorder/gaps/color filtering, private
scheduled-blend VAE recovery, numbered exports, immutable physical history under
logical overwrite, exact/old/prepared/encoder retries, midnight naming, forged
metadata/destinations, owner takeover, stale publishers and altered bytes.
Read tracing adds exact accepted dependencies without a global resolver bypass;
all 14 pinned-reader tests pass. The legacy upscale/assembly entrypoint and 12
processing execution tests also pass. A new 80-entrypoint frozen-source run is
required for this whole-code change; prior 79/79 results do not qualify assembly.

At that earlier milestone assembly remained incomplete: external `copy_to_output` was explicitly
fenced until its own recoverable no-overwrite publication is implemented.
Full copied-chain assembly, reverse recovery/reader/delete integration, prelude
and recovered Source Timeline coverage, native-model/GPU/production UI execution
and additional concurrent/fault cases remain open. Do not treat these fixture
tests as all-feature or live migration qualification.

The previously pending runs are both TERMINAL: 50361 passed 79/79 entrypoints,
sources unchanged, on `4e27aa481c064323a5f79fd390583bf7`; 33461 passed all 27
copied checkpoint PNG/WAV checks on `553ef93c858a4ac49a399b4756a63819`, verifying
10,145 payloads and 719 PNGs in 810.869s. It uses diagnostic CPU decoders, not
real-model quality tests. Copied ownership epoch is 37. The new accepted root
is `project/roots/9d1e24c45a914af3a08351283921f48b.json`. Those runs are not live
locks or blockers for a subsequent isolated replay.

- Full copied-chain VIDEO PNG/processing replay is complete: 30/30 checks,
  all 9,425 current payload hashes verified, 719 delivered PNGs across scenes
  5–7, prior controls and source settings retained. Evidence:
  `project-png-sequence-summary.json`, source snapshot `a74afbb118da4576a4beca6b06e62a11`.
- Snapshot `553ef93c858a4ac49a399b4756a63819` passed 79/79 entrypoints with
  unchanged sources in `project-latent-export-qualified-regressions`.
  The first sandboxed run is retained as failed evidence: eight async routes
  timed out. A minimal `asyncio.to_thread` probe reproduced the sandbox hang;
  outside the sandbox it and all eight unchanged entrypoints pass. Future
  harness runs probe worker wake-up before starting the full suite.
- Checkpoint PNG/WAV fixture coverage is now 20/20 (39.271 seconds). Added
  ordinary legacy-exporter indexes imported as mutable or immutable controls:
  verified prefixes fork independently without decoding old PNGs; an unchanged
  complete chapter also reuses its WAV without either VAE. Old indexes/bytes
  remain unchanged. This is consumer-contract coverage, not a replacement for
  the full migration/recovery qualification.
- Actual recursive VIDEO PNG graph tests found a missed ownership lookup:
  the normal adapter keeps proof in `source_manifest`, whereas the PNG port
  previously looked only at the outer state. The port now matches Segment
  Save's fallback, preserving explicit denials and conflicting-proof checks.
  All 13 executor tests pass (28.547 seconds), including PNG → real VIDEO
  decode → Save → Handoff recursion, no-cache retries and fresh-range starts.
  This correction is newer than the 79/79 frozen snapshot; requalification is
  required. The extended sequence suite adds direct fail-closed proof tests.
- Full-copy checkpoint PNG/WAV replay is currently running on the qualified
  `553ef...` snapshot, CPU diagnostic decoders only; no full-copy result yet.
  Its independent report is `project-latent-export-summary.json`. No second
  writer may use that copied project until the exact process finishes.

Final assembly/sidecars/subtitles, combined deletion and retention, remaining
asset/review writers, full-copy reverse recovery after new work, production
host/browser registration and real GPU/queue qualification remain open.
No live migration activation, original/backup writes, commit or push occurred.

### Earlier implementation checkpoints

Checkpoint PNG/WAV port is now implemented behind the same explicit export
grant/exact node IDs, in `storage_latent_export.py`. It calls the original
checkpoint renderer with explicit pinned reads and a private encoder workspace;
no VIDEO substitute or resolver-wide write bypass. Eighteen CPU integration
tests pass (18184, 63.174s): video-only/audio-only/both, real PNG16/WAV bytes,
ALT picture/base sampled audio, DeRoPE video/joint streams, exact scene metadata,
archive embedding, an actually saved 22-frame checkpoint trimmed at a shared
9-frame video/audio boundary, chapter append/reuse/variants, old WAV retention,
prepared/decode interruptions, ownership/stale writers, and reverse recovery
followed by ordinary legacy-node reuse without either VAE running. The legacy
PNG/WAV entrypoint also passes (17541); 12 layout tests pass after adding the
explicit optional `exports/audio/<export>/<revision>.wav` path contract.
Reverse recovery exposed checkpoint mtimes being treated as content identity;
the shared prefix comparison now ignores ONLY that timestamp hint and retains
all settings, source fields, sizes and SHA-256 checks. Shared picture/audio
resolution also preserves original audio for explicit ALT manifests in legacy
mode. A fresh 79-entrypoint qualification is next. These are fixture/CPU results,
not real-model or full-chain qualification of this new port.

Remaining checkpoint-export gates: full copied-chain replay on the qualified
source, migrated legacy-index append/fork cases, reader/deletion integration,
long-run progress/cache retention, third-party physical-index expectations,
and production host/browser/GPU execution. Other completion rows remain open.
Private encode attempts use bounded counters under their full operation ID to
fit the real copy's 240-character budget. Soundtracks have new immutable bytes
behind a versioned logical audio.wav; older snapshots/exports keep their WAV.

The preceding VIDEO PNG broad qualification is terminal: 1373, 78/78,
`sources_unchanged=true`, source receipt 4d10da32327c454ba50717e679f2f756.
Full-copy VIDEO replay 6020 remains live on earlier a74... sources: 26 checks
passed through scene 7/final delivery and old-scene retry. It is still doing
the prior-descriptor comparison; its final whole-copy hash result is NOT yet
available. The LAB script's future comparison now caches the after-state once
instead of repeatedly deep-copying it inside the loop. The running process and
its report were not restarted or rewritten. Never start a second writer on
that copied project until its exact handle is terminal.

VIDEO PNG follow-up: all 20 focused tests pass (19682, 69.844s, exit 0).
New coverage includes real reverse recovery followed by normal legacy-node
reuse/cleanup discovery; imported mutable indexes retain their original
scope/category, while immutable indexes fork without rewriting the archive;
per-frame staged checksums/interruption and final old-prefix verification
reject files edited during publication. The initial 78-entrypoint broad run
passed with unchanged frozen sources (11246; source copy
`a74afbb118da4576a4beca6b06e62a11`). That snapshot predates these last follow-up
tests and publication/mutable-index corrections. A current-source broad rerun
is next; the full real-copy node loop 6020 is still running on the earlier
snapshot, through PNG scene 6, with interruption/prepared resume already passed.
Do not mistake either earlier snapshot for qualification of later changes.

Current VIDEO PNG node port (copy-only, not production activation): explicit
export writer grants and per-node operation IDs now route the normal
`MiniMaxH3ChainExportPNG` VIDEO/state mode through `storage_png_sequence.py`.
Sixteen CPU integration tests cover same-directory append with unchanged prefix
inodes, RGB16, later range starts, exact/later retries, `_2`/`_3` variants,
independent prefix copies, prepared interruptions, ownership takeover, forged
prepared records, stale-root conflicts, legacy-shaped variant/session controls,
and actual Segment Save/Handoff owner/pin propagation (8964, 28.819s, exit 0).
The accepted logical `export.json` uses immutable versioned control storage;
status gives its exact physical index path. No unsafe CIFS fixed-path JSON
replacement is introduced. Imported immutable `png_exports.json` is preserved;
the new mutable ownership catalogue is logically `png_sequences/catalog.json`,
and cleanup reads both. Full copied-chain loop/broad qualification is pending.
Migrated sequence reuse, third-party index readers, reverse recovery, external
output folders, latent/WAV modes and production host wiring still need direct
qualification/implementation; unsupported modes remain fenced.

The previous "qualified" finished-PNG replay stopped on an over-strict test:
only the pre-existing export catalogue gained the expected second export. A
separate, preserved verification report proves every older catalogue entry and
all other prior descriptors unchanged, exact retry without encoding/rollback,
historical controls intact and all 8,694 accepted payload hashes correct.
`project-finished-png-catalogue-verification.json`: 10/10 checks, 97.422 seconds,
exit 0 (16442). Its code receipt is frozen source copy
`2068b9d2519245fcb075ed9be3cd4777`; it does not qualify the later sequence port.
The failed original report is retained, not edited into a success. The newest
finished-PNG broad run also passed 76/76 with unchanged sources (62131).
Private source snapshots now allow long replays to qualify captured code while
development continues; each report must identify its receipt and verify it at
completion, never claim an older snapshot validates newer edits.

Finished-PNG publication work: `storage_exports.py` provides an explicitly
authorized **service**, not a registered node path. It publishes independently
staged numbered RGB8/RGB16 frames, a readable `export.json`, immutable metadata
and a branch export catalogue together. Exact input VIDEO/source/archive
identity, prepared retries, lost acknowledgements, owner takeover and corrupt
payloads are tested. Twelve focused tests pass (83656, 14.969s); the shared
source validator still passes nine processing-save tests, and the extracted
bounded encoder passes all 36 legacy streaming/variant/recovery tests.
The 76-entrypoint `project-finished-png-regressions` finished successfully,
sources unchanged (95710, exit 0). The real-copy
`project-finished-png-summary.json` finished 17/17 checks, all 7,974 payloads
verified, 634.280 seconds (21786, exit 0). Its 719 delivered RGB16 frames retain
exact scenes 5–7 settings/archives and 39-frame overlap trims for scenes 6–7.

Post-qualification correction: the human-readable index is now staged LAST,
after every frame, before root acceptance. `ProjectStore.stage_payloads` shares
authority/history reads within locked batches of 32; each file retains its own
reservation, independent copy, checksum and fsync. The ready gate is rechecked
between batches and the final commit still validates all dependencies/epoch.
Fourteen PNG tests pass (86864, 20.667s); 23 payload tests pass (90378, 1.371s),
including interrupted batch retries, duplicate/escaped paths, corruption,
bounded authority-check counts and maintenance fencing between chunks.
Fresh `project-finished-png-qualified-regressions` (62131) and
`project-finished-png-qualified-summary.json` (47373) runs are pending. Keep
Python/JS sources frozen during these runs. Incremental
VIDEO append/reuse/numbered variants, latent/WAV export, node-host registration,
deletion and all other open feature gates remain unported. A finished export
must not silently substitute for those existing incremental workflows.

Actual CPU PromptExecutor graph follow-up: lazy End now has a dedicated host
adapter; resolved Handoff retains receipt/state/tensor verification. Host job
namespaces make repeated cacheless save evaluations idempotent per exact
dynamic node. Ten focused tests pass. `project-processing-graph-regressions`
finished 75/75 with unchanged Python/JS sources (48887, exit 0). The real-copy
replay 12997 finished exit 0: 24/24 checks, actual fresh-range 5–7 recursion,
failure/new-executor resume, exact settings/audio/API and all 7,254 payload
hashes verified in 942.008 seconds. The source inventory matched at completion.
This qualification predates the new finished-PNG publisher above; production
host registration is still open.

Processing retry follow-up is implemented and qualified on CPU. Host-issued
`processing_operations` are distinct from write grants and cannot reuse an ID
across generation/processing. Exact input requests, prepared encoder records
and accepted witnesses recover the original save without another encode or
pointer rollback. Focused results: 13 retry integration tests pass (34043,
16.140s) and 12 execution-provenance tests pass. Non-finite workflow extension
widgets now round-trip as opaque JSON strings in strict control metadata, and
HQ continuation witnesses no longer clone the complete latent. The saver also
rejects changed continuation inputs after encoding. The broad 74-entrypoint
run passed with unchanged sources; the finished copied-chain retry replay
passed 19/19 checks in 387.782 seconds, verifying all 7,234 payloads. Reports:
`project-processing-retry-regressions/qualification.json` and
`w-mBYCdo/project-processing-retry-summary.json` in the private lab. Production operation-ID
propagation and actual lazy/recursive graph qualification remain separate.

Latest qualified milestone: the combined processing publisher and accepted
save-to-continuation adapter are implemented behind explicit copy-only grants.
Nine focused real-encoder tests pass, including two-scene handoff/delivery,
fresh ranges, ALT/base audio, DeRoPE joint audio, interruption and ownership
takeover. `project-processing-saves-regressions/qualification.json` is terminal:
72/72 CPU entrypoints passed with the full source inventory unchanged (23576,
exit 0). `project-processing-saves-qualified-summary.json` passed 21/21 checks
in 408.279 seconds (90258, exit 0): real copied scenes 5–7 saved and continued
using synthetic 32×32 CPU pixels on their exact RAW clocks. All 7,226 accepted
payloads hash-verified; prior descriptors and tested source hashes were unchanged.
The first real-copy trial saved scene 5 correctly, then its simulated restart
used the old pre-save source pin and correctly could not see the new profile.
The replay now explicitly selects the accepted save pin; that trial and its
media/report remain retained. This was a replay pin-selection correction, not
relaxation of the runtime's historical-read isolation. The rerun was first
stopped before writes to fix an inefficient test assertion; the successful
replay performs the same complete descriptor comparison on one cached snapshot.
All processes from this milestone are terminal. No production activation occurred.
Exact processing save retries, full recursive graph/review/browser/server/GPU
coverage and export/deletion integration remain open. The older milestones
below retain their original evidence; their earlier unported-saver statements
are superseded by this implementation, not by a production activation.

**Resumed by user after reboot (2026-09-11).** The table below records the last
qualified milestones. Subsequent opt-in node save-retry changes pass 12/12
focused CPU tests and 70/70 broader CPU entrypoints with unchanged source hashes.
Their real-copy replay passed 27/27 checks in 431.682 seconds, verifying all
7,210 accepted payloads and its nine captured source hashes. The exact accepted
save is recovered after interruption/lost reply without re-encoding or rolling
back a later ALT choice. Production node-operation ID propagation is still open.
The CPU environment has been rebuilt in the persistent private lab, with exact
dependency versions saved; code, evidence and copied data remain present.
The lab's `PAUSED_FOR_REBOOT.md` retains the checkpoint and exact next steps.

Current processing work: explicit read ports now cover Upscale Adapter/Current,
saved-prefix resume, fresh ranges, pixel input decode and selected DeRoPE
sources. Twelve new real-encoder/migration fixture tests pass. A reproduced
direct ALT Loop End source issue is corrected: its picture stays the explicit
ALT, while original audio comes from the immutable base, not the current branch
pointer. The 16-test legacy ALT suite passes. The 71-entrypoint broad run passed
with unchanged source hashes; the real-copy source replay passed 21/21 checks
in 218.898 seconds with all 7,210 payloads verified and no control changes.
After those two runs, an explicit unported continuation guard was added and
12/12 reader/guard fixture tests passed in 8.777 seconds. The broad/full-copy
reports therefore predate only that additional guard and its test. Processing
save/continuation/export transactions remain unported. No test is running from
this milestone; proceed to the actual processing publisher, then requalify.

| Area | Current evidence | Completion requirement |
| --- | --- | --- |
| Inventory / layout | Read-only audit, short-path layout and legacy adapters | Full consumer inventory, collision/unknown/external-path policy |
| Media relocation | 7,182 independently copied payloads and 460 exact controls joined in one root; full hashes/inodes verified | Complete runtime writer integration |
| Atomic controls | 24 engine tests; 18 combined payload tests; 14 replacement-mode join tests; 37 immutable-log tests; 58 journal/migration/recovery tests | Complete per-feature ownership/mutability contracts |
| Branch authoring | 20 adapter tests, 31 real-copy checks; 14 runtime tests/18 real-copy checks; 5 actual API-handler tests and 25 further full-copy checks cover protected POST saves/defaults/empty creation/verified seven-scene forks; 22 writer-carrier tests, 16 full-copy writer checks and 10 full-copy retry checks cover exact accepted results, lost acknowledgements and commit-time ownership | Stable operation-ID propagation from workflow callers; browser/job pin propagation and full HTTP/browser qualification |
| Checkpoint graph / assignment | Normal constructors match both real branches: 21 original revisions, 0 broken; processing catalogues match 20/3 entries. Pointer retirement: 13 tests / 13 full-copy checks. Restore API: 14 tests / 17 full-copy checks, seven→five→same seven. Listing/ALT: 10 tests; attribution: 12 tests; 25 full-copy checks verify both cuts, one new shared-file alias, exact prompt/seed/media and retries; live CIFS qualification | End-to-end HTTP/browser pin registration; stable restore retry operation IDs; narrow restore dependency-watch scopes before concurrent processing qualification; remaining deletion handlers |
| Generation / ALT | Combined scene publisher: 14 tests. Actual CPU saver, ALT save, references, resume/context, recursive continuation and final/partial delivery: 31 integration tests; nine additional ALT acceptance tests and seven standalone delivery tests. Copied save/resume/recursive replays: 18/18, 25/25 and 18/18. Final copied ordinary delivery: 19/19, 7,198 payloads verified. Final copied ALT save → atomic cut/manifest acceptance: 20/20, all 7,206 payloads verified; prompts/seeds/canvas/branch retained and prior controls unchanged. Pre-existing main archived-Plan mismatch remains a user-accepted baseline | Stable node save IDs/retries; interactive/deferred Review Gate and candidates; runtime source/tensor correlation, direct ALT delivery to assembly/upscale, full recursive/requeue and GPU workflow execution |
| Processing | Bridge stage coverage; combined Save/Handoff/Adapter/Loop End: nine real-encoder fixture tests and 21 real-copy checks on scenes 5–7; source settings/audio, range/resume, atomic media+metadata+delivery and unchanged prior controls; 7,226 payloads verified | Stable processing operation IDs/prepared retries; full recursive graph/review qualification; opaque workflow extension values; large-latent witness memory; real upscale execution and production host integration |
| PNG / final exports | Bridge reuse/fork/append/assembly tests | Combined-store indexes/ownership and real pipeline export checks |
| References / assets / history | Bridge regressions; combined v1/v2/v3 cache adoption/readback in actual saver; accepted asset-catalogue recovery of source audio/video with exact content fingerprints, historical pins and corruption rejection | Complete asset/history writers and reference conversion/deletion; source archival/materialization and external workflow links |
| UI / queues / ownership | 10 external ownership tests; actual ownership/branch handlers; 17 read-carrier/host-adapter tests and 16 earlier full-copy carrier checks; exact writer pins, nested grants and current-owner commit guards; handoff transactions (15 tests), runtime (14 tests/15 full-copy checks), handlers + Loop End helper (18 tests; full-copy handler replay plus 14 helper checks) and both frontend requeue suites pass. Actual Loop End now accepts partial manifest + handoff in one commit; interruption/lost ack/claimed retry and read-only/owned Manifest Load pass on CPU and the full copy | Production ownership cutover/post-deletion lifecycle and bounded heartbeat retention; other writer receipts and browser/job host registration; real queue delivery/restart tests |
| Deletion / retention | Bridge ownership/deletion tests | Shared reference graph, associated PNGs, tombstones, safe retained-state cleanup |
| Migration | Fresh immutable-journal join: 460 controls/7,182 payloads; interrupted at payload 100, resumed; 20 checks passed, both graphs/ALT/pass catalogues match, longest payload path 221 characters | Production preview/quiesce/cutover, resumable initial control import |
| Recovery after new work | Previous reverse copy: 466 controls/7,184 payloads, 13 checks. New ownership-preserving recovery: 485 controls/7,182 payloads, five external authority archives, 14,895 total files; 20 full-copy checks and 14 focused ownership tests pass after interruption/resume | Later feature-specific transactions and normal workflow operations must also be covered; production/post-deletion cutover remains separate |
| Platform / failure testing | Latest 69 CPU entrypoints passed with unchanged before/after source hashes, including 31 actual encoder/resume/loop/delivery tests, nine ALT acceptance tests, seven standalone delivery tests, 17 carrier/adapter tests and 13 graph/asset-reader tests. Both frontend requeue suites passed at the earlier milestone; backend/runtime/ownership recovery/carriers/retries/handoffs/checkpoint APIs pass 129 live ComfyUI CIFS checks (before generation port); full-copy reports/source hashes retained | Remaining runtime operations; new generation/delivery ports need live-server qualification; native Windows, power loss and GPU/browser integration remain unverified |

## Filesystem limitation and qualified alternative

The agent host's `/media/unraid/comfyui/output` uses CIFS. Isolated probe files
outside `h3_chains` showed both a missing destination after a failed JSON
replacement and a concurrent reader observing `FileNotFoundError` while the
writer's replacements returned success. No project or backup was used as a
probe. Linux no-overwrite publication fixes the share's rejected hard links;
it does **not** make replacement of `storage.json` atomic on this mount.

Do not enable migration on that mount, weaken missing-authority checks, or
restore an old root automatically to hide this failure. Confirm the filesystem
used by the actual ComfyUI server separately. No server restart, remount,
configuration change or GPU render was performed during these checks.

Follow-up from inside live ComfyUI confirms its output also uses CIFS
(kernel 6.18.38-Unraid). A validated CPU-only Scripted Node → Preview as Text
diagnostic ran after an empty-queue check. In its separate `.h3-storage-test-*`
folder, replacement failed with EACCES after 15 successful updates, leaving the
test JSON destination absent; no-overwrite rename succeeded. This is a confirmed
server-side limitation, not merely a different agent-host mount. No chain or
backup data was used or modified by the diagnostic.

Replacement-mode control import/commit, media staging/join and reverse-copy writes
are fenced before mutation on known Linux CIFS/SMB mounts. Legacy savers are not
disabled by that gate. Filesystem-type detection is conservative, not a substitute
for qualifying the actual server and mount configuration.

The opt-in [immutable commit log](STORAGE_COMMIT_LOG.md) now supplies a tested
no-overwrite control backend. It passed 39 checks inside live ComfyUI, including
process exits/retry, media+control acceptance, initial import, interrupted joins,
new work surviving retry, and reverse recovery after failed owner/proof writes.
Join gates/journals inherit the source control copy's explicit protocol; reverse
recovery journals require their own explicit opt-in. The previous replacement
backend remains unsafe there. Do not mistake this qualification for full normal
workflow/migration readiness or change the server mount implicitly.

## Release gates

The [operation-local runtime rehearsal](STORAGE_RUNTIME_REHEARSAL.md) connects
normal branch and checkpoint/catalogue service constructors to one accepted
root. This is explicit host-side test access, not production activation or
automatic UI/queued-node pin propagation. Unported writers still reject it.

All required feature rows need direct evidence, not merely import success.
Preserve baseline missing/stale records as explicit pre-existing findings.
Recheck original-copy hashes and accepted lineage/settings before and after
testing. Validate on a full independent chain copy before offering production
migration. Unavailable platforms/features remain explicitly unverified.
