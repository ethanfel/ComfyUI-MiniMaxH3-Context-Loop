export function formatCheckpointBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export function checkpointRevisionKey(scene, revision) {
    return `${Number(scene)}:${String(revision ?? "").toLowerCase()}`;
}

export const CHECKPOINT_STAGES = [
    {id:"original", label:"Original"},
    {id:"derope", label:"DeRoPE"},
    {id:"latent_upscale", label:"Latent Upscale"},
    {id:"pixel_upscale", label:"Pixel Upscale"},
    {id:"other", label:"Other processing"},
];

export function checkpointStageVariants(payload, stage, original = null, range = null) {
    const key = original ? checkpointRevisionKey(original.scene, original.revision) : null;
    return (payload?.processing_variants ?? []).filter((item) => item.stage === stage
        && (!range || (Number(item.scene) >= range.start && Number(item.scene) <= range.end))
        && (!key || (item.originals ?? []).some((source) =>
            checkpointRevisionKey(source.scene, source.revision) === key)));
}

export function checkpointVariantLatentStatus(record) {
    if (!record?.latent_saved) return record?.context_steps > 0
        ? "Continuation tail only — full latent not saved"
        : "Not saved — preview/assembly only";
    if (!record.ready) return "Saved latent unavailable — missing artifacts";
    return `Full latent saved (${record.latent_layout || "unknown layout"}); not yet execution-validated`;
}

export function checkpointRevisionMap(payload) {
    return new Map((payload?.revisions ?? []).map((item) => [
        checkpointRevisionKey(item.scene, item.revision), item,
    ]));
}

export function selectedCheckpointRevision(payload, scene = null, revision = "") {
    const revisions = Array.isArray(payload?.revisions) ? payload.revisions : [];
    const wantedScene = Number(scene);
    const wantedRevision = String(revision ?? "").toLowerCase();
    if (Number.isInteger(wantedScene) && wantedRevision) {
        const exact = revisions.find((item) =>
            Number(item.scene) === wantedScene &&
            String(item.revision).toLowerCase() === wantedRevision);
        if (exact) return exact;
    }
    const sceneRevisions = Number.isInteger(wantedScene)
        ? revisions.filter((item) => Number(item.scene) === wantedScene) : [];
    const deepest = (items) => [...items].sort((left, right) =>
        Number(right.scene) - Number(left.scene) ||
        String(right.created_at).localeCompare(String(left.created_at)))[0];
    return sceneRevisions.find((item) => item.active)
        ?? sceneRevisions.sort((left, right) =>
            String(right.created_at).localeCompare(String(left.created_at)))[0]
        ?? deepest(revisions.filter((item) => item.active))
        ?? deepest(revisions)
        ?? null;
}

export function checkpointBranchRows(payload) {
    const revisions = checkpointRevisionMap(payload);
    return (payload?.branches ?? []).map((branch) => {
        const slot = branch.attribution_slot;
        return {
            ...branch,
            revisions: (branch.path ?? []).map((item) =>
            revisions.get(checkpointRevisionKey(item.scene, item.revision)))
            .filter(Boolean),
            attribution_slot: slot ? {
                ...slot,
                blocked_candidates:(slot.blocked_candidates ?? []).map((item) => ({
                    ...revisions.get(checkpointRevisionKey(item.scene, item.revision)),
                    ...item,
                })),
                candidates: (slot.candidates ?? []).map((item) =>
                    revisions.get(checkpointRevisionKey(
                        item.scene, item.revision,
                    ))).filter(Boolean),
            } : null,
        };
    });
}

