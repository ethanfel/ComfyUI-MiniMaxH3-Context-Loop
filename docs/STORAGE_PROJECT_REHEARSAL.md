# Combined project-storage rehearsal

Status: **copy-only, not a production migration or normal workflow backend**.
No automatic conversion, live project writes, commit or push is part of this
rehearsal. See the [completion checklist](STORAGE_COMPLETION_CHECKLIST.md) for
remaining feature and platform gates.

## One accepted state for controls and media

`storage_project.py` extends the atomic control engine. The selected immutable
root contains both exact legacy control bytes and checksum-bound media
descriptors. Each descriptor binds one logical address, explicit owner scope,
mutability policy and physical file witness. Staging a payload does not make it
visible; `commit_artifacts` accepts its verified receipt and the related controls
in one publication. Historical roots retain their original versions.

Payloads are independently copied, not linked to the source. Filename labels
are not identity or ownership. Duplicate logical/physical owners, case/file-
directory collisions, authority overlap and altered staging receipts fail.
Changed payloads are fully hashed before acceptance. Whole-store verification
hashes every accepted payload and checks for changes during the verification.

The format is `h3_project_storage_rehearsal_v1`, mode `project_rehearsal`.
`ProjectStore`, snapshots and reader/writer ports still require explicit
`control_rehearsal_access(project)` scope. Ordinary V1/bridge readers reject it;
there is no fallback to stale legacy files or the live project.

This versions existing schemas; it does not claim that every legacy document
already implements native take/cut/pass semantics. Frozen unknown documents are
preserved, not silently made writable. For example, prompt-history revision
metadata can change even when an executed prompt itself is immutable; its
writer contract must not be inferred merely from the revision filename.

## Copy, interrupt, resume, recover

`storage_project_migration.py` joins an independently receipted source with an
exact initial control import. It records the complete inventory/target mapping,
hashes the source, gates the target, copies independent bytes, validates the
candidate and publishes one combined root. Source and destination locks prevent
cooperating writers from racing the operation; hash/namespace checks also detect
uncoordinated edits. Unknown target files stop resume rather than being adopted.

The media join resumes interrupted copies. Retrying a completed join recognizes
its receipt and keeps later accepted work; it cannot revert to the initial root.
Initial control import still requires a fresh target after interruption.

`storage_project_recovery.py` feeds the reverse-copy service with the current
accepted controls/media, restoring their exact logical V1 addresses. Historical
roots, superseded versions, journals and unaccepted staging are archived outside
the recovered project under `.h3-storage-recovery/authority`; they are never
activated by filename guessing. Interrupted recovery stays gated and resumes.
The combined source is not changed. This is not an in-place rollback.

## Real-copy evidence

On the independent full copy of the user's chain:

- Joined 460 exact control documents and 7,182 media payloads; retained ten old
  bridge authority documents. Injected interruption at payload 100 and resumed.
  Full SHA-256 and independent inode checks passed. Longest payload path: 218
  characters, within the configured 240-character budget.
- Actual branch load/list matched V1, including the seven-scene 960×544 branch,
  exact large seeds, authored prompts and original chapter assignments.
- Pinned checkpoint graph matched both source branches: 21 revisions, no broken
  entries. Processing catalogues matched 20 entries for main and three for the
  named branch, with actual media preview paths and logical ownership preserved.
- Saved a new prompt/seed through `WorkingBranches`, then accepted one copied
  PNG and its new index together. Reverse recovery retained 462 current controls
  and 7,183 payloads; all 15 checks passed, including full hashes, independent
  inodes, both branch graphs/ALT selections, exact authoring and unchanged source.

Reports live in the private simulation lab, not this public repository. They
contain private project paths; do not commit test data or prompt contents.

## Consumers connected so far

`ProjectReadView` gives checkpoint graph and processing catalogue readers one
pinned logical directory view. It verifies imported controls, maps accepted
payloads to read-only physical paths and does not materialize a shadow legacy
tree. Unindexed files cannot satisfy a read. Missing accepted media stays visibly
broken; corrupt control authority fails the operation instead of being hidden by
the old reader's permissive JSON error handling.

