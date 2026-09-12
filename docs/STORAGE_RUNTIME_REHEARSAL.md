# Operation-local runtime rehearsal

## Current verification

Normal node and HTTP registration is now connected to explicitly activated
organized projects. See [Migrate a chain](MIGRATE_STORAGE.md) for usage and the
[completion checklist](STORAGE_COMPLETION_CHECKLIST.md) for full-copy migration,
normal-runtime checks and platform limits. The historical expansion lists below
are not outstanding release requirements. No live activation, commit or push
occurred in the final integration pass.

## Historical checkpoint: explicit input repair and Carousel action

`storage_asset_repair.py` restores only catalogued input files from one verified
accepted backup, under the existing asset-write, exact input-root and ownership
grants. `ProjectAssetStore.inspect_input_repair` and the GET `input-repair` route
are read-only: no directories, lock files, markers or automatic catalog repair.
POST `repair-inputs` requires that exact inspection and a stable operation ID.

Missing files are materialized without overwriting occupied destinations.
Damaged media and malformed catalog bytes are independently copied, synced and
verified under `project/assets/recovery` before replacement. The accepted repair
receipt retains those payloads for normal recovery. Different readable catalogs
(including another project or an unfamiliar schema) are conflicts requiring
review; they are not automatically rolled back. Uncatalogued files are untouched.
The accepted catalog, reference slots, prompts, seeds and original payload
descriptors are never changed by repair. Original catalog encoding is retained.

The shared input pending marker fences other edits, backup refresh and legacy
auto-repair while interrupted. Original-pin/operation retries resume prepared
work and recover lost commit replies; newer input/project state cannot be replaced
by a late retry. Corrupt accepted sources or preserved damaged-byte copies stop
publication. The actual HTTP handler propagates ownership and IDs through its
worker thread. Ordinary migrated browsing remains read-only.

Focused13 cases, frozen75208e59ab1d4ed68e5a5ef9eb680ce3 **29/29 regression
scripts**, and **47/47 independent real-slice checks** passed. The latter includes
partial repair, missing/corrupt input, preserved damaged bytes through ordinary
recovery and refusal of different valid authoring. No accepted catalog/Plan/seed/
editorial or protected full-copy bytes changed.

The actual Carousel's Repair input control inspects only on request, asks for
confirmation and retains the original inspection/transport ID for failed exact
retries while mounted. Cancel/conflict/healthy responses don't POST. Late replies
and errors cannot update another selected project. JS executes the actual action
with isolated transport/dialogs. Combined final snapshotf501c283e3f6482abe8291daf7796a86
passed **30/30 regression scripts**, sources unchanged (COMPLETE49687);
all Python sources match the real replay. All handles are terminal.
Durable browser/job retry pins, pending repair cancellation/retention and
production routing remain outstanding. No migration activation or default write grant.

## Previous checkpoint: migrated Review frame capture

The actual capture HTTP/helper now dispatches to a source-pinned capture service
under explicit asset/input/ownership grants. Saved logical/physical output paths
and input project asset videos resolve verified accepted payloads. Loose managed
sources are byte-witnessed; another project needs an independent pin. No raw
legacy path or missing accepted video is silently adopted.

Exact frame requests and durable PNG receipts survive interruption and lost
responses. Accepted registration stores capture provenance and uses the existing
numbered tag family. Review dialog failed retries keep their operation ID while
mounted; publication rechecks owner/source after ffmpeg. This does not activate
production routing or provide durable browser-reload/job pins.

Frozen `3abc41e2d4ec43fbb4256b8154abcdde` passed28/28 scripts, including12
new capture cases,16 legacy cases and actual-dialog JS tests. Real-copy replay
59417 completed40/40 checks in120.858s after correcting a harness payload-count
assumption for the added real scene video. The failed report is retained; runtime
stayed unchanged.33 original asset media,14 branches and one real scene video
were exercised; five added assets survived recovery and originals stayed exact.
See the completion checklist for evidence and all remaining scope.

## Previous checkpoint: model asset publication and exact successor compilation

`storage_asset_models.py` binds the complete model operation to its original
runtime pin and source/model bytes before compute. A verified durable PNG/render
receipt supports post-render retries without another model forward. Ownership is
checked before publication, not just before long-running compute. The accepted
asset retains model-weight/patch witnesses and the normal crop/alpha lineage.

`ProjectAssetStore.register_model_image` registers the result and synchronizes
connected reference slots within one recoverable catalog edit. The real Carousel
uses `accepted_asset_read_access` to compile paths/references from only its own
acknowledged successor. That internal context grants no writes and suspends the
old reader; it does not relax general nested-runtime or mixed-pin checks.

Frozen `89618d4ac11f4dd6a9e6fa2f2f39cccf` passed all 25 related regression
scripts, including ten CPU model/Carousel integration cases. Real-asset replay
96923 completed **36/36 checks in120.438s**, after fixing the standalone harness's
missing local core import; the first failed report was retained. It covered 33
original media files and14 branches, added four test assets, preserved original
Plan/seed/editorial and verified ordinary recovery. See the completion checklist
for the full outstanding scope. No production routing/live activation.

## Previous checkpoint: separate preview-cache access

`runtime_access(..., asset_previews=True)` is a new host-only, explicit cache
grant, independent of asset/catalog writes and ownership. Default GET/read calls
can use verified caches but cannot generate/repair missing ones. Normal node-host
access does not grant it. Production needs explicit cache-rebuild authorization
or an external application cache, not implicit project writes on browsing.

Existing ProjectAssetStore poster, thumbnail and browser-video methods generate
inside private optional staging, then publish checked, versioned cache receipts.
They still read source bytes through the exact accepted asset reader. Cache
publication never changes an accepted root or input asset/catalog. No preview
becomes an authoritative source, recovery object or grant to alter a project.

Frozen `467f823a0cb6476a9b26b293be889a75`: 24/24 related scripts and 33/33 real
copy checks passed, sources unchanged. Includes ten actual image/video/HTTP,
corruption, concurrency and permission cases. Real copy rendered both previews
for 33 visual cards, retained original media/Plan/seed/editorial bytes and
completed ordinary recovery. Full evidence and remaining release gates are in
[the completion checklist](STORAGE_COMPLETION_CHECKLIST.md). Model operations,
captures, production routing and final full-workflow qualification remain open.

## Previous checkpoint: media imports, crop/resize and uploads

See [completion evidence](STORAGE_COMPLETION_CHECKLIST.md) for full scope and
remaining release gates. Frozen `f262af85a2264914bb43ad75e7e40ac7` passed all 23
related scripts and 29 copied-real-asset replay checks. Prior metadata snapshot
`2bd049354a7344cebe2d4c4047caf0b0` completed its full 117-script matrix separately.

The asset transaction now stages original imports, slot binding and derived
lineage/media; real Pillow crop/resize uses one accepted commit. Early request
identity and byte verification permit interrupted render/re-upload recovery
across changing temporary filenames without loosening generic payload identity.
Input files publish independently without overwrite, then catalog and backup
commit together through the pending-edit protocol. Old roots/media stay intact.

Actual JSON import/derive and multipart upload handlers now pass stable IDs and
header ownership. Browser upload retries hash bounded chunks even on LAN HTTP;
this is an in-memory retry fingerprint, not a trusted media checksum or durable
browser-reload recovery. Explicit host grants and original request pins are still
required. Production host activation is not implemented by this qualification.
GPU model operations, captures, previews, cross-project edits and asset deletion
are not claimed ported. No live project or installed pack was changed.

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

The previous 108-entrypoint portable-custody matrix is COMPLETE and passed;
the newer six reader suites also passed. The complete real-copy read replay
passed 63/63 checks across 14 branches, and all 27 history indexes / 110 prompt
revisions matched exactly. Those runs predate the writer work below.

