# Conditional prompt-history commands

The existing `/minimax_h3_context_loop/prompt-history` endpoint now exposes
scoped reads and optional conditional writes for companion editors. It does
not apply text to Plan JSON or alter seeds, shared direction or generated media.
Existing native editor requests without `command_version` retain their behavior.

## Read and review

GET requires the exact `run_name` and canonical native `scene_id`. Named working
branches also require `branch_id`; omitting it selects Original (`main`). The
existing branch request wrapper carries this scope into the store's worker.

- List responses retain format `h3_scene_prompt_history_v1` and add
  `command_version: 1`, `working_branch_id`, and opaque `history_revision`.
- `revision=<id>` returns saved text with those same scope/version fields and
  the current history stamp. Compare the prompt hash with the listed metadata
  to detect an edit between the two reads.
- `operation_id=<id>` returns `{history, receipt}`. A null receipt does not
  authorize a fresh operation: retain and retry the exact original request.

The stamp covers project, scene, working branch, active revision and all revision
metadata. Legacy edits and execution bookkeeping invalidate it when they change
that state. Receipt bookkeeping itself is excluded. A copied history in a new
branch receives a different stamp; receipts from its source cannot be replayed
or reported as that branch's commands.

## Write

POST uses the existing project ownership headers and `branch_id` routing. Review
the current list and send the following JSON fields:

| Field | Contract |
| --- | --- |
| `command_version` | `1` |
| `run_name`, `scene_id` | Same exact project and canonical scene as the read |
| `operation_id` | New 32-character lowercase hexadecimal ID, retained by the caller |
| `base_revision` | Reviewed 64-character history stamp |
| `action` | `save`, `fork`, `activate`, `label`, `archive`, or `delete` |

`save` takes `prompt` and optional `parent_revision`, preserving native behavior:
identical text reactivates its existing revision; otherwise an active/selected
mutable draft is updated, or an executed parent gets a new child. `fork` takes
`revision` and `prompt` and always creates a separate child, even for identical
text or a mutable parent. `activate` takes `revision` and only changes the
history's active marker. `label` takes `revision` and `label`; `archive` takes
`revision` and a boolean `archived`; `delete` takes `revision`.

Native text normalization, 200,000-character prompts, 80-character labels and
archive/delete protections remain in force. Executed text is immutable; active,
executed and parent revisions cannot be deleted. Loading text into a companion,
applying it to the workflow, and saving workflow/branch authoring are separate
operations.

Successful responses contain `{history, receipt, replayed}`. Under the store
lock, an existing matching operation is checked before its old base stamp.
Exact retries return the retained receipt and current history without repeating
the mutation. Reusing an ID with a different request or scope is rejected. A
changed base produces HTTP 409; ownership denial produces 423; invalid commands
produce 400. I/O failures produce 500 because a write may already be committed.
On transport errors, unreadable acknowledgements or 500, query the operation
status or retry its exact body/ID; never create a new ID for that uncertain edit.

## Persistence and recovery boundaries

Conditional commands stage changed revision records in memory, then atomically
write `.command-journal.json` with the intended revision files, index and receipt.
They materialize revision files and the index before removing the journal. Native
store readers and writers recover a pending journal under the same process lock
before returning state. Consequently, a read after an interrupted command may
finish that previously authorized durable intent; ordinary reads create nothing
when no journal exists. This prevents a mutable prompt file from being exposed
with its old index hash after a process interruption.

Deletion commits the index/receipt before removing the unreferenced revision
file; replay also finishes any interrupted file cleanup. Receipts survive process
restart and are retained in the index. At 1,024 commands per scene history the
store rejects new conditional commands rather than evicting IDs and risking a
duplicate retry. Existing receipts remain readable/replayable. A durable receipt
does not preserve a caller's unsent request: companion clients must retain their
operation ID and exact body to retry it.

These guarantees target one ComfyUI process using the existing native store lock
and filesystem atomic replacement. They do not introduce coordination between
multiple ComfyUI processes sharing one output directory, transactional legacy
writes, or protection against manual edits to store files. Branch archival or
removal has its existing native lifecycle.

## Validation

- `_prompt_history_commands_test.py`: concurrent stale authors, immutable and
  mutable forks, lost acknowledgements, interruption between revision/index
  replacement, restart recovery, exact retries, and copied/named branch isolation.
- `_prompt_history_api_test.py`: real route functions with CPU-only ComfyUI stubs,
  branch echo, revision/status reads, 409 conflicts, interrupted I/O reported as
  500, recovery reads and the real 423 project ownership guard.
- Existing `_prompt_history_unit_test.py` and `_prompt_history_js_test.mjs`:
  legacy draft/executed/archive/delete behavior and native ancestry navigation.

All projects are temporary fixtures. Production workflow validation remains a
separate integration step; no GPU generation is required by these checks.
