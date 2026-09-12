import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    projectMutationOptions, subscribeProjectOwnership, isProjectReadOnlyError,
} from "./h3_project_ownership.mjs?v=0.7.4";
import {
    MAX_SHOTS,
    makeShot,
    parsePlanJson,
    planToJson,
    promptTextToLines,
    promptValueToText,
    sharedPrompt,
} from "./h3_chain_plan_core.mjs?v=0.7.8";
import {
    PROMPT_ASSIST_DEFAULT_INSTRUCTIONS,
    PROMPT_ASSIST_MODES,
    buildPromptAssistantContext,
    draftConflict,
    makePromptAssistRequest,
    promptSceneKey,
    promptSourceRevision,
} from "./h3_prompt_assistant_core.mjs?v=0.7.7";
import {PromptAssistantClient} from "./h3_prompt_assistant_client.mjs?v=0.7.0";
import {
    promptRevisionHelp,
    promptRevisionLabel,
    promptRevisionNavigation,
    promptRevisionTree,
} from "./h3_prompt_history_core.mjs?v=0.7.0";
import {
    availableReferenceRecords,
    convertTaggedPictureReference,
    referenceReplacementToken,
    replacePromptReferenceOccurrence,
    taggedPictureReferenceMode,
    taggedPictureReferenceToken,
} from "./h3_reference_preview_core.mjs?v=0.7.3";
import {
    PromptUndoHistory,
    promptUndoDirection,
    tokenizeRichPrompt,
} from "./h3_rich_prompt_editor_core.mjs?v=0.7.5";
import {createPromptCompletionController} from "./h3_prompt_completion_core.mjs?v=0.7.5";
import {bindPromptMarkerInteractions} from "./h3_prompt_marker_ui.mjs?v=0.7.5";
import {isWorkflowSaveShortcut, promptEditorRichText} from "./h3_prompt_editor_settings_core.mjs?v=0.7.5";
import {promptEditorPreferences} from "./h3_prompt_editor_settings.js";
import {createH3PromptSchemaController} from "./h3_prompt_schema_ui.mjs?v=0.7.5";
import * as promptCompanionSync from "./h3_prompt_companion_sync.mjs?v=0.7.2";
import {
    PROJECT_ASSET_CATALOG_CHANGED_EVENT,
} from "./h3_project_asset_sync_core.mjs?v=0.7.2";

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

// The compact reference and dialogue authoring interactions are inspired by
// nkxx188/ComfyUI-MiniMaxH3-Easy (MIT); see THIRD_PARTY_NOTICES.md.

const NODE_NAME = "MiniMaxH3ChainScenePromptEditor";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const PLAN_NAMES = new Set([PLAN_NAME, "MiniMaxH3ChainPlanModern"]);
const ACTIVE_SCENE_PROPERTY = "h3_scene_prompt_editor_active_scene";
const FONT_SIZE_PROPERTY = "h3_scene_prompt_editor_font_size";
const PRESENTATION_PROPERTY = "h3_scene_prompt_editor_rich_text";
const ASSIST_PROVIDER_PROPERTY = "h3_scene_prompt_editor_assist_provider";
const ASSIST_MODE_PROPERTY = "h3_scene_prompt_editor_assist_mode";
// Keep the complete prompt-assistant implementation available for a future
// revisit, but ship the original focused editor experience for now. Re-enabling
// it is intentionally a one-line change.
const PROMPT_ASSISTANT_ENABLED = false;
const DEFAULT_FONT_SIZE = 18;
const MIN_FONT_SIZE = 12;
const MAX_FONT_SIZE = 36;
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
});

function injectStyles() {
    if (document.getElementById("h3-scene-prompt-editor-style")) return;
    const style = document.createElement("style");
    style.id = "h3-scene-prompt-editor-style";
    style.textContent = `
        .h3sp-root {
            --h3sp-bg: color-mix(in srgb, var(--comfy-menu-bg, #202124) 92%, #101827);
            --h3sp-panel: color-mix(in srgb, var(--comfy-input-bg, #111827) 84%, #263552);
            --h3sp-border: color-mix(in srgb, var(--border-color, #555) 68%, #7891bf);
            --h3sp-text: var(--input-text, #eef1f7);
            --h3sp-muted: color-mix(in srgb, var(--h3sp-text) 58%, transparent);
            /* Blend semantic foregrounds with the active ComfyUI text color.
               This keeps the pastel identity in dark themes and makes the
               same controls dark enough to read in light themes. */
            --h3sp-accent: color-mix(in srgb, var(--h3sp-text) 38%, #4f83ff);
            --h3sp-token-picture: color-mix(in srgb, var(--h3sp-text) 42%, #139be8);
            --h3sp-token-video: color-mix(in srgb, var(--h3sp-text) 42%, #9355d6);
            --h3sp-token-audio: color-mix(in srgb, var(--h3sp-text) 42%, #d47700);
            --h3sp-token-subject: color-mix(in srgb, var(--h3sp-text) 42%, #26934a);
            --h3sp-token-dialogue: color-mix(in srgb, var(--h3sp-text) 42%, #cf3976);
            --h3sp-token-section: color-mix(in srgb, var(--h3sp-text) 38%, #5f87ff);
            --h3sp-token-flow: color-mix(in srgb, var(--h3sp-text) 42%, #00a99d);
            --h3sp-token-speaker: color-mix(in srgb, var(--h3sp-text) 42%, #d268b7);
            --h3sp-token-danger: color-mix(in srgb, var(--h3sp-text) 42%, #d44747);
            --h3sp-font-size: 18px;
            box-sizing:border-box; width:100%; height:100%; min-height:420px;
            display:flex; flex-direction:column; gap:8px; overflow:hidden; padding:10px;
            border:1px solid var(--h3sp-border); border-radius:8px; background:var(--h3sp-bg);
            color:var(--h3sp-text); font:12px/1.35 system-ui,sans-serif;
        }
        .h3sp-root *, .h3sp-root *::before, .h3sp-root *::after { box-sizing:border-box; }
        .h3sp-head, .h3sp-nav, .h3sp-tools, .h3sp-font, .h3sp-footer {
            display:flex; align-items:center; gap:6px;
        }
        .h3sp-head { justify-content:space-between; }
        .h3sp-title { color:var(--h3sp-accent); font-size:15px; font-weight:750; }
        .h3sp-context { color:var(--h3sp-muted); white-space:nowrap; overflow:hidden;
            text-overflow:ellipsis; text-align:right; }
        .h3sp-nav select { flex:1; min-width:0; }
        .h3sp-root button, .h3sp-root select {
            color:var(--h3sp-text); font:inherit; border:1px solid var(--h3sp-border);
            border-radius:5px; background:var(--comfy-input-bg,#171a21);
        }
        .h3sp-root button { padding:6px 9px; cursor:pointer; white-space:nowrap; }
        .h3sp-root button:hover { border-color:var(--h3sp-accent); }
        .h3sp-root button:disabled { cursor:not-allowed; opacity:.4; }
        .h3sp-root select { min-height:30px; padding:5px 7px; }
        .h3sp-font { margin-left:auto; }
        .h3sp-font-value { min-width:38px; color:var(--h3sp-muted); text-align:center; }
        .h3sp-textarea {
            width:100%; min-height:240px; flex:1 1 auto; resize:none; padding:12px 14px;
            border:1px solid var(--h3sp-border); border-radius:7px;
            outline:none; background:var(--comfy-input-bg,#11141a); color:var(--h3sp-text);
            font:var(--h3sp-font-size)/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;
            tab-size:4; white-space:pre-wrap;
        }
        .h3sp-textarea:focus { border-color:var(--h3sp-accent);
            box-shadow:0 0 0 1px color-mix(in srgb,var(--h3sp-accent) 45%,transparent); }
        .h3sp-hidden { display:none !important; }
        .h3sp-editor-shell { position:relative; width:100%; min-height:240px;
            flex:1 1 auto; overflow:hidden; border:1px solid var(--h3sp-border);
            border-radius:7px; background:var(--comfy-input-bg,#11141a); }
        .h3sp-editor-shell:focus-within { border-color:var(--h3sp-accent);
            box-shadow:0 0 0 1px color-mix(in srgb,var(--h3sp-accent) 45%,transparent); }
        .h3sp-rich-editor { width:100%; height:100%; min-height:240px; overflow:auto;
            padding:12px 14px; outline:none; white-space:pre-wrap; overflow-wrap:anywhere;
            caret-color:var(--h3sp-text);
            font:var(--h3sp-font-size)/1.55 ui-monospace,SFMono-Regular,Consolas,monospace; }
        .h3sp-rich-editor:empty::before { content:attr(data-placeholder);
            color:var(--h3sp-muted); pointer-events:none; }
        .h3sp-token { display:inline-flex; align-items:center; gap:3px; max-width:320px;
            margin:0 1px; padding:1px 4px 1px 2px; border:1px solid currentColor;
            border-radius:5px; vertical-align:1px; line-height:1.25; cursor:pointer;
            user-select:all; background:color-mix(in srgb,currentColor 14%,transparent); }
        .h3sp-token-picture { color:var(--h3sp-token-picture); }
        .h3sp-token-video { color:var(--h3sp-token-video); }
        .h3sp-token-audio { color:var(--h3sp-token-audio); }
        .h3sp-token-subject { color:var(--h3sp-token-subject); }
        .h3sp-token-dialogue { color:var(--h3sp-token-dialogue); }
        .h3sp-token-section { display:inline; margin:0; padding:0; border:0;
            border-radius:0; color:var(--h3sp-token-section); background:none;
            font-weight:650; cursor:text; user-select:text; }
        .h3sp-token-flow { color:var(--h3sp-token-flow); }
        .h3sp-token-speaker { color:var(--h3sp-token-speaker); }
        .h3sp-token-unknown, .h3sp-token-inactive { color:var(--h3sp-token-danger);
            border-style:dashed; }
        .h3sp-token-icon { width:16px; height:16px; display:inline-flex;
            flex:0 0 16px; color:currentColor; }
        .h3sp-token-icon svg { width:100%; height:100%; fill:none; stroke:currentColor;
            stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round; }
        .h3sp-token-thumb { width:18px; height:18px; flex:0 0 18px;
            object-fit:cover; border-radius:3px; background:rgba(255,255,255,.09); }
        .h3sp-token-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3sp-presentation-active { border-color:var(--h3sp-accent) !important;
            color:var(--h3sp-accent) !important; }
        .h3sp-popover { position:fixed; z-index:100000; width:min(360px,calc(100vw - 24px));
            padding:9px; border:1px solid #60718c; border-radius:9px; background:#171a20;
            color:#eef2f8; box-shadow:0 14px 38px rgba(0,0,0,.48);
            font:12px/1.4 system-ui,sans-serif; }
        .h3sp-popover[hidden] { display:none; }
        .h3sp-popover-title { margin-bottom:6px; font-weight:750; }
        .h3sp-popover-media { display:block; width:100%; max-height:240px;
            object-fit:contain; border-radius:6px; background:#08090c; }
        .h3sp-popover audio.h3sp-popover-media { height:42px; background:transparent; }
        .h3sp-popover-detail { margin-top:6px; color:rgba(238,242,248,.62);
            white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3sp-popover-editor { display:grid; gap:8px; }
        .h3sp-popover-field { display:grid; grid-template-columns:82px minmax(0,1fr);
            align-items:center; gap:7px; }
        .h3sp-popover-field > span { color:rgba(238,242,248,.68); }
        .h3sp-popover-field select,.h3sp-popover-field input { width:100%; min-width:0;
            padding:5px 7px; border:1px solid #60718c; border-radius:5px;
            background:#0e1117; color:#eef2f8; }
        .h3sp-popover-actions { display:flex; justify-content:flex-end; gap:6px; }
        .h3sp-popover-actions button { padding:4px 9px; }
        .h3sp-root.h3sp-assistant-enabled { overflow:auto; }
        .h3sp-root.h3sp-assistant-enabled .h3sp-textarea {
            min-height:220px; resize:vertical;
        }
        .h3sp-tools { position:relative; flex-wrap:wrap; }
        .h3sp-hint { color:var(--h3sp-muted); margin-left:auto; }
        .h3sp-refs { display:none; flex:0 0 auto; max-height:340px; overflow:auto;
            padding:7px; gap:5px; flex-wrap:wrap; border:1px solid var(--h3sp-border);
            border-radius:6px; background:var(--h3sp-panel); }
        .h3sp-refs.h3sp-open { display:flex; }
        .h3sp-refs button { padding:4px 7px; }
        .h3sp-ref-help { flex:1 0 100%; color:var(--h3sp-muted); }
        .h3sp-ref-chip.h3sp-inactive { opacity:.48; }
        .h3sp-ref-chip.h3sp-active { border-color:#658b77; }
        .h3sp-ref-entry { display:flex; align-items:stretch; gap:2px; }
        .h3sp-ref-mode { display:flex; align-items:stretch; gap:1px; }
        .h3sp-ref-mode button { min-width:28px; padding:2px 5px; font-size:10px; }
        .h3sp-ref-mode button.h3sp-selected { border-color:var(--h3sp-accent);
            color:var(--h3sp-accent); }
        .h3sp-ref-preview { display:none; flex:1 0 100%; min-height:58px;
            padding:8px; border:1px solid color-mix(in srgb,var(--h3sp-border) 74%,transparent);
            border-radius:6px; background:var(--comfy-input-bg,#11141a); }
        .h3sp-ref-preview.h3sp-visible { display:grid; grid-template-columns:minmax(120px,220px) 1fr;
            align-items:start; gap:9px; }
        .h3sp-ref-preview-media { width:100%; max-height:190px; display:block;
            border-radius:5px; object-fit:contain; background:#08090c; }
        audio.h3sp-ref-preview-media { min-width:190px; height:38px; background:transparent; }
        .h3sp-ref-preview-copy { min-width:0; color:var(--h3sp-muted);
            white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3sp-ref-preview-title { color:var(--h3sp-text); font-weight:700; }
        .h3sp-footer { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
            align-items:center; gap:8px; color:var(--h3sp-muted); }
        .h3sp-footer-status { min-width:0; overflow:hidden; text-align:right;
            text-overflow:ellipsis; white-space:nowrap; }
        .h3sp-history { min-width:0; display:flex; align-items:center;
            justify-content:center; gap:6px; color:var(--h3sp-muted); }
        .h3sp-history-nav { display:flex; align-items:center; gap:2px;
            border:1px solid color-mix(in srgb,var(--h3sp-border) 72%,transparent);
            border-radius:999px; padding:1px 3px; background:var(--comfy-input-bg,#11141a); }
        .h3sp-history-nav button { min-width:25px; padding:2px 5px; border:0;
            border-radius:999px; background:transparent; }
        .h3sp-history-count { min-width:42px; text-align:center; color:var(--h3sp-text);
            font-variant-numeric:tabular-nums; }
        .h3sp-history-meta { min-width:0; max-width:280px; overflow:hidden;
            text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
        .h3sp-history-error { color:#ffb3b3; }
        .h3sp-history-tree { position:relative; flex:0 0 auto; }
        .h3sp-history-tree > summary { list-style:none; cursor:pointer; padding:3px 7px;
            border:1px solid color-mix(in srgb,var(--h3sp-border) 72%,transparent);
            border-radius:999px; background:var(--comfy-input-bg,#11141a);
            color:var(--h3sp-text); user-select:none; }
        .h3sp-history-tree > summary::-webkit-details-marker { display:none; }
        .h3sp-history-tree[open] > summary { border-color:var(--h3sp-accent); }
        .h3sp-history-tree-panel { position:absolute; z-index:20; bottom:calc(100% + 7px);
            left:50%; transform:translateX(-50%); width:min(440px,calc(100vw - 30px));
            max-height:310px; overflow:auto; padding:7px; border:1px solid var(--h3sp-border);
            border-radius:8px; background:var(--h3sp-bg); box-shadow:0 14px 36px rgba(0,0,0,.48); }
        .h3sp-history-tree-row { display:flex; align-items:center; gap:4px; min-width:0;
            margin:2px 0; border-radius:5px; }
        .h3sp-history-tree-row.h3sp-history-active { background:rgba(132,170,255,.12); }
        .h3sp-history-tree-row.h3sp-history-archived { opacity:.58; }
        .h3sp-history-tree-select { flex:1 1 auto; min-width:0; justify-content:flex-start !important;
            min-height:26px !important; padding:3px 6px !important; border-color:transparent !important;
            background:transparent !important; text-align:left; }
        .h3sp-history-branch { flex:0 0 auto; color:var(--h3sp-muted); }
        .h3sp-history-tree-label { min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; color:var(--h3sp-text); }
        .h3sp-history-tree-badge { flex:0 0 auto; color:var(--h3sp-muted); font-size:10px; }
        .h3sp-history-tree-action { min-width:25px !important; min-height:25px !important;
            padding:2px 5px !important; }
        .h3sp-history-tree-tools { display:flex; justify-content:space-between; align-items:center;
            gap:8px; margin-top:6px; padding-top:6px; border-top:1px solid var(--h3sp-border);
            color:var(--h3sp-muted); font-size:10px; }
        .h3sp-history-tree-tools button { min-height:24px; padding:2px 6px; }
        .h3sp-error { padding:12px; border:1px solid #a76565; border-radius:6px;
            color:#ffb3b3; background:#351f24; white-space:pre-wrap; }
        .h3sp-assist { flex:0 0 auto; display:flex; flex-direction:column; gap:7px;
            padding:9px; border:1px solid var(--h3sp-border); border-radius:7px;
            background:var(--h3sp-panel); }
        .h3sp-assist-head, .h3sp-assist-controls, .h3sp-assist-actions,
        .h3sp-assist-contexts { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .h3sp-assist-head { justify-content:space-between; }
        .h3sp-assist-title { color:var(--h3sp-accent); font-size:13px; font-weight:750; }
        .h3sp-assist-status { color:var(--h3sp-muted); font-size:11px; }
        .h3sp-assist-controls select { flex:1 1 120px; min-width:100px; }
        .h3sp-assist-contexts label { display:flex; align-items:center; gap:4px;
            color:var(--h3sp-muted); cursor:pointer; }
        .h3sp-assist-contexts input { accent-color:var(--h3sp-accent); }
        .h3sp-assist-chat { display:flex; flex-direction:column; gap:5px; max-height:180px;
            overflow:auto; padding:6px; border:1px solid color-mix(in srgb,var(--h3sp-border) 70%,transparent);
            border-radius:6px; background:color-mix(in srgb,var(--comfy-input-bg,#11141a) 82%,transparent); }
        .h3sp-assist-message { padding:6px 8px; border-radius:6px; white-space:pre-wrap;
            overflow-wrap:anywhere; }
        .h3sp-assist-message-user { margin-left:12%; background:#23375b; }
        .h3sp-assist-message-agent { margin-right:7%; background:#263c34; }
        .h3sp-assist-message-system { color:var(--h3sp-muted); font-size:11px;
            border:1px dashed var(--h3sp-border); }
        .h3sp-assist-empty { color:var(--h3sp-muted); padding:5px; }
        .h3sp-assist-compose { display:flex; align-items:stretch; gap:6px; }
        .h3sp-assist-compose textarea, .h3sp-assist-draft textarea {
            width:100%; resize:vertical; min-height:58px; padding:7px 8px;
            border:1px solid var(--h3sp-border); border-radius:6px;
            outline:none; background:var(--comfy-input-bg,#11141a); color:var(--h3sp-text);
            font:12px/1.4 system-ui,sans-serif; }
        .h3sp-assist-compose textarea:focus, .h3sp-assist-draft textarea:focus {
            border-color:var(--h3sp-accent); }
        .h3sp-assist-compose button { align-self:stretch; }
        .h3sp-assist-draft { display:flex; flex-direction:column; gap:6px; padding:8px;
            border:1px solid #5d8a72; border-radius:6px; background:#182b25; }
        .h3sp-assist-draft-head { display:flex; justify-content:space-between;
            align-items:center; gap:6px; }
        .h3sp-assist-draft-title { font-weight:700; color:#9ad7b7; }
        .h3sp-assist-stale { color:#ffc08a; font-size:11px; }
        .h3sp-assist-original { color:var(--h3sp-muted); }
        .h3sp-assist-original pre { max-height:120px; overflow:auto; white-space:pre-wrap;
            padding:6px; border-radius:5px; background:var(--comfy-input-bg,#11141a); }
        .h3sp-assist-error { color:#ffb3b3; white-space:pre-wrap; }
    `;
    document.head.appendChild(style);
}