All six history mutators now publish atomically with explicit grants,
ownership checks and retry identities. Imported immutable revision metadata
is versioned without changing executed prompt text or discarding old bytes.
Focused evidence: 16/16 writer/API/carrier tests (69931), 2/2 actual Current Shot
and recovery tests (20537), plus JavaScript history/HTTP operation-ID tests.
COMPLETE96889: 112/112 project-history-writer-regressions entrypoints passed,
all_passed=true and sources_unchanged=true, frozen source
c50b92bb4b3e4de4932f782808261eef. Real history replay is COMPLETE: 220 accepted
label/restore writes across all 27 indexes / 110 revisions, on the independent
metadata-only h-d0884a slice. 47253 had a final test-only JSON-whitespace
assertion failure after every per-scene value check passed. 85769 completed
13/13 checks of exact JSON values, retained original bytes and ordinary recovery
without repeating the writes. Qualified report:
w-mBYCdo/project-prompt-history-write-qualified-summary.json. The initial
failed report remains preserved. No full media/workflow replay is claimed.
All launched test handles are terminal. Only status docs differ from the frozen
code/test copy. Do not restart completed runs; next work is the remaining
asset/reference writer and production integration boundaries below.

Automatic control classification, production request/job pins, older imported
history receipts, asset/reference writers, remaining damaged/partial-retention
cases, full latest real-copy workflows and platform qualification remain open.
The live chain, backup, installed pack and Git refs are untouched.

## Historical checkpoint: portable quarantine and cross-workflow undo

The latest completion-checklist section supersedes historical running notes.
The 106-entrypoint matrix 66812 finished successfully against its older frozen
source. Newer focused evidence: 25/25 Review roundtrip, 52/52 recovery/ownership,
9/9 portable-custody, and the reproduced closed-native-receipt error fixed and
verified (88889, 1/1). New processing/PNG/chapter roundtrip coverage is included
in the upcoming 108-entrypoint project-portable-custody-regressions matrix.
Record that run's frozen receipt and terminal result; do not assume it passed.
Healthy portable undo is implemented; damaged-file restore, older imports,
automatic control classification, remaining writers and the full real-chain /
production-host / platform matrix still need work. Live chain, backup,
installed pack and Git refs remain unchanged. No production activation.

## Historical checkpoint: repeated import and recovered undo

See the latest section of [the completion checklist](STORAGE_COMPLETION_CHECKLIST.md)
for authoritative coverage and remaining requirements. The old 105-entrypoint
run 18175 is complete and passed. New roundtrip/undo coverage passed 15/15,
then 2/2 additional cases. RUNNING66812 is the new 106-entrypoint matrix,
project-recovered-roundtrip-regressions, frozen source
26b977c833d8408b985eb7683dab3920. Poll the same handle and terminal report.
Undo after an ordinary recovered quarantine is now supported after re-import,
including a further ordinary recovery and lost-reply/stale-preview cases.
Older organized quarantines still need their external custody/evidence
closure carried into re-import; partly moved ordinary quarantines remain open.
No live migration, commit, push, installed-pack or backup change. Historical
RUNNING18175/RUNNING60844 notes below have been superseded.

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

## Current qualification

Latest actual Review port:19/19 focused checks pass (49823,56.746s), including real asynchronous
ComfyUI execution (two scenes, prompt/seed retry, cache disabled). Save retry14
and Review inventory21 pass. Named-branch coverage passes too. Fresh85-entrypoint
qualification is RUNNING60607 on frozen47b879a6e17f46ed9e11940279cc82cc;
real-chain replay remains next. Approval/retry/stop receipts
bind exact input state and Save provenance; optional audio previews are indexed
under project/optional/previews. Candidate selection/pruning, deferred execution
and partial stop assembly remain unported. No live activation or git publication.
Earlier cd849... source passed84/84 and its read-only Review inventory parity
against all14 real copied/recovered branches passed; these receipts do not
qualify the later execution code. See STORAGE_COMPLETION_CHECKLIST.md.

Frozen cceebeac7f4c4296be39c6ce065d615c passed82/82 CPU entrypoints. The matching
real copied-chain assembly verification passed28/28 checks: every one of719
RGB frames matches its saved source; generated WAV timing/PCM and metadata
match; a new numbered output and subtitle-first copy recover without rerender
or root rollback. All10,153 accepted payload hashes were verified (26258,
404.576 seconds; project-assembly-recovery-verification.json).

The earlier failed metadata assertion is preserved separately. It expected the
Plan canvas instead of the saved960x544 clips; the renderer correctly changes
only those dimensions. JSON key order is not a settings change. A new regression
compares every manifest value, archived tag, segment and actual output geometry.

Later changes, not covered by that frozen copy, fix generation/ALT delivery and
Manifest Load losing the caller's ownership on their outgoing connection.
Only the caller's existing proof travels downstream; stored delivery manifests
remain proof-free. Explicit denial and stale proof are never upgraded. Direct
ALT assembly now retains a WAV even for silent MP4s and uses the immutable
original soundtrack, not candidate audio. Focused assembly28, generation31,
ALT9 and delivery8 tests pass. Fresh82-entrypoint qualification passed on
5679d1e6b023421fa8943eb71f03aaf0 (3942). One later defensive change discards any
manifest-embedded proof before projecting the actual caller; delivery8 passes
again. Whole-source run39575 PASSED82/82 on430f9aa0db2e4cd49504c541b5bed72b;
sources unchanged.

Complete post-export reverse recovery PASSED61/61 (84218 terminal0,1470.293s):
all14 branches,687 controls,10,153 payloads and27,645 files including evidence.
It preserves the exact owner fence, resumes an injected interruption before
ownership publication, and passes ordinary recovered branch/graph/processing
readers and retry. All source hashes/root unchanged. Its frozen cce receipt
predates the later generation/ALT proof edits.

New durable Review inventory port:21 tests pass, including actual reverse
recovery, named branches, uint64 seeds, immutable imported archives, decision
receipts, ownership/lost ack/corruption/epoch checks and actual reconnect
listing. Records live under project/jobs, not recreated legacy folders.
Missing decision bytes fail instead of reopening a gate. This is newer than430
qualification; async Review execution, selection/pruning, previews and
deferred/stop paths remain open. It is not all-feature Review qualification.

Optional ordinary output copies are a SECOND publication after chain assembly,
not a cross-filesystem transaction. They use bounded independent staging,
exclusive basename claims, no-replace moves and exact retry receipts. Claims,
partials, combined deletion/retention and native-filesystem cases remain open.
Interactive/deferred Review, remaining asset/history writers, production host
registration, GPU/browser testing and cutover remain open as tracked in
STORAGE_COMPLETION_CHECKLIST.md. No live/backup/installed-pack writes or commit/push.

## Historical milestones

Latest milestone: ordinary Assemble is ported to explicit copied-runtime export
grants in `storage_assembly.py`. It shares the legacy renderer, uses explicit
pinned read hooks for media/archives/lyrics/processing provenance, writes all
temporary blends and sidecars into its exclusive job attempt, and publishes
MP4/WAV/SRT/HQ metadata together. Seventeen focused CPU-renderer tests pass;
fourteen read-view tests include nested dependency tracing. External output
copies remain fenced, not silently ignored. Full-copy assembly, remaining
recovery/deletion/production integration and broader qualification are still
required. No installed-pack replacement or live activation has occurred.

The formerly pending PNG graph qualification is complete (79/79 on 4e27aa...),
as is the full copied checkpoint PNG/WAV replay (27/27, 10,145 payload hashes,
719 PNGs, qualified 553ef... source, diagnostic CPU VAE outputs). The historical
notes below are retained; consult STORAGE_COMPLETION_CHECKLIST.md for open gates.

Current qualification: the 79-entrypoint frozen-source run passes outside the
sandbox (which blocks asyncio worker wake-up). The subsequent real PNG executor
graph found and fixed the PNG port's outer-state-only ownership lookup: normal
adapter state inherits proof from `source_manifest`, as Segment Save does.
Explicit missing/conflicting proofs remain fenced. All 13 executor tests pass,
including cached/uncached recursive PNG → decode → Save → Handoff graphs and
fresh-range starts. Requalify the new lookup before production use; prior broad
results do not cover that later code edit.

The completed copied VIDEO replay passed 30 checks and verified 9,425 payloads.
Checkpoint PNG/WAV now has 20 fixture tests, including imported ordinary legacy
indexes and no-decode prefix/WAV reuse. Its full-copy replay is still running.
See the latest section of STORAGE_COMPLETION_CHECKLIST.md; historical run notes
below are retained and are not current running-process claims.

