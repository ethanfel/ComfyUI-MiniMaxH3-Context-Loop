import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {branchRequestPath, branchSelectionJson} from "./h3_working_branches.mjs?v=0.7.18";
import {checkpointForkGraph, checkpointGraphKey, checkpointSaveOrder, checkpointGraphOutput, mountCheckpointGraphEdges} from "./h3_checkpoint_graph.mjs?v=0.7.20";
import {
    CHECKPOINT_STAGES,
    checkpointStageVariants,
    checkpointProcessingBranchRows,
    checkpointVariantLatentStatus,
    checkpointBranchRows,
    checkpointChapterBranchRows,
    checkpointOutputBranchTip,
    checkpointActivationMode,
    checkpointDeletionTitle,
    checkpointDependencyText,
    checkpointRevisionKey,
    checkpointRevisionLineage,
    checkpointSelectionJson,
    checkpointLocalSelection,
    checkpointLocalSelectionJson,
    checkpointDeropeSelectionJson,
    checkpointOutputSelectionJson,
    checkpointOutputSummary,
    formatCheckpointBytes,
    selectedCheckpointRevision,
} from "./h3_checkpoint_manager_core.mjs?v=0.7.19";
import {
    parsePlanJson,
    planToJson,
    promptValueToText,
} from "./h3_chain_plan_core.mjs?v=0.7.8";
import {applyCheckpointRevisionSet} from "./h3_chain_review_core.mjs?v=0.7.7";
import * as promptCompanionSync from "./h3_prompt_companion_sync.mjs?v=0.7.2";
import {
    refreshRestoredPlanEditors,
    restoreConnectedPolicyInputs,
} from "./h3_plan_restore_core.mjs?v=0.7.9";
import {projectMutationOptions} from "./h3_project_ownership.mjs?v=0.7.4";

const NODE_NAME = "MiniMaxH3ChainCheckpointManager";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const PLAN_NAMES = new Set([PLAN_NAME, "MiniMaxH3ChainPlanModern"]);
const START_NAME = "MiniMaxH3ChainLoopStart";
const RUN_PROPERTY = "h3_checkpoint_manager_run";
const SCENE_PROPERTY = "h3_checkpoint_manager_scene";
const REVISION_PROPERTY = "h3_checkpoint_manager_revision";
const CHAPTER_PROPERTY = "h3_checkpoint_manager_chapter";
const OUTPUT_SCOPE_PROPERTY = "h3_checkpoint_manager_output_scope";
const STAGE_PROPERTY = "h3_checkpoint_manager_stage";
const VARIANT_PROPERTY = "h3_checkpoint_manager_variant";
const COLLAPSED_CHAPTERS_PROPERTY = "h3_checkpoint_manager_collapsed_chapters";
const PREVIEW_HEIGHT_PROPERTY = "h3_checkpoint_manager_preview_height";
const DEFAULT_PREVIEW_HEIGHT = 280;
const MIN_PREVIEW_HEIGHT = 120;
const MAX_PREVIEW_HEIGHT = 720;

function previewHeight(value) {
    const number = Number(value);
    return Number.isFinite(number)
        ? Math.max(MIN_PREVIEW_HEIGHT, Math.min(MAX_PREVIEW_HEIGHT, Math.round(number)))
        : DEFAULT_PREVIEW_HEIGHT;
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? "";
}

function upstreamPlanNode(start, includeStudio = false) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        if (current !== start && (PLAN_NAMES.has(nodeType(current))
                || (includeStudio && nodeType(current) === "MiniMaxH3ChainPlanStudio"))) return current;
        for (const input of current.inputs ?? []) {
            if (input.link == null) continue;
            const link = graphLink(current.graph, input.link);
            const candidate = link
                ? current.graph?.getNodeById?.(link.origin_id) : null;
            if (candidate) queue.push(candidate);
        }
    }
    return null;
}

function widget(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function graphLink(graph, linkId) {
    return graph?.links?.[linkId] ?? graph?.links?.get?.(linkId) ?? null;
}

function connectedNode(start, wantedType) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        if (current !== start && nodeType(current) === wantedType) return current;
        for (const input of current.inputs ?? []) {
            if (input.link == null) continue;
            const link = graphLink(current.graph, input.link);
            const candidate = link
                ? current.graph?.getNodeById?.(link.origin_id) : null;
            if (candidate) queue.push(candidate);
        }
        for (const output of current.outputs ?? []) {
            for (const linkId of output.links ?? []) {
                const link = graphLink(current.graph, linkId);
                const candidate = link
                    ? current.graph?.getNodeById?.(link.target_id) : null;
                if (candidate) queue.push(candidate);
            }
        }
    }
    return null;
}

function publishCompanionPrompt(...args) {
    return promptCompanionSync.publishCompanionPrompt?.(...args) ?? 0;
}

function element(tag, className = "", text = undefined) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action, className = "") {
    const item = element("button", className, label);
    item.type = "button";
    item.title = title;
    item.addEventListener("click", action);
    return item;
}

function videoUrl(item) {
    if (!item?.filename) return "";
    const query = new URLSearchParams({
        filename:item.filename,
        subfolder:item.subfolder ?? "",
        type:item.type ?? "output",
    });
    return api.apiURL(`/view?${query.toString()}`);
}

function localTime(value) {
    if (!value) return "unknown";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || "unknown") : date.toLocaleString();
}

async function jsonRequest(path, options = {}) {
    const response = await api.fetchApi(path, options);
    const payload = await response.json();
    if (!response.ok) throw Object.assign(
        new Error(payload.error || `HTTP ${response.status}`), {payload});
    return payload;
}

async function mutationRequest(node, runName, path, options = {}, branch = node.properties?.h3_working_branch_id ?? "main") {
    return await jsonRequest(
        branchRequestPath(path, branch),
        await projectMutationOptions(node, runName, options),
    );
}

function injectStyles() {
    if (document.getElementById("h3-checkpoint-manager-style")) return;
    const style = document.createElement("style");
    style.id = "h3-checkpoint-manager-style";
    style.textContent = `
      .h3cm-root { --h3cm-bg:color-mix(in srgb,var(--comfy-menu-bg,#202124) 93%,#101827);
        --h3cm-panel:var(--comfy-input-bg,#15171d); --h3cm-border:var(--border-color,#586174);
        --h3cm-text:var(--input-text,#edf1f8); --h3cm-muted:color-mix(in srgb,var(--h3cm-text) 58%,transparent);
        --h3cm-accent:color-mix(in srgb,var(--h3cm-text) 38%,#4f83ff);
        --h3cm-chapter:color-mix(in srgb,var(--h3cm-text) 68%,#d99121);
        --h3cm-danger:color-mix(in srgb,var(--h3cm-text) 40%,#d44747);
        box-sizing:border-box; width:100%; height:100%; min-height:620px; display:flex; flex-direction:column;
        gap:8px; overflow:auto; padding:10px; border:1px solid var(--h3cm-border); border-radius:9px;
        background:var(--h3cm-bg); color:var(--h3cm-text); font:12px/1.4 system-ui,sans-serif; }
      .h3cm-root *, .h3cm-root *::before, .h3cm-root *::after { box-sizing:border-box; }
      .h3cm-head,.h3cm-run-row,.h3cm-chapter-tabs,.h3cm-scenes,.h3cm-branch-head,.h3cm-delete-actions,.h3cm-assignment-actions {
        display:flex; align-items:center; gap:6px; }
      .h3cm-head { justify-content:space-between; }
      .h3cm-title { font-size:15px; font-weight:760; color:var(--h3cm-accent); }
      .h3cm-summary,.h3cm-status,.h3cm-muted { color:var(--h3cm-muted); }
      .h3cm-root button,.h3cm-root select { min-height:30px; border:1px solid var(--h3cm-border);
        border-radius:6px; background:var(--h3cm-panel); color:var(--h3cm-text); font:inherit; }
      .h3cm-root button { padding:5px 8px; cursor:pointer; }
      .h3cm-root button:hover,.h3cm-root button:focus-visible { border-color:var(--h3cm-accent); outline:none; }
      .h3cm-root button:disabled { cursor:not-allowed; opacity:.45; }
      .h3cm-run-select { flex:1; min-width:0; padding:5px 7px; }
      .h3cm-run-delete { white-space:nowrap; color:var(--h3cm-danger) !important; }
      .h3cm-output { display:flex; flex-wrap:wrap; align-items:center; gap:6px;
        flex:0 0 auto; padding:7px; border:1px solid var(--h3cm-border); border-radius:7px; }
      .h3cm-output-summary { flex:1 1 250px; overflow-wrap:anywhere; }
      .h3cm-working-row { display:flex; align-items:center; gap:7px; flex:0 0 auto; }
      .h3cm-assignment { flex:0 0 auto; padding:7px; border:1px solid var(--h3cm-border); border-radius:7px; }
      .h3cm-assignment-actions { flex-wrap:wrap; margin-top:5px; }
      .h3cm-assignment-context { overflow-wrap:anywhere; }
      .h3cm-assignment-badge { display:block; color:var(--h3cm-chapter); font-size:11px; margin-top:4px; }
      .h3cm-local-label { color:var(--h3cm-accent) !important; font-weight:700; }
      .h3cm-scenes { flex:0 0 auto; overflow:auto; padding-bottom:2px; }
      .h3cm-stage-tabs { display:flex; flex:0 0 auto; gap:6px; overflow:auto; }
      .h3cm-stage-tab { white-space:nowrap; }
      .h3cm-stage-tab[aria-selected="true"] { color:var(--h3cm-accent); border-color:var(--h3cm-accent); }
      .h3cm-stage-note { flex:0 0 auto; color:var(--h3cm-muted); overflow-wrap:anywhere; }
      .h3cm-latest-label { color:var(--h3cm-chapter); font-weight:750; }
      .h3cm-chapter-tabs { flex:0 0 auto; overflow:auto; padding:2px 0; }
      .h3cm-chapter-tab { white-space:nowrap; border-radius:999px !important; }
      .h3cm-chapter-selected { color:var(--h3cm-chapter) !important; border-color:#d6a650 !important;
        background:color-mix(in srgb,var(--h3cm-panel) 78%,#6d4b16) !important; }
      .h3cm-scene { white-space:nowrap; }
      .h3cm-scene-selected,.h3cm-revision-selected { border-color:var(--h3cm-accent) !important;
        color:var(--h3cm-accent) !important; }
      .h3cm-main { min-height:240px; flex:1 1 auto; display:grid; grid-template-columns:minmax(310px,.9fr) minmax(390px,1.1fr); gap:8px; }
      .h3cm-panel { min-height:0; overflow:auto; padding:8px; border:1px solid var(--h3cm-border);
        border-radius:7px; background:color-mix(in srgb,var(--h3cm-panel) 90%,transparent); }
      .h3cm-panel-title { display:flex; justify-content:space-between; gap:8px; margin-bottom:7px; font-weight:750; }
      .h3cm-shared-legend { color:var(--h3cm-muted); font-size:10px; font-weight:500; }
      .h3cm-branches { position:relative; }
      .h3cm-branch-chapter { margin-bottom:12px; padding:7px; border:1px solid color-mix(in srgb,var(--h3cm-border) 62%,transparent);
        border-radius:8px; background:color-mix(in srgb,var(--h3cm-panel) 70%,transparent); }
      .h3cm-branch-chapter:last-child { margin-bottom:0; }
      .h3cm-branch-chapter-title { width:100%; min-height:0 !important; display:flex; align-items:center;
        justify-content:flex-start; gap:7px; margin:0 0 7px; padding:2px !important; border:0 !important;
        background:transparent !important; color:var(--h3cm-chapter) !important; font-size:11px !important;
        font-weight:750 !important; text-align:left; }
      .h3cm-branch-chapter-title:hover,.h3cm-branch-chapter-title:focus-visible {
        color:var(--h3cm-accent) !important; outline:1px solid var(--h3cm-accent) !important; }
      .h3cm-branch-chapter-title .h3cm-muted { margin-left:auto; font-weight:500; }
      .h3cm-branch-chapter-caret { width:11px; color:currentColor; text-align:center; }
      .h3cm-branch-chapter-collapsed { padding-bottom:7px; }
      .h3cm-branch-chapter-collapsed .h3cm-branch-chapter-title { margin-bottom:0; }
      .h3cm-branch { position:relative; z-index:1; margin-bottom:8px; padding:6px;
        border:1px solid color-mix(in srgb,var(--h3cm-border) 75%,transparent); border-radius:6px; }
      .h3cm-branch-head { justify-content:space-between; margin-bottom:5px; }
      .h3cm-branch-head[role="button"] { cursor:pointer; border-radius:4px; }
      .h3cm-branch-head[role="button"]:hover,.h3cm-branch-head[role="button"]:focus-visible {
        color:var(--h3cm-accent); outline:1px solid var(--h3cm-accent); outline-offset:2px; }
      .h3cm-branch-selected { border-color:var(--h3cm-accent) !important; }
      .h3cm-branch-active { color:var(--h3cm-accent); font-weight:700; }
      .h3cm-revision { position:relative; min-width:112px; text-align:left; white-space:nowrap; }
      .h3cm-revision small { display:block; color:var(--h3cm-muted); font-size:10px; }
      .h3cm-alternates { display:flex; flex-direction:column; gap:3px; min-width:104px;
        padding-left:7px; border-left:2px solid #8264bd; }
      .h3cm-alternate { min-width:100px; min-height:24px !important; padding:3px 6px !important;
        border-color:#7259a8 !important; color:var(--h3cm-text) !important; font-size:10px !important;
        white-space:normal; overflow-wrap:anywhere; }
      .h3cm-alternate-used { background:color-mix(in srgb,var(--h3cm-panel) 68%,#56358b) !important;
        box-shadow:inset 3px 0 0 #b493f0; }
      .h3cm-revision-empty { border-style:dashed !important; color:var(--h3cm-muted) !important;
        background:color-mix(in srgb,var(--h3cm-panel) 72%,transparent) !important; }
      div.h3cm-revision-empty { padding:5px 8px; border:1px dashed var(--h3cm-border); border-radius:6px; }
      .h3cm-revision-empty-selected { border-color:var(--h3cm-accent) !important;
        color:var(--h3cm-accent) !important; }
      .h3cm-fork-scroll { overflow:auto; padding:5px 3px 10px; }
      .h3cm-fork-graph { display:grid; position:relative; width:max-content; gap:28px 48px; align-items:start; }
      .h3cm-fork-node,.h3cm-fork-slot { width:180px; min-width:0; position:relative; z-index:1; }
      .h3cm-fork-node > .h3cm-revision,.h3cm-fork-slot > .h3cm-revision { width:100%; min-height:74px; white-space:normal; overflow-wrap:anywhere; }
      .h3cm-fork-node .h3cm-revision small { margin-top:2px; }
      .h3cm-fork-node .h3cm-alternates { margin-top:5px; }
      .h3cm-fork-node .h3cm-alternate small { display:block; }
      .h3cm-fork-node .h3cm-branch { margin:6px 0 0; padding:5px; }
      .h3cm-fork-node .h3cm-branch-head { flex-wrap:wrap; justify-content:flex-start; font-size:10px; gap:4px; margin:0; }
      .h3cm-fork-node .h3cm-branch-head > span { overflow-wrap:anywhere; }
      .h3cm-fork-node .h3cm-revision-empty { width:100%; margin-top:6px; }
      .h3cm-fork-edges { position:absolute; inset:0; overflow:visible; pointer-events:none; z-index:0; }
      .h3cm-fork-edge { fill:none; stroke:var(--h3cm-muted); stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
      .h3cm-fork-edge-output { stroke:var(--h3cm-accent); stroke-width:3.5; }
      .h3cm-fork-edge-reuse { stroke-dasharray:5 4; }
      .h3cm-revision .h3cm-latest-label { color:var(--h3cm-chapter); }
      .h3cm-output-path { border-color:var(--h3cm-accent) !important;
        box-shadow:0 0 0 2px color-mix(in srgb,var(--h3cm-accent) 55%,transparent) !important; }
      .h3cm-output-path-label { color:var(--h3cm-accent) !important; font-weight:700; }
      .h3cm-revision .h3cm-final-cut-alt { color:var(--h3cm-chapter); font-weight:700; }
      .h3cm-plan-marker { display:inline-block; border:1px dashed currentColor; border-radius:4px;
        padding:1px 4px; color:var(--h3cm-chapter); font-size:10px; font-weight:700; }
      .h3cm-plan-context { font-size:11px; color:var(--h3cm-muted); margin-bottom:6px; }
      .h3cm-detail { display:flex; flex-direction:column; gap:8px; }
      .h3cm-preview-frame { width:100%; height:280px; min-height:120px; max-height:720px;
        flex:0 0 auto; display:flex; flex-direction:column; overflow:hidden; border-radius:6px;
        background:#08090c; }
      .h3cm-preview { width:100%; min-height:0; flex:1 1 auto; object-fit:contain; background:#08090c; }
      .h3cm-preview-resizer { position:relative; flex:0 0 12px; min-height:12px;
        cursor:ns-resize; touch-action:none; background:color-mix(in srgb,var(--h3cm-panel) 88%,#000); }
      .h3cm-preview-resizer::after { content:""; position:absolute; top:5px; left:calc(50% - 28px);
        width:56px; height:2px; border-radius:2px; background:var(--h3cm-border); }
      .h3cm-preview-resizer:hover::after,.h3cm-preview-resizer:focus-visible::after {
        background:var(--h3cm-accent); }
      .h3cm-preview-resizer:focus-visible { outline:1px solid var(--h3cm-accent); outline-offset:-1px; }
      .h3cm-audio { width:100%; height:36px; }
      .h3cm-inspector { display:grid; grid-template-columns:auto minmax(0,1fr); gap:3px 9px; }
      .h3cm-inspector dt { color:var(--h3cm-muted); }
      .h3cm-inspector dd { margin:0; overflow-wrap:anywhere; }
      .h3cm-prompt { max-height:90px; overflow:auto; padding:6px; border-radius:5px;
        background:var(--h3cm-panel); white-space:pre-wrap; overflow-wrap:anywhere; }
      .h3cm-attribution { padding:7px; border:1px dashed var(--h3cm-accent);
        border-radius:6px; background:color-mix(in srgb,var(--h3cm-accent) 8%,transparent); }
      .h3cm-attribution-title { margin-bottom:6px; font-weight:750; color:var(--h3cm-accent); }
      .h3cm-attribution-candidates { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:7px; }
      .h3cm-attribution-candidate-selected { border-color:var(--h3cm-accent) !important;
        color:var(--h3cm-accent) !important; }
      .h3cm-delete { flex:0 0 auto; padding:8px;
        border:1px solid var(--h3cm-border); border-radius:7px; }
      .h3cm-delete-body { max-height:135px; overflow:auto; }
      .h3cm-delete-details > summary { cursor:pointer; color:var(--h3cm-muted); margin-top:5px; }
      .h3cm-delete-blocked { border-color:var(--h3cm-danger); }
      .h3cm-delete-title { font-weight:700; }
      .h3cm-files,.h3cm-dependents { margin:5px 0 0; padding-left:18px; }
      .h3cm-dependent { color:var(--h3cm-danger); cursor:pointer; }
      .h3cm-delete-actions { margin-top:7px; flex-wrap:wrap; }
      .h3cm-delete-actions .h3cm-status { flex:1 1 180px; min-width:120px; }
      .h3cm-delete-button { margin-left:auto; color:var(--h3cm-danger) !important; }
      .h3cm-error { color:var(--h3cm-danger); }
      @media (max-width:760px) { .h3cm-main { grid-template-columns:1fr; }
        .h3cm-root { overflow:auto; } }
    `;
    document.head.append(style);
}

