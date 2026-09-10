# Recovering branch prompts and settings

Older working branches could contain assigned videos from one generation while
their editable snapshot still contained another generation's prompts, seeds or
resolution. Assigning a path without a connected Plan did not update that snapshot.

After updating and restarting ComfyUI, reload the browser, then use **Reload saved
branch** in Plan Studio. Older snapshots are recovered from the coherent assigned
checkpoints. Switch to each affected branch to load its recovered settings. If a
stale-tab warning blocks switching, reload the current saved branch first (or use
the offered **Open saved…** action, which keeps local edits in browser recovery).

Recovery restores the assigned scenes' prompts, exact uint64 seeds, frame lengths,
steps, context settings and chapter resolutions. Ungenerated scenes and notes are
retained. Connected chain-policy settings are restored too; a policy that cannot
represent the saved settings stops the switch and rolls back its widget changes.
This is not a restore of external model loaders or the entire workflow.

Loading recovery does not rewrite saved project JSON or any media. **Save branch**
persists the recovered authoring, retaining the previous snapshot under
`branches/authoring_backups/<branch_id>/`. Local unsaved edits remain in browser
recovery. Review those edits before restoring them over the recovered settings.

New assignments invalidate the corresponding authoring snapshot, including when
no Plan is connected. Old tabs cannot save over that assignment without reloading.
After saving, intentional new prompt/seed/settings edits remain authoritative;
ordinary rendering and branch switching do not continually reset them to old takes.
