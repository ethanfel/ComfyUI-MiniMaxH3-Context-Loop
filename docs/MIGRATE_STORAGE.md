# Migrate a chain to organized storage

Run this from this node pack's directory, using ComfyUI's Python environment.
Stop ComfyUI first. Keep the original chain and your existing backup.

```bash
python tools/migrate_storage.py migrate \
  --source /media/unraid/comfyui/output/h3_chains/silver_estate_final_semantic \
  --workspace /media/unraid/comfyui/h3-migrate/silver
```

The workspace must be a **new, short directory outside the old output directory**.
Allow space for a full independent copy plus metadata. There are no hard links
to the source media. The command inventories, copies, verifies every imported
file, and enables the independent destination. It does **not** move, delete,
rewrite or activate the source project.

For a separate preview before copying:

```bash
python tools/migrate_storage.py preview --source /path/to/output/h3_chains/project --workspace /path/to/new-workspace
python tools/migrate_storage.py copy --workspace /path/to/new-workspace
python tools/migrate_storage.py verify --workspace /path/to/new-workspace
python tools/migrate_storage.py activate --workspace /path/to/new-workspace
```

If payload copying is interrupted, repeat `copy`, then `verify` and `activate`.
Do not remove the migration journal. Source changes or conflicting destination
files stop the operation; they are never silently overwritten. If initial
control-file preparation fails before a copy journal exists, retain that
workspace for inspection and choose a new one.

## Use the migrated project

After the command succeeds, start ComfyUI with this updated node pack and change
only its output argument to:

```text
--output-directory /media/unraid/comfyui/h3-migrate/silver/output
```

Keep the same base/input/model directories. Reload the browser, open your
workflow and select the same project and working branch. Load the saved branch
before queueing. Existing prompts, seeds, resolution, scene identities, ALT
choices and processing profiles are not rewritten by migration. Ownership is
preserved; an unauthorized workflow still needs the normal ownership action.

Normal nodes and the Checkpoint Manager detect the explicitly enabled project.
No test-host setup or workflow graph rewrite is needed. Queue a **fresh** job;
an execution, browser edit or automatic handoff created before migration is not
a valid new-storage job. Resume from the saved checkpoint instead.

This output directory contains the migrated project, not every other project
from the old output directory. The original output remains available separately.
Do not mix a partially copied project into it or manually edit `storage.json`.

## Resulting layout

```text
silver_estate_final_semantic/
  storage.json
  media/          # generation, ALT, de-rope, latent/pixel upscale, video refine
  exports/        # PNG sequences, finished videos and standalone audio
  project/        # plans, branches, references, profiles, history and recovery
    optional/     # disposable supporting files, created only when needed
```

Branch/chapter/profile labels no longer make media paths progressively deeper.
Saved metadata retains its original logical addresses; the accepted index maps
them to the new physical files. Old unknown files are preserved in `project/legacy`.
Existing V1/V2 reference bundles remain readable; migration does not require
running the separate legacy reference-cache conversion tool. Rebuilt disposable
conditioning uses the existing shared `h3_reference_cache` outside the project.
Third-party saver nodes still own their own output paths.

## Switch back safely

Stop ComfyUI and restore its previous `--output-directory` argument. The original
chain has not been changed. Keep the migrated workspace: work created there
after migration belongs to that copy and is **not** automatically copied back
to the original. Switching output directories is not a merge.

Validation details and platform limits are recorded in
[the completion checklist](STORAGE_COMPLETION_CHECKLIST.md).
