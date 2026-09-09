# Cancelling and resuming DeRoPE/upscale

Deferred DeRoPE, latent upscale, and pixel/VIDEO upscale save **one scene at a
time**. If scenes 1 and 2 have finished saving, cancelling while scene 3 is
processing keeps those scenes and the original generation checkpoints.

To continue, select the same source branch and chapter/output scope, keep the
same output profile and processing settings, and set **Upscale Adapter →
start_clip = 3**. Resume reads scenes 1–2 from their saved checkpoints, including
the compact latent context required by Drift-Control. ComfyUI does not need to
retain the previous execution in memory. `start_clip = 1` deliberately starts
again; it is not an automatic skip-completed switch.

There is no sampler-step or tile checkpoint inside an unfinished scene. Its
processing must run again. Enabling `save_latent` saves the **finished** full
latent; it does not enable mid-sampling resume. Full recovered latent saving
must be enabled when DeRoPE outputs will later serve as deferred latent sources.

## Saving and storage failures

- Scene media is flushed before publishing its immutable revision and current
  checkpoint pointer. Once publication starts, an uncertain write or lost
  network acknowledgement never triggers deletion of possibly committed media.
- A failed manifest refresh cannot remove the saved scene. Resume uses the
  individual checkpoint records; the manifest is rebuilt by later loop saves.
- If a current-pointer update itself fails, the previous current take remains
  intact and any newly published immutable take is retained. Check the saved
  versions before deciding whether to rerun the scene.

## Reviewing saved processing branches

Checkpoint Manager's DeRoPE, Latent Upscale, and Pixel Upscale tabs display one
row per saved processing branch, like Original. Rows follow the exact recorded
scene/take history, not a combination of each scene's newest version. Shared
prefix clips have matching colors and `shared ×N` badges. Creation dates are
shown on the cards; rows are ordered by their newest visible save, with a
`Latest save` label (ties are marked together). This is not an active-output
indicator or a guarantee that every planned scene has been processed.

Chapter tabs restrict the displayed history to that chapter. Identical chapter
prefixes are shown once even when later chapters fork. Deleted or unavailable
takes remain explicit gaps in surviving histories; another version is never
silently substituted. Older takes without saved lineage appear as standalone
takes marked `Branch history unavailable`.

Clicking a processed card or branch heading only changes the preview. It does
not activate an Original branch or change the downstream output selection.
The existing explicit `Use DeRoPE branch locally` action retains its integrity
and unambiguous-lineage checks.

## VIDEO → PNG passthrough

Keep the same PNG folder/export name and `reuse_existing = true` when resuming.

Completed PNG scenes are never rewritten. If PNG export finished before
Segment Save and the VIDEO is recreated on retry, a changed container hash is
accepted **only when its delivered RGB pixels match at the selected 8/16-bit
export precision**. This also works for older exports without pixel digests.
Different rendered pixels, changed source branches/settings, and edited or
missing committed PNGs remain protected: they are not silently adopted.
Instead, VIDEO export automatically selects a numbered sibling such as
`final_upscale_2`, then `final_upscale_3`. The `output_directory` output and
status report the actual destination. Setting `reuse_existing = false` forces
a fresh sequence; it does not create a separate folder for every scene.

The chosen folder is bound to the upscale pass on disk, so recursive scenes
and retries stay together. After a restart, a new pass considers the newest
exporter-created variant. Identical saved pixels still reuse it normally.
If a changed render forks midway through an otherwise verified sequence,
earlier unchanged scenes are copied into the new folder with bounded memory,
preserving continuous numbering. Those copies are independent files, so edits
to the older sequence cannot change them. Edited/missing prefix frames are
not copied; that fresh variant begins with the scene being exported.

This also applies when the **original source revisions** change: rerendering
only scenes 6–7 copies the verified unchanged scenes 1–5 into `_2`, then writes
the new 6–7 using continuous frame numbers (including `first_frame_number`).
The earlier folder is untouched. If an earlier source scene, selected upscale
take, or prefix PNG changed, that prefix is not silently reused. Changing frame
counts naturally shifts subsequent frame numbers in the new sequence.

New VIDEO exports and upscale saves record their shared per-scene/pass owner.
Checkpoint Manager's deletion preview includes the take's owned PNG frames,
including independent copies in numbered variants and registered custom output
folders. Confirming deletion removes those frames, including hand-edited frames
in that owned range. Other scenes keep their files and original frame numbers;
the affected export index is marked incomplete. Later exports use a new variant
instead of replaying deleted scenes from a saved prefix recipe. Identical PNGs
shared with another owner/take remain until the last owner is removed.

Legacy exports/takes without exact ownership are kept and reported rather than
matched by scene number, filename, or source revision alone. Assembled videos
and unrelated/untracked files are still kept. No folders are recursively deleted.

PNG publication writes a `.png_pending.json` journal after staging a complete
scene. Normal cancellation rolls back that attempt when it can safely do so.
After a process exit or unreachable share, the next export recovers that
journal before appending scenes. It verifies existing published frames and
finishes publishing missing frames from staging. No whole-scene image batch
is loaded into RAM.

A killed network copy can leave an incomplete file. Recovery preserves
conflicting bytes as `conflict_*` files in the private `.png_scene_*` directory,
logs the location, and publishes the verified staged frame. Earlier scenes and
untracked files outside the journal's frame range are never overwritten or
deleted. Untracked files from **pre-journal** interrupted exports are left
untouched and a new numbered export is used. Invalid journals, unsafe paths,
and genuine filesystem errors still require attention; numbering does not
bypass those checks.

These protections do not guarantee survival of a failed disk or a server that
does not honour flush requests. Graceful Cancel remains preferable to killing
ComfyUI. Interrupted sampling still restarts at the beginning of that scene.