Checkpoint PNG/WAV mode is now explicitly ported in `storage_latent_export.py`.
It shares the existing VAE renderer, trims and packed-audio assembly, with
validated immutable source metadata and separate ALT-picture/base-audio views.
Private progress records, prepared retries and one combined publication replace
direct legacy output/index/cache writes. Chapter PNG append retains the same
physical directory and old frame inodes; each longer WAV gets immutable new
bytes and an accepted logical audio.wav version. Audio-only exports live in
`exports/audio/<export>/`. The accepted versioned index path is reported in
status; no CIFS replace-in-place index is added. Eighteen focused CPU tests and
the ordinary legacy exporter pass; full-copy and production qualification of
this port remain open. Consult STORAGE_COMPLETION_CHECKLIST.md for current gates.

Latest copy-only VIDEO PNG port: normal ExportPNG now accepts explicit export
writer grants plus stable host-issued node IDs. The sequence publisher keeps a
single short frame directory across append; changed content/settings chooses
numbered logical variants and independently stages verified prefixes only on a
fork. Exact source VIDEO, archive and processing ownership checks remain.
Its logical current index is an immutable-versioned control; the physical index
path is reported in status, not replaced in place on CIFS. Sixteen focused CPU
tests pass, including the actual Save/Handoff connection. Full-copy/broad tests
are pending. Latent/WAV export, external destinations, existing migrated exports,
deletion/recovery and production host registration are not yet fully qualified.

This is an integration boundary for **explicitly permitted test copies**, not
production migration activation. A valid storage marker or browser-supplied pin
does not enable it. The host must already hold `control_rehearsal_access` and
explicitly enter `storage_runtime.runtime_access(ProjectStore(...))`.

## What is connected

Inside one runtime operation, normal constructors now select the same pinned
root automatically:

- `WorkingBranches(output, run)` uses the branch control transaction port.
- `CheckpointGraphManager(output)` uses the pinned read view.
- `saved_checkpoint_variants(output, run, originals)` uses that same read view.
- `HandoffStore(output)` uses the pinned handoff transaction port.

The real working-branch GET handler can consequently list/load saved authoring
without caller-specific constructor replacements. Tests execute that handler
from its source AST; this is not evidence of a full browser or HTTP-server test.
The checkpoint-listing handler, final-cut contexts and ALT presentation reader
also use the pinned view. They are tested with actual handler bodies and an
isolated transport fixture, not production HTTP/browser host registration.

Reads in an operation remain at its original root even if it publishes a branch
edit. A new operation sees that edit. `branch_writes=True` is an explicit host
capability for copied-fixture testing, never a property accepted from browser
JSON. Existing branch revision checks and storage scoped-CAS remain enforced.
The actual branch POST handler now works inside that explicit binding with its
normal ownership checks. Save, default selection, empty creation and saved-prefix
forks are covered. Prefix forks verify accepted immutable metadata plus every
required video/latent/audio/prompt hash. Only these explicit read-only verifier
calls receive relocated paths; the general output resolver stays fenced.

Default selection builds its response inside the transaction, so the response
includes the staged selection instead of returning the request's old default.
An unrelated read in the same request still remains pinned to its earlier root.

## External ownership

`ownership_writes=True` is a separate host capability. The existing ownership
API dispatches to `storage_ownership.OwnershipLog` within a runtime binding.
Its write-once bootstrap and immutable commits live at
`h3_chains/.project_ownership/<run-hash>/`, outside the deletable project. An
existing legacy ownership record is imported without resetting its epoch or
overwriting its bytes; subsequent changes to that legacy authority are rejected.
Workflow owner IDs are hashed; labels are public metadata, not credentials.

Ownership uses the **current** external authority, never an older data-root pin.
Expiry does not transfer ownership. Force/release retain epoch fencing and
heartbeat semantics. The same reentrant project lock protects ownership changes
and branch commits. Inherited worker contexts cannot bypass it; cross-task
re-entry from a suspended guard is rejected instead of sharing a thread RLock.
Lost acknowledgement retries keep the accepted owner and epoch.

Deleting/moving the output folder cannot silently remove the external fence or
restore anonymous legacy access. The post-deletion lifecycle and production
cutover remain unimplemented. Independent recovery of an existing copied
project now preserves ownership explicitly, as described below. The current
log still needs a bounded heartbeat/history retention policy before production
use; repeated heartbeats must not create unbounded history.

### Ownership-preserving recovery

New reverse-copy plans use `h3_legacy_recovery_plan_v2`. They inventory the exact
external legacy record and immutable authority/history under both source locks.
The current accepted owner is restored to the recovered output's normal
`h3_chains/.project_ownership/<run>.json`; it is not guessed from an old root or
the imported legacy record. The original authority files are independently
archived under `.h3-storage-recovery/ownership/`, outside the recovered run.
Epochs, labels and current ownership stay intact, including an expired owner or
a protected, explicitly released project. Expiry never grants anonymous access.

The effective owner is published before the incomplete-copy gate is removed.
Missing/changed acknowledged authority, a takeover after inventory, edited
target ownership, archive collisions or symlinks block recovery; no fallback
publishes an unprotected project. A failed publication acknowledgement resumes
without replacing the effective record. Normal ownership-reader lock files are
allowed at only the exact expected address and with normal lock contents.
This includes unowned projects: their ordinary status reader still creates a
coordination lock, but no ownership record. That lock must not invalidate a
completed recovery retry; arbitrary lock names or contents are still rejected.

Older V1 plans remain usable only when no external authority would be omitted.
Older recovery code rejects the V2 plan instead of silently ignoring its new
ownership inventory. This independent V1 recovery does not make legacy JSON
replacement safe on CIFS; that backend limitation still applies to later legacy
mutations, separately from verified initial publication and ownership reads.

## Pins and lifetime

The versioned pin contains only project name, selected branch, storage epoch and
the checksum-bound root reference. It contains no absolute path or authored
settings. Only current/committed ancestral roots are accepted. An epoch change
fences old pins and running operations. Changing project, output or nested
runtime binding fails instead of falling through to legacy storage.

The binding is task-local and propagates through `asyncio.to_thread`. Service
instances cannot escape it. Closing an operation explicitly revokes inherited
task contexts too; resetting only the parent's ContextVar would not do that.
`storage_carriers.node_host(store)` provides explicit copied-node qualification,
read-only by default. The existing `scoped_node` wrapper stamps `_storage_pin`
into returned run/plan/state/manifest envelopes and restores it for independent
sync/async node calls, including worker threads. Serialized Plan/checkpoint
selection inputs retain the same pin. Ordinary utility/model values and cached
upstream inputs are not copied or changed. Main is explicit too, not an implicit
default after a named branch disappears.

All incoming pins must agree exactly. Different roots, projects or branches,
invalid fields, unpublished roots and old epochs reject before the node runs.
A workflow-supplied pin cannot open a store, specify an output directory, enable
writes or prolong a closed host. Pins outside authorized host access reject.
Branch-setting writers can now be granted by exact Python callable through
`node_host(store, branch_writers=(save_method,))`. Serialized node names or flags
cannot grant access. The normal branch transaction forwards its successful
receipt; `ControlStore.committed_snapshot` resolves its exact accepted ancestor,
not whichever root happens to be current after the writer returns. Returned
envelopes receive that root. Cached input envelopes remain at their input root.
Missing/currently invalid ownership proofs reject under the short commit guard;
force takeover fences queued writes without locking the entire node execution.

Nested scoped readers retain the operation's original read snapshot. Nested
writers require their own exact callable grant AND a write-enabled outer node;
neither a reader nor an ungranted helper borrows the caller's write permission.
An inner writer cannot replace the outer operation's ownership proof. Its
successful receipt propagates to the enclosing node's output. A nested helper
cannot switch to a different input root; use an independent node operation to
consume a newly saved root.

Lost acknowledgements raise without returning a success carrier. Retrying a
branch save with its original domain operation ID, exact settings and original
expected revision now locates that save's accepted storage receipt. It completes
any missing acknowledgement without publishing another save or following an
unrelated later commit. A later branch mutation (including checkpoint assignment
while the old save ID remains in the branch record), changed request, ownership
takeover or storage epoch change rejects the retry. The original saved data stay
available. A caller that does not preserve its operation ID cannot identify an
exact retry and must reload rather than silently replay a stale edit.

### Handoff runtime and node grants

`handoff_writes=True` is separate from branch and ownership capability in the
host runtime. Node hosts grant it through exact Python callables in
`handoff_writers=(...)`. A branch grant cannot create handoffs, a handoff grant
cannot edit authoring, and neither grants permission to submit ComfyUI prompts.
Node handoff writes validate current ownership at publication and must belong
to the runtime's selected branch. Escaped services and inherited closed contexts
reject. Nested helpers without a grant, including utilities without a run input,
cannot borrow a caller's permissions or pass them on to a deeper writer.