function mount(node) {
    if (node._h3CheckpointManagerMounted) return;
    node._h3CheckpointManagerMounted = true;
    injectStyles();
    node.properties ??= {};
    let selectionWidget = widget(node, "selection_json");
    try {
        const saved = JSON.parse(selectionWidget?.value || "null");
        if (saved?.run_name) node.properties.h3_working_branch_id = saved._branch_id ?? "main";
    } catch { /* Execution reports invalid selections; never silently repair them. */ }
    const selectedWorkingBranch = () => node.properties.h3_working_branch_id ?? "main";
    const state = {
        runs:[], runName:String(node.properties[RUN_PROPERTY] ?? ""), payload:null,
        scene:Number(node.properties[SCENE_PROPERTY]) || null,
        revision:String(node.properties[REVISION_PROPERTY] ?? ""),
        chapterTab:String(node.properties[CHAPTER_PROPERTY] ?? "all"),
        stage:CHECKPOINT_STAGES.some(item => item.id === node.properties[STAGE_PROPERTY])
            ? node.properties[STAGE_PROPERTY] : "original",
        variantKey:String(node.properties[VARIANT_PROPERTY] ?? ""),
        collapsedChapters:new Set(
            Array.isArray(node.properties[COLLAPSED_CHAPTERS_PROPERTY])
                ? node.properties[COLLAPSED_CHAPTERS_PROPERTY].map(String) : [],
        ),
        previewHeight:previewHeight(node.properties[PREVIEW_HEIGHT_PROPERTY]),
        selected:null, outputTip:null, previewTip:null, deletion:null, busy:false, requestToken:0,
        initialRefresh:true, attribution:null, attributionButton:null,
        workingBranches:[], defaultWorkingBranch:"main", graphCleanups:[], planMarkerSignature:"",
    };
    const root = element("div", "h3cm-root");
    const head = element("div", "h3cm-head");
    const title = element("div", "h3cm-title", "Checkpoint Manager");
    const summary = element("div", "h3cm-summary", "Select a saved run");
    head.append(title, summary);
    const runRow = element("div", "h3cm-run-row");
    const runSelect = element("select", "h3cm-run-select");
    const workingSelect = element("select", "h3cm-run-select");
    workingSelect.title = "Working branch for the assignments shown here and the manager's output namespace. Saved paths are shared: any complete path can be assigned here. Does not switch Plan Studio or the project default.";
    workingSelect.setAttribute("aria-label", "Working branch whose assignments are shown");
    const workingRow = element("label", "h3cm-working-row");
    workingRow.append(element("span", "", "Assignments shown for:"), workingSelect);
    const workingHelp = element("div", "h3cm-muted",
        "Working-branch names are labels, not resolution restrictions. Saved clips are shared: assign a path to Original or another named branch without moving or deleting clips.");
    workingSelect.addEventListener("change", async () => {
        if (state.busy) { workingSelect.value = selectedWorkingBranch(); return; }
        if (checkpointLocalSelection(selectionWidget?.value) && !window.confirm(
            "Replace this manager's pinned output with the selected working branch? The project default stays unchanged.")) {
            workingSelect.value = selectedWorkingBranch(); return;
        }
        node.properties.h3_working_branch_id = workingSelect.value;
        if (selectionWidget) selectionWidget.value = "";
        state.outputTip = null; state.selected = null;
        await refreshCheckpoints();
    });
    const refresh = button("Refresh", "Rescan saved runs and checkpoint revisions", () => void refreshRuns());
    const open = button("Open folder", "Open the selected run folder on the ComfyUI host", () => void openFolder());
    const deleteRun = button(
        "Delete run folder",
        "Permanently delete the selected output/h3_chains run folder after a content preview and two confirmations. Original input project assets are kept.",
        () => void deleteRunFolder(),
        "h3cm-run-delete",
    );
    deleteRun.disabled = true;
    runRow.append(runSelect, refresh, open, deleteRun);
    const outputRow = element("div", "h3cm-output");
    const outputSummary = element("div", "h3cm-output-summary");
    const outputScope = element("select", "h3cm-output-scope");
    outputScope.title = "Output scope only; never changes the project's active branch. Chapter output keeps original scene numbers.";
    outputScope.setAttribute("aria-label", "Checkpoint output scope");
    for (const [value, label] of [["project", "Selected branch + earlier chapters"], ["chapter", "Selected chapter only"]]) {
        const option = element("option", "", label);
        option.value = value;
        outputScope.append(option);
    }
    restoreOutputScope();
    outputScope.addEventListener("change", () => {
        node.properties[OUTPUT_SCOPE_PROPERTY] = outputScope.value;
        const local = checkpointLocalSelection(selectionWidget?.value);
        if (local) {
            // Change only the scope of the existing pin, not its browsed tip.
            writeOutputSelection(JSON.stringify({...local, output_scope:outputScope.value}));
        } else persistSelection();
        render();
    });
    const useLocal = button("Use branch locally",
        "Use the entire browsed branch in this workflow. Previewing an earlier clip does not trim it. Set the processing range downstream. No project activation or Plan change.",
        () => pinLocalOutput());
    const followSelection = button("Follow branch selection",
        "Release the local pin: output follows branch selections, not clip previews. The project's active branch is unchanged.",
        () => releaseLocalOutput());
    outputRow.append(outputSummary, outputScope, useLocal, followSelection);
    const chapterTabs = element("div", "h3cm-chapter-tabs");
    const stageTabs = element("div", "h3cm-stage-tabs");
    stageTabs.setAttribute("role", "tablist");
    stageTabs.setAttribute("aria-label", "Saved clip processing stage");
    const stageNote = element("div", "h3cm-stage-note");
    const scenes = element("div", "h3cm-scenes");
    const main = element("div", "h3cm-main");
    const branchesPanel = element("section", "h3cm-panel");
    const branchesTitle = element("div", "h3cm-panel-title", "Saved clip paths");
    const branchLegend = element("span", "h3cm-shared-legend", "shared clips shown once · bright line = output path · dashed badge = Plan");
    branchesTitle.append(branchLegend);
    const planContext = element("div", "h3cm-plan-context");
    const branches = element("div", "h3cm-branches");
    branchesPanel.append(branchesTitle, planContext, branches);
    const detail = element("section", "h3cm-panel h3cm-detail");
    const previewFrame = element("div", "h3cm-preview-frame");
    const preview = element("video", "h3cm-preview");
    preview.controls = true;
    preview.preload = "metadata";
    const previewResizer = element("div", "h3cm-preview-resizer");
    previewResizer.tabIndex = 0;
    previewResizer.setAttribute("role", "separator");
    previewResizer.setAttribute("aria-orientation", "horizontal");
    previewResizer.setAttribute("aria-label", "Resize clip preview height");
    previewResizer.title = "Drag to resize the clip preview. Double-click to reset.";
    previewFrame.append(preview, previewResizer);
    const audio = element("audio", "h3cm-audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.hidden = true;
    const inspector = element("dl", "h3cm-inspector");
    const attributionPanel = element("div", "h3cm-attribution");
    attributionPanel.hidden = true;
    const prompt = element("div", "h3cm-prompt");
    detail.append(previewFrame, audio, attributionPanel, inspector, prompt);
    main.append(branchesPanel, detail);
    const deletion = element("section", "h3cm-delete");
    const deletionTitle = element("div", "h3cm-delete-title", "Select a checkpoint revision.");
    const deletionBody = element("div", "h3cm-delete-body");
    const deletionDetails = element("details", "h3cm-delete-details");
    deletionDetails.append(element("summary", "", "Files, dependencies and recovery pins"), deletionBody);
    const deletionActions = element("div", "h3cm-delete-actions");
    let retireButtons = [];
    const status = element("div", "h3cm-status");
    const assignmentPanel = element("section", "h3cm-assignment");
    const assignmentContext = element("div", "h3cm-assignment-context");
    const assignmentActions = element("div", "h3cm-assignment-actions");
    const load = button("Load path + settings into Plan", "Assign this chapter lineage to the branch being browsed, load saved Plan settings, and arm the next Loop Start. Unlike assignment alone, this can switch the connected Plan's branch.", () => void loadSelected());
    const activate = button("Assign path", "Assign this chapter lineage to the named working branch; other working branches are unchanged", () => void activateSelected());
    const assignPlan = button("Assign to Plan Studio branch", "Assign this saved path to the connected Plan's branch, even when it is already active in the branch being browsed", () => void assignSelectedToPlan());
    const remove = button("Delete selected revision", "Delete an inactive leaf or roll back the active branch tip after confirmation", () => void deleteSelected(), "h3cm-delete-button");
    load.disabled = true;
    activate.disabled = true;
    remove.disabled = true;
    assignPlan.disabled = true;
    assignmentActions.append(activate, assignPlan, load);
    assignmentPanel.append(assignmentContext, assignmentActions);
    deletionActions.append(remove);
    deletion.append(deletionActions, deletionTitle, deletionDetails);
    root.append(head, runRow, workingRow, workingHelp, outputRow, stageTabs, stageNote, chapterTabs, scenes,
        assignmentPanel, status, main, deletion);

    function setPreviewHeight(value, persist = false) {
        state.previewHeight = previewHeight(value);
        previewFrame.style.height = `${state.previewHeight}px`;
        previewResizer.setAttribute("aria-valuemin", String(MIN_PREVIEW_HEIGHT));
        previewResizer.setAttribute("aria-valuemax", String(MAX_PREVIEW_HEIGHT));
        previewResizer.setAttribute("aria-valuenow", String(state.previewHeight));
        if (!persist) return;
        node.properties[PREVIEW_HEIGHT_PROPERTY] = state.previewHeight;
        node.graph?.setDirtyCanvas?.(true, true);
        app.graph?.setDirtyCanvas?.(true, true);
    }

    setPreviewHeight(state.previewHeight);
    previewResizer.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const pointerId = event.pointerId;
        const startY = event.clientY;
        const startHeight = state.previewHeight;
        previewResizer.setPointerCapture?.(pointerId);
        const move = (moveEvent) => {
            if (moveEvent.pointerId !== pointerId) return;
            moveEvent.preventDefault();
            setPreviewHeight(startHeight + moveEvent.clientY - startY);
        };
        const finish = (finishEvent) => {
            if (finishEvent.pointerId !== pointerId) return;
            previewResizer.removeEventListener("pointermove", move);
            previewResizer.removeEventListener("pointerup", finish);
            previewResizer.removeEventListener("pointercancel", finish);
            if (previewResizer.hasPointerCapture?.(pointerId)) {
                previewResizer.releasePointerCapture(pointerId);
            }
            setPreviewHeight(state.previewHeight, true);
        };
        previewResizer.addEventListener("pointermove", move);
        previewResizer.addEventListener("pointerup", finish);
        previewResizer.addEventListener("pointercancel", finish);
    });
    previewResizer.addEventListener("keydown", (event) => {
        let next = state.previewHeight;
        const step = event.shiftKey ? 48 : 16;
        if (event.key === "ArrowUp") next -= step;
        else if (event.key === "ArrowDown") next += step;
        else if (event.key === "Home") next = MIN_PREVIEW_HEIGHT;
        else if (event.key === "End") next = MAX_PREVIEW_HEIGHT;
        else return;
        event.preventDefault();
        event.stopPropagation();
        setPreviewHeight(next, true);
    });
    previewResizer.addEventListener("dblclick", (event) => {
        event.preventDefault();
        event.stopPropagation();
        setPreviewHeight(DEFAULT_PREVIEW_HEIGHT, true);
    });

    function activePlanRun() {
        const plan = upstreamPlanNode(node);
        return String(widget(plan, "run_name")?.value ?? "").trim();
    }

    function currentPlanMarker() {
        const plan = upstreamPlanNode(node, true);
        if (!plan) return null; // Never guess from an unrelated workflow/tab.
        try {
            const authored = JSON.parse(String(widget(plan, "plan_json")?.value ?? ""));
            const branch = nodeType(plan) === "MiniMaxH3ChainPlanStudio"
                ? widget(plan, "working_branch_id")?.value ?? authored._branch_id ?? "main"
                : authored._branch_id ?? "main";
            if (!/^(main|[0-9a-f]{32})$/.test(branch)) return null;
            const studio = nodeType(plan) === "MiniMaxH3ChainPlanStudio" ? plan
                : connectedNode(plan, "MiniMaxH3ChainPlanStudio");
            return {run:String(widget(plan, "run_name")?.value ?? "").trim(), branch,
                label:studio && (studio === plan || upstreamPlanNode(studio) === plan) ? "In Plan Studio" : "In connected Plan"};
        } catch { return null; }
    }

    function workingBranchName(id = selectedWorkingBranch()) {
        const name = state.workingBranches.find(item => item.id === id)?.name
            ?? (id === "main" ? "Original" : String(id).slice(0, 8));
        return state.workingBranches.filter(item => item.name === name).length > 1
            ? `${name} (${id === "main" ? "main" : String(id).slice(0, 8)})` : name;
    }

    function renderPlanContext() {
        const marker = currentPlanMarker();
        planContext.textContent = marker
            ? `${marker.label}: ${marker.run} / ${workingBranchName(marker.branch)}`
                + (marker.run !== state.runName || marker.branch !== selectedWorkingBranch()
                    ? " · different from the assignments shown here; assigning here does not switch the Plan" : state.stage === "original"
                        ? (state.payload?.revisions?.some(item => item.active)
                            ? " · saved path marked below" : " · no saved active path yet")
                        : " · saved path marked on the Original tab")
            : "Connect a Plan or Plan Studio to show its working-branch marker.";
        workingSelect.replaceChildren();
        for (const item of state.workingBranches) {
            const isPlan = marker?.run === state.runName && marker.branch === item.id;
            const option = element("option", "", `${workingBranchName(item.id)}${item.id === state.defaultWorkingBranch ? " · project default" : ""}${isPlan ? " · " + marker.label : ""}`);
            option.value = item.id; workingSelect.append(option);
        }
        workingSelect.value = selectedWorkingBranch();
    }

    function restoreOutputScope() {
        let scope = node.properties[OUTPUT_SCOPE_PROPERTY];
        try { scope = JSON.parse(selectionWidget?.value || "{}").output_scope ?? scope; }
        catch { /* Preserve the saved scope even while selection is empty. */ }
        outputScope.value = scope === "chapter" ? "chapter" : "project";
    }

    function outputSelectionForScope(value) {
        // Scope is an explicit UI choice, not the range of the browsed branch.
        // Keep the exact pinned lineage; only repair a stale/missing scope flag.
        try {
            const selection = JSON.parse(value);
            if (!selection || typeof selection !== "object" || Array.isArray(selection)) return value;
            if (selection.output_scope != null && !["project", "chapter"].includes(selection.output_scope)) return value;
            if ((selection.output_scope ?? "project") === outputScope.value) return value;
            return JSON.stringify({...selection, output_scope:outputScope.value});
        } catch { return value; } // Invalid selections still fail backend validation.
    }

    function bindSelectionSerializer() {
        selectionWidget = widget(node, "selection_json");
        if (!selectionWidget) return;
        selectionWidget.hidden = true;
        selectionWidget.type = "hidden";
        selectionWidget.computeSize = () => [0, -4];
        if (selectionWidget._h3ScopeSerializer) return;
        const previous = selectionWidget.serializeValue;
        selectionWidget.serializeValue = function (...args) {
            const value = previous ? previous.apply(this, args) : this.value;
            const normalize = (saved) => branchSelectionJson(outputSelectionForScope(saved ?? this.value), selectedWorkingBranch());
            return value?.then ? value.then(normalize) : normalize(value);
        };
        selectionWidget._h3ScopeSerializer = true;
    }

    function persistSelection() {
        const previousRun = node.properties[RUN_PROPERTY];
        const previousScene = node.properties[SCENE_PROPERTY];
        const previousRevision = node.properties[REVISION_PROPERTY];
        const previousChapter = node.properties[CHAPTER_PROPERTY];
        const previousScope = node.properties[OUTPUT_SCOPE_PROPERTY];
        node.properties[RUN_PROPERTY] = state.runName;
        node.properties[SCENE_PROPERTY] = state.scene;
        node.properties[REVISION_PROPERTY] = state.revision;
        node.properties[CHAPTER_PROPERTY] = state.chapterTab;
        node.properties[OUTPUT_SCOPE_PROPERTY] = outputScope.value;
        let changed = previousRun !== state.runName ||
            previousScene !== state.scene ||
            previousRevision !== state.revision ||
            previousChapter !== state.chapterTab || previousScope !== outputScope.value;
        if (selectionWidget && state.stage === "original") {
            const value = branchSelectionJson(checkpointOutputSelectionJson(
                selectionWidget.value, state.payload, state.runName, state.outputTip,
                chapterRangeFor(state.outputTip), outputScope.value), selectedWorkingBranch());
            if (selectionWidget.value !== value) {
                selectionWidget.value = value;
                selectionWidget.callback?.(value);
                changed = true;
            }
        }
        if (changed) node.graph?.setDirtyCanvas?.(true, true);
    }

    function writeOutputSelection(value) {
        if (!selectionWidget) return;
        node.properties[OUTPUT_SCOPE_PROPERTY] = outputScope.value;
        selectionWidget.value = branchSelectionJson(value, selectedWorkingBranch());
        selectionWidget.callback?.(selectionWidget.value);
        node.graph?.setDirtyCanvas?.(true, true);
    }

    function pinLocalOutput() {
        if (!["original", "derope"].includes(state.stage) || state.busy || state.attribution || !selectionWidget) return;
        try {
            const tip = state.previewTip;
            if (!tip) throw new Error("Choose a branch heading first; this clip belongs to more than one branch.");
            writeOutputSelection(state.stage === "derope"
                ? checkpointDeropeSelectionJson(state.payload, state.runName, tip, currentVariant(), chapterRangeFor(tip), outputScope.value)
                : checkpointLocalSelectionJson(state.payload, state.runName, tip, chapterRangeFor(tip), outputScope.value));
            state.outputTip = tip;
            status.className = "h3cm-status";
            status.textContent = state.stage === "derope"
                ? "DeRoPE branch selected for deferred processing. Unsaved scenes use the selected original take; missing/corrupt full latents fail explicitly. Set the range downstream. Project unchanged."
                : "Whole original branch saved for this workflow. Set start/end on the downstream range selector. Project active branch and connected Plan unchanged.";
            render();
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        }
    }

    function releaseLocalOutput() {
        if (state.stage !== "original" || state.busy || !checkpointLocalSelection(selectionWidget?.value)) return;
        if (!state.previewTip) return;
        state.outputTip = state.previewTip;
        writeOutputSelection(checkpointSelectionJson(
            state.payload, state.runName, state.outputTip, chapterRangeFor(state.outputTip), outputScope.value));
        status.className = "h3cm-status";
        status.textContent = "Output follows whole-branch selections. Clip previews do not change the processing range.";
        render();
    }

    function localRevisionKeys() {
        const local = checkpointLocalSelection(selectionWidget?.value);
        return new Set(local?.run_name === state.runName
            ? (local.lineage ?? []).filter((item) => local.output_scope !== "chapter" || item.scene >= local.scope_start_scene)
                .map((item) => checkpointRevisionKey(item.scene, item.revision)) : []);
    }

    function renderOutputSelection() {
        const local = checkpointLocalSelection(selectionWidget?.value);
        outputScope.disabled = state.busy || state.stage !== "original";
        if (local) restoreOutputScope();
        useLocal.textContent = state.stage === "derope" ? "Use DeRoPE branch locally" : "Use branch locally";
        useLocal.disabled = state.busy || Boolean(state.attribution) || !selectionWidget || !["original", "derope"].includes(state.stage) || !state.previewTip?.ready;
        useLocal.title = state.stage === "derope"
            ? "Use this saved DeRoPE branch as the deferred source, with original takes for unsaved scenes. No project activation. A full recovered latent is required."
            : state.previewTip
            ? "Save the entire browsed branch for this workflow; set start/end on the downstream range selector. No project activation or Plan change."
            : "Choose a branch heading first. A shared clip alone does not identify which branch to use.";
        followSelection.disabled = state.busy || !local || state.stage !== "original" || !state.previewTip;
        outputSummary.className = "h3cm-output-summary";
        const outputValue = outputSelectionForScope(selectionWidget?.value || "null");
        outputSummary.textContent = checkpointOutputSummary(outputValue);
        if (local) {
            if (state.payload && local.run_name === state.runName) {
                const available = new Set((state.payload.revisions ?? []).filter((item) => item.ready)
                    .map((item) => checkpointRevisionKey(item.scene, item.revision)));
                if ((local.lineage ?? []).some((item) =>
                    (local.output_scope !== "chapter" || item.scene >= local.scope_start_scene) &&
                    !available.has(checkpointRevisionKey(item.scene, item.revision)))) {
                    outputSummary.className += " h3cm-error";
                    outputSummary.textContent += " · pinned checkpoint unavailable; reselect explicitly (no fallback)";
                }
            }
        }
        let saved = null;
        try { saved = JSON.parse(outputValue); } catch { /* invalid selections fail at execution */ }
        if (saved && state.payload && saved.run_name === state.runName) {
            const tip = saved.lineage?.at(-1);
            const range = {start:saved.scope_start_scene, end:saved.scope_end_scene};
            const fullTip = checkpointOutputBranchTip(state.payload, tip, range);
            if (!fullTip || Number(fullTip.scene) > Number(tip?.scene)) {
                outputSummary.textContent += " · old partial selection: choose the desired branch heading"
                    + (local ? ", then Use branch locally" : "") + " to include its later clips";
            }
        }
    }

    function setBusy(value, message = "") {
        state.busy = Boolean(value);
        runSelect.disabled = state.busy;
        workingSelect.disabled = state.busy;
        refresh.disabled = state.busy;
        open.disabled = state.busy || !state.runName;
        deleteRun.disabled = state.busy || !state.runName;
        load.disabled = state.busy || Boolean(state.attribution) || !canLoadSelected();
        activate.disabled = state.busy || Boolean(state.attribution) || !canActivateSelected();
        assignPlan.disabled = state.busy || Boolean(state.attribution) || !canAssignSelectedToPlan();
        remove.disabled = state.busy || Boolean(state.attribution) || !state.deletion?.allowed;
        for (const control of retireButtons) control.disabled = state.busy || Boolean(state.attribution);
        if (state.attributionButton) {
            state.attributionButton.disabled = state.busy || !state.attribution?.candidate;
        }
        renderOutputSelection();
        renderStageTabs();
        if (message) status.textContent = message;
    }

    function selectedLineage() {
        return checkpointRevisionLineage(
            state.payload, state.selected, selectedChapterRange());
    }

    function canLoadSelected() {
        const lineage = selectedLineage();
        const scope = selectedChapterRange();
        return Boolean(state.stage === "original" && state.selected?.ready &&
            state.selected?.take_kind !== "editorial_alternate" &&
            lineage.length === Number(state.selected.scene) - scope.start + 1);
    }

    function canActivateSelected() {
        return ["activate", "rollback"].includes(selectedActivationMode());
    }

    function canAssignSelectedToPlan() {
        const marker = currentPlanMarker();
        // "Active" belongs to the manager's browsing namespace, not necessarily
        // to the Plan being edited. Never use that flag to block cross-branch assignment.
        return Boolean(marker && marker.run === state.runName && canLoadSelected());
    }

    function selectedActivationMode() {
        if (state.stage !== "original") return "disabled";
        return checkpointActivationMode(
            state.payload, state.selected, selectedChapterRange());
    }

    function selectRevision(record, requestDeletion = true, variantKey = "", branchTip = null) {
        state.attribution = null;
        state.variantKey = variantKey;
        node.properties[VARIANT_PROPERTY] = variantKey;
        state.selected = record;
        state.scene = record ? Number(record.scene) : null;
        state.revision = record ? String(record.revision) : "";
        if (state.stage === "original") {
            state.previewTip = branchTip ?? checkpointOutputBranchTip(
                state.payload, record, chapterRangeFor(record), state.previewTip ?? state.outputTip);
        }
        state.deletion = null;
        state.requestToken += 1;
        persistSelection();
        render();
        if (requestDeletion) void refreshDeletionPreview();
    }

    function selectOutputBranch(tip) {
        if (state.stage !== "original") return;
        state.outputTip = tip;
        selectRevision(tip, true, "", tip);
    }

    function stageLabel() {
        return CHECKPOINT_STAGES.find(item => item.id === state.stage)?.label ?? "Original";
    }

    function currentVariant() {
        const records = checkpointStageVariants(state.payload, state.stage);
        const exact = records.find(item => item.key === state.variantKey);
        if (exact) return exact;
        // A vanished explicitly browsed take must not silently turn into
        // another take on refresh. A key belonging to another tab is fine.
        if (state.variantKey && !(state.payload?.processing_variants ?? []).some(item => item.key === state.variantKey)) return null;
        return state.selected ? checkpointStageVariants(state.payload, state.stage, state.selected)[0] ?? null : null;
    }

    function selectVariant(record, original = null, branchTip = null) {
        original ??= (state.payload?.revisions ?? []).find(item => (record.originals ?? []).some(
            source => checkpointRevisionKey(item.scene, item.revision) === checkpointRevisionKey(source.scene, source.revision)));
        state.previewTip = branchTip ?? checkpointOutputBranchTip(state.payload, original, chapterRangeFor(original), state.previewTip);
        selectRevision(original, true, record.key);
    }

    function selectStage(stage) {
        if (state.busy) return;
        state.stage = stage;
        state.attribution = null;
        state.deletion = null;
        state.requestToken += 1;
        node.properties[STAGE_PROPERTY] = stage;
        if (stage === "original") state.previewTip = checkpointOutputBranchTip(
            state.payload, state.selected, chapterRangeFor(state.selected));
        node.graph?.setDirtyCanvas?.(true, true);
        // A view switch never writes selection_json or promotes a branch.
        render();
        void refreshDeletionPreview();
    }

    function renderStageTabs() {
        stageTabs.replaceChildren();
        for (const stage of CHECKPOINT_STAGES) {
            const count = stage.id === "original" ? (state.payload?.revisions ?? []).filter(item => sceneVisible(item.scene)).length
                : checkpointStageVariants(state.payload, stage.id, null, activeChapterRange()).length;
            if (["pixel_upscale", "other"].includes(stage.id) && !count && state.stage !== stage.id) continue;
            const tab = button(`${stage.label} · ${count}`, `Browse saved ${stage.label} versions; does not activate a generation branch`,
                () => selectStage(stage.id), "h3cm-stage-tab");
            tab.setAttribute("role", "tab");
            tab.setAttribute("aria-selected", String(state.stage === stage.id));
            tab.disabled = state.busy;
            stageTabs.append(tab);
        }
        stageNote.textContent = state.stage === "original"
            ? "Preview only. ALT takes sit beneath their original; the used final-cut take is marked."
            : `${stageLabel()} saved branches. Browsing does not change output.`;
        stageNote.title = "Clip clicks preview only. Select path chooses output unless pinned. Assign path changes the named working branch. "
            + "Save # and Latest describe available takes, not the output or branch assignment. "
            + "Related forks stay together; arrows follow saved history and shared clips appear once. "
            + "ALT changes final-cut picture only; following scenes still use the original generation checkpoint.";
        if (state.stage === "derope") stageNote.title += " Select a saved take, then Use DeRoPE branch locally for deferred upscaling. Unsaved scenes use their original take.";
        const warnings = state.payload?.processing_variant_warnings ?? [];
        if (warnings.length) stageNote.textContent += ` ${warnings.length} processing metadata warning(s): ${warnings[0]}`;
        if (state.stage === "original" && state.payload?.editorial_notices?.length) {
            stageNote.textContent += ` ${state.payload.editorial_notices.length} saved editorial choice(s) do not apply to this base path.`;
            stageNote.title += ` Saved editorial choices not applied: ${state.payload.editorial_notices.join("; ")}. The choices and ALT files are kept for their original base clips.`;
        }
        stageNote.hidden = !stageNote.textContent;
        branchLegend.textContent = "bright line = output path · dashed arrow = reuse candidate · dashed badge = Plan";
    }

    function selectAttribution(parent, slot) {
        const candidates = slot?.candidates ?? [];
        if (!parent || !(candidates.length || slot?.blocked_candidates?.length)) return;
        state.selected = parent;
        state.scene = Number(parent.scene);
        state.revision = String(parent.revision);
        state.attribution = {
            parent,
            scene:Number(slot.scene),
            candidates,
            blocked:slot?.blocked_candidates ?? [],
            candidate:candidates[0] ?? null,
        };
        persistSelection();
        render();
    }

    function chapterRanges() {
        const chapters = Array.isArray(state.payload?.editorial?.chapters)
            ? state.payload.editorial.chapters : [];
        const ordered = chapters.slice().sort(
            (left, right) => Number(left.start_scene) - Number(right.start_scene),
        );
        const maximum = Math.max(
            0,
            ...(state.payload?.scenes ?? []).map((scene) => Number(scene.scene) || 0),
            ...(state.payload?.processing_variants ?? []).map((scene) => Number(scene.scene) || 0),
            ...(state.payload?.editorial?.scene_order ?? []).map(
                (scene) => Number(scene.scene) || 0,
            ),
        );
        const ranges = ordered.map((chapter, index) => ({
            id:String(chapter.id),
            title:String(chapter.title || `Chapter ${index + 1}`),
            text:String(chapter.text || ""),
            start:Number(chapter.start_scene),
            end:index + 1 < ordered.length
                ? Number(ordered[index + 1].start_scene) - 1 : maximum,
        })).filter((chapter) => Number.isFinite(chapter.start) && chapter.start > 0);
        if (ranges.length && ranges[0].start > 1) {
            ranges.unshift({id:"unassigned", title:"Unassigned", text:"", start:1, end:ranges[0].start - 1});
        }
        return ranges;
    }

    function activeChapterRange() {
        if (state.chapterTab === "all") return null;
        return chapterRanges().find((chapter) => chapter.id === state.chapterTab) ?? null;
    }

    function selectedChapterRange() {
        return chapterRangeFor(state.selected);
    }

    function chapterRangeFor(record) {
        const scene = Number(record?.scene);
        const ranges = chapterRanges();
        const selected = ranges.find((range) =>
            scene >= range.start && scene <= range.end);
        if (selected) return selected;
        const maximum = Math.max(
            1,
            ...(state.payload?.scenes ?? []).map(
                (item) => Number(item.scene) || 0),
            ...(state.payload?.editorial?.scene_order ?? []).map(
                (item) => Number(item.scene) || 0),
        );
        return {id:"all", title:"All scenes", text:"", start:1, end:maximum};
    }

    function sceneVisible(scene) {
        const range = activeChapterRange();
        const number = Number(scene);
        return !range || (number >= range.start && number <= range.end);
    }

    function chapterCollapseKey(range) {
        return `${state.runName}:${String(range.id)}`;
    }

    function setChapterCollapsed(range, collapsed) {
        const key = chapterCollapseKey(range);
        if (collapsed) state.collapsedChapters.add(key);
        else state.collapsedChapters.delete(key);
        node.properties[COLLAPSED_CHAPTERS_PROPERTY] = [
            ...state.collapsedChapters,
        ].sort();
        node.graph?.setDirtyCanvas?.(true, true);
        renderBranches();
    }

    function selectChapterTab(chapterId) {
        state.chapterTab = chapterId;
        state.variantKey = "";
        node.properties[VARIANT_PROPERTY] = "";
        const visibleScenes = (state.payload?.scenes ?? []).filter(
            (scene) => sceneVisible(scene.scene),
        );
        if (!sceneVisible(state.selected?.scene) && visibleScenes.length) {
            const scene = visibleScenes.at(-1);
            state.selected = selectedCheckpointRevision(state.payload, scene.scene);
            state.scene = Number(state.selected?.scene ?? scene.scene);
            state.revision = String(state.selected?.revision ?? "");
            state.deletion = null;
        }
        persistSelection();
        render();
        if (state.selected) void refreshDeletionPreview();
    }

    function renderChapterTabs() {
        chapterTabs.replaceChildren();
        const ranges = chapterRanges();
        chapterTabs.hidden = !ranges.length;
        if (!ranges.length) {
            state.chapterTab = "all";
            return;
        }
        const valid = new Set(["all", ...ranges.map((chapter) => chapter.id)]);
        if (!valid.has(state.chapterTab)) state.chapterTab = "all";
        const tabs = [{id:"all", title:"All scenes", text:""}, ...ranges];
        for (const chapter of tabs) {
            const item = button(
                chapter.title,
                chapter.text || (chapter.id === "all"
                    ? "Show every saved scene" : `Show scenes ${chapter.start}–${chapter.end}`),
                () => selectChapterTab(chapter.id),
                "h3cm-chapter-tab",
            );
            if (chapter.id === state.chapterTab) {
                item.classList.add("h3cm-chapter-selected");
            }
            chapterTabs.append(item);
        }
    }

    function renderScenes() {
        scenes.replaceChildren();
        for (const scene of state.payload?.scenes ?? []) {
            if (!sceneVisible(scene.scene)) continue;
            const original = state.stage === "original";
            const count = original ? scene.revision_count : checkpointStageVariants(state.payload, state.stage)
                .filter(item => Number(item.scene) === Number(scene.scene)).length;
            const noun = original ? "take" : "version";
            const label = `${scene.scene} · ${scene.scene_id} · ${count} ${noun}${count === 1 ? "" : "s"}`;
            const item = button(label, original ? `${formatCheckpointBytes(scene.bytes)} saved for this scene`
                : `${count} saved ${stageLabel()} versions for this scene`, () => {
                selectRevision(selectedCheckpointRevision(state.payload, scene.scene));
            }, "h3cm-scene");
            if (Number(scene.scene) === Number(state.scene)) item.classList.add("h3cm-scene-selected");
            scenes.append(item);
        }
    }

    function appendSaveOrder(card, record, order) {
        const saved = order.get(checkpointGraphKey(state.stage, record, record.profile_path));
        const reused = record.adopted_from_revision ? "Reused clip · " : "";
        card.append(element("small", `h3cm-save-order${saved?.latest ? " h3cm-latest-label" : ""}`,
            reused + (saved?.label ?? "Save order unknown")));
        card.append(element("small", "h3cm-save-time", `Saved: ${localTime(record.created_at)}`));
        card.title += `\n${reused}${saved?.label ?? "Save order unknown"}\nSaved: ${localTime(record.created_at)}`
            + "\nOrder is per scene among available saved takes, not branch order; equal timestamps are tied.";
    }

    function renderBranchRows(container, rows, order) {
        const original = state.stage === "original";
        const model = checkpointForkGraph(rows.map(row => row.attribution_slot && !sceneVisible(row.attribution_slot.scene)
            ? {...row, attribution_slot:null} : row), state.stage);
        const output = checkpointGraphOutput(outputSelectionForScope(selectionWidget?.value),
            state.runName, selectedWorkingBranch(), state.stage);
        const marker = currentPlanMarker();
        const inPlan = marker?.run === state.runName && marker.branch === selectedWorkingBranch();
        const scroll = element("div", "h3cm-fork-scroll");
        const graph = element("div", "h3cm-fork-graph");
        graph.style.gridTemplateColumns = `repeat(${model.columns}, 180px)`;
        graph.setAttribute("aria-label", `${stageLabel()} saved revision forks`);
        const cards = new Map();
        const localKeys = localRevisionKeys();
        for (const item of model.nodes) {
            const cell = element("div", "h3cm-fork-node");
            cell.style.gridColumn = String(item.column + 1);
            cell.style.gridRow = String(item.lane + 1);
            cell.dataset.graphKey = item.key;
            const revision = original ? item.entry : item.entry.record;
            let card;
            if (original) {
                card = button(
                    `S${revision.scene} · ${revision.revision.slice(0, 8)}`,
                    revision.prompt_preview || revision.scene_id,
                    () => selectRevision(revision), "h3cm-revision",
                );
                const selected = state.selected?.scene === revision.scene &&
                    state.selected?.revision === revision.revision;
                appendSaveOrder(card, revision, order);
                card.append(element("small", "", `${selected ? "previewed · " : ""}${revision.active ? `Assigned: ${workingBranchName()}` : "Saved take"}${revision.ready ? "" : " · broken"}`));
                if (revision.compatibility?.width && revision.compatibility?.height) {
                    card.append(element("small", "h3cm-muted", `${revision.compatibility.width}×${revision.compatibility.height}`));
                }
                if (localKeys.has(item.key)) card.append(element("small", "h3cm-local-label", "local output"));
                if (selected) card.classList.add("h3cm-revision-selected");
            } else if (revision) card = variantCard(revision, order);
            else {
                card = element("div", "h3cm-revision h3cm-revision-empty",
                    `S${item.scene} · ${String(item.entry.revision).slice(0, 8)}`);
                card.append(element("small", "", "Missing saved take"));
                card.title = "This exact take is missing or its identity does not match. No other version is substituted.";
            }
            const sharedCount = item.paths.length;
            if (sharedCount > 1) {
                card.dataset.sharedKey = item.key;
                card.append(element("small", "h3cm-muted", `shared ×${sharedCount}`));
            }
            if (output.nodes.has(item.key)) {
                card.classList.add("h3cm-output-path");
                card.append(element("small", "h3cm-output-path-label", output.tip === item.key ? "Output path · end" : "Output path"));
            }
            cell.append(card); cards.set(item.key, card);
            if (original) {
                const alternates = revision.alternates ?? [];
                const usedAlternate = alternates.find(alternate => alternate.used_in_final_cut);
                if (usedAlternate) {
                    const marker = element("small", "h3cm-final-cut-alt",
                        `Final cut: ALT · ${String(usedAlternate.revision).slice(0, 8)}`);
                    marker.title = "Picture-only alternate is used in the final cut. Generation context still uses this original checkpoint.";
                    card.append(marker);
                }
                if (alternates.length) {
                    const group = element("span", "h3cm-alternates");
                    for (const alternate of alternates) {
                        const alt = button(
                            `ALT · ${String(alternate.revision).slice(0, 8)}`,
                            alternate.prompt_preview || alternate.prompt ||
                                "Prompt-only editorial alternate",
                            () => selectRevision(alternate),
                            "h3cm-alternate",
                        );
                        appendSaveOrder(alt, alternate, order);
                        if (alternate.used_in_final_cut) {
                            alt.classList.add("h3cm-alternate-used");
                            alt.append(element("small", "", "used in final cut"));
                        } else {
                            alt.append(element("small", "", alternate.ready ? "available take" : "Missing artifacts"));
                        }
                        if (state.selected?.scene === alternate.scene
                                && state.selected?.revision === alternate.revision) {
                            alt.classList.add("h3cm-revision-selected");
                        }
                        group.append(alt);
                    }
                    cell.append(group);
                }
            }
            for (const {row:branch} of item.ends) {
                const end = element("div", "h3cm-branch");
                const header = element("div", "h3cm-branch-head");
                const tip = original ? branch.revisions.at(-1) : branch.entries.at(-1);
                const label = original
                    ? `Select path · S${branch.revisions[0].scene}–S${tip.scene} · ${tip.revision.slice(0, 8)}`
                    : `${branch.history_known ? "Branch" : "Take"} ${String(tip.revision).slice(0, 8)}`;
                header.append(element("span", branch.active ? "h3cm-branch-active" : "", label));
                const selected = original ? state.previewTip?.scene === tip.scene && state.previewTip?.revision === tip.revision
                    : state.variantKey === tip.metadata_path;
                if (selected) end.classList.add("h3cm-branch-selected");
                if (original || tip.record) {
                    const choose = () => {
                        if (state.busy) return;
                        if (original) {
                            selectOutputBranch(tip);
                            status.textContent = checkpointLocalSelection(selectionWidget?.value)
                                ? "Path selected for preview/assignment. Output is pinned and unchanged; use Use branch locally to replace the pin."
                                : "Output path selected. Working-branch assignments and Plan Studio are unchanged.";
                        } else selectVariant(tip.record);
                    };
                    header.role = "button"; header.tabIndex = 0;
                    header.title = original ? `Select the saved path through scene ${tip.scene}. Output follows unless pinned. Use Assign path to change a working branch; set the processing range downstream.`
                        : "Preview this saved processing branch tip; output stays unchanged";
                    header.addEventListener("click", choose);
                    header.addEventListener("keydown", event => {
                        if (!["Enter", " "].includes(event.key)) return;
                        event.preventDefault(); choose();
                    });
                }
                if (original && branch.active && inPlan) header.append(element("span", "h3cm-plan-marker", marker.label));
                if (!original && branch.latest) header.append(element("span", "h3cm-latest-label", "Latest save"));
                end.append(header);
                if (original && branch.active) {
                    end.append(element("span", "h3cm-assignment-badge", `Assigned to ${workingBranchName()} through S${tip.scene}`));
                    if (model.edges.some(edge => edge.from === item.key && edge.kind !== "reuse")) {
                        end.append(element("small", "h3cm-muted", "Saved continuations exist; select their last take to assign the longer path."));
                    }
                }
                if (!original) {
                    const description = branch.history_known
                        ? `Scenes ${branch.entries[0].scene}–${tip.scene} · ${branch.entries.length - branch.missing_count} saved`
                            + (branch.missing_count ? ` · ${branch.missing_count} missing — history incomplete` : "")
                        : "Branch history unavailable — standalone saved take";
                    end.append(element("small", "h3cm-muted", `${branch.profile} · ${description}`));
                }
                cell.append(end);
            }
            graph.append(cell);
        }
        for (const item of model.slots) {
            const cell = element("div", "h3cm-fork-slot");
            cell.style.gridColumn = String(item.column + 1);
            cell.style.gridRow = String(item.lane + 1);
            cell.dataset.graphKey = item.key;
            const count = item.slot.candidates?.length ?? 0;
            const empty = button(`Reuse for S${item.scene}`,
                `Inspect saved candidates for scene ${item.scene} after S${item.parent.scene} · ${item.parent.revision.slice(0, 8)}; nothing is attached until confirmed`,
                () => selectAttribution(item.parent, item.slot), "h3cm-revision h3cm-revision-empty");
            empty.append(element("small", "", `${count} available candidate${count === 1 ? "" : "s"} · not a saved scene`));
            empty.append(element("small", "", `After S${item.parent.scene} · ${item.parent.revision.slice(0, 8)}`));
            if (state.attribution?.parent.scene === item.parent.scene && state.attribution?.parent.revision === item.parent.revision)
                empty.classList.add("h3cm-revision-empty-selected");
            cell.append(empty); graph.append(cell); cards.set(item.key, empty);
        }
        scroll.append(graph); container.append(scroll);
        state.graphCleanups.push(mountCheckpointGraphEdges(graph, model, cards, output, document, window));
    }

    function variantCard(record, order) {
        const card = button(`S${record.scene} · ${record.revision.slice(0, 8)}`,
            `${record.profile_path}\nCreated: ${localTime(record.created_at)}\n${checkpointVariantLatentStatus(record)}`,
            () => selectVariant(record), "h3cm-revision h3cm-processing-variant");
        appendSaveOrder(card, record, order);
        card.append(element("small", "", record.profile));
        card.append(element("small", "", `${record.width || "?"}×${record.height || "?"} · ${record.ready ? "saved" : "missing artifacts"}`));
        card.append(element("small", "", record.latent_saved ? "full latent saved" : "full latent not saved"));
        card.append(element("small", "h3cm-muted", (record.originals ?? []).length
            ? `Original: ${(record.originals ?? []).map(item => String(item.revision).slice(0, 8)).join(" / ")}`
            : "Original unavailable or mismatched"));
        if (currentVariant()?.key === record.key) card.classList.add("h3cm-revision-selected");
        return card;
    }

    function renderBranches() {
        for (const cleanup of state.graphCleanups) cleanup();
        state.graphCleanups = [];
        renderPlanContext();
        branches.replaceChildren();
        const originals = state.payload?.revisions ?? [];
        const order = checkpointSaveOrder(state.stage === "original"
            ? [...originals, ...originals.flatMap(record => record.alternates ?? [])]
            : checkpointStageVariants(state.payload, state.stage), state.stage);
        const ranges = chapterRanges();
        if (!ranges.length) {
            const rows = state.stage === "original" ? checkpointBranchRows(state.payload)
                : checkpointProcessingBranchRows(state.payload, state.stage);
            if (rows.length) renderBranchRows(branches, rows, order);
            else branches.append(element(
                "div", "h3cm-muted", "No versioned checkpoints were found.",
            ));
            return;
        }
        const visibleRanges = state.chapterTab === "all"
            ? ranges : ranges.filter((range) => range.id === state.chapterTab);
        let rendered = 0;
        for (const range of visibleRanges) {
            const rows = state.stage === "original" ? checkpointChapterBranchRows(state.payload, range)
                : checkpointProcessingBranchRows(state.payload, state.stage, range);
            if (!rows.length) continue;
            if (state.chapterTab === "all") {
                const section = element("section", "h3cm-branch-chapter");
                const collapseKey = chapterCollapseKey(range);
                const collapsed = state.collapsedChapters.has(collapseKey);
                section.classList.toggle(
                    "h3cm-branch-chapter-collapsed", collapsed);
                const heading = button(
                    "",
                    `${collapsed ? "Expand" : "Collapse"} ${range.title}`,
                    () => setChapterCollapsed(range, !collapsed),
                    "h3cm-branch-chapter-title",
                );
                heading.setAttribute("aria-expanded", String(!collapsed));
                heading.append(
                    element("span", "h3cm-branch-chapter-caret", collapsed ? "▸" : "▾"),
                    element("span", "", range.title),
                    element("span", "h3cm-muted", `Scenes ${range.start}–${range.end}`),
                );
                const body = element("div", "h3cm-branch-chapter-body");
                body.hidden = collapsed;
                if (!collapsed) renderBranchRows(body, rows, order);
                section.append(heading, body);
                branches.append(section);
            } else renderBranchRows(branches, rows, order);
            rendered += rows.length;
        }
        if (!rendered) {
            branches.append(element(
                "div", "h3cm-muted", "No versioned checkpoints were found in this chapter.",
            ));
        }
    }

    function addInspector(label, value) {
        inspector.append(element("dt", "", label), element("dd", "", value));
    }

    function renderDetail() {
        inspector.replaceChildren();
        attributionPanel.replaceChildren();
        attributionPanel.hidden = !state.attribution;
        state.attributionButton = null;
        const processing = state.stage !== "original";
        const record = processing ? currentVariant() : state.attribution?.candidate ?? state.selected;
        if (!record) {
            preview.removeAttribute("src");
            delete preview.dataset.source;
            preview.load();
            audio.hidden = true;
            audio.removeAttribute("src");
            delete audio.dataset.source;
            prompt.textContent = processing ? (state.variantKey
                ? "The previously browsed processing take is unavailable. Select a saved version; no other take has been substituted."
                : `No saved ${stageLabel()} version for this source revision. The original clip has not been substituted.`)
                : "Select a revision from the branch graph.";
            return;
        }
        if (state.attribution) {
            const attribution = state.attribution;
            attributionPanel.append(element(
                "div", "h3cm-attribution-title",
                `Attribute an existing scene ${attribution.scene} candidate to branch ${attribution.parent.revision.slice(0, 8)}`,
            ));
            const candidates = element("div", "h3cm-attribution-candidates");
            for (const candidate of attribution.candidates) {
                const choice = button(
                    `${candidate.revision.slice(0, 8)} · seed ${candidate.seed || "?"}`,
                    candidate.prompt_preview || candidate.scene_id,
                    () => {
                        attribution.candidate = candidate;
                        renderDetail();
                    },
                );
                if (candidate.revision === record.revision) {
                    choice.classList.add("h3cm-attribution-candidate-selected");
                }
                candidates.append(choice);
            }
            const attach = button(
                "Attach selected candidate",
                "Create a metadata-only lineage link; saved media and checkpoint files remain shared",
                () => void attributeCandidate(),
            );
            attach.disabled = state.busy || !attribution.candidate;
            state.attributionButton = attach;
            attributionPanel.append(
                candidates,
                element("div", "h3cm-muted",
                    attribution.candidate
                        ? "This candidate uses no predecessor video or generated-audio context. Attribution creates a new lineage link without regeneration or media duplication."
                        : "No reusable candidate is proven independent by its saved metadata."),
                attach,
            );
            for (const blocked of attribution.blocked ?? []) {
                attributionPanel.append(element("div", "h3cm-muted",
                    `${blocked.revision.slice(0, 8)}: ${blocked.reason}`));
            }
        }
        const media = record.preview_video ?? record.video;
        const nextVideo = videoUrl(media);
        if (nextVideo && preview.dataset.source !== nextVideo) {
            preview.src = nextVideo;
            preview.dataset.source = nextVideo;
            preview.load();
        } else if (!nextVideo) {
            preview.removeAttribute("src");
            delete preview.dataset.source;
            preview.load();
        }
        const audioUrl = videoUrl(record.audio);
        audio.hidden = !audioUrl;
        if (audioUrl && audio.dataset.source !== audioUrl) {
            audio.src = audioUrl;
            audio.dataset.source = audioUrl;
            audio.load();
        } else if (!audioUrl) {
            audio.removeAttribute("src");
            delete audio.dataset.source;
        }
        if (processing) {
            addInspector("Version", `${stageLabel()} · Scene ${record.scene} · ${record.revision}`);
            addInspector("Profile", record.profile_path);
            addInspector("State", record.ready ? "Saved files present (integrity checked at execution)" : `Missing: ${(record.missing_files ?? []).join(", ")}`);
            addInspector("Original", (record.originals ?? []).map(item => `Scene ${item.scene} · ${item.revision.slice(0, 8)}`).join(", ") || record.source_status);
            addInspector("Immediate source", record.source_revision || "Unknown");
            addInspector("Created", localTime(record.created_at));
            addInspector("Canvas", `${record.width || "?"}×${record.height || "?"} @ 24 fps`);
            addInspector("Frames", `${record.raw_frames} raw · ${record.delivered_frames} delivered`);
            addInspector("Latent", checkpointVariantLatentStatus(record));
            addInspector("Audio", record.audio_route);
            addInspector("Storage", formatCheckpointBytes(record.size_bytes));
            addInspector("Metadata", record.metadata_path);
            prompt.textContent = record.prompt || "No saved scene prompt.";
            return;
        }
        addInspector("Identity", `${state.attribution ? "Candidate " : ""}Scene ${record.scene} · ${record.scene_id} · ${record.revision}`);
        addInspector("State", record.take_kind === "editorial_alternate"
            ? `Editorial alternate · ${record.used_in_final_cut ? "used in final cut" : "available"} · ${record.ready ? "Ready" : "Broken"}`
            : `${record.active ? `Assigned to ${workingBranchName()}` : `Not assigned to ${workingBranchName()}`} · ${record.ready ? "Saved take ready" : "Broken"}`);
        if (record.take_kind === "editorial_alternate") {
            addInspector("Original base", `Scene ${record.scene} · ${String(record.alternate_of_revision).slice(0, 8)}`);
            addInspector("Media", "Picture only · original audio and downstream lineage stay unchanged");
            addInspector("Use", "Select Original or ALT for this scene in Plan Studio; ALT cannot be loaded or activated as generation lineage");
        }
        if (record.take_kind !== "editorial_alternate") {
            addInspector("Branches", (record.branches ?? []).map((item) => item.label).join(", ") || "Unresolved lineage");
        }
        addInspector("Created", localTime(record.created_at));
        addInspector("Frames", `${record.raw_frames} raw · ${record.delivered_frames} delivered`);
        addInspector("Sampling", `seed ${record.seed || "unknown"} · ${record.steps || "?"} steps`);
        addInspector("Incoming", `${record.continuation_mode} · Video ${record.context_length}f · Audio ${record.audio_context_length}f`);
        if (record.inactive_reason) addInspector("Inactive", record.inactive_reason);
        addInspector("Parent", state.attribution
            ? `Will become Scene ${state.attribution.parent.scene} · ${state.attribution.parent.revision.slice(0, 8)}`
            : record.parent ? `Scene ${record.parent.scene} · ${record.parent.revision.slice(0, 8)}` : record.lineage_status);
        addInspector("Following", (record.children ?? []).length
            ? record.children.map(checkpointDependencyText).join(" · ") : "No dependent revision");
        addInspector("Storage", `${formatCheckpointBytes(record.size_bytes)}` +
            `${record.shared_size_bytes ? ` · ${formatCheckpointBytes(record.shared_size_bytes)} shared` : ""}` +
            ` · ${(record.missing_files ?? []).length ? `missing ${record.missing_files.join(", ")}` : "complete"}`);
        const compatibility = record.compatibility ?? {};
        addInspector("Canvas", compatibility.width && compatibility.height
            ? `${compatibility.width}×${compatibility.height} @ ${compatibility.fps ?? 24} fps` : "Unknown");
        addInspector("Audio mode", compatibility.audio_mode ?? "Unknown");
        addInspector("Encoding", [compatibility.encode_mode, compatibility.anchor_mode,
            compatibility.crop].filter(Boolean).join(" · ") || "Unknown");
        addInspector("Metadata", record.metadata_path ?? "Unknown");
        prompt.textContent = record.prompt || record.prompt_preview || "No saved scene prompt.";
    }

    function renderDeletion() {
        deletionBody.replaceChildren();
        retireButtons = [];
        const processing = state.stage !== "original";
        const activationMode = selectedActivationMode();
        const rollsBack = !processing && activationMode === "rollback";
        const record = state.selected;
        const lineage = processing ? [] : selectedLineage();
        assignmentContext.textContent = processing
            ? "Processed takes are previewed here. Select a generated checkpoint on the Original tab to assign a saved generation path."
            : !record ? "Select the last take of the saved path you want to assign."
                : record.take_kind === "editorial_alternate"
                    ? "Previewing an editorial ALT. Select its base take to assign a generation path."
                    : `Selected take: S${record.scene} · ${record.revision.slice(0, 8)}. `
                        + (canLoadSelected() ? `Assignment uses scenes ${lineage[0].scene}–${record.scene} (${lineage.length} clips), not the output pin.`
                            : "This path is incomplete or has unavailable artifacts; assignment is disabled.");
        load.textContent = `Load path + settings into Plan (${workingBranchName()})`;
        remove.textContent = processing ? "Delete processed version" : "Delete selected revision";
        remove.title = processing ? "Preview and permanently delete only this processed take's owned files" : "Delete an inactive leaf or roll back the active branch tip after confirmation";
        deletion.classList.toggle("h3cm-delete-blocked", Boolean(state.deletion && !state.deletion.allowed));
        if (processing) {
            deletionTitle.textContent = !state.deletion ? "Select a processed version to inspect deletion safety."
                : state.deletion.allowed
                    ? `Delete processed version · ${state.deletion.owned_file_count} files · ${formatCheckpointBytes(state.deletion.reclaimed_bytes)} · originals kept`
                    : state.deletion.blockers?.join(" ") || "Deletion is blocked.";
            activate.textContent = `Assign path to ${workingBranchName()}`;
            load.disabled = activate.disabled = true;
        } else {
            activate.textContent = activationMode === "current"
                ? `Already assigned to ${workingBranchName()}` : `Assign path to ${workingBranchName()}`;
            activate.title = rollsBack
                ? `Assign this shorter path to ${workingBranchName()}, clearing later assignments in this chapter; all saved clips are kept`
                : `Assign all scenes through the selected take to ${workingBranchName()}; other working branches and the output selection are unchanged`;
            deletionTitle.textContent = checkpointDeletionTitle(state.deletion);
            load.disabled = state.busy || !canLoadSelected();
            activate.disabled = state.busy || !canActivateSelected();
        }
        remove.disabled = state.busy || !state.deletion?.allowed;
        const planMarker = currentPlanMarker();
        assignPlan.textContent = planMarker
            ? `Assign path to ${workingBranchName(planMarker.branch)} (Plan)` : "Assign path to Plan branch";
        assignPlan.hidden = !planMarker || planMarker.run !== state.runName || planMarker.branch === selectedWorkingBranch();
        assignPlan.disabled = state.busy || Boolean(state.attribution) || !canAssignSelectedToPlan();
        if (state.attribution) {
            load.disabled = true;
            activate.disabled = true;
            remove.disabled = true;
        }
        if (!state.deletion) return;
        if (!processing && state.deletion.chapter_references?.length) {
            const snapshots = element("div", "h3cm-delete-actions");
            for (const reference of state.deletion.chapter_references) {
                if (reference.error || !reference.path || !reference.snapshot) continue;
                const control = button(
                    `Retire Chapter ${reference.number} snapshot ${reference.snapshot.slice(0, 8)}…`,
                    "Preview releasing this snapshot's recovery pins. Its JSON is archived; no clips are deleted.",
                    () => void retireChapterSnapshot(reference),
                );
                control.disabled = state.busy || Boolean(state.attribution);
                retireButtons.push(control);
                snapshots.append(control);
            }
            deletionBody.append(snapshots);
        }
        const files = (state.deletion.files ?? []).filter((item) => item.exists);
        if (files.length) {
            const list = element("ul", "h3cm-files");
            for (const file of files) {
                list.append(element("li", file.shared ? "h3cm-muted" : "",
                    `${file.label} · ${formatCheckpointBytes(file.size_bytes)} · ${file.path}` +
                    `${file.shared ? " · shared, kept" : ""}`));
            }
            deletionBody.append(list);
        }
        if (processing && state.deletion.retained_independent_takes?.length) {
            deletionBody.append(element("div", "h3cm-muted",
                "Independent pixel takes kept: " + state.deletion.retained_independent_takes.map(item =>
                    `Scene ${item.scene} · ${String(item.revision).slice(0, 8)}`).join(", ") +
                ". Affected sequence manifests are invalidated; rebuild missing scenes before full-sequence resume."));
        }
        if (state.deletion.dependents?.length) {
            const heading = element(
                "div", "h3cm-error",
                "Permanent deletion is blocked by dependent revisions:",
            );
            if (rollsBack) {
                deletionBody.append(element(
                    "div", "h3cm-muted",
                    "To continue from this scene, use Roll active branch back. It clears later active pointers but keeps every saved take.",
                ));
            }
            const list = element("ul", "h3cm-dependents");
            for (const dependent of state.deletion.dependents) {
                if (processing) {
                    const item = element("li", "h3cm-dependent",
                        `Scene ${dependent.scene ?? "?"} · ${String(dependent.revision ?? "").slice(0, 8)} · ${dependent.reason} · ${dependent.metadata_path}`);
                    item.addEventListener("click", () => {
                        const variant = (state.payload?.processing_variants ?? []).find(v => v.key === dependent.metadata_path);
                        if (variant) { selectStage(variant.stage); selectVariant(variant); }
                    });
                    list.append(item);
                    continue;
                }
                const action = dependent.leaf
                    ? (dependent.active
                        ? " · active leaf: select to delete it"
                        : " · leaf: delete this first")
                    : "";
                const item = element("li", "h3cm-dependent",
                    `${checkpointDependencyText(dependent)}${action}`);
                item.title = dependent.leaf && !dependent.active
                    ? "Select this deletable leaf checkpoint"
                    : dependent.leaf
                        ? "Select this active branch tip to inspect its rollback"
                        : "Select this dependent checkpoint to inspect its own descendants";
                item.addEventListener("click", () => {
                    const revision = selectedCheckpointRevision(
                        state.payload, dependent.scene, dependent.revision);
                    if (revision) selectRevision(revision);
                });
                list.append(item);
            }
            deletionBody.append(heading, list);
        }
        if (state.deletion.not_deleted?.length) {
            const kept = element("details", "h3cm-muted");
            kept.append(element("summary", "", "Always kept by this deletion"));
            const list = element("ul", "h3cm-files");
            for (const label of state.deletion.not_deleted) {
                list.append(element("li", "", label));
            }
            kept.append(list);
            deletionBody.append(kept);
        }
    }

    function render() {
        const total = state.payload?.summary;
        summary.textContent = total
            ? `${total.scene_count} scenes · ${total.revision_count} revisions · ${total.branch_count} branches · ${formatCheckpointBytes(total.bytes)}`
            : "Select a saved run";
        renderOutputSelection();
        renderChapterTabs();
        renderStageTabs();
        renderScenes();
        renderBranches();
        renderDetail();
        renderDeletion();
    }

    async function refreshDeletionPreview() {
        const processing = state.stage !== "original";
        const record = processing ? currentVariant() : state.selected;
        const token = ++state.requestToken;
        if (!record || !state.runName || state.attribution) return;
        deletionTitle.textContent = "Inspecting owned files and dependencies…";
        remove.disabled = true;
        try {
            const payload = await jsonRequest(
                processing ? "/minimax_h3_context_loop/processing-checkpoints/delete-preview"
                    : "/minimax_h3_context_loop/checkpoint-revisions/delete-preview", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:state.runName, branch_id:selectedWorkingBranch(), scene:record.scene, revision:record.revision,
                        ...(processing ? {metadata_path:record.key} : {})}),
                });
            if (token !== state.requestToken) return;
            state.deletion = payload;
        } catch (error) {
            if (token !== state.requestToken) return;
            state.deletion = null;
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        }
        renderDeletion();
    }

    async function refreshCheckpoints() {
        if (!state.runName) {
            state.payload = null;
            selectRevision(null, false);
            return;
        }
        setBusy(true, "Scanning checkpoint metadata…");
        const run = state.runName, branch = selectedWorkingBranch();
        const epoch = state.scanEpoch = (state.scanEpoch ?? 0) + 1;
        const current = () => state.scanEpoch === epoch && state.runName === run && selectedWorkingBranch() === branch;
        try {
            const branchList = await jsonRequest(`/minimax_h3_context_loop/working-branches?${new URLSearchParams({run_name:state.runName})}`);
            if (!current()) return;
            state.workingBranches = branchList.branches ?? [];
            state.defaultWorkingBranch = branchList.default_branch;
            renderPlanContext();
            const query = new URLSearchParams({
                run_name:state.runName,
                branch_id:selectedWorkingBranch(),
                cache_bust:String(Date.now()),
            });
            const payload = await jsonRequest(
                `/minimax_h3_context_loop/checkpoints?${query}`,
                {cache:"no-store"},
            );
            if (!current()) return;
            state.payload = payload;
            // Output is a branch selection, independent of the preview cursor.
            // Preserve old snapshots on reload; never guess a descendant of a
            // shared ancestor. The output row explains how to replace an old
            // partial pin with an explicitly chosen whole branch.
            let savedOutput = null;
            try { savedOutput = JSON.parse(selectionWidget?.value || "null"); } catch { /* validated at execution */ }
            const savedTip = savedOutput?.run_name === state.runName ? savedOutput.lineage?.at(-1) : null;
            state.outputTip = savedTip
                ? (state.payload.revisions ?? []).find(item => checkpointRevisionKey(item.scene, item.revision)
                    === checkpointRevisionKey(savedTip.scene, savedTip.revision)) ?? savedTip
                : selectedCheckpointRevision(state.payload);
            let selected = selectedCheckpointRevision(
                state.payload, state.scene, state.revision);
            if (state.stage === "original" && selected?.take_kind !== "editorial_alternate"
                    && state.initialRefresh && !checkpointLocalSelection(selectionWidget?.value)) {
                const activeTip = selectedCheckpointRevision(state.payload);
                if (checkpointRevisionLineage(
                        state.payload, activeTip).length >
                        checkpointRevisionLineage(
                            state.payload, selected).length) {
                    selected = activeTip;
                }
            }
            state.initialRefresh = false;
            status.className = "h3cm-status";
            status.textContent = state.payload.summary?.broken_count
                ? `${state.payload.summary.broken_count} broken revision${state.payload.summary.broken_count === 1 ? "" : "s"} found`
                : "Checkpoint graph is current";
            selectRevision(selected, false, state.variantKey);
            void refreshDeletionPreview();
        } catch (error) {
            if (!current()) return;
            state.payload = null;
            state.selected = null;
            state.deletion = null;
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
            render();
        } finally {
            if (current()) setBusy(false);
        }
    }

    async function refreshRuns() {
        setBusy(true, "Scanning output/h3_chains…");
        try {
            const payload = await jsonRequest("/minimax_h3_context_loop/runs");
            state.runs = payload.runs ?? [];
            const connected = activePlanRun();
            const local = checkpointLocalSelection(selectionWidget?.value);
            const preferred = local?.run_name || state.runName || connected;
            if (local && state.initialRefresh) {
                state.scene = local.lineage?.at(-1)?.scene ?? null;
                state.revision = local.lineage?.at(-1)?.revision ?? "";
            }
            state.runName = local ? preferred : state.runs.some((item) => item.run_name === preferred)
                ? preferred : state.runs[0]?.run_name ?? "";
            if (state.runName !== preferred) node.properties.h3_working_branch_id = "main";
            runSelect.replaceChildren();
            for (const run of state.runs) {
                const option = element("option", "", `${run.run_name} · ${run.checkpoint_count} active checkpoints`);
                option.value = run.run_name;
                runSelect.append(option);
            }
            if (local && !state.runs.some((item) => item.run_name === preferred)) {
                const option = element("option", "", `${preferred} · pinned run unavailable`);
                option.value = preferred;
                runSelect.append(option);
            }
            runSelect.value = state.runName;
        } catch (error) {
            state.runs = [];
            state.runName = "";
            runSelect.replaceChildren();
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
        await refreshCheckpoints();
    }

    async function openFolder() {
        if (!state.runName) return;
        setBusy(true, "Opening run folder…");
        try {
            const payload = await jsonRequest("/minimax_h3_context_loop/open-run-folder", {
                method:"POST", headers:{"Content-Type":"application/json"},
                body:JSON.stringify({run_name:state.runName}),
            });
            status.textContent = payload.opened ? "Opened on ComfyUI host" : payload.path;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function deleteRunFolder() {
        const runName = state.runName;
        if (!runName || state.busy) return;
        let plan;
        setBusy(true, "Inspecting the complete run folder…");
        try {
            plan = await jsonRequest(
                "/minimax_h3_context_loop/run-folder/delete-preview", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:runName}),
                });
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
            return;
        } finally {
            setBusy(false);
        }
        if (state.runName !== runName) return;
        const confirmed = window.confirm(
            `Delete the complete run folder "${runName}"?\n\n` +
            `${plan.file_count} files · ${plan.directory_count} folders · ${formatCheckpointBytes(plan.reclaimed_bytes)}\n\n` +
            `This permanently removes ${plan.folder}, including checkpoints, revisions, generated segments and audio, prompt history, archived workflows, reference backups, previews, and assembled exports.\n\n` +
            "Original input project assets are kept. A second validation follows. This cannot be undone.",
        );
        if (!confirmed) return;
        const typed = window.prompt(
            `Second validation: type the exact Run name to delete ${plan.folder}:\n\n${runName}`,
            "",
        );
        if (typed === null) return;
        if (typed !== runName) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = "Run folder not deleted: the second validation did not exactly match the Run name.";
            return;
        }
        setBusy(true, "Deleting the complete run folder…");
        try {
            const payload = await mutationRequest(node, runName,
                "/minimax_h3_context_loop/run-folder/delete", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:runName,
                        snapshot:plan.snapshot, confirmation:typed}),
                });
            state.runName = "";
            state.scene = null;
            state.revision = "";
            state.selected = null;
            state.deletion = null;
            state.payload = null;
            persistSelection();
            await refreshRuns();
            status.className = "h3cm-status";
            status.textContent = `${payload.message} Reclaimed ${formatCheckpointBytes(payload.reclaimed_bytes)}. Original input project assets were kept.`;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.payload?.preview
                ? `${error.message} Click Delete run folder to review it again.`
                : error.message;
        } finally {
            setBusy(false);
        }
    }

    function restoreSavedPlanInputs(inputs, policyInputs = {}) {
        const planNode = upstreamPlanNode(node);
        if (!planNode || !inputs || typeof inputs !== "object") {
            throw new Error("The saved run has no Plan inputs to restore.");
        }
        const names = Object.keys(inputs).sort((left, right) =>
            Number(left === "plan_json") - Number(right === "plan_json"));
        const graph = planNode.graph ?? app.graph;
        const applied = [];
        graph?.beforeChange?.();
        try {
            for (const name of names) {
                const target = widget(planNode, name);
                if (!target) continue;
                target.value = inputs[name];
                target.callback?.(inputs[name]);
                applied.push(name);
            }
        } finally {
            graph?.afterChange?.();
        }
        if (!applied.includes("plan_json")) {
            throw new Error("The connected Plan does not expose an editable plan_json widget.");
        }
        restoreConnectedPolicyInputs(planNode, policyInputs);
        refreshRestoredPlanEditors(planNode);
        app.graph?.setDirtyCanvas?.(true, true);
        return planNode;
    }

    function applyLoadedRevisions(planNode, revisions) {
        const target = widget(planNode, "plan_json");
        if (!target) throw new Error("The connected Plan has no plan_json control.");
        const plan = applyCheckpointRevisionSet(
            parsePlanJson(String(target.value ?? "")), revisions,
        );
        if (selectedWorkingBranch() === "main") delete plan._branch_id;
        else plan._branch_id = selectedWorkingBranch();
        const selectedWidget = widget(planNode, "working_branch_id");
        if (selectedWidget) selectedWidget.value = selectedWorkingBranch();
        const value = planToJson(plan);
        target.value = value;
        target.callback?.(value);
        refreshRestoredPlanEditors(planNode);
        planNode.graph?.setDirtyCanvas?.(true, true);
        for (const revision of revisions ?? []) {
            const sceneIndex = Number(revision.scene) - 1;
            if (sceneIndex < 0 || sceneIndex >= plan.shots.length) continue;
            publishCompanionPrompt(
                node, planNode, sceneIndex,
                promptValueToText(plan.shots[sceneIndex]?.prompt),
            );
        }
        return plan;
    }

    function applyActivatedRevisions(planNode, revisions, targetBranch = selectedWorkingBranch()) {
        const target = widget(planNode, "plan_json");
        if (!target) return false;
        if ((parsePlanJson(String(target.value ?? ""))._branch_id ?? "main") !== targetBranch) return false;
        const plan = applyCheckpointRevisionSet(
            parsePlanJson(String(target.value ?? "")), revisions, {
                useEffectivePrompts: true,
                useTipSharedPrompt: true,
            },
        );
        const value = planToJson(plan);
        target.value = value;
        target.callback?.(value);
        refreshRestoredPlanEditors(planNode);
        planNode.graph?.setDirtyCanvas?.(true, true);
        for (const revision of revisions ?? []) {
            const sceneIndex = Number(revision.scene) - 1;
            if (sceneIndex < 0 || sceneIndex >= plan.shots.length) continue;
            publishCompanionPrompt(
                node, planNode, sceneIndex,
                promptValueToText(plan.shots[sceneIndex]?.prompt),
            );
        }
        return true;
    }

    function prepareResume(scene) {
        const start = connectedNode(node, START_NAME);
        const startClip = widget(start, "start_clip");
        if (!startClip) return false;
        startClip.value = scene;
        startClip.callback?.(scene);
        const range = widget(start, "scene_range");
        if (range) {
            range.value = "";
            range.callback?.("");
        }
        start.graph?.setDirtyCanvas?.(true, true);
        return true;
    }

    async function attributeCandidate() {
        const attribution = state.attribution;
        const candidate = attribution?.candidate;
        const parent = attribution?.parent;
        if (!candidate || !parent || state.busy) return;
        const confirmed = window.confirm(
            `Attribute scene ${candidate.scene} candidate ${candidate.revision.slice(0, 8)} after scene ${parent.scene} revision ${parent.revision.slice(0, 8)}?\n\n` +
            "The candidate has no predecessor video or generated-audio dependency. A new immutable lineage record will be created; its existing video, audio, prompt, and checkpoint files remain shared. Nothing is regenerated or copied.",
        );
        if (!confirmed) return;
        setBusy(true, "Attributing saved candidate to branch…");
        try {
            const payload = await mutationRequest(node, state.runName,
                "/minimax_h3_context_loop/checkpoint-revisions/attribute", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({
                        run_name:state.runName,
                        parent_scene:parent.scene,
                        parent_revision:parent.revision,
                        candidate_scene:candidate.scene,
                        candidate_revision:candidate.revision,
                    }),
                });
            state.attribution = null;
            state.scene = Number(payload.scene);
            state.revision = String(payload.revision);
            await refreshCheckpoints();
            // Attachment is an explicit branch edit, unlike a preview click.
            const attached = (state.payload?.revisions ?? []).find(item =>
                Number(item.scene) === Number(payload.scene) && item.revision === payload.revision);
            if (attached) selectOutputBranch(checkpointOutputBranchTip(
                state.payload, attached, chapterRangeFor(attached)) ?? attached);
            status.className = "h3cm-status";
            status.textContent = payload.message;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function loadSelected() {
        const record = state.selected;
        const lineage = selectedLineage();
        const scope = selectedChapterRange();
        if (!record || !canLoadSelected() || state.busy) return;
        const planNode = upstreamPlanNode(node);
        if (!planNode || !widget(planNode, "plan_json")) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = "Connect the Checkpoint Manager to an editable H3 Chain Plan first.";
            return;
        }
        const confirmed = window.confirm(
            `Load ${state.runName} through scene ${record.scene} revision ${record.revision.slice(0, 8)}?\n\n` +
            "This assigns these clips to the selected working branch and restores the connected Plan. For output only, cancel and choose Use branch locally.\n\n" +
            `${scope.title} scenes ${scope.start}–${scope.end} will use this branch. Other chapters keep their active checkpoint branches. Saved revision files are kept.`,
        );
        if (!confirmed) return;
        setBusy(true, "Loading saved Plan and checkpoint lineage…");
        try {
            const runQuery = new URLSearchParams({
                run_name: state.runName,
                branch_id:selectedWorkingBranch(),
                include_assets: "false",
            });
            const runBody = await jsonRequest(
                `/minimax_h3_context_loop/run?${runQuery.toString()}`,
            );
            const connectedPlan = parsePlanJson(String(widget(planNode, "plan_json")?.value ?? ""));
            const sameConnectedRun = activePlanRun() === state.runName &&
                (connectedPlan._branch_id ?? "main") === selectedWorkingBranch();
            const savedPlan = parsePlanJson(String(sameConnectedRun
                ? widget(planNode, "plan_json")?.value ?? ""
                : runBody.plan_inputs?.plan_json ?? ""));
            const chapters = state.payload?.editorial?.chapters ?? [];
            if (chapters.length) {
                savedPlan.chapters = chapters.map((chapter) => ({
                    id:chapter.id,
                    title:chapter.title,
                    start_scene_id:chapter.start_scene_id,
                    text:chapter.text ?? "",
                }));
            }
            if (savedPlan.shots.length < Number(record.scene)) {
                throw new Error(
                    `The saved Plan has only ${savedPlan.shots.length} scenes.`,
                );
            }
            const resumeScene = Number(record.scene) + 1;
            if (resumeScene <= savedPlan.shots.length &&
                    !widget(connectedNode(node, START_NAME), "start_clip")) {
                throw new Error("Could not find the connected H3 Chain Loop Start node.");
            }
            const restored = await mutationRequest(node, state.runName,
                "/minimax_h3_context_loop/checkpoint-revisions/restore", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({
                        run_name:state.runName,
                        resume_scene:resumeScene,
                        revisions:lineage,
                        scope_start_scene:scope.start,
                        scope_end_scene:scope.end,
                    }),
                });
            const activePlan = sameConnectedRun ? planNode
                : restoreSavedPlanInputs(
                    {...runBody.plan_inputs, plan_json:planToJson(savedPlan)},
                    restored.policy_inputs ?? runBody.policy_inputs,
                );
            const plan = applyLoadedRevisions(activePlan, restored.restored ?? []);
            const canResume = resumeScene <= plan.shots.length &&
                resumeScene <= scope.end;
            if (canResume && !prepareResume(resumeScene)) {
                throw new Error("Loaded the branch, but could not arm H3 Chain Loop Start.");
            }
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = canResume
                ? `Loaded ${scope.title} scenes ${scope.start}–${record.scene}; Loop Start is armed for scene ${resumeScene}.`
                : `Loaded ${scope.title} through scene ${record.scene}; other chapters were preserved.`;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function activateSelected() {
        const record = state.selected;
        const lineage = selectedLineage();
        const scope = selectedChapterRange();
        if (!record || !canActivateSelected() || state.busy || state.attribution) return;
        const runName = state.runName, targetBranch = selectedWorkingBranch();
        const planNode = upstreamPlanNode(node);
        const targetName = workingBranchName(targetBranch);
        const confirmed = window.confirm(
            `Assign ${scope.title} through scene ${record.scene} revision ${record.revision.slice(0, 8)} to "${targetName}" (${targetBranch.slice(0, 8)})?\n\n` +
            `This replaces that branch's assignments in scenes ${scope.start}–${scope.end} with the selected path. Later assignments in this chapter will be cleared. ` +
            "Other branches, other chapters, the project default, and the manager's output selection are unchanged. " +
            "A connected Plan already on this branch receives the saved scene settings. No saved clips, workflows, references, or assembled videos are deleted.",
        );
        if (!confirmed) return;
        setBusy(true, `Assigning saved path to ${targetName}…`);
        try {
            const payload = await mutationRequest(node, runName,
                "/minimax_h3_context_loop/checkpoint-revisions/restore", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({
                        run_name:runName, branch_id:targetBranch,
                        resume_scene:Number(record.scene) + 1,
                        revisions:lineage,
                        activate_only:true,
                        scope_start_scene:scope.start,
                        scope_end_scene:scope.end,
                    }),
                }, targetBranch);
            const marker = currentPlanMarker();
            const planUpdated = Boolean(marker?.run === runName && marker.branch === targetBranch && planNode
                && upstreamPlanNode(node) === planNode &&
                applyActivatedRevisions(planNode, payload.restored ?? [], targetBranch));
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = `${targetName}: ${scope.title} assigned through scene ${record.scene} revision ${record.revision.slice(0, 8)}. Output selection unchanged. ` +
                `${payload.retired_scope_pointers || 0} later pointer${payload.retired_scope_pointers === 1 ? " was" : "s were"} cleared inside this chapter; other chapters were preserved; all immutable revisions were kept` +
                `${planUpdated ? "; connected Plan scene settings were restored." : "."}`;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function assignSelectedToPlan() {
        if (state.busy || state.attribution || !canAssignSelectedToPlan()) return;
        const marker = currentPlanMarker(), planNode = upstreamPlanNode(node) ?? upstreamPlanNode(node, true);
        const record = state.selected, scope = selectedChapterRange();
        const lineage = selectedLineage();
        const targetName = workingBranchName(marker.branch);
        if (!window.confirm(
            `Assign ${scope.title} through scene ${record.scene} to Plan branch "${targetName}" (${marker.branch.slice(0, 8)})?\n\n` +
            `This replaces that branch's active pointers in scenes ${scope.start}–${scope.end} with the selected saved path, clearing later pointers in this chapter. ` +
            "The connected Plan's scene settings will be restored. Other branches, other chapters, the manager's output selection, and all saved clips are kept."
        )) return;
        setBusy(true, `Assigning saved path to ${targetName}…`);
        try {
            const payload = await mutationRequest(node, marker.run,
                "/minimax_h3_context_loop/checkpoint-revisions/restore", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:marker.run, branch_id:marker.branch,
                        resume_scene:Number(record.scene) + 1, revisions:lineage, activate_only:true,
                        scope_start_scene:scope.start, scope_end_scene:scope.end}),
                }, marker.branch);
            const current = currentPlanMarker();
            const planUpdated = Boolean(current?.run === marker.run && current.branch === marker.branch
                && planNode && (upstreamPlanNode(node) ?? upstreamPlanNode(node, true)) === planNode
                && applyActivatedRevisions(planNode, payload.restored ?? [], marker.branch));
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = `Saved path through scene ${record.scene} assigned to ${targetName}. All saved clips were kept. ` +
                (planUpdated ? "Connected Plan scene settings restored." : "Plan view changed; its widgets were not modified.");
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally { setBusy(false); }
    }

    async function retireChapterSnapshot(reference) {
        const runName = state.runName, record = state.selected;
        if (state.busy || state.stage !== "original" || state.attribution || !record) return;
        setBusy(true, "Inspecting chapter snapshot retirement…");
        try {
            const plan = await jsonRequest(
                "/minimax_h3_context_loop/chapter-snapshots/retire-preview", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:runName, branch_id:selectedWorkingBranch(), path:reference.path}),
                });
            // A slow response must not apply to a different run or selection.
            if (state.runName !== runName || state.selected !== record || state.stage !== "original") return;
            const scenes = (plan.scenes ?? []).map(item =>
                `Scene ${item.scene} · ${String(item.revision).slice(0, 8)}${item.active ? " · currently active" : ""}`).join("\n");
            if (!window.confirm(
                `Retire Chapter ${plan.chapter_number} snapshot ${plan.chapter_manifest_id.slice(0, 8)}?\n\n` +
                `${scenes}\n\n${plan.message}\n\n` +
                `Archive: ${plan.retired_path}\n\n` +
                "This snapshot will no longer load in Chapter Loader or protect its old takes from cleanup. " +
                "Workflows pinned to it need a new source. Deleting its inputs later makes full recovery unavailable. " +
                "Other snapshots and branch dependencies remain protected.")) {
                status.textContent = "Snapshot retirement cancelled; nothing changed.";
                return;
            }
            const result = await mutationRequest(node, runName,
                "/minimax_h3_context_loop/chapter-snapshots/retire", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:runName, path:plan.path, snapshot:plan.snapshot}),
                });
            await refreshDeletionPreview();
            status.className = "h3cm-status";
            status.textContent = result.message;
        } catch (error) {
            await refreshDeletionPreview();
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function deleteSelected() {
        if (state.stage !== "original") return deleteProcessedVersion();
        const record = state.selected;
        const plan = state.deletion;
        if (!record || !plan?.allowed || state.busy) return;
        const confirmed = window.confirm(
            `${plan.rollback ? "Roll back and permanently delete" : "Permanently delete"} scene ${record.scene} revision ${record.revision.slice(0, 8)}?\n\n` +
            `${plan.owned_file_count} owned files · ${formatCheckpointBytes(plan.reclaimed_bytes)}\n` +
            `${plan.rollback ? (plan.rollback_to_scene > 0
                ? `The active chain will roll back through scene ${plan.rollback_to_scene}. `
                : "The run will have no active checkpoint scenes. ") : ""}` +
            "Run archives, references, prompt history, and assembled exports are kept. This cannot be undone.",
        );
        if (!confirmed) return;
        setBusy(true, "Deleting staged revision files…");
        try {
            const payload = await mutationRequest(node, state.runName,
                "/minimax_h3_context_loop/checkpoint-revisions/delete", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:state.runName, scene:record.scene,
                        revision:record.revision, snapshot:plan.snapshot}),
                });
            state.scene = payload.rollback ? payload.rollback_to_scene : state.scene;
            state.revision = "";
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = `${payload.message} Reclaimed ${formatCheckpointBytes(payload.reclaimed_bytes)}.`;
        } catch (error) {
            state.deletion = error.payload?.preview ?? state.deletion;
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
            renderDeletion();
        } finally {
            setBusy(false);
        }
    }

    async function deleteProcessedVersion() {
        const record = currentVariant(), plan = state.deletion, runName = state.runName;
        if (state.busy || !record || !plan?.allowed || plan.metadata_path !== record.key) return;
        const confirmed = window.confirm(
            `Permanently delete ${stageLabel()} scene ${record.scene} take ${record.revision.slice(0, 8)} (${record.profile})?\n\n` +
            `${plan.owned_file_count} files · ${formatCheckpointBytes(plan.reclaimed_bytes)}\n` +
            "Its current processed pointer and affected branch manifests will be cleared. Original clips, shared references, other takes and assembled videos are kept. " +
            (plan.retained_independent_takes?.length
                ? "Later independent pixel clips are kept; rebuild missing scenes before full-sequence resume. " : "") +
            "This cannot be undone.");
        if (!confirmed) return;
        setBusy(true, "Deleting processed version…");
        try {
            const payload = await mutationRequest(node, runName,
                "/minimax_h3_context_loop/processing-checkpoints/delete", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:runName, metadata_path:record.key, snapshot:plan.snapshot}),
                });
            // Keep the vanished browse key and output pin: never substitute a different take.
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = `${payload.message} Reclaimed ${formatCheckpointBytes(payload.reclaimed_bytes)}.`;
            let selection;
            try { selection = JSON.parse(selectionWidget?.value || "null"); } catch { /* unchanged invalid selection */ }
            if (selection?.processing_source?.branch?.lineage?.some(item => item.metadata_path === record.key)) {
                status.textContent += " This workflow's output pin referenced the deleted take; explicitly select another source branch before running.";
            }
            if (payload.cleanup_pending?.length) status.textContent += ` Cleanup pending: ${payload.cleanup_pending.join(", ")}`;
        } catch (error) {
            state.deletion = error.payload?.preview ?? null;
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
            renderDeletion();
        } finally {
            setBusy(false);
        }
    }

    runSelect.addEventListener("change", () => {
        if (checkpointLocalSelection(selectionWidget?.value)) {
            if (!window.confirm("Switch runs and release this workflow's local output pin? The project active branches will not change.")) {
                runSelect.value = state.runName;
                return;
            }
            writeOutputSelection("");
        }
        state.runName = runSelect.value;
        node.properties.h3_working_branch_id = "main";
        state.payload = null;
        state.selected = null;
        state.outputTip = state.previewTip = null;
        state.scene = null;
        state.revision = "";
        persistSelection();
        void refreshCheckpoints();
    });

    const domWidget = node.addDOMWidget("h3_checkpoint_manager", "h3-checkpoint-manager", root, {
        serialize:false, hideOnZoom:false, getMinHeight:() => 620,
    });
    domWidget.serialize = false;
    node.setSize?.([
        Math.max(Number(node.size?.[0]) || 0, 900),
        Math.max(Number(node.size?.[1]) || 0, 760),
    ]);
    const connectionsChanged = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = connectionsChanged?.apply(this, arguments);
        window.setTimeout(() => {
            const connected = activePlanRun();
            if (connected && connected !== state.runName && !checkpointLocalSelection(selectionWidget?.value)) {
                state.runName = connected;
                void refreshRuns();
            }
        }, 0);
        return result;
    };
    node._h3CheckpointManagerRefresh = () => void refreshRuns();
    node._h3CheckpointManagerConfigured = () => {
        // Configuration can arrive after mount, including undo/redo and tab
        // restores. Hydrate the scope before refresh can persist a selection.
        bindSelectionSerializer();
        restoreOutputScope();
        state.runName = String(node.properties[RUN_PROPERTY] ?? "");
        state.scene = Number(node.properties[SCENE_PROPERTY]) || null;
        state.revision = String(node.properties[REVISION_PROPERTY] ?? "");
        state.chapterTab = String(node.properties[CHAPTER_PROPERTY] ?? "all");
        state.stage = CHECKPOINT_STAGES.some(item => item.id === node.properties[STAGE_PROPERTY])
            ? node.properties[STAGE_PROPERTY] : "original";
        state.variantKey = String(node.properties[VARIANT_PROPERTY] ?? "");
        state.initialRefresh = !state.scene || !state.revision;
    };
    bindSelectionSerializer();
    const serialized = node.onSerialize;
    node.onSerialize = function (saved) {
        if (selectionWidget) selectionWidget.value = outputSelectionForScope(selectionWidget.value);
        node.properties[OUTPUT_SCOPE_PROPERTY] = outputScope.value;
        const result = serialized?.apply(this, arguments);
        if (saved) {
            saved.properties ??= {};
            saved.properties[OUTPUT_SCOPE_PROPERTY] = outputScope.value;
            const index = node.widgets?.indexOf(selectionWidget) ?? -1;
            if (index >= 0 && Array.isArray(saved.widgets_values)) saved.widgets_values[index] = selectionWidget.value;
            if (selectionWidget && saved.widgets_values_named) saved.widgets_values_named.selection_json = selectionWidget.value;
        }
        return result;
    };
    const removed = node.onRemoved;
    const refreshPlanMarker = () => {
        const signature = JSON.stringify(currentPlanMarker());
        if (signature === state.planMarkerSignature || state.busy) return;
        state.planMarkerSignature = signature;
        // Read-only refresh: no selection serialization or server mutation.
        renderBranches();
        renderDeletion();
    };
    node._h3CheckpointManagerPlanMarkerRefresh = refreshPlanMarker;
    const markerTimer = window.setInterval?.(refreshPlanMarker, 500);
    node.onRemoved = function () {
        if (markerTimer != null) window.clearInterval(markerTimer);
        for (const cleanup of state.graphCleanups) cleanup();
        state.graphCleanups = [];
        delete this._h3CheckpointManagerPlanMarkerRefresh;
        return removed?.apply(this, arguments);
    };
    void refreshRuns();
}

app.registerExtension({
    name:"minimax_h3_context_loop.checkpoint_manager",
    async beforeRegisterNodeDef(nodeTypeClass, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeTypeClass.prototype.onNodeCreated;
        nodeTypeClass.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            window.setTimeout(() => mount(this), 0);
            return result;
        };
        const configured = nodeTypeClass.prototype.onConfigure;
        nodeTypeClass.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            this._h3CheckpointManagerConfigured?.();
            window.setTimeout(() => this._h3CheckpointManagerRefresh?.(), 0);
            return result;
        };
    },
    async nodeCreated(node) {
        if (nodeType(node) === NODE_NAME) mount(node);
    },
});
