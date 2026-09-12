# Storage relocation bridge: copy-only development gate

This is the compatibility step before a production storage migration. It does
not enable V2 storage for normal nodes, and it does not migrate projects on
startup or update. Organized payload writers can now be enabled explicitly on
test copies; the complete V2 control/state layout remains a target policy.

## Implemented

`storage_resolver.py` resolves an exact legacy address to a validated physical
location. Reverse lookup preserves the legacy address when computing source
contracts, persisting references, or checking ownership. Historical JSON is not
rewritten, and hashes, prompts, seeds, revision identities and reference-cache
descriptors remain unchanged.

The bridge now covers the exercised generation/ALT artifact readers, checkpoint
graph ownership, processing catalogue, deferred source/assembly readers,
reference tensor store, PNG verification, export variant routing, and processing
deletion ownership. Media previews receive physical paths; persisted identities
and deletion ownership comparisons use logical paths.

Relocated PNG folders are included in cleanup discovery even where legacy
folder scans would no longer find their indexes. Variant names continue to use
their logical family names, not the new export directory IDs. Existing PNG
coordination files stay at their original addresses/inodes and are excluded from
relocation. Atomic JSON publication uses a bounded `.tmp-<32-hex>` sibling name,
with explicit target and temporary-path budget checks.

No marker means the existing legacy behavior. Unsupported versions, incomplete
relocation phases, missing alias authority, checksum failures, overlapping or
case-colliding maps, and path/link escapes fail closed. Alias maps are separate
authority, not a cache that can be discarded and rebuilt by guessing filenames.

## Explicit restrictions

- The only bridge marker currently supported has format
  `h3_storage_relocation_bridge_v1`, version `1`, and mode `rehearsal`.
- It is readable only inside an explicit `rehearsal_access(project)` test/tool
  scope. Normal UI/nodes reject it. **There is no production activation path.**
- The rehearsal relocates known media/export/support files. Legacy checkpoint
  control records and mutable branch state remain in their current locations.
  It is not the complete V2 take/cut/pass/root schema.
- `prepare(..., organized_writers=True)` enables `organized_payloads_v1` only
  on the rehearsal copy. Generation/ALT and processing payloads use their stage
  directories; PNG forks and final video exports use short export destinations.
  Prompt snapshots, recovery snapshots, asset backups, reference-cache adoption
  and exercised preview writers use `project/` subdirectories. Existing mapped
  assignments remain stable. Without this policy, new addresses stay V1.
- Canonical/immutable checkpoint JSON, manifests, branch authoring, history and
  orchestration still use legacy control schemas/paths. Optional source-import
  and other unconverted writers remain a production gate. This is **not** a
  finished V2 writer or complete folder cleanup.
- Third-party tools opening literal old paths are outside an H3-only resolver's
  compatibility boundary. Retained literal files or explicit relinking need a
  separate production policy.

## Rehearsal and recovery

`storage_rehearsal.py` is an internal copy-testing API, not a node or general
migration command. It requires an independent-copy receipt and a proposal whose
file hashes match that receipt. The copy must be beneath the receipt directory
and distinct from the recorded original source. The journal must be in a new
sibling directory outside the copy.

Preparation rejects occupied targets, untracked children in moved directories,
unsupported control-record moves, invalid maps and path-budget violations.
Publication rechecks both moved payload hashes and retained control-file hashes:
a changed Plan/branch/history record prevents publication. It uses the existing
cross-process project commit lock, an operation identity and an incomplete marker
while relocating. A ready marker is published only after verification.
New untracked files also block publication even if old control hashes match.

`storage_writes.py` fences the exercised save/export operations for their whole
I/O lifetime, and history/handoff/asset/ownership mutations share the same
re-entrant cross-process lock. Marker validation occurs after lock acquisition.
Each new payload reservation publishes an immutable complete alias map before
atomically replacing its small authority pointer. Uncertain publication retains
the map and media instead of deleting possibly referenced work. Generation
payloads are flushed before metadata publication; genuine fsync failures
propagate. This is reservation safety, **not** a unified V2 root-state commit.

The journal supports restarting a partially moved copy and rolling it back.
Recovery checks all source/destination collisions before moving files back. It
does not overwrite changed files or another operation's marker. Coordination
files are not relocated. Recovered alias/marker records are preserved next to
the journal; repeated completed rollback is a no-op.
After new reservations, edits or additional files, simple rollback refuses
without changing a ready marker or deleting the new work. A reverse migration
that changes the source in place is **not implemented**. Keep the tested copy
and its journal; do not remove its marker to force it into legacy mode.

### Independent legacy recovery after new work

`storage_recovery.py` provides a separate copy-only recovery path. It captures
the **current** accepted files and controls, including post-relocation renders,
prompts, large integer seeds, branch selections and appended PNG indexes. It
does not restore old controls over newer work or dismantle the organized copy.

```python
from storage_recovery import prepare_legacy_copy, recover_legacy_copy

# All three paths refer to a private, independently receipted rehearsal lab.
# The output and journal directories must not exist at preparation time.
journal = prepare_legacy_copy(receipt_path, new_output, new_journal_directory)
result = recover_legacy_copy(journal)
# After interruption, resume the same journal; never remove the recovery gate.
```

