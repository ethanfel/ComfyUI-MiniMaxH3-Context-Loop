# Atomic control-state rehearsal

Status: copy-only transaction engine plus explicit branch-operation adapter
implemented; **not enabled in UI/routes, normal workflows or production
migration**. The [combined-store rehearsal](STORAGE_PROJECT_REHEARSAL.md) now
joins controls and media under one accepted root and supports independent V1
recovery after new work. No existing project changes format on import, startup,
update or branch selection.

## What this adds

`storage_state.py` versions exact control-document bytes below `project/` and
publishes one immutable state root. The original backend replaces a small
`storage.json` pointer. The opt-in [immutable commit log](STORAGE_COMMIT_LOG.md)
instead keeps that bootstrap write-once and publishes numbered commits, tested
on the server's CIFS mount. A logical change becomes visible as one generation,
instead of several branch/settings/checkpoint files changing independently.

```text
storage.json -> project/roots/<root>.json
                 documents: logical legacy address -> verified document version
                 scope_revisions: branch/pass/read-dependency versions
                 operations: durable idempotency receipts
                 parent: previous committed root
```

Unchanged documents are reused. Historical roots and document versions remain
readable and are not rewritten or garbage-collected. Legacy JSON/text bytes,
including whitespace, prompts and integers above JavaScript's exact range, are
copied exactly. This is a versioned document boundary, not a claim that every
legacy schema has been redesigned into native V2 take/cut/pass records.

Documents have explicit logical identities, scopes, categories and immutability
contracts. The engine does not infer ownership from labels or newest timestamps.
Import classification is a reviewed caller responsibility. Unknown semantics
can be retained as frozen legacy documents; that does not make the corresponding
feature safe to enable for writes.

## Publication and conflict rules

1. Pin a checksum-verified immutable root for the entire logical read operation.
   Snapshot references and returned state cannot mutate that pin accidentally.
2. Submit exact new bytes, a unique operation ID and every additional scope read
   by the operation. Written scopes are included automatically.
3. Acquire the existing cross-process project lock and reload current authority.
   The base must be a committed ancestor, not an unpublished staged candidate.
4. Reject changed read/written scope revisions and changed storage epochs. A
   change confined to another branch can merge into the newer current root.
5. Write the intent and immutable document versions, then a complete candidate
   root. Verify its references/hashes before changing the pointer. Recheck
   files across the verification pass and the pointer immediately before commit.
6. Publish the selected backend's pointer or immutable commit record. A crash before publication exposes the old root;
   a lost acknowledgement after publication is recognized by the operation ID.

The three version concepts remain distinct:

| Version | Meaning |
| --- | --- |
| Storage epoch | Maintenance fence; invalidates queued pre-maintenance writes |
| Root generation | Increments for each committed state publication |
| Scope revision | Changes only for scopes written by that transaction |

Ordinary branch saves do not change the storage epoch. Maintenance can advance
the epoch without altering branch data or scope revisions. It is not rollback,
promotion or default-branch selection.

Writers must declare read dependencies. For example, a processing commit that
depends on a source branch must watch that branch as well as write its own pass
scope. The engine cannot discover omitted domain dependencies automatically.

## Failure behavior

- Readers fail on missing or corrupt authority; no fallback to a similarly
  named legacy file or another branch.
- Conflicting changes, case/path collisions, scope/category reassignment and
  overwriting immutable work are refused.
- Intents, staged bytes and candidate roots are retained on uncertain failures.
  Retrying needs the original operation ID, pinned base and exact request.
  Reusing an operation ID for different changes is an error.
- Existing files are never replaced to publish an immutable document. Publication
  uses Linux `RENAME_NOREPLACE`, Windows no-overwrite rename, or a no-overwrite
  link/unlink fallback over independent staging (never the source file).
  Permission errors do not trigger an overwrite fallback. A tested CIFS mount
  supports this step but **fails concurrent atomic pointer replacement**; it is
  not qualified for replacement-based activation. The opt-in commit-log backend
  avoids that operation. Copy migration/recovery journals also support it;
  normal runtime ports and production cutover remain gated.
