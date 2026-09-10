export const PLAN_STUDIO_NODE_TYPE = "MiniMaxH3ChainPlanStudio";
export const PROMPT_EDITOR_NODE_TYPES = Object.freeze([
    "MiniMaxH3ChainScenePromptEditor",
    "MiniMaxH3ChainRichScenePromptEditor",
]);

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function graphLink(graph, linkId) {
    return graph?.links?.[linkId] ?? graph?.links?.get?.(linkId) ?? null;
}

function allGraphNodes(graph, output = []) {
    for (const node of graph?._nodes ?? []) {
        output.push(node);
        if (node.subgraph) allGraphNodes(node.subgraph, output);
    }
    return output;
}

function graphRoot(node) {
    return node?.graph?.rootGraph ?? node?.graph;
}

/** Direct PLAN neighbours in either direction. Keeping this adjacency-scoped
 * means two independent editor branches may share a Plan without unexpectedly
 * taking over each other's scene selection. */
export function adjacentPlanCompanions(node) {
    const graph = node?.graph;
    const found = [];
    const seen = new Set();
    const add = (candidate) => {
        if (!candidate || candidate === node || seen.has(candidate)) return;
        seen.add(candidate);
        found.push(candidate);
    };
    for (const input of node?.inputs ?? []) {
        if (input.link == null) continue;
        const link = graphLink(graph, input.link);
        add(link ? graph?.getNodeById?.(link.origin_id) : null);
    }
    for (const output of node?.outputs ?? []) {
        for (const linkId of output.links ?? []) {
            const link = graphLink(graph, linkId);
            add(link ? graph?.getNodeById?.(link.target_id) : null);
        }
    }
    return found;
}

export function connectedPromptEditors(node) {
    const allowed = new Set(PROMPT_EDITOR_NODE_TYPES);
    return adjacentPlanCompanions(node).filter((candidate) => allowed.has(nodeType(candidate)));
}

export function connectedPlanStudios(node) {
    return adjacentPlanCompanions(node).filter(
        (candidate) => nodeType(candidate) === PLAN_STUDIO_NODE_TYPE,
    );
}

/** Merge a dedicated editor's active prompt onto a freshly parsed Plan while
 * preserving the local Plan and active-shot object identities. DOM input
 * handlers commonly close over that shot object; replacing it after the first
 * keystroke would make later keystrokes write into a detached object. */
export function rebaseScenePrompt(localPlan, livePlan, sceneIndex) {
    if (!Array.isArray(localPlan?.shots) || !Array.isArray(livePlan?.shots)) return -1;
    const localIndex = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    const editedShot = localPlan.shots[localIndex];
    if (!editedShot) return -1;
    const editedId = String(editedShot.id ?? "").trim();
    const targetIndex = editedId
        ? livePlan.shots.findIndex((shot) => String(shot?.id ?? "").trim() === editedId)
        : localIndex;
    if (targetIndex < 0 || targetIndex >= livePlan.shots.length) return -1;

    const targetShot = livePlan.shots[targetIndex];
    targetShot.prompt = Array.isArray(editedShot.prompt)
        ? [...editedShot.prompt] : editedShot.prompt;
    if ("basic_prompt" in editedShot) {
        targetShot.basic_prompt = editedShot.basic_prompt;
    }
    for (const key of Object.keys(editedShot)) delete editedShot[key];
    Object.assign(editedShot, targetShot);
    livePlan.shots[targetIndex] = editedShot;

    for (const key of Object.keys(localPlan)) delete localPlan[key];
    Object.assign(localPlan, livePlan);
    return targetIndex;
}

/** Keep a companion editor on the same logical scene when another Plan UI
 * rewrites or reorders the Plan. Scene IDs are stable across prompt edits;
 * the numeric position is only a safe fallback for legacy plans. */
export function activeSceneIndexAfterRefresh(previousPlan, nextPlan, sceneIndex) {
    if (!Array.isArray(nextPlan?.shots) || !nextPlan.shots.length) return 0;
    const previousIndex = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    const previousShot = Array.isArray(previousPlan?.shots)
        ? previousPlan.shots[previousIndex] : null;
    const previousId = String(previousShot?.id ?? "").trim();
    if (previousId) {
        const matched = nextPlan.shots.findIndex(
            (shot) => String(shot?.id ?? "").trim() === previousId,
        );
        if (matched >= 0) return matched;
    }
    return Math.min(previousIndex, nextPlan.shots.length - 1);
}

