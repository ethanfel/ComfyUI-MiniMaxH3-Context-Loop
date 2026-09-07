import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    CHECKPOINT_STAGES,
    checkpointStageVariants,
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
    formatCheckpointBytes,
    selectedCheckpointRevision,
} from "./h3_checkpoint_manager_core.mjs?v=0.7.12";
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
const SHARED_COLORS = ["#6ea8ff", "#58c99d", "#bd8cff", "#e8a84f", "#f07f8c", "#55bfd0"];

function previewHeight(value) {
    const number = Number(value);
    return Number.isFinite(number)
        ? Math.max(MIN_PREVIEW_HEIGHT, Math.min(MAX_PREVIEW_HEIGHT, Math.round(number)))
        : DEFAULT_PREVIEW_HEIGHT;
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? "";
}

function upstreamPlanNode(start) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const current = queue.shift();
        if (!current || seen.has(current)) continue;
        seen.add(current);
        if (current !== start && PLAN_NAMES.has(nodeType(current))) return current;
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
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || "unknown") : date.toLocaleString();
}

function sharedColor(key) {
    let hash = 0;
    for (const character of String(key ?? "")) {
        hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
    }
    return SHARED_COLORS[hash % SHARED_COLORS.length];
}

async function jsonRequest(path, options = {}) {
    const response = await api.fetchApi(path, options);
    const payload = await response.json();
    if (!response.ok) throw Object.assign(
        new Error(payload.error || `HTTP ${response.status}`), {payload});
    return payload;
}

