# Reviewed image variants

Public project catalogs advertise `library_image_version: 1`. Read the oriented
source geometry using `GET /minimax_h3_context_loop/project-assets?project=<run>&create=false&image_asset=<id>`.
The response includes exact project/asset identity, the native storage revision,
oriented source dimensions, supported resampling modes and the maximum output
pixel count. It does not create or alter a catalog. Non-picture and unavailable
sources are rejected.

Add URL-encoded `image_edit=<JSON>` to review a variant. Its fields are:

- `crop`: whole-pixel `x`, `y`, `width`, `height` in the EXIF-oriented source.
- `target`: whole-pixel `width`, `height`.
- `resample`: a supported native filter.
- `tag`: requested variant tag; native registration chooses a unique tag.
- `folder_id`: an existing destination folder, or an empty string for unfiled.

Review uses the same geometry and resampling validation as
`ProjectAssetStore.derive_image`. It returns normalized geometry/tag/filter,
`base_revision`, `preview_revision`, and catalog-capacity eligibility. The
review fingerprint covers the source entry, catalog storage stamp, media
identity and edit intent. A caller must retain its reviewed fields.

Submit those fields to `POST /minimax_h3_context_loop/project-assets/library`
with `action: "asset_derive"`, `command_version: 1`, the project/asset identities,
a new 32-hex `operation_id`, and both reviewed revisions. Native target ownership
is mandatory. Fractional/invalid coordinates, out-of-bounds crops, oversized
targets, missing folders and unsupported filters fail before publication.

The command freezes and hash-checks source bytes, then invokes the existing
native `derive_image` and `register_derived_image` methods in a private staging
store. The native crop, EXIF orientation, transparency, resize, role, options,
tag and transform rules remain authoritative. Source provenance is retained;
the new enabled image keeps its local `parent_asset_id` and native `transform`.
The source catalog entry, original bytes and workflow Plan are not edited.

The shared staged-media mechanism promotes verified output media and publishes
the complete variant plus its receipt in one authoritative catalog commit.
It inherits the copy command's file protection, storage CAS, ownership,
interruption, exact-replay and primary/mirror boundaries. A retained prepared
variant can finish from frozen bytes after restart; a changed destination
requires a fresh review. No upscale model or GPU queue is used.

`library_pending_operations` lists staged copies and image variants, including
their action, root asset ID, operation ID and persisted phase.
`library_pending_copies` remains limited to cross-project copies for older
clients. Operation-status reads include `pending_operation` with the exact
saved request; `pending_copy` remains a compatibility field for copies only.
A reopened companion can explicitly resume the original media request with
current project ownership. A phase is not proof that a worker is running.
Committed status reads also clean leftover private staging on a best-effort
basis. Admission is serialized and rejects before allocating a new directory
at 1,024 retained media operations. Existing receipts remain replayable.

`tests/_asset_image_test.py` verifies actual selected pixels, alpha, oriented
geometry, native provenance, one catalog commit, unchanged originals, invalid
and stale reviews, interruption/restart/replay and the admission limit.
`tests/_asset_library_api_test.py` exercises real image-inspect/save/replay routes
and real ownership rejection with temporary CPU-only fixtures. Existing copy,
library and native-store checks remain applicable.