/** Compare every persisted Plan field except scene prompt text.
 *
 * Prompt broadcasts deliberately update their text field in place to preserve
 * browser selection and undo. They must not, however, mark a newer Plan JSON
 * as consumed when that JSON also changes a seed, length, scene order, context
 * setting, or any other Plan data. */
export function planHasNonPromptChanges(previousPlan, nextPlan) {
    const withoutScenePrompts = (plan) => {
        if (!plan || typeof plan !== "object" || Array.isArray(plan)) {
            return plan;
        }
        return {
            ...plan,
            shots:Array.isArray(plan.shots) ? plan.shots.map((shot) => {
                if (!shot || typeof shot !== "object" || Array.isArray(shot)) {
                    return shot;
                }
                const copy = {...shot};
                delete copy.prompt;
                delete copy.basic_prompt;
                return copy;
            }) : plan.shots,
        };
    };
    return JSON.stringify(withoutScenePrompts(previousPlan))
        !== JSON.stringify(withoutScenePrompts(nextPlan));
}

/** Transient UI coordination only: no selection state is added to the Plan or
 * workflow. Receivers verify that they currently resolve the same Plan node. */
export function publishCompanionScene(source, planNode, sceneIndex) {
    const index = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    let delivered = 0;
    for (const candidate of adjacentPlanCompanions(source)) {
        const apply = candidate?._h3PromptCompanionSetActiveScene;
        if (typeof apply !== "function") continue;
        try {
            if (apply.call(candidate, planNode, index, source) !== false) delivered += 1;
        } catch (_error) {
            // A companion UI must never break navigation in the source node.
        }
    }
    return delivered;
}

/** A runtime component such as Review Gate is not directly adjacent to the
 * Plan's authoring companions. Broadcast across the current graph instead;
 * each receiver still verifies the exact Plan node before accepting. */
export function publishPlanCompanionScene(source, planNode, sceneIndex) {
    const index = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    let delivered = 0;
    for (const candidate of allGraphNodes(graphRoot(source))) {
        if (!candidate || candidate === source) continue;
        const apply = candidate._h3PromptCompanionSetActiveScene;
        if (typeof apply !== "function") continue;
        try {
            if (apply.call(candidate, planNode, index, source) !== false) delivered += 1;
        } catch (_error) {
            // Synchronization must never interrupt review or execution.
        }
    }
    return delivered;
}

/** Publish one already-written Plan prompt to every UI bound to that exact
 * Plan. Receivers update their live field without writing back or rebroadcasting,
 * which keeps this loop-free and preserves browser undo in the source editor. */
export function publishCompanionPrompt(source, planNode, sceneIndex, prompt) {
    const index = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    const text = String(prompt ?? "").replace(/\r\n?/g, "\n");
    let delivered = 0;
    for (const candidate of allGraphNodes(graphRoot(source))) {
        if (!candidate || candidate === source) continue;
        const apply = candidate._h3PromptCompanionSetScenePrompt;
        if (typeof apply !== "function") continue;
        try {
            if (apply.call(candidate, planNode, index, text, source) !== false) delivered += 1;
        } catch (_error) {
            // A companion UI must not make a Plan write fail.
        }
    }
    return delivered;
}

/** Publish one already-written basic (pre-optimization) prompt to every UI
 * bound to that exact Plan, mirroring publishCompanionPrompt. Kept as a
 * separate broadcast (not folded into publishCompanionPrompt) since the two
 * fields are edited and consumed independently - only Rich Scene Prompt
 * Editor's Optimize action ever turns one into the other. */
export function publishCompanionBasicPrompt(source, planNode, sceneIndex, basicPrompt) {
    const index = Math.max(0, Math.trunc(Number(sceneIndex) || 0));
    const text = String(basicPrompt ?? "").replace(/\r\n?/g, "\n");
    let delivered = 0;
    for (const candidate of allGraphNodes(graphRoot(source))) {
        if (!candidate || candidate === source) continue;
        const apply = candidate._h3PromptCompanionSetBasicPrompt;
        if (typeof apply !== "function") continue;
        try {
            if (apply.call(candidate, planNode, index, text, source) !== false) delivered += 1;
        } catch (_error) {
            // A companion UI must not make a Plan write fail.
        }
    }
    return delivered;
}