Handoff writes within an operation watch its most recently acknowledged root.
For example, a node can save a branch and then create a handoff that watches the
new branch revision. Ordinary reads still retain the original input snapshot.
Successful intermediate result pins from this same operation can be carried
into its final output; arbitrary ancestor or concurrent-writer roots cannot.
Independent create/claim/transition nodes return their exact saved state. Old
queued claims cannot win twice; changed source branches/epochs reject claims.
A lost claim acknowledgement does not automatically release or submit a job.

The actual handoff list/claim/transition/release handlers now use these normal
services inside the explicit binding. Manual-resume hints read the accepted
Plan of each record's branch, even when listing from a different branch; they
never reopen a leftover legacy Plan. A genuine absent Plan retains the manual
fallback, but broken accepted bytes fail the request. Scoped storage conflicts
return HTTP 409, current-owner failures 423, missing records 404, and malformed
requests 400. Storage I/O failures return 503 with `retry_automatically: false`:
publication may already have succeeded, so callers must reload the state before
deciding what to do. This is handler integration, not HTTP host registration.

Loop End's actual `_write_next_scene_handoff` helper also uses its runtime's
acknowledged checkpoint metadata. It verifies the source scene/revision, hashes
the exact accepted bytes and preserves the next scene's integer seed. Missing
or mismatched checkpoints reject instead of creating an unbound handoff. The
legacy path is unchanged. This helper qualification does not yet port Segment
Save, all of Loop End, or real frontend queue submission to combined storage.

Server/browser host registration, actual queue delivery/restart qualification,
generation/ALT and other writer kinds remain required. These capabilities do
not activate a production migration or demonstrate a full GPU workflow.

## Safety boundary

No global `open`, `Path`, ComfyUI output directory or installed node is patched.
Outside the binding, legacy behavior and rejection of unsupported combined
projects are unchanged. Generation, checkpoint assignment/deletion
and other unported writers remain fenced; never feed immutable read paths into
those writers. No legacy control-directory mirror is created.

## Evidence

- 14 focused tests cover normal service construction, actual branch GET handler,
  exact seed/prompt recovery, coherent reads across edits, explicit write
  capability, stale CAS, epoch fencing, invalid/unpublished pins, task/thread
  isolation and lifetime revocation.
- The 48-entrypoint CPU regression run passed before the final lifetime guard;
  all 14 focused tests passed after that guard.
- An independent, fully migrated copy of the actual chain passed 18 runtime
  checks: both branches' exact authoring, 21-revision graphs, 20/3 processing
  catalogues, a 960×544 draft edit, historical reads, stale-write rejection,
  retention of old control versions, and all 7,182 payload hashes unchanged.
- The latest 25-module bundle passed 46 CPU-only checks inside live ComfyUI on
  CIFS, including normal runtime services, exact branch saves, inherited-context
  revocation, worker-thread reads and stale-write rejection. Diagnostic data
  lived in a new `.h3-commit-log-test-*` folder, not a chain or backup.

Ownership/POST follow-up evidence:

- 10 external ownership tests, 5 actual API-handler tests and 11 combined
  recovery tests pass. The final 50-entrypoint CPU regression run passes.
- The full migrated chain passed 25 checks (269.710 seconds): seven-scene forks
  from both the 1344×768 and 960×544 branches preserve exact prompts/seeds/settings;
  an empty branch has no assigned clips; default responses, retry and takeover
  rejection work; source authoring and processing catalogues stay unchanged.
  All 7,182 payload hashes and every prior control/ownership version are retained.
  A separate 4-check full-copy test verifies ownership-dropping recovery is
  rejected without a destination or journal and leaves the accepted root intact.
- The final 27-module bundle passed 54 checks inside live ComfyUI/Python 3.13.14
  on CIFS (8.340 seconds backend). Includes ownership takeover, lost ack, inherited
  worker contention, historical-pin rejection and the recovery safety fence.
  No installed node reload, restart or GPU render occurred.

Private reports and source hashes are retained in the migration lab. No GPU
generation, browser integration, native Windows run or power-loss simulation is
claimed by these checks. See [the full checklist](STORAGE_COMPLETION_CHECKLIST.md)
for all remaining release gates.

### Ownership recovery and node-carrier follow-up

Ownership-preserving recovery supersedes the earlier rejection-only milestone:

- 14 focused ownership-recovery tests and 11 combined recovery tests pass,
  along with all 51 CPU regression entrypoints at that milestone.
- An independent full recovery passed 20 checks in 841.359 seconds: 485 current
  control files, 7,182 independently copied media files, five external ownership
  archives and 14,895 total recovered files. Both branches' exact authoring,
  graphs and processing catalogues match. Interruption before effective-owner
  publication resumes, anonymous writes reject, normal reader locks permit a
  completed retry, and every source media hash plus the source root is unchanged.
- The 28-module live ComfyUI bundle passed 60 checks on actual CIFS, including
  lost effective-owner publication acknowledgement and successful recovery.
  Backend time 10.623 seconds; workflow time 10.992 seconds.
- The subsequent read-carrier addition passes 15 focused tests and all 52 CPU
  regression entrypoints, including legacy branch/upscale/export behavior.
- Full-chain node-carrier replay passed 16 checks (final run 198.213 seconds):
  both current and historical branches retain exact prompts, seeds, settings,
  checkpoint graphs and processing catalogues across independent node calls.
  All 7,182 source media hashes and the accepted root stay unchanged.
- The final 29-module live bundle passed 70 checks (11.145 seconds backend /
  11.531 seconds workflow) on CIFS, including unowned-status recovery retry,
  connected-project routing, numeric pin-type validation, async worker nodes
  and closed-host revocation. The unowned lock correction also passes all 14
  ownership-recovery, 11 combined recovery and 58 journal tests. These node
  calls use the real wrapper/services within the isolated probe, not a browser
  or real multi-scene GPU queue/handoff test.
- The final unowned-status retry correction also passed five checks on the
  earlier full recovery (54.259 seconds): all 14,904 files reverified, source
  root unchanged, and no owner record invented by opening status.

### Exact writer results

- 12 focused branch-writer carrier tests cover exact successful receipts,
  independent downstream reads, historical retention, no false output after a
  lost acknowledgement, stale saves, ownership takeover and nested grant limits.
- All 53 CPU regression entrypoints pass. A subsequent early nested permission
  check correction also passes all 12 writer tests.
- The actual full copied chain passed 16 writer checks in 207.960 seconds.
  Main and the 960×544 branch save independently; downstream authoring matches
  the exact receipt, historical prompts/seeds/canvas remain readable, the first
  save's result does not follow the second save, and prior controls plus all
  7,182 media hashes remain exact. The test deliberately edits only copied
  authoring and advances that copy's external owner epoch to exercise takeover.
- The first 29-module live writer bundle passed 77 checks on ComfyUI/CIFS
  (12.459 seconds backend, 12.836 seconds workflow). The nested follow-up
  correctly denied a write but exposed permission/proof check ordering in its
  assertions; that failure is retained separately, not counted as a pass.
  No installed-node reload, restart or GPU render was needed.
- The corrected final live bundle passed all 80 assertions, including nested
  grant boundaries (13.067 seconds backend, 13.465 seconds workflow). The queue
  was checked before each validated diagnostic. A successful ComfyUI history
  entry alone is not treated as a passing report; the diagnostic assertions
  are checked separately.

### Branch retries and handoff follow-up

- Exact branch-save retry passed 19 focused writer tests at its first
  milestone; nested utility/grandchild and cross-branch limits bring the suite
  to 22. The full-copy retry continuation passed 10 checks (168.857 seconds),
  retained the original save without publishing it again and verified all
  7,182 media hashes. A prior test-fixture method-name error is retained as a
  failure, not relabeled as success. The corrected live bundle passed 84 checks
  (15.121 seconds backend / 15.500 seconds workflow).
- Normal handoff construction, separate grants, intermediate writer receipts,
  duplicate claims, ownership/source fences and closed-context rejection pass
  14 focused tests. All 54 CPU entrypoints passed at that milestone.