function element(tag, className = "", text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action) {
    const item = element("button", "", label);
    item.type = "button";
    item.title = title;
    // Preserve a contenteditable selection while clicking toolbar/reference
    // controls. Keyboard focus via Tab remains available.
    item.addEventListener("pointerdown", (event) => event.preventDefault());
    item.addEventListener("click", action);
    return item;
}

function icon(kind) {
    const item = element("span", "h3sp-token-icon");
    item.innerHTML = ICONS[kind] ?? ICONS.reference;
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
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (node !== start && PLAN_NAMES.has(nodeType(node))) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = node.graph?.links?.[input.link];
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
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
        return {
            filename: String(value.filename),
            subfolder: String(value.subfolder ?? ""),
            type: String(value.type ?? "input"),
        };
    }
    let text = typeof value === "string" ? value.trim() : "";
    if (!text) return null;
    if (/^(?:blob:|data:|https?:|\/api\/view\?|\/view\?)/i.test(text)) {
        return {url: text};
    }
    let type = "input";
    const annotated = text.match(/\s+\[(input|output|temp)\]\s*$/i);
    if (annotated) {
        type = annotated[1].toLowerCase();
        text = text.slice(0, annotated.index).trim();
    }
    text = text.replaceAll("\\", "/").replace(/^\/+/, "");
    if (!mediaExtension(kind).test(text)) return null;
    const slash = text.lastIndexOf("/");
    return {
        filename: slash >= 0 ? text.slice(slash + 1) : text,
        subfolder: slash >= 0 ? text.slice(0, slash) : "",
        type,
    };
}

function assetUrl(asset) {
    if (!asset) return null;
    if (asset.url) return asset.url;
    const query = new URLSearchParams({
        filename: asset.filename,
        subfolder: asset.subfolder ?? "",
        type: asset.type ?? "input",
    });
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
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        const url = previewFromNode(node, kind);
        if (url) return {url, source: node};
        for (const input of node.inputs ?? []) {
            const parent = inputSource(node, input.name);
            if (parent) queue.push(parent);
        }
    }
    return {url: null, source: start};
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

function insertText(textarea, text, selectionOffset = text.length) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    textarea.setRangeText(text, start, end, "end");
    const caret = start + selectionOffset;
    textarea.setSelectionRange(caret, caret);
    textarea.dispatchEvent(new Event("input", {bubbles: true}));
    textarea.focus();
}

function insertDialogue(textarea) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const selected = textarea.value.slice(start, end);
    const markup = `<d>${selected}</d>`;
    insertText(textarea, markup, selected ? markup.length : 3);
}

function editorPlainText(editor, {trimFinalNewline = true} = {}) {
    function read(current, root) {
        if (current.nodeType === Node.TEXT_NODE) return current.textContent ?? "";
        if (current.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
            let text = "";
            for (const child of current.childNodes) text += read(child, root);
            return text;
        }
        if (current.nodeType !== Node.ELEMENT_NODE) return "";
        if (current.classList?.contains("h3sp-token")) {
            return current.dataset.token ?? current.textContent ?? "";
        }
        if (current.tagName === "BR") return "\n";
        let text = "";
        for (const child of current.childNodes) text += read(child, root);
        if (["DIV", "P"].includes(current.tagName)
                && current !== root && !text.endsWith("\n")) text += "\n";
        return text;
    }
    const text = read(editor, editor);
    return trimFinalNewline ? text.replace(/\n$/, "") : text;
}

function selectedEditorPlainText(editor) {
    const selection = globalThis.getSelection?.();
    if (!selection?.rangeCount || selection.isCollapsed) return null;
    const inside = (current) => current === editor || editor.contains(current);
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
        editor.dispatchEvent(new Event("input", {bubbles: true}));
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
    function visit(current) {
        if (placed) return;
        if (current.nodeType === Node.TEXT_NODE) {
            const length = current.textContent?.length ?? 0;
            if (target <= consumed + length) {
                range.setStart(current, Math.max(0, target - consumed));
                placed = true;
                return;
            }
            consumed += length;
            return;
        }
        if (current.nodeType !== Node.ELEMENT_NODE) return;
        if (current.classList?.contains("h3sp-token")) {
            const length = String(current.dataset.token ?? "").length;
            if (target <= consumed + length) {
                if (target <= consumed) range.setStartBefore(current);
                else range.setStartAfter(current);
                placed = true;
                return;
            }
            consumed += length;
            return;
        }
        for (const child of current.childNodes) visit(child);
    }
    visit(editor);
    if (!placed) {
        range.selectNodeContents(editor);
        range.collapse(false);
    } else {
        range.collapse(true);
    }
    selection?.removeAllRanges();
    selection?.addRange(range);
}

