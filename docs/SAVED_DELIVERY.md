# Saved delivery snapshots

The delivery interface captures a saved checkpoint lineage, its selected final cut,
picture alternates and subtitle cues for later assembly. A subsequent editorial
edit or lyric catalog change does not change the prepared job. Preparation reads
the project without activating checkpoints, restoring a Plan or claiming ownership.
Subtitle catalogs are read without filesystem recovery: a missing or corrupt primary
can use its valid backup without restoring files during preparation.

Connect **MiniMax H3 Saved Delivery Source** to the manifest input of **H3 Chain
Assemble**. Paste the exact `snapshot_json` returned by the API into the source
node. Assemble retains its existing audio, blend, color and output controls. This
path reads saved media; it does not generate new scenes. Blending can still require
the connected VAE. Ordinary Checkpoint Manager and Assemble behavior is unchanged
when no delivery snapshot is present.

## Prepare a source

`POST /minimax_h3_context_loop/delivery/prepare`, JSON body:

```json
{
  "selection": {
    "run_name": "my_film",
    "_branch_id": "main",
    "output_mode": "workflow_local",
    "final_cut_branch_id": "main",
    "lineage": [{ "scene": 1, "revision": "0123456789abcdef0123456789abcdef" }]
  },
  "editorial_revision": "the revision from the reviewed checkpoint inventory"
}
```

Use the existing native `checkpointLocalSelectionJson` helper to produce an exact
saved lineage. A named destination branch and the final-cut source branch are
separate identities; both must be explicit. `final_cut_branch_id: "auto"` and
legacy/adopting checkpoint selections are rejected. Native chapter pins also
accept `output_scope: "chapter"`, `scope_start_scene` and `scope_end_scene`.

A successful response contains `version: 1`, a SHA-256 `snapshot_id`, the opaque
`snapshot_json` string, and a `summary` with project/branch/cut identity, editorial
revision, scene range, duration in frames, fps, cue count and picture revisions.
The response is not cached. A changed editorial revision returns HTTP 409; invalid
selections and unavailable media return HTTP 400. Media hashing can take time.

Preserve `snapshot_json` as a string. Parsing and reserializing it in JavaScript can
round native uint64 seeds and invalidate its digest. The source node verifies the
digest, branch existence and captured media hashes again at execution. Missing or
changed source bytes stop the job; it does not substitute today's active take.
The snapshot is a source description, not an authorization token or a media bundle.

## Native browser adapter

`web/h3_delivery_core.mjs` exports `DELIVERY_VERSION = 1`,
`prepareDelivery(api, selection, editorialRevision)` and
`deliveryPrompt(serialized, target, snapshotJson, settings)`. The checkpoint entry
module re-exports them for discovery. SceneWeaver discovers this interface from
the installed H3 pack; merely updating the companion does not install it.

Run native before-queue hooks and ComfyUI's `graphToPrompt` first. The recipe helper
retains full workflow metadata, replaces only the chosen Assemble manifest input
with a saved source, and keeps its serialized ancillary dependencies. Queue its
returned target with native partial execution. It never changes the live graph.

Supported ancillary producers are `VAELoader`, `LoadAudio`, `VHS_LoadAudioUpload`,
`MiniMaxH3AudioTracks`, `MiniMaxH3SourceTimeline`, `MiniMaxH3ProjectAssetManager`,
`LoadVideo`, `VHS_LoadVideo` and `VHS_LoadVideoFFmpeg`. Unknown dependencies are
rejected rather than silently omitted. Connected scalar overrides and
`overwrite_existing` are rejected. Processed/upscale sources need a separate
adapter and are not supported by this snapshot version.

## Output and recovery

Normal native MP4, audio and subtitle outputs remain available. Frozen assembly
also writes `<video-stem>.delivery.json` alongside the project video. It records
the exact source snapshot, scalar assembly settings, video path, SHA-256 and frame
count. Connected audio/VAE producers remain in the native queued workflow and
history; the record alone is not a portable reconstruction of those dependencies.
The record is not currently copied with `copy_to_output` or exposed as a grouped
download by SceneWeaver. Preserve the referenced source media for reassembly.

## Validation

Run `python3 tests/_delivery_snapshot_unit_test.py` with Torch, safetensors and
FFmpeg installed, and `node --test tests/_delivery_snapshot_js_test.mjs`. The Python
case uses native H3 functions, temporary synthetic projects and real CPU FFmpeg
assembly. It checks a 48-frame video with audio, frozen ALT pixels and SRT text,
chapter subtitle offsets, separate branch identities, stale revision rejection,
source corruption and preparation without project writes, including a real
mirror-only subtitle catalog. `python3 tests/_catalog_readonly_unit_test.py` also
checks corrupt primary catalogs and unchanged ordinary recovery. Its async route check
requires working local socket notifications. The JS cases validate dependency
preservation and rejection of unsupported sources. These checks do not establish
GPU generation or production workflow parity.