- The full copied chain passed 15 handoff-runtime checks (338.676 seconds):
  both branches completed independent pending/claimed/queued/consumed test
  transitions, preserved the exact uint64 seed and historical state, and
  rejected an old owner after takeover. Authoring, checkpoint graphs and
  processing catalogues remained unchanged. Every prior control version and
  all 7,182 media hashes were verified. No real job was queued by those records.
- The 33-module live runtime bundle passed all 89 checks inside ComfyUI on
  CIFS (17.396 seconds backend / 17.838 seconds workflow). No installed-node
  reload, server restart, production-chain write or GPU operation occurred.
- Actual handoff handlers pass 12 further tests, including per-record branch
  Plan counts, historical reads, worker-thread ownership takeover, malformed
  JSON/proofs and lost acknowledgements. The expanded 57-entrypoint CPU run
  passes, including original top-level requeue/ownership tests; both frontend
  requeue helper/coordinator suites pass too.
- Full-copy handler replay passed 13 handler checks, then its final verifier
  was deliberately interrupted because the test repeatedly deep-copied the
  inventory inside a loop. An eight-check read-only continuation completed in
  57.221 seconds without repeating any mutations: exactly ten handoff commits,
  two new records, prior control descriptors/bytes unchanged and all 7,182
  media hashes verified. Both reports are retained and checksum-linked.
- The final 35-module handler bundle passed 97 live ComfyUI/CIFS checks
  (21.024 seconds backend / 21.453 seconds workflow). A preceding fixture
  category error was rejected by validation and remains recorded as a failure.
- Loop End helper tests bring the focused handler/creator suite to 18; the
  final 57-entrypoint CPU run passes. Its full-copy replay passed 14 checks in
  254.570 seconds across both real branches, including exact checkpoint hashes,
  next-scene seeds, result pins, idempotent retry, claim/cancel, unchanged saved
  Plans and all 7,182 media hashes. The 35-module live bundle passed 101 checks
  (23.281 seconds backend / 23.751 seconds workflow). These are handoff tests,
  not GPU generation or production activation.

### Checkpoint restore API

`_restore_checkpoint_revisions` now detects only explicit runtime access. It
reads the pinned graph without legacy adoption, verifies immutable selected
metadata/media, and retains the existing chapter-scope, immediate predecessor,
multi-source dependency, compatibility/shared-prompt and ALT rejection checks.
The response is built before publication, preserving uint64 seeds as strings
and resolving preview URLs through the read-only accepted view. It carries the
exact accepted `storage_pin`; this is not browser host registration.

`_publish_runtime_checkpoint_restore` stages selected canonical assignments with
one authoring marker and retires only later assignments inside the requested
chapter range. Current ownership is guarded across the short publication.
The selected branch and input dependency scopes use storage CAS, not a second
graph read that could silently rebase. No old pointer bytes, immutable take,
media or other chapter/branch assignments are physically removed. Historical
readers retain the old assignments; fresh readers see the whole new set.

The dependency watch is currently conservative: it covers accepted take and
payload-index scopes. Narrow it to the actual validation read set before
claiming unrestricted concurrent processing/restore behavior. Stale state is
HTTP 409, ownership rejection 423, missing artifacts 404, and uncertain storage
errors 503 with `retry_automatically: false`. The API does not yet propagate a
durable domain restore operation ID: after a lost acknowledgement, inspect the
accepted state rather than blindly retrying from a new pin. V1 behavior remains
on its existing journal and is not made CIFS-safe by this change.

- Fourteen actual-handler tests pass, including ownership takeover after
  validation, malformed requests, exact prompt/seed/media response, read-only
  grants, another branch advancing, old-input conflict, ALT/lineage rejection,
  interrupted pre-publication and post-publication lost acknowledgement.
- All 61 CPU entrypoints pass, including legacy restore, mixed-policy activation,
  authoring recovery, original ALT and processing/export regressions. The final
  portable test-factory refactor was separately rerun: all 14 tests pass.
- The real copied chain passed 17 checks in 201.406 seconds. A new private
  seven-scene fork was restored to five and back to exactly the same seven;
  960×544 authoring and full-size seeds/prompts matched, current/historical
  readers were distinct, every prior branch control remained unchanged, and
  all 7,182 media hashes verified. The test fork is retained as evidence.
- The validated 37-module isolated live bundle passed all 115 assertions on
  ComfyUI/CIFS, 27.854 seconds backend / 28.296 seconds workflow. It tests the
  real handler bodies with a transport fixture and tiny opaque media, not an
  installed-route/browser or model execution. The core skill's queue check,
  workflow validation and WebSocket monitoring procedure was followed. No
  installed-node reload, restart, production-chain or backup write occurred.

### Checkpoint listing, ALT presentation and attribution

The actual `_saved_checkpoint_listing` / `_list_saved_checkpoints` handlers now
read checkpoint pointers, retained takes, reviews, audio sidecars, partial clips,
editorial documents and processing variants from the same accepted root. Their
response includes its input `storage_pin`. Preview URLs name only verified,
accepted physical files; scans do not adopt residual V1 files. Malformed or
hash-corrupt accepted controls fail the read instead of silently selecting the
original picture. Stale editorial choices remain explicit, nonmutating notices.

`final_cut_contexts` resolves each branch's assignments and cut through that
view. `_editorial_presentation_segments` loads the shared selected ALT and
checks its identity, duration and artifact hashes. It preserves picture-only
base provenance without modifying the generation lineage. This qualifies that
reader, not the entire upscale/export pipeline.

`CheckpointGraphManager.attribute` and `_attribute_checkpoint_revision` can
publish one immutable metadata alias under an explicit branch-write grant.
Existing adjacent-scene, intact-media, ALT and predecessor-independence checks
still apply. The alias shares original media/prompt/audio files and changes no
assignment or authored setting. Publication fences the selected branch and
source artifact scopes and rechecks current ownership for node-origin writes.
A concurrently published equivalent alias causes an old request to fail with
409; a fresh retry finds the accepted alias without another commit. Precommit
failures and lost acknowledgements return 503 with automatic retry disabled.

Recovery workflow snapshots are opaque, hash-verified bytes, not control
authority. The actual copied chain exposed `Infinity` in an old archive: an
unnecessary strict parse initially rejected attribution. The corrected path
verifies those archive bytes without parsing or rewriting them. Strict parsing
of storage authority and payload-index descriptors is unchanged. Both failed
replay reports are retained; neither changed the copied control root.

- Ten listing/ALT tests and twelve attribution tests pass. The final full CPU
  run passed **63/63 entrypoints**, including legacy activation, authoring,
  processing and exports. Native Windows CI steps are added, not yet qualified.
- The actual copied chain passed **25/25 checks in 191.018 seconds**: both
  branches' listing and selected ALT matched, one independent saved scene was
  attributed using shared files, all previous controls stayed unchanged, stale
  and fresh retries behaved correctly, and all **7,182 media hashes** verified.
- The corrected 41-module CPU-only live bundle passed **129/129 assertions**
  on ComfyUI's CIFS output, **36.454 seconds** backend / **36.967 seconds**
  workflow. It includes an opaque Infinity/NaN recovery archive and 17 tiny
  payloads. This remains a source-bundled handler/domain diagnostic, not an
  installed-node/browser/GPU workflow. No server restart was necessary.

Production host/pin registration, deletion and retention, remaining generation
and processing/export writers, and the previously documented restore retry and
dependency-watch limitations remain separate release gates.

### Actual generation saver and pinned resume

`generation_writes=True` and the node host's exact `generation_writers` callables
grant only the combined generation domain. Branch/handoff grants cannot enable
it, nested helpers cannot inherit it implicitly, and ownership is checked again
at publication. `MiniMaxH3ChainSegmentSave.save` retains its real video, WAV,
safetensors and prompt encoders. It writes into a private job directory, then
accepts media, immutable metadata/recovery archives and selected-branch pointers
together. An ALT save adds a take without replacing its base or selecting it in
the cut. Failed/uncertain jobs retain staging; no rollback deletes accepted files.

Reference cache v1/v2/v3 adoption participates in that same publication. Global
cache payloads are independently staged; already accepted run-local objects are
verified and reused. A declared cache cannot be silently dropped when adoption
fails. Reads use the pinned run-local descriptor and tensor objects, not a later
global conversion. Execution pins and ownership proofs are excluded from archived
authoring; exact prompts, uint64 seeds, AV streams and source audio are retained.