async function mutationRequest(node, runName, path, options = {}) {
    return await jsonRequest(
        path, await projectMutationOptions(node, runName, options),
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
        gap:8px; overflow:hidden; padding:10px; border:1px solid var(--h3cm-border); border-radius:9px;
        background:var(--h3cm-bg); color:var(--h3cm-text); font:12px/1.4 system-ui,sans-serif; }
      .h3cm-root *, .h3cm-root *::before, .h3cm-root *::after { box-sizing:border-box; }
      .h3cm-head,.h3cm-run-row,.h3cm-chapter-tabs,.h3cm-scenes,.h3cm-branch-head,.h3cm-branch-path,.h3cm-delete-actions {
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
      .h3cm-local-label { color:var(--h3cm-accent) !important; font-weight:700; }
      .h3cm-scenes { flex:0 0 auto; overflow:auto; padding-bottom:2px; }
      .h3cm-stage-tabs { display:flex; flex:0 0 auto; gap:6px; overflow:auto; }
      .h3cm-stage-tab { white-space:nowrap; }
      .h3cm-stage-tab[aria-selected="true"] { color:var(--h3cm-accent); border-color:var(--h3cm-accent); }
      .h3cm-stage-note { flex:0 0 auto; color:var(--h3cm-muted); overflow-wrap:anywhere; }
      .h3cm-variant-group { display:flex; flex-direction:column; gap:5px; }
      .h3cm-chapter-tabs { flex:0 0 auto; overflow:auto; padding:2px 0; }
      .h3cm-chapter-tab { white-space:nowrap; border-radius:999px !important; }
      .h3cm-chapter-selected { color:var(--h3cm-chapter) !important; border-color:#d6a650 !important;
        background:color-mix(in srgb,var(--h3cm-panel) 78%,#6d4b16) !important; }
      .h3cm-scene { white-space:nowrap; }
      .h3cm-scene-selected,.h3cm-revision-selected { border-color:var(--h3cm-accent) !important;
        color:var(--h3cm-accent) !important; }
      .h3cm-main { min-height:0; flex:1 1 auto; display:grid; grid-template-columns:minmax(310px,.9fr) minmax(390px,1.1fr); gap:8px; }
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
      .h3cm-branch-path { position:relative; z-index:3; align-items:stretch; overflow:auto; padding-bottom:2px; }
      .h3cm-arrow { align-self:center; color:var(--h3cm-muted); }
      .h3cm-revision { position:relative; min-width:112px; text-align:left; white-space:nowrap; }
      .h3cm-revision small { display:block; color:var(--h3cm-muted); font-size:10px; }
      .h3cm-alternates { display:flex; flex-direction:column; gap:3px; min-width:104px;
        padding-left:7px; border-left:2px solid #8264bd; }
      .h3cm-alternate { min-width:100px; min-height:24px !important; padding:3px 6px !important;
        border-color:#7259a8 !important; color:#d9c9f7 !important; font-size:10px !important; }
      .h3cm-alternate-used { background:color-mix(in srgb,var(--h3cm-panel) 68%,#56358b) !important;
        box-shadow:inset 3px 0 0 #b493f0; }
      .h3cm-revision-empty { border-style:dashed !important; color:var(--h3cm-muted) !important;
        background:color-mix(in srgb,var(--h3cm-panel) 72%,transparent) !important; }
      .h3cm-revision-empty-selected { border-color:var(--h3cm-accent) !important;
        color:var(--h3cm-accent) !important; }
      .h3cm-revision-shared { border-color:var(--h3cm-shared-color) !important;
        box-shadow:inset 3px 0 0 var(--h3cm-shared-color); }
      .h3cm-shared-label { display:block; width:max-content; margin:2px 0; padding:1px 5px;
        border-radius:999px; background:color-mix(in srgb,var(--h3cm-shared-color) 23%,transparent);
        color:color-mix(in srgb,var(--h3cm-shared-color) 72%,var(--h3cm-text)); font-size:9px; font-weight:750; }
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
      .h3cm-delete { flex:0 0 auto; max-height:210px; overflow:auto; padding:8px;
        border:1px solid var(--h3cm-border); border-radius:7px; }
      .h3cm-delete-blocked { border-color:var(--h3cm-danger); }
      .h3cm-delete-title { font-weight:700; }
      .h3cm-files,.h3cm-dependents { margin:5px 0 0; padding-left:18px; }
      .h3cm-dependent { color:var(--h3cm-danger); cursor:pointer; }
      .h3cm-delete-actions { margin-top:7px; }
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
    };
    const root = element("div", "h3cm-root");
    const head = element("div", "h3cm-head");
    const title = element("div", "h3cm-title", "Checkpoint Manager");
    const summary = element("div", "h3cm-summary", "Select a saved run");
    head.append(title, summary);
    const runRow = element("div", "h3cm-run-row");
    const runSelect = element("select", "h3cm-run-select");
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
    const branchesTitle = element("div", "h3cm-panel-title", "Revision branches");
    const branchLegend = element("span", "h3cm-shared-legend", "matching color = same saved clip");
    branchesTitle.append(branchLegend);
    const branches = element("div", "h3cm-branches");
    branchesPanel.append(branchesTitle, branches);
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
    const deletionBody = element("div");
    const deletionActions = element("div", "h3cm-delete-actions");
    const status = element("div", "h3cm-status");
    const load = button("Load selected branch", "Project-wide: activate this chapter lineage and restore the connected Plan for generation", () => void loadSelected());
    const activate = button("Make branch active (project)", "Project-wide: promote this chapter for all workflows using this Run", () => void activateSelected());
    const remove = button("Delete selected revision", "Delete an inactive leaf or roll back the active branch tip after confirmation", () => void deleteSelected(), "h3cm-delete-button");
    load.disabled = true;
    activate.disabled = true;
    remove.disabled = true;
    deletionActions.append(load, activate, status, remove);
    deletion.append(deletionTitle, deletionBody, deletionActions);
    root.append(head, runRow, outputRow, stageTabs, stageNote, chapterTabs, scenes, main, deletion);

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
            const normalize = (saved) => outputSelectionForScope(saved ?? this.value);
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
            const value = checkpointOutputSelectionJson(
                selectionWidget.value, state.payload, state.runName, state.outputTip,
                chapterRangeFor(state.outputTip), outputScope.value);
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
        selectionWidget.value = value;
        selectionWidget.callback?.(value);
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
        if (local) {
            const tip = local.lineage?.at(-1);
            outputSummary.textContent = `Local output · ${local.run_name} · through scene ${tip?.scene ?? "?"} / ${String(tip?.revision ?? "").slice(0, 8)} · saved with this workflow`;
            if (local.output_scope === "chapter") outputSummary.textContent += ` · chapter only, scenes ${local.scope_start_scene}–${tip?.scene ?? "?"}`;
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
        } else {
            outputSummary.textContent = state.outputTip
                ? `Output branch · scenes ${outputScope.value === "chapter" ? chapterRangeFor(state.outputTip).start : 1}–${state.outputTip.scene} · clip clicks preview only; set the processing range downstream`
                : "Choose a branch heading for output · clip clicks preview only";
        }
        let saved = local;
        try { saved ??= JSON.parse(selectionWidget?.value || "null"); } catch { /* invalid selections fail at execution */ }
        if (saved && state.payload && saved.run_name === state.runName) {
            const tip = saved.lineage?.at(-1);
            const range = {start:saved.scope_start_scene, end:saved.scope_end_scene};
            const fullTip = checkpointOutputBranchTip(state.payload, tip, range);
            if (!fullTip || Number(fullTip.scene) > Number(tip?.scene)) {
                outputSummary.textContent += " · old partial selection: choose the desired branch heading"
                    + (local ? ", then Use branch locally" : "") + " to include its later clips";
            }
        }
        if (saved?.processing_source?.stage === "derope") outputSummary.textContent += ` · DeRoPE source: ${saved.processing_source.profile_path} · original fallback for unsaved scenes`;
        if (state.stage !== "original") outputSummary.textContent += " · tab browsing does not change output";
    }

    function setBusy(value, message = "") {
        state.busy = Boolean(value);
        runSelect.disabled = state.busy;
        refresh.disabled = state.busy;
        open.disabled = state.busy || !state.runName;
        deleteRun.disabled = state.busy || !state.runName;
        load.disabled = state.busy || Boolean(state.attribution) || !canLoadSelected();
        activate.disabled = state.busy || Boolean(state.attribution) || !canActivateSelected();
        remove.disabled = state.busy || state.stage !== "original" || Boolean(state.attribution) || !state.deletion?.allowed;
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
        persistSelection();
        render();
        if (record && requestDeletion && state.stage === "original") void refreshDeletionPreview();
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
        selectRevision(original, false, record.key);
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
        if (stage === "original" && state.selected) void refreshDeletionPreview();
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
        stageNote.textContent = state.stage === "original" ? ""
            : `${stageLabel()} versions grouped by their original source branch. Browsing does not change output.`
                + (state.stage === "derope" ? " Select a saved take, then Use DeRoPE branch locally for deferred upscaling. Unsaved scenes use their original take." : "");
        const warnings = state.payload?.processing_variant_warnings ?? [];
        if (warnings.length) stageNote.textContent += ` ${warnings.length} processing metadata warning(s): ${warnings[0]}`;
        stageNote.hidden = !stageNote.textContent;
        branchLegend.textContent = state.stage === "original" ? "matching color = same saved clip"
            : "saved versions per source clip";
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

    function renderBranchRows(container, rows) {
        if (state.stage !== "original") {
            renderVariantBranchRows(container, rows);
            return;
        }
        const localKeys = localRevisionKeys();
        const occurrences = new Map();
        for (const branch of rows) {
            for (const revision of branch.revisions) {
                const key = checkpointRevisionKey(revision.scene, revision.revision);
                occurrences.set(key, (occurrences.get(key) ?? 0) + 1);
            }
        }
        for (const branch of rows) {
            const row = element("div", "h3cm-branch");
            const header = element("div", "h3cm-branch-head");
            const tip = branch.revisions.at(-1) ?? null;
            const selectedTip = Boolean(tip &&
                Number(state.previewTip?.scene) === Number(tip.scene) &&
                String(state.previewTip?.revision) === String(tip.revision));
            if (selectedTip) row.classList.add("h3cm-branch-selected");
            if (tip) {
                header.role = "button";
                header.tabIndex = 0;
                header.title = `Select this whole branch (ends at scene ${tip.scene}); set start/end downstream`;
                header.addEventListener("click", () => selectOutputBranch(tip));
                header.addEventListener("keydown", (event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    selectOutputBranch(tip);
                });
            }
            const name = element("span", branch.active ? "h3cm-branch-active" : "", branch.active ? "Project active branch" : branch.label);
            const count = element("span", "h3cm-muted", `${branch.revisions.length} visible scene${branch.revisions.length === 1 ? "" : "s"}`);
            header.append(name, count);
            const path = element("div", "h3cm-branch-path");
            branch.revisions.forEach((revision, index) => {
                if (index) path.append(element("span", "h3cm-arrow", "→"));
                const key = checkpointRevisionKey(revision.scene, revision.revision);
                const sharedCount = occurrences.get(key) ?? 1;
                const card = button(
                    `S${revision.scene} · ${revision.revision.slice(0, 8)}`,
                    revision.prompt_preview || revision.scene_id,
                    () => selectRevision(revision, true, "", tip), "h3cm-revision",
                );
                const selected = state.selected?.scene === revision.scene &&
                    state.selected?.revision === revision.revision;
                if (sharedCount > 1) {
                    card.classList.add("h3cm-revision-shared");
                    card.dataset.sharedKey = key;
                    card.style.setProperty("--h3cm-shared-color", sharedColor(key));
                    card.append(element(
                        "span", "h3cm-shared-label",
                        `shared ×${sharedCount}`,
                    ));
                }
                card.append(element("small", "", `${selected ? "selected · " : ""}${revision.active ? "saved active" : "saved inactive"}${revision.ready ? "" : " · broken"}`));
                if (localKeys.has(key)) card.append(element("small", "h3cm-local-label", "local output"));
                if (selected) {
                    card.classList.add("h3cm-revision-selected");
                }
                path.append(card);
                const alternates = (revision.alternates ?? []).filter(
                    (alternate) => alternate.ready,
                );
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
                        if (alternate.used_in_final_cut) {
                            alt.classList.add("h3cm-alternate-used");
                            alt.append(element("small", "", "used in final cut"));
                        } else {
                            alt.append(element("small", "", "available take"));
                        }
                        if (state.selected?.scene === alternate.scene
                                && state.selected?.revision === alternate.revision) {
                            alt.classList.add("h3cm-revision-selected");
                        }
                        group.append(alt);
                    }
                    path.append(group);
                }
            });
            const slot = branch.attribution_slot;
            if ((slot?.candidates?.length || slot?.blocked_candidates?.length)
                    && tip && sceneVisible(slot.scene)) {
                path.append(element("span", "h3cm-arrow", "→"));
                const count = slot.candidates.length;
                const empty = button(
                    `S${slot.scene} · reuse saved clip`,
                    `${count} independent saved candidate${count === 1 ? "" : "s"} can be attributed here`,
                    () => selectAttribution(tip, slot),
                    "h3cm-revision h3cm-revision-empty",
                );
                empty.append(element(
                    "small", "", count ? `${count} available candidate${count === 1 ? "" : "s"}`
                        : "Check saved context requirements",
                ));
                if (state.attribution &&
                        state.attribution.parent.revision === tip.revision) {
                    empty.classList.add("h3cm-revision-empty-selected");
                }
                path.append(empty);
            }
            row.append(header, path);
            container.append(row);
        }
    }

    function variantCard(record, original = null, branchTip = null) {
        const card = button(`S${record.scene} · ${record.revision.slice(0, 8)}`,
            `${record.profile_path}\n${checkpointVariantLatentStatus(record)}`,
            () => selectVariant(record, original, branchTip), "h3cm-revision h3cm-processing-variant");
        card.append(element("small", "", record.profile));
        card.append(element("small", "", `${record.width || "?"}×${record.height || "?"} · ${record.ready ? "saved" : "missing artifacts"}`));
        card.append(element("small", "", record.latent_saved ? "full latent saved" : "full latent not saved"));
        if (currentVariant()?.key === record.key) card.classList.add("h3cm-revision-selected");
        return card;
    }

    function renderVariantBranchRows(container, rows) {
        for (const branch of rows) {
            const row = element("div", "h3cm-branch");
            const header = element("div", "h3cm-branch-head");
            header.append(element("span", branch.active ? "h3cm-branch-active" : "",
                `Source: ${branch.active ? "Project active branch" : branch.label}`));
            const path = element("div", "h3cm-branch-path");
            branch.revisions.forEach((original, index) => {
                if (index) path.append(element("span", "h3cm-arrow", "→"));
                const group = element("div", "h3cm-variant-group");
                group.append(element("small", "h3cm-muted", `Original S${original.scene} · ${original.revision.slice(0, 8)}`));
                const records = checkpointStageVariants(state.payload, state.stage, original);
                for (const record of records) group.append(variantCard(record, original, branch.revisions.at(-1)));
                if (!records.length) group.append(button(`S${original.scene} · not saved`,
                    `No saved ${stageLabel()} version of this source revision`,
                    () => selectRevision(original, false), "h3cm-revision h3cm-revision-empty"));
                path.append(group);
            });
            row.append(header, path);
            container.append(row);
        }
    }

    function renderUnlinkedVariants() {
        if (state.stage === "original") return;
        const records = checkpointStageVariants(state.payload, state.stage, null, activeChapterRange())
            .filter(item => !(item.originals ?? []).length);
        if (!records.length) return;
        branches.append(element("div", "h3cm-muted", "Saved versions with an unavailable or mismatched original (not attached to another take)"));
        const row = element("div", "h3cm-branch-path");
        for (const record of records) row.append(variantCard(record));
        branches.append(row);
    }

    function renderBranches() {
        branches.replaceChildren();
        const ranges = chapterRanges();
        if (!ranges.length) {
            const rows = checkpointBranchRows(state.payload);
            if (rows.length) renderBranchRows(branches, rows);
            else branches.append(element(
                "div", "h3cm-muted", "No versioned checkpoints were found.",
            ));
            return;
        }
        const visibleRanges = state.chapterTab === "all"
            ? ranges : ranges.filter((range) => range.id === state.chapterTab);
        let rendered = 0;
        for (const range of visibleRanges) {
            const rows = checkpointChapterBranchRows(state.payload, range);
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
                if (!collapsed) renderBranchRows(body, rows);
                section.append(heading, body);
                branches.append(section);
            } else renderBranchRows(branches, rows);
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
            : `${record.active ? "Active generation checkpoint" : "Inactive generation checkpoint"} · ${record.ready ? "Ready" : "Broken"}`);
        if (record.take_kind === "editorial_alternate") {
            addInspector("Original base", `Scene ${record.scene} · ${String(record.alternate_of_revision).slice(0, 8)}`);
            addInspector("Media", "Picture only · original audio and downstream lineage stay unchanged");
            addInspector("Use", "Select Original or ALT for this scene in Plan Studio; ALT cannot be loaded or activated as generation lineage");
        }
        addInspector("Branches", (record.branches ?? []).map((item) => item.label).join(", ") || "Unresolved lineage");
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
        if (state.stage !== "original") {
            deletion.classList.toggle("h3cm-delete-blocked", false);
            deletionTitle.textContent = "Processing version preview — original branch activation and deletion are unavailable in this tab.";
            activate.textContent = "Make branch active (project)";
            load.disabled = activate.disabled = remove.disabled = true;
            return;
        }
        const activationMode = selectedActivationMode();
        const rollsBack = activationMode === "rollback";
        activate.textContent = rollsBack
            ? "Roll active branch back (project)" : "Make branch active (project)";
        activate.title = rollsBack
            ? "Project-wide: retire later active scene pointers in this chapter without deleting saved revisions"
            : "Project-wide: promote this chapter for all workflows using this Run";
        deletion.classList.toggle("h3cm-delete-blocked", Boolean(state.deletion && !state.deletion.allowed));
        deletionTitle.textContent = checkpointDeletionTitle(state.deletion);
        load.disabled = state.busy || !canLoadSelected();
        activate.disabled = state.busy || !canActivateSelected();
        remove.disabled = state.busy || !state.deletion?.allowed;
        if (state.attribution) {
            load.disabled = true;
            activate.disabled = true;
            remove.disabled = true;
        }
        if (!state.deletion) return;
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
        renderUnlinkedVariants();
        renderDetail();
        renderDeletion();
    }

    async function refreshDeletionPreview() {
        const record = state.selected;
        const token = ++state.requestToken;
        if (state.stage !== "original" || !record || !state.runName) return;
        deletionTitle.textContent = "Inspecting owned files and dependencies…";
        remove.disabled = true;
        try {
            const payload = await jsonRequest(
                "/minimax_h3_context_loop/checkpoint-revisions/delete-preview", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({run_name:state.runName, scene:record.scene, revision:record.revision}),
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
        try {
            const query = new URLSearchParams({
                run_name:state.runName,
                cache_bust:String(Date.now()),
            });
            state.payload = await jsonRequest(
                `/minimax_h3_context_loop/checkpoints?${query}`,
                {cache:"no-store"},
            );
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
            if (state.stage === "original" && state.initialRefresh && !checkpointLocalSelection(selectionWidget?.value)) {
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
            if (selected) void refreshDeletionPreview();
        } catch (error) {
            state.payload = null;
            state.selected = null;
            state.deletion = null;
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
            render();
        } finally {
            setBusy(false);
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

    function applyActivatedRevisions(planNode, revisions) {
        const target = widget(planNode, "plan_json");
        if (!target) return false;
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
            "This activates the branch project-wide and restores the connected Plan. For output only, cancel and choose Use branch locally.\n\n" +
            `${scope.title} scenes ${scope.start}–${scope.end} will use this branch. Other chapters keep their active checkpoint branches. Saved revision files are kept.`,
        );
        if (!confirmed) return;
        setBusy(true, "Loading saved Plan and checkpoint lineage…");
        try {
            const runQuery = new URLSearchParams({
                run_name: state.runName,
                include_assets: "false",
            });
            const runBody = await jsonRequest(
                `/minimax_h3_context_loop/run?${runQuery.toString()}`,
            );
            const sameConnectedRun = activePlanRun() === state.runName;
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
        const activationMode = selectedActivationMode();
        const rollsBack = activationMode === "rollback";
        if (!record || !canActivateSelected() || state.busy) return;
        const confirmed = window.confirm(
            `${rollsBack ? "Roll" : "Make"} ${scope.title} ${rollsBack ? "back" : "active"} through scene ${record.scene} revision ${record.revision.slice(0, 8)}?\n\n` +
            "This changes the project-wide active branch for all workflows using this Run. Use branch locally changes only this manager's output.\n\n" +
            `${rollsBack ? `Active pointers after scene ${record.scene} will be cleared. ` : ""}` +
            `Only scenes ${scope.start}–${scope.end} are affected. Other chapters keep their active branches. If a Plan is connected, the selected chapter's saved scene settings are restored. No saved revision, workflow, reference, or assembled video is deleted.`,
        );
        if (!confirmed) return;
        setBusy(true, rollsBack
            ? "Rolling active checkpoint branch back…"
            : "Promoting selected checkpoint lineage…");
        try {
            const payload = await mutationRequest(node, state.runName,
                "/minimax_h3_context_loop/checkpoint-revisions/restore", {
                    method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({
                        run_name:state.runName,
                        resume_scene:Number(record.scene) + 1,
                        revisions:lineage,
                        activate_only:true,
                        scope_start_scene:scope.start,
                        scope_end_scene:scope.end,
                    }),
                });
            const planNode = upstreamPlanNode(node);
            const planUpdated = Boolean(planNode &&
                applyActivatedRevisions(planNode, payload.restored ?? []));
            await refreshCheckpoints();
            status.className = "h3cm-status";
            status.textContent = `${scope.title} ${rollsBack ? "rolled back" : "is now active"} through scene ${record.scene} revision ${record.revision.slice(0, 8)}. ` +
                `${payload.retired_scope_pointers || 0} later pointer${payload.retired_scope_pointers === 1 ? " was" : "s were"} cleared inside this chapter; other chapters were preserved; all immutable revisions were kept` +
                `${planUpdated ? "; connected Plan scene settings were restored." : "."}`;
        } catch (error) {
            status.className = "h3cm-status h3cm-error";
            status.textContent = error.message;
        } finally {
            setBusy(false);
        }
    }

    async function deleteSelected() {
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

    runSelect.addEventListener("change", () => {
        if (checkpointLocalSelection(selectionWidget?.value)) {
            if (!window.confirm("Switch runs and release this workflow's local output pin? The project active branches will not change.")) {
                runSelect.value = state.runName;
                return;
            }
            writeOutputSelection("");
        }
        state.runName = runSelect.value;
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
    node.onRemoved = function () {
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
