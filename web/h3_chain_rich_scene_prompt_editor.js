import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    parsePlanJson,
    planToJson,
    promptTextToLines,
    promptValueToText,
    sharedPrompt,
} from "./h3_chain_plan_core.mjs?v=0.6.8";
import {
    buildPromptAssistantContext,
    makePromptAssistRequest,
} from "./h3_prompt_assistant_core.mjs?v=0.6.8";
import {PromptAssistantClient} from "./h3_prompt_assistant_client.mjs?v=0.6.8";
import {
    directOptimizerConfigurationError,
    makeDirectPromptOptimizeRequest,
} from "./h3_prompt_optimizer_core.mjs?v=0.6.8";
import {
    openPromptOptimizerSettings,
    promptOptimizerBackend,
    promptOptimizerDirectConfig,
    promptOptimizerMcpProvider,
} from "./h3_prompt_optimizer_settings.js";
import {
    promptRevisionHelp,
    promptRevisionLabel,
    promptRevisionNavigation,
    promptRevisionTree,
} from "./h3_prompt_history_core.mjs?v=0.6.8";
import {
    availableReferenceRecords,
    convertTaggedPictureReference,
    referenceReplacementToken,
    replacePromptReferenceOccurrence,
    taggedPictureReferenceMode,
    taggedPictureReferenceToken,
} from "./h3_reference_preview_core.mjs?v=0.6.8";
import {
    PromptUndoHistory,
    RICH_PROMPT_GUIDES,
    normalizeRichGuide,
    optimizerSource,
    promptUndoDirection,
    richGenerationMode,
    richGuideInstruction,
    tokenizeRichPrompt,
} from "./h3_rich_prompt_editor_core.mjs?v=0.6.8";
import {createPromptCompletionController} from "./h3_prompt_completion_core.mjs?v=0.6.8";
import {bindPromptMarkerInteractions} from "./h3_prompt_marker_ui.mjs?v=0.6.8";
import {isWorkflowSaveShortcut, promptEditorRichText} from "./h3_prompt_editor_settings_core.mjs?v=0.6.8";
import {promptEditorPreferences} from "./h3_prompt_editor_settings.js";
import {createH3PromptSchemaController} from "./h3_prompt_schema_ui.mjs?v=0.6.8";
import * as promptCompanionSync from "./h3_prompt_companion_sync.mjs?v=0.6.8";
import {
    PROJECT_ASSET_CATALOG_CHANGED_EVENT,
} from "./h3_project_asset_sync_core.mjs?v=0.6.8";

const {
    publishCompanionScene,
    rebaseScenePrompt,
} = promptCompanionSync;
const activeSceneIndexAfterRefresh =
    typeof promptCompanionSync.activeSceneIndexAfterRefresh === "function"
        ? promptCompanionSync.activeSceneIndexAfterRefresh
        : (_previousPlan, nextPlan, sceneIndex) => Math.min(
            Math.max(0, Math.trunc(Number(sceneIndex) || 0)),
            Math.max(0, (nextPlan?.shots?.length ?? 1) - 1),
        );
const planHasNonPromptChanges =
    typeof promptCompanionSync.planHasNonPromptChanges === "function"
        ? promptCompanionSync.planHasNonPromptChanges
        : () => true;
function publishCompanionPrompt(...args) {
    return promptCompanionSync.publishCompanionPrompt?.(...args) ?? 0;
}

// The rich mention presentation and compact optimizer interaction are inspired
// by nkxx188/ComfyUI-MiniMaxH3-Easy (MIT). Graph discovery, scene scheduling,
// revision storage, and Codex/Hermes bridge integration are implemented here.

const NODE_NAME = "MiniMaxH3ChainRichScenePromptEditor";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const PLAN_NAMES = new Set([PLAN_NAME, "MiniMaxH3ChainPlanModern"]);
const ACTIVE_PROPERTY = "h3_rich_prompt_active_scene";
const FONT_PROPERTY = "h3_rich_prompt_font_size";
const GUIDE_PROPERTY = "h3_rich_prompt_guide";
const PRESENTATION_PROPERTY = "h3_rich_prompt_rich_text";
const DEFAULT_FONT = 17;
const MIN_FONT = 12;
const MAX_FONT = 32;
const PROMPT_SYNC_DELAY_MS = 140;
const PROMPT_ANALYSIS_DELAY_MS = 90;

const ICONS = Object.freeze({
    picture: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9" r="1.5"/><path d="m5 17 4.5-4.5 3.2 3.2 2.3-2.3 4 3.6"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3z"/></svg>',
    audio: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13v-2M8 17V7M12 20V4M16 16V8M20 13v-2"/></svg>',
    subject: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M5 21c.8-4.2 3.1-6.3 7-6.3s6.2 2.1 7 6.3"/></svg>',
    dialogue: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v11H9l-4 3z"/><path d="M8 9h8M8 12h6"/></svg>',
    reference: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1"/></svg>',
    section: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14M5 12h14M5 19h14"/></svg>',
    flow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h11M12 4l3 3-3 3M20 17H9M12 14l-3 3 3 3"/></svg>',
    speaker: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9c1.5 1.5 1.5 4.5 0 6"/></svg>',
    sparkle: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 1.4 5.1L18 9l-4.6 1.9L12 16l-1.4-5.1L6 9l4.6-1.9zM19 15l.7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7z"/></svg>',
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>',
});

function injectStyles() {
    if (document.getElementById("h3-rich-prompt-editor-style")) return;
    const style = document.createElement("style");
    style.id = "h3-rich-prompt-editor-style";
    style.textContent = `
      .h3rp-root { --h3rp-bg:color-mix(in srgb,var(--comfy-menu-bg,#202124) 94%,#101727);
        --h3rp-panel:color-mix(in srgb,var(--comfy-input-bg,#111827) 86%,#26334d);
        --h3rp-border:color-mix(in srgb,var(--border-color,#555) 70%,#7591bd);
        --h3rp-text:var(--input-text,#edf2fa); --h3rp-muted:color-mix(in srgb,var(--h3rp-text) 57%,transparent);
        /* Theme-aware semantic colors remain pastel on dark ComfyUI themes
           and gain enough foreground contrast on light themes. */
        --h3rp-accent:color-mix(in srgb,var(--h3rp-text) 38%,#4f83ff);
        --h3rp-token-picture:color-mix(in srgb,var(--h3rp-text) 42%,#139be8);
        --h3rp-token-video:color-mix(in srgb,var(--h3rp-text) 42%,#9355d6);
        --h3rp-token-audio:color-mix(in srgb,var(--h3rp-text) 42%,#d47700);
        --h3rp-token-subject:color-mix(in srgb,var(--h3rp-text) 42%,#26934a);
        --h3rp-token-dialogue:color-mix(in srgb,var(--h3rp-text) 42%,#cf3976);
        --h3rp-token-section:color-mix(in srgb,var(--h3rp-text) 38%,#5f87ff);
        --h3rp-token-flow:color-mix(in srgb,var(--h3rp-text) 42%,#00a99d);
        --h3rp-token-speaker:color-mix(in srgb,var(--h3rp-text) 42%,#d268b7);
        --h3rp-token-danger:color-mix(in srgb,var(--h3rp-text) 42%,#d44747);
        --h3rp-font-size:17px; box-sizing:border-box; width:100%; height:100%;
        min-height:560px; display:flex; flex-direction:column; gap:8px; overflow:hidden; padding:10px;
        border:1px solid var(--h3rp-border); border-radius:9px; background:var(--h3rp-bg);
        color:var(--h3rp-text); font:12px/1.35 system-ui,sans-serif; }
      .h3rp-root *, .h3rp-root *::before, .h3rp-root *::after { box-sizing:border-box; }
      .h3rp-row,.h3rp-head,.h3rp-nav,.h3rp-toolbar,.h3rp-footer,.h3rp-history { display:flex; align-items:center; gap:6px; }
      .h3rp-head { justify-content:space-between; }
      .h3rp-title { color:var(--h3rp-accent); font-size:15px; font-weight:760; }
      .h3rp-context,.h3rp-muted { color:var(--h3rp-muted); }
      .h3rp-context { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .h3rp-root button,.h3rp-root select { min-height:30px; color:var(--h3rp-text); font:inherit;
        border:1px solid var(--h3rp-border); border-radius:6px; background:var(--comfy-input-bg,#171a21); }
      .h3rp-root button { display:inline-flex; align-items:center; justify-content:center; gap:5px; padding:5px 8px;
        cursor:pointer; white-space:nowrap; }
      .h3rp-root button:hover,.h3rp-root button:focus-visible { border-color:var(--h3rp-accent); outline:none; }
      .h3rp-root button:disabled,.h3rp-root select:disabled { opacity:.42; cursor:not-allowed; }
      .h3rp-root select { min-width:0; padding:4px 7px; }
      .h3rp-nav select { flex:1; }
      .h3rp-icon { width:16px; height:16px; display:inline-flex; flex:0 0 16px; color:currentColor; }
      .h3rp-icon svg { width:100%; height:100%; fill:none; stroke:currentColor; stroke-width:1.7;
        stroke-linecap:round; stroke-linejoin:round; }
      .h3rp-editor-shell { position:relative; flex:1 1 auto; min-height:300px; overflow:hidden;
        border:1px solid var(--h3rp-border); border-radius:8px; background:var(--comfy-input-bg,#11141a); }
      .h3rp-editor-shell:focus-within { border-color:var(--h3rp-accent);
        box-shadow:0 0 0 1px color-mix(in srgb,var(--h3rp-accent) 40%,transparent); }
      .h3rp-editor { width:100%; height:100%; min-height:300px; overflow:auto; padding:13px 14px;
        outline:none; white-space:pre-wrap; overflow-wrap:anywhere; caret-color:var(--h3rp-text);
        font:var(--h3rp-font-size)/1.58 ui-monospace,SFMono-Regular,Consolas,monospace; }
      .h3rp-editor:empty::before { content:attr(data-placeholder); color:var(--h3rp-muted); pointer-events:none; }
      .h3rp-basic-prompt-label { display:flex; flex-direction:column; gap:4px;
        color:var(--h3rp-muted); font-size:12px; }
      .h3rp-basic-prompt {
        width:100%; min-height:64px; resize:vertical; padding:8px 10px;
        border:1px solid var(--h3rp-border); border-radius:8px;
        outline:none; background:var(--comfy-input-bg,#11141a); color:var(--h3rp-text);
        font:13px/1.4 inherit;
      }
      .h3rp-basic-prompt:focus { border-color:var(--h3rp-accent);
        box-shadow:0 0 0 1px color-mix(in srgb,var(--h3rp-accent) 40%,transparent); }
      .h3rp-history-tree-basic-prompt { margin-top:3px; padding:4px 6px;
        border-left:2px solid var(--h3rp-border); color:var(--h3rp-muted);
        font-size:12px; white-space:pre-wrap; }
      .h3rp-editor[contenteditable="false"] { opacity:.68; cursor:wait; }
      .h3rp-token { display:inline-flex; align-items:center; gap:3px; max-width:320px; margin:0 1px;
        padding:1px 4px 1px 2px; border:1px solid currentColor; border-radius:5px; vertical-align:1px;
        line-height:1.25; cursor:pointer; user-select:all;
        background:color-mix(in srgb,currentColor 14%,transparent); }
      .h3rp-token-picture { color:var(--h3rp-token-picture); }
      .h3rp-token-video { color:var(--h3rp-token-video); }
      .h3rp-token-audio { color:var(--h3rp-token-audio); }
      .h3rp-token-subject { color:var(--h3rp-token-subject); }
      .h3rp-token-dialogue { color:var(--h3rp-token-dialogue); }
      .h3rp-token-section { display:inline; margin:0; padding:0; border:0; border-radius:0;
        color:var(--h3rp-token-section); background:none; font-weight:650; cursor:text; user-select:text; }
      .h3rp-token-flow { color:var(--h3rp-token-flow); }
      .h3rp-token-speaker { color:var(--h3rp-token-speaker); }
      .h3rp-token-unknown,.h3rp-token-inactive { color:var(--h3rp-token-danger); border-style:dashed; }
      .h3rp-presentation-active { border-color:var(--h3rp-accent) !important;
        color:var(--h3rp-accent) !important; }
      .h3rp-token-thumb { width:18px; height:18px; flex:0 0 18px; object-fit:cover; border-radius:3px;
        background:rgba(255,255,255,.09); }
      .h3rp-token-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .h3rp-toolbar { flex-wrap:wrap; }
      .h3rp-toolbar .h3rp-guide { min-width:150px; }
      .h3rp-toolbar-spacer { flex:1; }
      .h3rp-status { min-width:0; color:var(--h3rp-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .h3rp-status-error { color:#ffaaaa; }
      .h3rp-status-success { color:#9bdab0; }
      .h3rp-spinner .h3rp-icon { animation:h3rp-spin .9s linear infinite; }
      @keyframes h3rp-spin { to { transform:rotate(360deg); } }
      .h3rp-ref-tray { display:none; max-height:260px; overflow:auto; padding:8px; gap:6px;
        border:1px solid var(--h3rp-border); border-radius:7px; background:var(--h3rp-panel); }
      .h3rp-ref-tray.h3rp-open { display:grid; grid-template-columns:repeat(auto-fill,minmax(205px,1fr)); }
      .h3rp-ref-help { grid-column:1/-1; color:var(--h3rp-muted); }
      .h3rp-ref-card { justify-content:flex-start !important; min-width:0; text-align:left; }
      .h3rp-ref-card-shell { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px;
        align-items:stretch; min-width:0; }
      .h3rp-ref-mode { display:flex; flex-direction:column; gap:2px; }
      .h3rp-ref-mode button { min-width:38px !important; min-height:0 !important; padding:2px 5px !important;
        font-size:10px !important; }
      .h3rp-ref-mode button.h3rp-selected { border-color:var(--h3rp-accent) !important;
        color:var(--h3rp-accent) !important; }
      .h3rp-ref-card.h3rp-inactive { opacity:.46; }
      .h3rp-ref-card-copy { min-width:0; overflow:hidden; }
      .h3rp-ref-card-title,.h3rp-ref-card-detail { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .h3rp-ref-card-title { font-weight:700; }
      .h3rp-ref-card-detail { color:var(--h3rp-muted); font-size:10px; }
      .h3rp-footer { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); color:var(--h3rp-muted); }
      .h3rp-footer-status { text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .h3rp-history { justify-content:center; }
      .h3rp-history-nav { display:flex; align-items:center; border:1px solid var(--h3rp-border); border-radius:999px; padding:1px 3px; }
      .h3rp-history-nav button { min-width:25px; min-height:22px; padding:1px 5px; border:0; border-radius:999px; background:transparent; }
      .h3rp-history-count { min-width:43px; text-align:center; color:var(--h3rp-text); font-variant-numeric:tabular-nums; }
      .h3rp-history-meta { max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
      .h3rp-history-tree { position:relative; flex:0 0 auto; }
      .h3rp-history-tree > summary { list-style:none; cursor:pointer; padding:3px 7px;
        border:1px solid var(--h3rp-border); border-radius:999px; background:var(--comfy-input-bg,#11141a);
        color:var(--h3rp-text); user-select:none; }
      .h3rp-history-tree > summary::-webkit-details-marker { display:none; }
      .h3rp-history-tree[open] > summary { border-color:var(--h3rp-accent); }
      .h3rp-history-tree-panel { position:absolute; z-index:20; bottom:calc(100% + 7px); left:50%;
        transform:translateX(-50%); width:min(440px,calc(100vw - 30px)); max-height:310px; overflow:auto;
        padding:7px; border:1px solid var(--h3rp-border); border-radius:8px; background:var(--h3rp-bg);
        box-shadow:0 14px 36px rgba(0,0,0,.48); }
      .h3rp-history-tree-row { display:flex; align-items:center; gap:4px; min-width:0; margin:2px 0; border-radius:5px; }
      .h3rp-history-tree-row.h3rp-history-active { background:rgba(142,181,255,.12); }
      .h3rp-history-tree-row.h3rp-history-archived { opacity:.58; }
      .h3rp-history-tree-select { flex:1 1 auto; min-width:0; justify-content:flex-start !important;
        min-height:26px !important; padding:3px 6px !important; border-color:transparent !important;
        background:transparent !important; text-align:left; }
      .h3rp-history-branch { flex:0 0 auto; color:var(--h3rp-muted); }
      .h3rp-history-tree-label { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        color:var(--h3rp-text); }
      .h3rp-history-tree-badge { flex:0 0 auto; color:var(--h3rp-muted); font-size:10px; }
      .h3rp-history-tree-action { min-width:25px !important; min-height:25px !important; padding:2px 5px !important; }
      .h3rp-history-tree-tools { display:flex; justify-content:space-between; align-items:center; gap:8px;
        margin-top:6px; padding-top:6px; border-top:1px solid var(--h3rp-border);
        color:var(--h3rp-muted); font-size:10px; }
      .h3rp-history-tree-tools button { min-height:24px; padding:2px 6px; }
      .h3rp-error { padding:12px; border:1px solid #a76565; border-radius:7px; color:#ffb3b3; background:#351f24; white-space:pre-wrap; }
      .h3rp-popover { position:fixed; z-index:100000; width:min(360px,calc(100vw - 24px)); padding:9px;
        border:1px solid #60718c; border-radius:9px; background:#171a20; color:#eef2f8;
        box-shadow:0 14px 38px rgba(0,0,0,.48); font:12px/1.4 system-ui,sans-serif; }
      .h3rp-popover[hidden] { display:none; }
      .h3rp-popover-title { margin-bottom:6px; font-weight:750; }
      .h3rp-popover-media { display:block; width:100%; max-height:240px; object-fit:contain; border-radius:6px; background:#08090c; }
      .h3rp-popover audio.h3rp-popover-media { height:42px; background:transparent; }
      .h3rp-popover-detail { margin-top:6px; color:rgba(238,242,248,.62); white-space:pre-wrap; overflow-wrap:anywhere; }
      .h3rp-popover-editor { display:grid; gap:8px; }
      .h3rp-popover-field { display:grid; grid-template-columns:82px minmax(0,1fr); align-items:center; gap:7px; }
      .h3rp-popover-field > span { color:rgba(238,242,248,.68); }
      .h3rp-popover-field select,.h3rp-popover-field input { width:100%; min-width:0; padding:5px 7px;
        border:1px solid #60718c; border-radius:5px; background:#0e1117; color:#eef2f8; }
      .h3rp-popover-actions { display:flex; justify-content:flex-end; gap:6px; }
      .h3rp-popover-actions button { min-height:0; padding:4px 9px; }
    `;
    document.head.append(style);
}

