import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {activeSceneFromOutput} from "./h3_chain_cancel_reroll_core.mjs?v=0.7.10";
import {
    DEFAULT_CLEANUP_DELAY_MS,
    HANDOFF_API_BASE,
    RECURSIVE_MODE,
    migrateRecursiveExecutionMode,
    checkpointPredecessorReady,
    cleanupDelayMs,
    isQueueSafe,
    matchingNextSceneHandoff,
    pendingNextSceneHandoffs,
    predecessorScene,
    resumeHint,
    handleTopLevelRequeueSuccessScheduling,
    loopEndMatchesObservedCurrent,
    topLevelRequeueCompletionMatches,
} from "./h3_chain_top_level_requeue_core.mjs?v=0.7.11";
import {createNotificationStack} from "./h3_notification_stack_core.mjs?v=0.7.10";
import {projectMutationOptions} from "./h3_project_ownership.mjs?v=0.7.4";
import {submitWithPromptIdentity, submissionFailure, createContinuationTracker, runRequeueLifecycle, authoritativeRunName, finalizeAcceptedSubmission, handleConfirmedSubmissionRejection, handleUncertainSubmission, classifySubmissionOutcome, releaseHandoffChecked} from "./h3_chain_top_level_requeue_coordinator.mjs?v=0.7.10";

// Top-level scene requeue coordinator (M3, candidate_count = 1).
//
// After a successful top-level terminal event, the previous heavyweight H3
// prompt is done and its Loop End has already written a durable next_scene
// handoff (instead of recursively expanding the next scene). This extension:
//   1. waits for a safe queue state (no running/queued jobs),
//   2. waits the configurable cleanup interval measured from terminal success,
//   3. re-validates the visible workflow, run_name, and predecessor checkpoint,
//   4. claims the pending handoff exactly once,
//   5. sets the existing Loop Start resume widgets (start_clip / scene_range),
//   6. queues the SAME existing workflow as a NEW top-level prompt,
//   7. marks the handoff queued, then consumed when the new prompt's Loop
//      Start executes.
//
// The Plan JSON is never modified by this coordinator: it only drives the
// existing Loop Start resume controls. Failures keep the durable handoff and
// tell the user how to resume manually. A browser/server restart never
// auto-runs stale work; it only shows the pending recoverable state.

const SETTING_ID = "MiniMaxH3ContextLoop.topLevelRequeue";
const DELAY_SETTING_ID =
    "MiniMaxH3ContextLoop.topLevelRequeueCleanupDelay";
const START_TYPE = "MiniMaxH3ChainLoopStart";
const END_TYPE = "MiniMaxH3ChainLoopEnd";
const CURRENT_TYPES = new Set([
    "MiniMaxH3ChainCurrent",
    "MiniMaxH3CurrentTaggedReferenceScene",
]);
const PLAN_TYPES = new Set(["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"]);
const PROJECT_ASSET_MANAGER_TYPE = "MiniMaxH3ProjectAssetManager";
const QUEUE_POLL_INTERVAL_MS = 500;
const QUEUE_WAIT_TIMEOUT_MS = 10 * 60 * 1000;
const MAX_OBSERVED_PROMPTS = 100;
const TRANSIENT_NOTICE_MS = 5000;
const WARNING_NOTICE_MS = 10000;

// Nightly's workflow ownership fence also covers orchestration mutations.
// This is a local adapter for handoff requests, not a global API patch.
const handoffApi = {
    async fetchApi(url, options) {
        const runName = JSON.parse(options.body).run_name;
        return api.fetchApi(url, await projectMutationOptions(
            {graph: app.graph}, runName, options));
    },
};

let notifications = null;
let pumpActive = false;
let continuationTracker = null;
let requeueEpoch = 0; // invalidates sleeps/polls when opt-in is withdrawn
const sceneRecords = new Map();   // prompt_id -> observed H3 scene record
const requeueQueue = [];
const hintedRuns = new Set();     // run_names already shown a pending hint

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

function requeueIcon() {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.classList.add("h3trq-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d",
        "M20 7v5h-5M4 17v-5h5M6.1 8.2A7 7 0 0 1 18.7 7M17.9 15.8A7 7 0 0 1 5.3 17");
    svg.append(path);
    return svg;
}

function ensureNotifications() {
    notifications ??= createNotificationStack({
        anchorSelector: ".h3cr-root",
    });
    return notifications;
}