- Import requires a new output beneath an independent-copy receipt's lab.
  Source hashes are checked again before publishing the initial root. Failed
  imports remain blocked and preserved for inspection; there is not yet an
  automatic import-resume or cleanup command.
- No physical deletion/retention API exists here. Unreferenced staging, old roots and
  operation receipts must not be manually purged as if they were UI caches.

### Atomic checkpoint assignment retirement

`ControlStore.commit(..., retire_pointers=[...])` can combine replacements and
retirement of canonical `checkpoints/clip_NNNN.json` assignments in one root.
Only an existing mutable `branches` control owned by that exact main/named
branch qualifies. Arbitrary controls, immutable takes, media descriptors, absent
assignments, duplicate entries and simultaneous replacement/retirement reject.
All retired assignments' bytes and prior roots remain retained. No filesystem
file is deleted, and this API is not a media cleanup or garbage collector.

The input root identifies the retired versions; their branch scopes are watched
alongside replacement scopes. A concurrent branch edit rejects the operation,
while unrelated branch work can be preserved. Request identity includes the
retirement witnesses. Primitive retries with the same operation ID and request
acknowledge the original commit without applying the retirement again.

`BranchControlDocuments.retire_pointer(path)` stages the same operation. Reads
within that transaction observe the staged absence; separate pinned readers
still see their original assignments. The runtime requires a branch-writer
grant, confines node retirement to the selected branch, verifies current
workflow ownership at commit and forwards the exact accepted result pin.
The existing checkpoint restore handler uses this port inside an explicit
runtime binding. It retains its ancestry, compatibility, ALT and owner checks;
V1 still uses its existing pointer recovery journal. Checkpoint listing and
independent-candidate attribution are also runtime-aware: attribution adds only
an immutable alias, sharing verified source files and leaving assignments alone.
Deletion handlers and production HTTP/browser host registration remain separate
gates. See [runtime qualification](STORAGE_RUNTIME_REHEARSAL.md) for the actual
copied-chain and live CIFS evidence and the opaque legacy-archive correction.

## Development API and compatibility boundary

The marker is `h3_control_state_rehearsal_v1`, mode
`control_only_rehearsal`. `ControlStore` and pinned snapshots require explicit
`control_rehearsal_access(project)` scope. Normal H3 readers deliberately reject
the format even inside the older payload bridge's test scope. Do not remove its
marker to force it into legacy mode.

`create_control_rehearsal(...)` imports explicitly reviewed documents only; it
does not copy/rebind video, latent, audio or image payloads. The test store is
not a renderable migrated project. The existing UI is **not** redirected to it.

### Branch operations now exercised through the port

`WorkingBranches(..., rehearsal_controls=BranchControlDocuments(store))` is an
explicit test/tool dependency, still requiring `control_rehearsal_access`.
Without it, the existing V1/bridge paths and format gates remain in force.
The same branch load/save/create/default methods now run against either their
existing files or pinned logical documents. The port never exposes an immutable
blob path to a mutable legacy writer, and never materializes a shadow legacy
directory tree.

- An outer operation pins one root. Nested branch calls share that view and
  stage writes until the outer operation succeeds. Exceptions publish nothing.
- Authoring recovery uses the same checkpoint-lineage/chapter-root rules as the
  legacy reader. Assigned prompts, string-preserved uint64 seeds and resolution
  are recovered; stale predecessor assignments are excluded. This cheap
  selection is not full payload validation or assignment authorization.
- Saving recovered authoring publishes its backup and updated record together.
  Forking publishes the branch record, selected checkpoint pointers, Plan mirror
  and filtered editorial ALT/trims/locks together. Empty forks retain authoring
  but no checkpoint assignments or accepted alternates.
