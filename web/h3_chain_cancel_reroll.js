import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {parsePlanJson, planToJson, randomSceneSeed} from "./h3_chain_plan_core.mjs?v=0.7.8";
import {
    activeSceneFromOutput,
    applySceneReroll,
    resumeSelection,
} from "./h3_chain_cancel_reroll_core.mjs?v=0.7.10";
import {refreshRestoredPlanEditors} from "./h3_plan_restore_core.mjs?v=0.7.9";

// The compact scene expands Current Shot internally. ComfyUI routes that
// internal node's active-scene event to the compact node's display id.
const CURRENT_TYPES = new Set([
    "MiniMaxH3ChainCurrent",
    "MiniMaxH3CurrentTaggedReferenceScene",
]);
const PLAN_TYPE = "MiniMaxH3ChainPlan";
const PLAN_TYPES = new Set([PLAN_TYPE, "MiniMaxH3ChainPlanModern"]);
const START_TYPE = "MiniMaxH3ChainLoopStart";
const SETTING_ID = "MiniMaxH3ContexLoop.cancelRerollControl";
const GENERATION_FINISHED_TYPES = new Set([
    "MiniMaxH3ChainSegmentSave",
    "MiniMaxH3ChainReview",
    "MiniMaxH3ChainLoopEnd",
]);

let root = null;
let actionButton = null;
let status = null;
let active = null;
let busy = false;
let controlAllowed = true;
let wantsVisible = false;
const interruptionWaiters = new Map();

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function findNodeByDisplayId(qid) {
    if (!app.graph || qid == null) return null;
    const parts = String(qid).split(":");
    let graph = app.graph;
    for (let index = 0; index < parts.length - 1; index += 1) {
        const id = Number(parts[index]);
        const parent = Number.isFinite(id) ? graph?.getNodeById?.(id) : null;
        if (!parent?.subgraph) return null;
        graph = parent.subgraph;
    }
    const leaf = Number(parts.at(-1));
    return Number.isFinite(leaf) ? graph?.getNodeById?.(leaf) ?? null : null;
}

function activeWorkflowIdentity() {
    const workflow = app.extensionManager?.workflow?.activeWorkflow;
    const value = workflow?.path
        ?? workflow?.activeState?.id
        ?? workflow?.filename
        ?? null;
    if (value == null || String(value).trim() === "") return null;
    return String(value);
}

function findUpstreamNode(start, wantedType) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        const matches = wantedType instanceof Set
            ? wantedType.has(nodeType(node)) : nodeType(node) === wantedType;
        if (node !== start && matches) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = node.graph?.links?.[input.link];
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function widgetByName(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function injectStyles() {
    if (document.getElementById("h3-chain-cancel-reroll-style")) return;
    const style = document.createElement("style");
    style.id = "h3-chain-cancel-reroll-style";
    style.textContent = `
        .h3cr-root { position:fixed; right:18px; bottom:76px; z-index:10020;
            display:flex; flex-direction:column; align-items:flex-end; gap:5px;
            max-width:min(390px,calc(100vw - 36px)); pointer-events:auto;
            font:12px/1.35 system-ui,sans-serif; }
        .h3cr-root[hidden] { display:none; }
        .h3cr-button { display:flex; align-items:center; gap:7px; padding:9px 13px;
            border:1px solid #c77b4e; border-radius:999px; background:#44281e;
            color:#fff4ed; box-shadow:0 4px 18px #0009; cursor:pointer; font-weight:700; }
        .h3cr-button:hover { background:#573225; }
        .h3cr-button:disabled { opacity:.58; cursor:wait; }
        .h3cr-icon { width:16px; height:16px; fill:none; stroke:currentColor;
            stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
        .h3cr-status { padding:5px 9px; border:1px solid #4a4f5d; border-radius:7px;
            background:#181a20ee; color:#d9dce5; box-shadow:0 3px 12px #0008;
            box-sizing:border-box; max-width:100%; min-width:0;
            text-align:right; white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3cr-status:empty { display:none; }
        .h3cr-error { color:#ffd0bc; border-color:#a86148; }
    `;
    document.head.appendChild(style);
}

function rerollIcon() {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.classList.add("h3cr-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", "M20 7v5h-5M4 17v-5h5M6.1 8.2A7 7 0 0 1 18.7 7M17.9 15.8A7 7 0 0 1 5.3 17");
    svg.append(path);
    return svg;
}

function ensureControl() {
    if (root) return;
    injectStyles();
    root = document.createElement("div");
    root.className = "h3cr-root";
    root.hidden = true;
    actionButton = document.createElement("button");
    actionButton.type = "button";
    actionButton.className = "h3cr-button";
    actionButton.title = "Cancel only this H3 prompt, assign a new scene seed, resume from the preceding checkpoint, and queue it again.";
    actionButton.append(rerollIcon(), document.createElement("span"));
    status = document.createElement("div");
    status.className = "h3cr-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    root.append(actionButton, status);
    document.body.append(root);
    actionButton.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        void cancelAndReroll();
    });
}

