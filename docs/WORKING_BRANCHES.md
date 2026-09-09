# Working branches (nightly)

Plan Studio can keep several working versions of one project. A working branch
owns its clip selection, Plan snapshot, editorial data and processing outputs.
It does not have to become the project default before generation or upscaling.

## Plan Studio

- Use the arrows or branch dropdown to change the Plan and clips shown in Studio.
- **Fork here** retains the selected branch's saved scenes through the current
  scene. The whole authored Plan remains available for continuing or editing.
  The prefix must be present, coherent and in the same scene order.
- **New empty branch** copies the prompts and Plan settings, chapters and timeline
  placements, but starts with no generated clips or processed outputs. Rendered
  trims, locks and alternate selections are cleared. You can keep the seeds or
  request fresh seeds.
- **Save branch** saves an authoring snapshot. Switching, forking and changing
  the default also save the current snapshot first. Save your workflow normally
  to retain its graph, model/reference connections and unswitched edits.
- **Make project default** changes only the preferred-branch pointer.
  **Open project default** explicitly opens that branch. Existing workflows and
  already queued jobs keep their explicit selection.

Branch writes use the existing project ownership check. A conflicting authoring
save is rejected rather than overwriting another workflow's newer snapshot.
External model/reference/policy nodes remain part of the workflow graph; branch
switching does not replace their connections.

## Checkpoint Manager: assignment is retained

Choose a working branch in the manager's new dropdown. The existing saved take
inventory, independent-clip **assign/reuse** controls and predecessor/context
checks remain available. **Assign to working branch** changes only that branch's
selection. It does not promote it to project default or change another branch.
**Use branch locally** remains an output-only pin.

Old revision histories are still available in Checkpoint Manager. They are not
automatically converted into named working branches. To continue one separately,
create a working branch and assign the desired saved lineage to it.

## Storage and recovery

The **Original** working branch uses the existing project paths. New branches
use `h3_chains/<run>/branches/<id>/` for small mutable records and new processing
outputs. Existing UUID-addressed videos, latents, reference caches and immutable
recovery archives remain shared. Creating a branch does not copy those media.
An empty branch never falls back to Original's active clip pointers.

Scenes are still committed individually. Cancelling during a later scene keeps
the completed scenes on that working branch. Resume with the same branch and
the first unfinished scene. Top-level requeue verifies the branch as well as
the predecessor; switching Studio to another branch stops automatic continuation
instead of sending it there.

Deletion protects revisions selected by another retained working branch,
including its editorial selections and sealed chapter snapshots. Processing
cleanup uses exact saved addresses, including branch-local PNG exports.

Existing projects/workflows need no migration. Named working-branch workflows
require this nightly implementation: do not run them in an older build that
does not understand branch identity. Main is unchanged by this nightly feature.