export function checkpointChapterBranchRows(payload, range) {
    const start = Number(range?.start);
    const end = Number(range?.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
        return checkpointBranchRows(payload);
    }
    const grouped = new Map();
    for (const branch of checkpointBranchRows(payload)) {
        const revisions = branch.revisions.filter((revision) => {
            const scene = Number(revision.scene);
            return scene >= start && scene <= end;
        });
        if (!revisions.length) continue;
        const key = revisions.map((revision) => checkpointRevisionKey(
            revision.scene, revision.revision,
        )).join("|");
        const tip = revisions.at(-1);
        const active = revisions.every((revision) => Boolean(revision.active));
        const slot = branch.attribution_slot;
        const attributionSlot = slot && Number(slot.scene) >= start
            && Number(slot.scene) <= end
            && Number(slot.parent_scene ?? tip.scene) === Number(tip.scene)
            && String(slot.parent_revision ?? "").toLowerCase()
                === String(tip.revision ?? "").toLowerCase()
            ? slot : null;
        const existing = grouped.get(key);
        if (existing) {
            existing.source_branch_ids.push(branch.id);
            if (active) {
                existing.active = true;
                existing.label = "Active branch";
            }
            existing.attribution_slot ??= attributionSlot;
            continue;
        }
        grouped.set(key, {
            ...branch,
            id:`chapter:${String(range?.id ?? `${start}-${end}`)}:${key}`,
            label:active ? "Active branch"
                : `Branch ${String(tip.revision ?? "").slice(0, 8)}`,
            active,
            path:revisions.map((revision) => ({
                scene:Number(revision.scene),
                revision:String(revision.revision ?? "").toLowerCase(),
            })),
            revisions,
            attribution_slot:attributionSlot,
            source_branch_ids:[branch.id],
        });
    }
    return [...grouped.values()].sort((left, right) =>
        Number(Boolean(right.active)) - Number(Boolean(left.active)) ||
        Number(right.revisions.at(-1)?.scene ?? 0)
            - Number(left.revisions.at(-1)?.scene ?? 0));
}

// Resolve a whole branch, never use a previewed ancestor as an output range.
// Shared ancestors are ambiguous unless the caller supplies the clicked row.
export function checkpointOutputBranchTip(payload, selected, range = null, preferred = null) {
    if (!selected || selected.take_kind === "editorial_alternate") return null;
    const key = checkpointRevisionKey(selected.scene, selected.revision);
    const rows = checkpointChapterBranchRows(payload, range).filter(branch =>
        branch.revisions.some(item => checkpointRevisionKey(item.scene, item.revision) === key));
    const tips = new Map(rows.map(branch => {
        const tip = branch.revisions.at(-1);
        return [checkpointRevisionKey(tip.scene, tip.revision), tip];
    }));
    const preferredKey = preferred ? checkpointRevisionKey(preferred.scene, preferred.revision) : null;
    return tips.get(preferredKey) ?? tips.get(key) ?? (tips.size === 1 ? [...tips.values()][0] : null);
}

export function checkpointRevisionLineage(payload, selected, range = null) {
    const records = checkpointRevisionMap(payload);
    const start = Math.max(1, Number(range?.start) || 1);
    let cursor = selected ?? null;
    const reversed = [];
    const seen = new Set();
    while (cursor) {
        const scene = Number(cursor.scene);
        if (!Number.isInteger(scene) || scene < start) return [];
        const key = checkpointRevisionKey(cursor.scene, cursor.revision);
        if (seen.has(key)) return [];
        seen.add(key);
        reversed.push({
            scene,
            revision: String(cursor.revision ?? "").toLowerCase(),
        });
        // A chapter start is an editorial branch root. Its immutable parent
        // remains useful provenance, but it does not select the prior
        // chapter's branch.
        if (scene === start) break;
        if (!cursor.parent) break;
        cursor = records.get(checkpointRevisionKey(
            cursor.parent.scene, cursor.parent.revision,
        ));
        if (!cursor) return [];
    }
    const lineage = reversed.reverse();
    if (!lineage.length || lineage[0].scene !== start) return [];
    if (lineage.some((item, index) => item.scene !== start + index)) return [];
    return lineage;
}

export function checkpointProjectLineage(payload, selected, range = null) {
    const start = Math.max(1, Number(range?.start) || 1);
    const chapterLineage = checkpointRevisionLineage(payload, selected, {
        ...range, start,
    });
    if (!chapterLineage.length) return [];
    const revisions = Array.isArray(payload?.revisions) ? payload.revisions : [];
    const prefix = [];
    for (let scene = 1; scene < start; scene += 1) {
        const active = revisions.find((item) =>
            Number(item.scene) === scene && Boolean(item.active));
        if (!active) return [];
        prefix.push({
            scene,
            revision:String(active.revision ?? "").toLowerCase(),
        });
    }
    return [...prefix, ...chapterLineage];
}

export function checkpointSelectionJson(payload, runName, selected, range = null, outputScope = "project") {
    const normalizedRun = String(runName ?? "").trim();
    const lineage = checkpointProjectLineage(payload, selected, range);
    const start = Math.max(1, Number(range?.start) || 1);
    const end = Math.max(start, Number(range?.end) || Number(selected?.scene) || start);
    return normalizedRun && lineage.length
        ? JSON.stringify({
            run_name:normalizedRun,
            lineage,
            scope_start_scene:start,
            scope_end_scene:end,
            ...(outputScope === "chapter" ? {output_scope:"chapter"} : {}),
        })
        : "";
}