function showTransient(message) {
    ensureNotifications().show("requeue-transient", message, "info", {
        durationMs: TRANSIENT_NOTICE_MS,
    });
}

function showWarning(message) {
    ensureNotifications().clear("requeue-transient");
    ensureNotifications().show("requeue-warning", message, "warning", {durationMs: WARNING_NOTICE_MS});
}

function showError(message) {
    ensureNotifications().clear("requeue-transient");
    ensureNotifications().show("requeue-error", message, "error");
}

function clearNotifications() {
    notifications?.clearAll();
}

function settingEnabled() {
    return app.extensionManager?.setting?.get?.(SETTING_ID) === true;
}

function operationIsCurrent(epoch) {
    return settingEnabled() && epoch === requeueEpoch;
}

function requireCurrentOperation(epoch) {
    if (!operationIsCurrent(epoch)) {
        throw new Error("Automatic requeue was disabled or cancelled.");
    }
}

function sleep(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function safeJson(response) {
    try {
        return await response.json();
    } catch (_error) {
        return null;
    }
}

function trimObservedPrompts() {
    while (sceneRecords.size > MAX_OBSERVED_PROMPTS) {
        const oldest = sceneRecords.keys().next().value;
        sceneRecords.delete(oldest);
    }
}

function onExecuted(detail) {
    const promptId = String(detail?.prompt_id ?? "");
    if (!promptId || detail?.display_node == null) return;
    const node = findNodeByDisplayId(detail.display_node);
    const type = nodeType(node);
    let record = sceneRecords.get(promptId);
    if (!record) {
        record = {
            promptId,
            runName: "",
            clipIndex: 0,
            clipCount: 0,
            endClip: 0,
            shotId: "",
            workflowFingerprint: "",
            executionMode: RECURSIVE_MODE,
            loopEndExecuted: false,
            displayNode: null,
            workflowIdentity: activeWorkflowIdentity(),
        };
        sceneRecords.set(promptId, record);
        trimObservedPrompts();
    }
    if (CURRENT_TYPES.has(type)) {
        const scene = activeSceneFromOutput(detail?.output);
        if (scene) {
            record.runName = scene.runName;
            record.clipIndex = scene.clipIndex;
            record.clipCount = scene.clipCount;
            record.endClip = scene.endClip;
            record.shotId = scene.shotId;
            record.workflowFingerprint = String(scene.workflowFingerprint || "");
            record.workingBranchId = String(detail?.output?.h3_chain_active_scene?.at(-1)?._branch_id ?? "main");
            record.displayNode = String(detail.display_node);
        }
    } else if (type === END_TYPE && loopEndMatchesObservedCurrent({
        record, loopEndNode: node, resolveDisplayNode: findNodeByDisplayId,
        findUpstreamCurrent: end => findUpstreamNode(end, CURRENT_TYPES),
    })) {
        const completion = detail?.output?.h3_chain_top_level_requeue;
        const payload = Array.isArray(completion) ? completion[0] : null;
        if (topLevelRequeueCompletionMatches(record, payload)) {
            record.loopEndExecuted = true;
            record.executionMode = "top_level_requeue";
            record.handoffId = String(payload.handoff_id);
        }
    }
}

function enqueueRequeue(record) {
    // A success is only a candidate signal: it must be the matching opt-in
    // Loop End execution, and opt-in is checked again at every async edge.
    if (!settingEnabled() || !record.loopEndExecuted
        || record.executionMode !== "top_level_requeue") return;
    requeueQueue.push({record, epoch: requeueEpoch});
    void pumpRequeues();
}

async function pumpRequeues() {
    if (pumpActive) return;
    pumpActive = true;
    try {
        while (requeueQueue.length) {
            const scheduled = requeueQueue.shift();
            await processRequeue(scheduled.record, scheduled.epoch);
        }
    } finally {
        pumpActive = false;
    }
}

export function onExecutionSuccess(detail) {
    const promptId = String(detail?.prompt_id ?? "");
    const record = sceneRecords.get(promptId);
    if (continuationTracker?.current()?.promptId === promptId
        && !record?.loopEndExecuted) {
        continuationTracker.failed(promptId);
    }
    if (!record) return;
    sceneRecords.delete(promptId);
    handleTopLevelRequeueSuccessScheduling({record, scheduleRequeue: enqueueRequeue});
}

function onContinuationStart(detail) {
    const promptId = String(detail?.prompt_id ?? "");
    void continuationTracker?.started(promptId);
}

function onTerminalFailure(kind, detail) {
    // Failures only clean exact prompt state. Failed source prompts never
    // schedule a next scene (scheduling requires execution_success), and an
    // unrelated failure must not cancel an already-valid continuation.
    const promptId = String(detail?.prompt_id ?? "");
    sceneRecords.delete(promptId);
    const wait = continuationTracker?.failed(promptId);
    if (wait) {
        // The continuation prompt ended before/without a recorded Loop Start
        // execution. The handoff deliberately stays queued: no auto-retry.
        showError(
            `The requeued prompt for run "${wait.runName}" ended `
            + `${kind === "interrupted" ? "interrupted" : "with an error"} `
            + "before its scene started. The handoff stays queued; set Loop "
            + `Start to the handoff scene and queue manually to resume.`);
    }
}

function requireVisibleWorkflow(record) {
    const workflowIdentity = activeWorkflowIdentity();
    if (record.workflowIdentity && workflowIdentity
        && workflowIdentity !== record.workflowIdentity) {
        throw new Error("Return to the running H3 workflow before requeueing.");
    }
    const currentNode = record.displayNode
        ? findNodeByDisplayId(record.displayNode) : null;
    const startNode = currentNode
        ? findUpstreamNode(currentNode, START_TYPE) : null;
    const planNode = currentNode
        ? findUpstreamNode(currentNode, PLAN_TYPES) : null;
    const runName = authoritativeRunName(planNode);
    if (!currentNode || !startNode || !planNode
        || !record.runName || runName !== record.runName) {
        throw new Error("Return to the running H3 workflow before requeueing.");
    }
    const authoredBranch = JSON.parse(String(widgetByName(planNode, "plan_json")?.value || "{}"))?._branch_id ?? "main";
    if (authoredBranch !== (record.workingBranchId ?? "main")) {
        throw new Error("The working branch changed. Return to the completed scene's branch to resume it.");
    }
    return {startNode, planNode, runName};
}

async function waitForSafeQueue(epoch) {
    const started = Date.now();
    for (;;) {
        requireCurrentOperation(epoch);
        let safe = false;
        try {
            const response = await api.fetchApi("/api/queue");
            if (response.ok) safe = isQueueSafe(await response.json());
        } catch (_error) {
            safe = false;
        }
        if (safe) {
            requireCurrentOperation(epoch);
            return;
        }
        if (Date.now() - started > QUEUE_WAIT_TIMEOUT_MS) {
            throw new Error(
                "The queue did not reach a safe state within 10 minutes.");
        }
        await sleep(QUEUE_POLL_INTERVAL_MS);
    }
}

async function verifyPredecessorCheckpoint(runName, predecessor, branchId = "main") {
    if (predecessor < 1) return null;
    const response = await api.fetchApi(
        `${HANDOFF_API_BASE}/checkpoints?run_name=${encodeURIComponent(runName)}&branch_id=${encodeURIComponent(branchId)}`);
    const body = await safeJson(response);
    if (!response.ok) {
        throw new Error(
            `Cannot verify checkpoint ${predecessor} `
            + `(HTTP ${response.status}).`);
    }
    const checkpoint = Array.isArray(body?.checkpoints)
        ? body.checkpoints.find((item) => item?.ready === true
            && Number(item.scene) === predecessor) : null;
    if (!checkpointPredecessorReady(body?.checkpoints, predecessor)
        || !checkpoint?.revision || !checkpoint?.metadata_sha256) {
        throw new Error(
            `Checkpoint ${predecessor} is not ready with committed identity; `
            + "resume manually once the segment transaction has saved it.");
    }
    return checkpoint;
}

// ComfyUI frontend 1.51.x app.queuePrompt resolves a boolean and deliberately
// discards the server response.  Use its underlying public API when available
// so the prompt_id that /prompt accepted is retained; retain the legacy wrapper
// fallback only for older frontends which return an object.
async function queuePromptWithIdentity(current) {
    const result = await submitWithPromptIdentity({app, api, current});
    return {accepted: result.kind === "accepted" ? true : result.kind === "rejected" ? false : null,
        promptId: result.promptId};
}

async function postHandoffTransition(runName, handoffId, status, acceptedPromptId = null) {
    const response = await handoffApi.fetchApi(
        `${HANDOFF_API_BASE}/handoffs/transition`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                run_name: runName,
                handoff_id: handoffId,
                status,
                accepted_prompt_id: acceptedPromptId,
            }),
        });
    if (!response.ok) {
        const body = await safeJson(response);
        throw new Error(body?.error
            || `Handoff transition failed (HTTP ${response.status}).`);
    }
}

