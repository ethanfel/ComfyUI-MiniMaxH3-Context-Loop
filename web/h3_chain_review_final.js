import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    canCaptureFrame, captureCarousels, captureTargetProject, carouselProject,
} from "./h3_review_capture_core.mjs?v=0.7.0";
import {
    parsePlanJson,
    planToJson,
    promptValueToText,
} from "./h3_chain_plan_core.mjs?v=0.7.8";
import * as promptCompanionSync from "./h3_prompt_companion_sync.mjs?v=0.7.2";
import {
    refreshRestoredPlanEditors,
    restoreConnectedPolicyInputs,
} from "./h3_plan_restore_core.mjs?v=0.7.9";
import {
    acceptedPreviewDisposition,
    applyCheckpointRevisionSet,
    applyReviewEdit,
    checkpointRevisionChain,
    checkpointResumeOptions,
    reviewCountdown,
    reviewDuration,
    reviewDurationText,
    reviewLocalDeadline,
    reviewPlanScenePrompt,
    reviewSeed,
} from "./h3_chain_review_core.mjs?v=0.7.7";
import {projectMutationOptions} from "./h3_project_ownership.mjs?v=0.7.4";

const NODE_NAME = "MiniMaxH3ChainReview";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const PLAN_NAMES = new Set([PLAN_NAME, "MiniMaxH3ChainPlanModern"]);
const PROJECT_ASSET_MANAGER_NODE = "MiniMaxH3ProjectAssetManager";
const PROMPT_EDITOR_SETTING = "MiniMaxH3ContexLoop.ReviewGate.PromptEditor";
const VIDEO_HEIGHT_PROPERTY = "h3_chain_review_video_height";
const PROMPT_HEIGHT_PROPERTY = "h3_chain_review_prompt_height";
const DEFAULT_VIDEO_HEIGHT = 300;
const MIN_VIDEO_HEIGHT = 140;
const MAX_VIDEO_HEIGHT = 1200;
const DEFAULT_PROMPT_HEIGHT = 120;
const MIN_PROMPT_HEIGHT = 120;
const MAX_PROMPT_HEIGHT = 800;
const notifiedTokens = new Set();
const mountedReviewNodes = new Set();
let notificationAudioContext = null;
let pendingFetchPromise = null;
let pendingPollTimer = null;
const activeSceneExecutions = new Map();
const reviewInterruptionWaiters = new Map();

function sceneExecutionKey(runName, clipIndex) {
    return `${String(runName ?? "").trim()}\u0000${Number(clipIndex)}`;
}

function activeSceneFromExecutedOutput(output) {
    const values = output?.h3_chain_active_scene;
    const value = Array.isArray(values) ? values.at(-1) : null;
    const clipIndex = Number(value?.clip_index);
    if (!value || !String(value.run_name ?? "").trim()
            || !Number.isInteger(clipIndex) || clipIndex < 1) return null;
    return {
        runName: String(value.run_name).trim(),
        clipIndex,
    };
}

function trackActiveSceneExecution(data) {
    const scene = activeSceneFromExecutedOutput(data?.output);
    const promptId = String(data?.prompt_id ?? "");
    if (!scene || !promptId) return;
    activeSceneExecutions.set(
        sceneExecutionKey(scene.runName, scene.clipIndex),
        {promptId, displayNode: String(data?.display_node ?? "")},
    );
}

function waitForReviewInterruption(promptId, timeoutMilliseconds = 30000) {
    let timer;
    const promise = new Promise((resolve, reject) => {
        timer = window.setTimeout(() => {
            reviewInterruptionWaiters.delete(promptId);
            reject(new Error(
                "ComfyUI did not confirm candidate interruption within 30 seconds."));
        }, timeoutMilliseconds);
        reviewInterruptionWaiters.set(promptId, {
            resolve: () => {
                window.clearTimeout(timer);
                reviewInterruptionWaiters.delete(promptId);
                resolve();
            },
            reject: (message) => {
                window.clearTimeout(timer);
                reviewInterruptionWaiters.delete(promptId);
                reject(new Error(message));
            },
        });
    });
    // The websocket terminal event can beat the cancellation HTTP response.
    void promise.catch(() => {});
    return {
        promise,
        cancel() {
            window.clearTimeout(timer);
            reviewInterruptionWaiters.delete(promptId);
        },
    };
}

function finishTrackedReviewExecution(kind, data) {
    const promptId = String(data?.prompt_id ?? "");
    if (!promptId) return;
    for (const [key, record] of activeSceneExecutions) {
        if (record.promptId !== promptId) continue;
        record.terminal = kind;
        window.setTimeout(() => {
            if (activeSceneExecutions.get(key) === record) {
                activeSceneExecutions.delete(key);
            }
        }, 60000);
    }
    const waiter = reviewInterruptionWaiters.get(promptId);
    if (!waiter) return;
    if (kind === "interrupted") waiter.resolve();
    else waiter.reject(
        kind === "success"
            ? "The candidate finished before targeted cancellation."
            : "The H3 prompt ended before candidate cancellation was confirmed.",
    );
}

function reviewPromptEditorEnabled() {
    return app.ui?.settings?.getSettingValue?.(PROMPT_EDITOR_SETTING) === true;
}

// A browser can briefly retain the preceding companion module after updating
// a custom node. Namespace access keeps Review Gate mountable in that state;
// prompt synchronization becomes a no-op until the module cache refreshes.
function publishCompanionPrompt(...args) {
    return promptCompanionSync.publishCompanionPrompt?.(...args) ?? 0;
}

function publishPlanCompanionScene(...args) {
    return promptCompanionSync.publishPlanCompanionScene?.(...args) ?? 0;
}

function ensureNotificationAudioContext() {
    const AudioContext = window.AudioContext ?? window.webkitAudioContext;
    if (!AudioContext) return null;
    notificationAudioContext ??= new AudioContext();
    return notificationAudioContext;
}

function unlockNotificationAudio() {
    ensureNotificationAudioContext()?.resume?.().catch(() => {});
}

// Queueing a workflow is normally the needed user gesture. Prime WebAudio on
// the first interaction so a much later review notification is not rejected by
// browser autoplay policy.
window.addEventListener("pointerdown", unlockNotificationAudio, {once: true, capture: true});
window.addEventListener("keydown", unlockNotificationAudio, {once: true, capture: true});

async function playReviewChime() {
    const context = ensureNotificationAudioContext();
    if (!context) return;
    if (context.state === "suspended") {
        await context.resume();
    }
    const now = context.currentTime;
    const gain = context.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.12, now + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.55);
    gain.connect(context.destination);
    for (const [frequency, delay] of [[660, 0], [880, 0.16]]) {
        const oscillator = context.createOscillator();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        oscillator.connect(gain);
        oscillator.start(now + delay);
        oscillator.stop(now + delay + 0.28);
    }
}

