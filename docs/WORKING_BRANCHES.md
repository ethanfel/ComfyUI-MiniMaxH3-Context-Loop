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
  to retain its graph, model/reference connections and branch revision binding.
- **Make project default** changes only the preferred-branch pointer.
  **Open project default** explicitly opens that branch. Existing workflows and
  already queued jobs keep their explicit selection.

Branch writes use the existing project ownership check. A conflicting authoring
save is rejected rather than overwriting another workflow's newer snapshot.
External model/reference/policy nodes remain part of the workflow graph; branch
switching does not replace their connections.

### Safe switching and recovery

- Studio checks the saved revision against the snapshot loaded by this workflow.
  Reopening an older workflow does not grant it permission to overwrite newer
  branch settings. If they differ, choose **Reload saved branch** or preserve
  the current settings with **+ Empty branch**. No generated clips are deleted.
- Studio controls are temporarily disabled during a switch. If an external
  prompt/JSON editor changes the Plan while a request is pending, the switch
  stops and leaves those edits in the current view. Widget callback failures
  roll back the branch ID, Plan widgets and connected prompt-editor widgets.
- **Recovery drafts are browser-local**, separate from shared branch saves.
  Studio captures edits and polls external Plan changes every 500 ms, with a
  final capture on page hide. **Restore local draft** explicitly restores a
  draft after reopening; it never silently replaces saved branch settings.
  Drafts remain tied to the browser/workflow node, project and branch. Clearing
  browser storage removes them; an abrupt crash can lose the last uncaptured
  edit. Draft storage/quota errors are shown and do not prevent **Save branch**.
- Saves and branch creation use persistent operation IDs. A lost response can
  be retried without another commit or duplicate branch. If both automatic
  attempts fail, use **Retry pending operation** to reconcile the exact request
  before making another change. A newer intervening save still wins: an old
  retry cannot overwrite it.
- **Refresh branches** rechecks availability without adopting a newer revision
  for stale local settings. Reloading saved settings preserves the current
  local draft where browser storage is available. Local drafts are a recovery
  convenience, not a substitute for saving the branch and workflow.

## Checkpoint Manager: assignment is retained

The **Assignments shown for** dropdown selects the working branch being inspected.
**Original** is the legacy working branch's name; names such as **960x544** are
also labels, not resolution restrictions or exclusive owners of saved clips.
Every working branch can use the same compatible saved path.

To make Original use an existing seven-scene path, choose **Original** in that
dropdown, click the desired **scene 7** take, then **Assign path to Original**
above the graph. The toolbar shows the full scene range being assigned. This
changes that chapter's assignments, clearing any later assignments in the same
chapter, without deleting saved clips or changing other branches, other chapters,
the project default, or the manager's output selection. If the connected Plan
uses another branch, a separate **Assign path to <name> (Plan)** button targets it.
**Load path + settings into Plan** additionally loads saved settings and can
switch the connected Plan's branch; it is not needed just to assign clips.
**Use branch locally** remains an output-only pin.

The revision display is a fork graph: shared clips appear once and arrows follow
the saved paths, including processing histories. Related forks are kept together,
before unrelated root paths/profiles, regardless of the order they were listed.
Editorial ALT cards stay below their exact original checkpoint; **Final cut:
ALT · <revision>** marks the original whose alternate picture is currently used.
The bright **Output path** is
the manager's serialized output selection; a clip preview does not move it.
**Select path** headings choose a whole original path (unless output is pinned),
while processed path headings only preview. **Assigned to <name> through S<n>**
is a separate badge showing where that working branch's assignment ends; it
does not own or block any continuation below it. Click the continuation's final
take and assign it to extend that branch.
Independent-clip **Reuse for S<n>** controls and predecessor/context checks remain
available at eligible branch tips.
Reuse controls sit in the next scene's column with a dashed proposed connector
from their exact parent. They are not saved clips or part of the output path;
opening one only shows candidates, and attachment still requires confirmation.
No control is drawn when there are only blocked candidates: that is not an empty
saved scene or a branch to delete. Delete controls stay separate from assignment,
above their scrollable file inventory; dependency/shared-clip protections remain.
Cards show **Save #**, the saved date and **Latest** per scene among the available
revisions in the tab (across profiles for processed takes). These labels do not
change the fork layout or output selection. Equal timestamps are tied, missing
dates remain unknown, and reused clips are labelled rather than presented as
new renders. Save numbers are relative to the available inventory, not permanent
generation IDs; deleting a take can change them. Legacy dates may come from the
metadata file timestamp when no creation date was saved.
The separate **In Plan Studio** / **In connected Plan** badge marks the saved
path of the connected Plan's working branch. If the Plan is on a different
branch or project, the note above the graph says so rather than marking the
manager's output as active in the Plan. No connection means no guessed marker.

If an editorial ALT belongs to a different base take, a notice explains why it
is not applied to the current path. Polling does not repeatedly log the same
notice. Its saved selection and files are retained for the original base take.

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
