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
| `asset_copy` | `source_project`, `asset_id`, boolean `enabled`, `folder_id` (empty for unfiled), `preview_revision`; requires `library_copy_version: 1` |
| `asset_derive` | `asset_id`, `crop`, `target`, `resample`, `tag`, `folder_id`, `preview_revision`; requires `library_image_version: 1`, see [image commands](asset-image-commands.md) |

The native store preserves shared media when duplicating cards, disables a
duplicated Source track, and retains its existing tag, role, track-group and
folder validation. Deleting a folder preserves its media. Deleting an asset
protects track-group members and media paths still shared by another card.
This API does not add cross-Plan/branch usage analysis or rewrite prompt tags.
Upload, model-based image jobs, whole-project duplication and the legacy
import/derive endpoints retain their existing behavior. Reviewed cross-project
copies and resampled image variants use staged commands.

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

## Reviewed copies from another project

Public catalogs advertise `library_copy_version: 1` and
`library_pending_copies: [{operation_id, action, phase}]`. Browse source project
summaries through the existing `/project-assets/projects` endpoint and read a
chosen catalog with `project=<source>&create=false`. Original previews use the
existing source-project `/project-assets/media` endpoint.

Review with `GET /project-assets?project=<destination>&copy_source=<source>&copy_asset=<id>&enabled=true|false&folder_id=<folder>`.
The response identifies both projects and the root asset, destination
`base_revision`, source storage revision, all required audio dependencies,
`copyable`, an explanatory `issue`, and a 64-hex `preview_revision`. The review
covers the catalogs, selected folder/enabled intent and source media identities.
Missing media, malformed track bindings and catalog capacity prevent a copy.
Only one enabled Source track is allowed; choosing a disabled copy preserves an
existing soundtrack. Reading/reviewing does not request a project write.

Submit `asset_copy` through `/library` with the exact review fields. The native
importer runs in a private project store beneath `.library_copies/<operation>`.
Its normal multi-save group import never publishes partial destination state.
Root/stem IDs and audio links are remapped, dependency stems are disabled audio
references, tags remain unique, lyrics/options survive and the chosen folder
contains the entire copied group. `source_origin` records source project/asset,
source parent, transform and prior provenance without leaving a dangling local
parent or reusing a source transform operation ID.

Source hashes and catalog/media identities are checked before preparation is
accepted. The prepared manifest freezes the copied bytes and final new asset
IDs. Retry can therefore finish those same bytes even if the source later
changes. Operation locks serialize exact retries across processes, while the
brief target catalog commit still uses native storage CAS. Reciprocal imports
never hold both project locks. New media uses operation-specific paths in input
and output stores; only after both copies exist does one authoritative catalog
commit publish the entire group and its receipt. Rejected publication cleans
this operation's unreferenced media. I/O interruption keeps the stage for retry;
an interrupted process can leave staged/promoted files until recovery.

An operation-status read without a receipt returns `pending_copy`, including
the saved request and phase. A reopened companion can offer **Resume saved
import**, recover that exact request and submit it with current target ownership.
A phase describes persisted progress, not proof that a process is still running.
Never replace saved fields with current UI choices. A status read with a committed
receipt can also finish private-stage cleanup. Finished/rejected stages are
removed on a best-effort basis; small operation manifests remain to prevent ID
reuse. At 1,024 retained copy operations, new imports stop without evicting IDs.
Whole-project duplication and global dependency/usage analysis are separate.

`tests/_asset_copy_test.py` checks grouped audio, derived provenance, a single
authoritative publication, source immutability, missing/damaged media, Source
track conflicts, stale catalogs, preparation/promotion/commit interruption,
restart/retry and concurrent destination changes. The real route tests also
exercise copy preview, replay and ownership rejection.