function injectStyles() {
    if (document.getElementById("h3-chain-review-style")) return;
    const style = document.createElement("style");
    style.id = "h3-chain-review-style";
    style.textContent = `
        .h3r-root { box-sizing:border-box; display:flex; flex-direction:column; gap:8px;
            min-height:500px; padding:9px; overflow:auto; position:relative;
            border:1px solid #56637e; border-radius:8px; background:#181a20;
            color:#e8eaf0; font:12px/1.35 system-ui,sans-serif; }
        .h3r-root * { box-sizing:border-box; }
        .h3r-root [hidden] { display:none !important; }
        .h3r-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
        .h3r-title { font-weight:750; color:#a9c2ff; }
        .h3r-badge { color:#d5d9e3; opacity:.75; }
        .h3r-video-panel { width:100%; height:300px; min-height:140px; max-height:1200px;
            flex:0 0 auto; display:flex; flex-direction:column; overflow:hidden;
            border:1px solid #343b4b; border-radius:6px; background:#08090c; }
        .h3r-video { width:100%; height:calc(100% - 11px); min-height:0; display:block;
            flex:1 1 auto; background:#08090c; object-fit:contain; }
        .h3r-video-grip { height:11px; flex:0 0 11px; cursor:ns-resize;
            border-top:1px solid #343b4b; background:linear-gradient(180deg,#252a35,#171a21);
            position:relative; touch-action:none; }
        .h3r-video-grip::after { content:""; position:absolute; left:calc(50% - 20px); top:4px;
            width:40px; height:2px; border-top:1px solid #7e899f;
            border-bottom:1px solid #4f586b; }
        .h3r-video-grip:hover { background:linear-gradient(180deg,#313848,#1d212b); }
        .h3r-capture-row { display:flex; align-items:center; gap:7px; }
        .h3r-capture-button { flex:0 0 auto; padding:6px 10px; border:1px solid #63708b;
            border-radius:5px; background:#232837; color:#eef1f7; cursor:pointer; }
        .h3r-capture-button:hover { background:#343b4b; }
        .h3r-capture-button:disabled { opacity:.42; cursor:not-allowed; }
        .h3r-capture-status { color:#aeb5c5; opacity:.8; overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap; }
        .h3r-capture-dialog { position:absolute; inset:0; z-index:30; display:flex;
            align-items:center; justify-content:center; background:rgba(6,7,10,.72); }
        .h3r-capture-card { display:flex; flex-direction:column; gap:9px; width:min(320px, 92%);
            max-height:90%; overflow-y:auto; padding:12px; border:1px solid #56637e;
            border-radius:8px; background:#181c26; box-shadow:0 8px 28px rgba(0,0,0,.5); }
        .h3r-capture-preview { width:100%; max-height:180px; object-fit:contain;
            border:1px solid #343b4b; border-radius:6px; background:#08090c; }
        .h3r-capture-title { font-weight:700; color:#a9c2ff; }
        .h3r-capture-field { display:flex; flex-direction:column; gap:4px; color:#aeb5c5; }
        .h3r-capture-tag-row { display:flex; gap:0; position:relative; }
        .h3r-capture-tag { flex:1 1 auto; min-width:0; width:100%; padding:6px 7px;
            border:1px solid #56637e; border-right:0; border-radius:5px 0 0 5px;
            background:#101218; color:#eef1f7; }
        .h3r-capture-tag-picker { flex:0 0 auto; width:28px; padding:6px 0;
            border:1px solid #56637e; border-radius:0 5px 5px 0; background:#232837;
            color:#eef1f7; cursor:pointer; }
        .h3r-capture-tag-picker:hover { background:#343b4b; }
        .h3r-capture-tag-menu { position:absolute; top:calc(100% + 3px); left:0; right:0;
            z-index:40; max-height:150px; overflow-y:auto; border:1px solid #56637e;
            border-radius:5px; background:#101218; box-shadow:0 6px 18px rgba(0,0,0,.5); }
        .h3r-capture-tag-option { display:block; width:100%; text-align:left; border:0; background:transparent; padding:6px 8px; color:#eef1f7; cursor:pointer; }
        .h3r-capture-tag-option:hover { background:#232837; }
        .h3r-capture-tag-empty { padding:6px 8px; color:#8b93a6; }
        .h3r-capture-hint { color:#8b93a6; font-size:11px; }
        .h3r-capture-error { color:#ff9a9a; }
        .h3r-capture-actions { display:flex; justify-content:flex-end; gap:7px; }
        .h3r-label { display:flex; flex-direction:column; gap:4px; color:#aeb5c5; }
        .h3r-prompt-panel { width:100%; height:120px; min-height:120px; max-height:800px;
            display:flex; flex-direction:column; overflow:hidden;
            border:1px solid #56637e; border-radius:5px; background:#101218; }
        .h3r-prompt { width:100%; height:calc(100% - 11px); min-height:0; resize:none;
            padding:7px; border:0; border-radius:0; background:transparent; color:#eef1f7; }
        .h3r-prompt-grip { height:11px; flex:0 0 11px; cursor:ns-resize;
            border-top:1px solid #343b4b; background:linear-gradient(180deg,#252a35,#171a21);
            position:relative; touch-action:none; }
        .h3r-prompt-grip::after { content:""; position:absolute; left:calc(50% - 20px); top:4px;
            width:40px; height:2px; border-top:1px solid #7e899f;
            border-bottom:1px solid #4f586b; }
        .h3r-prompt-grip:hover { background:linear-gradient(180deg,#313848,#1d212b); }
        .h3r-prompt-notice { padding:8px 9px; border:1px solid #56637e;
            border-radius:6px; background:#202431; color:#cbd3e5; white-space:pre-wrap; }
        .h3r-row { display:flex; align-items:flex-end; gap:7px; }
        .h3r-field { display:flex; flex-direction:column; gap:4px; min-width:0;
            color:#aeb5c5; }
        .h3r-field-seed { flex:2 1 0; }
        .h3r-field-duration { flex:1 1 0; }
        .h3r-seed { width:100%; min-width:0; padding:6px 7px; border:1px solid #56637e;
            border-radius:5px; background:#101218; color:#eef1f7; }
        .h3r-duration { width:100%; min-width:0; padding:6px 7px;
            border:1px solid #56637e; border-radius:5px; background:#101218;
            color:#eef1f7; }
        .h3r-candidates { display:flex; flex-direction:column; gap:7px; padding:8px;
            border:1px solid #4c6388; border-radius:6px; background:#172033; }
        .h3r-candidate-nav { display:grid; grid-template-columns:auto minmax(0,1fr) auto;
            align-items:center; gap:7px; }
        .h3r-candidate-title { min-width:0; text-align:center; color:#a9c2ff;
            font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3r-candidate-arrow { width:38px; padding:5px; font-size:16px; }
        .h3r-candidate-meta { display:flex; align-items:center; justify-content:space-between;
            gap:8px; min-width:0; }
        .h3r-candidate-keep { display:flex; align-items:center; gap:6px; color:#d6e3ff;
            cursor:pointer; user-select:none; }
        .h3r-candidate-progress { color:#9ca8bc; font-size:10px; text-align:right; }
        .h3r-candidate-dots { display:flex; flex-wrap:wrap; justify-content:center; gap:6px; }
        .h3r-candidate-dot { width:11px; height:11px; padding:0; border:1px solid #71809c;
            border-radius:50%; background:#343b4b; cursor:pointer; }
        .h3r-candidate-dot:hover { background:#526078; }
        .h3r-candidate-dot.h3r-selected { outline:2px solid #a9c2ff;
            outline-offset:2px; background:#6f8fd0; }
        .h3r-candidate-dot.h3r-kept { border-color:#70d39c; background:#347a54; }
        .h3r-actions { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
        .h3r-button { padding:7px; border:1px solid #63708b; border-radius:5px;
            background:#292e3a; color:#eef1f7; cursor:pointer; }
        .h3r-button:hover { background:#343b4b; }
        .h3r-button:disabled { opacity:.42; cursor:not-allowed; }
        .h3r-button:disabled:hover { background:#292e3a; }
        .h3r-approve { border-color:#4b9d72; background:#204332; }
        .h3r-retry { border-color:#b58b45; background:#4a3820; }
        .h3r-stop { border-color:#8a6171; background:#3b252d; }
        .h3r-status { min-height:18px; color:#aeb5c5; white-space:pre-wrap; }
        .h3r-warning { color:#f2bd67; }
        .h3r-prefix { margin:0; padding:6px 7px; max-height:90px; overflow:auto;
            border-left:2px solid #56637e; color:#aeb5c5; white-space:pre-wrap; }
        .h3r-resume { display:flex; flex-direction:column; gap:6px; margin-top:2px;
            padding:8px; border:1px solid #3f4759; border-radius:6px; background:#14161c; }
        .h3r-resume-title { font-weight:700; color:#a9c2ff; }
        .h3r-resume-row { display:flex; gap:6px; align-items:center; }
        .h3r-resume-select { flex:1; min-width:0; padding:6px; border:1px solid #56637e;
            border-radius:5px; background:#101218; color:#eef1f7; }
        .h3r-resume-status { color:#8f98aa; white-space:pre-wrap; }
        .h3r-revisions { display:flex; flex-direction:column; gap:6px; padding-top:4px;
            border-top:1px solid #343b4b; }
        .h3r-revisions-title { font-weight:650; color:#c3cad8; }
        .h3r-revision-rows { display:flex; flex-direction:column; gap:6px; }
        .h3r-revision-row { display:grid; grid-template-columns:auto minmax(0,1fr) auto;
            gap:6px; align-items:center; }
        .h3r-revision-label { min-width:54px; color:#aeb5c5; }
        .h3r-revision-select { min-width:0; padding:6px; border:1px solid #56637e;
            border-radius:5px; background:#101218; color:#eef1f7; }
        .h3r-delete { border-color:#8a6171; background:#3b252d; }
        .h3r-root.h3r-busy .h3r-actions .h3r-button { opacity:.45; pointer-events:none; }
        .h3r-root.h3r-busy .h3r-candidates { opacity:.55; pointer-events:none; }
    `;
    document.head.appendChild(style);
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function findNodeByQualifiedId(qid) {
    if (!app.graph || qid == null) return null;
    const parts = String(qid).split(":");
    let graph = app.graph;
    for (let i = 0; i < parts.length - 1; i += 1) {
        const id = Number(parts[i]);
        const parent = Number.isFinite(id) ? graph?.getNodeById?.(id) : null;
        if (!parent?.subgraph) return null;
        graph = parent.subgraph;
    }
    const leaf = Number(parts.at(-1));
    return Number.isFinite(leaf) ? graph?.getNodeById?.(leaf) ?? null : null;
}

function allNodes(graph, output = []) {
    for (const node of graph?._nodes ?? []) {
        output.push(node);
        if (node.subgraph) allNodes(node.subgraph, output);
    }
    return output;
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

function videoUrl(item) {
    const query = new URLSearchParams({
        filename: item.filename,
        subfolder: item.subfolder ?? "",
        type: item.type ?? "output",
    });
    return api.apiURL(`/view?${query.toString()}`);
}

function upstreamPlanNode(reviewNode) {
    return findUpstreamNode(reviewNode, PLAN_NAMES) ??
        allNodes(app.graph).find((item) => PLAN_NAMES.has(nodeType(item)));
}

function widgetByName(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function planResumeContext(reviewNode) {
    const planNode = upstreamPlanNode(reviewNode);
    const planWidget = widgetByName(planNode, "plan_json");
    const runWidget = widgetByName(planNode, "run_name");
    if (!planWidget || !runWidget) {
        throw new Error("Connect this Review Gate to an H3 Chain Plan and Loop Start.");
    }
    const plan = parsePlanJson(String(planWidget.value ?? ""));
    const runName = String(runWidget.value ?? "").trim();
    if (!runName) throw new Error("The H3 Chain Plan run_name is empty.");
    return {runName, clipCount: plan.shots.length};
}

function updatePlan(reviewNode, index, prompt, seed, length) {
    const planNode = upstreamPlanNode(reviewNode);
    const widget = planNode?.widgets?.find((item) => item.name === "plan_json");
    if (!widget) return false;
    const sceneIndex = Number(index) - 1;
    const plan = applyReviewEdit(
        parsePlanJson(String(widget.value ?? "")), index, prompt, seed, length,
    );
    const value = planToJson(plan);
    widget.value = value;
    widget.callback?.(value);
    refreshRestoredPlanEditors(planNode);
    planNode.graph?.setDirtyCanvas?.(true, true);
    publishCompanionPrompt(
        reviewNode,
        planNode,
        sceneIndex,
        promptValueToText(plan.shots[sceneIndex]?.prompt),
    );
    return true;
}

function planScenePrompt(reviewNode, review) {
    try {
        const planNode = upstreamPlanNode(reviewNode);
        const widget = widgetByName(planNode, "plan_json");
        if (!widget) return null;
        return reviewPlanScenePrompt(
            parsePlanJson(String(widget.value ?? "")),
            review?.clip_index,
            review?.shot_id,
        );
    } catch (_error) {
        return null;
    }
}

function updatePlanFromCheckpointRevisions(reviewNode, revisions) {
    const planNode = upstreamPlanNode(reviewNode);
    const widget = planNode?.widgets?.find((item) => item.name === "plan_json");
    if (!widget) return false;
    const plan = applyCheckpointRevisionSet(
        parsePlanJson(String(widget.value ?? "")), revisions,
    );
    const value = planToJson(plan);
    widget.value = value;
    widget.callback?.(value);
    refreshRestoredPlanEditors(planNode);
    planNode.graph?.setDirtyCanvas?.(true, true);
    for (const revision of revisions ?? []) {
        const sceneIndex = Number(revision?.scene) - 1;
        if (sceneIndex < 0 || sceneIndex >= plan.shots.length) continue;
        publishCompanionPrompt(
            reviewNode,
            planNode,
            sceneIndex,
            promptValueToText(plan.shots[sceneIndex]?.prompt),
        );
    }
    return true;
}

function restoreSavedPlanInputs(reviewNode, inputs, policyInputs = {}) {
    const planNode = upstreamPlanNode(reviewNode);
    if (!planNode || !inputs || typeof inputs !== "object") {
        throw new Error("The saved run has no Plan inputs to restore.");
    }
    const names = Object.keys(inputs).sort((left, right) =>
        Number(left === "plan_json") - Number(right === "plan_json"));
    const applied = [];
    const unavailable = [];
    const graph = planNode.graph ?? app.graph;
    graph?.beforeChange?.();
    try {
        for (const name of names) {
            const widget = widgetByName(planNode, name);
            if (!widget) {
                unavailable.push(name);
                continue;
            }
            widget.value = inputs[name];
            widget.callback?.(inputs[name]);
            applied.push(name);
        }
    } finally {
        graph?.afterChange?.();
    }
    if (!applied.includes("plan_json")) {
        throw new Error("The connected Plan does not expose an editable plan_json widget.");
    }
    const planWidget = widgetByName(planNode, "plan_json");
    const plan = parsePlanJson(String(planWidget?.value ?? ""));
    const policies = restoreConnectedPolicyInputs(
        planNode, policyInputs, inputs);
    refreshRestoredPlanEditors(planNode);
    app.graph?.setDirtyCanvas?.(true, true);
    for (const [sceneIndex, shot] of plan.shots.entries()) {
        publishCompanionPrompt(
            reviewNode,
            planNode,
            sceneIndex,
            promptValueToText(shot.prompt),
        );
    }
    return {sceneCount: plan.shots.length, unavailable, policies};
}

function formatBytes(value) {
    let size = Math.max(0, Number(value) || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    const digits = unit === 0 || size >= 100 ? 0 : size >= 10 ? 1 : 2;
    return `${size.toFixed(digits)} ${units[unit]}`;
}

function checkpointRevisionLabel(revision) {
    const active = revision.active ? "Active · " : "";
    const date = revision.createdAt
        ? new Date(revision.createdAt).toLocaleString() : "unknown time";
    const seed = revision.seed ? ` · seed ${revision.seed}` : "";
    return `${active}${date} · ${revision.revision.slice(0, 8)}${seed} · ${formatBytes(revision.sizeBytes)}`;
}

function prepareResume(reviewNode, nextIndex, endIndex = null, clipCount = null) {
    const startNode = findUpstreamNode(reviewNode, "MiniMaxH3ChainLoopStart") ??
        allNodes(app.graph).find((item) => nodeType(item) === "MiniMaxH3ChainLoopStart");
    const widget = startNode?.widgets?.find((item) => item.name === "start_clip");
    if (!widget) return false;
    widget.value = nextIndex;
    widget.callback?.(nextIndex);
    // An explicit range overrides start_clip in the backend. Checkpoint loads
    // clear it; immediate candidate acceptance preserves a shorter active
    // range while advancing to its next scene.
    const rangeWidget = startNode.widgets?.find((item) => item.name === "scene_range");
    if (rangeWidget) {
        const end = Number(endIndex);
        const total = Number(clipCount);
        const range = Number.isInteger(end) && Number.isInteger(total) &&
                nextIndex <= end && end < total
            ? nextIndex === end ? String(nextIndex) : `${nextIndex}:${end}`
            : "";
        rangeWidget.value = range;
        rangeWidget.callback?.(range);
    }
    startNode.graph?.setDirtyCanvas?.(true, true);
    return true;
}

async function activateAcceptedCandidate(reviewNode, submittedReview, body) {
    const endClip = Number(submittedReview.end_clip ?? submittedReview.clip_count);
    const clipIndex = Number(submittedReview.clip_index);
    if (!Number.isInteger(endClip) || clipIndex >= endClip) {
        return {
            immediate: false,
            message: "This is the last scene in the active range, so the current " +
                "take must reach Loop End before the selected checkpoint can finish it.",
        };
    }
    const execution = activeSceneExecutions.get(sceneExecutionKey(
        submittedReview.run_name, clipIndex));
    if (!execution?.promptId) {
        return {
            immediate: false,
            message: "The exact running prompt could not be identified; the " +
                "selection is armed and will stop at the next safe boundary.",
        };
    }
    const revisions = Array.isArray(body.resume_revisions)
        ? body.resume_revisions : [];
    if (revisions.length !== clipIndex) {
        return {
            immediate: false,
            message: "The selected checkpoint lineage could not be activated " +
                "immediately; the selection remains armed at the safe boundary.",
        };
    }
    const saved = updatePlan(
        reviewNode, clipIndex, body.scene_prompt, body.seed, body.length);
    if (!saved) {
        return {
            immediate: false,
            message: "The connected Plan could not be updated; the selection " +
                "remains armed at the safe boundary.",
        };
    }
    const activationResponse = await api.fetchApi(
        "/minimax_h3_context_loop/checkpoint-revisions/restore",
        await projectMutationOptions(
            reviewNode, submittedReview.run_name, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                run_name: submittedReview.run_name,
                resume_scene: Number(body.resume_scene),
                revisions,
                activate_only: true,
            }),
        }),
    );
    const activation = await activationResponse.json().catch(() => ({}));
    if (!activationResponse.ok) {
        return {
            immediate: false,
            message: `${activation.error || `HTTP ${activationResponse.status}`} ` +
                "The selection remains armed at the safe boundary.",
        };
    }

    if (execution.terminal === "success") {
        return {
            immediate: false,
            message: "The speculative prompt already completed; its Review " +
                "boundary is applying the armed selection now.",
        };
    }
    if (execution.terminal !== "interrupted" && execution.terminal !== "error") {
        const waiter = waitForReviewInterruption(execution.promptId);
        const cancelResponse = await api.fetchApi(
            `/api/jobs/${encodeURIComponent(execution.promptId)}/cancel`,
            {method: "POST"},
        );
        const cancelled = await cancelResponse.json().catch(() => ({}));
        if (!cancelResponse.ok) {
            waiter.cancel();
            return {
                immediate: false,
                message: `${cancelled.error ||
                    `Targeted cancellation failed (HTTP ${cancelResponse.status}).`} ` +
                    "The selection remains armed at the safe boundary.",
            };
        }
        if (!cancelled.cancelled) {
            waiter.cancel();
            return {
                immediate: false,
                message: "The speculative take finished during cancellation; the " +
                    "armed selection will be applied by Review Gate now.",
            };
        }
        await waiter.promise;
    }
    const nextIndex = clipIndex + 1;
    if (!prepareResume(
        reviewNode, nextIndex, endClip, Number(submittedReview.clip_count))) {
        throw new Error(
            `Candidate accepted and sampling stopped, but Loop Start could not ` +
            `be armed for scene ${nextIndex}. Queue it manually from that scene.`);
    }

    let cleanupWarning = "";
    try {
        const finalizeResponse = await api.fetchApi(
            "/minimax_h3_context_loop/review-candidate-batch",
            await projectMutationOptions(
                reviewNode, submittedReview.run_name, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    token: submittedReview.token,
                    action: "finalize",
                    candidate_revision: body.candidate_revision,
                }),
            }),
        );
        const finalize = await finalizeResponse.json().catch(() => ({}));
        if (!finalizeResponse.ok) {
            cleanupWarning = ` Candidate cleanup warning: ${finalize.error ||
                `HTTP ${finalizeResponse.status}`}`;
        } else if (Array.isArray(finalize.cleanup_warnings) &&
                finalize.cleanup_warnings.length) {
            cleanupWarning = ` Candidate cleanup warning: ${
                finalize.cleanup_warnings.join(" ")}`;
        }
    } catch (error) {
        cleanupWarning = ` Candidate cleanup warning: ${error.message}`;
    }
    await app.queuePrompt(0, 1);
    return {immediate: true, nextIndex, saved, cleanupWarning};
}

