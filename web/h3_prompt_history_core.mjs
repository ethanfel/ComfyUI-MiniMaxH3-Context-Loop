function chronologicalPromptRevisions(history) {
    return [...(history?.revisions ?? [])].sort((left, right) => {
        const time = String(left.created_at ?? "").localeCompare(
            String(right.created_at ?? ""),
        );
        return time || String(left.id ?? "").localeCompare(String(right.id ?? ""));
    });
}

export function promptRevisionTree(history, {includeArchived = false} = {}) {
    const revisions = chronologicalPromptRevisions(history);
    const byId = new Map(revisions.map((revision) => [revision.id, revision]));
    const children = new Map(revisions.map((revision) => [revision.id, []]));
    const roots = [];
    for (const revision of revisions) {
        if (revision.parent_id && byId.has(revision.parent_id)) {
            children.get(revision.parent_id).push(revision);
        } else {
            roots.push(revision);
        }
    }

    const activeRevision = String(history?.active_revision ?? "");
    const rows = [];
    const visited = new Set();
    const visit = (revision, depth) => {
        if (!revision || visited.has(revision.id)) return;
        visited.add(revision.id);
        const isArchived = Boolean(revision.archived_at);
        const visible = includeArchived || !isArchived;
        const childRevisions = children.get(revision.id) ?? [];
        if (visible) {
            rows.push({
                revision,
                depth,
                isActive: revision.id === activeRevision,
                isExecuted: Boolean(revision.executed_at),
                isArchived,
                hasDescendants: childRevisions.length > 0,
                canDelete: revision.id !== activeRevision
                    && !revision.executed_at
                    && childRevisions.length === 0,
                canArchive: revision.id !== activeRevision && !isArchived,
                canRestore: isArchived,
            });
        }
        for (const child of childRevisions) {
            visit(child, depth + (visible ? 1 : 0));
        }
    };
    for (const root of roots) visit(root, 0);
    // Corrupt/cyclic ancestry should not make history disappear from the UI.
    for (const revision of revisions) visit(revision, 0);
    rows.forEach((row, index) => {
        row.position = index + 1;
        row.displayLabel = String(row.revision.label ?? "").trim()
            || `Revision ${row.position}`;
    });
    return {
        rows,
        revisions: rows.map((row) => row.revision),
        archivedCount: revisions.filter((revision) => revision.archived_at).length,
    };
}

export function orderedPromptRevisions(history, options = {}) {
    return promptRevisionTree(history, options).revisions;
}

export function promptRevisionNavigation(history, revisionId = null) {
    const requested = String(revisionId ?? history?.active_revision ?? "");
    const requestedRevision = (history?.revisions ?? []).find(
        (item) => item.id === requested,
    );
    const revisions = orderedPromptRevisions(history, {
        includeArchived: Boolean(requestedRevision?.archived_at),
    });
    let index = revisions.findIndex((item) => item.id === requested);
    if (index < 0 && revisions.length) index = revisions.length - 1;
    const revision = index < 0 ? null : revisions[index];
    const parentIndex = revision?.parent_id == null
        ? -1 : revisions.findIndex((item) => item.id === revision.parent_id);
    const activeRevision = String(history?.active_revision ?? "");
    const latestExecutedRevision = String(
        history?.latest_executed_revision
        ?? [...revisions].filter((item) => item.executed_at).sort((left, right) =>
            String(left.last_executed_at ?? left.executed_at ?? "").localeCompare(
                String(right.last_executed_at ?? right.executed_at ?? ""),
            )).at(-1)?.id
        ?? "",
    );
    return {
        revisions,
        revision,
        index,
        position: index < 0 ? 0 : index + 1,
        total: revisions.length,
        previous: index > 0 ? revisions[index - 1] : null,
        next: index >= 0 && index < revisions.length - 1 ? revisions[index + 1] : null,
        parentPosition: parentIndex < 0 ? null : parentIndex + 1,
        activeRevision,
        latestExecutedRevision,
        isActive: Boolean(revision && revision.id === activeRevision),
        isExecuted: Boolean(revision?.executed_at),
        isImmutable: Boolean(revision?.executed_at),
        isLatestExecuted: Boolean(
            revision && revision.id === latestExecutedRevision),
    };
}

export function promptRevisionLabel(navigation, locale = undefined) {
    const revision = navigation?.revision;
    if (!revision) return "No saved versions";
    const timestamp = revision.executed_at ?? revision.updated_at ?? revision.created_at;
    const date = timestamp ? new Date(timestamp) : null;
    const time = date && Number.isFinite(date.getTime())
        ? date.toLocaleString(locale, {dateStyle: "medium", timeStyle: "short"})
        : "Unknown time";
    const state = navigation.isActive
        ? (navigation.isExecuted ? "Active executed" : "Active draft")
        : (navigation.isExecuted ? "Executed history" : "Draft history");
    const customLabel = String(revision.label ?? "").trim();
    const namedState = customLabel ? `${customLabel} · ${state}` : state;
    const branch = navigation.parentPosition == null
        ? "" : ` · branched from ${navigation.parentPosition}`;
    const repeats = Number(revision.execution_count) > 1
        ? ` · executed ${revision.execution_count}×` : "";
    return `${namedState} · ${time}${branch}${repeats}`;
}

export function promptRevisionHelp(navigation) {
    if (!navigation?.revision) return "No prompt revision exists yet.";
    if (navigation.isActive && navigation.isImmutable) {
        return "This executed revision is active in the Plan and immutable. Typing creates a child draft.";
    }
    if (navigation.isActive) {
        return "This draft is active in the Plan. Typing updates this draft until it executes.";
    }
    return navigation.isImmutable
        ? "This is immutable executed history. Activate it to restore it, or archive it to hide it."
        : "This is an inactive draft. Activate, label, archive, or delete it if it has no descendants.";
}