Actual Loop Start preflight and resume now resolve canonical metadata through
the same accepted view. Artifact hashes remain mandatory even when the existing
history-verification option is disabled. Named branches and historical pins do
not switch to a newer main-branch take. Non-linear visual and independent audio
context readers verify the accepted immutable checkpoint before loading it.
Public segment envelopes now retain an explicitly saved per-scene resolution;
this field was previously dropped when constructing resume/manifests.

Source Timeline recovery can resolve a saved file hash against the accepted
`project_assets/catalog.json` mirror. It verifies catalogue identity, logical
address, payload checksum and size; it does not search by basename, import files,
rewrite the input folder or modify the caller's descriptor. Exact video/audio
fingerprints and decoded contents survive replacing an old host's runtime path.
This port does not implement general asset mutations or source materialization.

Evidence at this milestone:

- Fourteen publisher tests cover atomic saves, lineage/dependency watches,
  grants, ownership takeover, conflicts, interruption and service-level retry.
- Fourteen actual CPU encoder/resume integration tests and thirteen graph/asset
  reader tests pass. They include precise prompts/seeds, ALT isolation, all three
  cache formats, historical/named branch resume, bad-file rejection, and source
  timeline video/audio recovery. No diffusion-model sampling is asserted.
- The broader regression run passed 67/67 entrypoints, including legacy Source
  Timeline and processing/export/deletion coverage. Expanded targeted tests above
  ran afterward; this is not a claim that all combined workflow consumers exist.
- Re-encoding the real copied 362-frame 960×544 scene passed 18/18 checks in
  146.031 seconds. A private fork received one new save; all earlier branch
  controls and all 7,182 old plus four new payload hashes verified. Saved AV
  tensors, prompt, seed, audio and the exact existing reference cache matched.
  Conditioning-cache lookup was supplied from the accepted original cache for
  this replay; it was not a conditioning or GPU-render qualification.
- Actual Loop Start/Current on the real copied chain passed 25/25 checks in
  257.843 seconds, without any data-root publication; all 7,186 payload hashes
  verified afterward. The private new-save fork and 960×544 branch resume
  strictly with exact saved lineage, prompt/seed/canvas and predecessor AV
  tensors. The main branch's archived scene-7 Plan already disagreed with its
  selected scene-6 seed/visual recipe before generation testing. Strict resume
  preserves that rejection; its explicit history-disabled path was tested and
  remains flagged. The replay does not silently repair or qualify that main
  archive as a strict matching Plan. Only private external ownership advanced.
  The user subsequently accepted that pre-existing mismatch as a non-blocking
  baseline; it is not a required migration repair.

### Accepted save-to-recursive-loop transition

The actual saver now publishes an immutable `jobs/<operation>/scene-save.json`
witness in the same commit as its take. It binds the exact input pin, the
serializable Plan/range/predecessor-state digest, ownership-proof digest and
immutable metadata hash. Its returned execution envelope carries the accepted
receipt; these execution fields are not added to public saved segment metadata.

The copy-only node host can grant an exact Python input adapter for Loop End:
`input_adapters={MiniMaxH3ChainLoopEnd.end: storage_continuation.loop_end_inputs}`.
It verifies the accepted receipt/root/witness, input state, exact immutable take
and media checksums before copying only the state's execution envelopes onto
that successor pin. A newer unrelated commit is never substituted. Generic
mixed-root rejection remains unchanged, JSON cannot grant adapters, and adapters
cannot rebind an already active runtime. Sync/async signatures and upstream
cached inputs are preserved.

Actual recursive GraphBuilder expansion binds the successor's exact prepared
Plan directly to its Loop Start. This avoids reconnecting an old external Plan
node while retaining the new initial state. In combined-storage recursion,
Loop Start requires exact Plan equality; the legacy base-Plan check is unchanged.
The recursive step does not itself publish a derived manifest or a top-level
queue handoff. Their separate terminal/requeue integration is described below.

- All **22 actual encoder/resume/recursive integration tests** pass, including
  forged receipt, altered prompt/seed/range/lineage, corrupted saved media,
  ownership takeover, later unrelated saves and unchanged input envelopes.
- **17 carrier tests** include exact-callable host adapters, positional-only and
  variadic sync/async signatures, invalid grants and nested-runtime rejection.
- The latest broader run passes **67/67 entrypoints**.
- Full copied-chain CPU replay passes **18/18 checks in 221.762 seconds**. It
  re-encodes the real 362-frame 960×544 scene in a new private fork, executes
  actual Loop End expansion and Loop Start/Current for scene 2, preserves all
  pre-existing control descriptors, exact prompts/seeds/canvas/AV context, and
  verifies all **7,190 accepted payload hashes**. Source-cache lookup is again
  supplied from the accepted original; no diffusion sampling is claimed.

Remaining execution gates at that milestone included actual node save/retry IDs,
review/ALT cut approval, delivery manifests, source-timeline/tensor correlation,
top-level queue delivery and production hosts. The following milestone ports the
manifest and handoff publication only; it does not establish GPU/browser/restart
qualification. Current Shot's best-effort prompt-history write still warns/rejects
combined storage. The earlier 129-check live CIFS diagnostic predates these
generation changes. The full completion checklist remains authoritative.

### Frozen final/partial delivery and atomic requeue publication

`RuntimeDelivery` verifies each contiguous saved scene against both its branch
assignment and immutable revision. It verifies accepted media, prompt sidecars,
recovery archives, exact frame totals and duration, then publishes an immutable
`deliveries/<branch>/<digest>.json` plus a branch-local `latest.json` pointer.
These are logical identities; organized control storage owns their physical
locations. Imported legacy manifest paths are never overwritten. An immutable
manifest stores the already accepted source pin; returned execution carriers
carry the exact new delivery receipt's pin, not the latest global root.

The actual Loop End terminal path uses this publisher. Manifest Load can rebuild
from pinned saved checkpoints in a read-only session; publishing its derived
manifest requires an explicit generation-writer host grant and current ownership.
Ordinary manifest publication does not mutate generation assignments or their
branch revision. Legacy prompt sidecars imported as text controls are verified
as such; a missing binary payload cannot use that compatibility exception.

Actual `top_level_requeue` Loop End now stages its partial manifest and pending
handoff in **one** accepted control commit. The handoff must identify the same
saved revision, scene boundary, branch, next seed and accepted dependencies. Both
generation and handoff writer grants are required. Deterministic publication
IDs and stable saved-scene timestamps allow exact acknowledgement retries without
creating another delivery or resetting a subsequently claimed handoff. An
interruption before acceptance exposes neither member of the batch.

This remains an explicit copy-only host port. It does not submit a new ComfyUI
prompt or install a production host. ALT acceptance is covered by the next
milestone; interactive/deferred review decisions remain separate.

- **31 actual CPU encoder/resume/loop integration tests** pass, including full
  and partial delivery, read-only/owned Manifest Load, separate writer grants,
  stale branch rejection, interruption, lost acknowledgement and claimed-handoff
  retry. **Six standalone delivery tests** also pass.
- The final code-pinned regression run passes **68/68 entrypoints**. Its source
  inventory is hashed before and after execution; no source changed during the
  run. Reports and per-entrypoint logs remain in the private migration lab.
- Final full-copy replay passes **19/19 checks in 342.150 seconds**, with all
  **7,198 accepted payloads** hash-verified. It re-encodes the real 362-frame
  960×544 scene in another private fork, injects a publication interruption,
  accepts and retries the real partial manifest/handoff, reads and publishes
  reconstructed manifests, and verifies that retry does not reset a claimed
  handoff. The test handoff is then cancelled without queue submission. Every
  pre-existing control descriptor stays unchanged. Exact code hashes match the
  final source inventory; the earlier 19-check replay is retained separately.

All replay processes from that milestone are terminal. No original chain, user
backup, installed node pack or server state was changed by these CPU tests.

### Atomic ALT final-cut acceptance

The actual Loop End ALT path now uses `RuntimeDelivery` instead of writing an
editorial sidecar and an alternate manifest separately. It validates the exact
picture-only Plan target, queued prompt/seed, unchanged base identity/duration/
canvas, immutable base assignment and accepted candidate. Base video, checkpoint
and original audio are verified alongside the alternate's media. A later base or
editorial change rejects the queued acceptance rather than rebasing it silently.