function fetchPending() {
    if (pendingFetchPromise) return pendingFetchPromise;
    pendingFetchPromise = (async () => {
        try {
            const response = await api.fetchApi("/minimax_h3_context_loop/reviews");
            if (!response.ok) return 0;
            const body = await response.json();
            const reviews = body.reviews ?? [];
            for (const review of reviews) routeReview(review);
            return reviews.length;
        } catch (error) {
            console.warn("[H3 Chain Review] Could not recover pending reviews:", error);
            return 0;
        } finally {
            pendingFetchPromise = null;
        }
    })();
    return pendingFetchPromise;
}

function reviewRunName(planNode) {
    if (!planNode) return "";
    const assets = planNode.inputs?.find((item) => item.name === "project_assets");
    if (assets?.link != null) {
        const link = planNode.graph?.links?.[assets.link];
        const manager = link
            ? planNode.graph?.getNodeById?.(link.origin_id) : null;
        // Project Assets is authoritative: it replaces Plan.run_name
        // server-side while the Plan editor deliberately keeps its hidden
        // widget unchanged. Do not fall back to a reused Review node id.
        if (nodeType(manager) === PROJECT_ASSET_MANAGER_NODE) {
            const run = String(widgetByName(manager, "run_name")?.value ?? "").trim();
            if (run) return run;
        }
    }
    return String(widgetByName(planNode, "run_name")?.value ?? "").trim();
}

function deliverReview(node, data) {
    if (!node || nodeType(node) !== NODE_NAME) return false;
    const expectedRun = String(data?.run_name ?? "").trim();
    if (expectedRun) {
        const actualRun = reviewRunName(findUpstreamNode(node, PLAN_NAMES));
        if (actualRun && actualRun !== expectedRun) return false;
    }
    if (typeof node._h3ReviewHandler === "function") {
        node._h3ReviewHandler(data);
    } else {
        // A websocket event can arrive between node creation and the DOM
        // widget's deferred mount. Preserve it instead of leaving a live
        // backend future with no token in the browser.
        node._h3QueuedReview = data;
    }
    return true;
}

function reviewFallbackNode(data) {
    const gates = [...new Set([
        ...allNodes(app.graph).filter((item) => nodeType(item) === NODE_NAME),
        ...[...mountedReviewNodes].filter((item) => nodeType(item) === NODE_NAME),
    ])];
    const expectedRun = String(data?.run_name ?? "").trim();
    if (expectedRun) {
        const matchingRun = gates.filter((item) =>
            reviewRunName(findUpstreamNode(item, PLAN_NAMES)) === expectedRun);

        if (matchingRun.length === 1) return matchingRun[0];
    }
    // Durable recovery inventory has no reliable display-node identity. It
    // may only route through its authoritative run match above.
    if (data?.durable === true) return null;
    // GraphBuilder execution ids use dots while subgraph-qualified display
    // ids use colons. The visible LiteGraph node is always the final leaf.
    const leaf = String(data?.node_id ?? "").split(/[.:]/).at(-1);
    const matchingLeaf = gates.filter((item) => String(item.id) === leaf);
    if (matchingLeaf.length === 1) return matchingLeaf[0];
    return gates.length === 1 ? gates[0] : null;
}

function routeReview(data) {
    const exact = findNodeByQualifiedId(data?.node_id);
    if (deliverReview(exact, data)) return true;
    const fallback = reviewFallbackNode(data);
    if (deliverReview(fallback, data)) {
        console.warn(
            `[H3 Chain Review] Display node ${data?.node_id} was not directly ` +
            "resolvable; routed the pending review to the only matching gate.",
        );
        return true;
    }
    if (data?.durable !== true) {
        console.warn(
            `[H3 Chain Review] Pending token ${data?.token ?? "?"} could not be ` +
            `routed to display node ${data?.node_id ?? "?"}.`,
        );
    }
    return false;
}

function routeReviewResolved(data) {
    const exact = findNodeByQualifiedId(data?.node_id);
    const node = nodeType(exact) === NODE_NAME ? exact : reviewFallbackNode(data);
    node?._h3ReviewResolvedHandler?.(data);
}

function updatePendingPolling() {
    if (mountedReviewNodes.size > 0 && pendingPollTimer == null) {
        // send_sync targets the browser session that queued the prompt. A
        // reconnect, reverse proxy, sleeping tab, or websocket race can miss
        // that event even though the backend review remains healthy. Polling
        // this tiny in-memory endpoint makes the pending token recoverable.
        pendingPollTimer = window.setInterval(() => {
            if (document.visibilityState !== "hidden") fetchPending();
        }, 2000);
    } else if (mountedReviewNodes.size === 0 && pendingPollTimer != null) {
        window.clearInterval(pendingPollTimer);
        pendingPollTimer = null;
    }
}

