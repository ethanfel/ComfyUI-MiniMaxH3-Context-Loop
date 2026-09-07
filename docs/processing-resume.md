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

## VIDEO → PNG passthrough

Keep the same PNG folder/export name and `reuse_existing = true` when resuming.

Completed PNG scenes are never rewritten. If PNG export finished before
Segment Save and the VIDEO is recreated on retry, a changed container hash is
accepted **only when its delivered RGB pixels match at the selected 8/16-bit
export precision**. This also works for older exports without pixel digests.
Different rendered pixels, changed source branches/settings, and edited or
missing committed PNGs remain protected: they are not silently adopted.

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
deleted. Untracked files from **pre-journal** interrupted exports still require
manual recovery or a different folder; ownership cannot safely be inferred.

These protections do not guarantee survival of a failed disk or a server that
does not honour flush requests. Graceful Cancel remains preferable to killing
ComfyUI. Interrupted sampling still restarts at the beginning of that scene.