function settingEnabled() {
    return app.ui?.settings?.getSettingValue?.(SETTING_ID) !== false;
}

function setControlAllowed(value) {
    controlAllowed = value !== false;
    if (root) root.hidden = !controlAllowed || !wantsVisible;
}

function setControlVisible(value) {
    wantsVisible = Boolean(value);
    if (root) root.hidden = !controlAllowed || !wantsVisible;
}

function showActive(record) {
    ensureControl();
    active = record;
    busy = false;
    actionButton.disabled = false;
    actionButton.lastElementChild.textContent =
        `Cancel & reroll scene ${record.scene.clipIndex}`;
    status.className = "h3cr-status";
    status.textContent = `${record.scene.shotId} · current seed ${record.scene.seed}`;
    setControlVisible(true);
}

function hideControl() {
    active = null;
    busy = false;
    setControlVisible(false);
}

function showFailure(message, retryable = Boolean(active)) {
    ensureControl();
    busy = false;
    actionButton.disabled = !retryable;
    status.className = "h3cr-status h3cr-error";
    status.textContent = String(message);
    setControlVisible(true);
}

function waitForInterruption(promptId, timeoutMilliseconds = 30000) {
    let timer;
    const promise = new Promise((resolve, reject) => {
        timer = window.setTimeout(() => {
            interruptionWaiters.delete(promptId);
            reject(new Error("ComfyUI did not confirm interruption within 30 seconds."));
        }, timeoutMilliseconds);
        interruptionWaiters.set(promptId, {
            resolve: () => {
                window.clearTimeout(timer);
                interruptionWaiters.delete(promptId);
                resolve();
            },
            reject: (message) => {
                window.clearTimeout(timer);
                interruptionWaiters.delete(promptId);
                reject(new Error(message));
            },
        });
    });
    // Cancellation can race the HTTP response. Attach a handler immediately
    // so a terminal websocket event cannot become a transient unhandled
    // rejection before cancelAndReroll reaches `await waiter.promise`.
    void promise.catch(() => {});
    return {
        promise,
        cancel() {
            window.clearTimeout(timer);
            interruptionWaiters.delete(promptId);
        },
    };
}

async function verifyPredecessorCheckpoint(record) {
    if (record.scene.clipIndex === 1) return;
    const query = new URLSearchParams({run_name: record.scene.runName});
    const response = await api.fetchApi(
        `/minimax_h3_context_loop/checkpoints?${query.toString()}`,
    );
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    const predecessor = record.scene.clipIndex - 1;
    const ready = (body.checkpoints ?? []).some((item) =>
        item?.ready && Number(item.scene) === predecessor);
    if (!ready) {
        throw new Error(
            `Cannot reroll scene ${record.scene.clipIndex}: checkpoint ${predecessor} is not ready.`,
        );
    }
}