function restoreTextSelection(editor, requestedStart, requestedEnd) {
    const start = Math.max(0, Number(requestedStart) || 0);
    const end = Math.max(start, Number(requestedEnd) || start);
    let consumed = 0;
    let startPoint = null;
    let endPoint = null;
    function visit(current) {
        if (startPoint && endPoint) return;
        if (current.nodeType === Node.TEXT_NODE) {
            const length = current.textContent?.length ?? 0;
            if (!startPoint && start >= consumed && start <= consumed + length) {
                startPoint = [current, start - consumed];
            }
            if (!endPoint && end >= consumed && end <= consumed + length) {
                endPoint = [current, end - consumed];
            }
            consumed += length;
            return;
        }
        if (current.nodeType !== Node.ELEMENT_NODE) return;
        if (current.classList?.contains("h3sp-token")) {
            consumed += String(current.dataset.token ?? "").length;
            return;
        }
        for (const child of current.childNodes) visit(child);
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
    const inside = Boolean(selection?.rangeCount && editor.contains(selection.anchorNode));
    const range = inside ? selection.getRangeAt(0) : document.createRange();
    if (!inside) {
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
    editor.dispatchEvent(new Event("input", {bubbles: true}));
}

function clamp(value, minimum, maximum, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric)
        ? Math.max(minimum, Math.min(maximum, Math.round(numeric))) : fallback;
}

function promptAssistantIdentityKey(node) {
    // Node ids are only unique inside one workflow. ComfyUI can keep several
    // workflows open in the same browser/sessionStorage, so include the active
    // workflow identity or two editors with (for example) node id 7 would share
    // one agent route and pending-request record.
    const workflow = app.extensionManager?.workflow?.activeWorkflow;
    const workflowIdentity = workflow?.path
        ?? workflow?.activeState?.id
        ?? workflow?.filename
        ?? "legacy-workflow";
    const nodeIdentity = node.id == null
        ? `new-${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`
        : String(node.id);
    return `workflow-${workflowIdentity}-node-${nodeIdentity}`;
}

function mount(node) {
    if (node._h3ScenePromptEditorMounted || typeof node.addDOMWidget !== "function") return;
    node._h3ScenePromptEditorMounted = true;
    injectStyles();

    node.properties ??= {};
    const root = element("div", "h3sp-root");
    root.classList.toggle("h3sp-assistant-enabled", PROMPT_ASSISTANT_ENABLED);
    root.title = "Edit the active scene prompt stored in the connected H3 Chain Plan.";
    for (const eventName of [
        "pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick",
    ]) {
        root.addEventListener(eventName, (event) => event.stopPropagation());
    }
    // Keep ComfyUI/LiteGraph's canvas shortcuts from claiming Ctrl/Cmd+V (and
    // the other native editing shortcuts) while focus is inside this DOM
    // widget. stopPropagation deliberately preserves the browser's default
    // textarea/contenteditable action; preventDefault here would break paste.
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
        plan: null,
        planNode: null,
        planWidget: null,
        lastValue: "",
        lastRunName: "",
        active: Math.max(0, Number(node.properties[ACTIVE_SCENE_PROPERTY]) || 0),
        fontSize: clamp(
            node.properties[FONT_SIZE_PROPERTY], MIN_FONT_SIZE, MAX_FONT_SIZE,
            DEFAULT_FONT_SIZE,
        ),
        decorated: promptEditorRichText(node.properties, PRESENTATION_PROPERTY, promptEditorPreferences()),
        records: [],
        referenceMode: null,
        referenceSyntax: new Map(),
        completion: null,
        schema: null,
        promptTextarea: null,
        richEditor: null,
        promptSelection: null,
        referenceTray: null,
        popover: null,
        popoverTimer: null,
        popoverPinned: false,
        assistant: {
            client: null,
            host: null,
            status: "idle",
            statusDetail: "Connects through comfyui-mcp on the first request",
            provider: ["codex", "hermes"].includes(
                node.properties[ASSIST_PROVIDER_PROPERTY],
            ) ? node.properties[ASSIST_PROVIDER_PROPERTY] : "codex",
            mode: PROMPT_ASSIST_MODES.some(
                (item) => item.id === node.properties[ASSIST_MODE_PROPERTY],
            ) ? node.properties[ASSIST_MODE_PROPERTY] : "rewrite",
            includeShared: true,
            includeAdjacent: true,
            composer: "",
            messagesByProvider: {codex: [], hermes: []},
            drafts: new Map(),
            requestContexts: new Map(),
            activeRequest: null,
            preparingRequest: null,
            pendingStorageKey: "",
            reconnectTimer: null,
            lastApplied: null,
            providers: null,
            error: "",
        },
        history: {
            sceneKey: "",
            data: null,
            revisionId: null,
            host: null,
            textarea: null,
            status: null,
            loadToken: 0,
            loadPromise: null,
            saveTimer: null,
            pendingDraft: null,
            savePromise: null,
            error: "",
            treeOpen: false,
            showArchived: false,
        },
        undoByScene: new Map(),
        promptUndo: null,
        richInputType: "",
        pollTimer: null,
        planSyncTimer: null,
        planSyncPending: null,
        analysisTimer: null,
        applyPromptHistoryShortcut: null,
        ownsPromptHistoryTarget: null,
        disposed: false,
    };
    node._h3ScenePromptEditorState = state;

    // Intercept prompt undo before ComfyUI's workflow history can restore an
    // older node snapshot (which commonly resets the editor to scene 1).
    root.addEventListener("keydown", (event) => {
        const direction = promptUndoDirection(event);
        if (!direction || !state.ownsPromptHistoryTarget?.(event.target)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        state.applyPromptHistoryShortcut?.(direction, event.target);
    }, true);

    const assistant = state.assistant;
    if (PROMPT_ASSISTANT_ENABLED) {
        assistant.client = new PromptAssistantClient({
            identityKey: promptAssistantIdentityKey(node),
            onFrame: (frame) => handleAssistantFrame(frame),
            onStatus: (status, detail) => {
                assistant.status = status;
                if (status === "connected") {
                    clearAssistantReconnect();
                    assistant.statusDetail = "Connected · isolated prompt session";
                } else if (status === "connecting") {
                    assistant.statusDetail = "Connecting to comfyui-mcp…";
                } else if (assistant.activeRequest) {
                    assistant.statusDetail = "Disconnected · reconnecting to recover the active draft…";
                    scheduleAssistantReconnect();
                } else {
                    assistant.statusDetail = "Disconnected · send to reconnect";
                }
                if (detail?.providers) assistant.providers = detail.providers;
                refreshAssistant();
            },
        });
        assistant.pendingStorageKey = `h3.prompt-assistant.pending.${assistant.client.identity}`;
    }

    function dirty() {
        node.graph?.setDirtyCanvas?.(true, true);
        app.graph?.setDirtyCanvas?.(true, true);
    }

    function persistView() {
        node.properties[ACTIVE_SCENE_PROPERTY] = state.active;
        node.properties[FONT_SIZE_PROPERTY] = state.fontSize;
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
        node.properties[ACTIVE_SCENE_PROPERTY] = index;
        return true;
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
                if (pending.status) {
                    pending.status.textContent =
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
        if (pending.status) pending.status.textContent = pending.message;
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
        if (pending.status) pending.status.textContent = "Editing…";
    }

    function schedulePromptAnalysis() {
        if (state.disposed) return;
        if (state.analysisTimer != null) {
            window.clearTimeout(state.analysisTimer);
        }
        state.analysisTimer = window.setTimeout(() => {
            state.analysisTimer = null;
            refreshAssistant();
            state.schema?.refresh();
        }, PROMPT_ANALYSIS_DELAY_MS);
    }

    function flushPromptAnalysis() {
        if (state.analysisTimer == null) return;
        window.clearTimeout(state.analysisTimer);
        state.analysisTimer = null;
        refreshAssistant();
        state.schema?.refresh();
    }

    function commitPlan(
        status, message = "Saved to connected Plan",
        {deferEffects = false} = {},
    ) {
        const value = planToJson(state.plan);
        state.lastValue = value;
        state.planWidget.value = value;
        const pending = {
            value,
            sceneIndex:state.active,
            prompt:promptValueToText(state.plan.shots[state.active]?.prompt),
            status,
            message,
        };
        if (deferEffects) schedulePlanEffects(pending);
        else {
            state.planSyncPending = pending;
            flushPlanEffects();
        }
    }

    function writePlan(status, options = {}) {
        if (!state.plan || !state.planWidget || !state.planNode) return;
        // This node owns only one scene prompt. Rebase when another UI changed
        // the live Plan so concurrent Studio edits are preserved, while
        // consecutive keystrokes avoid reparsing the full JSON.
        const liveValue = String(state.planWidget.value ?? "");
        if (liveValue !== state.lastValue && !rebaseActivePromptOntoLivePlan()) {
            if (status) status.textContent = "Plan structure changed; waiting to resynchronize";
            return;
        }
        commitPlan(status, "Saved to connected Plan", options);
    }

    function planRunName() {
        return String(state.planNode?.widgets?.find(
            (item) => item.name === "run_name",
        )?.value ?? "").trim();
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

    async function historyRequest(query = {}, body = null) {
        const suffix = new URLSearchParams(query).toString();
        const response = await api.fetchApi(
            `/minimax_h3_context_loop/prompt-history${suffix ? `?${suffix}` : ""}`,
            body == null ? undefined : await projectMutationOptions(node,
                body.run_name ?? query.run_name ?? "", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(body),
            }),
        );
        let payload = {};
        try {
            payload = await response.json();
        } catch (_error) {
            // A proxy can replace a backend error with a non-JSON response.
        }
        if (!response.ok) {
            throw new Error(payload.error || `Prompt history request failed (HTTP ${response.status}).`);
        }
        return payload;
    }

    function renderHistory() {
        const history = state.history;
        const host = history.host;
        if (!host) return;
        host.replaceChildren();
        if (history.error) {
            host.append(element("span", "h3sp-history-meta h3sp-history-error", history.error));
            return;
        }
        if (!history.data) {
            host.append(element("span", "h3sp-history-meta", "Loading prompt versions…"));
            return;
        }
        const navigation = promptRevisionNavigation(history.data, history.revisionId);
        const controls = element("span", "h3sp-history-nav");
        const previous = button("‹", "Activate previous prompt version in the Plan", () => {
            if (navigation.previous) void selectHistoryRevision(navigation.previous.id);
        });
        const count = element(
            "span", "h3sp-history-count",
            `Active ${navigation.position} / ${navigation.total}`,
        );
        const next = button("›", "Activate next prompt version in the Plan", () => {
            if (navigation.next) void selectHistoryRevision(navigation.next.id);
        });
        previous.disabled = !navigation.previous;
        next.disabled = !navigation.next;
        controls.append(previous, count, next);
        const treeDetails = renderHistoryTree();
        const metadata = element(
            "span", "h3sp-history-meta", promptRevisionLabel(navigation),
        );
        metadata.title = promptRevisionHelp(navigation);
        host.append(controls, treeDetails, metadata);
    }

    function renderHistoryTree() {
        const history = state.history;
        const details = element("details", "h3sp-history-tree");
        details.open = history.treeOpen;
        const tree = promptRevisionTree(history.data, {
            includeArchived: history.showArchived,
        });
        const summary = element("summary", "", `History (${tree.rows.length})`);
        summary.title = "Show prompt ancestry, labels, and revision actions";
        const panel = element("div", "h3sp-history-tree-panel");
        for (const row of tree.rows) {
            const rowHost = element(
                "div",
                `h3sp-history-tree-row${row.isActive ? " h3sp-history-active" : ""}${row.isArchived ? " h3sp-history-archived" : ""}`,
            );
            rowHost.style.paddingLeft = `${row.depth * 15}px`;
            const select = button("", `Activate ${row.displayLabel} in the Plan`, () => {
                if (!row.isActive) void selectHistoryRevision(row.revision.id);
            });
            select.className = "h3sp-history-tree-select";
            select.disabled = row.isActive;
            select.append(
                element("span", "h3sp-history-branch", row.depth ? "↳" : "●"),
                element("span", "h3sp-history-tree-label", row.displayLabel),
                element(
                    "span", "h3sp-history-tree-badge",
                    row.isArchived ? "Archived"
                        : row.isActive ? "Active"
                            : row.isExecuted ? "Executed" : "Draft",
                ),
            );
            const rename = button("Label", `Label ${row.displayLabel}`, () => {
                const label = window.prompt(
                    "Short label for this prompt revision (leave blank to clear):",
                    String(row.revision.label ?? ""),
                );
                if (label != null) void mutateHistoryRevision(
                    "label", row.revision.id, {label});
            });
            rename.className = "h3sp-history-tree-action";
            rowHost.append(select, rename);
            if (row.canRestore) {
                const restore = button("Restore", `Restore ${row.displayLabel} to visible history`, () => {
                    void mutateHistoryRevision("archive", row.revision.id, {archived:false});
                });
                restore.className = "h3sp-history-tree-action";
                rowHost.append(restore);
            } else if (row.canArchive) {
                const archive = button("Archive", `Archive ${row.displayLabel}`, () => {
                    void mutateHistoryRevision("archive", row.revision.id, {archived:true});
                });
                archive.className = "h3sp-history-tree-action";
                rowHost.append(archive);
            }
            if (row.canDelete) {
                const remove = button("Delete", `Delete unexecuted leaf ${row.displayLabel}`, () => {
                    if (window.confirm(`Delete ${row.displayLabel}? This cannot be undone.`)) {
                        void mutateHistoryRevision("delete", row.revision.id);
                    }
                });
                remove.className = "h3sp-history-tree-action";
                rowHost.append(remove);
            }
            panel.append(rowHost);
        }
        if (!tree.rows.length) {
            panel.append(element("div", "h3sp-history-tree-tools", "No visible revisions"));
        }
        const tools = element("div", "h3sp-history-tree-tools");
        tools.append(element(
            "span", "",
            "↳ shows parent → child progression",
        ));
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
        details.addEventListener("toggle", () => {
            history.treeOpen = details.open;
        });
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
                action,
                run_name: runName,
                scene_id: shotId,
                revision: revisionId,
                ...fields,
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
            history.error = "Set a Plan run_name to enable prompt history.";
            renderHistory();
            return;
        }
        const request = synchronize
            ? historyRequest({}, {
                action: "save",
                run_name: runName,
                scene_id: shotId,
                prompt,
                parent_revision: null,
            })
            : historyRequest({run_name: runName, scene_id: shotId});
        history.loadPromise = request;
        try {
            const payload = await request;
            if (token !== history.loadToken || history.sceneKey !== key) return;
            history.data = payload.history ?? payload;
            history.revisionId = payload.revision?.id
                ?? history.data.active_revision ?? history.revisionId;
            history.error = "";
        } catch (error) {
            if (token !== history.loadToken || history.sceneKey !== key) return;
            history.error = error?.message || String(error);
        } finally {
            if (history.loadPromise === request) history.loadPromise = null;
            if (token === history.loadToken && history.sceneKey === key) renderHistory();
        }
    }

    function scheduleHistoryDraft(shotId, prompt) {
        if (state.disposed) return;
        const history = state.history;
        const runName = planRunName();
        if (!runName) return;
        history.pendingDraft = {
            key: historySceneKey(runName, shotId),
            runName,
            shotId,
            prompt,
        };
        history.error = "";
        if (history.saveTimer != null) window.clearTimeout(history.saveTimer);
        history.saveTimer = window.setTimeout(() => {
            history.saveTimer = null;
            void flushHistoryDraft();
        }, 650);
    }

    async function flushHistoryDraft() {
        const history = state.history;
        if (history.saveTimer != null) {
            window.clearTimeout(history.saveTimer);
            history.saveTimer = null;
        }
        if (history.savePromise) {
            await history.savePromise;
            return history.pendingDraft ? flushHistoryDraft() : undefined;
        }
        const draft = history.pendingDraft;
        if (!draft) return;
        history.pendingDraft = null;
        if (history.loadPromise && history.sceneKey === draft.key) {
            await history.loadPromise;
        }
        const parent = history.sceneKey === draft.key ? history.revisionId : null;
        const request = historyRequest({}, {
            action: "save",
            run_name: draft.runName,
            scene_id: draft.shotId,
            prompt: draft.prompt,
            parent_revision: parent,
        });
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
            if (history.sceneKey === draft.key) {
                history.error = error?.message || String(error);
                renderHistory();
            }
        } finally {
            if (history.savePromise === request) history.savePromise = null;
        }
        if (history.pendingDraft) await flushHistoryDraft();
    }

    async function selectHistoryRevision(revisionId) {
        await flushHistoryDraft();
        const history = state.history;
        const shot = state.plan?.shots?.[state.active];
        if (!shot || !history.textarea) return;
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const runName = planRunName();
        const key = historySceneKey(runName, shotId);
        try {
            const payload = await historyRequest({}, {
                action: "activate",
                run_name: runName,
                scene_id: shotId,
                revision: revisionId,
            });
            if (history.sceneKey !== key) return;
            history.data = payload.history;
            history.revisionId = payload.revision.id;
            history.error = "";
            history.textarea.value = String(payload.revision.prompt ?? "");
            state.promptUndo?.record(history.textarea.value, {
                inputType: "insertReplacementText",
            });
            renderRichEditorText(history.textarea.value);
            shot.prompt = promptTextToLines(history.textarea.value);
            writePlan(history.status);
            if (history.status) history.status.textContent = "Loaded prompt version";
            renderHistory();
            history.textarea.focus();
        } catch (error) {
            if (history.sceneKey !== key) return;
            history.error = error?.message || String(error);
            renderHistory();
        }
    }

    function messagesForProvider(provider = assistant.provider) {
        const key = provider === "hermes" ? "hermes" : "codex";
        assistant.messagesByProvider[key] ??= [];
        return assistant.messagesByProvider[key];
    }

    function assistantMessage(role, text, provider = assistant.provider) {
        const value = String(text ?? "").trim();
        if (!value) return;
        const messages = messagesForProvider(provider);
        messages.push({role, text: value});
        assistant.messagesByProvider[provider === "hermes" ? "hermes" : "codex"] = messages.slice(-24);
    }

    function clearAssistantReconnect() {
        if (assistant.reconnectTimer != null) {
            window.clearTimeout(assistant.reconnectTimer);
            assistant.reconnectTimer = null;
        }
    }

    function scheduleAssistantReconnect(delay = 500) {
        if (!assistant.activeRequest || assistant.reconnectTimer != null) return;
        assistant.reconnectTimer = window.setTimeout(async () => {
            assistant.reconnectTimer = null;
            if (!assistant.activeRequest) return;
            try {
                await assistant.client.connect();
            } catch (_error) {
                if (assistant.activeRequest) scheduleAssistantReconnect(1_000);
            }
        }, delay);
    }

    function persistPendingRequest(requestId, meta) {
        try {
            globalThis.sessionStorage?.setItem(assistant.pendingStorageKey, JSON.stringify({
                requestId,
                ...meta,
            }));
        } catch (_error) {
            // Recovery across reload is best-effort; live correlation still works.
        }
    }

    function clearPendingRequest(requestId = null) {
        if (requestId && assistant.activeRequest && assistant.activeRequest !== requestId) return;
        try { globalThis.sessionStorage?.removeItem(assistant.pendingStorageKey); } catch (_error) { /* unavailable */ }
    }

    function restorePendingRequest() {
        let stored = null;
        try {
            stored = JSON.parse(globalThis.sessionStorage?.getItem(assistant.pendingStorageKey) || "null");
        } catch (_error) {
            clearPendingRequest();
        }
        if (!stored || typeof stored !== "object" || typeof stored.requestId !== "string") return;
        const meta = {
            sceneKey: String(stored.sceneKey || ""),
            sceneId: String(stored.sceneId || ""),
            sceneIndex: Number(stored.sceneIndex),
            sourcePrompt: String(stored.sourcePrompt ?? ""),
            sourceRevision: String(stored.sourceRevision || ""),
            provider: stored.provider === "hermes" ? "hermes" : "codex",
        };
        if (!meta.sceneKey || !meta.sceneId || !Number.isInteger(meta.sceneIndex) || !meta.sourceRevision) {
            clearPendingRequest();
            return;
        }
        assistant.provider = meta.provider;
        assistant.activeRequest = stored.requestId;
        assistant.requestContexts.set(stored.requestId, meta);
        assistant.status = "connecting";
        assistant.statusDetail = "Reconnecting to recover the active draft…";
        refreshAssistant();
        scheduleAssistantReconnect(0);
    }

    function reconcileAssistantProvider() {
        // A restored in-flight request remains owned by its original provider.
        // Switching the visible lane during recovery would hide its reply in a
        // different provider transcript.
        if (assistant.activeRequest) return;
        const selected = assistant.providers?.find((item) => item.id === assistant.provider);
        if (selected?.available !== false) return;
        const fallback = assistant.providers?.find((item) => item.available !== false);
        if (!fallback || !["codex", "hermes"].includes(fallback.id)) return;
        const unavailable = assistant.provider === "hermes" ? "Hermes" : "Codex";
        assistant.provider = fallback.id;
        node.properties[ASSIST_PROVIDER_PROPERTY] = assistant.provider;
        assistantMessage("system", `${unavailable} is unavailable; using ${fallback.label || fallback.id}.`, assistant.provider);
        dirty();
    }

    function assistantProviderAvailable(provider = assistant.provider) {
        return assistant.providers?.find((item) => item.id === provider)?.available !== false;
    }

    function refreshAssistant() {
        const host = assistant.host;
        const promptTextarea = root.querySelector(".h3sp-textarea");
        if (!host || !promptTextarea || !state.plan?.shots?.length) return;
        renderAssistant(host, promptTextarea);
    }

    function handleAssistantFrame(frame) {
        if (!frame || typeof frame !== "object") return;
        if (frame.type === "prompt_assist_ready") {
            assistant.providers = Array.isArray(frame.providers) ? frame.providers : null;
            reconcileAssistantProvider();
            assistant.error = "";
        } else if (frame.type === "prompt_assist_started") {
            if (frame.request_id === assistant.activeRequest) assistant.status = "working";
        } else if (frame.type === "prompt_assist_progress") {
            if (frame.request_id === assistant.activeRequest) assistant.statusDetail = "Agent is drafting…";
        } else if (frame.type === "prompt_assist_result") {
            // Accept only a request this editor incarnation has in its live or
            // sessionStorage-restored correlation map. A disconnected "New
            // chat" can reconnect to a buffered result before its reset frame
            // reaches the server; reconstructing metadata from that result
            // would resurrect a draft the user explicitly discarded.
            const meta = assistant.requestContexts.get(frame.request_id);
            if (!meta) return;
            assistant.requestContexts.delete(frame.request_id);
            if (assistant.activeRequest === frame.request_id) assistant.activeRequest = null;
            clearPendingRequest(frame.request_id);
            clearAssistantReconnect();
            assistant.status = "connected";
            assistant.statusDetail = "Connected · isolated prompt session";
            assistant.error = "";
            assistantMessage("agent", frame.message || "Draft ready.", meta.provider);
            if (typeof frame.rewritten_prompt === "string" && frame.rewritten_prompt.trim()) {
                assistant.drafts.set(meta.sceneKey, {
                    sceneId: meta.sceneId,
                    sceneIndex: meta.sceneIndex,
                    sourcePrompt: meta.sourcePrompt,
                    sourceRevision: meta.sourceRevision,
                    proposed: frame.rewritten_prompt,
                    provider: frame.provider || meta.provider,
                });
            } else if (typeof frame.rewritten_prompt === "string") {
                assistant.error = "The agent returned an empty draft, so it was not staged.";
            }
        } else if (frame.type === "prompt_assist_error") {
            const meta = assistant.requestContexts.get(frame.request_id);
            if (frame.request_id && !meta && assistant.activeRequest !== frame.request_id) return;
            if (meta) assistant.requestContexts.delete(frame.request_id);
            if (!frame.request_id || assistant.activeRequest === frame.request_id) {
                assistant.activeRequest = null;
            }
            clearPendingRequest(frame.request_id);
            clearAssistantReconnect();
            assistant.status = assistant.client?.socket?.readyState === WebSocket.OPEN
                ? "connected" : "disconnected";
            assistant.error = String(frame.error || "Prompt assistant failed.");
            assistantMessage("system", `Agent error: ${assistant.error}`, meta?.provider);
        } else if (frame.type === "prompt_assist_cancelled") {
            const meta = assistant.requestContexts.get(frame.request_id);
            if (!meta && assistant.activeRequest !== frame.request_id) return;
            assistant.requestContexts.delete(frame.request_id);
            if (assistant.activeRequest === frame.request_id) assistant.activeRequest = null;
            clearPendingRequest(frame.request_id);
            clearAssistantReconnect();
            assistant.status = "connected";
            assistant.statusDetail = "Stopped · ready for another request";
            assistantMessage("system", "Request stopped. The scene prompt was not changed.", meta?.provider);
        } else if (
            frame.type === "prompt_assist_cancel_ack"
            && frame.cancelled === false
            && frame.request_id === assistant.activeRequest
        ) {
            // The orchestrator may have restarted while the browser retained a
            // pending request. A negative correlated ack proves there is no
            // server-side turn left to wait for.
            const meta = assistant.requestContexts.get(frame.request_id);
            assistant.requestContexts.delete(frame.request_id);
            assistant.activeRequest = null;
            clearPendingRequest(frame.request_id);
            clearAssistantReconnect();
            assistant.status = assistant.client?.socket?.readyState === WebSocket.OPEN
                ? "connected" : "disconnected";
            assistant.statusDetail = "No active server request · ready for another request";
            assistant.error = "The prior request is no longer active, likely because the bridge restarted.";
            assistantMessage("system", assistant.error, meta?.provider);
        }
        refreshAssistant();
    }

    async function sendAssistant(promptTextarea) {
        if (assistant.activeRequest || assistant.preparingRequest || !state.plan?.shots?.length) return;
        // Snapshot the selected scene before the asynchronous bridge handshake.
        // Navigation remains usable while connecting; reading state.active and
        // an old textarea after await could otherwise pair scene B's id with
        // scene A's prompt.
        const requestSceneIndex = state.active;
        const requestShot = state.plan.shots[requestSceneIndex];
        const sourcePrompt = promptTextarea.value;
        const selectedText = sourcePrompt.slice(
            promptTextarea.selectionStart ?? 0,
            promptTextarea.selectionEnd ?? 0,
        );
        const context = buildPromptAssistantContext(
            state.plan,
            requestSceneIndex,
            sourcePrompt,
            {
                includeShared: assistant.includeShared,
                includeAdjacent: assistant.includeAdjacent,
                selectedText,
            },
        );
        const preparation = {};
        assistant.preparingRequest = preparation;
        assistant.status = "connecting";
        assistant.statusDetail = "Connecting to comfyui-mcp…";
        assistant.error = "";
        refreshAssistant();
        try {
            // Wait for prompt_assist_ready before freezing the provider into the
            // request. The ready frame may switch a persisted unavailable Hermes
            // selection to Codex.
            await assistant.client.connect();
        } catch (error) {
            // New chat may have invalidated this connect attempt while it was
            // awaiting the handshake. In that case it owns no UI state.
            if (assistant.preparingRequest !== preparation) return;
            assistant.preparingRequest = null;
            assistant.status = "disconnected";
            assistant.error = error.message || String(error);
            assistantMessage("system", `Could not connect: ${assistant.error}`);
            refreshAssistant();
            return;
        }
        if (assistant.preparingRequest !== preparation) return;
        assistant.preparingRequest = null;
        if (!assistantProviderAvailable()) {
            assistant.error = `${assistant.provider === "hermes" ? "Hermes" : "Codex"} is unavailable.`;
            refreshAssistant();
            return;
        }
        const sceneId = String(requestShot.id || `clip_${String(requestSceneIndex + 1).padStart(4, "0")}`);
        const sceneKey = promptSceneKey(sceneId, requestSceneIndex);
        const requestId = `pa-${globalThis.crypto?.randomUUID?.()
            ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`;
        const request = makePromptAssistRequest({
            requestId,
            conversationId: assistant.client.conversationId,
            provider: assistant.provider,
            mode: assistant.mode,
            instruction: assistant.composer,
            context,
        });
        assistant.requestContexts.set(requestId, {
            sceneKey,
            sceneId,
            sceneIndex: requestSceneIndex,
            sourcePrompt,
            sourceRevision: request.source_revision,
            provider: assistant.provider,
        });
        const requestMeta = assistant.requestContexts.get(requestId);
        assistant.activeRequest = requestId;
        persistPendingRequest(requestId, requestMeta);
        assistant.status = "working";
        assistant.statusDetail = `Asking ${assistant.provider === "hermes" ? "Hermes" : "Codex"}…`;
        assistant.error = "";
        assistantMessage("user", request.instruction);
        assistant.composer = "";
        refreshAssistant();
        try {
            await assistant.client.send(request);
        } catch (error) {
            if (assistant.activeRequest !== requestId) return;
            assistant.activeRequest = null;
            assistant.requestContexts.delete(requestId);
            clearPendingRequest(requestId);
            clearAssistantReconnect();
            assistant.status = "disconnected";
            assistant.error = error.message || String(error);
            assistantMessage("system", `Could not send: ${assistant.error}`);
            refreshAssistant();
        }
    }

    function stopAssistant() {
        if (!assistant.activeRequest) return;
        if (!assistant.client.cancel(assistant.activeRequest)) {
            assistant.error = "The bridge is disconnected; the local request may finish without being delivered.";
            refreshAssistant();
        } else {
            assistant.statusDetail = "Stopping agent…";
            refreshAssistant();
        }
    }

    function resetAssistantChat() {
        if (assistant.activeRequest) assistant.client.cancel(assistant.activeRequest);
        assistant.client.reset();
        assistant.activeRequest = null;
        assistant.preparingRequest = null;
        clearPendingRequest();
        clearAssistantReconnect();
        assistant.requestContexts.clear();
        assistant.messagesByProvider = {codex: [], hermes: []};
        assistant.error = "";
        assistant.statusDetail = "New isolated conversation";
        refreshAssistant();
    }

    function copyAssistantDraft(draft) {
        const operation = navigator.clipboard?.writeText?.(draft.proposed);
        if (!operation) {
            assistant.error = "Clipboard access is unavailable. Select the proposed text and copy it manually.";
            refreshAssistant();
            return;
        }
        operation.then(() => {
            assistantMessage("system", "Proposed prompt copied.");
            refreshAssistant();
        }).catch(() => {
            assistant.error = "Clipboard access was refused. Select the proposed text and copy it manually.";
            refreshAssistant();
        });
    }

    function applyAssistantDraft(draft, promptTextarea, conflict) {
        if (conflict.stale && !window.confirm(
            `${conflict.reason}\n\nApply this draft anyway and replace the current scene prompt?`,
        )) return;
        const before = promptTextarea.value;
        const after = draft.proposed;
        if (!String(after).trim()) {
            assistant.error = "An empty assistant draft cannot replace the scene prompt.";
            refreshAssistant();
            return;
        }
        assistant.lastApplied = {
            sceneKey: promptSceneKey(draft.sceneId, draft.sceneIndex),
            before,
            after,
        };
        promptTextarea.value = after;
        promptTextarea.dispatchEvent(new Event("input", {bubbles: true}));
        assistant.drafts.delete(promptSceneKey(draft.sceneId, draft.sceneIndex));
        assistantMessage("system", "Draft applied to the active scene and saved in the connected Plan.");
        refreshAssistant();
    }

    function undoAssistantApply(promptTextarea, sceneKey) {
        const undo = assistant.lastApplied;
        if (!undo || undo.sceneKey !== sceneKey || promptTextarea.value !== undo.after) return;
        promptTextarea.value = undo.before;
        promptTextarea.dispatchEvent(new Event("input", {bubbles: true}));
        assistant.lastApplied = null;
        assistantMessage("system", "The last assistant apply was undone.");
        refreshAssistant();
    }

    function renderAssistant(host, promptTextarea) {
        host.replaceChildren();
        const shot = state.plan.shots[state.active];
        const sceneId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const sceneKey = promptSceneKey(sceneId, state.active);

        const head = element("div", "h3sp-assist-head");
        const heading = element("span", "h3sp-assist-title", "Prompt Assistant");
        const busy = Boolean(assistant.activeRequest || assistant.preparingRequest);
        const statusText = busy
            ? assistant.statusDetail || "Agent is working…"
            : assistant.statusDetail;
        head.append(heading, element("span", "h3sp-assist-status", statusText));

        const controls = element("div", "h3sp-assist-controls");
        const provider = element("select");
        provider.title = "Choose which isolated local agent handles this prompt turn.";
        for (const [id, label] of [["codex", "Codex"], ["hermes", "Hermes"]]) {
            const option = element("option", "", label);
            option.value = id;
            const available = assistant.providers?.find((item) => item.id === id)?.available;
            if (available === false) {
                option.textContent = `${label} (not found)`;
                option.disabled = true;
            }
            provider.append(option);
        }
        provider.value = assistant.provider;
        provider.disabled = busy;
        provider.addEventListener("change", () => {
            assistant.provider = provider.value;
            node.properties[ASSIST_PROVIDER_PROPERTY] = assistant.provider;
            persistView();
            refreshAssistant();
        });
        const mode = element("select");
        mode.title = "Choose the kind of help to request.";
        for (const item of PROMPT_ASSIST_MODES) {
            const option = element("option", "", item.label);
            option.value = item.id;
            mode.append(option);
        }
        mode.value = assistant.mode;
        mode.disabled = busy;
        mode.addEventListener("change", () => {
            assistant.mode = mode.value;
            node.properties[ASSIST_MODE_PROPERTY] = assistant.mode;
            persistView();
            refreshAssistant();
        });
        controls.append(provider, mode, button("New chat", "Clear agent conversation; staged drafts remain.", resetAssistantChat));

        const chat = element("div", "h3sp-assist-chat");
        const messages = messagesForProvider();
        if (!messages.length) {
            chat.append(element(
                "div", "h3sp-assist-empty",
                "Ask for a rewrite, continuity pass, critique, or a specific change. Nothing is applied until you press Apply.",
            ));
        } else {
            for (const message of messages) {
                chat.append(element(
                    "div",
                    `h3sp-assist-message h3sp-assist-message-${message.role}`,
                    message.text,
                ));
            }
            setTimeout(() => { chat.scrollTop = chat.scrollHeight; }, 0);
        }

        const draft = assistant.drafts.get(sceneKey);
        let draftPanel = null;
        if (draft) {
            const conflict = draftConflict(draft, sceneId, promptTextarea.value);
            draftPanel = element("div", "h3sp-assist-draft");
            const draftHead = element("div", "h3sp-assist-draft-head");
            draftHead.append(
                element("span", "h3sp-assist-draft-title", `Staged ${draft.provider || "agent"} proposal`),
                conflict.stale ? element("span", "h3sp-assist-stale", conflict.reason) : element("span"),
            );
            const proposed = element("textarea");
            proposed.value = draft.proposed;
            proposed.title = "You can edit the staged proposal before applying it.";
            proposed.addEventListener("input", () => { draft.proposed = proposed.value; });
            const original = element("details", "h3sp-assist-original");
            original.append(
                element("summary", "", "Compare with original"),
                element("pre", "", draft.sourcePrompt),
            );
            const actions = element("div", "h3sp-assist-actions");
            actions.append(
                button(
                    conflict.stale ? "Apply anyway…" : "Apply to scene",
                    "Replace the real active scene prompt in the connected Plan.",
                    () => applyAssistantDraft(draft, promptTextarea, conflict),
                ),
                button("Copy", "Copy the proposed prompt without changing the Plan.", () => copyAssistantDraft(draft)),
                button("Discard", "Remove this staged proposal without changing the Plan.", () => {
                    assistant.drafts.delete(sceneKey);
                    refreshAssistant();
                }),
            );
            draftPanel.append(draftHead, proposed, original, actions);
        }

        const contexts = element("div", "h3sp-assist-contexts");
        const contextToggle = (label, checked, change, title) => {
            const wrapper = element("label");
            wrapper.title = title;
            const input = element("input");
            input.type = "checkbox";
            input.checked = checked;
            input.disabled = busy;
            input.addEventListener("change", () => change(input.checked));
            wrapper.append(input, document.createTextNode(label));
            return wrapper;
        };
        contexts.append(
            contextToggle("Shared prompt", assistant.includeShared, (value) => {
                assistant.includeShared = value;
            }, "Include the Plan's shared/global prompt as read-only context."),
            contextToggle("Previous + next", assistant.includeAdjacent, (value) => {
                assistant.includeAdjacent = value;
            }, "Include adjacent scene prompts for continuity advice."),
            element("span", "h3sp-assist-status", "Selected text is included automatically"),
        );

        const compose = element("div", "h3sp-assist-compose");
        const composer = element("textarea");
        composer.value = assistant.composer;
        composer.placeholder = PROMPT_ASSIST_DEFAULT_INSTRUCTIONS[assistant.mode];
        composer.disabled = busy;
        composer.addEventListener("input", () => { assistant.composer = composer.value; });
        composer.addEventListener("keydown", (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                event.preventDefault();
                void sendAssistant(promptTextarea);
            }
        });
        const send = button("Ask agent", "Send (Ctrl/Cmd+Enter). The response is staged, never auto-applied.", () => {
            void sendAssistant(promptTextarea);
        });
        send.disabled = busy || !assistantProviderAvailable();
        compose.append(composer, send);
        if (assistant.activeRequest) {
            compose.append(button("Stop", "Interrupt this prompt-assist request.", stopAssistant));
        }

        const error = assistant.error
            ? element("div", "h3sp-assist-error", assistant.error) : null;
        const undo = assistant.lastApplied;
        const undoButton = undo?.sceneKey === sceneKey && promptTextarea.value === undo.after
            ? button("Undo last apply", "Restore the prompt that existed before the last assistant Apply.", () => {
                undoAssistantApply(promptTextarea, sceneKey);
            }) : null;

        host.append(head, controls, chat);
        if (draftPanel) host.append(draftPanel);
        host.append(contexts, compose);
        if (undoButton) host.append(undoButton);
        if (error) host.append(error);
    }

    function showFailure(message) {
        state.completion?.destroy();
        state.completion = null;
        assistant.host = null;
        state.promptTextarea = null;
        state.richEditor = null;
        state.history.host = null;
        state.history.textarea = null;
        state.history.status = null;
        root.replaceChildren();
        root.append(
            element("div", "h3sp-title", "MiniMax H3 Scene Prompt Editor"),
            element("div", "h3sp-error", message),
            element("div", "h3sp-context", "Connect the Plan output to this node's plan input."),
        );
        hidePopover(true);
    }

    function navigate(offset, absolute = null, {synchronize = true, focus = true} = {}) {
        if (!state.plan?.shots?.length) return;
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
            if (focus) (state.decorated ? state.richEditor : state.promptTextarea)?.focus();
        })();
    }

    async function appendScene() {
        if (!state.plan || state.plan.shots.length >= MAX_SHOTS) return;
        state.sceneNavigation = null;
        flushPlanEffects();
        flushPromptAnalysis();
        await flushHistoryDraft();
        // This is the one structural operation exposed by the focused editor.
        // Start from the live Plan so Studio/timing edits cannot be overwritten,
        // then append instead of inserting into the middle of an active chain.
        if (!rebaseActivePromptOntoLivePlan()) return;
        state.plan.shots.push(makeShot(state.plan.shots));
        state.active = state.plan.shots.length - 1;
        persistView();
        commitPlan(null);
        render();
        publishCompanionScene(node, state.planNode, state.active);
        (state.decorated ? state.richEditor : state.promptTextarea)?.focus();
    }

    function ensureTokenPopover() {
        if (state.popover) return state.popover;
        const popover = element("div", "h3sp-popover");
        popover.hidden = true;
        popover.addEventListener("mouseenter", () => {
            if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer);
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
        if (state.popoverTimer != null) {
            window.clearTimeout(state.popoverTimer);
            state.popoverTimer = null;
        }
        state.popoverPinned = false;
        for (const media of state.popover?.querySelectorAll?.("audio,video") ?? []) {
            media.pause?.();
        }
        if (state.popover) state.popover.hidden = true;
    }

    function scheduleHidePopover() {
        if (state.popoverPinned) return;
        if (state.popoverTimer != null) window.clearTimeout(state.popoverTimer);
        state.popoverTimer = window.setTimeout(hidePopover, 140);
    }

    function positionTokenPopover(popover, anchor) {
        popover.hidden = false;
        const rect = anchor.getBoundingClientRect();
        const width = Math.min(360, globalThis.innerWidth - 24);
        popover.style.left = `${Math.max(
            12, Math.min(globalThis.innerWidth - width - 12, rect.left),
        )}px`;
        popover.style.top = `${Math.max(12, Math.min(
            globalThis.innerHeight - popover.offsetHeight - 12, rect.bottom + 7,
        ))}px`;
    }

    function showTokenPopover(record, anchor) {
        if (state.popoverPinned) return;
        hidePopover(true);
        const popover = ensureTokenPopover();
        popover.replaceChildren(element("div", "h3sp-popover-title", record.token));
        const mediaKind = record.kind === "picture" ? "image" : record.kind;
        const media = referenceMediaPreview(record, mediaKind);
        if (media.url) {
            const mediaElement = element(
                mediaKind === "image" ? "img" : mediaKind === "video" ? "video" : "audio",
                "h3sp-popover-media",
            );
            mediaElement.src = media.url;
            if (mediaKind !== "image") {
                mediaElement.controls = true;
                mediaElement.preload = "metadata";
            } else {
                mediaElement.alt = `Preview for ${record.token}`;
            }
            popover.append(mediaElement);
        } else {
            popover.append(element(
                "div", "h3sp-popover-detail", "No browser-playable file preview was found upstream.",
            ));
        }
        const sourceTitle = media.source?.title || nodeType(media.source) || "unresolved source";
        popover.append(element(
            "div", "h3sp-popover-detail",
            `${record.kind.toUpperCase()} · scenes ${record.selector}\n` +
            `${record.active ? "Active" : "Inactive"} in scene ${state.active + 1}\n` +
            `Source: ${sourceTitle}`,
        ));
        positionTokenPopover(popover, anchor);
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
        const popover = ensureTokenPopover();
        const editor = element("div", "h3sp-popover-editor");
        editor.append(element("div", "h3sp-popover-title", "Replace reference"));

        const referenceField = element("label", "h3sp-popover-field");
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

        const modeField = element("label", "h3sp-popover-field");
        modeField.append(element("span", "", "Syntax"));
        const modeSelect = element("select");
        modeField.append(modeSelect);

        const timestampField = element("label", "h3sp-popover-field");
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

        const actions = element("div", "h3sp-popover-actions");
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
            const current = activePromptText();
            if (current.slice(start, end) !== part.text) {
                hidePopover(true);
                renderRichEditorText(current);
                return;
            }
            const next = replacePromptReferenceOccurrence(
                current, start, end, record, mode, timestamp,
            );
            const replacement = referenceReplacementToken(
                record, mode, timestamp,
            );
            hidePopover(true);
            if (!state.richEditor || !replacement || next === current) return;
            renderRichEditorText(next, start + replacement.length);
            state.richEditor.dispatchEvent(new InputEvent("input", {
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
            renderRichEditorText(next, start + replacement.length);
            if (state.referenceTray) renderReferenceTray(state.referenceTray);
            state.richEditor.focus();
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
        positionTokenPopover(popover, anchor);
        referenceSelect.focus();
    }

    function makeRichToken(part, start, end) {
        if (part.type === "text") return document.createTextNode(part.text);
        const kind = part.type === "reference" ? part.kind : part.type;
        const token = element("span", `h3sp-token h3sp-token-${kind}`);
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
        if (part.unresolved) token.classList.add("h3sp-token-unknown");
        if (part.record && !part.record.active) token.classList.add("h3sp-token-inactive");
        if (part.type === "section") {
            token.append(element("span", "h3sp-token-label", part.text));
            token.title = `H3 section · ${part.section}`;
            return token;
        }
        const mediaKind = kind === "picture" ? "image" : kind;
        const media = part.record
            ? referenceMediaPreview(part.record, mediaKind) : null;
        if (kind === "picture" && media?.url) {
            const thumbnail = element("img", "h3sp-token-thumb");
            thumbnail.src = media.url;
            thumbnail.alt = "";
            token.append(thumbnail);
        } else {
            token.append(icon(kind));
        }
        token.append(element("span", "h3sp-token-label", part.text));
        if (part.record) {
            token.title = part.type === "reference"
                ? `${part.text} · click to replace or edit; hover for preview`
                : `${part.text} · hover for ${kind} preview`;
            token.addEventListener("mouseenter", () => showTokenPopover(part.record, token));
            token.addEventListener("mouseleave", scheduleHidePopover);
            token.addEventListener("focus", () => showTokenPopover(part.record, token));
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

    function renderRichEditorText(text, caret = null, editableSelection = null) {
        if (!state.richEditor) return;
        const scrollTop = state.richEditor.scrollTop;
        const scrollLeft = state.richEditor.scrollLeft;
        const parts = tokenizeRichPrompt(String(text ?? ""), state.records);
        const fragment = document.createDocumentFragment();
        let sourceOffset = 0;
        for (let index = 0; index < parts.length; index += 1) {
            const part = parts[index];
            const partStart = sourceOffset;
            const partEnd = partStart + part.text.length;
            sourceOffset = partEnd;
            const unfinished = part.type === "reference" && part.unresolved
                && part.text.startsWith("@") && index === parts.length - 1
                && document.activeElement === state.richEditor;
            const editingSemanticTime = part.type === "reference" && part.semantic
                && editableSelection?.selectionStart >= partStart
                && editableSelection?.selectionEnd <= partEnd;
            fragment.append(unfinished || editingSemanticTime
                ? document.createTextNode(part.text)
                : makeRichToken(part, partStart, partEnd));
        }
        state.richEditor.replaceChildren(fragment);
        if (editableSelection?.selectionStart != null
                && editableSelection?.selectionEnd != null) {
            restoreTextSelection(
                state.richEditor,
                editableSelection.selectionStart,
                editableSelection.selectionEnd,
            );
        } else if (caret != null) restoreCaret(state.richEditor, caret);
        // replaceChildren resets a contenteditable's viewport. Prompt edits,
        // undo/redo, and reference refreshes must not throw the reader back to
        // the first line of a long scene.
        state.richEditor.scrollTop = scrollTop;
        state.richEditor.scrollLeft = scrollLeft;
    }

    function rememberPromptSelection() {
        if (state.decorated && state.richEditor) {
            const selected = editorSelectionOffsets(state.richEditor);
            if (selected) state.promptSelection = {kind:"rich", ...selected};
        } else if (state.promptTextarea) {
            const start = state.promptTextarea.selectionStart;
            const end = state.promptTextarea.selectionEnd;
            if (start != null && end != null) {
                state.promptSelection = {kind:"plain", start, end};
            }
        }
    }

    function insertPromptText(text, selectSemanticTimestamp = false) {
        const inserted = String(text ?? "");
        selectSemanticTimestamp = Boolean(
            selectSemanticTimestamp && inserted.includes("[")
            && inserted.includes("s]"));
        if (state.decorated && state.richEditor) {
            const current = editorPlainText(state.richEditor);
            const live = document.activeElement === state.richEditor
                ? editorSelectionOffsets(state.richEditor) : null;
            const saved = state.promptSelection?.kind === "rich"
                ? state.promptSelection : null;
            const selected = live ?? saved ?? {
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
            refreshReferenceState(next);
            if (selectSemanticTimestamp) {
                const first = inserted.indexOf("[") + 1;
                const last = inserted.lastIndexOf("s]");
                renderRichEditorText(next, null, {
                    selectionStart:insertionStart + first,
                    selectionEnd:insertionStart + last,
                });
                state.promptSelection = {
                    kind:"rich",
                    start:insertionStart + first,
                    end:insertionStart + last,
                };
            } else {
                const caret = insertionStart + inserted.length;
                renderRichEditorText(next, caret);
                state.promptSelection = {kind:"rich", start:caret, end:caret};
            }
            state.richEditor.dispatchEvent(new InputEvent("input", {
                bubbles:true, inputType:"insertText",
            }));
            state.richEditor.focus();
        } else if (state.promptTextarea) {
            const saved = state.promptSelection?.kind === "plain"
                ? state.promptSelection : null;
            const insertionStart = saved?.start
                ?? state.promptTextarea.selectionStart
                ?? state.promptTextarea.value.length;
            const insertionEnd = saved?.end
                ?? state.promptTextarea.selectionEnd
                ?? insertionStart;
            state.promptTextarea.setSelectionRange(insertionStart, insertionEnd);
            insertText(state.promptTextarea, inserted);
            if (selectSemanticTimestamp) {
                const first = inserted.indexOf("[") + 1;
                const last = inserted.lastIndexOf("s]");
                state.promptTextarea.setSelectionRange(
                    insertionStart + first, insertionStart + last,
                );
                state.promptSelection = {
                    kind:"plain",
                    start:insertionStart + first,
                    end:insertionStart + last,
                };
            } else {
                const caret = insertionStart + inserted.length;
                state.promptSelection = {kind:"plain", start:caret, end:caret};
            }
        }
    }

    function referenceSyntaxKey(record) {
        const shot = state.plan?.shots?.[state.active];
        return `${String(shot?.id ?? state.active)}:${record.tag}`;
    }

    function activePromptText() {
        return state.decorated && state.richEditor
            ? editorPlainText(state.richEditor)
            : state.promptTextarea?.value ?? "";
    }

    function refreshReferenceState(prompt = activePromptText()) {
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

    function selectedReferenceSyntax(record) {
        if (state.referenceMode !== "tagged") return "native";
        if (record.semanticOnly) return "semantic";
        if (!record.supportsSemantic) return "native";
        const promptMode = taggedPictureReferenceMode(
            activePromptText(), record.tag,
        );
        if (promptMode === "native" || promptMode === "semantic") {
            return promptMode;
        }
        return state.referenceSyntax.get(referenceSyntaxKey(record)) ?? "native";
    }

    function convertReferenceSyntax(record, mode, refs) {
        state.referenceSyntax.set(referenceSyntaxKey(record), mode);
        const current = activePromptText();
        const next = convertTaggedPictureReference(current, record.tag, mode);
        if (next !== current) {
            if (state.decorated && state.richEditor) {
                renderRichEditorText(next, next.length);
                state.richEditor.dispatchEvent(new InputEvent("input", {
                    bubbles:true, inputType:"insertReplacementText",
                }));
            } else if (state.promptTextarea) {
                state.promptTextarea.value = next;
                state.promptTextarea.dispatchEvent(new InputEvent("input", {
                    bubbles:true, inputType:"insertReplacementText",
                }));
            }
        }
        renderReferenceTray(refs);
    }

    function insertPromptDialogue() {
        if (state.decorated && state.richEditor) {
            const selection = globalThis.getSelection?.();
            const selected = selection?.rangeCount ? selection.toString() : "";
            insertPromptText(`<d>${selected}</d>`);
        } else if (state.promptTextarea) {
            insertDialogue(state.promptTextarea);
        }
    }

    function showReferencePreview(record, preview) {
        preview.replaceChildren();
        preview.classList.add("h3sp-visible");
        const mediaKind = record.kind === "picture" ? "image" : record.kind;
        const media = referenceMediaPreview(record, mediaKind);
        if (media.url) {
            const mediaElement = element(mediaKind === "image" ? "img"
                : mediaKind === "video" ? "video" : "audio",
            "h3sp-ref-preview-media");
            mediaElement.src = media.url;
            if (mediaKind !== "image") {
                mediaElement.controls = true;
                mediaElement.preload = "metadata";
            } else {
                mediaElement.alt = `Preview for ${record.token}`;
                mediaElement.loading = "lazy";
            }
            preview.append(mediaElement);
        } else {
            preview.append(element(
                "div", "h3sp-ref-preview-copy",
                "No browser-playable source was found. Dynamic tensors can " +
                "still run in the generation graph, but need an upstream loaded file for an editor preview.",
            ));
        }

        const sourceTitle = media.source?.title || nodeType(media.source) || "unresolved source";
        const mapping = record.label
            ? `${record.label} in scene ${state.active + 1}`
            : record.semanticActive
                ? `semantic anchor active in scene ${state.active + 1}`
                : `inactive in scene ${state.active + 1}`;
        const syntax = selectedReferenceSyntax(record);
        const displayToken = syntax === "semantic"
            ? taggedPictureReferenceToken(record.tag, "semantic")
            : record.token;
        const previewTitle = record.mode === "native"
            ? displayToken : `${displayToken} → ${mapping}`;
        const copy = element("div", "h3sp-ref-preview-copy");
        copy.append(
            element("div", "h3sp-ref-preview-title", previewTitle),
            document.createTextNode(
                `\n${record.kind.toUpperCase()} · ${record.selector === "prompt tag" ? "activated by prompt tag" : `scenes ${record.selector}`}` +
                `\nSource: ${sourceTitle}`,
            ),
        );
        preview.append(copy);
    }

    function renderReferenceTray(refs) {
        refs.replaceChildren();
        const referenceData = refreshReferenceState(activePromptText());
        const {mode, wrapper} = referenceData;
        const records = mode === "tagged"
            ? referenceData.records
            : referenceData.records.filter((record) => record.active);
        const preview = element("div", "h3sp-ref-preview");
        if (!records.length) {
            refs.append(element(
                "div", "h3sp-ref-help",
                wrapper
                    ? `No connected references are active in scene ${state.active + 1}.`
                    : "No connected Tagged/Scheduled Ref2VA, core Ref2VA, or core I2V/FL2V references were found. The menu does not invent unavailable labels.",
            ));
            return;
        }

        const help = mode === "tagged"
            ? "Prompt-driven references. Picture previews switch and convert between " +
              "native @tag and Qwen-only #tag; add [time] for explicit placement. " +
              "Video and audio stay native-only. " +
              "Audio never autoplays."
            : mode === "scheduled"
            ? `Scheduled references for scene ${state.active + 1}. Hover to preview; ` +
              "click to insert the optional stable @alias. It compiles to a native label; " +
              "the scheduler inserts no prompt text. Audio never autoplays."
            : mode === "native_keyframes"
              ? `Core I2V/FL2V keyframes for scene ${state.active + 1}. ` +
                "A first-scene gate is shown inactive on continuations. " +
                "Hover to preview; click to insert the native Picture label."
              : "Core Ref2VA references. Hover to preview; click to insert the " +
                "native label. Audio never autoplays.";
        refs.append(element("div", "h3sp-ref-help", help));
        const icons = {picture: "▧", video: "▶", audio: "♫"};
        for (const record of records) {
            const aliasMode = mode === "scheduled" || mode === "tagged";
            const mapping = aliasMode && record.label
                ? ` → ${record.label}` : "";
            const syntax = selectedReferenceSyntax(record);
            const displayToken = syntax === "semantic"
                ? taggedPictureReferenceToken(record.tag, "semantic")
                : record.token;
            const entry = element("div", "h3sp-ref-entry");
            const chip = button(
                `${icons[record.kind] ?? "@"} ${displayToken}${mapping}`,
                aliasMode
                    ? `Insert ${displayToken}; it activates this reference in this scene.`
                    : `Insert ${record.token} for the connected core conditioning input.`,
                () => {
                    insertPromptText(displayToken, syntax === "semantic");
                    refs.classList.remove("h3sp-open");
                },
            );
            chip.classList.add("h3sp-ref-chip");
            chip.classList.toggle("h3sp-active", record.active);
            chip.addEventListener("mouseenter", () => showReferencePreview(record, preview));
            chip.addEventListener("focus", () => showReferencePreview(record, preview));
            entry.append(chip);
            if (mode === "tagged" && record.supportsSemantic) {
                const usedMode = taggedPictureReferenceMode(
                    activePromptText(), record.tag,
                );
                const modes = element("div", "h3sp-ref-mode");
                const native = button("@", "Use native @tag and convert semantic anchors in this scene", () => {
                    convertReferenceSyntax(record, "native", refs);
                });
                const semantic = button("#", "Use untimed Qwen-only #tag; add [time] for placement", () => {
                    convertReferenceSyntax(record, "semantic", refs);
                });
                native.classList.toggle(
                    "h3sp-selected", syntax === "native" || usedMode === "mixed",
                );
                semantic.classList.toggle(
                    "h3sp-selected", syntax === "semantic" || usedMode === "mixed",
                );
                modes.append(native, semantic);
                entry.append(modes);
            }
            refs.append(entry);
        }
        refs.append(preview);
        showReferencePreview(records[0], preview);
    }

    function render() {
        if (!state.plan?.shots?.length) {
            showFailure("The connected Plan has no scenes.");
            return;
        }
        hidePopover(true);
        state.completion?.destroy();
        state.completion = null;
        state.schema = null;
        state.active = Math.max(0, Math.min(state.active, state.plan.shots.length - 1));
        root.style.setProperty("--h3sp-font-size", `${state.fontSize}px`);
        assistant.host = null;
        root.replaceChildren();

        const shot = state.plan.shots[state.active];
        const shotId = String(shot.id || `clip_${String(state.active + 1).padStart(4, "0")}`);
        const head = element("div", "h3sp-head");
        head.append(
            element("span", "h3sp-title", "Scene Prompt Editor"),
            element("span", "h3sp-context", sharedPrompt(state.plan).text.trim()
                ? "Shared prompt active (unchanged)" : "No shared prompt"),
        );

        const nav = element("div", "h3sp-nav");
        const previous = button("←", "Previous scene (Alt+Left)", () => navigate(-1));
        const next = button("→", "Next scene (Alt+Right)", () => navigate(1));
        const add = button("+", "Append a new scene and select it", () => void appendScene());
        previous.disabled = state.active === 0;
        next.disabled = state.active === state.plan.shots.length - 1;
        add.disabled = state.plan.shots.length >= MAX_SHOTS;
        const sceneSelect = element("select");
        for (let index = 0; index < state.plan.shots.length; index += 1) {
            const option = element("option", "", `Scene ${index + 1} — ${state.plan.shots[index].id || `clip_${String(index + 1).padStart(4, "0")}`}`);
            option.value = String(index);
            sceneSelect.append(option);
        }
        sceneSelect.value = String(state.active);
        sceneSelect.title = "Jump directly to another scene prompt.";
        sceneSelect.addEventListener("change", () => navigate(0, sceneSelect.value));

        const font = element("div", "h3sp-font");
        const fontValue = element("span", "h3sp-font-value", `${state.fontSize}px`);
        const smaller = button("A−", "Decrease editor font size", () => {
            state.fontSize = clamp(state.fontSize - 2, MIN_FONT_SIZE, MAX_FONT_SIZE, DEFAULT_FONT_SIZE);
            persistView();
            render();
        });
        const larger = button("A+", "Increase editor font size", () => {
            state.fontSize = clamp(state.fontSize + 2, MIN_FONT_SIZE, MAX_FONT_SIZE, DEFAULT_FONT_SIZE);
            persistView();
            render();
        });
        smaller.disabled = state.fontSize <= MIN_FONT_SIZE;
        larger.disabled = state.fontSize >= MAX_FONT_SIZE;
        font.append(smaller, fontValue, larger);
        nav.append(previous, sceneSelect, next, add, font);

        const textarea = element("textarea", "h3sp-textarea");
        textarea.value = promptValueToText(shot.prompt, `Scene ${state.active + 1} prompt`);
        textarea.placeholder = "Write this scene's action, camera, performance, dialogue, and ending continuity…";
        textarea.spellcheck = true;
        textarea.title = "This is the actual active scene prompt in the connected H3 Chain Plan.";
        textarea.classList.toggle("h3sp-hidden", state.decorated);
        state.promptTextarea = textarea;
        state.promptUndo = promptUndoForScene(shotId, textarea.value);

        const referenceData = availableReferenceRecords(
            node, state.active + 1, {includeInactive:true, prompt: [
                sharedPrompt(state.plan).text.trim(), textarea.value.trim(),
            ].filter(Boolean).join("\n\n")});
        state.records = referenceData.records;
        state.referenceMode = referenceData.mode;
        const editorShell = element("div", "h3sp-editor-shell");
        editorShell.classList.toggle("h3sp-hidden", !state.decorated);
        const richEditor = element("div", "h3sp-rich-editor");
        richEditor.contentEditable = "true";
        richEditor.spellcheck = true;
        richEditor.tabIndex = 0;
        richEditor.setAttribute("role", "textbox");
        richEditor.setAttribute("aria-multiline", "true");
        richEditor.dataset.placeholder = textarea.placeholder;
        richEditor.title = textarea.title;
        state.richEditor = richEditor;
        renderRichEditorText(textarea.value);
        editorShell.append(richEditor);
        state.promptSelection = {
            kind:state.decorated ? "rich" : "plain",
            start:textarea.value.length,
            end:textarea.value.length,
        };

        const tools = element("div", "h3sp-tools");
        const refs = element("div", "h3sp-refs");
        state.referenceTray = refs;
        const referenceButton = button(
            "@ Reference",
            "Browse connected Ref2VA media; typing @ opens inline autocomplete",
            () => {
                state.completion?.hide();
                const opening = !refs.classList.contains("h3sp-open");
                if (opening) renderReferenceTray(refs);
                refs.classList.toggle("h3sp-open", opening);
            },
        );
        referenceButton.addEventListener("pointerdown", rememberPromptSelection);
        const dialogueButton = button("Dialogue", "Wrap selection in <d> dialogue tags", () => {
            insertPromptDialogue();
        });
        const presentationButton = button(
            state.decorated ? "Rich text" : "Plain text",
            state.decorated
                ? "Show the same prompt as plain text"
                : "Color recognized H3 references, subjects, and dialogue tags",
            () => {
                if (state.decorated && state.richEditor) {
                    textarea.value = editorPlainText(state.richEditor);
                }
                state.decorated = !state.decorated;
                persistView();
                render();
                (state.decorated ? state.richEditor : state.promptTextarea)?.focus();
            },
        );
        presentationButton.classList.toggle("h3sp-presentation-active", state.decorated);
        tools.append(
            referenceButton,
            dialogueButton,
            presentationButton,
            element("span", "h3sp-hint",
                "Alt+←/→ scenes · type @ # < [ ( · Ctrl/Cmd+Space for all"),
        );
        const footer = element("div", "h3sp-footer");
        const identity = element(
            "span", "", `Scene ${state.active + 1}/${state.plan.shots.length} · ${shotId}`,
        );
        const status = element("span", "h3sp-footer-status", "Synchronized with Plan");
        const historyHost = element("div", "h3sp-history");
        footer.append(identity, historyHost, status);
        state.history.host = historyHost;
        state.history.textarea = textarea;
        state.history.status = status;

        const applyPromptUndo = (direction, target) => {
            const text = state.promptUndo?.[direction]?.();
            if (text == null) return false;
            const scrollTop = target.scrollTop;
            const scrollLeft = target.scrollLeft;
            const richCaret = target === richEditor
                ? selectionTextOffset(richEditor) : null;
            textarea.value = text;
            shot.prompt = promptTextToLines(text);
            writePlan(status);
            scheduleHistoryDraft(shotId, text);
            renderRichEditorText(
                text,
                richCaret == null ? null : Math.min(richCaret, text.length),
            );
            if (target === textarea) textarea.setSelectionRange(text.length, text.length);
            target.focus({preventScroll:true});
            target.scrollTop = scrollTop;
            target.scrollLeft = scrollLeft;
            refreshAssistant();
            state.schema?.refresh();
            return true;
        };
        state.ownsPromptHistoryTarget = (target) => (
            target === textarea || target === richEditor || richEditor.contains(target)
        );
        state.applyPromptHistoryShortcut = (direction, target) => applyPromptUndo(
            direction, target === textarea ? textarea : richEditor,
        );
        const handlePromptUndo = (event, target) => {
            const direction = promptUndoDirection(event);
            if (!direction) return false;
            event.preventDefault();
            applyPromptUndo(direction, target);
            return true;
        };

        textarea.addEventListener("input", (event) => {
            state.promptUndo?.record(textarea.value, {
                inputType:event.inputType || state.richInputType,
            });
            shot.prompt = promptTextToLines(textarea.value);
            writePlan(status, {deferEffects:true});
            scheduleHistoryDraft(shotId, textarea.value);
            if (document.activeElement !== richEditor) renderRichEditorText(textarea.value);
            state.completion?.refresh();
            schedulePromptAnalysis();
        });
        for (const eventName of ["focus", "pointerup", "keyup", "select"]) {
            textarea.addEventListener(eventName, rememberPromptSelection);
        }
        textarea.addEventListener("keydown", (event) => {
            if (state.completion?.handleKeydown(event)) {
                return;
            } else if (handlePromptUndo(event, textarea)) {
                return;
            } else if (event.altKey && event.key === "ArrowLeft") {
                event.preventDefault();
                navigate(-1);
            } else if (event.altKey && event.key === "ArrowRight") {
                event.preventDefault();
                navigate(1);
            }
        });
        textarea.addEventListener("blur", () => {
            flushPlanEffects();
            flushPromptAnalysis();
        });

        richEditor.addEventListener("input", (event) => {
            state.richInputType = event.inputType || "";
            try {
                textarea.value = editorPlainText(richEditor);
                textarea.dispatchEvent(new Event("input", {bubbles: true}));
            } finally {
                state.richInputType = "";
            }
        });
        for (const eventName of ["focus", "pointerup", "keyup"]) {
            richEditor.addEventListener(eventName, rememberPromptSelection);
        }
        richEditor.addEventListener("beforeinput", (event) => {
            if (event.inputType === "insertParagraph" || event.inputType === "insertLineBreak") {
                event.preventDefault();
                insertPlainText(richEditor, "\n");
            }
        });
        richEditor.addEventListener("paste", (event) => {
            event.preventDefault();
            insertPlainText(richEditor, event.clipboardData?.getData("text/plain") ?? "");
            // Decorate pasted tokens now, retaining the existing text-level
            // undo transaction and routing the save through the input handler.
            renderRichEditorText(editorPlainText(richEditor, {trimFinalNewline:false}), selectionTextOffset(richEditor));
            state.completion?.refresh();
        });
        richEditor.addEventListener("copy", (event) => copyEditorSelection(richEditor, event));
        richEditor.addEventListener("cut", (event) => copyEditorSelection(richEditor, event, true));
        richEditor.addEventListener("keydown", (event) => {
            if (state.completion?.handleKeydown(event)) {
                return;
            } else if (handlePromptUndo(event, richEditor)) {
                return;
            } else if (event.altKey && event.key === "ArrowLeft") {
                event.preventDefault();
                navigate(-1);
            } else if (event.altKey && event.key === "ArrowRight") {
                event.preventDefault();
                navigate(1);
            } else if (event.key === "Escape") {
                refs.classList.remove("h3sp-open");
            }
        });
        richEditor.addEventListener("blur", () => {
            flushPlanEffects();
            flushPromptAnalysis();
            renderRichEditorText(editorPlainText(richEditor));
        });

        const replacePromptText = (result, message = "H3 edit saved to Plan") => {
            const text = String(result.text ?? "");
            const refreshed = availableReferenceRecords(
                node, state.active + 1, {
                    includeInactive:true,
                    prompt:[sharedPrompt(state.plan).text.trim(), text.trim()]
                        .filter(Boolean).join("\n\n"),
                },
            );
            state.records = refreshed.records;
            state.referenceMode = refreshed.mode;
            textarea.value = text;
            if (state.decorated) {
                state.richInputType = "insertReplacementText";
                try {
                    textarea.dispatchEvent(new Event("input", {bubbles:true}));
                } finally {
                    state.richInputType = "";
                }
                renderRichEditorText(text, result.caret, result);
                richEditor.focus();
            } else {
                textarea.setSelectionRange(
                    result.selectionStart ?? result.caret,
                    result.selectionEnd ?? result.caret,
                );
                textarea.dispatchEvent(new InputEvent("input", {
                    bubbles:true,
                    inputType:"insertReplacementText",
                }));
                textarea.focus();
            }
            status.textContent = message;
        };

        state.schema = createH3PromptSchemaController({
            node,
            propertyPrefix:"h3_scene_prompt_editor",
            getText:() => state.decorated
                ? editorPlainText(richEditor) : textarea.value,
            replaceText:replacePromptText,
            focusAt:(caret) => {
                const position = Math.max(0, Math.min(textarea.value.length, Number(caret) || 0));
                if (state.decorated) {
                    renderRichEditorText(textarea.value, position);
                    richEditor.focus();
                } else {
                    textarea.focus();
                    textarea.setSelectionRange(position, position);
                }
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
        if (state.schema) {
            tools.prepend(state.schema.modeSelect, state.schema.toggle);
            identity.append(document.createTextNode(" · "), state.schema.counts);
        }

        const activeInput = state.decorated ? richEditor : textarea;
        state.completion = createPromptCompletionController({
            input:activeInput,
            maxItems:40,
            getText:() => state.decorated
                ? editorPlainText(richEditor) : textarea.value,
            getCaret:() => state.decorated
                ? selectionTextOffset(richEditor) : textarea.selectionStart,
            getRecords:() => state.records,
            getReferenceMode:() => state.referenceMode,
            getMode:() => state.schema?.getMode() ?? "auto",
            getAutomaticSuggestions:() => promptEditorPreferences().automaticSuggestions,
            getAppendCompletionSpace:() => promptEditorPreferences().appendCompletionSpace,
            getMarkerReplacement:() => promptEditorPreferences().markerReplacement,
            replaceText:(result) => replacePromptText(result, "Completion saved to Plan"),
        });
        bindPromptMarkerInteractions({
            input:activeInput, completion:state.completion,
            getText:() => activePromptText(),
            getCaret:() => state.decorated ? selectionTextOffset(richEditor) : textarea.selectionStart,
            readFragment:(fragment) => editorPlainText(fragment, {trimFinalNewline:false}),
            restoreCaret:(position) => state.decorated ? restoreCaret(richEditor, position)
                : textarea.setSelectionRange(position, position),
            getEnabled:() => promptEditorPreferences().markerReplacement,
            getRecords:() => state.records,
            beforeOpen:() => hidePopover(true),
        });

        root.append(head, nav, tools);
        if (state.schema) root.append(state.schema.panel);
        root.append(refs, textarea, editorShell);
        if (PROMPT_ASSISTANT_ENABLED) {
            const assistantHost = element("div", "h3sp-assist");
            assistant.host = assistantHost;
            renderAssistant(assistantHost, textarea);
            root.append(assistantHost);
        }
        root.append(footer);
        void loadHistory(shotId, textarea.value);
    }

    function loadPlan(force = false) {
        if (state.disposed) return;
        const planNode = upstreamPlanNode(node);
        const planWidget = planNode?.widgets?.find((item) => item.name === "plan_json");
        if (!planNode || !planWidget) {
            if (force || state.planNode) {
                state.plan = null;
                state.planNode = null;
                state.planWidget = null;
                state.lastValue = "";
                state.lastRunName = "";
                showFailure("No connected H3 Chain Plan was found.");
            }
            return;
        }
        const value = String(planWidget.value ?? "");
        const runName = String(planNode.widgets?.find(
            (item) => item.name === "run_name",
        )?.value ?? "").trim();
        if (!force && planNode === state.planNode && value === state.lastValue
            && runName === state.lastRunName) return;
        try {
            const previousPlan = state.plan;
            const previousActive = state.active;
            const nextPlan = parsePlanJson(value);
            state.active = activeSceneIndexAfterRefresh(
                previousPlan, nextPlan, previousActive,
            );
            node.properties[ACTIVE_SCENE_PROPERTY] = state.active;
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

    const widget = node.addDOMWidget(
        "h3_scene_prompt_editor", "h3-scene-prompt-editor", root,
        {
            serialize: false,
            hideOnZoom: false,
            getMinHeight: () => PROMPT_ASSISTANT_ENABLED ? 760 : 420,
        },
    );
    widget.serialize = false;
    const minimumWidth = PROMPT_ASSISTANT_ENABLED ? 760 : 700;
    const minimumHeight = PROMPT_ASSISTANT_ENABLED ? 900 : 620;
    const currentWidth = Number(node.size?.[0]);
    const currentHeight = Number(node.size?.[1]);
    // Undo only the exact assistant-era default dimensions. Preserve any node
    // the user deliberately made larger.
    const targetWidth = !PROMPT_ASSISTANT_ENABLED && currentWidth === 760
        ? minimumWidth : Math.max(currentWidth || minimumWidth, minimumWidth);
    const targetHeight = !PROMPT_ASSISTANT_ENABLED && currentHeight === 900
        ? minimumHeight : Math.max(currentHeight || minimumHeight, minimumHeight);
    node.setSize?.([
        targetWidth,
        targetHeight,
    ]);

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
        // Current Shot marks the revision on the backend before emitting this
        // event. Reload only the lightweight index; prompt content stays lazy.
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
        if (!state.plan?.shots?.length) return;
        const prompt = activePromptText();
        const selection = state.decorated && state.richEditor
            && document.activeElement === state.richEditor
            ? editorSelectionOffsets(state.richEditor) : null;
        refreshReferenceState(prompt);
        if (state.decorated && state.richEditor) {
            renderRichEditorText(prompt, null, selection ? {
                selectionStart:selection.start,
                selectionEnd:selection.end,
            } : null);
        }
        if (state.referenceTray?.classList.contains("h3sp-open")) {
            renderReferenceTray(state.referenceTray);
        }
        state.completion?.refresh();
        state.schema?.refresh();
    };
    globalThis.addEventListener?.(
        PROJECT_ASSET_CATALOG_CHANGED_EVENT, onProjectAssetCatalogChanged,
    );

    node._h3FlushProjectWrites = async (expectedRun = runName()) => {
        const run = String(expectedRun ?? "").trim();
        const draftRun = String(state.history.pendingDraft?.runName ?? "");
        if (!run || !draftRun || draftRun === run) await flushHistoryDraft();
    };
    function onProjectOwnershipChanged(payload) {
        if (state.disposed || payload?.owned_by_requester !== true) return;
        const currentRun = planRunName();
        const history = state.history;
        const prefix = `${currentRun}\u0000`;
        if (!currentRun || payload.run_name !== currentRun
                || !history.sceneKey.startsWith(prefix)
                || !isProjectReadOnlyError(history.error, currentRun)) return;
        const sceneId = history.sceneKey.slice(prefix.length);
        const status = history.status ?? state.status;
        if (status) status.textContent =
            "Write access restored. The blocked action was not retried.";
        // Refresh only the history controls. Never replay a denied mutation,
        // rebuild the editor, move its selection, or replace the user's text.
        void loadHistory(sceneId, "", false);
    }
    const unsubscribeOwnership = subscribeProjectOwnership(node, onProjectOwnershipChanged);
    const removed = node.onRemoved;
    node.onRemoved = function () {
        // Workflow removal runs inside ComfyUI's tab/session teardown. The
        // Plan value is already updated synchronously by commitPlan(), so only
        // cancel deferred work here; callbacks and analysis can race teardown.
        state.disposed = true;
        unsubscribeOwnership();
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
        ];
        if (PROMPT_ASSISTANT_ENABLED) {
            assistant.preparingRequest = null;
            cleanups.push(
                () => clearAssistantReconnect(),
                () => clearPendingRequest(),
                () => assistant.client?.close(),
            );
        }
        for (const cleanup of cleanups) {
            try { cleanup(); }
            catch (error) { console.warn("H3 Scene Prompt Editor cleanup failed", error); }
        }
        state.completion = null;
        delete node._h3PromptCompanionSetActiveScene;
        delete node._h3PromptCompanionSetScenePrompt;
        delete node._h3FlushProjectWrites;
        return removed?.apply(this, arguments);
    };
    node._h3PromptCompanionSetActiveScene = (planNode, index) => {
        if (planNode !== state.planNode || !state.plan?.shots?.length) return false;
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
        if (index === state.active) {
            const textarea = root.querySelector(".h3sp-textarea");
            if (textarea && textarea.value !== text) {
                const focused = document.activeElement === textarea;
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                const richFocused = document.activeElement === state.richEditor;
                const richCaret = richFocused ? selectionTextOffset(state.richEditor) : null;
                textarea.value = text;
                if (focused) textarea.setSelectionRange(
                    Math.min(start, text.length), Math.min(end, text.length));
                renderRichEditorText(
                    text,
                    richCaret == null ? null : Math.min(richCaret, text.length),
                );
                scheduleHistoryDraft(
                    String(state.plan.shots[index].id || `clip_${String(index + 1).padStart(4, "0")}`),
                    text);
                refreshAssistant();
                state.schema?.refresh();
            }
        }
        if (livePlanParsed) state.lastValue = liveValue;
        return true;
    };
    node._h3ScenePromptEditorRefresh = () => loadPlan(true);
    state.pollTimer = window.setInterval(() => loadPlan(false), 500);
    loadPlan(true);
    if (PROMPT_ASSISTANT_ENABLED) restorePendingRequest();
}

app.registerExtension({
    name: "minimax_h3_context_loop.scene_prompt_editor",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
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
            if (nodeType(node) === NODE_NAME) {
                setTimeout(() => node._h3ScenePromptEditorRefresh?.(), 0);
            }
        }
    },
});
