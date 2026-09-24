# Reviewed saved-frame captures

Catalogs advertise `library_capture_version: 1`. This command saves one picture
from an existing H3 scene revision. It uses native FFmpeg frame extraction and
native picture import/tag-numbering rules. It never queues a workflow or model.

Read `GET /minimax_h3_context_loop/project-assets?project=<run>&create=false&capture_frame=<JSON>`.
The URL-encoded selection contains:

```json
{
  "source": {
    "scene": 2,
    "revision": "0123456789abcdef0123456789abcdef",
    "branch_id": "main",
    "file": {"filename": "clip.webm", "subfolder": "h3_chains/film/segments", "type": "output"}
  },
  "time_seconds": 1.25,
  "tag": "arrival_capture",
  "folder_id": ""
}
```

Use the displayed video's local `currentTime`, not its sequence placement or
the Plan selection cursor. Supply the displayed picture revision: an ALT uses
its own revision and video even when audio comes from the base take. The viewer
must reject seeking, unloaded or failed media, gaps, and unsaved sources.

Review verifies immutable checkpoint metadata, exact raw-video or native review
preview membership, branch, output confinement, media and metadata SHA-256,
finite in-duration time, destination folder and catalog capacity. It returns
`base_revision`, `preview_revision`, normalized tag and eligibility. No catalog
or preview asset is created. Reading hashes can take time for large files.

POST the selection and both reviewed revisions to the existing `/project-assets/library`
route with `action: "asset_capture"`, `command_version: 1`, `project`, and a new
32-hex `operation_id`. The source is a saved clip; `asset_id` is not required.
The caller supplies the normal native ownership headers. The original
`/project-assets/capture-frame` endpoint remains compatible with native clients.

The operation checks source identity before and after native extraction, then
imports into a private store. Native numbered tags and destination folder are
retained. The enabled picture, `source_origin` (project, scene, revision, branch,
media identity, clip time and hashes), transform/operation ID and receipt are
published in one authoritative catalog commit. Captions/monitor overlays are
not baked into the picture. The source clip, checkpoint, cut and Plan are unchanged.

All reviewed staged media commands use the same lock order: operation lock,
then a short ownership guard, then catalog commit. Ownership is checked at
admission and again around media promotion/publication. Extraction and copy
preparation do not hold the ownership lock; a takeover can proceed and fences
publication. Different actions cannot invert these locks on a reused operation
ID. Source/catalog changes reject publication and remove only this operation's
unreferenced media. Extraction failure is a rejected operation.

Prepared images and exact requests survive restart in the shared bounded
staging mechanism. `library_pending_operations` and the operation-status read
expose `asset_capture`; a prepared capture can publish after source removal
without extracting again. A changed destination still requires a new review.
An I/O failure returns 500 because its catalog/receipt may already have committed.
Check that exact operation before retrying; receipt replay never creates another
picture. Existing copy/variant limits and primary/mirror guarantees apply.

`tests/_asset_capture_commands_test.py` uses real temporary two-color videos and
CPU stubs to check requested-time pixels, saved/review identities, provenance,
numbered tags, source/metadata/catalog changes, ownership takeover, single commit,
interruption/restart, lost acknowledgement, exact replay and real route admission.