function requireVisibleWorkflow(record) {
    const workflowIdentity = activeWorkflowIdentity();
    if (record.workflowIdentity && workflowIdentity
        && workflowIdentity !== record.workflowIdentity) {
        throw new Error("Return to the running H3 workflow before requeueing the scene.");
    }
    const currentNode = findNodeByDisplayId(record.displayNode);
    if (!CURRENT_TYPES.has(nodeType(currentNode))) {
        throw new Error("Return to the running H3 workflow before requeueing the scene.");
    }
    const startNode = findUpstreamNode(currentNode, START_TYPE);
    const planNode = findUpstreamNode(currentNode, PLAN_TYPES);
    const runName = String(widgetByName(planNode, "run_name")?.value ?? "").trim();
    if (!startNode || !planNode
        || (record.scene.runName && runName !== record.scene.runName)) {
        throw new Error("Return to the running H3 workflow before requeueing the scene.");
    }
    // ComfyUI may reconstruct graph and node objects when workflow tabs change.
    // Refresh the cached references after validating stable workflow, node-path,
    // and run identities instead of requiring stale JavaScript object equality.
    record.workflowIdentity ??= workflowIdentity;
    record.currentNode = currentNode;
    record.startNode = startNode;
    record.planNode = planNode;
    return {currentNode, startNode, planNode};
}

function updateWorkflowForReroll(record, seed) {
    const {planNode, startNode} = requireVisibleWorkflow(record);
    const planWidget = widgetByName(planNode, "plan_json");
    const startWidget = widgetByName(startNode, "start_clip");
    const rangeWidget = widgetByName(startNode, "scene_range");
    if (!planWidget || !startWidget) {
        throw new Error("The active Current Shot is not connected to an editable Plan and Loop Start.");
    }
    const plan = applySceneReroll(
        parsePlanJson(String(planWidget.value ?? "")),
        record.scene.clipIndex,
        seed,
    );
    const resume = resumeSelection(
        record.scene.clipIndex,
        record.scene.endClip,
        record.scene.clipCount,
    );
    const serialized = planToJson(plan);
    planWidget.value = serialized;
    planWidget.callback?.(serialized);
    refreshRestoredPlanEditors(planNode);
    startWidget.value = resume.startClip;
    startWidget.callback?.(resume.startClip);
    if (rangeWidget) {
        rangeWidget.value = resume.sceneRange;
        rangeWidget.callback?.(resume.sceneRange);
    }
    planNode.graph?.setDirtyCanvas?.(true, true);
    startNode.graph?.setDirtyCanvas?.(true, true);
}

async function cancelAndReroll() {
    if (!controlAllowed || !active || busy) return;
    const record = active;
    busy = true;
    actionButton.disabled = true;
    status.className = "h3cr-status";
    status.textContent = `Checking checkpoint for scene ${record.scene.clipIndex}…`;
    let waiter = null;
    let rerollPrepared = false;
    try {
        if (!record.scene.runName) throw new Error("The active H3 run_name is empty.");
        // A fixed overlay survives ComfyUI tab switches. Refuse before
        // cancellation unless the graph that emitted this scene is still the
        // visible graph; app.queuePrompt always serializes the visible graph.
        requireVisibleWorkflow(record);
        await verifyPredecessorCheckpoint(record);
        let seed = randomSceneSeed();
        while (seed === record.scene.seed) seed = randomSceneSeed();
        status.textContent = `Cancelling scene ${record.scene.clipIndex}…`;
        waiter = waitForInterruption(record.promptId);
        const response = await api.fetchApi(
            `/api/jobs/${encodeURIComponent(record.promptId)}/cancel`,
            {method: "POST"},
        );
        let body = {};
        try {
            body = await response.json();
        } catch (_error) {
            // A proxy may replace an unsupported endpoint's JSON response.
        }
        if (!response.ok) {
            throw new Error(body.error ||
                `Targeted cancellation is unavailable (HTTP ${response.status}). Update ComfyUI.`);
        }
        if (!body.cancelled) {
            active = null;
            throw new Error("The scene finished before targeted cancellation; nothing was requeued.");
        }
        // The exact prompt accepted cancellation. Never offer another action
        // against its now-terminal id, even if a proxy loses the websocket
        // confirmation or the following Plan update needs manual recovery.
        active = null;
        status.textContent = "Waiting for ComfyUI to finish interrupting the scene…";
        await waiter.promise;
        waiter = null;
        updateWorkflowForReroll(record, seed);
        rerollPrepared = true;
        status.textContent = `Queueing scene ${record.scene.clipIndex} with seed ${seed}…`;
        await app.queuePrompt(0, 1);
        busy = false;
        actionButton.disabled = true;
        status.textContent = `Scene ${record.scene.clipIndex} requeued with seed ${seed}.`;
        window.setTimeout(() => {
            if (!active && !busy) setControlVisible(false);
        }, 5000);
    } catch (error) {
        waiter?.cancel();
        const suffix = rerollPrepared
            ? " The Plan and Loop Start are prepared; queue the workflow manually."
            : "";
        showFailure(`${error?.message || error}${suffix}`);
    }
}

