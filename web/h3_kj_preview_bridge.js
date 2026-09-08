import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
    H3_LOOP_END_TYPE,
    KJ_PREVIEW_TYPE,
    fallbackDisplayIds,
    recursiveRootId,
} from "./h3_kj_preview_bridge_core.mjs?v=0.6.8";

const executionToDisplay = new Map();

// Mirrors ComfyUI's qualified-id lookup for nodes inside subgraphs.
function findNodeByDisplayId(qid) {
    if (!app.graph || qid == null) return null;
    const parts = String(qid).split(":");
    let graph = app.graph;
    for (let i = 0; i < parts.length - 1; i++) {
        const parentId = Number(parts[i]);
        if (!Number.isFinite(parentId)) return null;
        const parent = graph?.getNodeById?.(parentId);
        if (!parent?.subgraph) return null;
        graph = parent.subgraph;
    }
    const leafId = Number(parts.at(-1));
    return Number.isFinite(leafId) ? graph?.getNodeById?.(leafId) ?? null : null;
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function belongsToH3Loop(executionId) {
    const rootId = recursiveRootId(executionId);
    return rootId != null && nodeType(findNodeByDisplayId(rootId)) === H3_LOOP_END_TYPE;
}

function rememberDisplayMapping(data) {
    if (data?.node == null || data?.display_node == null) return;
    const executionId = String(data.node);
    const displayId = String(data.display_node);
    if (executionId === displayId || !belongsToH3Loop(executionId)) return;
    if (nodeType(findNodeByDisplayId(displayId)) === KJ_PREVIEW_TYPE) {
        executionToDisplay.set(executionId, displayId);
    }
}

function resolvePreviewNode(executionId) {
    if (!belongsToH3Loop(executionId)) return null;

    const candidates = [];
    const mapped = executionToDisplay.get(executionId);
    if (mapped != null) candidates.push(mapped);
    candidates.push(...fallbackDisplayIds(executionId));

    for (const displayId of candidates) {
        const node = findNodeByDisplayId(displayId);
        if (nodeType(node) === KJ_PREVIEW_TYPE) return node;
    }
    return null;
}

app.registerExtension({
    name: "minimax_h3_context_loop.kj_preview_loop_bridge",
    setup() {
        api.addEventListener("execution_start", () => executionToDisplay.clear());
        api.addEventListener("executing", (event) => rememberDisplayMapping(event.detail));
        api.addEventListener("kj_preview_override", (event) => {
            const data = event.detail;
            if (data?.node_id == null) return;
            const executionId = String(data.node_id);

            // KJ handles ordinary canvas ids itself. We intervene only when its
            // source is a generated H3 recursion id that has no canvas node.
            if (findNodeByDisplayId(executionId)) return;
            const node = resolvePreviewNode(executionId);
            if (typeof node?._kjPreviewHandler === "function") {
                node._kjPreviewHandler(data);
            }
        });
    },
});