The legacy editorial normalizer is passed as an exact host-side Python callable;
JSON cannot supply executable normalizers. Selection replaces only that scene's
picture choice, consumes the draft, and preserves other normalized editorial
settings/selections. One accepted commit contains the branch's editorial change
and immutable ALT manifest/pointer. Logical ALT delivery identities are separate
from ordinary final/partial deliveries:
`deliveries/<branch>/alternates/scene_<number>/<digest>.json` and `latest.json`.
Their physical files stay in organized `project/cuts`; no nested legacy ALT
output folder is created. Generation pointers, authored Plan/recovery archives,
base audio and ordinary generation delivery pointers are not rewritten.

The manifest embeds the accepted editorial view as well as its exact replacement
and source pin. Returned carriers identify the accepted publication. Deterministic
IDs/timestamps make interruption and lost-acknowledgement retries safe; retrying
an older acceptance after another ALT has been selected does not reselect it.
Checkpoint listing and picture-presentation readers see the selected ALT, while
Manifest Load still exposes the original seven-scene generation/AV lineage.

A reproduced metadata-corruption case tightened both ordinary and ALT delivery:
each saved media checksum must agree with the accepted payload index, and the
physical file must independently verify. Hash-valid index/file bytes alone do
not prove that imported take metadata describes those bytes. Prompt text controls
retain their separate checksum-verified legacy import path.

- **Nine actual ALT acceptance tests** and **seven standalone delivery tests**
  pass, including the reproduced saved-checksum/index disagreement, corruption,
  ownership takeover, interruption, lost acknowledgement and older-acceptance
  retries after a newer cut selection.
- The final code-pinned broad run passes **69/69 entrypoints**, with unchanged
  before/after Python/JS source inventories.
- Final real-copy replay passes **20/20 checks in 521.650 seconds**, verifies all
  **7,206 accepted payload hashes**, and confirms its seven captured source hashes
  match the tested code. Actual CPU ALT Save re-encodes the real 362-frame 960×544
  source into a private fork; interrupted acceptance is retried, all seven base
  checkpoints remain unchanged, and all pre-test controls retain their exact
  descriptors. The earlier 20/20 replay (7,202 payloads, 302.867 seconds) remains
  retained; this final run includes the subsequent checksum-validation fix.

Both final replay and regression processes completed successfully. These tests
did not alter the live chain, backup, installed pack or server configuration, and
did not sample the diffusion model or submit a ComfyUI queue job.

This is not a port of interactive Review Gate: approve/retry/stop, candidate
batches, deferred review persistence, optional preview/partial assembly and
candidate pruning still need their own runtime transitions and tests. Stable
node-save IDs, source/tensor correlation, combined processing/exports/deletion,
later reverse recovery and live GPU/browser/queue qualification also remain.
The positive ALT audio checks above use the original generation manifest plus
its independent picture-presentation view. Direct ALT Loop End manifest inputs
to assembly/upscale must also be qualified for picture-only/base-audio semantics
when those writers are ported; this milestone does not claim that end-to-end path.

### Retryable actual scene saves (after reboot)

`storage_save_retry` now binds an explicitly host-issued node operation ID to
the exact input pin, ownership proof, Plan, scene state, prompt/workflow, images,
audio and sampled/denoised tensors. `storage_execution_digest` hashes typed
values and all tensor bytes in bounded CPU chunks; unsupported inputs fail
instead of falling back to object names. No default node or workflow gains IDs
or write authority merely by supplying JSON.

Before acceptance, checksum-wrapped prepared records retain the exact encoded
files and recovery/reference inputs. A retry can publish those files without
encoding again. After acceptance, the immutable receipt and scene witness
recover the original result pin after verifying saved metadata, media, recovery
archives and adopted references. Current ownership remains mandatory. An older
save retry does not overwrite a newer saved take or later ALT cut selection.

- 12/12 focused actual-encoder tests and 70/70 CPU regression entrypoints pass.
- A real copied-chain replay passes 27/27 checks in 431.682 seconds, verifies
  all 7,210 accepted payload hashes and nine captured source hashes, and leaves
  all pre-test control descriptors and seven base checkpoints unchanged.
- The replay saves a real 362-frame 960×544 ALT in a private fork, injects a
  pre-accept failure and lost reply, recovers without encoding, then checks
  atomic ALT acceptance and retry after that later cut selection.
- Both processes finished successfully. The persistent private CPU environment
  uses Python 3.11.16 / torch 2.10.0+cpu; its dependency freeze is retained.

This does not establish sampler provenance, Loop End tensor correlation,
production/browser operation-ID propagation, Review Gate, combined processing
or export/deletion support, GPU execution, or native Windows qualification.
No live chain, backup, installed pack, server settings or queue was modified.

### Combined upscale sources and migrated prefix reads

Upscale Adapter/Current now use explicit pinned reads for source manifests,
checkpoints, recovered DeRoPE sources and saved processing prefixes. Profile
paths remain logical identities; the port does not create old `upscaled` or
chapter directories. Pixel Current uses the same source selection and selective
audio loading. Fresh ranges keep global scene numbers without requiring unused
earlier HQ outputs. Existing profile/configuration/source and integrity checks
remain active; a named branch does not borrow Original's processed prefix.

Direct ALT Loop End manifests exposed a picture/audio bug: the segment list
already contains the ALT, so treating it as the base triggered stale-editorial
handling and could consume its audio. Deferred source resolution now looks up
that take's immutable original revision before applying the explicit candidate.
It keeps the input ALT even after another ALT is chosen, and retains original
audio. Neither the active generation pointer nor authoring is rewritten.

- 11 migrated-fixture tests and 16 legacy ALT/upscale tests initially passed.
- The source-pinned broad run passed 71/71 entrypoints. A read-only real-copy
  replay passed 21/21 checks in 218.898 seconds, verified all 7,210 payloads and
  seven captured source hashes, and retained the same accepted root/controls.
  Actual Adapter/Current started the 960×544 branch at scene 5 for scenes 5–7.
  Both full-generation and direct-ALT manifests matched the saved ALT video and
  original latent/delivered audio tensors, prompt and uint64 seed exactly.
- After those runs, an explicit fence was added to the unported `_advance`
  writer, before an empty/forged prefix could reach a legacy manifest write.
  All 12 reader/guard fixture tests pass in 8.777 seconds. The broad/full-copy
  reports predate this last guard/test; qualification is not overstated.

All processes from this milestone are terminal. No production data, ownership,
queue or installed code changed. These checks load saved tensors and CPU-encoded
fixtures; they do not run an upscaler model, VAE on the real chain, or GPU sampling.

Next is the actual processing publisher, not enabling the legacy writer behind
these read paths. It must stage organized stage/pass/take media, freeze the real
processing workflow/API prompt, preserve original/recovered audio and compact
HQ context, and accept media + immutable metadata + resume controls together.
It needs its own exact node grants, current-owner checks, input/source/prefix
dependencies and accepted continuation witness. Imported immutable partial
manifests must not be overwritten. Stable retry IDs and pin advancement need
explicit handling; `_source_hash` currently includes runtime carrier fields and
must not drift merely because a processing save advances the accepted root.
Processing cleanup/PNG linkage and actual recursive/export operations remain
separate unimplemented parts of the overall objective.

## Combined processing publisher and continuation (2026-09-11)

The explicit test-copy runtime now has a separate `processing_writes` grant.
`MiniMaxH3ChainUpscaleSegmentSave.save` uses a private encoder workspace and
accepts its media, checkpoint, prompt, optional audio, immutable metadata,
canonical resume pointer and revision-specific delivery manifest in one commit.
Existing partial-manifest controls remain untouched; new partial deliveries
have revision-specific addresses. Pass media uses the organized stage/pass/take
layout. Legacy operation remains on its existing persistence protocol.

Before encoding, selected source prompts/seeds/timing/artifact addresses are
checked against accepted immutable metadata, including the ALT/base audio split
and DeRoPE ancestry. Imported profile ownership scopes are retained. Publication
checks current workflow ownership again after encoding and staging.

`storage_processing_continuation.loop_inputs` is an explicit host input adapter
for Upscale Handoff and Loop End. It validates the accepted receipt, original
input pin, state digest, metadata and bit-exact image/latent input digests before
advancing the carrier pin. Advance validates the delivered prefix and retained
HQ context and reads the manifest already accepted by the saver; it does not
write another legacy manifest. General mixed-root execution remains forbidden.

