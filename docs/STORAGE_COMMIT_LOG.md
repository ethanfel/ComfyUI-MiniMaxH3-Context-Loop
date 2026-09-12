# Immutable commit log rehearsal

Status: explicit copy-only backend, **not production activation**. Tested inside
live ComfyUI on its actual Linux CIFS output mount with isolated dummy files.
Migration and recovery can now use the same no-overwrite protocol, including
their progress journals and incomplete-copy gates. Normal workflow integration
and production cutover remain unfinished.

## Why another publication backend

The tested CIFS mount can expose a missing destination during replacement of an
occupied file. `storage_commit_log.py` avoids that operation entirely. A
write-once `storage.json` identifies the protocol and initial state. Complete
numbered records below `project/commits` publish successive state roots with
no-overwrite rename. An acknowledgement file binds each accepted record's hash.

```text
storage.json (write-once bootstrap)
  -> project/commits/000000000001.json + checksum acknowledgement
  -> project/commits/000000000002.json + checksum acknowledgement
       -> project/roots/<immutable root>.json
            -> exact controls + accepted payload descriptors
```

Commit records form a contiguous checksum-bound chain, not a timestamp-sorted
search for whichever file looks newest. Readers and writers use the same
reentrant process/thread project lock. A reader pins the resulting immutable
root and can continue reading it after another writer commits.

The storage epoch, root generation, scope revision, operation ID and logical
document ownership rules remain those of the control-state engine. Log sequence
is separate from root generation: initial import also publishes a log record.

## Crash and integrity contract

- Publishing the complete numbered record is the visibility point. Before it,
  only the old state is visible; after it, the complete new state is visible.
- Unaccepted staging and candidate roots never satisfy a read. They are retained
  for diagnosis and retry, not guessed into the accepted path.
- A lost acknowledgement is reconciled against the exact operation ID/request.
  The original receipt is returned; no duplicate generation is created.
- Retrying a visible but unacknowledged record revalidates its controls and new
  payloads against the parent root, re-flushes the commit, then publishes the
  checksum witness. An I/O/flush error is not converted into success.
- A missing acknowledged tail, an interior gap, corrupt witnesses, a different
  bootstrap, unknown entries, or inconsistent predecessor references fail closed.
  There is no automatic older-root fallback.
- The initial control importer publishes a write-once `building` gate before
  copying documents. Readiness is a new log record. An interrupted import cannot
  expose a half-imported directory as a usable project. Import resume is still
  a separate requirement; do not remove this bootstrap to retry.
- Metadata/hashes are bound to the opened file handle, followed by checks of
  both that handle and its path. This accommodates CIFS refreshing cached
  pre-open timestamps without ignoring actual modification or replacement.

All accepted commit/history files are retained. There is no log compaction or
garbage collection yet. Deleting an entire tail **and every witness to it** is
not detectable without an external authority; this is not a tamper-proof log.
Successful fsync/rename and process-crash tests do not prove survival of server
power loss, filesystem rollback, hardware failure, or arbitrary mount changes.

## Opt-in boundary

`create_control_rehearsal(..., commit_protocol="immutable_slots_v1")` selects
this backend for a fresh independently receipted control copy. It keeps the same
explicit `control_rehearsal_access(project)` gate. `ProjectStore` recognizes the
same protocol in a separately verified combined-copy bootstrap. No normal
workflow/router automatically selects it; no installed project is converted.

The original replacement backend remains available for existing test copies
and remains rejected on known Linux CIFS/SMB mounts. Setting an unknown protocol
never falls back to replacement. `prepare_join` inherits the protocol from the
control copy: both its building/ready gates and external progress journal use
immutable records. `prepare_legacy_copy(..., commit_protocol="immutable_slots_v1")`
explicitly selects no-overwrite recovery journals. The default recovery mode
retains the old filesystem guard; a log-backed source alone does not change it.

Recovery stages a complete owner-and-gate directory inside its private journal
before publishing a new destination. Failed owner/proof writes leave partials
outside the recovered tree, so retry does not adopt an unowned target or mistake
temporary files for project contents. Existing different bytes remain errors.
Journal scope and immutable-plan identity are checked before creating locks.

## Evidence

- 37 focused tests: exact controls/seeds, CAS, epochs, concurrent readers/writers,
  actual process exits at four boundaries, lost acknowledgements, damaged media,
  missing/corrupt commits, occupied destinations, and initial import failures.
- 58 journal/migration/recovery tests: phase CAS, identity protection, interrupted
  joins, lost gate/progress/final acknowledgements, failed recovery owner/proof
  publication, directory re-flush on retry, explicit path budgets, new work
  retained on retry, source/target edits, and independent reverse copies.
  The original replacement-mode regression tests also pass. The exact normal
  run-lock file may appear after checkpoint-manager reads; recovery retry
  accepts only its expected address and empty/one-NUL contents. Other added
  files remain errors, not silently adopted project data.
- A validated CPU-only workflow ran the actual backend in a separate subprocess
  inside ComfyUI (Python 3.13.14, kernel 6.18.38-Unraid, CIFS). All 39 checks passed,
  including real process exits/retries, coupled media/control acceptance,
  write-once bootstrap, missing-tail rejection, the actual control importer,
  interrupted/resumed media join, newer edits surviving a completed-join retry,
  and reverse recovery after owner/proof publication, directory-flush failures
  and normal checkpoint-reader use of the recovered project.
  Installed node code, user projects and backups were not changed.
- An earlier live probe passed the commit checks but stopped during fresh-file
  hashing. A separate timestamp diagnostic established the stale pre-open
  metadata behavior; the opened-handle fix then passed the full probe. Failed
  evidence was retained rather than overwritten.
- A full independent copy of the current combined chain passed 16 checks:
  all files copied and hashed independently, a real branch prompt/seed edit,
  a real copied PNG plus index accepted together, both checkpoint/ALT graphs
  and processing catalogues unchanged, and all 7,184 payload hashes verified.
- Interrupted/resumed reverse recovery from that log-backed copy passed all 12
  checks: 466 current controls and 7,184 independently hashed payloads restored
  to V1, current branch authoring/ALT selections retained, log history archived
  outside active state, and the complete source namespace/hashes unchanged.
- The subsequent actual journal-mode migration passed 20 checks on a fresh
  independent full copy: 460 controls and 7,182 payloads, interruption at payload
  100, exact branches/ALT graphs/pass catalogues, full source/destination hashes,
  and a longest physical payload path of 221 characters. A fresh journal-mode
  reverse copy passed 13 checks, recovering 466 current controls, 7,184 payloads
  and archived history across 14,904 files. These are copy-only services, not
  live-project activation or full normal-node workflow tests.
- Latest-code revalidation of those completed copies passed all five checks,
  including completed recovery retry after actual checkpoint-manager reads.

Private reports remain in the simulation lab, not the repository. Native
Windows and full normal-node/UI integration remain separate release gates.
