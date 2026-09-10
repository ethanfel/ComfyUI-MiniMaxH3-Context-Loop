# Saved sequence editing interface

Companions can inspect, preview and save H3 editorial trims, placements, timing
locks and subtitle settings through `POST /minimax_h3_context_loop/editorial/command`.
The native browser module `h3_editorial_commands.mjs` exports
`EDITORIAL_COMMAND_VERSION = 1` and `editorialCommand(api, body, options)`; Plan
Studio re-exports it for discovery. Existing Studio saves remain compatible.

All bodies identify `run_name` and `branch_id` (`main` for Original). The response
echoes both identities and `version: 1`. This interface edits saved editorial
state. It does not change the authored Plan, active checkpoint pointers, immutable
take files or generation order, and it does not queue rendering.

## Inspect, preview, apply

1. Send `{"action":"inspect","run_name":"film","branch_id":"main"}`.
   The response supplies a source `stamp`, saved scenes with exact revision IDs,
   native `safe_out_frames`, current trim/placement/lock values, subtitle settings,
   timed audio-asset choices and resolved sequence timing. Existing invalid
   timeline/caption settings are reported in `timeline.error` so they can be fixed.
2. Send `action: "preview"` with that `stamp` and one `patch`. For example:

   ```json
   {
     "scene": {
       "scene": 1,
       "scene_id": "arrival",
       "revision": "0123456789abcdef0123456789abcdef",
       "out_frame": 48,
       "start_frame": 120
     }
   }
   ```

   The response contains a `preview_token`, the patch, and a native timeline:
   resolved scene/gap records, frame count, caption cues and direct/transitive
   continuation mismatches. Review this response before writing.
3. Send `action: "apply"` with the same identities, `stamp`, exact `patch` and
   `preview_token`, using the normal native project ownership headers. Apply
   rechecks source state under the checkpoint lock before the conditional native
   editorial save. Its response contains the saved editorial revision and timing.

Inspection and preview do not claim ownership, create checkpoint locks or repair
catalogs. A missing/corrupt primary catalog can be read from its valid backup
without restoring files. Apply uses native project ownership; a preview token is
not authorization. A changed cut, checkpoint metadata, asset catalog or patch
requires another review (HTTP 409). Ownership failures return 423; invalid input
returns 400. Responses are not cached. A failed/lost response after a write must
be reconciled by inspecting current state, not automatically resubmitted.

## Supported changes

| Patch | Meaning |
| --- | --- |
| `scene.out_frame` | Retained delivered prefix, using a native valid video/audio cut point; `null` or full delivered length removes the trim. No in-trim is introduced. |
| `scene.start_frame` | Explicit requested frame, 0–864000; `null` restores natural placement. H3 resolves ordering and pushes overlaps forward, retaining gaps. |
| `scene.locked` | Boolean timing lock. A locked scene must be unlocked in a separate saved edit before changing its trim or placement. |
| `subtitles` | One settings patch containing `mode` (`off` / `preview_srt`), `asset_id` and/or `offset_seconds` (-3600 to 3600). Uses the native timed-lyrics parser. |

Each request edits either one exact saved scene or subtitle settings. Other
editorial fields, including chapters, final-cut replacements and ALT drafts, pass
through native validation unchanged. Audio waveform monitoring, soundtrack/Plan
routing, incoming blend settings and chapter authoring use their existing separate
interfaces; they are not added to this command.

A shortened endpoint can invalidate saved continuations downstream. Preview
reports these scenes using H3's native dependency analysis; the user may save the
edit and regenerate from the first affected scene. Native assembly continues to
reject stale continuations. The diagnostic timeline alone is not evidence that
the saved sequence is currently assemblable. No checkpoint is deleted or restored
by accepting an edit.

## Validation

`python3 tests/_editorial_commands_unit_test.py` exercises real native validation,
ownership, named-branch isolation, read-only catalog recovery, locks, captions,
checkpoint/catalog conflicts and direct/transitive continuation impact using
temporary synthetic project metadata. Its async route checks need working local
socket notifications.

`python3 tests/_editorial_commands_ffmpeg_test.py` additionally uses Torch,
safetensors and CPU FFmpeg to assemble real synthetic media. It compares native
preview records with a 168-frame output containing a trimmed ALT picture, a black
gap, the base take's generated audio, silence during the gap, and shifted SRT cues.
It does not execute GPU generation or validate a production workflow.