function mount(node) {
    if (node._h3ReviewMounted || typeof node.addDOMWidget !== "function") return;
    node._h3ReviewMounted = true;
    injectStyles();

    const root = document.createElement("div");
    root.className = "h3r-root";
    root.tabIndex = 0;
    root.title = "Review each persisted H3 scene with synchronized sound, then approve, retry, reroll, stop, or arm a saved checkpoint for resume.";
    // LiteGraph listens to pointer events on the canvas. Shield the complete
    // pointer sequence, not only mousedown: Firefox can otherwise let the
    // canvas capture pointerdown before a DOM-widget button receives click.
    for (const eventName of [
        "pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick",
    ]) {
        root.addEventListener(eventName, (event) => event.stopPropagation());
    }
    root.addEventListener("wheel", (event) => event.stopPropagation());

    const head = document.createElement("div");
    head.className = "h3r-head";
    const title = document.createElement("span");
    title.className = "h3r-title";
    title.textContent = "H3 Segment Review";
    const badge = document.createElement("span");
    badge.className = "h3r-badge";
    badge.textContent = "waiting for segment…";
    head.append(title, badge);

    const video = document.createElement("video");
    video.className = "h3r-video";
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    video.title = "Saved delivered scene preview. Review motion, continuity, and synchronized audio before choosing an action.";
    const videoPanel = document.createElement("div");
    videoPanel.className = "h3r-video-panel";
    videoPanel.title = "Resizable saved-scene preview.";
    const videoGrip = document.createElement("div");
    videoGrip.className = "h3r-video-grip";
    videoGrip.title = "Drag vertically to resize the video preview. Double-click to reset.";
    videoGrip.setAttribute("role", "separator");
    videoGrip.setAttribute("aria-label", "Resize video preview");
    videoGrip.setAttribute("aria-orientation", "horizontal");
    videoPanel.append(video, videoGrip);

    node.properties ??= {};
    const savedVideoHeight = Number(node.properties[VIDEO_HEIGHT_PROPERTY]);
    const initialVideoHeight = Number.isFinite(savedVideoHeight)
        ? Math.max(MIN_VIDEO_HEIGHT, Math.min(MAX_VIDEO_HEIGHT, savedVideoHeight))
        : DEFAULT_VIDEO_HEIGHT;
    videoPanel.style.height = `${initialVideoHeight}px`;

    let videoResize = null;
    function setVideoHeight(height, persist = false) {
        const next = Math.round(Math.max(
            MIN_VIDEO_HEIGHT,
            Math.min(MAX_VIDEO_HEIGHT, Number(height) || DEFAULT_VIDEO_HEIGHT),
        ));
        videoPanel.style.height = `${next}px`;
        videoGrip.setAttribute("aria-valuenow", String(next));
        if (persist) {
            node.properties[VIDEO_HEIGHT_PROPERTY] = next;
            node.graph?.setDirtyCanvas?.(true, true);
            app.graph?.setDirtyCanvas?.(true, true);
        }
    }
    setVideoHeight(initialVideoHeight);
    videoGrip.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        const layoutHeight = videoPanel.offsetHeight;
        const visualHeight = videoPanel.getBoundingClientRect().height;
        const displayScale = layoutHeight > 0 && visualHeight > 0
            ? visualHeight / layoutHeight : 1;
        videoResize = {
            pointerId: event.pointerId,
            startY: event.clientY,
            startHeight: layoutHeight || DEFAULT_VIDEO_HEIGHT,
            displayScale,
        };
        videoGrip.setPointerCapture?.(event.pointerId);
    });
    videoGrip.addEventListener("pointermove", (event) => {
        if (!videoResize || event.pointerId !== videoResize.pointerId) return;
        event.preventDefault();
        setVideoHeight(videoResize.startHeight
            + (event.clientY - videoResize.startY) / videoResize.displayScale);
    });
    function finishVideoResize(event) {
        if (!videoResize || event.pointerId !== videoResize.pointerId) return;
        videoResize = null;
        // offsetHeight is an unscaled layout value. getBoundingClientRect()
        // includes ComfyUI canvas zoom and caused every release to compound a
        // smaller screen-space height into the saved CSS height.
        setVideoHeight(videoPanel.offsetHeight, true);
        videoGrip.releasePointerCapture?.(event.pointerId);
    }
    videoGrip.addEventListener("pointerup", finishVideoResize);
    videoGrip.addEventListener("pointercancel", finishVideoResize);
    videoGrip.addEventListener("dblclick", (event) => {
        event.preventDefault();
        setVideoHeight(DEFAULT_VIDEO_HEIGHT, true);
    });

    const captureRow = document.createElement("div");
    captureRow.className = "h3r-capture-row";
    const captureButton = document.createElement("button");
    captureButton.type = "button";
    captureButton.className = "h3r-capture-button";
    captureButton.textContent = "Capture frame…";
    captureButton.title = "Scrub the preview above to the desired frame, then save it as a project asset in the Asset Carousel.";
    const captureStatus = document.createElement("span");
    captureStatus.className = "h3r-capture-status";
    captureRow.append(captureButton, captureStatus);

    async function fetchExistingTags(project) {
        const response = await api.fetchApi(
            `/minimax_h3_context_loop/project-assets?${new URLSearchParams({project, create: "false"})}`);
        const catalog = await response.json();
        if (!response.ok) throw new Error(catalog.error || `HTTP ${response.status}`);
        return [...new Set((catalog.assets ?? [])
            .map((item) => String(item.tag || "").trim()).filter(Boolean))];
    }

    let captureBusy = false;
    function closeCaptureDialog() {
        root.querySelector(".h3r-capture-dialog")?.remove();
        video.pause();
    }

    async function openCaptureDialog() {
        if (captureBusy) return;
        if (!canCaptureFrame(video)) {
            captureStatus.textContent = "Wait for a saved preview frame to finish loading or seeking.";
            return;
        }
        const item = {...video.h3CaptureItem};
        const sourceGraph = node.graph;
        const carousels = captureCarousels(sourceGraph);
        video.pause();
        const captureTime = video.currentTime;
        let project = "";
        try {
            project = captureTargetProject(node);
        } catch (_error) {
            project = "";
        }
        closeCaptureDialog();

        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 360;
        const context2d = canvas.getContext("2d");
        try { context2d?.drawImage(video, 0, 0, canvas.width, canvas.height); }
        catch (_error) { /* tainted or unavailable frame; dialog still works */ }

        const overlay = document.createElement("div");
        overlay.className = "h3r-capture-dialog";
        const card = document.createElement("div");
        card.className = "h3r-capture-card";
        const title = document.createElement("div");
        title.className = "h3r-capture-title";
        title.textContent = `Save frame at ${captureTime.toFixed(2)}s`;
        const projectField = document.createElement("label");
        projectField.className = "h3r-capture-field";
        projectField.append("Carousel project");
        const projectInput = document.createElement("input");
        projectInput.className = "h3r-capture-tag";
        projectInput.value = project;
        projectInput.placeholder = "e.g. sammys_house";
        projectInput.title = "The Asset Carousel node's own project name, which can differ " +
            "from the Plan's run_name. Only an unambiguous upstream project is selected " +
            "automatically. Otherwise enter the destination project explicitly.";
        projectField.append(projectInput);
        const preview = document.createElement("img");
        preview.className = "h3r-capture-preview";
        try { preview.src = canvas.toDataURL("image/png"); } catch (_error) {}
        const tagField = document.createElement("label");
        tagField.className = "h3r-capture-field";
        tagField.append("Tag");
        const tagInput = document.createElement("input");
        tagInput.className = "h3r-capture-tag";
        tagInput.placeholder = "e.g. hero_pose";
        tagInput.autocomplete = "off";
        const tagPickerRow = document.createElement("div");
        tagPickerRow.className = "h3r-capture-tag-row";
        const tagPickerButton = document.createElement("button");
        tagPickerButton.type = "button";
        tagPickerButton.className = "h3r-capture-tag-picker";
        tagPickerButton.textContent = "▾";
        tagPickerButton.title = "Choose from existing tags";
        const tagMenu = document.createElement("div");
        tagMenu.className = "h3r-capture-tag-menu";
        tagMenu.hidden = true;
        let knownTags = [];
        let tagLookup = 0;
        let tagLookupStatus = "Enter a destination project.";
        async function loadTags() {
            const sequence = ++tagLookup;
            const target = projectInput.value.trim();
            knownTags = [];
            tagLookupStatus = target ? "Loading tags…" : "Enter a destination project.";
            renderTagMenu(tagInput.value);
            if (!target) return;
            try {
                const tags = await fetchExistingTags(target);
                if (sequence !== tagLookup || !overlay.isConnected) return;
                knownTags = tags;
                tagLookupStatus = "No tags yet.";
            } catch (lookupError) {
                if (sequence !== tagLookup || !overlay.isConnected) return;
                tagLookupStatus = `Could not load tags: ${lookupError.message || lookupError}`;
            }
            renderTagMenu(tagInput.value);
        }
        projectInput.addEventListener("input", () => { void loadTags(); });
        function renderTagMenu(filter = "") {
            const needle = filter.trim().toLowerCase();
            const matches = needle
                ? knownTags.filter((tag) => tag.toLowerCase().includes(needle))
                : knownTags;
            tagMenu.replaceChildren(...matches.map((tag) => {
                const option = document.createElement("button");
                option.type = "button";
                option.className = "h3r-capture-tag-option";
                option.textContent = tag;
                option.addEventListener("mousedown", (event) => event.preventDefault());
                option.addEventListener("click", () => {
                    tagInput.value = tag;
                    tagMenu.hidden = true;
                    tagInput.focus();
                });
                return option;
            }));
            if (!matches.length) {
                const empty = document.createElement("div");
                empty.className = "h3r-capture-tag-empty";
                empty.textContent = knownTags.length ? "No matching tags." : tagLookupStatus;
                tagMenu.append(empty);
            }
        }
        tagPickerButton.addEventListener("mousedown", (event) => event.preventDefault());
        tagPickerButton.addEventListener("click", () => {
            const opening = tagMenu.hidden;
            tagInput.focus();
            tagMenu.hidden = !opening;
            if (opening) renderTagMenu(tagInput.value);
        });
        tagInput.addEventListener("input", () => {
            tagMenu.hidden = false;
            renderTagMenu(tagInput.value);
        });
        tagPickerRow.addEventListener("focusout", (event) => {
            if (!tagPickerRow.contains(event.relatedTarget)) tagMenu.hidden = true;
        });
        tagPickerRow.append(tagInput, tagPickerButton, tagMenu);
        tagField.append(tagPickerRow);
        const hint = document.createElement("div");
        hint.className = "h3r-capture-hint";
        hint.textContent = "Choose an existing tag (or type a new one) to save this as an " +
            "updated take — a number is appended automatically (e.g. char-sammy1, " +
            "char-sammy2, ...) so every take stays in the Carousel.";
        const error = document.createElement("div");
        error.className = "h3r-capture-error";
        error.hidden = true;
        const actionsRow = document.createElement("div");
        actionsRow.className = "h3r-capture-actions";
        const cancelButton = document.createElement("button");
        cancelButton.type = "button";
        cancelButton.className = "h3r-button";
        cancelButton.textContent = "Cancel";
        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.className = "h3r-button";
        saveButton.textContent = "Save to Carousel";
        actionsRow.append(cancelButton, saveButton);
        card.append(title, projectField, preview, tagField, hint, error, actionsRow);
        overlay.append(card);
        root.append(overlay);
        tagInput.focus();

        void loadTags();

        cancelButton.addEventListener("click", () => { if (!captureBusy) overlay.remove(); });
        overlay.addEventListener("click", (event) => {
            if (!captureBusy && event.target === overlay) overlay.remove();
        });
        saveButton.addEventListener("click", async () => {
            if (captureBusy) return;
            const tag = tagInput.value.trim();
            const targetProject = projectInput.value.trim();
            error.hidden = true;
            if (!targetProject) {
                error.textContent = "Carousel project cannot be blank.";
                error.hidden = false;
                projectInput.focus();
                return;
            }
            if (node.graph !== sourceGraph) {
                error.textContent = "The Review Gate changed workflows. Reopen frame capture.";
                error.hidden = false;
                return;
            }
            captureBusy = true;
            for (const control of [saveButton, cancelButton, projectInput, tagInput,
                tagPickerButton, captureButton]) control.disabled = true;
            tagMenu.hidden = true;
            saveButton.textContent = "Saving…";
            try {
                const requestOptions = await projectMutationOptions(node, targetProject, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        project: targetProject,
                        filename: item.filename,
                        subfolder: item.subfolder ?? "",
                        type: item.type ?? "output",
                        time_seconds: captureTime,
                        tag,
                    }),
                });
                if (node.graph !== sourceGraph) {
                    throw new Error("The Review Gate changed workflows. Reopen frame capture.");
                }
                const response = await api.fetchApi(
                    "/minimax_h3_context_loop/project-assets/capture-frame", requestOptions);
                const body = await response.json();
                if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
                const savedProject = body.catalog?.project ?? targetProject;
                captureStatus.textContent =
                    `Saved @${body.asset?.tag ?? tag} to the ${savedProject} Asset Carousel.`;
                overlay.remove();
                // Refresh is not part of the save transaction. If it fails,
                // do not offer to save again and create a duplicate asset.
                const refreshes = await Promise.allSettled(carousels.filter((carousel) =>
                    carousel.graph && carouselProject(carousel) === savedProject
                ).map((carousel) => Promise.resolve().then(() => carousel._h3ProjectAssetRefresh?.())));
                if (refreshes.some((result) => result.status === "rejected")) {
                    captureStatus.textContent += " Refresh the Carousel to see it.";
                }
            } catch (captureError) {
                error.textContent = captureError.message || String(captureError);
                error.hidden = false;
            } finally {
                captureBusy = false;
                for (const control of [saveButton, cancelButton, projectInput, tagInput,
                    tagPickerButton, captureButton]) control.disabled = false;
                saveButton.textContent = "Save to Carousel";
            }
        });
    }

    captureButton.addEventListener("click", () => { void openCaptureDialog(); });

    const prefix = document.createElement("pre");
    prefix.className = "h3r-prefix";
    prefix.hidden = true;
    prefix.title = "Shared prompt prepended to every scene. It is shown for context and is not changed by retrying this scene.";

    const promptLabel = document.createElement("label");
    promptLabel.className = "h3r-label";
    promptLabel.append("Scene prompt (used when retrying)");
    const prompt = document.createElement("textarea");
    prompt.className = "h3r-prompt";
    prompt.title = "The connected Prompt Editor and this fallback field share the current Plan scene. Retry regenerates it from the same accepted predecessor.";
    const promptPanel = document.createElement("div");
    promptPanel.className = "h3r-prompt-panel";
    const promptGrip = document.createElement("div");
    promptGrip.className = "h3r-prompt-grip";
    promptGrip.title = "Drag vertically to resize the prompt editor. Double-click to reset.";
    promptGrip.setAttribute("role", "separator");
    promptGrip.setAttribute("aria-label", "Resize scene prompt editor");
    promptGrip.setAttribute("aria-orientation", "horizontal");
    promptPanel.append(prompt, promptGrip);
    promptLabel.append(promptPanel);
    let promptEditedInGate = false;
    prompt.addEventListener("input", () => { promptEditedInGate = true; });

    const promptNotice = document.createElement("div");
    promptNotice.className = "h3r-prompt-notice";
    promptNotice.textContent = "Prompt editing in Review Gate is disabled by default in 0.5. Use Scene Prompt Editor or Rich Scene Prompt Editor, then Retry or Reroll here.\n\nTo restore the old field: ComfyUI Settings → MiniMax H3 Context Loop → Interface → Review Gate.";

    function refreshPromptEditorSetting() {
        const enabled = reviewPromptEditorEnabled();
        promptLabel.hidden = !enabled;
        prompt.disabled = !enabled;
        promptGrip.hidden = !enabled;
        promptNotice.hidden = enabled;
    }
    node._h3ReviewRefreshPromptSetting = refreshPromptEditorSetting;
    refreshPromptEditorSetting();

    function setPromptHeight(height, persist = false) {
        const next = Math.round(Math.max(
            MIN_PROMPT_HEIGHT,
            Math.min(MAX_PROMPT_HEIGHT, Number(height) || DEFAULT_PROMPT_HEIGHT),
        ));
        promptPanel.style.height = `${next}px`;
        promptGrip.setAttribute("aria-valuenow", String(next));
        if (persist) {
            node.properties[PROMPT_HEIGHT_PROPERTY] = next;
            node.graph?.setDirtyCanvas?.(true, true);
            app.graph?.setDirtyCanvas?.(true, true);
        }
    }
    let promptResize = null;
    promptGrip.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        const layoutHeight = promptPanel.offsetHeight;
        const visualHeight = promptPanel.getBoundingClientRect().height;
        const displayScale = layoutHeight > 0 && visualHeight > 0
            ? visualHeight / layoutHeight : 1;
        promptResize = {
            pointerId: event.pointerId,
            startY: event.clientY,
            startHeight: layoutHeight || DEFAULT_PROMPT_HEIGHT,
            displayScale,
        };
        promptGrip.setPointerCapture?.(event.pointerId);
    });
    promptGrip.addEventListener("pointermove", (event) => {
        if (!promptResize || event.pointerId !== promptResize.pointerId) return;
        event.preventDefault();
        setPromptHeight(promptResize.startHeight
            + (event.clientY - promptResize.startY) / promptResize.displayScale);
    });
    function finishPromptResize(event) {
        if (!promptResize || event.pointerId !== promptResize.pointerId) return;
        promptResize = null;
        setPromptHeight(promptPanel.offsetHeight, true);
        promptGrip.releasePointerCapture?.(event.pointerId);
    }
    promptGrip.addEventListener("pointerup", finishPromptResize);
    promptGrip.addEventListener("pointercancel", finishPromptResize);
    promptGrip.addEventListener("dblclick", (event) => {
        event.preventDefault();
        setPromptHeight(DEFAULT_PROMPT_HEIGHT, true);
    });

    function applySavedLayout() {
        node.properties ??= {};
        const restoredVideoHeight = Number(node.properties[VIDEO_HEIGHT_PROPERTY]);
        setVideoHeight(Number.isFinite(restoredVideoHeight)
            ? restoredVideoHeight : DEFAULT_VIDEO_HEIGHT);
        const restoredPromptHeight = Number(node.properties[PROMPT_HEIGHT_PROPERTY]);
        setPromptHeight(Number.isFinite(restoredPromptHeight)
            ? restoredPromptHeight : DEFAULT_PROMPT_HEIGHT);
    }
    node._h3ReviewApplyLayout = applySavedLayout;
    applySavedLayout();

    const seedRow = document.createElement("div");
    seedRow.className = "h3r-row";
    const seedField = document.createElement("label");
    seedField.className = "h3r-field h3r-field-seed";
    seedField.append("Seed");
    const seed = document.createElement("input");
    seed.className = "h3r-seed";
    seed.inputMode = "numeric";
    seed.title = "Unsigned 64-bit seed for the current scene. Edit it before Retry, or use Reroll seed to generate a new value automatically.";
    seedField.append(seed);
    const durationField = document.createElement("label");
    durationField.className = "h3r-field h3r-field-duration";
    durationField.append("Duration (s)");
    const duration = document.createElement("input");
    duration.className = "h3r-duration";
    duration.type = "number";
    duration.inputMode = "decimal";
    duration.min = String(5 / 24);
    duration.max = String(3592 / 24);
    duration.step = String(17 / 24);
    duration.title = "Generated scene duration. Retry and Reroll round this upward to H3's exact 17k+5 frame grid, revise the full Plan, and retime downstream scenes. Prompt wording and written timestamps are not changed.";
    durationField.append(duration);
    seedRow.append(seedField, durationField);

    const candidateRow = document.createElement("div");
    candidateRow.className = "h3r-candidates";
    candidateRow.hidden = true;
    candidateRow.title = "Preview each saved take. The active candidate's exact video and audio continuation tensors become the next scene's checkpoint state. Every candidate remains saved by default; uncheck only takes you intentionally want removed after accepting another take.";
    const candidateNav = document.createElement("div");
    candidateNav.className = "h3r-candidate-nav";
    const candidatePrevious = document.createElement("button");
    candidatePrevious.type = "button";
    candidatePrevious.className = "h3r-button h3r-candidate-arrow";
    candidatePrevious.textContent = "←";
    candidatePrevious.title = "Preview the previous saved candidate.";
    const candidateTitle = document.createElement("span");
    candidateTitle.className = "h3r-candidate-title";
    candidateTitle.textContent = "Candidate";
    const candidateNext = document.createElement("button");
    candidateNext.type = "button";
    candidateNext.className = "h3r-button h3r-candidate-arrow";
    candidateNext.textContent = "→";
    candidateNext.title = "Preview the next saved candidate.";
    candidateNav.append(candidatePrevious, candidateTitle, candidateNext);
    const candidateMeta = document.createElement("div");
    candidateMeta.className = "h3r-candidate-meta";
    const candidateKeepLabel = document.createElement("label");
    candidateKeepLabel.className = "h3r-candidate-keep";
    const candidateKeep = document.createElement("input");
    candidateKeep.type = "checkbox";
    candidateKeep.title = "Keep this saved take in checkpoint history. Candidates are checked by default; an unchecked, unselected take is deleted only when you confirm acceptance.";
    candidateKeepLabel.append(candidateKeep, "Keep saved");
    const candidateProgress = document.createElement("span");
    candidateProgress.className = "h3r-candidate-progress";
    candidateMeta.append(candidateKeepLabel, candidateProgress);
    const candidateDots = document.createElement("div");
    candidateDots.className = "h3r-candidate-dots";
    candidateDots.setAttribute("role", "tablist");
    candidateRow.append(candidateNav, candidateMeta, candidateDots);

    const actions = document.createElement("div");
    actions.className = "h3r-actions";
    const actionButtons = [];
    function actionButton(label, className, action) {
        const button = document.createElement("button");
        button.className = `h3r-button ${className}`;
        button.textContent = label;
        button.type = "button";
        button.title = {
            approve: "Accept this saved scene and continue the loop with the next scene.",
            next_candidate: "Keep any marked takes, resume the workflow, and generate the next candidate for this scene.",
            retry: "Reject this attempt and regenerate the same scene using the active Plan prompt, seed, and duration.",
            reroll: "Reject this attempt, assign a new random seed, and regenerate the same scene using the active Plan prompt and duration.",
            stop: "Accept this scene but stop before the next one. Optionally assemble a partial joined MP4 and arm the next scene for resume.",
        }[action] ?? "Submit this review decision.";
        // Keep actions clickable while waiting. If a websocket event or node-id
        // route was missed, submit() can recover the live token through the
        // pending-review endpoint and perform the requested action immediately.
        button.disabled = false;
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            void submit(action);
        });
        actions.append(button);
        actionButtons.push(button);
        return button;
    }
    const approveButton = actionButton(
        "Approve & continue", "h3r-approve", "approve");
    const nextCandidateButton = actionButton(
        "Generate next candidate", "h3r-retry", "next_candidate");
    nextCandidateButton.hidden = true;
    const retryButton = actionButton(
        "Retry scene / seed / length", "h3r-retry", "retry");
    const rerollButton = actionButton(
        "Reroll seed", "h3r-retry", "reroll");
    const stopButton = actionButton(
        "Approve & stop", "h3r-stop", "stop");

    const status = document.createElement("div");
    status.className = "h3r-status";
    status.textContent = "The loop will pause here after each saved segment.";

    const deferred = document.createElement("section");
    deferred.className = "h3r-resume";
    const deferredTitle = document.createElement("div");
    deferredTitle.className = "h3r-resume-title";
    deferredTitle.textContent = "Pending candidate reviews";
    const deferredRow = document.createElement("div");
    deferredRow.className = "h3r-resume-row";
    const deferredSelect = document.createElement("select");
    deferredSelect.className = "h3r-resume-select";
    deferredSelect.title = "Saved candidate batches waiting for a decision in the connected Plan's run.";
    const refreshDeferred = document.createElement("button");
    refreshDeferred.type = "button";
    refreshDeferred.className = "h3r-button";
    refreshDeferred.textContent = "Refresh";
    refreshDeferred.title = "Scan this run for candidate batches saved by MiniMax H3 Pending Review.";
    const openDeferred = document.createElement("button");
    openDeferred.type = "button";
    openDeferred.className = "h3r-button h3r-approve";
    openDeferred.textContent = "Review";
    openDeferred.title = "Load the selected persisted batch into this Review Gate.";
    openDeferred.disabled = true;
    const deferredStatus = document.createElement("div");
    deferredStatus.className = "h3r-resume-status";
    deferredStatus.textContent = "Refresh to find batches saved for later review.";
    deferredRow.append(deferredSelect, refreshDeferred, openDeferred);
    deferred.append(deferredTitle, deferredRow, deferredStatus);

    const resume = document.createElement("section");
    resume.className = "h3r-resume";
    const resumeTitle = document.createElement("div");
    resumeTitle.className = "h3r-resume-title";
    resumeTitle.textContent = "Resume from saved checkpoint";
    const resumeRow = document.createElement("div");
    resumeRow.className = "h3r-resume-row";
    const resumeSelect = document.createElement("select");
    resumeSelect.className = "h3r-resume-select";
    resumeSelect.title = "Choose the next scene to render. Its immediately preceding saved checkpoint supplies the visual and AV continuation state.";
    const refreshResume = document.createElement("button");
    refreshResume.type = "button";
    refreshResume.className = "h3r-button";
    refreshResume.textContent = "Refresh";
    refreshResume.title = "Scan this run's checkpoint folder again and list valid resume positions.";
    const loadResume = document.createElement("button");
    loadResume.type = "button";
    loadResume.className = "h3r-button h3r-approve";
    loadResume.textContent = "Load checkpoint";
    loadResume.title = "Restore the saved run's full Plan, preview the selected predecessor checkpoint, and set H3 Chain Loop Start to the chosen resume scene. Queue the workflow afterward to actually resume.";
    const resumeStatus = document.createElement("div");
    resumeStatus.className = "h3r-resume-status";
    resumeStatus.textContent = "Refresh to discover saved scenes for this run.";
    const revisionsPanel = document.createElement("div");
    revisionsPanel.className = "h3r-revisions";
    revisionsPanel.hidden = true;
    const revisionsTitle = document.createElement("div");
    revisionsTitle.className = "h3r-revisions-title";
    revisionsTitle.textContent = "Checkpoint history";
    const revisionsHint = document.createElement("div");
    revisionsHint.className = "h3r-resume-status";
    revisionsHint.textContent = "All saved scenes are listed. Only scenes before the selected resume position are restored.";
    const revisionsRows = document.createElement("div");
    revisionsRows.className = "h3r-revision-rows";
    revisionsPanel.append(revisionsTitle, revisionsHint, revisionsRows);
    resumeRow.append(resumeSelect, refreshResume, loadResume);
    resume.append(resumeTitle, resumeRow, resumeStatus, revisionsPanel);

    root.append(
        head, videoPanel, captureRow, prefix, promptNotice, promptLabel,
        seedRow, candidateRow, actions, status, deferred, resume,
    );

    let current = null;
    let deferredReviews = [];
    let deferredRefreshToken = 0;
    let countdownTimer = null;
    let resumeChoices = [];
    let checkpointRevisions = [];
    let revisionChain = [];
    let planClipCount = 0;
    let resumeRefreshToken = 0;
    let resumeRefreshPromise = null;
    let queuedAutomaticResumeRefresh = false;
    let queuedExplicitResumeRefresh = false;
    let deferredAutomaticResumeRefresh = false;
    const activeResumeExecutionIds = new Set();
    let activeCandidateRevision = "";
    let keptCandidateRevisions = new Set();
    let previewLoadRevision = 0;
    let acceptedPreviewPin = null;

    function setReviewVideo(item, preservePosition = false) {
        if (!item?.filename) return false;
        const source = videoUrl(item);
        if (video.dataset.source === source) return false;
        const savedTime = preservePosition && Number.isFinite(video.currentTime)
            ? Math.max(0, video.currentTime) : 0;
        const resumePlayback = preservePosition && !video.paused;
        const revision = ++previewLoadRevision;
        video.dataset.source = source;
        video.h3CaptureItem = item;
        video.src = source;
        video.load();
        if (preservePosition) {
            video.addEventListener("loadedmetadata", () => {
                if (revision !== previewLoadRevision ||
                        video.dataset.source !== source) return;
                const limit = Number.isFinite(video.duration)
                    ? Math.max(0, video.duration - .02) : savedTime;
                try { video.currentTime = Math.min(savedTime, limit); }
                catch (_error) {}
                if (resumePlayback) void video.play().catch(() => {});
            }, {once:true});
        }
        return true;
    }

    function showAcceptedPreview() {
        if (!acceptedPreviewPin?.video) return;
        activeCandidateRevision = acceptedPreviewPin.revision;
        setReviewVideo(acceptedPreviewPin.video, true);
        badge.textContent = `accepted scene ${acceptedPreviewPin.clipIndex} · ` +
            `revision ${acceptedPreviewPin.revision.slice(0, 8)}`;
    }

    function pinAcceptedPreview(review, candidate) {
        if (!candidate?.video || !candidate?.revision) return;
        acceptedPreviewPin = {
            runName: String(review?.run_name ?? ""),
            clipIndex: Number(review?.clip_index),
            token: String(review?.token ?? ""),
            revision: String(candidate.revision),
            video: candidate.video,
        };
        showAcceptedPreview();
    }

    function setActionsEnabled(enabled) {
        // Recovered durable inventory has no live PromptExecutor future.
        // It is intentionally read-only rather than offering decisions that
        // are guaranteed to fail after a server restart.
        if (current?.actionable === false) enabled = false;
        for (const button of actionButtons) button.disabled = !enabled;
        if (enabled && current?.candidate_batch_active) {
            retryButton.disabled = true;
            rerollButton.disabled = true;
            stopButton.disabled = true;
        }
    }

    function selectedCandidate() {
        const candidates = Array.isArray(current?.candidates)
            ? current.candidates : [];
        return candidates.find(
            (candidate) => candidate.revision === activeCandidateRevision,
        ) ?? candidates.at(-1) ?? null;
    }

    function showCandidate(candidate, announce = false) {
        if (!candidate) return;
        const sameCandidate = activeCandidateRevision === candidate.revision;
        activeCandidateRevision = candidate.revision;
        if (candidate.video) {
            setReviewVideo(candidate.video, sameCandidate);
        }
        seed.value = String(candidate.seed ?? seed.value);
        candidateTitle.textContent = `Candidate ${candidate.number}/${current.candidate_count} · ` +
            `seed ${candidate.seed} · ${candidate.revision.slice(0, 8)}`;
        candidateKeep.checked = keptCandidateRevisions.has(candidate.revision);
        const candidates = Array.isArray(current?.candidates)
            ? current.candidates : [];
        const selectedIndex = Math.max(0, candidates.indexOf(candidate));
        candidatePrevious.disabled = selectedIndex <= 0;
        candidateNext.disabled = selectedIndex >= candidates.length - 1;
        badge.textContent = `clip ${current.clip_index}/${current.clip_count} · ` +
            `candidate ${candidate.number}/${current.candidate_count} · ${current.shot_id}`;
        renderCandidateDots();
        renderCandidateProgress();
        if (announce) {
            status.className = `h3r-status${candidate.warning ? " h3r-warning" : ""}`;
            status.textContent = candidate.warning ||
                `Previewing candidate ${candidate.number}/${current.candidate_count}, seed ${candidate.seed}.`;
        }
    }

    function renderCandidateDots() {
        const candidates = Array.isArray(current?.candidates)
            ? current.candidates : [];
        candidateDots.replaceChildren();
        for (const candidate of candidates) {
            const dot = document.createElement("button");
            dot.type = "button";
            dot.className = "h3r-candidate-dot";
            if (candidate.revision === activeCandidateRevision) {
                dot.classList.add("h3r-selected");
            }
            if (keptCandidateRevisions.has(candidate.revision)) {
                dot.classList.add("h3r-kept");
            }
            dot.title = `Preview candidate ${candidate.number}, seed ${candidate.seed}` +
                (keptCandidateRevisions.has(candidate.revision) ? " (kept)" : "");
            dot.setAttribute("aria-label", dot.title);
            dot.addEventListener("click", () => showCandidate(candidate, true));
            candidateDots.append(dot);
        }
    }

    function renderCandidateProgress() {
        const generated = Array.isArray(current?.candidates)
            ? current.candidates.length : 0;
        const target = Number(current?.candidate_count) || generated;
        const remaining = Math.max(0, target - generated);
        candidateProgress.textContent = `${generated}/${target} generated · ` +
            `${keptCandidateRevisions.size} marked to keep` +
            (remaining ? ` · ${remaining} remaining` : " · complete");
    }

    function renderCandidateCarousel() {
        const candidates = Array.isArray(current?.candidates)
            ? current.candidates : [];
        const batch = Number(current?.candidate_count) > 1;
        candidateRow.hidden = !batch;
        const complete = Boolean(current?.candidate_generation_complete) ||
            candidates.length >= Number(current?.candidate_count);
        const running = Boolean(current?.candidate_batch_active) && !complete;
        nextCandidateButton.hidden = !running && (
            !batch || complete || !current?.review_each_candidate);
        nextCandidateButton.textContent = running
            ? "Pause candidate run" : "Generate next candidate";
        nextCandidateButton.title = running
            ? "Let the current in-flight take finish, then pause before another candidate starts."
            : "Keep any marked takes, resume the workflow, and generate the next candidate for this scene.";
        approveButton.textContent = current?.deferred_review
            ? "Use this take & prepare resume"
            : batch
            ? running ? "Use this take & stop batch"
                : complete ? "Use this take & continue" : "Accept now & continue"
            : "Approve & continue";
        approveButton.title = current?.deferred_review
            ? "Activate this saved checkpoint lineage, keep the marked alternatives, and prepare Loop Start at the next scene."
            : running
            ? "Accept this saved take now, cancel only the speculative in-flight " +
                "H3 prompt, activate its checkpoint, and continue at the next scene."
            : "Accept this saved scene and continue the loop with the next scene.";
        if (!batch || !candidates.length) return null;
        if (!candidates.some(
            (candidate) => candidate.revision === activeCandidateRevision)) {
            activeCandidateRevision = candidates.at(-1).revision;
        }
        const selected = selectedCandidate();
        renderCandidateDots();
        renderCandidateProgress();
        return selected;
    }

    function moveCandidate(offset) {
        const candidates = Array.isArray(current?.candidates)
            ? current.candidates : [];
        const index = candidates.findIndex(
            (candidate) => candidate.revision === activeCandidateRevision);
        const candidate = candidates[index + offset];
        if (candidate) showCandidate(candidate, true);
    }

    candidatePrevious.addEventListener("click", () => moveCandidate(-1));
    candidateNext.addEventListener("click", () => moveCandidate(1));
    root.addEventListener("keydown", (event) => {
        if (candidateRow.hidden || event.altKey || event.ctrlKey || event.metaKey) return;
        if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(
            event.target?.tagName)) return;
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        moveCandidate(event.key === "ArrowLeft" ? -1 : 1);
    });
    candidateKeep.addEventListener("change", () => {
        const candidate = selectedCandidate();
        if (!candidate) return;
        if (candidateKeep.checked) keptCandidateRevisions.add(candidate.revision);
        else keptCandidateRevisions.delete(candidate.revision);
        renderCandidateDots();
        renderCandidateProgress();
        if (current?.candidate_batch_active && current?.token) {
            void projectMutationOptions(node, current.run_name, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        token: current.token,
                        action: "update",
                        candidate_revisions: [...keptCandidateRevisions],
                    }),
                }).then((options) => api.fetchApi(
                    "/minimax_h3_context_loop/review-candidate-batch", options,
                )).then(async (response) => {
                if (response.ok) return;
                const body = await response.json().catch(() => ({}));
                throw new Error(body.error || `HTTP ${response.status}`);
            }).catch((error) => {
                status.className = "h3r-status h3r-warning";
                status.textContent = `Could not retain the live keep marks: ${error.message}`;
            });
        }
    });

    function renderDeferredChoices() {
        const selectedToken = deferredSelect.value;
        deferredSelect.replaceChildren();
        for (const review of deferredReviews) {
            const option = document.createElement("option");
            option.value = String(review.token ?? "");
            const count = Array.isArray(review.candidates)
                ? review.candidates.length : Number(review.candidate_count) || 0;
            option.textContent = `Scene ${review.clip_index} · ${review.shot_id} · ` +
                `${count} candidate${count === 1 ? "" : "s"}`;
            deferredSelect.append(option);
        }
        if (deferredReviews.some((review) => review.token === selectedToken)) {
            deferredSelect.value = selectedToken;
        }
        openDeferred.disabled = deferredReviews.length === 0;
        deferredStatus.textContent = deferredReviews.length
            ? `${deferredReviews.length} pending batch${deferredReviews.length === 1 ? "" : "es"} found for this run.`
            : "No pending candidate batch was found for this run.";
    }

    async function refreshDeferredReviews() {
        const refreshToken = ++deferredRefreshToken;
        try {
            const context = planResumeContext(node);
            const query = new URLSearchParams({run_name: context.runName});
            const response = await api.fetchApi(
                `/minimax_h3_context_loop/deferred-reviews?${query.toString()}`,
            );
            const body = await response.json();
            if (refreshToken !== deferredRefreshToken) return;
            if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
            deferredReviews = Array.isArray(body.reviews) ? body.reviews : [];
            renderDeferredChoices();
        } catch (error) {
            if (refreshToken !== deferredRefreshToken) return;
            deferredReviews = [];
            renderDeferredChoices();
            deferredStatus.textContent = error.message;
        }
    }

    refreshDeferred.addEventListener("click", refreshDeferredReviews);
    openDeferred.addEventListener("click", () => {
        const review = deferredReviews.find(
            (item) => String(item.token ?? "") === deferredSelect.value,
        );
        if (review) node._h3ReviewHandler?.(review);
    });
    node._h3RefreshDeferred = refreshDeferredReviews;

    function selectedRevisionChain() {
        const selectedResumeScene = Number(resumeSelect.value);
        if (!Number.isInteger(selectedResumeScene) || selectedResumeScene < 2) {
            return [];
        }
        return revisionChain.map((group) => {
            const select = revisionsRows.querySelector(
                `select[data-scene="${group.scene}"]`,
            );
            const revision = group.revisions.find(
                (item) => item.revision === select?.value,
            );
            return revision ?? group.revisions[0];
        }).filter((revision) => revision.scene < selectedResumeScene);
    }

    function showRevisionPreview(revision) {
        if (!revision?.video) return;
        acceptedPreviewPin = null;
        setReviewVideo(revision.video);
        badge.textContent = `saved scene ${revision.scene} · revision ${revision.revision.slice(0, 8)}`;
    }

    async function deleteRevision(revision) {
        if (!revision || revision.active) return;
        try {
            const context = planResumeContext(node);
            const previewResponse = await api.fetchApi(
                "/minimax_h3_context_loop/checkpoint-revisions/delete-preview", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        run_name: context.runName,
                        scene: revision.scene,
                        revision: revision.revision,
                    }),
                },
            );
            const preview = await previewResponse.json();
            if (!previewResponse.ok) {
                throw new Error(preview.error || `HTTP ${previewResponse.status}`);
            }
            if (!preview.allowed) {
                throw new Error((preview.blockers ?? []).join(" ") ||
                    "This checkpoint revision cannot be deleted safely.");
            }
            const confirmed = window.confirm(
                `Permanently delete scene ${revision.scene} revision ` +
                `${revision.revision.slice(0, 8)} and its ` +
                `${preview.owned_file_count} owned files ` +
                `(${formatBytes(preview.reclaimed_bytes)})? Run archives, ` +
                "references, prompt history, and assembled exports are kept. " +
                "This cannot be undone.",
            );
            if (!confirmed) return;
            const response = await api.fetchApi(
                "/minimax_h3_context_loop/checkpoint-revisions/delete",
                await projectMutationOptions(node, context.runName, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        run_name: context.runName,
                        scene: revision.scene,
                        revision: revision.revision,
                        snapshot: preview.snapshot,
                    }),
                }),
            );
            const body = await response.json();
            if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
            await refreshResumeOptions();
            resumeStatus.textContent = `${body.message} Reclaimed ${formatBytes(body.reclaimed_bytes)}.`;
        } catch (error) {
            resumeStatus.textContent = error.message;
        }
    }

    function renderRevisionChoices() {
        revisionsRows.replaceChildren();
        revisionChain = checkpointRevisionChain(
            checkpointRevisions, planClipCount + 1,
        );
        const hasHistory = revisionChain.length > 0;
        revisionsPanel.hidden = !hasHistory;
        if (!hasHistory) {
            loadResume.textContent = "Load checkpoint";
            return;
        }
        for (const group of revisionChain) {
            const row = document.createElement("div");
            row.className = "h3r-revision-row";
            const label = document.createElement("span");
            label.className = "h3r-revision-label";
            label.textContent = `Scene ${group.scene}`;
            const select = document.createElement("select");
            select.className = "h3r-revision-select";
            select.dataset.scene = String(group.scene);
            for (const revision of group.revisions) {
                const option = document.createElement("option");
                option.value = revision.revision;
                option.textContent = checkpointRevisionLabel(revision);
                select.append(option);
            }
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "h3r-button h3r-delete";
            remove.textContent = "Delete";
            remove.title = "Permanently delete the selected inactive revision and only its owned files.";
            const update = (preview = false) => {
                const revision = group.revisions.find(
                    (item) => item.revision === select.value,
                );
                remove.disabled = !revision || revision.active;
                loadResume.textContent = selectedRevisionChain().some(
                    (item) => !item.active,
                ) ? "Restore & load" : "Load checkpoint";
                if (preview) showRevisionPreview(revision);
            };
            select.addEventListener("change", () => update(true));
            remove.addEventListener("click", () => {
                const revision = group.revisions.find(
                    (item) => item.revision === select.value,
                );
                void deleteRevision(revision);
            });
            row.append(label, select, remove);
            revisionsRows.append(row);
            update();
        }
    }

    async function performResumeRefresh() {
        const refreshToken = ++resumeRefreshToken;
        const previousResumeScene = Number(resumeSelect.value);
        try {
            const context = planResumeContext(node);
            planClipCount = context.clipCount;
            const query = new URLSearchParams({run_name: context.runName});
            const response = await api.fetchApi(
                `/minimax_h3_context_loop/checkpoints?${query.toString()}`,
            );
            const body = await response.json();
            if (refreshToken !== resumeRefreshToken) return;
            if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
            const nextResumeChoices = checkpointResumeOptions(
                body.checkpoints, context.clipCount);
            const nextCheckpointRevisions = body.revisions ?? [];
            resumeSelect.replaceChildren();
            revisionsRows.replaceChildren();
            revisionsPanel.hidden = true;
            resumeChoices = nextResumeChoices;
            checkpointRevisions = nextCheckpointRevisions;
            revisionChain = [];
            for (const option of resumeChoices) {
                const element = document.createElement("option");
                element.value = String(option.resumeScene);
                element.textContent = `Resume scene ${option.resumeScene} — checkpoint ${option.savedScene} · ${option.sceneId}`;
                resumeSelect.append(element);
            }
            if (resumeChoices.some(
                    (option) => option.resumeScene === previousResumeScene)) {
                resumeSelect.value = String(previousResumeScene);
            }
            renderRevisionChoices();
            loadResume.disabled = resumeChoices.length === 0;
            const complete = (body.checkpoints ?? []).filter((item) => item.ready).length;
            resumeStatus.textContent = resumeChoices.length
                ? `${complete} saved checkpoint${complete === 1 ? "" : "s"} found. Select the scene to resume.`
                : complete >= context.clipCount
                    ? `All ${context.clipCount} scenes are saved; there is no later scene to resume.`
                    : "No usable predecessor checkpoint was found for a later scene.";
        } catch (error) {
            if (refreshToken !== resumeRefreshToken) return;
            resumeStatus.textContent = error.message;
        }
    }

    function refreshResumeOptions({automatic = false} = {}) {
        if (automatic && activeResumeExecutionIds.size > 0) {
            deferredAutomaticResumeRefresh = true;
            return Promise.resolve();
        }
        if (resumeRefreshPromise) {
            if (automatic) queuedAutomaticResumeRefresh = true;
            else queuedExplicitResumeRefresh = true;
            return resumeRefreshPromise;
        }
        const request = performResumeRefresh();
        resumeRefreshPromise = request;
        void request.finally(() => {
            if (resumeRefreshPromise !== request) return;
            resumeRefreshPromise = null;
            const explicit = queuedExplicitResumeRefresh;
            const automaticFollowup = queuedAutomaticResumeRefresh;
            queuedExplicitResumeRefresh = false;
            queuedAutomaticResumeRefresh = false;
            if (explicit || automaticFollowup) {
                void refreshResumeOptions({automatic: !explicit});
            }
        });
        return request;
    }

    refreshResume.addEventListener("click", () => void refreshResumeOptions());
    resumeSelect.addEventListener("change", renderRevisionChoices);
    node._h3RefreshResume = () => refreshResumeOptions({automatic: true});
    loadResume.addEventListener("click", async () => {
        const resumeScene = Number(resumeSelect.value);
        if (!Number.isInteger(resumeScene)) return;
        try {
            const context = planResumeContext(node);
            const selections = selectedRevisionChain();
            const changed = selections.some((item) => !item.active);
            let restored = [];
            if (changed) {
                const changedScenes = selections.filter(
                    (item) => !item.active,
                ).map((item) => item.scene).join(", ");
                const confirmed = window.confirm(
                    `Restore the selected checkpoint revisions through scene ` +
                    `${resumeScene - 1} (changed scenes: ${changedScenes})? ` +
                    "The current revisions will remain available in revision history.",
                );
                if (!confirmed) return;
            }
            const runQuery = new URLSearchParams({
                run_name: context.runName,
                include_assets: "false",
            });
            const runResponse = await api.fetchApi(
                `/minimax_h3_context_loop/run?${runQuery.toString()}`,
            );
            const runBody = await runResponse.json();
            if (!runResponse.ok) throw new Error(
                runBody.error || `HTTP ${runResponse.status}`,
            );
            let restoredPolicyInputs = runBody.policy_inputs;
            if (selections.length) {
                const response = await api.fetchApi(
                    "/minimax_h3_context_loop/checkpoint-revisions/restore",
                    await projectMutationOptions(node, context.runName, {
                        method: "POST",
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({
                            run_name: context.runName,
                            resume_scene: resumeScene,
                            revisions: selections.map((item) => ({
                                scene: item.scene,
                                revision: item.revision,
                            })),
                        }),
                    }),
                );
                const body = await response.json();
                if (!response.ok) throw new Error(
                    body.error || `HTTP ${response.status}`,
                );
                restored = body.restored ?? [];
                restoredPolicyInputs = body.policy_inputs
                    ?? restoredPolicyInputs;
            }
            const savedPlan = restoreSavedPlanInputs(
                node, runBody.plan_inputs, restoredPolicyInputs);
            if (savedPlan.sceneCount < resumeScene) {
                throw new Error(
                    `The saved Plan has ${savedPlan.sceneCount} scenes and cannot resume scene ${resumeScene}.`,
                );
            }
            if (restored.length &&
                    !updatePlanFromCheckpointRevisions(node, restored)) {
                throw new Error(
                    "Checkpoint files were restored, but the connected Plan editor could not be updated.",
                );
            }
            if (!prepareResume(node, resumeScene)) {
                throw new Error("Could not find the connected H3 Chain Loop Start node.");
            }
            const choice = resumeChoices.find(
                (item) => item.resumeScene === resumeScene);
            const selectedPredecessor = selections.find(
                (item) => item.scene === resumeScene - 1,
            );
            const preview = selectedPredecessor?.video
                ?? choice?.partialVideo ?? choice?.video;
            if (preview) {
                acceptedPreviewPin = null;
                setReviewVideo(preview);
                badge.textContent = selectedPredecessor
                    ? `saved scene ${selectedPredecessor.scene} · revision ${selectedPredecessor.revision.slice(0, 8)}`
                    : choice?.partialVideo
                    ? `partial through checkpoint ${choice.savedScene}`
                    : `saved scene ${choice.savedScene} · ${choice.sceneId}`;
            }
            const unavailable = savedPlan.unavailable.length
                ? ` Current Plan has no ${savedPlan.unavailable.join(", ")} control. ` : " ";
            const unavailablePolicies = savedPlan.policies.unavailable.length
                ? ` Could not restore ${savedPlan.policies.unavailable.join(", ")}. ` : "";
            const finalStatus = `Restored the saved ${savedPlan.sceneCount}-scene Plan and ${restored.length} checkpoint scene${restored.length === 1 ? "" : "s"}.` +
                unavailable + unavailablePolicies +
                `Checkpoint ${resumeScene - 1} loaded for preview. Loop Start is armed for scene ${resumeScene}; queue the workflow to validate and resume.`;
            if (changed) await refreshResumeOptions();
            resumeStatus.textContent = finalStatus;
        } catch (error) {
            resumeStatus.textContent = error.message;
        }
    });

    function stopCountdown() {
        if (countdownTimer != null) clearInterval(countdownTimer);
        countdownTimer = null;
    }

    function renderWaitingStatus() {
        if (!current) return;
        const generated = Array.isArray(current.candidates)
            ? current.candidates.length : 0;
        const target = Number(current.candidate_count) || generated;
        const complete = Boolean(current.candidate_generation_complete) ||
            generated >= target;
        const batchCommand = current.candidate_batch_command_pending;
        const message = current.actionable === false
            ? (current.recovery_instructions || "Recovered review inventory is read-only; resume manually from the saved checkpoint.")
            : current.deferred_review
            ? "This complete candidate batch is stored on disk. Choose the active continuation, mark alternatives to keep, then prepare the next scene."
            : batchCommand
            ? batchCommand === "accept"
                ? "Selected take is being activated; Review Gate is stopping the speculative take."
                : "Pause queued. The current in-flight candidate will finish, then Review Gate will wait here."
            : current.warning || (target > 1
            ? complete
                ? `All ${target} candidates are saved. Choose the active continuation and mark every take you want to retain.`
                : current.review_each_candidate
                    ? `Candidate ${generated}/${target} is ready in per-candidate review mode. Keep it if wanted, accept a take now, or generate the next candidate.`
                    : `Candidate ${generated}/${target} is ready in the live carousel. Candidate ${generated + 1} is generating automatically; choose a take to stop early or pause the run.`
            : "Review the synchronized picture and sound, then choose an action.");
        const countdown = reviewCountdown(current.local_deadline);
        status.className = `h3r-status${current.warning ? " h3r-warning" : ""}`;
        status.textContent = countdown ?
            `${message}\nAuto-continue in ${countdown.text}.` : message;
        if (countdown?.seconds === 0) {
            root.classList.add("h3r-busy");
            setActionsEnabled(false);
            status.textContent = `${message}\nTimeout reached — continuing…`;
            stopCountdown();
        }
    }

    function startCountdown() {
        stopCountdown();
        renderWaitingStatus();
        if (reviewCountdown(current?.local_deadline)?.seconds > 0) {
            countdownTimer = setInterval(renderWaitingStatus, 250);
        }
    }

    async function submitDeferredReview(submittedReview, submittedCandidate) {
        if (!submittedCandidate?.revision) {
            throw new Error("Choose a saved candidate before resolving this pending review.");
        }
        const requestBody = {
            token: submittedReview.token,
            run_name: submittedReview.run_name,
            candidate_revision: submittedCandidate.revision,
            candidate_revisions: [...keptCandidateRevisions],
        };
        const prepareResponse = await api.fetchApi(
            "/minimax_h3_context_loop/deferred-review",
            await projectMutationOptions(node, submittedReview.run_name, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({...requestBody, action: "prepare"}),
            }),
        );
        const prepared = await prepareResponse.json();
        if (!prepareResponse.ok) {
            throw new Error(prepared.error || `HTTP ${prepareResponse.status}`);
        }
        const restoreResponse = await api.fetchApi(
            "/minimax_h3_context_loop/checkpoint-revisions/restore",
            await projectMutationOptions(node, prepared.run_name, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    run_name: prepared.run_name,
                    resume_scene: prepared.resume_scene,
                    scope_start_scene: 1,
                    // Changing the selected scene invalidates every later
                    // mutable pointer, including scenes outside the original
                    // bounded render range. Immutable revisions stay intact.
                    scope_end_scene: prepared.clip_count,
                    activate_only: true,
                    revisions: prepared.resume_revisions,
                }),
            }),
        );
        const restored = await restoreResponse.json();
        if (!restoreResponse.ok) {
            throw new Error(restored.error || `HTTP ${restoreResponse.status}`);
        }
        const finalizeResponse = await api.fetchApi(
            "/minimax_h3_context_loop/deferred-review",
            await projectMutationOptions(node, submittedReview.run_name, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({...requestBody, action: "finalize"}),
            }),
        );
        const finalized = await finalizeResponse.json();
        if (!finalizeResponse.ok) {
            throw new Error(finalized.error || `HTTP ${finalizeResponse.status}`);
        }
        const saved = updatePlan(
            node, prepared.scene, prepared.scene_prompt,
            prepared.seed, prepared.length);
        const hasNextScene = prepared.resume_scene <= prepared.clip_count;
        const armed = hasNextScene && prepareResume(
            node, prepared.resume_scene, prepared.end_clip,
            prepared.clip_count);
        pinAcceptedPreview(submittedReview, submittedCandidate);
        await Promise.all([refreshDeferredReviews(), refreshResumeOptions()]);
        current = null;
        showAcceptedPreview();
        root.classList.add("h3r-busy");
        setActionsEnabled(false);
        status.className = "h3r-status";
        const cleanupWarning = Array.isArray(finalized.cleanup_warnings) &&
            finalized.cleanup_warnings.length
            ? ` Cleanup warning: ${finalized.cleanup_warnings.join(" ")}` : "";
        status.textContent = `Candidate ${prepared.candidate_number}/${prepared.candidate_count} is active. ` +
            `${prepared.kept_candidate_count} take${prepared.kept_candidate_count === 1 ? "" : "s"} kept; ` +
            `${finalized.deleted_candidate_count} removed.` +
            (saved ? " The Plan seed was updated." : "") +
            (armed ? ` Loop Start is armed for scene ${prepared.resume_scene}; queue when ready.`
                : hasNextScene
                    ? ` Arm Loop Start manually for scene ${prepared.resume_scene}.`
                    : " This was the final scene.") + cleanupWarning;
    }

    async function submit(action) {
        if (!current?.token) {
            status.className = "h3r-status h3r-warning";
            status.textContent = "No live review token is attached. Checking the server…";
            await fetchPending();
            if (!current?.token) {
                status.textContent = "No pending review is available for this project yet.";
                return;
            }
        }
        try {
            // Freeze one coherent review edit before yielding to the server.
            // Websocket recovery can replace `current`, and a companion UI can
            // refresh the live controls, while this request is in flight.
            const submittedReview = current;
            const submittedToken = submittedReview.token;
            const submittedIndex = submittedReview.clip_index;
            const submittedCandidate = selectedCandidate();
            if (submittedReview.deferred_review) {
                if (action !== "approve") return;
                stopCountdown();
                root.classList.add("h3r-busy");
                setActionsEnabled(false);
                status.className = "h3r-status";
                status.textContent = "Activating the selected saved lineage…";
                await submitDeferredReview(
                    submittedReview, submittedCandidate);
                return;
            }
            const liveCandidateBatch = Boolean(
                submittedReview.candidate_batch_active) &&
                !submittedReview.candidate_generation_complete;
            const candidateBatchAction = liveCandidateBatch
                ? action === "approve" ? "accept"
                    : action === "next_candidate" ? "pause" : ""
                : "";
            if (liveCandidateBatch && !candidateBatchAction) return;
            const submittedPrompt = reviewPromptEditorEnabled() && promptEditedInGate
                ? prompt.value
                : (planScenePrompt(node, submittedReview)
                    ?? submittedReview.scene_prompt
                    ?? prompt.value);
            const normalizedSeed = action === "retry" ? reviewSeed(seed.value) : seed.value;
            const normalizedDuration = action === "retry" || action === "reroll"
                ? reviewDuration(duration.value) : null;
            stopCountdown();
            root.classList.add("h3r-busy");
            setActionsEnabled(false);
            status.className = "h3r-status";
            status.textContent = candidateBatchAction === "accept"
                ? "Activating this checkpoint and cancelling the speculative take…"
                : candidateBatchAction === "pause"
                    ? "Queuing a pause after the in-flight candidate…"
                : action === "approve" ? "Sending approval…" :
                action === "next_candidate" ? "Resuming to generate the next candidate…" :
                action === "stop" ? "Sending stop decision…" : "Sending retry decision…";
            const response = await api.fetchApi(candidateBatchAction
                ? "/minimax_h3_context_loop/review-candidate-batch"
                : "/minimax_h3_context_loop/review",
                await projectMutationOptions(node, submittedReview.run_name, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    token: submittedToken,
                    action: candidateBatchAction || action,
                    scene_prompt: submittedPrompt,
                    seed: normalizedSeed,
                    length: normalizedDuration?.length,
                    candidate_revision: (candidateBatchAction === "accept" ||
                            action === "approve" || action === "stop") &&
                            Number(submittedReview.candidate_count) > 1
                        ? submittedCandidate?.revision ?? "" : "",
                    candidate_revisions: [...keptCandidateRevisions],
                }),
            }));
            const body = await response.json();
            if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
            if (candidateBatchAction) {
                if (current?.token === submittedToken) {
                    current.candidate_batch_command_pending = candidateBatchAction;
                }
                if (candidateBatchAction === "accept") {
                    pinAcceptedPreview(submittedReview, submittedCandidate);
                    const result = await activateAcceptedCandidate(
                        node, submittedReview, body);
                    showAcceptedPreview();
                    if (result.immediate) {
                        status.textContent =
                            `Candidate ${body.candidate_number}/${body.candidate_count} ` +
                            `is active. The unwanted take was cancelled; scene ` +
                            `${result.nextIndex} was queued immediately.` +
                            (result.saved ? " The Plan seed was updated." : "") +
                            (result.cleanupWarning || "");
                    } else {
                        status.textContent =
                            `Candidate ${body.candidate_number}/${body.candidate_count} ` +
                            `selected. ${result.message}`;
                    }
                } else {
                    status.textContent = "Pause queued. The current in-flight take " +
                        "will finish, then the carousel will wait for you.";
                }
            } else if (action === "next_candidate") {
                status.textContent = `Candidate ${submittedReview.candidates.length}/` +
                    `${submittedReview.candidate_count} reviewed — generating the next take ` +
                    `with seed ${body.seed}. ${body.kept_candidate_count} marked take` +
                    `${body.kept_candidate_count === 1 ? " is" : "s are"} retained.`;
            } else if (action === "approve") {
                const selected = Number(body.candidate_count) > 1;
                const saved = selected && updatePlan(
                    node, submittedIndex, body.scene_prompt, body.seed, body.length);
                status.textContent = submittedReview.unload_models_while_waiting
                    ? "Approval received — workflow is resuming and reloading the model stack."
                    : "Approval received — workflow resumed.";
                if (selected) {
                    status.textContent += ` Candidate ${body.candidate_number}/${body.candidate_count} is now active.` +
                        ` ${body.kept_candidate_count} take${body.kept_candidate_count === 1 ? "" : "s"} kept.` +
                        (saved ? " The Plan seed was updated." : "");
                }
            } else if (action === "retry" || action === "reroll") {
                const acceptedPrompt = typeof body.scene_prompt === "string"
                    ? body.scene_prompt : submittedPrompt.trim();
                const acceptedDuration = reviewDurationText(body.length);
                const saved = updatePlan(
                    node, submittedIndex, acceptedPrompt, body.seed, body.length);
                if (current?.token === submittedToken) {
                    prompt.value = acceptedPrompt;
                    promptEditedInGate = false;
                    seed.value = body.seed;
                    duration.value = acceptedDuration;
                }
                status.textContent = `Retrying scene with seed ${body.seed} at ${body.length} frames (${acceptedDuration}s).` +
                    (saved ? " The Plan editor was updated." : "");
            } else if (action === "stop") {
                const selected = Number(body.candidate_count) > 1;
                const saved = selected && updatePlan(
                    node, submittedIndex, body.scene_prompt, body.seed, body.length);
                const prepared = submittedReview.clip_index < submittedReview.clip_count &&
                    prepareResume(node, submittedReview.clip_index + 1);
                status.textContent = (submittedReview.assemble_partial_on_stop
                    ? "Stop accepted — assembling the partial video…"
                    : "Stopped at the accepted checkpoint.") +
                    (selected ? ` Candidate ${body.candidate_number}/${body.candidate_count} is now active.` : "") +
                    (selected ? ` ${body.kept_candidate_count} take${body.kept_candidate_count === 1 ? "" : "s"} kept.` : "") +
                    (saved ? " The Plan seed was updated." : "") +
                    (prepared ? ` Loop Start is ready at clip ${submittedReview.clip_index + 1}.` : "");
                setTimeout(() => void refreshResumeOptions({automatic: true}), 0);
            }
        } catch (error) {
            root.classList.remove("h3r-busy");
            setActionsEnabled(Boolean(current?.token));
            status.className = "h3r-status h3r-warning";
            status.textContent = error.message;
            if (reviewCountdown(current?.local_deadline)?.seconds > 0) {
                countdownTimer = setInterval(renderWaitingStatus, 250);
            }
        }
    }

    node._h3ReviewHandler = (data) => {
        const acceptedDisposition = acceptedPreviewDisposition(
            acceptedPreviewPin, data);
        if (acceptedDisposition === "ignore") return;
        if (acceptedDisposition === "release") acceptedPreviewPin = null;
        const sameToken = Boolean(current?.token) && current.token === data?.token;
        const carriesCandidateBatch = !sameToken &&
            String(current?.run_name ?? "") === String(data?.run_name ?? "") &&
            Number(current?.clip_index) === Number(data?.clip_index) &&
            Number(current?.candidate_count) > 1;
        const carriedActiveCandidateRevision = carriesCandidateBatch
            ? activeCandidateRevision : "";
        const carriedKeepRevisions = carriesCandidateBatch
            ? [...keptCandidateRevisions] : [];
        const previousRevision = Number(current?.preview_revision ?? 0);
        const incomingRevision = Number(data?.preview_revision ?? 0);
        if (sameToken && incomingRevision <= previousRevision) return;
        const localDeadline = sameToken ? current.local_deadline :
            reviewLocalDeadline(data?.deadline, data?.server_now);
        current = sameToken ? {
            ...current,
            ...data,
            local_deadline: current.local_deadline,
        } : {
            ...data,
            local_deadline: localDeadline,
        };
        const candidateBatchComplete = Boolean(current?.candidate_generation_complete) ||
            (Array.isArray(current?.candidates) && current.candidates.length >=
                Number(current?.candidate_count));
        if (sameToken && current?.actionable !== false &&
                Number(current?.candidate_count) > 1 && candidateBatchComplete &&
                !current?.candidate_batch_command_pending) {
            root.classList.remove("h3r-busy");
            setActionsEnabled(true);
        }
        if (!sameToken) {
            root.classList.remove("h3r-busy");
            setActionsEnabled(true);
            const deferredReview = Boolean(data.deferred_review);
            retryButton.hidden = deferredReview;
            rerollButton.hidden = deferredReview;
            stopButton.hidden = deferredReview;
            activeCandidateRevision = (data.candidates ?? []).some(
                (candidate) => candidate.revision ===
                    carriedActiveCandidateRevision,
            ) ? carriedActiveCandidateRevision : "";
            keptCandidateRevisions = new Set(
                [
                    ...(Array.isArray(data.kept_candidate_revisions)
                        ? data.kept_candidate_revisions : []),
                    ...carriedKeepRevisions,
                ].filter((revision) => (data.candidates ?? []).some(
                    (candidate) => candidate.revision === revision)),
            );
            // The scene is persisted before Review Gate receives its token.
            // Refresh here so the current (including final) checkpoint appears
            // in history without requiring the user to press Refresh.
            setTimeout(() => void refreshResumeOptions({automatic: true}), 0);
            if (deferredReview) setTimeout(refreshDeferredReviews, 0);
        }
        if (!sameToken) {
            prompt.value = data.scene_prompt ?? "";
            promptEditedInGate = false;
            seed.value = data.seed ?? "";
            duration.value = reviewDurationText(data.raw_frames);
            prefix.textContent = data.prompt_prefix ?
                `Shared prompt (unchanged)\n${data.prompt_prefix}` : "";
            prefix.hidden = !data.prompt_prefix;
            startCountdown();
            if (data.play_notification_sound && !notifiedTokens.has(data.token)) {
                notifiedTokens.add(data.token);
                playReviewChime().catch((error) => {
                    console.warn("[H3 Chain Review] Browser blocked notification sound:", error);
                });
            }
        } else if (!root.classList.contains("h3r-busy")) {
            renderWaitingStatus();
        }
        const candidate = renderCandidateCarousel();
        if (candidate) {
            showCandidate(candidate);
        } else {
            badge.textContent = `clip ${data.clip_index}/${data.clip_count} · ${data.shot_id}`;
            setReviewVideo(data.video, sameToken);
        }
        if (acceptedDisposition === "hold") showAcceptedPreview();
        const planNode = upstreamPlanNode(node);
        if (planNode) {
            publishPlanCompanionScene(node, planNode, Number(data.clip_index) - 1);
        }
    };

    node._h3PromptCompanionSetScenePrompt = (planNode, index, text) => {
        if (root.classList.contains("h3r-busy")
                || planNode !== upstreamPlanNode(node)
                || Number(index) !== Number(current?.clip_index) - 1) {
            return false;
        }
        prompt.value = String(text ?? "").replace(/\r\n?/g, "\n");
        promptEditedInGate = false;
        return true;
    };

    node._h3ReviewResolvedHandler = (data) => {
        if (!current || data?.token !== current.token) return;
        stopCountdown();
        root.classList.add("h3r-busy");
        setActionsEnabled(false);
        status.className = "h3r-status";
        status.textContent = data.status || "Review resolved; continuing…";
        if (data.action === "candidate_batch_approve") {
            const saved = updatePlan(
                node, Number(data.clip_index), data.scene_prompt,
                data.seed, data.raw_frames);
            if (saved) status.textContent += " The Plan seed was updated.";
        }
        const completedVideo = data.final_video ?? data.partial_video;
        if (completedVideo) {
            setReviewVideo(completedVideo);
            badge.textContent = data.final_video
                ? "final assembled video"
                : "partial joined video";
        }
        if (data.action === "approve" || data.action === "stop" ||
                data.action === "candidate_batch_approve") {
            setTimeout(() => void refreshResumeOptions({automatic: true}), 0);
        }
    };

    const onResumeExecutionStart = (event) => {
        const promptId = String(event.detail?.prompt_id ?? "");
        if (promptId) activeResumeExecutionIds.add(promptId);
    };
    const onResumeExecutionTerminal = (event) => {
        const promptId = String(event.detail?.prompt_id ?? "");
        if (!promptId || !activeResumeExecutionIds.delete(promptId) ||
                activeResumeExecutionIds.size !== 0 ||
                !deferredAutomaticResumeRefresh) return;
        deferredAutomaticResumeRefresh = false;
        void refreshResumeOptions({automatic: true});
    };
    api.addEventListener("execution_start", onResumeExecutionStart);
    api.addEventListener("execution_success", onResumeExecutionTerminal);
    api.addEventListener("execution_error", onResumeExecutionTerminal);
    api.addEventListener("execution_interrupted", onResumeExecutionTerminal);

    const widget = node.addDOMWidget("h3_chain_review", "h3-chain-review", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 500,
    });
    widget.serialize = false;
    mountedReviewNodes.add(node);
    updatePendingPolling();
    const removed = node.onRemoved;
    node.onRemoved = function () {
        stopCountdown();
        delete this._h3PromptCompanionSetScenePrompt;
        api.removeEventListener("execution_start", onResumeExecutionStart);
        api.removeEventListener("execution_success", onResumeExecutionTerminal);
        api.removeEventListener("execution_error", onResumeExecutionTerminal);
        api.removeEventListener("execution_interrupted", onResumeExecutionTerminal);
        mountedReviewNodes.delete(this);
        updatePendingPolling();
        return removed?.apply(this, arguments);
    };
    node.setSize?.([Math.max(node.size?.[0] ?? 540, 540), Math.max(node.size?.[1] ?? 650, 650)]);
    const queuedReview = node._h3QueuedReview;
    delete node._h3QueuedReview;
    if (queuedReview) node._h3ReviewHandler(queuedReview);
    setTimeout(fetchPending, 0);
    setTimeout(refreshDeferredReviews, 0);
    setTimeout(() => void refreshResumeOptions({automatic: true}), 0);
}