`BranchControlDocuments` connects actual branch load/save/fork/default methods.
Complete branch changes publish together, with explicit source dependencies.
Explicit historical pins must be committed ancestors, not staged candidates.

`HandoffControlDocuments` connects the actual durable handoff state machine:
create, claim, release, transition, load and list. It preserves exact integer
seeds, status rules, bounded attempts and uncertain-delivery states. Source
branch revision and storage epoch are captured at creation; stale handoffs cannot
be claimed. Imported records without a valid pin require manual resume but can
be cancelled. Competing claims cannot both commit. Lost acknowledgement does not
make an accepted claim available again. No queue submission happens in this port.

Normal routes do not construct these ports. Actual UI, live queue delivery,
generation/ALT/processing saves, references, history and deletion require their
complete operation boundaries before activation. Read-only physical paths must
never be handed to a legacy mutable writer or deletion routine.

## Filesystem evidence and release blocker

Local Linux tests cover interruption, lost acknowledgements, corrupt/missing
authority, stale branches/epochs, ownership collisions and process locking.
The CI definition includes Windows/Python 3.12–3.13, but has not run remotely.

Actual tests on the agent host's CIFS output mount found:

- Hard links rejected with `EACCES`. Linux `RENAME_NOREPLACE` publication works,
  refuses occupied names and respects Python audit guards. Source files remain
  independent. Windows uses its no-overwrite `os.rename` behavior.
- File/directory fsync and two-process exclusion/release passed.
- **Atomic pointer replacement failed**: a failed replacement left the test
  destination missing; another run's concurrent reader observed a missing file
  while replacements returned success. The Linux CIFS implementation can unlink
  an occupied target and retry the rename after `EACCES`/`EEXIST`.
  [Linux CIFS rename implementation](https://github.com/torvalds/linux/blob/master/fs/smb/client/inode.c).

No-overwrite publication fixes the first item, not the last. Bounded retries
cover transient sharing errors only when staging still exists and the target
has not changed; I/O/disk-full errors and ambiguous destination changes propagate.
The tested CIFS mount is **not qualified for the current root-pointer protocol**.
The replacement-mode control import/commit, media staging/join and reverse-copy
services check the effective Linux mount type before starting writes. Known
CIFS/SMB mounts are rejected with a clear error; ordinary legacy savers are not disabled
by this gate. This conservative check is not proof that every other filesystem
is qualified, and it does not change the system's mount configuration.
Do not mask this with automatic fallback to another root or call migration ready.
Verify the actual ComfyUI server filesystem separately; no native Windows,
power-loss or server-restart result is claimed here.

The subsequent live CPU-only diagnostic confirmed that ComfyUI's output also
uses CIFS (kernel 6.18.38-Unraid). In a fresh isolated probe folder, replacement
failed with EACCES after 15 updates and left the destination missing;
no-overwrite publication passed. The server itself therefore needs an
SMB-compatible commit protocol before this format can be offered there. No
production project files, model execution or server configuration were involved.

### No-overwrite backend follow-up

The opt-in [immutable commit log](STORAGE_COMMIT_LOG.md) now passes 39 checks
inside the live server on that CIFS mount, using the actual storage code in an
isolated subprocess. It keeps `storage.json` write-once and publishes numbered
checksum-bound commits. A fresh-file timestamp discrepancy was separately
diagnosed and fixed by binding integrity checks to the opened file handle.

The complete copied chain also passed 16 log-backend checks, including an actual
branch save, coupled PNG/index acceptance, unchanged graphs/ALT selections and
processing catalogues, and full SHA verification of 7,184 payloads. This does
not qualify the **old replacement-based** backend or enable normal workflow
writers. The journal port has since passed the live diagnostic's forward-copy
interruption/resume, retry after newer edits, and reverse-copy owner/proof
publication failures. Full normal workflow integration remains a release gate.

Independent reverse recovery from the log-backed copy subsequently passed all
12 checks after interruption/resume: 466 exact current controls, 7,184 verified
payloads, current branch authoring and ALT lineage, archived log history, and
unchanged source hashes/namespace. It recovers the latest logged root, not the
older root retained in the static bootstrap.