The immutable SHA-bound plan records every non-lock source file, destination,
size and checksum plus a digest of the current control records. Mapped payloads
return to their exact logical legacy addresses; current V1 control JSON is
copied byte-for-byte. The original marker and all retained alias authorities
are archived outside the recovered project under
`<new_output>/.h3-storage-recovery/authority/`. Coordination locks are excluded
and recreated independently when needed.

The new project stays blocked by an incomplete marker while bytes are copied.
Source and destination hashes and namespaces are checked before publication,
including changes made during verification. A durable verification record is
published before removing that operation's temporary gate. The result is an
ordinary V1 project that does not require the bridge or its test scope.

Unknown organized files, case/path collisions, links/junctions, changed source
controls, occupied or edited destinations, unsupported filesystem operations,
and insufficient space stop recovery without overwriting either copy. Failed
staging files and journals remain available. A lost publication acknowledgement
can be resumed. Re-running recovery against an already edited published copy
refuses; it never reinstalls the gate or restores the previous snapshot.

Limitations:

- Requires another full independent copy's disk space. This is not an in-place
  reverse migration, space-saving rollback, or production activation feature.
- Atomic no-replace publication uses a hard link **from independent staging
  bytes**, then removes the staging name; no hard link to the source project is
  created. Filesystems without this primitive fail closed. Native Windows/SMB
  behavior still needs execution on those hosts.
- A crash between reserving the empty output directory and publishing its
  ownership record leaves an unowned directory. Recovery refuses to adopt it;
  preserve it for inspection and prepare a new destination/journal.
- File contents are preserved, not source ACLs/permissions. Choose a private
  rehearsal directory and review permissions before using a recovered copy.
- Explicit Windows path budgets can reject long legacy names. The operation
  does not rename them or claim to make legacy paths portable automatically.
- Saved workflow paths outside the project are not relinked. Existing missing
  historical references are preserved, not silently repaired or deleted.
- The control digest is a recovery witness, **not** the future V2 authoritative
  root-state transaction protocol. Production control-state conversion and all
  remaining writer/UI integrations are still gated.

Final export reservations include provenance JSON alongside video, audio and
subtitles. Provenance resolves related files through their logical addresses,
so organized `audio.wav` is not mistaken for a missing legacy
`<video-stem>.generated.wav`.

## Saved workflow paths

`storage_workflows.py` audits exact and embedded project-path values without
editing saved workflows. Explicitly approved exact external-node paths can be
relinked in an in-memory clone. Unknown syntax, missing targets and unapproved
nodes block relinking. H3 input identities are retained verbatim.

One known non-authoritative cache is handled separately: a cloned Plan Studio
workflow drops `h3_plan_studio_checkpoint_cache_v1`, allowing fresh server
preview URLs. It does not alter authoring, prompts, seeds or the saved original.
Historical recovery documents are never rewritten. This helper does not yet
provide a production-wide relinking UI or handle arbitrary third-party tools.

This is not yet a production transaction protocol: the original must remain
untouched. Production still needs all writers fenced across their real commit
boundaries, a supported atomic root-state model, all workflow integrations
converted, native Windows/SMB fault coverage, and a defined external-path policy.

## Checks

- `tests/_storage_resolver_unit_test.py`: confined lookup and reverse identity,
  graph ownership, authority/version failures, interrupted movement/restart,
  collision-preserving rollback, concurrent control changes, marker ownership,
  untracked files and Windows temporary-path budgets.
- `tests/_storage_bridge_consumers_unit_test.py`: real small FFV1-to-PNG reuse,
  append and numbered fork after relocation, plus actual processed-take/PNG
  deletion on disposable fixtures while retaining another scene.
- `tests/_storage_writes_unit_test.py`: faulted alias/pointer publication,
  stale-lock checks, two-thread and separate-process coordination, occupied
  destinations, links, handoff/history gates and safe rollback refusal.
- `tests/_storage_writer_integration_test.py`: CPU generation/ALT saves,
  pixel/latent/DeRoPE/video-refine/custom storage routing, resume, final assembly,
  PNG export/reuse, asset backup recovery and generation publication failures.
  Small synthetic tensors exercise storage; they do not validate model quality.
- `tests/_storage_workflows_unit_test.py`: reviewed workflow-copy relinking,
  preview cache clearing, unchanged originals and exact large integers.
- `tests/_storage_recovery_unit_test.py`: independent recovery of current
  controls/new work, interrupted copying, lost publication acknowledgements,
  disk-space failure, changed/untracked source and target files, verification
  races, unsupported publication, link/case collisions and Windows budgets.
- `.github/workflows/storage-compatibility.yml` defines Linux/Windows and Python
  3.12/3.13 filesystem tests. Adding the file is not evidence that Windows/SMB
  execution or native crash recovery has passed; record actual CI results.
- Existing checkpoint, branch, ALT, upscale, reference-cache, PNG, persistence
  and deletion suites remain regression gates.
- Real project copy comparisons must check identical active lineages, authored
  settings, selected ALT prompt/seed/original-audio routing, source contracts,
  complete owned-file sets, and every original copied file's checksum after
  rollback. Historical missing references must not become new missing active
  takes or be silently repaired/deleted.

The live project is not a test fixture. Never point these development helpers at
it or offer normal migration UI until the remaining production gates pass.

The follow-up [atomic control-state rehearsal](STORAGE_CONTROL_STATE.md) is a
separate control-only test format. It does not change this bridge's marker,
legacy control paths or normal-node restrictions.