async function processRequeue(record, epoch) {
    const startedAt = Date.now();
    let deliveryMayHaveOccurred = false;
    try {
        requireCurrentOperation(epoch);
        if (!record.runName) {
            throw new Error("The active H3 run_name is empty.");
        }
        showTransient("Waiting for a safe queue state…");
        const lifecycle = await runRequeueLifecycle({
            current: () => { requireCurrentOperation(epoch); requireVisibleWorkflow(record); },
            waitSafe: () => waitForSafeQueue(epoch),
            cleanup: async () => {
                const delay = cleanupDelayMs(app.extensionManager?.setting?.get?.(DELAY_SETTING_ID));
                const remaining = delay - (Date.now() - startedAt);
                if (remaining > 0) await sleep(remaining);
            },
            resolveRun: async () => ({...record, runtimeRunName:record.runName, ...requireVisibleWorkflow(record)}),
            loadCheckpoint: (runName, context) => verifyPredecessorCheckpoint(runName, Number(context.clipIndex), record.workingBranchId),
            listHandoffs: async runName => { const response = await api.fetchApi(`${HANDOFF_API_BASE}/handoffs?run_name=${encodeURIComponent(runName)}`); if (!response.ok) throw new Error(`The handoff list is unavailable (HTTP ${response.status}).`); return response.json(); },
            matchHandoff: matchingNextSceneHandoff,
            claimHandoff: async (runName, handoff) => { const response = await handoffApi.fetchApi(`${HANDOFF_API_BASE}/handoffs/claim`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({run_name:runName,handoff_id:handoff.handoff_id,source_prompt_id:record.promptId})}); if (response.status === 409) throw new Error("The handoff was already claimed; nothing was queued."); if (!response.ok) throw new Error(`Claiming the handoff failed (HTTP ${response.status}).`); },
            prepareResume: async (_runName, handoff, context) => { const resume = resumeHint(handoff); const startWidget = widgetByName(context.startNode, "start_clip"); const rangeWidget = widgetByName(context.startNode, "scene_range"); if (!resume || !startWidget) throw new Error("The handoff has no resume hint or Loop Start widget."); startWidget.value = resume.startClip; startWidget.callback?.(resume.startClip); if (rangeWidget) { rangeWidget.value = resume.sceneRange; rangeWidget.callback?.(resume.sceneRange); } context.startNode.graph?.setDirtyCanvas?.(true, true); showTransient(`Queueing scene ${resume.startClip} as a new top-level prompt…`); },
            submit: () => queuePromptWithIdentity(() => { requireCurrentOperation(epoch); requireVisibleWorkflow(record); }),
            release: (handoff, releasedRun) => releaseHandoffChecked({api:handoffApi, apiBase:HANDOFF_API_BASE, runName:releasedRun, handoffId:handoff.handoff_id, reason:"Automatic requeue was cancelled."}),
        });
        if (!lifecycle) { clearNotifications(); return; }
        if (lifecycle.kind === "cancelled") { clearNotifications(); return; }
        const {runName, handoff, context, submission: lifecycleSubmission, submissionError} = lifecycle;
        const startNode = context.startNode;
        const resume = resumeHint(handoff);
        if (!resume) throw new Error("The handoff has no resume hint; resume the scene manually.");
        try {
            const delivery = await classifySubmissionOutcome({outcome:lifecycleSubmission,error:submissionError,
                accepted: async promptId => { deliveryMayHaveOccurred = true; continuationTracker ??= createContinuationTracker({transition:postHandoffTransition,reportError:error=>showError(`Marking the handoff consumed failed: ${error?.message || error}`)}); await finalizeAcceptedSubmission({runName,handoffId:handoff.handoff_id,promptId,transitionQueued:postHandoffTransition,trackContinuation:continuationTracker.track.bind(continuationTracker)}); },
                rejected: async () => { throw new Error("ComfyUI rejected the prompt validation."); },
                uncertain: async () => { deliveryMayHaveOccurred = true; await handleUncertainSubmission({runName,handoffId:handoff.handoff_id,markUncertain:postHandoffTransition}); },
            });
            if (delivery.kind === "uncertain") throw new Error("Queue delivery is uncertain; recover this handoff manually.");
        } catch (error) {
            if (!deliveryMayHaveOccurred) {
                try {
                    await handleConfirmedSubmissionRejection({
                        runName, handoffId: handoff.handoff_id,
                        releaseHandoff: (releasedRun, releasedHandoff) => releaseHandoffChecked({
                            api: handoffApi, apiBase: HANDOFF_API_BASE, runName: releasedRun, handoffId: releasedHandoff,
                            reason: String(error?.message || error),
                        }),
                    });
                } catch (releaseError) {
                    throw new Error(`ComfyUI rejected the prompt, and releasing the claimed H3 handoff also failed: ${releaseError?.message || releaseError}`);
                }
            }
            throw error;
        }
    } catch (error) {
        showError(deliveryMayHaveOccurred
            ? `Top-level requeue delivery may have occurred: ${error?.message || error} `
                + "The claimed handoff was not released because doing so could duplicate "
                + "the continuation. Reconcile the handoff or queue history manually before retrying."
            : `Top-level requeue did not queue: ${error?.message || error} `
                + "The run's checkpoints are intact; set Loop Start to the "
                + "handoff scene and queue the workflow manually.");
    }
}