// Keep the pinned output in the execution widget itself: one serialized source
// of truth survives save/load, duplication and node reconfiguration.
export function checkpointLocalSelection(value) {
    try {
        const selection = typeof value === "string" ? JSON.parse(value) : value;
        return selection?.output_mode === "workflow_local" ? selection : null;
    } catch {
        return null;
    }
}

function checkpointSceneRanges(scenes) {
    const values = [...new Set(scenes)].sort((left, right) => left - right);
    const ranges = [];
    for (let index = 0; index < values.length; index += 1) {
        const start = values[index];
        let end = start;
        while (values[index + 1] === end + 1) end = values[++index];
        ranges.push(start === end ? String(start) : `${start}–${end}`);
    }
    return ranges.join(", ");
}

// Describe the serialized output, never the preview cursor or the open tab.
// This is a source manifest, not a promise to process every available scene.
export function checkpointOutputSummary(value) {
    const invalid = "Cannot send a source: the saved output selection is invalid. Select a branch again.";
    let saved;
    try { saved = typeof value === "string" ? JSON.parse(value || "null") : value; }
    catch { return invalid; }
    if (!saved) {
        return "No source selected for connected nodes. Choose a saved branch heading; clip and tab clicks only preview.";
    }
    if (!saved.run_name || !Array.isArray(saved.lineage) || !saved.lineage.length
        || saved.lineage.some((item, index) => Number(item?.scene) !== index + 1 || !item?.revision)
        || ![undefined, "project", "chapter"].includes(saved.output_scope)
        || ![undefined, null, "workflow_local"].includes(saved.output_mode)) return invalid;
    const chapterOnly = saved.output_scope === "chapter";
    const first = chapterOnly ? Number(saved.scope_start_scene ?? 1) : 1;
    if (!Number.isInteger(first) || first < 1) return invalid;
    const clips = saved.lineage.filter(item => Number(item.scene) >= first);
    const tip = clips.at(-1);
    if (!tip) return "No saved clips in this output scope. Select a branch again.";
    const processing = saved.processing_source;
    if (processing != null && processing.stage !== "derope") return invalid;
    let source = "Original checkpoints";
    if (processing?.stage === "derope") {
        const lineage = processing.branch?.lineage;
        if (!processing.profile_path || !Array.isArray(lineage) || !lineage.length
            || lineage.some(item => !Number.isInteger(Number(item?.scene))
                || Number(item.scene) < 1 || !item?.revision)) return invalid;
        const scenes = new Set(lineage.map(item => Number(item.scene)));
        const processed = clips.filter(item => scenes.has(Number(item.scene))).map(item => Number(item.scene));
        const fallback = clips.filter(item => !scenes.has(Number(item.scene))).map(item => Number(item.scene));
        source = `DeRoPE checkpoints for scenes ${checkpointSceneRanges(processed) || "none"}`;
        source += fallback.length ? `; Original fallback for scenes ${checkpointSceneRanges(fallback)}` : "; no Original fallback needed";
        source += ` · DeRoPE branch ${String(lineage.at(-1)?.revision ?? "?").slice(0, 8)} (${processing.profile_path})`;
    }
    const scope = chapterOnly ? "selected chapter only" : "selected branch + earlier chapters";
    const mode = saved.output_mode === "workflow_local" ? "pinned to this workflow" : "follows branch selection";
    return `Will send to connected nodes: ${source} · ${saved.run_name} · original branch through scene ${tip.scene} / ${String(tip.revision).slice(0, 8)}`
        + ` · scenes ${first}–${tip.scene} (${clips.length} ${clips.length === 1 ? "clip" : "clips"}; ${scope}) · ${mode}.`
        + " Clip and tab previews do not change this output. Set the processing range downstream.";
}

export function checkpointLocalSelectionJson(payload, runName, selected, range = null, outputScope = "project") {
    const value = checkpointSelectionJson(payload, runName, selected, range, outputScope);
    if (!value) throw new Error("Select a complete saved checkpoint lineage first.");
    const selection = JSON.parse(value);
    const records = checkpointRevisionMap(payload);
    if (selection.lineage.some((item) => {
        const record = records.get(checkpointRevisionKey(item.scene, item.revision));
        // Earlier chapters supply immutable timing metadata only.
        if (outputScope === "chapter" && item.scene < selection.scope_start_scene) return !record;
        return !record?.ready || record.take_kind === "editorial_alternate";
    })) {
        throw new Error("Local output requires available generation checkpoints for every selected scene.");
    }
    return JSON.stringify({...selection, output_mode:"workflow_local"});
}

