# Conditional asset-library editing

Companion library views can remain open while the native Carousel changes a
folder or asset. The command interface binds an edit to the reviewed storage
revision and retains an operation receipt in the authoritative input-side
catalog. It delegates all asset/folder semantics to `ProjectAssetStore`.

## Read

`GET /minimax_h3_context_loop/project-assets?project=<run>&create=false` retains
the public catalog format and adds `library_command_version: 1` and
`library_revision`. The revision is the native `storage_revision`, or `empty`
for an absent catalog. It covers presentation-only changes such as folder names
and colors; the generation fingerprint (`revision`) does not cover those.
Reading an absent catalog with `create=false` does not create a project.
Existing primary-from-mirror recovery behavior remains unchanged.

Add `operation_id=<32 hex ID>` to read `{catalog, receipt}` for a retained command.
An absent receipt is `null`. Catalog reads and command responses omit internal
receipt storage. Public receipts include the project, action, request hash,
before/after revision and created asset/folder IDs. They do not contain credentials,
the full request, deleted lyrics/metadata, or internal cleanup paths.

## Write

`POST /minimax_h3_context_loop/project-assets/library` requires the existing
native project ownership proof. Assets remain project-shared; `branch_id` does
not select a separate library. Send a JSON object with `project`,
`command_version: 1`, a new lowercase 32-hex `operation_id`, the reviewed
`base_revision`, and one of these `action` values:

| Action | Additional fields |
| --- | --- |
| `folder_create` | `name`, optional `color` |
| `folder_update` | `folder_id`, `changes` containing `name` and/or `color` |
| `folder_delete` | `folder_id`; native behavior unfiles its cards |
| `folder_reorder` | `folder_ids`, an exact permutation of every folder |
| `asset_update` | `asset_id`, `changes`: tag, role, enabled, lyrics, folder_id and/or options |
| `asset_reorder` | `asset_ids`, an exact permutation of every asset |
| `asset_duplicate` | `asset_id`, optional `tag` and `folder_id` |
| `asset_delete` | `asset_id` |

The native store preserves shared media when duplicating cards, disables a
duplicated Source track, and retains its existing tag, role, track-group and
folder validation. Deleting a folder preserves its media. Deleting an asset
protects track-group members and media paths still shared by another card.
This API does not add cross-Plan/branch usage analysis or rewrite prompt tags.
Cross-project copying, upload, derived-image jobs and whole-project duplication
retain their existing endpoints; their multi-step operations are not covered
by this single-catalog command transaction.

On success the response is `{catalog, receipt, replayed}`. Repeat only the exact
request/operation ID after an uncertain result. A matching retained receipt is
checked before the old base revision and cannot create another duplicate.
Reusing an ID for different fields or a different project is rejected.

HTTP 409 means the library changed, 423 means ownership rejected the write, and
400 means invalid input. I/O errors use 500 because the catalog may already be
committed. Keep the exact operation/body after transport, unreadable response or
500 failures; query its receipt or retry it. A companion must not invent a fresh
operation ID merely because the first acknowledgement was lost.

## Persistence boundaries

The native per-project process lock surrounds review validation and mutation.
The reviewed revision is checked again when the operation's native method saves,
so reloading newer state inside that method cannot silently bypass the review.
Existing cross-process catalog CAS/file locking remains in use. Receipts are
committed with the input-side catalog and retained by subsequent legacy writes.
Public snapshots cannot erase them. The recovery mirror retains the existing
best-effort publication behavior; input-side catalog commit is authoritative.

Asset deletion commits catalog removal and its receipt before file cleanup.
Operation-status reads and exact retries finish cleanup for that already
authorized deletion, checking current catalog references before removing media.
An asset restored with the same ID is left intact. Thus a status read can finish
an earlier deletion; an ordinary library read never requests a new deletion.
Cleanup is not a transaction across multiple ComfyUI processes or external file
edits. Receipts survive restart but a caller still needs its original ID/body to
retry. At 1,024 retained commands per project, new conditional commands stop
instead of silently evicting IDs. Existing receipts stay readable/replayable.

## Validation

`tests/_asset_library_commands_test.py` checks empty reads and first creation,
folder-only stale reviews, membership/order, native duplicate provenance, shared
media deletion protection, interrupted responses and cleanup, restart/retry,
legacy receipt retention and a native reload between review and commit.
`tests/_asset_library_api_test.py` uses the real handlers with CPU-only ComfyUI
stubs and the real ownership guard, covering 409/423/500 and receipt reads.
Existing store, editor and catalog-notification tests remain applicable.
All projects and images are temporary fixtures; no production GPU workflow is
executed by this validation.