function element(tag, className = "", text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function icon(kind) {
    const item = element("span", "h3rp-icon");
    item.innerHTML = ICONS[kind] ?? ICONS.reference;
    return item;
}

function button(label, title, action, iconKind = null) {
    const item = element("button");
    item.type = "button";
    item.title = title;
    if (iconKind) item.append(icon(iconKind));
    if (label) item.append(document.createTextNode(label));
    // Pointer-clicking a toolbar or reference control must not discard the
    // contenteditable selection it is about to operate on. Keyboard focus via
    // Tab remains available because only pointer default behavior is blocked.
    item.addEventListener("pointerdown", (event) => event.preventDefault());
    item.addEventListener("click", action);
    return item;
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function allNodes(graph, output = []) {
    for (const node of graph?._nodes ?? []) {
        output.push(node);
        if (node.subgraph) allNodes(node.subgraph, output);
    }
    return output;
}

function upstreamPlanNode(start) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const candidate = queue.shift();
        if (!candidate || seen.has(candidate)) continue;
        seen.add(candidate);
        if (candidate !== start && PLAN_NAMES.has(nodeType(candidate))) return candidate;
        for (const input of candidate.inputs ?? []) {
            const link = input.link == null ? null : candidate.graph?.links?.[input.link];
            const parent = link ? candidate.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function inputSource(node, name) {
    const input = node?.inputs?.find((item) => item.name === name);
    const link = input?.link == null ? null : node.graph?.links?.[input.link];
    return link ? node.graph?.getNodeById?.(link.origin_id) ?? null : null;
}

function mediaExtension(kind) {
    if (kind === "image") return /\.(?:avif|bmp|gif|jpe?g|png|webp)$/i;
    if (kind === "video") return /\.(?:m4v|mkv|mov|mp4|webm)$/i;
    return /\.(?:aac|flac|m4a|mp3|ogg|opus|wav)$/i;
}

function widgetAsset(value, kind) {
    if (value && typeof value === "object" && value.filename) {
        return {filename:String(value.filename), subfolder:String(value.subfolder ?? ""), type:String(value.type ?? "input")};
    }
    let text = typeof value === "string" ? value.trim() : "";
    if (!text) return null;
    if (/^(?:blob:|data:|https?:|\/api\/view\?|\/view\?)/i.test(text)) return {url:text};
    let type = "input";
    const annotated = text.match(/\s+\[(input|output|temp)\]\s*$/i);
    if (annotated) {
        type = annotated[1].toLowerCase();
        text = text.slice(0, annotated.index).trim();
    }
    text = text.replaceAll("\\", "/").replace(/^\/+/, "");
    if (!mediaExtension(kind).test(text)) return null;
    const slash = text.lastIndexOf("/");
    return {filename:slash >= 0 ? text.slice(slash + 1) : text, subfolder:slash >= 0 ? text.slice(0, slash) : "", type};
}

function assetUrl(asset) {
    if (!asset) return null;
    if (asset.url) return asset.url;
    const query = new URLSearchParams({filename:asset.filename, subfolder:asset.subfolder ?? "", type:asset.type ?? "input"});
    return api.apiURL(`/view?${query.toString()}`);
}

function previewFromNode(node, kind) {
    if (kind === "image") {
        const rendered = node?.imgs?.[0];
        const src = typeof rendered === "string" ? rendered : rendered?.src;
        if (src) return src;
    }
    for (const widget of node?.widgets ?? []) {
        const asset = widgetAsset(widget.value, kind);
        if (asset) return assetUrl(asset);
    }
    return null;
}

function findMediaPreview(start, kind) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const candidate = queue.shift();
        if (!candidate || seen.has(candidate)) continue;
        seen.add(candidate);
        const url = previewFromNode(candidate, kind);
        if (url) return {url, source:candidate};
        for (const input of candidate.inputs ?? []) {
            const parent = inputSource(candidate, input.name);
            if (parent) queue.push(parent);
        }
    }
    return {url:null, source:start};
}

function referenceMediaPreview(record, kind) {
    if (record?.previewUrl) {
        return {
            url: api.apiURL(record.previewUrl),
            source: record.source ?? record.node ?? null,
        };
    }
    return findMediaPreview(record?.source, kind);
}

function findMediaAsset(start, kind) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const candidate = queue.shift();
        if (!candidate || seen.has(candidate)) continue;
        seen.add(candidate);
        for (const widget of candidate.widgets ?? []) {
            const asset = widgetAsset(widget.value, kind);
            if (asset && !asset.url) {
                return {
                    filename:asset.filename,
                    subfolder:asset.subfolder ?? "",
                    storage:asset.type ?? "input",
                };
            }
        }
        for (const input of candidate.inputs ?? []) {
            const parent = inputSource(candidate, input.name);
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function clamp(value, minimum, maximum, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Math.max(minimum, Math.min(maximum, Math.round(numeric))) : fallback;
}

function editorPlainText(editor, {trimFinalNewline = true} = {}) {
    function read(node, root) {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
        if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
            let text = "";
            for (const child of node.childNodes) text += read(child, root);
            return text;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return "";
        if (node.classList?.contains("h3rp-token")) return node.dataset.token ?? node.textContent ?? "";
        if (node.tagName === "BR") return "\n";
        let text = "";
        for (const child of node.childNodes) text += read(child, root);
        if (["DIV", "P"].includes(node.tagName) && node !== root && !text.endsWith("\n")) text += "\n";
        return text;
    }
    const text = read(editor, editor);
    return trimFinalNewline ? text.replace(/\n$/, "") : text;
}

function selectedEditorPlainText(editor) {
    const selection = globalThis.getSelection?.();
    if (!selection?.rangeCount || selection.isCollapsed) return null;
    const inside = (node) => node === editor || editor.contains(node);
    if (!inside(selection.anchorNode) || !inside(selection.focusNode)) return null;
    const fragment = selection.getRangeAt(0).cloneContents();
    return editorPlainText(fragment);
}

function copyEditorSelection(editor, event, cut = false) {
    const text = selectedEditorPlainText(editor);
    if (text == null || !event.clipboardData) return false;
    // A decorated tag contains icons/thumbnails as well as its visible label.
    // Copy its canonical data-token value so another prompt editor receives
    // exactly the original prompt markup rather than browser-generated HTML.
    event.clipboardData.setData("text/plain", text);
    event.preventDefault();
    if (cut) {
        const selection = globalThis.getSelection?.();
        const range = selection.getRangeAt(0);
        range.deleteContents();
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
        editor.dispatchEvent(new Event("input", {bubbles:true}));
    }
    return true;
}

function editorPointTextOffset(editor, node, offset) {
    if (!node || (node !== editor && !editor.contains(node))) return null;
    const range = document.createRange();
    range.selectNodeContents(editor);
    try {
        range.setEnd(node, offset);
    } catch (_error) {
        return null;
    }
    return editorPlainText(range.cloneContents()).length;
}

function editorSelectionOffsets(editor) {
    const selection = globalThis.getSelection?.();
    if (!selection?.rangeCount) return null;
    const selected = selection.getRangeAt(0);
    const start = editorPointTextOffset(
        editor, selected.startContainer, selected.startOffset,
    );
    const end = editorPointTextOffset(
        editor, selected.endContainer, selected.endOffset,
    );
    return start == null || end == null ? null : {start, end};
}

function selectionTextOffset(editor) {
    const selection = globalThis.getSelection?.();
    const offset = selection?.rangeCount
        ? editorPointTextOffset(editor, selection.focusNode, selection.focusOffset)
        : null;
    return offset == null ? editorPlainText(editor).length : offset;
}

function restoreCaret(editor, requested) {
    const target = Math.max(0, Number(requested) || 0);
    const range = document.createRange();
    const selection = globalThis.getSelection?.();
    let consumed = 0;
    let placed = false;
    function visit(node) {
        if (placed) return;
        if (node.nodeType === Node.TEXT_NODE) {
            const length = node.textContent?.length ?? 0;
            if (target <= consumed + length) {
                range.setStart(node, Math.max(0, target - consumed));
                placed = true;
                return;
            }
            consumed += length;
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.classList?.contains("h3rp-token")) {
            const length = String(node.dataset.token ?? "").length;
            if (target <= consumed + length) {
                if (target <= consumed) range.setStartBefore(node);
                else range.setStartAfter(node);
                placed = true;
                return;
            }
            consumed += length;
            return;
        }
        for (const child of node.childNodes) visit(child);
    }
    visit(editor);
    if (!placed) range.selectNodeContents(editor), range.collapse(false);
    else range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
}

function restoreTextSelection(editor, requestedStart, requestedEnd) {
    const start = Math.max(0, Number(requestedStart) || 0);
    const end = Math.max(start, Number(requestedEnd) || start);
    let consumed = 0;
    let startPoint = null;
    let endPoint = null;
    function visit(node) {
        if (startPoint && endPoint) return;
        if (node.nodeType === Node.TEXT_NODE) {
            const length = node.textContent?.length ?? 0;
            if (!startPoint && start >= consumed && start <= consumed + length) {
                startPoint = [node, start - consumed];
            }
            if (!endPoint && end >= consumed && end <= consumed + length) {
                endPoint = [node, end - consumed];
            }
            consumed += length;
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        if (node.classList?.contains("h3rp-token")) {
            consumed += String(node.dataset.token ?? "").length;
            return;
        }
        for (const child of node.childNodes) visit(child);
    }
    visit(editor);
    if (!startPoint || !endPoint) {
        restoreCaret(editor, end);
        return;
    }
    const range = document.createRange();
    range.setStart(...startPoint);
    range.setEnd(...endPoint);
    const selection = globalThis.getSelection?.();
    selection?.removeAllRanges();
    selection?.addRange(range);
}

function insertPlainText(editor, text) {
    editor.focus();
    const selection = globalThis.getSelection?.();
    const range = selection?.rangeCount && editor.contains(selection.anchorNode)
        ? selection.getRangeAt(0) : document.createRange();
    if (!selection?.rangeCount || !editor.contains(selection.anchorNode)) {
        range.selectNodeContents(editor);
        range.collapse(false);
    }
    range.deleteContents();
    const textNode = document.createTextNode(String(text ?? ""));
    range.insertNode(textNode);
    range.setStartAfter(textNode);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    editor.dispatchEvent(new Event("input", {bubbles:true}));
}

function promptAssistantIdentityKey(node) {
    const workflow = app.extensionManager?.workflow?.activeWorkflow;
    const workflowIdentity = workflow?.path ?? workflow?.activeState?.id ?? workflow?.filename ?? "legacy-workflow";
    return `rich-workflow-${workflowIdentity}-node-${node.id ?? "new"}`;
}

function mount(node) {
    if (node._h3RichPromptMounted || typeof node.addDOMWidget !== "function") return;
    node._h3RichPromptMounted = true;
    injectStyles();
    node.properties ??= {};

    const root = element("div", "h3rp-root");
    root.title = "Edits only the selected scene prompt in the connected H3 Chain Plan.";
    for (const eventName of ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick"]) {
        root.addEventListener(eventName, (event) => event.stopPropagation());
    }
    // Keep ComfyUI/LiteGraph's canvas shortcuts from claiming Ctrl/Cmd+V (and
    // the other native editing shortcuts) while focus is inside this DOM
    // widget. stopPropagation deliberately preserves the browser's default
    // contenteditable action; preventDefault here would break paste.
    for (const eventName of ["keydown", "keyup", "keypress", "copy", "cut", "paste"]) {
        root.addEventListener(eventName, (event) => {
            // Saving the workflow must remain available while typing; all
            // editing/undo shortcuts keep their existing scene-local routing.
            if (isWorkflowSaveShortcut(event)) {
                if (event.type === "keydown") flushPlanEffects();
                return;
            }
            event.stopPropagation();
        });
    }
    root.addEventListener("wheel", (event) => event.stopPropagation());

    const state = {
        plan:null, planNode:null, planWidget:null, lastValue:"", lastRunName:"",
        active:Math.max(0, Number(node.properties[ACTIVE_PROPERTY]) || 0),
        fontSize:clamp(node.properties[FONT_PROPERTY], MIN_FONT, MAX_FONT, DEFAULT_FONT),
        guide:normalizeRichGuide(node.properties[GUIDE_PROPERTY]),
        decorated:promptEditorRichText(node.properties, PRESENTATION_PROPERTY, promptEditorPreferences()),
        records:[], referenceMode:null, editor:null, refs:null, status:null, optimizerStatus:null,
        referenceSyntax:new Map(),
        promptSelection:null,
        completion:null, schema:null,
        history:{sceneKey:"", data:null, revisionId:null, host:null, loadToken:0, loadPromise:null,
            saveTimer:null, pendingDraft:null, savePromise:null, error:"", treeOpen:false,
            showArchived:false},
        optimizer:{client:null, preparing:false, requestId:null, meta:null, origins:new Map(), providers:null,
            abortController:null, activeBackend:null, error:"", message:"", pendingResult:null},
        undoByScene:new Map(), promptUndo:null,
        popover:null, popoverTimer:null, popoverPinned:false, pollTimer:null,
        planSyncTimer:null, planSyncPending:null, analysisTimer:null,
        applyPromptHistoryShortcut:null, ownsPromptHistoryTarget:null,
        disposed:false,
    };
    node._h3RichPromptState = state;

    // Own prompt undo during capture so ComfyUI's workflow-level Ctrl/Cmd+Z
    // never sees the gesture first and restores an older active-scene
    // property. The callback is replaced whenever the active scene renders.
    root.addEventListener("keydown", (event) => {
        const direction = promptUndoDirection(event);
        if (!direction || !state.ownsPromptHistoryTarget?.(event.target)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        state.applyPromptHistoryShortcut?.(direction);
    }, true);

    function dirty() {
        node.graph?.setDirtyCanvas?.(true, true);
        app.graph?.setDirtyCanvas?.(true, true);
    }

    function persistView() {
        node.properties[ACTIVE_PROPERTY] = state.active;
        node.properties[FONT_PROPERTY] = state.fontSize;
        node.properties[GUIDE_PROPERTY] = state.guide;
        node.properties[PRESENTATION_PROPERTY] = state.decorated;
        dirty();
    }

    function rebaseActivePromptOntoLivePlan() {
        if (!state.plan || !state.planWidget) return false;
        let live;
        try { live = parsePlanJson(String(state.planWidget.value ?? "")); }
        catch (_error) { return false; }
        const index = rebaseScenePrompt(state.plan, live, state.active);
        if (index < 0) return false;
        state.active = index;
        node.properties[ACTIVE_PROPERTY] = index;
        return true;
    }

    function planRunName() {
        return String(state.planNode?.widgets?.find((item) => item.name === "run_name")?.value ?? "").trim();
    }

    function flushPlanEffects() {
        if (state.planSyncTimer != null) {
            window.clearTimeout(state.planSyncTimer);
            state.planSyncTimer = null;
        }
        const pending = state.planSyncPending;
        state.planSyncPending = null;
        if (!pending || !state.planWidget || !state.planNode) return;
        const liveValue = String(state.planWidget.value ?? "");
        if (liveValue !== pending.value) {
            if (!rebaseActivePromptOntoLivePlan()) {
                if (state.status) {
                    state.status.textContent =
                        "Plan structure changed; waiting to resynchronize";
                }
                return;
            }
            pending.value = planToJson(state.plan);
            pending.sceneIndex = state.active;
            pending.prompt = promptValueToText(
                state.plan.shots[state.active]?.prompt,
            );
            state.lastValue = pending.value;
            state.planWidget.value = pending.value;
        }
        if (typeof state.planWidget.callback === "function") {
            state.planWidget.callback(pending.value);
        } else {
            state.planNode._h3ChainEditorRefresh?.();
        }
        state.planNode.graph?.setDirtyCanvas?.(true, true);
        publishCompanionPrompt(
            node, state.planNode, pending.sceneIndex, pending.prompt,
        );
        if (state.status) state.status.textContent = pending.message;
        dirty();
    }

    function schedulePlanEffects(pending) {
        if (state.disposed) return;
        state.planSyncPending = pending;
        if (state.planSyncTimer != null) {
            window.clearTimeout(state.planSyncTimer);
        }
        state.planSyncTimer = window.setTimeout(
            flushPlanEffects, PROMPT_SYNC_DELAY_MS,
        );
        if (state.status) state.status.textContent = "Editing…";
    }

    function schedulePromptAnalysis() {
        if (state.disposed) return;
        if (state.analysisTimer != null) {
            window.clearTimeout(state.analysisTimer);
        }
        state.analysisTimer = window.setTimeout(() => {
            state.analysisTimer = null;
            state.schema?.refresh();
        }, PROMPT_ANALYSIS_DELAY_MS);
    }

    function flushPromptAnalysis() {
        if (state.analysisTimer == null) return;
        window.clearTimeout(state.analysisTimer);
        state.analysisTimer = null;
        state.schema?.refresh();
    }

    function writePlan(
        message = "Saved to connected Plan", {deferEffects = false} = {},
    ) {
        if (!state.plan || !state.planWidget || !state.planNode) return;
        // Rich Editor owns only the selected prompt. Rebase when another UI
        // changed the live Plan; consecutive keystrokes already share the
        // latest Plan value and should not repeatedly parse the full JSON.
        const liveValue = String(state.planWidget.value ?? "");
        if (liveValue !== state.lastValue && !rebaseActivePromptOntoLivePlan()) {
            if (state.status) state.status.textContent = "Plan structure changed; waiting to resynchronize";
            return;
        }
        const value = planToJson(state.plan);
        state.lastValue = value;
        state.planWidget.value = value;
        const pending = {
            value,
            sceneIndex:state.active,
            prompt:promptValueToText(state.plan.shots[state.active]?.prompt),
            message,
        };
        if (deferEffects) schedulePlanEffects(pending);
        else {
            state.planSyncPending = pending;
            flushPlanEffects();
        }
    }

    function historySceneKey(runName, shotId) {
        return `${runName}\u0000${shotId}`;
    }

    function promptUndoForScene(shotId, text, {external = false} = {}) {
        const key = historySceneKey(planRunName(), shotId);
        let history = state.undoByScene.get(key);
        if (!history) {
            history = new PromptUndoHistory(text);
            state.undoByScene.set(key, history);
        } else if (external) {
            // Plan Studio republishes the active prompt after non-prompt edits.
            // Preserve undo when that synchronized text did not actually change.
            history.align(text);
        } else {
            history.align(text);
        }
        return history;
    }

    function recordPromptReplacement(sceneIndex, shot, text) {
        const shotId = String(
            shot?.id || `clip_${String(sceneIndex + 1).padStart(4, "0")}`,
        );
        const current = promptValueToText(
            shot?.prompt, `Scene ${sceneIndex + 1} prompt`,
        );
        const history = promptUndoForScene(shotId, current);
        history.record(text, {inputType:"insertReplacementText"});
        if (sceneIndex === state.active) state.promptUndo = history;
        return history;
    }

    async function historyRequest(query = {}, body = null) {
        const suffix = new URLSearchParams(query).toString();
        const response = await api.fetchApi(`/minimax_h3_context_loop/prompt-history${suffix ? `?${suffix}` : ""}`,
            body == null ? undefined : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
        let payload = {};
        try { payload = await response.json(); } catch (_error) { /* proxy error */ }
        if (!response.ok) throw new Error(payload.error || `Prompt history request failed (HTTP ${response.status}).`);
        return payload;
    }

    function renderHistory() {
        const history = state.history;
        if (!history.host) return;
        history.host.replaceChildren();
        if (history.error) {
            history.host.append(element("span", "h3rp-history-meta h3rp-status-error", history.error));
            return;
        }
        if (!history.data) {
            history.host.append(element("span", "h3rp-history-meta", "Loading versions…"));
            return;
        }
        const navigation = promptRevisionNavigation(history.data, history.revisionId);
        const controls = element("span", "h3rp-history-nav");
        const previous = button("‹", "Activate previous prompt version in the Plan", () => navigation.previous && void selectHistoryRevision(navigation.previous.id));
        const count = element("span", "h3rp-history-count", `Active ${navigation.position} / ${navigation.total}`);
        const next = button("›", "Activate next prompt version in the Plan", () => navigation.next && void selectHistoryRevision(navigation.next.id));
        previous.disabled = !navigation.previous;
        next.disabled = !navigation.next;
        controls.append(previous, count, next);
        const treeDetails = renderHistoryTree();
        const metadata = element("span", "h3rp-history-meta", promptRevisionLabel(navigation));
        metadata.title = promptRevisionHelp(navigation);
        history.host.append(controls, treeDetails, metadata);
    }

    function renderHistoryTree() {
        const history = state.history;
        const details = element("details", "h3rp-history-tree");
        details.open = history.treeOpen;
        const tree = promptRevisionTree(history.data, {includeArchived:history.showArchived});
        const summary = element("summary", "", `History (${tree.rows.length})`);
        summary.title = "Show prompt ancestry, labels, and revision actions";
        const panel = element("div", "h3rp-history-tree-panel");
        for (const row of tree.rows) {
            const rowHost = element("div",
                `h3rp-history-tree-row${row.isActive ? " h3rp-history-active" : ""}${row.isArchived ? " h3rp-history-archived" : ""}`);
            rowHost.style.paddingLeft = `${row.depth * 15}px`;
            const select = button("", `Activate ${row.displayLabel} in the Plan`, () => {
                if (!row.isActive) void selectHistoryRevision(row.revision.id);
            });
            select.className = "h3rp-history-tree-select";
            select.disabled = row.isActive;
            select.append(
                element("span", "h3rp-history-branch", row.depth ? "↳" : "●"),
                element("span", "h3rp-history-tree-label", row.displayLabel),
                element("span", "h3rp-history-tree-badge",
                    row.isArchived ? "Archived"
                        : row.isActive ? "Active"
                            : row.isExecuted ? "Executed" : "Draft"),
            );
            const rename = button("Label", `Label ${row.displayLabel}`, () => {
                const label = window.prompt(
                    "Short label for this prompt revision (leave blank to clear):",
                    String(row.revision.label ?? ""),
                );
                if (label != null) void mutateHistoryRevision("label", row.revision.id, {label});
            });
            rename.className = "h3rp-history-tree-action";
            rowHost.append(select, rename);
            if (row.canRestore) {
                const restore = button("Restore", `Restore ${row.displayLabel} to visible history`, () => {
                    void mutateHistoryRevision("archive", row.revision.id, {archived:false});
                });
                restore.className = "h3rp-history-tree-action";
                rowHost.append(restore);
            } else if (row.canArchive) {
                const archive = button("Archive", `Archive ${row.displayLabel}`, () => {
                    void mutateHistoryRevision("archive", row.revision.id, {archived:true});
                });
                archive.className = "h3rp-history-tree-action";
                rowHost.append(archive);
            }
            if (row.canDelete) {
                const remove = button("Delete", `Delete unexecuted leaf ${row.displayLabel}`, () => {
                    if (window.confirm(`Delete ${row.displayLabel}? This cannot be undone.`)) {
                        void mutateHistoryRevision("delete", row.revision.id);
                    }
                });
                remove.className = "h3rp-history-tree-action";
                rowHost.append(remove);
            }
            if (String(row.revision.basic_prompt ?? "").trim()) {
                const original = element(
                    "div", "h3rp-history-tree-basic-prompt",
                    `Original: ${row.revision.basic_prompt}`,
                );
                original.title = "The plain-language basic prompt that produced this revision.";
                rowHost.append(original);
            }
            panel.append(rowHost);
        }
        if (!tree.rows.length) panel.append(element("div", "h3rp-history-tree-tools", "No visible revisions"));
        const tools = element("div", "h3rp-history-tree-tools");
        tools.append(element("span", "", "↳ shows parent → child progression"));
        if (tree.archivedCount) {
            tools.append(button(
                history.showArchived ? "Hide archived" : `Show archived (${tree.archivedCount})`,
                "Toggle archived prompt revisions",
                () => {
                    history.showArchived = !history.showArchived;
                    history.treeOpen = true;
                    renderHistory();
                },
            ));
        }
        panel.append(tools);
        details.append(summary, panel);
        details.addEventListener("toggle", () => { history.treeOpen = details.open; });
        return details;
    }

    async function mutateHistoryRevision(action, revisionId, fields = {}) {
        await flushHistoryDraft();
        const history = state.history;
        const shot = state.plan?.shots?.[state.active];
        if (!shot) return;
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const runName = planRunName();
        const key = historySceneKey(runName, shotId);
        try {
            const payload = await historyRequest({}, {
                action, run_name:runName, scene_id:shotId, revision:revisionId, ...fields,
            });
            if (history.sceneKey !== key) return;
            history.data = payload.history;
            history.revisionId = payload.history?.active_revision ?? history.revisionId;
            history.error = "";
            renderHistory();
        } catch (error) {
            if (history.sceneKey !== key) return;
            history.error = error?.message || String(error);
            renderHistory();
        }
    }

    async function loadHistory(shotId, prompt, synchronize = true) {
        const runName = planRunName();
        const history = state.history;
        const key = historySceneKey(runName, shotId);
        const token = ++history.loadToken;
        history.sceneKey = key;
        history.data = null;
        history.revisionId = null;
        history.error = "";
        renderHistory();
        if (!runName) {
            history.error = "Set run_name for prompt history.";
            renderHistory();
            return;
        }
        const request = synchronize
            ? historyRequest({}, {action:"save", run_name:runName, scene_id:shotId, prompt, parent_revision:null})
            : historyRequest({run_name:runName, scene_id:shotId});
        history.loadPromise = request;
        try {
            const payload = await request;
            if (token !== history.loadToken || history.sceneKey !== key) return;
            history.data = payload.history ?? payload;
            history.revisionId = payload.revision?.id ?? history.data.active_revision ?? null;
        } catch (error) {
            if (token === history.loadToken && history.sceneKey === key) history.error = error?.message || String(error);
        } finally {
            if (history.loadPromise === request) history.loadPromise = null;
            if (token === history.loadToken && history.sceneKey === key) renderHistory();
        }
    }

    function scheduleHistoryDraft(shotId, prompt, basicPrompt = undefined) {
        if (state.disposed) return;
        const runName = planRunName();
        if (!runName) return;
        const history = state.history;
        history.pendingDraft = {
            key:historySceneKey(runName, shotId), runName, shotId, prompt, basicPrompt,
        };
        if (history.saveTimer != null) window.clearTimeout(history.saveTimer);
        history.saveTimer = window.setTimeout(() => { history.saveTimer = null; void flushHistoryDraft(); }, 650);
    }

    async function flushHistoryDraft() {
        const history = state.history;
        if (history.saveTimer != null) window.clearTimeout(history.saveTimer), history.saveTimer = null;
        if (history.savePromise) {
            await history.savePromise;
            return history.pendingDraft ? flushHistoryDraft() : undefined;
        }
        const draft = history.pendingDraft;
        if (!draft) return;
        history.pendingDraft = null;
        if (history.loadPromise && history.sceneKey === draft.key) await history.loadPromise;
        const parent = history.sceneKey === draft.key ? history.revisionId : null;
        const request = historyRequest({}, {action:"save", run_name:draft.runName, scene_id:draft.shotId,
            prompt:draft.prompt, parent_revision:parent, basic_prompt:draft.basicPrompt});
        history.savePromise = request;
        try {
            const payload = await request;
            if (history.sceneKey === draft.key) {
                history.data = payload.history;
                history.revisionId = payload.revision?.id ?? payload.history?.active_revision;
                history.error = "";
                renderHistory();
            }
        } catch (error) {
            if (history.sceneKey === draft.key) history.error = error?.message || String(error), renderHistory();
        } finally {
            if (history.savePromise === request) history.savePromise = null;
        }
        if (history.pendingDraft) await flushHistoryDraft();
    }

    async function selectHistoryRevision(revisionId) {
        await flushHistoryDraft();
        const shot = state.plan?.shots?.[state.active];
        if (!shot || !state.editor) return;
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const key = historySceneKey(planRunName(), shotId);
        try {
            const payload = await historyRequest({}, {action:"activate", run_name:planRunName(), scene_id:shotId, revision:revisionId});
            if (state.history.sceneKey !== key) return;
            state.history.data = payload.history;
            state.history.revisionId = payload.revision.id;
            const text = String(payload.revision.prompt ?? "");
            recordPromptReplacement(state.active, shot, text);
            shot.prompt = promptTextToLines(text);
            if (typeof payload.revision.basic_prompt === "string") {
                shot.basic_prompt = payload.revision.basic_prompt;
                const basicPromptTextarea = root.querySelector(".h3rp-basic-prompt");
                if (basicPromptTextarea) basicPromptTextarea.value = shot.basic_prompt;
            }
            renderEditorText(text);
            writePlan("Loaded prompt version");
            renderHistory();
            state.editor.focus();
        } catch (error) {
            if (state.history.sceneKey === key) state.history.error = error?.message || String(error), renderHistory();
        }
    }

    function ensurePopover() {
        if (state.popover) return state.popover;
        const popover = element("div", "h3rp-popover");
        popover.hidden = true;
        popover.addEventListener("mouseenter", () => {
            if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer), state.popoverTimer = null;
        });
        popover.addEventListener("mouseleave", scheduleHidePopover);
        for (const eventName of [
            "pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick",
            "keydown", "keyup", "keypress", "wheel",
        ]) {
            popover.addEventListener(eventName, (event) => event.stopPropagation());
        }
        document.body.append(popover);
        state.popover = popover;
        return popover;
    }

    function hidePopover(force = false) {
        if (state.popoverPinned && !force) return;
        state.popoverPinned = false;
        if (!state.popover) return;
        for (const media of state.popover.querySelectorAll("audio,video")) media.pause?.();
        state.popover.hidden = true;
    }

    function scheduleHidePopover() {
        if (state.popoverPinned) return;
        if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer);
        state.popoverTimer = window.setTimeout(() => { state.popoverTimer = null; hidePopover(); }, 220);
    }

    function positionPopover(popover, anchor) {
        popover.hidden = false;
        const rect = anchor.getBoundingClientRect();
        const width = Math.min(360, globalThis.innerWidth - 24);
        const left = Math.max(12, Math.min(globalThis.innerWidth - width - 12, rect.left));
        popover.style.left = `${left}px`;
        popover.style.top = `${Math.max(12, Math.min(
            globalThis.innerHeight - popover.offsetHeight - 12, rect.bottom + 7,
        ))}px`;
    }

    function showPopover(record, anchor) {
        if (!record || state.popoverPinned) return;
        if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer), state.popoverTimer = null;
        const popover = ensurePopover();
        popover.replaceChildren();
        const syntax = selectedReferenceSyntax(record);
        const displayToken = syntax === "semantic"
            ? taggedPictureReferenceToken(record.tag, "semantic")
            : record.token;
        const title = (state.referenceMode === "scheduled" || state.referenceMode === "tagged") && record.label
            ? `${displayToken} → ${record.label}` : displayToken;
        popover.append(element("div", "h3rp-popover-title", title));
        const mediaKind = record.kind === "picture" ? "image" : record.kind;
        const media = referenceMediaPreview(record, mediaKind);
        if (media.url) {
            const mediaElement = element(mediaKind === "image" ? "img" : mediaKind);
            mediaElement.className = "h3rp-popover-media";
            mediaElement.src = media.url;
            if (mediaKind !== "image") mediaElement.controls = true, mediaElement.preload = "metadata";
            else mediaElement.alt = `Preview for ${record.token}`;
            popover.append(mediaElement);
        } else {
            popover.append(element("div", "h3rp-muted", "No browser-playable file preview was found upstream."));
        }
        const sourceTitle = media.source?.title || nodeType(media.source) || "unresolved source";
        popover.append(element("div", "h3rp-popover-detail",
            `${record.kind.toUpperCase()} · ${record.selector === "prompt tag" ? "activated by prompt tag" : `scenes ${record.selector}`}\n${record.active ? "Active" : "Inactive"} in scene ${state.active + 1}\nSource: ${sourceTitle}`));
        positionPopover(popover, anchor);
    }

    function recordSupportsNative(record) {
        return record?.nativeToken !== null
            && Boolean(String(record?.nativeToken ?? record?.token ?? ""));
    }

    function recordSupportsSemantic(record) {
        return record?.kind === "picture" && Boolean(record?.tag)
            && Boolean(record?.supportsSemantic || record?.semanticOnly);
    }

    function inlineReferenceCandidates(part) {
        return state.records.filter((record) => {
            if (state.referenceMode !== "tagged" && !record.active) return false;
            return part.kind === "unknown" || record.kind === part.kind;
        });
    }

    function showReferenceEditor(part, start, end, anchor) {
        const candidates = inlineReferenceCandidates(part);
        if (!candidates.length) return;
        hidePopover(true);
        state.popoverPinned = true;
        const popover = ensurePopover();
        const editor = element("div", "h3rp-popover-editor");
        editor.append(element("div", "h3rp-popover-title", "Replace reference"));

        const referenceField = element("label", "h3rp-popover-field");
        referenceField.append(element("span", "", "Reference"));
        const referenceSelect = element("select");
        for (let index = 0; index < candidates.length; index += 1) {
            const record = candidates[index];
            const option = element("option", "", record.token);
            option.value = String(index);
            if (record.label && record.label !== record.token) {
                option.textContent = `${record.token} → ${record.label}`;
            }
            referenceSelect.append(option);
        }
        const currentIndex = Math.max(0, candidates.findIndex(
            (record) => record === part.record
                || (record.tag && record.tag === part.record?.tag)
                || record.token === part.record?.token,
        ));
        referenceSelect.value = String(currentIndex);
        referenceField.append(referenceSelect);

        const modeField = element("label", "h3rp-popover-field");
        modeField.append(element("span", "", "Syntax"));
        const modeSelect = element("select");
        modeField.append(modeSelect);

        const timestampField = element("label", "h3rp-popover-field");
        timestampField.append(element("span", "", "Time (sec)"));
        const timestampInput = element("input");
        timestampInput.type = "number";
        timestampInput.min = "0";
        timestampInput.step = "0.01";
        timestampInput.placeholder = "blank = untimed";
        timestampInput.value = part.timestamp == null
            ? "" : String(part.timestamp);
        timestampField.append(timestampInput);

        const updateModes = (preferred = modeSelect.value) => {
            const record = candidates[Number(referenceSelect.value)];
            const native = recordSupportsNative(record);
            const semantic = recordSupportsSemantic(record);
            modeSelect.replaceChildren();
            if (native) {
                const option = element("option", "", "@ native");
                option.value = "native";
                modeSelect.append(option);
            }
            if (semantic) {
                const option = element("option", "", "# semantic (time optional)");
                option.value = "semantic";
                modeSelect.append(option);
            }
            modeSelect.value = [...modeSelect.options].some(
                (option) => option.value === preferred,
            ) ? preferred : semantic && !native ? "semantic" : "native";
            timestampField.hidden = modeSelect.value !== "semantic";
            modeField.hidden = modeSelect.options.length < 2;
        };
        referenceSelect.addEventListener("change", () => updateModes());
        modeSelect.addEventListener("change", () => {
            timestampField.hidden = modeSelect.value !== "semantic";
        });
        updateModes(part.semantic ? "semantic" : "native");
        editor.append(referenceField, modeField, timestampField);

        const actions = element("div", "h3rp-popover-actions");
        const cancel = element("button", "", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", () => hidePopover(true));
        const apply = element("button", "", "Apply");
        apply.type = "button";
        apply.addEventListener("click", () => {
            const record = candidates[Number(referenceSelect.value)];
            const mode = modeSelect.value;
            const timestampText = timestampInput.value.trim();
            const timestamp = timestampText === ""
                ? null : Number(timestampText);
            const current = editorPlainText(state.editor);
            if (current.slice(start, end) !== part.text) {
                hidePopover(true);
                renderEditorText(current);
                return;
            }
            const next = replacePromptReferenceOccurrence(
                current, start, end, record, mode, timestamp,
            );
            const replacement = referenceReplacementToken(
                record, mode, timestamp,
            );
            hidePopover(true);
            if (!state.editor || !replacement || next === current) return;
            renderEditorText(next, start + replacement.length);
            state.editor.dispatchEvent(new InputEvent("input", {
                bubbles:true, inputType:"insertReplacementText",
            }));
            const referenceData = availableReferenceRecords(
                node, state.active + 1, {
                    includeInactive:true,
                    prompt:[sharedPrompt(state.plan).text.trim(), next.trim()]
                        .filter(Boolean).join("\n\n"),
                },
            );
            state.records = referenceData.records;
            state.referenceMode = referenceData.mode;
            renderEditorText(next, start + replacement.length);
            renderReferenceTray();
            state.editor.focus();
        });
        actions.append(cancel, apply);
        editor.append(actions);
        editor.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                event.preventDefault();
                cancel.click();
            } else if (event.key === "Enter") {
                event.preventDefault();
                apply.click();
            }
        });
        popover.replaceChildren(editor);
        positionPopover(popover, anchor);
        referenceSelect.focus();
    }

    function makeToken(part, start, end) {
        if (part.type === "text") return document.createTextNode(part.text);
        const kind = part.type === "reference" ? part.kind : part.type;
        const token = element("span", `h3rp-token h3rp-token-${kind}`);
        token.contentEditable = "false";
        token.dataset.token = part.text;
        if (["subject", "speaker", "dialogue", "flow"].includes(part.type)) {
            token.dataset.h3PromptMarker = "";
            token.tabIndex = 0;
            token.setAttribute("role", "button");
            token.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                event.stopPropagation();
                token.click();
            });
        }
        if (part.unresolved) token.classList.add("h3rp-token-unknown");
        if (part.record && !part.record.active) token.classList.add("h3rp-token-inactive");
        if (part.type === "section") {
            token.append(element("span", "h3rp-token-label", part.text));
            token.title = `H3 section · ${part.section}`;
            return token;
        }
        const mediaKind = kind === "picture" ? "image" : kind;
        const media = part.record
            ? referenceMediaPreview(part.record, mediaKind) : null;
        if (kind === "picture" && media?.url) {
            const thumb = element("img", "h3rp-token-thumb");
            thumb.src = media.url;
            thumb.alt = "";
            token.append(thumb);
        } else {
            token.append(icon(kind));
        }
        token.append(element("span", "h3rp-token-label", part.text));
        if (part.record) {
            token.title = part.type === "reference"
                ? `${part.text} · click to replace or edit; hover for preview`
                : `${part.text} · hover for ${kind} preview`;
            token.addEventListener("mouseenter", () => showPopover(part.record, token));
            token.addEventListener("mouseleave", scheduleHidePopover);
            token.addEventListener("focus", () => showPopover(part.record, token));
        } else {
            token.title = part.type === "reference"
                ? "Unresolved reference in this scene · click to replace"
                : token.dataset.h3PromptMarker !== undefined
                  ? "Click to replace or remove this prompt token" : part.text;
        }
        if (part.type === "reference") {
            token.tabIndex = 0;
            token.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                showReferenceEditor(part, start, end, token);
            });
            token.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                showReferenceEditor(part, start, end, token);
            });
        }
        return token;
    }

    function renderEditorText(text, caret = null, editableSelection = null) {
        if (!state.editor) return;
        const scrollTop = state.editor.scrollTop;
        const scrollLeft = state.editor.scrollLeft;
        const source = String(text ?? "");
        if (!state.decorated) {
            state.editor.replaceChildren(document.createTextNode(source));
            if (editableSelection?.selectionStart != null
                    && editableSelection?.selectionEnd != null) {
                restoreTextSelection(
                    state.editor,
                    editableSelection.selectionStart,
                    editableSelection.selectionEnd,
                );
            } else if (caret != null) restoreCaret(state.editor, caret);
            state.editor.scrollTop = scrollTop;
            state.editor.scrollLeft = scrollLeft;
            return;
        }
        const parts = tokenizeRichPrompt(source, state.records);
        const fragment = document.createDocumentFragment();
        let sourceOffset = 0;
        for (let index = 0; index < parts.length; index += 1) {
            const part = parts[index];
            const partStart = sourceOffset;
            const partEnd = partStart + part.text.length;
            sourceOffset = partEnd;
            // Leave an unfinished unknown @word as normal text while the user
            // is still typing it; it becomes a red unresolved chip on blur or
            // another explicit decoration pass.
            const unfinished = part.type === "reference" && part.unresolved
                && part.text.startsWith("@") && index === parts.length - 1
                && document.activeElement === state.editor;
            const editingSemanticTime = part.type === "reference" && part.semantic
                && editableSelection?.selectionStart >= partStart
                && editableSelection?.selectionEnd <= partEnd;
            fragment.append(unfinished || editingSemanticTime
                ? document.createTextNode(part.text)
                : makeToken(part, partStart, partEnd));
        }
        state.editor.replaceChildren(fragment);
        if (editableSelection?.selectionStart != null
                && editableSelection?.selectionEnd != null) {
            restoreTextSelection(
                state.editor,
                editableSelection.selectionStart,
                editableSelection.selectionEnd,
            );
        } else if (caret != null) restoreCaret(state.editor, caret);
        // Content decoration rebuilds the children and otherwise resets the
        // editor viewport. Keep long prompts at the line the user was editing.
        state.editor.scrollTop = scrollTop;
        state.editor.scrollLeft = scrollLeft;
    }

    function saveEditorInput(event) {
        const shot = state.plan?.shots?.[state.active];
        if (!shot || !state.editor) return;
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const text = editorPlainText(state.editor);
        state.promptUndo?.record(text, {inputType:event?.inputType});
        shot.prompt = promptTextToLines(text);
        writePlan("Saved to connected Plan", {deferEffects:true});
        scheduleHistoryDraft(shotId, text);
        // Keep the browser's live DOM intact while the user types. Existing
        // chips remain atomic; newly typed raw labels are decorated on blur.
        // The shared text-level undo survives later tag decoration and
        // programmatic toolbar/menu insertions.
    }

    function rememberPromptSelection() {
        const selected = state.editor ? editorSelectionOffsets(state.editor) : null;
        if (selected) state.promptSelection = selected;
    }

    function insertDecoratedText(text, selectSemanticTimestamp = false) {
        const inserted = String(text ?? "");
        selectSemanticTimestamp = Boolean(
            selectSemanticTimestamp && inserted.includes("[")
            && inserted.includes("s]"));
        const current = editorPlainText(state.editor);
        const live = document.activeElement === state.editor
            ? editorSelectionOffsets(state.editor) : null;
        const selected = live ?? state.promptSelection ?? {
            start:current.length, end:current.length,
        };
        const insertionStart = Math.max(
            0, Math.min(current.length, Number(selected.start) || 0),
        );
        const insertionEnd = Math.max(
            insertionStart,
            Math.min(current.length, Number(selected.end) || insertionStart),
        );
        const next = current.slice(0, insertionStart)
            + inserted + current.slice(insertionEnd);
        if (state.referenceMode === "tagged") {
            state.records = availableReferenceRecords(
                node, state.active + 1, {
                    includeInactive: true,
                    prompt: [
                        sharedPrompt(state.plan).text.trim(),
                        next.trim(),
                    ].filter(Boolean).join("\n\n"),
                },
            ).records;
        }
        if (selectSemanticTimestamp) {
            const first = inserted.indexOf("[") + 1;
            const last = inserted.lastIndexOf("s]");
            renderEditorText(next, null, {
                selectionStart: insertionStart + first,
                selectionEnd: insertionStart + last,
            });
            state.promptSelection = {
                start:insertionStart + first,
                end:insertionStart + last,
            };
        } else {
            const caret = insertionStart + inserted.length;
            renderEditorText(next, caret);
            state.promptSelection = {start:caret, end:caret};
        }
        state.editor.dispatchEvent(new InputEvent("input", {
            bubbles:true, inputType:"insertText",
        }));
        state.editor.focus();
    }

    function referenceSyntaxKey(record) {
        const shot = state.plan?.shots?.[state.active];
        return `${String(shot?.id ?? state.active)}:${record.tag}`;
    }

    function selectedReferenceSyntax(record) {
        if (state.referenceMode !== "tagged") return "native";
        if (record.semanticOnly) return "semantic";
        if (!record.supportsSemantic) return "native";
        const promptMode = taggedPictureReferenceMode(
            editorPlainText(state.editor), record.tag,
        );
        if (promptMode === "native" || promptMode === "semantic") {
            return promptMode;
        }
        return state.referenceSyntax.get(referenceSyntaxKey(record)) ?? "native";
    }

    function refreshReferenceState(prompt = editorPlainText(state.editor)) {
        if (!state.plan?.shots?.length) {
            state.records = [];
            state.referenceMode = null;
            return {wrapper:null, mode:null, records:[]};
        }
        const referenceData = availableReferenceRecords(
            node, state.active + 1, {
                includeInactive:true,
                prompt:[
                    sharedPrompt(state.plan).text.trim(),
                    String(prompt ?? "").trim(),
                ].filter(Boolean).join("\n\n"),
            },
        );
        state.records = referenceData.records;
        state.referenceMode = referenceData.mode;
        return referenceData;
    }

    function convertReferenceSyntax(record, mode) {
        state.referenceSyntax.set(referenceSyntaxKey(record), mode);
        const current = editorPlainText(state.editor);
        const next = convertTaggedPictureReference(current, record.tag, mode);
        if (next !== current) {
            renderEditorText(next, next.length);
            state.editor.dispatchEvent(new InputEvent("input", {
                bubbles:true, inputType:"insertReplacementText",
            }));
            state.records = availableReferenceRecords(
                node, state.active + 1, {
                    includeInactive:true,
                    prompt:[sharedPrompt(state.plan).text.trim(), next.trim()]
                        .filter(Boolean).join("\n\n"),
                },
            ).records;
        }
        renderReferenceTray();
    }

    function insertReference(record) {
        const mode = selectedReferenceSyntax(record);
        const token = mode === "semantic"
            ? taggedPictureReferenceToken(record.tag, "semantic")
            : record.token;
        insertDecoratedText(token, mode === "semantic");
        state.refs?.classList.remove("h3rp-open");
    }

    function renderReferenceTray() {
        const tray = state.refs;
        if (!tray) return;
        refreshReferenceState(editorPlainText(state.editor));
        tray.replaceChildren();
        if (!state.records.length) {
            tray.append(element("div", "h3rp-ref-help", "No connected Tagged/Scheduled Ref2VA, core Ref2VA, or core I2V/FL2V references were found."));
            return;
        }
        tray.append(element("div", "h3rp-ref-help",
            state.referenceMode === "tagged"
                ? "Picture previews can switch and convert between native @tag and Qwen-only #tag; add [time] for explicit placement. Video and audio stay native-only. Hover for a media preview; audio never autoplays."
                : `Scene ${state.active + 1}: click an active reference to insert it. Hover for image, video, or audio preview. Audio never autoplays.`));
        for (const record of state.records) {
            const insertable = record.active || state.referenceMode === "tagged";
            const mode = selectedReferenceSyntax(record);
            const displayToken = mode === "semantic"
                ? taggedPictureReferenceToken(record.tag, "semantic")
                : record.token;
            const shell = element("div", "h3rp-ref-card-shell");
            const card = button("", insertable ? `Insert ${displayToken}` : `${record.token} is inactive in this scene`, () => {
                if (!insertable) return;
                insertReference(record);
            });
            card.classList.add("h3rp-ref-card");
            if (!record.active) card.classList.add("h3rp-inactive");
            const mediaKind = record.kind === "picture" ? "image" : record.kind;
            const media = referenceMediaPreview(record, mediaKind);
            if (record.kind === "picture" && media.url) {
                const thumb = element("img", "h3rp-token-thumb");
                thumb.src = media.url;
                thumb.alt = "";
                card.append(thumb);
            } else card.append(icon(record.kind));
            const copy = element("span", "h3rp-ref-card-copy");
            copy.append(
                element("div", "h3rp-ref-card-title", displayToken),
                element("div", "h3rp-ref-card-detail", record.label && record.label !== record.token
                    ? `${record.label} · ${record.selector === "prompt tag" ? "prompt activated" : `scenes ${record.selector}`}`
                    : `${record.kind} · ${record.selector === "prompt tag" ? "insert to activate" : `scenes ${record.selector}`}`),
            );
            card.append(copy);
            card.addEventListener("mouseenter", () => showPopover(record, card));
            card.addEventListener("mouseleave", scheduleHidePopover);
            shell.append(card);
            if (state.referenceMode === "tagged" && record.supportsSemantic) {
                const usedMode = taggedPictureReferenceMode(
                    editorPlainText(state.editor), record.tag,
                );
                const modes = element("div", "h3rp-ref-mode");
                const native = button("@", "Use native @tag; convert semantic anchors already in this scene", () => {
                    convertReferenceSyntax(record, "native");
                });
                const semantic = button("#", "Use untimed Qwen-only #tag; add [time] for placement", () => {
                    convertReferenceSyntax(record, "semantic");
                });
                native.classList.toggle(
                    "h3rp-selected", mode === "native" || usedMode === "mixed",
                );
                semantic.classList.toggle(
                    "h3rp-selected", mode === "semantic" || usedMode === "mixed",
                );
                modes.append(native, semantic);
                shell.append(modes);
            }
            tray.append(shell);
        }
    }

    function optimizerSceneKey(index = state.active) {
        const shot = state.plan?.shots?.[index];
        return `${index}:${String(shot?.id || `clip_${String(index + 1).padStart(4, "0")}`)}`;
    }

    function optimizerBusy() {
        return Boolean(state.optimizer.preparing || state.optimizer.requestId);
    }

    function optimizerProviders() {
        const advertised = Array.isArray(state.optimizer.providers)
            ? state.optimizer.providers.filter((item) => item && typeof item.id === "string") : [];
        return advertised;
    }

    function optimizerProviderLabel(id = promptOptimizerMcpProvider()) {
        return optimizerProviders().find((item) => item.id === id)?.label || id;
    }

    function refreshOptimizerUi() {
        const busy = optimizerBusy();
        if (busy) state.completion?.hide();
        if (state.editor) state.editor.contentEditable = busy ? "false" : "true";
        for (const control of root.querySelectorAll("[data-h3rp-lock]")) control.disabled = busy;
        const optimize = root.querySelector(".h3rp-optimize");
        if (optimize) {
            const backend = promptOptimizerBackend();
            optimize.disabled = busy || backend === "disabled";
            optimize.title = backend === "disabled"
                ? "Prompt optimization is disabled in ComfyUI Settings → MiniMax H3 Context Loop → Prompt optimizer."
                : `Optimize through ${backend === "mcp" ? `MCP agent (${promptOptimizerMcpProvider()})` : "the configured Direct API"}. The result becomes a reversible prompt revision.`;
        }
        const stop = root.querySelector(".h3rp-stop");
        if (stop) stop.hidden = !state.optimizer.requestId;
        const applyPending = root.querySelector(".h3rp-apply-pending");
        if (applyPending) applyPending.hidden = !state.optimizer.pendingResult;
        if (state.optimizerStatus) {
            state.optimizerStatus.className = `h3rp-status${state.optimizer.error ? " h3rp-status-error" : state.optimizer.message ? " h3rp-status-success" : ""}`;
            state.optimizerStatus.textContent = state.optimizer.error || state.optimizer.message || (busy ? "Optimizing…" : "");
        }
    }

    function handleOptimizerFrame(frame) {
        if (!frame || typeof frame !== "object") return;
        if (frame.type === "prompt_assist_ready") {
            state.optimizer.providers = Array.isArray(frame.providers) ? frame.providers : null;
            return;
        }
        // A previously connected bridge may still emit a late frame while a
        // Direct API request is active. Never let that frame cancel or replace
        // the direct result.
        if (state.optimizer.activeBackend !== "mcp") return;
        if (frame.type === "prompt_assist_started" && frame.request_id === state.optimizer.requestId) {
            state.optimizer.message = `Optimizing with ${optimizerProviderLabel()}…`;
        } else if (frame.type === "prompt_assist_progress" && frame.request_id === state.optimizer.requestId) {
            state.optimizer.message = "Agent is drafting…";
        } else if (frame.type === "prompt_assist_result" && frame.request_id === state.optimizer.requestId) {
            const meta = state.optimizer.meta;
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.activeBackend = null;
            const result = typeof frame.rewritten_prompt === "string" ? frame.rewritten_prompt : null;
            if (!result?.trim()) {
                state.optimizer.error = "The optimizer returned no replacement prompt.";
            } else {
                const shot = state.plan?.shots?.[meta.sceneIndex];
                const current = shot ? promptValueToText(shot.prompt) : "";
                if (!shot || String(shot.id || "") !== meta.sceneId || current !== meta.currentAtRequest) {
                    state.optimizer.pendingResult = {...meta, result};
                    state.optimizer.error = "The scene changed while optimizing; the result was not applied.";
                } else {
                    recordPromptReplacement(meta.sceneIndex, shot, result);
                    shot.prompt = promptTextToLines(result);
                    state.optimizer.origins.set(meta.sceneKey, {source:meta.source, result});
                    writePlan("Optimized prompt saved to Plan");
                    if (state.active === meta.sceneIndex) {
                        renderEditorText(result);
                        scheduleHistoryDraft(meta.sceneId, result, shot.basic_prompt);
                        void flushHistoryDraft();
                    }
                    state.optimizer.message = frame.message || "Optimized prompt saved as a new revision.";
                    state.optimizer.error = "";
                }
            }
        } else if (frame.type === "prompt_assist_error"
                && (!frame.request_id || frame.request_id === state.optimizer.requestId)) {
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.activeBackend = null;
            state.optimizer.error = String(frame.error || "Prompt optimization failed.");
        } else if (frame.type === "prompt_assist_cancelled" && frame.request_id === state.optimizer.requestId) {
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.activeBackend = null;
            state.optimizer.message = "Optimization stopped; the prompt was not changed.";
        } else if (frame.type === "prompt_assist_cancel_ack" && frame.cancelled === false
                && frame.request_id === state.optimizer.requestId) {
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.activeBackend = null;
            state.optimizer.error = "The optimizer request is no longer active.";
        }
        refreshOptimizerUi();
    }

    function applyPendingOptimizerResult() {
        const pending = state.optimizer.pendingResult;
        if (!pending) return;
        const shot = state.plan?.shots?.[pending.sceneIndex];
        if (!shot || String(shot.id || "") !== pending.sceneId) {
            state.optimizer.error = "The optimized result belongs to a scene that no longer exists.";
            state.optimizer.pendingResult = null;
            refreshOptimizerUi();
            return;
        }
        recordPromptReplacement(pending.sceneIndex, shot, pending.result);
        shot.prompt = promptTextToLines(pending.result);
        state.optimizer.origins.set(pending.sceneKey, {source:pending.source, result:pending.result});
        state.optimizer.pendingResult = null;
        state.optimizer.error = "";
        state.optimizer.message = "Changed source replaced explicitly; result saved as a new revision.";
        writePlan("Optimized prompt saved to Plan");
        if (state.active === pending.sceneIndex) {
            renderEditorText(pending.result);
            scheduleHistoryDraft(pending.sceneId, pending.result, shot.basic_prompt);
            void flushHistoryDraft();
        }
        refreshOptimizerUi();
    }

    function optimizerInstruction(mode, refs, basicPrompt = "") {
        const referenceSummary = refs.records.length
            ? refs.records.map((record) => {
                const mapping = record.label && record.label !== record.token
                    ? ` -> ${record.label}` : "";
                return `${record.token}${mapping} (${record.kind}, ${record.active ? "active" : "inactive"})`;
            }).join(", ")
            : "none discovered";
        const basic = String(basicPrompt ?? "").trim();
        const contentDirective = basic
            ? `Base the rewrite on this plain-language scene idea, expressed in the required H3 style: ${basic} `
            : "";
        return `${contentDirective}${richGuideInstruction(state.guide, mode)} Connected scene references: ${referenceSummary}.`;
    }

    function optimizerMeta(sceneIndex, sceneId, sceneKey, source, current) {
        return {sceneIndex, sceneId, sceneKey, source, currentAtRequest:current};
    }

    function optimizerResources(refs) {
        return refs.records.filter((record) => record.active).map((record) => ({
            type:record.kind === "picture" ? "image" : record.kind,
            tag:record.token,
            asset:findMediaAsset(
                record.source, record.kind === "picture" ? "image" : record.kind),
        })).filter((resource) => resource.asset);
    }

    function applyOptimizerResponse(result, message, meta) {
        if (!result?.trim()) {
            state.optimizer.error = "The optimizer returned no replacement prompt.";
            return;
        }
        const shot = state.plan?.shots?.[meta.sceneIndex];
        const current = shot ? promptValueToText(shot.prompt) : "";
        if (!shot || String(shot.id || "") !== meta.sceneId || current !== meta.currentAtRequest) {
            state.optimizer.pendingResult = {...meta, result};
            state.optimizer.error = "The scene changed while optimizing; the result was not applied.";
            return;
        }
        recordPromptReplacement(meta.sceneIndex, shot, result);
        shot.prompt = promptTextToLines(result);
        state.optimizer.origins.set(meta.sceneKey, {source:meta.source, result});
        writePlan("Optimized prompt saved to Plan");
        if (state.active === meta.sceneIndex) {
            renderEditorText(result);
            scheduleHistoryDraft(meta.sceneId, result, shot.basic_prompt);
            void flushHistoryDraft();
        }
        state.optimizer.message = message || "Optimized prompt saved as a new revision.";
        state.optimizer.error = "";
    }

    async function optimizeDirect(requestId, instruction, context, resources, meta, config) {
        const abortController = new AbortController();
        state.optimizer.abortController = abortController;
        state.optimizer.requestId = requestId;
        state.optimizer.preparing = false;
        state.optimizer.activeBackend = "direct";
        state.optimizer.meta = meta;
        state.optimizer.message = "Optimizing with Direct API…";
        refreshOptimizerUi();
        try {
            const body = makeDirectPromptOptimizeRequest({
                config, instruction, context, resources,
            });
            const response = await api.fetchApi("/minimax_h3_context_loop/prompt-optimize", {
                method:"POST", headers:{"Content-Type":"application/json"},
                body:JSON.stringify(body), signal:abortController.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || `Direct prompt optimizer failed (HTTP ${response.status}).`);
            if (state.optimizer.requestId !== requestId) return;
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.abortController = null;
            state.optimizer.activeBackend = null;
            applyOptimizerResponse(String(payload.prompt || ""), payload.message, meta);
        } catch (error) {
            if (state.optimizer.requestId !== requestId) return;
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.abortController = null;
            state.optimizer.activeBackend = null;
            if (error?.name === "AbortError") state.optimizer.message = "Optimization stopped; the prompt was not changed.";
            else state.optimizer.error = error?.message || String(error);
        }
        refreshOptimizerUi();
    }

    async function optimizeMcp(requestId, instruction, context, meta) {
        const provider = promptOptimizerMcpProvider();
        await state.optimizer.client.connect();
        const selected = state.optimizer.providers?.find((item) => item.id === provider);
        if (!selected) throw new Error(`${provider} is not supported by this comfyui-mcp prompt bridge.`);
        if (selected.available === false) {
            throw new Error(`${selected.label || provider} is unavailable${selected.reason ? `: ${selected.reason}` : "."}`);
        }
        state.optimizer.client.reset();
        const request = makePromptAssistRequest({
            requestId,
            conversationId:state.optimizer.client.conversationId,
            provider,
            mode:"rewrite",
            instruction,
            context,
        });
        state.optimizer.requestId = requestId;
        state.optimizer.preparing = false;
        state.optimizer.activeBackend = "mcp";
        state.optimizer.meta = meta;
        state.optimizer.message = `Optimizing with ${optimizerProviderLabel(provider)}…`;
        refreshOptimizerUi();
        await state.optimizer.client.send(request);
    }

    async function optimizePrompt() {
        if (optimizerBusy() || !state.plan?.shots?.length || !state.editor) return;
        const backend = promptOptimizerBackend();
        if (backend === "disabled") return;
        const directConfig = backend === "direct" ? promptOptimizerDirectConfig() : null;
        const configurationError = directConfig
            ? directOptimizerConfigurationError(directConfig) : "";
        if (configurationError) {
            state.optimizer.error = `${configurationError} Configure it in ComfyUI Settings → MiniMax H3 Context Loop → Prompt optimizer.`;
            state.optimizer.message = "";
            refreshOptimizerUi();
            void openPromptOptimizerSettings();
            return;
        }
        const sceneIndex = state.active;
        const shot = state.plan.shots[sceneIndex];
        const sceneId = String(shot.id || `clip_${String(sceneIndex + 1).padStart(4, "0")}`);
        const sceneKey = optimizerSceneKey(sceneIndex);
        const current = editorPlainText(state.editor);
        const source = optimizerSource(current, state.optimizer.origins.get(sceneKey));
        const refs = availableReferenceRecords(node, sceneIndex + 1, {
            includeInactive: true,
            prompt: [sharedPrompt(state.plan).text.trim(), source.trim()]
                .filter(Boolean).join("\n\n"),
        });
        const mode = richGenerationMode(refs.mode);
        const context = buildPromptAssistantContext(state.plan, sceneIndex, source, {
            includeShared:true, includeAdjacent:true,
        });
        context.generation_mode = mode;
        const requestId = `rich-${globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`;
        const instruction = optimizerInstruction(mode, refs, shot.basic_prompt);
        const meta = optimizerMeta(sceneIndex, sceneId, sceneKey, source, current);
        state.optimizer.error = "";
        state.optimizer.pendingResult = null;
        state.optimizer.message = backend === "mcp"
            ? "Connecting to the isolated prompt agent…" : "Preparing Direct API request…";
        state.optimizer.preparing = true;
        state.optimizer.activeBackend = backend;
        refreshOptimizerUi();
        try {
            if (backend === "direct") {
                await optimizeDirect(
                    requestId, instruction, context, optimizerResources(refs), meta, directConfig);
            } else {
                await optimizeMcp(requestId, instruction, context, meta);
            }
        } catch (error) {
            state.optimizer.preparing = false;
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.abortController = null;
            state.optimizer.activeBackend = null;
            state.optimizer.error = error?.message || String(error);
            refreshOptimizerUi();
        }
    }

    function stopOptimizer() {
        if (!state.optimizer.requestId) return;
        if (state.optimizer.activeBackend === "direct") {
            state.optimizer.requestId = null;
            state.optimizer.meta = null;
            state.optimizer.activeBackend = null;
            state.optimizer.abortController?.abort();
            state.optimizer.abortController = null;
            state.optimizer.message = "Optimization stopped; the prompt was not changed.";
        } else if (!state.optimizer.client.cancel(state.optimizer.requestId)) {
            state.optimizer.error = "The bridge is disconnected; the request could not be cancelled.";
        } else state.optimizer.message = "Stopping optimizer…";
        refreshOptimizerUi();
    }

    function showFailure(message) {
        state.completion?.destroy();
        state.completion = null;
        state.schema = null;
        state.editor = null;
        state.history.host = null;
        root.replaceChildren(
            element("div", "h3rp-title", "Rich Scene Prompt Editor"),
            element("div", "h3rp-error", message),
            element("div", "h3rp-context", "Connect the Plan output to this node's plan input."),
        );
    }

    function navigate(offset, absolute = null, {synchronize = true, focus = true} = {}) {
        if (!state.plan?.shots?.length || optimizerBusy()) return;
        flushPlanEffects();
        flushPromptAnalysis();
        const pending = state.sceneNavigation;
        const from = pending && pending.planNode === state.planNode
                && pending.runName === planRunName() ? pending.index : state.active;
        const requested = absolute == null ? from + offset : Number(absolute);
        if (!Number.isFinite(requested)) return;
        const index = Math.max(0, Math.min(state.plan.shots.length - 1, Math.trunc(requested)));
        const selection = {
            planNode:state.planNode, runName:planRunName(),
            sceneId:String(state.plan.shots[index]?.id ?? ""), index,
        };
        state.sceneNavigation = selection;
        void (async () => {
            await flushHistoryDraft();
            // History IO can complete out of order. Only the latest selection
            // may move this editor or broadcast a scene to its companions.
            if (state.disposed || state.sceneNavigation !== selection
                    || state.planNode !== selection.planNode
                    || planRunName() !== selection.runName) return;
            state.sceneNavigation = null;
            const target = selection.sceneId
                    && String(state.plan.shots[selection.index]?.id ?? "") !== selection.sceneId
                ? state.plan.shots.findIndex(shot => String(shot?.id ?? "") === selection.sceneId)
                : selection.index;
            if (!state.plan.shots[target]) return;
            state.active = target;
            persistView();
            render();
            if (synchronize) publishCompanionScene(node, state.planNode, state.active);
            if (focus) state.editor?.focus();
        })();
    }

    function render() {
        hidePopover(true);
        state.completion?.destroy();
        state.completion = null;
        state.schema = null;
        if (!state.plan?.shots?.length) return showFailure("The connected Plan has no scenes.");
        state.active = Math.max(0, Math.min(state.active, state.plan.shots.length - 1));
        root.style.setProperty("--h3rp-font-size", `${state.fontSize}px`);
        root.replaceChildren();
        const shot = state.plan.shots[state.active];
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const referenceData = availableReferenceRecords(
            node, state.active + 1, {
                includeInactive: true,
                prompt: [
                    sharedPrompt(state.plan).text.trim(),
                    promptValueToText(
                        shot.prompt, `Scene ${state.active + 1} prompt`).trim(),
                ].filter(Boolean).join("\n\n"),
            },
        );
        state.records = referenceData.records;
        state.referenceMode = referenceData.mode;

        const head = element("div", "h3rp-head");
        head.append(
            element("span", "h3rp-title", "Rich Scene Prompt Editor"),
            element("span", "h3rp-context", `${richGenerationMode(referenceData.mode)} · ${sharedPrompt(state.plan).text.trim() ? "shared prompt active" : "no shared prompt"}`),
        );

        const nav = element("div", "h3rp-nav");
        const previous = button("←", "Previous scene (Alt+Left)", () => navigate(-1));
        const next = button("→", "Next scene (Alt+Right)", () => navigate(1));
        previous.disabled = state.active === 0;
        next.disabled = state.active === state.plan.shots.length - 1;
        previous.dataset.h3rpLock = "";
        next.dataset.h3rpLock = "";
        const sceneSelect = element("select");
        sceneSelect.dataset.h3rpLock = "";
        for (let index = 0; index < state.plan.shots.length; index += 1) {
            const id = state.plan.shots[index].id || `clip_${String(index + 1).padStart(4, "0")}`;
            const option = element("option", "", `Scene ${index + 1} — ${id}`);
            option.value = String(index);
            sceneSelect.append(option);
        }
        sceneSelect.value = String(state.active);
        sceneSelect.addEventListener("change", () => navigate(0, sceneSelect.value));
        const smaller = button("A−", "Decrease prompt font", () => {
            state.fontSize = clamp(state.fontSize - 1, MIN_FONT, MAX_FONT, DEFAULT_FONT); persistView(); render();
        });
        const larger = button("A+", "Increase prompt font", () => {
            state.fontSize = clamp(state.fontSize + 1, MIN_FONT, MAX_FONT, DEFAULT_FONT); persistView(); render();
        });
        smaller.dataset.h3rpLock = "";
        larger.dataset.h3rpLock = "";
        nav.append(previous, sceneSelect, next, smaller, element("span", "h3rp-muted", `${state.fontSize}px`), larger);

        const toolbar = element("div", "h3rp-toolbar");
        const refsButton = button("References", "Show connected references, miniatures, and media previews", () => {
            state.completion?.hide();
            renderReferenceTray();
            state.refs.classList.toggle("h3rp-open");
        }, "reference");
        refsButton.addEventListener("pointerdown", rememberPromptSelection);
        refsButton.dataset.h3rpLock = "";
        const dialogue = button("Dialogue", "Wrap selected text in <d> tags", () => {
            const selection = globalThis.getSelection?.();
            const selected = selection?.rangeCount ? selection.toString() : "";
            insertDecoratedText(`<d>${selected}</d>`);
        }, "dialogue");
        dialogue.dataset.h3rpLock = "";
        const presentation = button(
            state.decorated ? "Rich text" : "Plain text",
            state.decorated
                ? "Show the same prompt as plain text"
                : "Color H3 sections, references, speakers, flow markers, and dialogue tags",
            () => {
                const text = editorPlainText(state.editor);
                const caret = selectionTextOffset(state.editor);
                state.decorated = !state.decorated;
                persistView();
                renderEditorText(text, caret);
                state.editor.focus();
            },
        );
        presentation.classList.toggle("h3rp-presentation-active", state.decorated);
        presentation.dataset.h3rpLock = "";
        const guide = element("select", "h3rp-guide");
        guide.title = "Prompt Guide used by one-click optimization";
        guide.dataset.h3rpLock = "";
        for (const item of RICH_PROMPT_GUIDES) {
            const option = element("option", "", item.label);
            option.value = item.id;
            guide.append(option);
        }
        guide.value = state.guide;
        guide.addEventListener("change", () => { state.guide = normalizeRichGuide(guide.value); persistView(); });
        const optimize = button("Optimize", "Optimize with the globally configured prompt backend.", () => void optimizePrompt(), "sparkle");
        optimize.classList.add("h3rp-optimize");
        const stop = button("Stop", "Cancel prompt optimization", stopOptimizer, "stop");
        stop.classList.add("h3rp-stop");
        stop.hidden = true;
        const applyPending = button("Apply changed result", "Explicitly replace a scene that changed while optimization was running", applyPendingOptimizerResult, "sparkle");
        applyPending.classList.add("h3rp-apply-pending");
        applyPending.hidden = true;
        const optimizerStatus = element("span", "h3rp-status");
        state.optimizerStatus = optimizerStatus;
        toolbar.replaceChildren(
            refsButton, dialogue, presentation, guide,
            optimize, stop, applyPending, optimizerStatus,
        );

        const basicPromptLabel = element("label", "h3rp-basic-prompt-label", "Basic prompt (plain language)");
        const basicPromptTextarea = element("textarea", "h3rp-basic-prompt");
        basicPromptTextarea.value = String(shot.basic_prompt ?? "");
        basicPromptTextarea.placeholder = "Optional plain-language scene idea, kept separate from the H3-formatted prompt. Optimize turns this into the prompt below, combined with the selected Prompt Guide's style rules.";
        basicPromptTextarea.title = "A simple draft description, not H3-formatted. Optimize sends this as content alongside the Prompt Guide's style rules.";
        basicPromptTextarea.spellcheck = true;
        basicPromptTextarea.addEventListener("input", () => {
            shot.basic_prompt = basicPromptTextarea.value;
            writePlan("Basic prompt saved to Plan", {deferEffects:true});
        });
        basicPromptLabel.append(basicPromptTextarea);

        const refs = element("div", "h3rp-ref-tray");
        state.refs = refs;
        const editorShell = element("div", "h3rp-editor-shell");
        const editor = element("div", "h3rp-editor");
        editor.contentEditable = "true";
        editor.spellcheck = true;
        editor.tabIndex = 0;
        editor.setAttribute("role", "textbox");
        editor.setAttribute("aria-multiline", "true");
        editor.dataset.placeholder = "Write this scene's action, camera, performance, dialogue, sound, and ending continuity…";
        state.editor = editor;
        const initialPrompt = promptValueToText(
            shot.prompt, `Scene ${state.active + 1} prompt`,
        );
        state.promptUndo = promptUndoForScene(shotId, initialPrompt);
        renderEditorText(initialPrompt);
        state.promptSelection = {
            start:initialPrompt.length, end:initialPrompt.length,
        };
        editor.addEventListener("input", (event) => {
            saveEditorInput(event);
            state.completion?.refresh();
            schedulePromptAnalysis();
        });
        for (const eventName of ["focus", "pointerup", "keyup"]) {
            editor.addEventListener(eventName, rememberPromptSelection);
        }
        editor.addEventListener("beforeinput", (event) => {
            if (event.inputType === "insertParagraph" || event.inputType === "insertLineBreak") {
                event.preventDefault(); insertPlainText(editor, "\n");
            }
        });
        editor.addEventListener("paste", (event) => {
            event.preventDefault(); insertPlainText(editor, event.clipboardData?.getData("text/plain") ?? "");
            // Decorate pasted tokens now, retaining the existing text-level
            // undo transaction and routing the save through the input handler.
            renderEditorText(editorPlainText(editor, {trimFinalNewline:false}), selectionTextOffset(editor));
            state.completion?.refresh();
        });
        editor.addEventListener("copy", (event) => copyEditorSelection(editor, event));
        editor.addEventListener("cut", (event) => copyEditorSelection(editor, event, true));
        const applyPromptUndo = (direction) => {
            const text = state.promptUndo?.[direction]?.();
            if (text == null) return false;
            const scrollTop = editor.scrollTop;
            const scrollLeft = editor.scrollLeft;
            const caret = selectionTextOffset(editor);
            renderEditorText(text, Math.min(caret, text.length));
            shot.prompt = promptTextToLines(text);
            writePlan(direction === "undo" ? "Undo saved to Plan" : "Redo saved to Plan");
            scheduleHistoryDraft(shotId, text);
            state.schema?.refresh();
            editor.focus({preventScroll:true});
            editor.scrollTop = scrollTop;
            editor.scrollLeft = scrollLeft;
            return true;
        };
        state.ownsPromptHistoryTarget = (target) => editor === target || editor.contains(target);
        state.applyPromptHistoryShortcut = applyPromptUndo;
        editor.addEventListener("keydown", (event) => {
            if (state.completion?.handleKeydown(event)) return;
            const undoDirection = promptUndoDirection(event);
            if (undoDirection) {
                event.preventDefault();
                applyPromptUndo(undoDirection);
            } else if (event.altKey && event.key === "ArrowLeft") event.preventDefault(), navigate(-1);
            else if (event.altKey && event.key === "ArrowRight") event.preventDefault(), navigate(1);
            else if (event.key === "Escape") refs.classList.remove("h3rp-open");
        });
        editor.addEventListener("blur", () => {
            flushPlanEffects();
            flushPromptAnalysis();
            const text = editorPlainText(editor);
            renderEditorText(text);
        });
        editorShell.append(editor);

        const replacePromptText = (result, message = "H3 edit saved to Plan") => {
            const text = String(result.text ?? "");
            if (state.referenceMode === "tagged") {
                const refreshed = availableReferenceRecords(
                    node, state.active + 1, {
                        includeInactive:true,
                        prompt:[sharedPrompt(state.plan).text.trim(), text.trim()]
                            .filter(Boolean).join("\n\n"),
                    },
                );
                state.records = refreshed.records;
                state.referenceMode = refreshed.mode;
            }
            state.promptUndo?.record(text, {inputType:"insertReplacementText"});
            shot.prompt = promptTextToLines(text);
            renderEditorText(text, result.caret, result);
            writePlan(message);
            scheduleHistoryDraft(shotId, text);
            state.schema?.refresh();
            editor.focus();
        };

        state.schema = createH3PromptSchemaController({
            node,
            propertyPrefix:"h3_rich_scene_prompt_editor",
            getText:() => editorPlainText(editor),
            replaceText:replacePromptText,
            focusAt:(caret) => {
                const text = editorPlainText(editor);
                const position = Math.max(0, Math.min(text.length, Number(caret) || 0));
                renderEditorText(text, position);
                editor.focus();
            },
            getRecords:() => state.records,
            defaultDuration:Number(shot.duration_seconds)
                || Number(shot.length) / 24 || 6,
            defaultMode:["tagged", "scheduled", "native"].includes(state.referenceMode)
                ? "ref2va"
                : state.referenceMode === "native_keyframes"
                  ? state.records.filter((record) => record.active && record.kind === "picture").length >= 2
                    ? "fl2va" : "i2va"
                  : "auto",
            scopeKey:shotId,
            markDirty:dirty,
        });
        if (state.schema) toolbar.prepend(state.schema.modeSelect, state.schema.toggle);

        state.completion = createPromptCompletionController({
            input:editor,
            maxItems:40,
            getText:() => editorPlainText(editor),
            getCaret:() => selectionTextOffset(editor),
            getRecords:() => state.records,
            getReferenceMode:() => state.referenceMode,
            getMode:() => state.schema?.getMode() ?? "auto",
            getAutomaticSuggestions:() => promptEditorPreferences().automaticSuggestions,
            getAppendCompletionSpace:() => promptEditorPreferences().appendCompletionSpace,
            getMarkerReplacement:() => promptEditorPreferences().markerReplacement,
            replaceText:(result) => replacePromptText(result, "Completion saved to Plan"),
        });
        bindPromptMarkerInteractions({
            input:editor, completion:state.completion,
            getText:() => editorPlainText(state.editor),
            getCaret:() => selectionTextOffset(editor),
            readFragment:(fragment) => editorPlainText(fragment, {trimFinalNewline:false}),
            restoreCaret:(position) => restoreCaret(editor, position),
            getEnabled:() => promptEditorPreferences().markerReplacement,
            getRecords:() => state.records,
            beforeOpen:() => hidePopover(true),
        });

        const footer = element("div", "h3rp-footer");
        const identity = element("span", "", `Scene ${state.active + 1}/${state.plan.shots.length} · ${shotId}`);
        const historyHost = element("div", "h3rp-history");
        const status = element("span", "h3rp-footer-status", "Synchronized with Plan");
        footer.append(identity, historyHost, status);
        state.history.host = historyHost;
        state.status = status;

        const completionHint = element(
            "span", "h3rp-muted",
            "Type @, #, <, or [; use ( for speakers · Ctrl/Cmd+Space for all H3 completions",
        );
        toolbar.append(completionHint);
        if (state.schema) identity.append(document.createTextNode(" · "), state.schema.counts);
        root.append(head, nav, basicPromptLabel, toolbar);
        if (state.schema) root.append(state.schema.panel);
        root.append(refs, editorShell, footer);
        refreshOptimizerUi();
        void loadHistory(shotId, editorPlainText(editor));
    }

    function loadPlan(force = false) {
        if (state.disposed) return;
        const planNode = upstreamPlanNode(node);
        const planWidget = planNode?.widgets?.find((item) => item.name === "plan_json");
        if (!planNode || !planWidget) {
            if (force || state.planNode) {
                state.plan = null; state.planNode = null; state.planWidget = null;
                state.lastValue = ""; state.lastRunName = "";
                showFailure("No connected H3 Chain Plan was found.");
            }
            return;
        }
        const value = String(planWidget.value ?? "");
        const runName = String(planNode.widgets?.find((item) => item.name === "run_name")?.value ?? "").trim();
        if (!force && planNode === state.planNode && value === state.lastValue && runName === state.lastRunName) return;
        try {
            const previousPlan = state.plan;
            const previousActive = state.active;
            const nextPlan = parsePlanJson(value);
            state.active = activeSceneIndexAfterRefresh(
                previousPlan, nextPlan, previousActive,
            );
            node.properties[ACTIVE_PROPERTY] = state.active;
            state.plan = nextPlan;
            state.planNode = planNode;
            state.planWidget = planWidget;
            state.lastValue = value;
            state.lastRunName = runName;
            render();
        } catch (error) {
            showFailure(`Connected Plan JSON is invalid:\n${error.message}`);
        }
    }

    state.optimizer.client = new PromptAssistantClient({
        identityKey:promptAssistantIdentityKey(node),
        onFrame:handleOptimizerFrame,
        onStatus:(status, detail) => {
            if (detail?.providers) {
                state.optimizer.providers = detail.providers;
            }
            if (status === "disconnected" && state.optimizer.requestId
                    && state.optimizer.activeBackend === "mcp") {
                state.optimizer.error = "Prompt-agent bridge disconnected.";
                state.optimizer.requestId = null;
                state.optimizer.meta = null;
                state.optimizer.activeBackend = null;
            }
            refreshOptimizerUi();
        },
    });
    const onOptimizerSettingsChanged = () => refreshOptimizerUi();
    globalThis.addEventListener?.("h3-prompt-optimizer-settings-changed", onOptimizerSettingsChanged);

    const widget = node.addDOMWidget("h3_rich_scene_prompt_editor", "h3-rich-scene-prompt-editor", root,
        {serialize:false, hideOnZoom:false, getMinHeight:() => 560});
    widget.serialize = false;
    node.setSize?.([Math.max(Number(node.size?.[0]) || 780, 780), Math.max(Number(node.size?.[1]) || 760, 760)]);

    const connectionsChanged = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = connectionsChanged?.apply(this, arguments);
        setTimeout(() => loadPlan(true), 0);
        return result;
    };
    const onPromptExecuted = (event) => {
        const values = event.detail?.output?.h3_chain_active_scene;
        const scene = Array.isArray(values) ? values.at(-1) : null;
        if (!scene || String(scene.run_name ?? "") !== planRunName()) return;
        const shot = state.plan?.shots?.[state.active];
        const shotId = String(shot?.id ?? "");
        if (!shotId || String(scene.shot_id ?? "") !== shotId) return;
        window.setTimeout(() => {
            if (state.history.sceneKey === historySceneKey(planRunName(), shotId)) {
                void loadHistory(shotId, promptValueToText(shot.prompt), false);
            }
        }, 50);
    };
    api.addEventListener("executed", onPromptExecuted);
    const onProjectAssetCatalogChanged = (event) => {
        const changedProject = String(event?.detail?.project ?? "").trim();
        const currentProject = planRunName();
        if (changedProject && currentProject && changedProject !== currentProject) return;
        if (!state.plan?.shots?.length || !state.editor) return;
        const prompt = editorPlainText(state.editor);
        const selection = document.activeElement === state.editor
            ? editorSelectionOffsets(state.editor) : null;
        refreshReferenceState(prompt);
        renderEditorText(prompt, null, selection ? {
            selectionStart:selection.start,
            selectionEnd:selection.end,
        } : null);
        if (state.refs?.classList.contains("h3rp-open")) renderReferenceTray();
        state.completion?.refresh();
        state.schema?.refresh();
    };
    globalThis.addEventListener?.(
        PROJECT_ASSET_CATALOG_CHANGED_EVENT, onProjectAssetCatalogChanged,
    );

    const removed = node.onRemoved;
    node.onRemoved = function () {
        // Closing a workflow removes many nodes while ComfyUI is also saving
        // its tab session. Never propagate Plan callbacks, analyze prompts, or
        // start history requests from teardown: the edited Plan JSON was
        // already assigned synchronously by writePlan().
        state.disposed = true;
        const cleanups = [
            () => {
                if (state.planSyncTimer != null) window.clearTimeout(state.planSyncTimer);
                state.planSyncTimer = null;
                state.planSyncPending = null;
                if (state.analysisTimer != null) window.clearTimeout(state.analysisTimer);
                state.analysisTimer = null;
                if (state.history.saveTimer != null) window.clearTimeout(state.history.saveTimer);
                state.history.saveTimer = null;
                state.history.pendingDraft = null;
                state.history.loadToken += 1;
                state.history.host = null;
            },
            () => { if (state.pollTimer != null) window.clearInterval(state.pollTimer); },
            () => { if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer); },
            () => hidePopover(true),
            () => state.popover?.remove(),
            () => state.completion?.destroy(),
            () => api.removeEventListener("executed", onPromptExecuted),
            () => globalThis.removeEventListener?.(
                PROJECT_ASSET_CATALOG_CHANGED_EVENT, onProjectAssetCatalogChanged,
            ),
            () => state.optimizer.client?.close(),
            () => state.optimizer.abortController?.abort(),
            () => globalThis.removeEventListener?.(
                "h3-prompt-optimizer-settings-changed", onOptimizerSettingsChanged,
            ),
        ];
        for (const cleanup of cleanups) {
            try { cleanup(); }
            catch (error) { console.warn("H3 Rich Prompt Editor cleanup failed", error); }
        }
        state.completion = null;
        delete node._h3PromptCompanionSetActiveScene;
        delete node._h3PromptCompanionSetScenePrompt;
        delete node._h3PromptCompanionSetBasicPrompt;
        return removed?.apply(this, arguments);
    };
    node._h3PromptCompanionSetActiveScene = (planNode, index) => {
        if (planNode !== state.planNode || !state.plan?.shots?.length || optimizerBusy()) return false;
        navigate(0, index, {synchronize:false, focus:false});
        return true;
    };
    node._h3PromptCompanionSetScenePrompt = (planNode, index, text) => {
        if (planNode !== state.planNode || !state.plan?.shots?.[index]) return false;
        const liveValue = String(state.planWidget?.value ?? "");
        let livePlanParsed = false;
        try {
            const livePlan = parsePlanJson(liveValue);
            livePlanParsed = true;
            if (planHasNonPromptChanges(state.plan, livePlan)) {
                loadPlan(true);
                return true;
            }
        } catch (_error) {
            // Leave lastValue untouched so normal polling reports invalid JSON.
        }
        state.plan.shots[index].prompt = promptTextToLines(text);
        const shotId = String(
            state.plan.shots[index].id
            || `clip_${String(index + 1).padStart(4, "0")}`,
        );
        const promptUndo = promptUndoForScene(shotId, text, {external:true});
        if (index === state.active) state.promptUndo = promptUndo;
        if (index === state.active && state.editor) {
            const current = editorPlainText(state.editor);
            if (current !== text) {
                const caret = document.activeElement === state.editor
                    ? selectionTextOffset(state.editor) : null;
                renderEditorText(text, caret == null ? null : Math.min(caret, text.length));
                scheduleHistoryDraft(
                    String(state.plan.shots[index].id || `clip_${String(index + 1).padStart(4, "0")}`),
                    text);
                state.schema?.refresh();
            }
        }
        if (livePlanParsed) state.lastValue = liveValue;
        return true;
    };
    node._h3PromptCompanionSetBasicPrompt = (planNode, index, text) => {
        if (planNode !== state.planNode || !state.plan?.shots?.[index]) return false;
        state.plan.shots[index].basic_prompt = text;
        if (index === state.active) {
            const basicPromptTextarea = root.querySelector(".h3rp-basic-prompt");
            if (basicPromptTextarea && basicPromptTextarea.value !== text) {
                basicPromptTextarea.value = text;
            }
        }
        return true;
    };
    node._h3RichPromptRefresh = () => loadPlan(true);
    state.pollTimer = window.setInterval(() => loadPlan(false), 500);
    loadPlan(true);
}

app.registerExtension({
    name:"minimax_h3_context_loop.rich_scene_prompt_editor",
    async beforeRegisterNodeDef(nodeTypeDefinition, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeTypeDefinition.prototype.onNodeCreated;
        nodeTypeDefinition.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setTimeout(() => mount(this), 0);
            return result;
        };
    },
    async nodeCreated(node) {
        if (nodeType(node) === NODE_NAME) mount(node);
    },
    async afterConfigureGraph() {
        for (const node of allNodes(app.graph)) {
            if (nodeType(node) === NODE_NAME) setTimeout(() => node._h3RichPromptRefresh?.(), 0);
        }
    },
});