Nine focused real-encoder fixture tests pass (10.859 seconds): atomic pixel
publication, fresh range, missing authority/forged settings, interrupted commit,
two-scene save/handoff/adapter/delivery, mismatched continuation inputs, changed
context/prefix, joint DeRoPE latent/audio followed by pixel processing, and an
ownership takeover during encoding. Full-copy and broad qualification results
are recorded separately when terminal; this focused result is not release proof.

Still required: stable host-issued processing operation IDs and exact retry
recovery (the current node allocates a new save operation), full recursive graph
and deferred review qualification, production host/browser registration and
ownership propagation, export/deletion integration, and real GPU/server tests.
The continuation digest witnesses supplied tensors, not sampler provenance.
Current witness construction clones the CPU continuation latent; a bounded,
non-cloning normalization should be qualified for large HQ jobs. Saved workflow
extension values such as non-finite UI sentinels also need explicit provenance
serialization coverage. None of this activates the live project.

Terminal qualification:

- `project-processing-saves-regressions/qualification.json`: 72/72 CPU
  entrypoints passed, full Python/JS source inventory unchanged (23576, exit 0).
- `w-mBYCdo/project-processing-saves-qualified-summary.json`: 21/21 checks,
  408.279 seconds, all 7,226 payloads hash-verified (90258, exit 0). The actual
  copied 960×544 branch's scenes 5–7 use synthetic 32×32 pixels for storage
  testing, not an upscaler model. Their exact prompts, uint64 seeds, RAW/delivered
  clocks and original audio survive saves, restart/resume from 6, Handoff,
  Adapter continuation and final Loop End. Old descriptors and the original
  input manifest remain unchanged; no legacy processing directory was created.
- First trial retained scene 5 but tried restart from the pre-save source pin;
  that correctly could not see a later profile. Its output/report remain. The
  corrected test explicitly pins restart to the accepted save. An intervening
  test-only interruption removed redundant whole-index copies in assertions,
  not any validation. Both failures/interruptions are documented, not hidden.

Accepted root: `project/roots/92bccacb0f854ff487980107a921fac7.json`,
SHA-256 `0388a8ba68326b55a4fd17ab25ac4dbe16c3bbc65feb11fd71461215675d1054`,
2,524,971 bytes. Copied ownership epoch 31. All test processes are terminal.
Next: durable processing save retries and the remaining qualification gaps
above, before exports/deletion and final production integration.

### Durable processing retries (follow-up)

`NodeHost.processing_operations` supplies exact `(callable, unique_id)` IDs
out of band, independently of processing writer grants. IDs must be unique
across both generation and processing domains. Missing/wrong node IDs fail
closed when a retryable call was explicitly registered; unregistered ordinary
calls still allocate a fresh save operation.

`ProcessingSaveRequest` freezes the input pin and hashes state, images, latent,
recovered audio and the actual archived processing API/UI documents. The
immutable request catches reused IDs with changed inputs. A checksum-protected
prepared record identifies exact private encoder files and the continuation
witness; interrupted publication reuses these files, never calls the encoder,
and still checks current ownership and accepted source/prefix dependencies.
Failure before preparation can allocate a new private encoder attempt under
the same request; old attempts remain retained. A committed receipt verifies
saved metadata, payloads, execution and delivery, completes a lost acknowledgement,
and returns the original accepted root even if a newer take was saved afterward.

Processing execution metadata keeps the existing `execution` representation
for finite documents. Non-finite UI extension values use `execution_json`
strings, retaining the original JSON values and uint64 seeds without relaxing
strict storage-control JSON. Execution hashes and standard media tags use the
decoded original documents. The execution reader rejects ambiguous dual forms.
The retry input digest hashes these opaque serialized documents explicitly.

HQ continuation hashing now normalizes latent streams without cloning the
full latent. Chunked hashing remains content-exact and bounded. The actual
saver rechecks continuation inputs after encoding; a changed supplied image,
latent, source state or ownership proof does not become an accepted take.
This proves consistency of supplied inputs, not their sampler provenance.

Focused qualification: 13 retry integration tests pass in 16.140 seconds,
and all 12 execution-metadata tests pass. The finished broad run passed all
74 entrypoints with unchanged sources. The finished copied-chain retry replay
passed 19/19 checks in 387.782s and verified all 7,234 accepted payloads.

### Actual lazy processing graph execution

The local core PromptExecutor reproduced a combined-store failure before any
scene ran: the direct continuation adapter required tensors at lazy Loop End.
The new host-only `loop_end_inputs` adapter discards End's unused lazy values
(missing or already cached) only for a declared Upscale Loop End. It does not
read a latest root or grant writes. Expanded Handoff still needs its separate
`loop_inputs` adapter and verifies the full accepted receipt/source/tensors.
Direct Python End calls continue to use the strict verification path.

Cache-disabled execution also reproduced repeated evaluation of a save node,
which attempted a second publication from the same old root. Optional host-side
`operation_namespace` now derives stable save IDs from the job namespace,
generation/processing domain, exact callable and dynamic execution node ID.
Writer grants remain separate. Explicit ID mappings cannot be mixed with a job
namespace, and a namespace must never span unrelated jobs. Repeated evaluations
use the existing exact-input retry logic; changed inputs are not silently reused.

Ten actual CPU executor/host tests pass (18737, exit 0, 17.128s): recursive
two-scene save/delivery, source API provenance/audio, NullCache, pre-scheduled
Save with cached lazy inputs, fresh ranges, failed later scene followed by a
new executor resume, changed handoff pixels, absent adapter grants, and exact
job-ID boundaries. The initial failures are preserved in turn evidence: missing
lazy data, an actual cacheless scope conflict, and later test-only assertions
that incorrectly required finite execution metadata/no producer reevaluation.

Broader 75-entrypoint run 48887 finished exit 0 with unchanged source inventory.
Copied-chain graph replay 12997 finished exit 0: 24/24 checks including
failure/resume and all 7,254 payload hashes, in 942.008 seconds; tested sources
matched at completion. Production host/browser
registration, durable job namespace propagation, review decisions, final
exports/deletion, new-work reverse recovery and live/GPU qualification remain
separate completion requirements.

## Finished VIDEO-to-PNG export publisher (copy-only service)

`storage_exports.FinishedPNGExport` now validates an explicitly selected saved
scene range using the same immutable source/ALT/base-audio/DeRoPE checks as
processing saves. It uses a separate direct host export capability; it does not
acquire processing permission and nested unported nodes cannot borrow it.

The caller's exact source pin, scene settings, input VIDEO hashes, archives,
export settings, display label and owner bind a durable request. The shared
bounded RGB8/RGB16 encoder stages numbered frames privately. Prepared index and
file hashes allow restart without decoding again. Publication accepts a short
`exports/png/<id>/` folder's frame payloads and readable immutable `export.json`
together with the export record and branch catalogue. No prefix is recopied by
this finished-snapshot service. Lost replies recover the exact accepted result
without replacing later exports; changed input/settings/payloads and ownership
takeover fail closed.

Twelve focused tests passed (83656, 14.969s), including real RGB16 pixels,
RAW overlap trimming, non-one range start, exact archived-workflow embedding,
failure/prepared resume, lost acknowledgement with later work preserved,
read-only/node fences, takeover, corruption and path confinement. All 36 legacy
streaming PNG tests and nine processing-save tests pass after extracting the
shared encoder and source validator. The broader 76-entrypoint run passed with
unchanged sources; the real-copy scene 5–7 replay passed 17/17 checks and all
7,974 payload hashes in 634.280 seconds (95710 and 21786, both exit 0).

Subsequent follow-up publishes the readable index after all frames. The new
ordered `stage_payloads` API shares authority/history checks only while a
bounded batch holds the existing project lock; it retains independent per-file
reservation intents, copies, hashes and fsyncs. It releases/revalidates between
batches (default 32, maximum 128), and never accepts media or changes final
commit dependency/epoch checks. Single-file staging uses the same implementation.
Fourteen focused PNG tests and 23 payload tests pass; a fresh broad run (62131)
and full-copy replay (47373) are in progress under `project-finished-png-qualified`
labels. Their results are not yet claimed.

This service is **not** an ExportPNG node registration or an incremental
sequence implementation. Keep the existing writer guard for those nodes until
VIDEO append/reuse/numbered variants and ownership/index transitions are ported.
Latent/WAV export, final video assembly, deletion, recovery of these new exports,
live/GPU execution and production host registration remain required separately.