function onCurrentExecuted(data) {
    const scene = activeSceneFromOutput(data?.output);
    if (!scene || !data?.prompt_id || data?.display_node == null) return;
    const currentNode = findNodeByDisplayId(data.display_node);
    if (!CURRENT_TYPES.has(nodeType(currentNode))) return;
    const startNode = findUpstreamNode(currentNode, START_TYPE);
    const planNode = findUpstreamNode(currentNode, PLAN_TYPES);
    if (!startNode || !planNode) return;
    showActive({
        promptId: String(data.prompt_id),
        displayNode: String(data.display_node),
        workflowIdentity: activeWorkflowIdentity(),
        currentNode,
        startNode,
        planNode,
        scene,
    });
}

function onExecuting(data) {
    if (!active || String(data?.prompt_id ?? "") !== active.promptId) return;
    const node = findNodeByDisplayId(data?.display_node);
    if (GENERATION_FINISHED_TYPES.has(nodeType(node))) hideControl();
}

function onTerminal(kind, data) {
    const promptId = String(data?.prompt_id ?? "");
    const waiter = interruptionWaiters.get(promptId);
    if (waiter) {
        if (kind === "interrupted") waiter.resolve();
        else {
            if (active?.promptId === promptId) active = null;
            waiter.reject(
                kind === "success"
                    ? "The scene completed before cancellation; nothing was requeued."
                    : "The H3 prompt ended before cancellation was confirmed.",
            );
        }
    }
    if (active?.promptId === promptId && !busy) hideControl();
}

app.registerExtension({
    name: "minimax_h3_context_loop.cancel_reroll",
    init() {
        app.ui?.settings?.addSetting?.({
            id: SETTING_ID,
            category: ["MiniMax H3 Context Loop", "Interface", "Cancel & reroll"],
            name: "Show floating Cancel & reroll control",
            tooltip: "Show the guarded floating action while an H3 scene is sampling. Disabling it does not remove Review Gate retry or reroll controls.",
            type: "boolean",
            defaultValue: true,
            onChange(value) {
                setControlAllowed(value);
            },
        });
        setControlAllowed(settingEnabled());
    },
    setup() {
        ensureControl();
        setControlAllowed(settingEnabled());
        api.addEventListener("executed", (event) => onCurrentExecuted(event.detail));
        api.addEventListener("executing", (event) => onExecuting(event.detail));
        api.addEventListener("execution_interrupted", (event) =>
            onTerminal("interrupted", event.detail));
        api.addEventListener("execution_success", (event) =>
            onTerminal("success", event.detail));
        api.addEventListener("execution_error", (event) =>
            onTerminal("error", event.detail));
    },
});
