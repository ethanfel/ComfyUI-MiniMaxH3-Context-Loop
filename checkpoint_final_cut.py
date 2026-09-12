"""Resolve a manager's final cut independently of its assignment/output folder.

Saved media are shared between working branches. A local video-path selection
must not silently pick the editorial document of an unrelated assignment view.
"""

import os

from .branch_scope import branch_id, branch_scope


def final_cut_contexts(chain, run, *, include_editorial=False):
    """Read-only, lightweight branch assignments and saved picture choices."""
    store = chain.WorkingBranches(chain._output_root(), run)
    manager = chain.CheckpointGraphManager(chain._output_root())
    contexts = []
    for record in store.listing()["branches"]:
        selected = record["id"]
        with branch_scope(run, selected):
            # Do not infer through an interrupted pointer transaction. Avoid
            # the mutation lock here: it creates files even for a local read.
            store._pointers(selected)
            active, _stale = manager.active_selection(run)
            path = chain._run_editorial_path(run)
            # Invalid saved cuts must fail, never masquerade as Original.
            editorial = (chain._normalize_run_editorial(chain._read_json(path), run)
                         if os.path.isfile(path) else chain._load_run_editorial(run))
        contexts.append({
            "id": selected, "name": record["name"],
            "lineage": [{"scene": scene, "revision": revision}
                        for scene, revision in sorted(active.items())],
            "replacements": editorial.get("replacements", []),
            **({"_editorial": editorial} if include_editorial else {}),
        })
    return contexts


def resolve_final_cut_context(selection, contexts, current="main"):
    """Prefer a matching current branch, otherwise require a unique match.

    Explicit choices also allow an unassigned historical path to use a retained
    cut. No match preserves the existing assignment-branch behaviour; more than
    one alternative match is ambiguous even if their pictures happen to agree.
    """
    requested = selection.get("final_cut_branch_id", "auto")
    if not isinstance(requested, str) or not requested:
        raise ValueError("Invalid final-cut branch choice. Choose Final cut from in Checkpoint Manager.")
    if requested != "auto":
        requested = branch_id(requested)
    by_id = {item["id"]: item for item in contexts}
    if requested != "auto":
        if requested not in by_id:
            raise ValueError("Selected final-cut branch is unavailable. Choose Final cut from in Checkpoint Manager.")
        return by_id[requested]
    if current not in by_id:
        raise ValueError("Checkpoint Manager's working branch is unavailable.")
    first = (int(selection.get("scope_start_scene", 1))
             if selection.get("output_scope") == "chapter" else 1)
    wanted = {int(item["scene"]): str(item["revision"]).lower()
              for item in selection.get("lineage", [])
              if int(item["scene"]) >= first}
    matches = []
    if wanted:
        for context in contexts:
            assigned = {int(item["scene"]): str(item["revision"]).lower()
                        for item in context["lineage"]}
            if all(assigned.get(scene) == revision for scene, revision in wanted.items()):
                matches.append(context)
    if any(item["id"] == current for item in matches):
        return by_id[current]
    if len(matches) > 1:
        raise ValueError("This saved path matches multiple final-cut branches (%s). "
                         "Choose Final cut from in Checkpoint Manager before upscaling."
                         % ", ".join(item["name"] for item in matches))
    return matches[0] if matches else by_id[current]


def selection_editorial(chain, run, selection, current):
    """Resolve the cut from one read-only inventory (no saved-data writes)."""
    context = resolve_final_cut_context(
        selection, final_cut_contexts(chain, run, include_editorial=True), current)
    return context["_editorial"], context["id"]