function findPlanRunName() {
    const graph = app.graph;
    if (!graph?.nodes) return null;
    for (const node of graph.nodes) {
        if (!PLAN_TYPES.has(nodeType(node))) continue;
        const value = authoritativeRunName(node);
        if (value) return value;
    }
    return null;
}

async function checkPendingHandoffs() {
    const runName = findPlanRunName();
    if (!runName || hintedRuns.has(runName)) return;
    try {
        const response = await api.fetchApi(
            `${HANDOFF_API_BASE}/handoffs?run_name=${encodeURIComponent(runName)}`);
        if (!response.ok) return;
        const pending = pendingNextSceneHandoffs(await response.json());
        if (pending.length) {
            const resume = resumeHint(pending[0]);
            hintedRuns.add(runName);
            showWarning(
                `Pending H3 handoff for run "${runName}": scene `
                + `${resume?.startClip ?? "?"} is resumable. Set Loop Start to `
                + `scene ${resume?.startClip ?? "?"} and queue manually, or `
                + "enable top-level auto requeue to let it claim after "
                + "a terminal success. Nothing auto-runs on startup.");
        }
    } catch (_error) {
        // Server not ready yet; the next graph change retries.
    }
}

app.registerExtension({
    name: "minimax_h3_context_loop.top_level_requeue",
    settings: [
        {
            id: SETTING_ID,
            category: ["MiniMax H3 Context Loop", "Interface", "Top-level requeue"],
            name: "Auto requeue next scene as a new top-level prompt",
            tooltip: "After a successful H3 terminal event, wait for a safe queue and the cleanup interval, claim the next-scene handoff, set Loop Start, and queue the same workflow as a new prompt.",
            type: "boolean",
            defaultValue: false,
            onChange(value) {
                if (value !== true) { requeueEpoch += 1; clearNotifications(); }
            },
        },
        {
            id: DELAY_SETTING_ID,
            category: ["MiniMax H3 Context Loop", "Interface", "Top-level requeue cleanup"],
            name: "Requeue cleanup interval (ms)",
            tooltip: `Milliseconds to wait after the top-level terminal success before queueing the next scene (default ${DEFAULT_CLEANUP_DELAY_MS}).`,
            type: "number",
            defaultValue: DEFAULT_CLEANUP_DELAY_MS,
        },
    ],
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== END_TYPE) return;
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = configure?.apply(this, args);
            migrateRecursiveExecutionMode(this);
            return result;
        };
    },
    nodeCreated(node) {
        migrateRecursiveExecutionMode(node);
    },
    setup() {
        api.addEventListener("executed", (event) => onExecuted(event.detail));
        api.addEventListener("execution_start", (event) => onContinuationStart(event.detail));
        api.addEventListener("execution_success", (event) =>
            onExecutionSuccess(event.detail));
        api.addEventListener("execution_error", (event) =>
            onTerminalFailure("error", event.detail));
        api.addEventListener("execution_interrupted", (event) =>
            onTerminalFailure("interrupted", event.detail));
        api.addEventListener("graphChanged", () => {
            void checkPendingHandoffs();
        });
        // A recovered/opened workflow may load before extension setup; retry
        // shortly after load. Still only shows state — never auto-runs.
        window.setTimeout(() => {
            void checkPendingHandoffs();
        }, 1500);
    },
});