export function checkpointOutputSelectionJson(current, payload, runName, selected, range = null, outputScope = "project") {
    // Browsing or a new project-wide active tip must never move a local pin.
    return checkpointLocalSelection(current) ? current
        : checkpointSelectionJson(payload, runName, selected, range, outputScope);
}

export function checkpointDeropeSelectionJson(payload, runName, tip, variant, range = null, outputScope = "project") {
    const base = JSON.parse(checkpointLocalSelectionJson(payload, runName, tip, range, outputScope));
    if (variant?.stage !== "derope" || !variant.processing_branch) {
        throw new Error("Select a DeRoPE take with an unambiguous saved branch. For a shared take, choose the desired branch's later take.");
    }
    const selected = new Set(base.lineage.filter(item => outputScope !== "chapter" || item.scene >= base.scope_start_scene)
        .map(item => checkpointRevisionKey(item.scene, item.revision)));
    const first = outputScope === "chapter" ? base.scope_start_scene : 1;
    let used = 0;
    for (const ref of variant.processing_branch.lineage) {
        if (ref.scene < first || ref.scene > tip.scene) continue;
        const saved = (payload.processing_variants ?? []).find(item => item.key === ref.metadata_path && item.checkpoint_sha256 === ref.checkpoint_sha256);
        if (!saved || saved.stage !== "derope" || !saved.ready || !saved.latent_saved) {
            throw new Error(`DeRoPE scene ${ref.scene} needs an available full latent. Save with save_latent enabled and Recovered AV connected, or explicitly use Original.`);
        }
        if (saved.profile_path !== variant.profile_path || !(saved.originals ?? []).some(item => selected.has(checkpointRevisionKey(item.scene, item.revision)))) {
            throw new Error(`DeRoPE scene ${ref.scene} belongs to a different original branch.`);
        }
        used += 1;
    }
    if (!used) throw new Error("This DeRoPE branch has no scenes in the selected output scope.");
    return JSON.stringify({...base, processing_source:{stage:"derope", profile_path:variant.profile_path, branch:variant.processing_branch}});
}

export function checkpointActivationMode(payload, selected, range = null) {
    if (!selected?.ready || selected?.take_kind === "editorial_alternate") {
        return "disabled";
    }
    const start = Math.max(1, Number(range?.start) || 1);
    const selectedScene = Number(selected.scene);
    const lineage = checkpointRevisionLineage(payload, selected, range);
    if (!Number.isInteger(selectedScene)
            || lineage.length !== selectedScene - start + 1) {
        return "disabled";
    }
    const revisions = Array.isArray(payload?.revisions)
        ? payload.revisions : [];
    const records = checkpointRevisionMap(payload);
    if (lineage.some((item) => !records.get(
        checkpointRevisionKey(item.scene, item.revision),
    )?.active)) {
        return "activate";
    }
    const maximumScene = Math.max(
        selectedScene,
        ...revisions.map((item) => Number(item.scene) || 0),
    );
    const end = Math.max(start, Number(range?.end) || maximumScene);
    return revisions.some((item) =>
        Boolean(item.active || item.pointer_active)
        && item.take_kind !== "editorial_alternate"
        && Number(item.scene) > selectedScene
        && Number(item.scene) <= end)
        ? "rollback" : "current";
}

export function checkpointDependencyText(item) {
    const scene = Number(item?.scene) || 0;
    const id = String(item?.scene_id ?? `clip_${String(scene).padStart(4, "0")}`);
    const video = Math.max(0, Number(item?.context_length) || 0);
    const audio = Math.max(0, Number(item?.audio_context_length) || 0);
    const mode = String(item?.continuation_mode ?? "guide");
    const relationship = video || audio
        ? `uses Video ${video}f / Audio ${audio}f via ${mode}`
        : `has a structural continuation edge (Video 0f / Audio 0f)`;
    return `Scene ${scene} · ${id} ${relationship}`;
}

export function checkpointDeletionTitle(preview) {
    if (!preview) return "Select a checkpoint revision to inspect deletion safety.";
    if (preview.allowed) {
        const action = preview.rollback ? "Safe active-tip rollback" : "Safe leaf deletion";
        return `${action} · ${preview.owned_file_count} files · ${formatCheckpointBytes(preview.reclaimed_bytes)}`;
    }
    return (preview.blockers ?? []).join(" ") || "Deletion is blocked.";
}