- Default selection changes only the default record, not prompts or accepted
  picture selections. It is not a checkpoint promotion operation.
- Reads and missing-document checks watch explicit branch/project scopes.
  Forks watch their source branch; independent branch saves can merge. Import
  descriptors with incorrect ownership/categories are rejected, not rewritten.
- Existing save/create operation IDs provide domain-level retry receipts.
  Storage attempt IDs are separate because a pre-publication retry can generate
  new revision IDs. After a lost acknowledgement, retry against current state;
  an explicitly supplied stale `base` remains fenced, not silently refreshed.
  Once a newer branch save supersedes a receipt, an older retry cannot replace it.
- `BranchControlDocuments(store, base=snapshot)` tests queued work's scope and
  epoch fences. Normal browser/queued-node handoffs do not yet carry these pins.

Legacy read-only listing retains its no-write behavior: it does not create a
coordination lock file. Control reads fail on missing/corrupt authority and never
fall back to the live project or its old directory tree. Pending imported
checkpoint-restore journals still block authoring recovery.

This is actual branch API integration on copied controls, **not** an end-to-end
browser test, a generation/processing writer conversion, or a renderable V2
project. Combined-store checkpoint graph and processing-catalogue reads now have
explicit pinned ports. Assignment/deletion, generation/processing saves,
reference caches, histories and media endpoints still need their transactions.

Required next integration steps:

- Convert feature reads to a pinned control view and writes to complete domain
  transactions. Never hand a legacy mutable-file writer an immutable blob path.
- Use the implemented combined payload/control commit from complete feature
  operations, including reference/deletion ownership witnesses.
- Define branch/pass/history dependency scopes for every operation and carry
  the epoch/read revisions through queued work and durable handoffs.
- Convert directory scans, existence tests, restore/assignment journals and
  browser/media endpoints explicitly; do not monkey-patch filesystem calls.
- Complete semantic schema validation, root-history retention, initial control
  import restart, external-path policy and ownership gates. Combined media join
  and reverse-copy recovery already support interrupted-copy resume.
- Execute native Windows/SMB durability/permissions/locking tests and actual
  workflow integration before offering any production activation.

## Tests

`tests/_storage_branch_controls_unit_test.py` runs 20 standard-library branch
tests: V1 recovery/listing parity; read-only listing; save/backup and fork
atomicity; ALT/trim preservation; empty branches; default selection; exact large
seeds; assignment recovery and stale-predecessor exclusion; domain retries;
stale browser/base/source/epoch rejection; pending restore, corrupt authority and
wrong import contracts. A separate private replay exercises the same API on all
460 copied project controls, including the seven-scene 960×544 branch.

`tests/_storage_state_unit_test.py` uses standard-library fixtures to test exact
bytes/large seeds, atomic grouped updates, pinned reads, stale writes, independent
merges, declared dependencies, epoch fencing, immutable/corrupt/missing records,
interrupted publication, lost acknowledgements, uncommitted ancestors, reference
mutation, verification races, path/link rejection, two threads and separate
processes. It also tests unchanged source bytes and blocked failed imports.

The storage compatibility CI definition includes this test on Linux/Windows
with Python 3.12/3.13. The definition alone does not establish that native Windows
or SMB execution passed.

Thirteen focused pointer-retirement tests pass, together with the 58-entrypoint
CPU regression run. On the full copied chain, a new private seven-scene fork
passed 13 checks (171.807 seconds): retire scenes 6–7 atomically, preserve the
historical seven-scene view, reject stale repeat, explicitly restore the exact
pointer bytes, preserve every pre-existing branch control and verify all 7,182
media hashes. The live ComfyUI/CIFS diagnostic passed 107 checks, including an
interrupted retirement before publication, grants, normal reader visibility,
reassignment and unchanged payload/bootstrap bytes (23.852 seconds backend /
24.286 seconds workflow). This does not qualify the complete restore API/UI or
physical deletion/retention lifecycle.