api.addEventListener("minimax_h3_context_loop_review", (event) => routeReview(event.detail));
api.addEventListener("minimax_h3_context_loop_review_resolved", (event) =>
    routeReviewResolved(event.detail));
api.addEventListener("executed", (event) =>
    trackActiveSceneExecution(event.detail));
api.addEventListener("execution_interrupted", (event) =>
    finishTrackedReviewExecution("interrupted", event.detail));
api.addEventListener("execution_success", (event) =>
    finishTrackedReviewExecution("success", event.detail));
api.addEventListener("execution_error", (event) =>
    finishTrackedReviewExecution("error", event.detail));
// A status event is sent when ComfyUI's websocket connects or reconnects.
api.addEventListener("status", fetchPending);
window.addEventListener("focus", fetchPending);
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "hidden") fetchPending();
});

app.registerExtension({
    name: "minimax_h3_context_loop.chain_review",
    init() {
        app.ui?.settings?.addSetting?.({
            id: PROMPT_EDITOR_SETTING,
            category: ["MiniMax H3 Context Loop", "Interface", "Review Gate"],
            name: "Enable prompt editing inside Review Gate",
            tooltip: "Disabled by default in 0.5. Keep prompt authoring in Scene Prompt Editor or Rich Scene Prompt Editor. Enable this only to restore the legacy Review Gate textarea.",
            type: "boolean",
            defaultValue: false,
            onChange() {
                for (const node of mountedReviewNodes) {
                    node._h3ReviewRefreshPromptSetting?.();
                }
            },
        });
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setTimeout(() => mount(this), 0);
            return result;
        };
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            setTimeout(() => this._h3ReviewApplyLayout?.(), 0);
            return result;
        };
        const graphConfigured = nodeType.prototype.onGraphConfigured;
        nodeType.prototype.onGraphConfigured = function () {
            const result = graphConfigured?.apply(this, arguments);
            setTimeout(() => this._h3ReviewApplyLayout?.(), 0);
            return result;
        };
    },
    async nodeCreated(node) {
        // Official per-instance hook. Keep the prototype hook above for older
        // frontends; mount() is idempotent, so supporting both closes the
        // timing gap without creating two widgets.
        if (nodeType(node) === NODE_NAME) mount(node);
    },
    async afterConfigureGraph() {
        await fetchPending();
        for (const node of allNodes(app.graph)) {
            if (nodeType(node) === NODE_NAME) node._h3RefreshResume?.();
            if (nodeType(node) === NODE_NAME) node._h3RefreshDeferred?.();
            if (nodeType(node) === NODE_NAME) node._h3ReviewApplyLayout?.();
        }
    },
});
