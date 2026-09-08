import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    H3_CONTEXT_LENGTHS,
    MAX_SHOTS,
    automaticSceneColor,
    calculatePlanTiming,
    derivedSceneSeed,
    duplicateShot,
    formatClock,
    makeShot,
    moveShot,
    removePlanShot,
    parsePlanJson,
    planToJson,
    promptTextToLines,
    promptValueToText,
    randomSceneSeed,
    safeShotId,
    sceneContextLength,
    sceneContinuationMode,
    sceneLoRARoute,
    sceneVisualContextSource,
    sceneVisualContextLeadFrames,
    sceneVisualContextLeadSource,
    scenePromptSeedMode,
    sceneVideoBlendFrames,
    setScenePromptSeedMode,
    setShotLengthMode,
    setSharedPrompt,
    shotLengthMode,
    sharedPrompt,
    visualContextCompositions,
} from "./h3_chain_plan_core.mjs?v=0.6.8";
import {availableReferenceRecords} from "./h3_reference_preview_core.mjs?v=0.6.8";
import {
    applySceneAudioOverride,
    applySceneLipSync,
    sceneLipSyncMode,
    applySceneTransitionPreset,
    primaryTransitionOptions,
    sceneAudioOverride,
    sceneAudioPolicy,
    sceneTransitionPreset,
    transitionPresetLabel,
} from "./h3_policy_core.mjs?v=0.6.8";
import {
    resolveAudioContextLength,
    resolveAudioPolicy,
    resolveTransitionPolicy,
} from "./h3_socket_presentation_core.mjs?v=0.6.8";
import {
    availableLoRARoutes,
    loraRouteLabel,
} from "./h3_lora_scheduler_core.mjs?v=0.6.8";
import {
    MODERN_PLAN_NODE as MODERN_NODE_NAME,
    MODERN_PLAN_WIDGET_NAMES as MODERN_BACKING_WIDGETS,
    upgradeLegacyPlanNode,
} from "./h3_plan_upgrade_core.mjs?v=0.6.8";

// This scene editor is an original implementation. Its quick @ reference and
// # dialogue interactions are inspired by nkxx188/ComfyUI-MiniMaxH3-Easy,
// distributed under the MIT License. See THIRD_PARTY_NOTICES.md.

const EXTENSION = "minimax_h3_context_loop.chain_plan_editor";
const NODE_NAME = "MiniMaxH3ChainPlan";
const NODE_NAMES = new Set([NODE_NAME, MODERN_NODE_NAME]);
const MIN_WIDTH = 700;
const EDITOR_HEIGHT = 650;
const SCENE_COLOR_PROPERTY = "h3_chain_scene_colors";
const LAYOUT_PROPERTY = "h3_chain_plan_layout";

function injectStyles() {
    if (document.getElementById("h3-chain-plan-editor-style")) return;
    const style = document.createElement("style");
    style.id = "h3-chain-plan-editor-style";
    style.textContent = `
        .h3c-editor {
            --h3c-bg: color-mix(in srgb, var(--comfy-menu-bg, #202124) 90%, #111827);
            --h3c-panel: color-mix(in srgb, var(--comfy-input-bg, #111827) 84%, #24304a);
            --h3c-border: color-mix(in srgb, var(--border-color, #555) 70%, #7c8db5);
            --h3c-text: var(--input-text, #e8eaf0);
            --h3c-muted: color-mix(in srgb, var(--h3c-text) 58%, transparent);
            --h3c-accent: #7fa8ff;
            box-sizing: border-box;
            width: 100%;
            height: 100%;
            max-height: 100%;
            min-height: 0;
            overflow: auto;
            contain: layout paint;
            padding: 10px;
            border: 1px solid var(--h3c-border);
            border-radius: 8px;
            background: var(--h3c-bg);
            color: var(--h3c-text);
            font: 12px/1.35 system-ui, sans-serif;
            scrollbar-gutter: stable;
        }
        .h3c-editor *, .h3c-editor *::before, .h3c-editor *::after { box-sizing: border-box; }
        .h3c-editor input, .h3c-editor textarea, .h3c-editor select, .h3c-editor button {
            color: var(--h3c-text);
            font: inherit;
        }
        .h3c-editor input, .h3c-editor textarea, .h3c-editor select {
            width: 100%;
            min-width: 0;
            padding: 6px 7px;
            border: 1px solid var(--h3c-border);
            border-radius: 5px;
            background: var(--comfy-input-bg, #15171d);
        }
        .h3c-editor textarea { resize: vertical; line-height: 1.45; }
        .h3c-editor button {
            padding: 5px 8px;
            border: 1px solid var(--h3c-border);
            border-radius: 5px;
            background: var(--comfy-input-bg, #252832);
            cursor: pointer;
            white-space: nowrap;
        }
        .h3c-editor button:hover { border-color: var(--h3c-accent); }
        .h3c-editor button:disabled { cursor: not-allowed; opacity: .4; }
        .h3c-header, .h3c-toolbar, .h3c-card-head, .h3c-prompt-tools,
        .h3c-json-actions, .h3c-footer { display: flex; align-items: center; gap: 6px; }
        .h3c-header { justify-content: space-between; margin-bottom: 8px; }
        .h3c-header-actions { display:flex; align-items:center; justify-content:flex-end;
            gap:7px; min-width:0; }
        .h3c-open-output { display:inline-flex; align-items:center; gap:5px;
            padding:4px 7px !important; }
        .h3c-folder-icon { width:15px; height:15px; flex:none; fill:none;
            stroke:currentColor; stroke-width:1.8; stroke-linecap:round;
            stroke-linejoin:round; }
        .h3c-title { font-size: 15px; font-weight: 700; }
        .h3c-summary { color: var(--h3c-muted); text-align: right; }
        .h3c-external-plan {
            margin-bottom: 8px;
            padding: 7px 9px;
            border: 1px solid color-mix(in srgb, var(--h3c-accent) 65%, var(--h3c-border));
            border-radius: 6px;
            background: color-mix(in srgb, var(--h3c-accent) 12%, var(--h3c-panel));
            color: var(--h3c-text);
        }
        .h3c-external-plan strong { color: var(--h3c-accent); }
        .h3c-section {
            margin-bottom: 9px;
            padding: 9px;
            border: 1px solid var(--h3c-border);
            border-radius: 7px;
            background: var(--h3c-panel);
        }
        .h3c-settings { display:none; }
        .h3c-settings.h3c-open { display:block; }
        .h3c-settings-head { display:flex; align-items:center; gap:8px;
            margin-bottom:8px; }
        .h3c-settings-head strong { font-size:13px; }
        .h3c-policy-status { margin-left:auto; color:var(--h3c-muted); }
        .h3c-policy-status.h3c-missing { color:#ffb4b8; }
        .h3c-settings-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
            gap:8px; }
        .h3c-settings-group { min-width:0; padding:8px; border:1px solid var(--h3c-border);
            border-radius:6px; background:var(--h3c-bg); }
        .h3c-settings-group-title { margin-bottom:7px; color:var(--h3c-accent);
            font-weight:700; }
        .h3c-settings-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
            gap:7px; }
        .h3c-settings-fields .h3c-wide { grid-column:1 / -1; }
        .h3c-managed-note { margin-top:6px; color:var(--h3c-muted); }
        .h3c-label { display: block; margin-bottom: 4px; color: var(--h3c-muted); font-weight: 650; }
        .h3c-help { margin-top: 4px; color: var(--h3c-muted); }
        .h3c-prefix { min-height: 88px; }
        .h3c-toolbar { position: sticky; top: -10px; z-index: 4; padding: 7px 0; background: var(--h3c-bg); }
        .h3c-toolbar .h3c-spacer { flex: 1; }
        .h3c-card {
            --h3c-scene-color: var(--h3c-accent);
            margin-bottom: 9px;
            padding: 8px;
            border: 1px solid color-mix(in srgb, var(--h3c-scene-color) 58%, var(--h3c-border));
            border-left: 4px solid var(--h3c-scene-color);
            border-radius: 7px;
            background:
                linear-gradient(90deg, color-mix(in srgb, var(--h3c-scene-color) 7%, transparent), transparent 140px),
                var(--h3c-panel);
        }
        .h3c-card.h3c-invalid { border-left-color: #ff6b72; }
        .h3c-card.h3c-drag-over { outline: 2px solid var(--h3c-accent); }
        .h3c-card-head { margin-bottom: 7px; }
        .h3c-drag { cursor: grab; user-select: none; padding: 4px 3px; color: var(--h3c-muted); }
        .h3c-index { min-width: 58px; font-weight: 700; }
        .h3c-color {
            width: 23px !important;
            min-width: 23px !important;
            height: 23px;
            padding: 1px !important;
            border-radius: 50% !important;
            cursor: pointer;
        }
        .h3c-color::-webkit-color-swatch-wrapper { padding: 1px; }
        .h3c-color::-webkit-color-swatch { border: 0; border-radius: 50%; }
        .h3c-color::-moz-color-swatch { border: 0; border-radius: 50%; }
        .h3c-id { flex: 1; }
        .h3c-timing { color: var(--h3c-muted); white-space: nowrap; }
        .h3c-length-row { display: grid; grid-template-columns: 115px minmax(120px, 1fr) 190px; gap: 7px; align-items: center; margin-bottom: 7px; }
        .h3c-prompt { min-height: 112px; }
        .h3c-basic-prompt { min-height: 72px; }
        .h3c-prompt-tools { position: relative; margin: 5px 0 2px; }
        .h3c-prompt-tools .h3c-hint { color: var(--h3c-muted); margin-left: auto; }
        .h3c-ref-menu {
            display: none;
            flex-wrap: wrap;
            gap: 4px;
            margin-top: 6px;
            padding: 6px;
            border: 1px solid var(--h3c-border);
            border-radius: 6px;
            background: var(--h3c-bg);
        }
        .h3c-ref-menu.h3c-open { display: flex; }
        .h3c-ref-menu button { padding: 3px 6px; color: var(--h3c-accent); }
        .h3c-seed-control { display:grid; grid-template-columns:minmax(210px,1fr) auto auto;
            align-items:center; gap:6px; }
        .h3c-seed-status { grid-column:1 / -1; color:var(--h3c-muted);
            overflow-wrap:anywhere; }
        .h3c-boundary-fields { display:grid; grid-template-columns:minmax(220px,1fr)
            minmax(180px,.7fr); gap:7px; margin-top:8px; }
        .h3c-audio-fields { display:grid; grid-template-columns:repeat(3,minmax(150px,1fr));
            gap:7px; margin-top:8px; }
        .h3c-advanced-fields { display: none; grid-template-columns:repeat(4, minmax(140px, 1fr)); gap: 7px; margin-top: 8px; }
        .h3c-editor.h3c-show-advanced .h3c-advanced-fields { display: grid; }
        .h3c-errors { display: none; margin: 7px 0; padding: 7px; border-radius: 5px; color: #ffb4b8; background: #5d202866; white-space: pre-wrap; }
        .h3c-errors.h3c-open { display: block; }
        .h3c-json-panel { display: none; }
        .h3c-json-panel.h3c-open { display: block; }
        .h3c-json { min-height: 260px; font-family: ui-monospace, SFMono-Regular, Consolas, monospace !important; }
        .h3c-json-actions { margin-top: 6px; }
        .h3c-json-status { color: var(--h3c-muted); }
        .h3c-footer { justify-content: space-between; padding-top: 4px; color: var(--h3c-muted); }
        .h3c-footer a { color: var(--h3c-accent); }
        @media (max-width: 650px) {
            .h3c-length-row, .h3c-advanced-fields,
            .h3c-seed-control, .h3c-boundary-fields, .h3c-audio-fields,
            .h3c-settings-grid, .h3c-settings-fields {
                grid-template-columns: 1fr; }
            .h3c-settings-fields .h3c-wide { grid-column:1; }
            .h3c-seed-status { grid-column:1; }
            .h3c-card-head { flex-wrap: wrap; }
            .h3c-timing { width: 100%; }
        }
    `;
    document.head.appendChild(style);
}

function element(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action) {
    const item = element("button", "", label);
    item.type = "button";
    if (title) item.title = title;
    item.addEventListener("click", action);
    return item;
}

function folderOpenIcon() {
    const namespace = "http://www.w3.org/2000/svg";
    const icon = document.createElementNS(namespace, "svg");
    icon.classList.add("h3c-folder-icon");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    const folder = document.createElementNS(namespace, "path");
    folder.setAttribute(
        "d",
        "M3.5 8.5V6.25A1.75 1.75 0 0 1 5.25 4.5h4.1l2.1 2.25h7.3a1.75 1.75 0 0 1 1.75 1.75v1",
    );
    const opening = document.createElementNS(namespace, "path");
    opening.setAttribute(
        "d",
        "M4.5 9.5h15.35a1.35 1.35 0 0 1 1.3 1.72l-2.05 7.1a1.6 1.6 0 0 1-1.54 1.18H5.4a1.6 1.6 0 0 1-1.57-1.3L2.9 13.15A3.1 3.1 0 0 1 4.5 9.5Z",
    );
    icon.append(folder, opening);
    return icon;
}

function setOutputButtonLabel(item, label, showIcon = false) {
    item.replaceChildren();
    if (showIcon) item.append(folderOpenIcon());
    item.append(document.createTextNode(label));
}

function field(label, control) {
    const wrap = element("label", "");
    wrap.append(element("span", "h3c-label", label), control);
    return wrap;
}

function numberInput(value, options = {}) {
    const input = element("input");
    input.type = "number";
    input.value = value ?? "";
    for (const [key, setting] of Object.entries(options)) input[key] = setting;
    return input;
}

function selectInput(value, choices) {
    const select = element("select");
    for (const [choice, label] of choices) {
        const option = element("option", "", label);
        option.value = choice;
        select.append(option);
    }
    select.value = String(value ?? "");
    return select;
}

function widgetValue(node, name, fallback) {
    const widget = node.widgets?.find((item) => item.name === name);
    return widget?.value ?? fallback;
}

function inputConnected(node, name) {
    const input = node.inputs?.find((item) => item.name === name);
    return input?.link !== null && input?.link !== undefined;
}

function setWidgetValue(node, name, value) {
    const widget = node.widgets?.find((item) => item.name === name);
    if (!widget || Object.is(widget.value, value)) return;
    widget.value = value;
    widget.callback?.(value);
    node.graph?.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
}

function collapseWidget(widget) {
    widget._h3OriginalType ??= widget.type;
    widget._h3OriginalComputeSize ??= widget.computeSize;
    widget._h3OriginalDraw ??= widget.draw;
    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
    // Modern and legacy canvas paths do not agree on whether a hidden widget
    // may still draw. This field is internal editor state; preserve its value
    // for serialization while ensuring it can never cover native settings.
    widget.draw = () => {};

    // Multiline STRING widgets are real DOM textareas in current ComfyUI.
    // Collapsing only the LiteGraph geometry leaves that textarea floating at
    // its previous coordinates, over the scene editor. Hide both possible DOM
    // handles while retaining widget.value for workflow serialization.
    const elements = new Set([widget.inputEl, widget.element]);
    for (const item of elements) {
        if (!item?.style) continue;
        item.style.setProperty("display", "none", "important");
        item.style.setProperty("pointer-events", "none", "important");
        item.setAttribute?.("aria-hidden", "true");
    }

    // Current ComfyUI places DOM widgets in a separate Vue-owned wrapper.
    // Its widget object is markRaw, so changing `hidden` can leave that empty
    // wrapper pointer-active until a later renderer transition. Unregister
    // only the DOM surface: this widget deliberately remains in node.widgets
    // with its value and callback intact for workflow serialization.
    if (widget.element && widget.id && typeof widget.onRemove === "function") {
        widget.onRemove();
    }
}

function collapseModernBackingWidgets(node) {
    if ((node.comfyClass ?? node.type) !== MODERN_NODE_NAME) return;
    for (const name of MODERN_BACKING_WIDGETS) {
        const widget = node.widgets?.find((item) => item.name === name);
        if (widget) collapseWidget(widget);
    }
}

function setProjectAssetManagedWidget(widget, managed) {
    if (!widget) return;
    widget._h3ProjectAssetOriginal ??= {
        hidden: widget.hidden,
        type: widget.type,
        computeSize: widget.computeSize,
        draw: widget.draw,
        disabled: widget.disabled,
    };
    if (widget._h3ProjectAssetManaged === managed) return;
    widget._h3ProjectAssetManaged = managed;
    const original = widget._h3ProjectAssetOriginal;
    if (managed) {
        widget.hidden = true;
        widget.type = "hidden";
        widget.computeSize = () => [0, -4];
        widget.draw = () => {};
        widget.disabled = true;
    } else {
        widget.hidden = original.hidden;
        widget.type = original.type;
        widget.computeSize = original.computeSize;
        widget.draw = original.draw;
        widget.disabled = original.disabled;
    }
    for (const item of new Set([widget.inputEl, widget.element])) {
        if (!item?.style) continue;
        if (managed) {
            item.style.setProperty("display", "none", "important");
            item.style.setProperty("pointer-events", "none", "important");
            item.setAttribute?.("aria-hidden", "true");
        } else {
            item.style.removeProperty("display");
            item.style.removeProperty("pointer-events");
            item.removeAttribute?.("aria-hidden");
        }
    }
}

function colorOverrides(node) {
    node.properties ??= {};
    const current = node.properties[SCENE_COLOR_PROPERTY];
    if (!current || typeof current !== "object" || Array.isArray(current)) {
        node.properties[SCENE_COLOR_PROPERTY] = {};
    }
    return node.properties[SCENE_COLOR_PROPERTY];
}

function planLayout(node) {
    node.properties ??= {};
    const current = node.properties[LAYOUT_PROPERTY];
    if (!current || typeof current !== "object" || Array.isArray(current)) {
        node.properties[LAYOUT_PROPERTY] = {};
    }
    const layout = node.properties[LAYOUT_PROPERTY];
    if (!layout.promptHeights || typeof layout.promptHeights !== "object"
            || Array.isArray(layout.promptHeights)) {
        layout.promptHeights = {};
    }
    return layout;
}

function sceneColorKey(shot, index) {
    return safeShotId(shot?.id, `clip_${String(index + 1).padStart(4, "0")}`);
}

function sceneColor(node, shot, index) {
    const override = colorOverrides(node)[sceneColorKey(shot, index)];
    return /^#[0-9a-f]{6}$/i.test(String(override || ""))
        ? String(override).toLowerCase()
        : automaticSceneColor(index);
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
    const offset = selected ? markup.length : 3;
    insertText(textarea, markup, offset);
}

function downloadJson(value, filename) {
    const blob = new Blob([value], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

function replaceWithModernPlan(node) {
    const createNode = globalThis.LiteGraph?.createNode;
    const result = upgradeLegacyPlanNode(node, {
        createNode,
        confirmUpgrade: () => (
            typeof globalThis.confirm !== "function" || globalThis.confirm(
                "Replace this Plan with MiniMax H3 Plan (Modern)?\n\n" +
                "Scenes, supported settings, position, and links are preserved. " +
                "Legacy fallback settings are intentionally not copied.",
            )
        ),
    });
    if (result.ok) {
        app.canvas?.selectNode?.(result.node);
        return;
    }
    if (result.reason === "cancelled") return;
    const messages = {
        policy_required:
            "Connect MiniMax H3 Generation Profile to this Plan before upgrading. " +
            "The Modern Plan has no legacy continuity or audio fallbacks, so the " +
            "original node was kept unchanged.",
        modern_unregistered:
            "MiniMax H3 Plan (Modern) is not registered. Restart ComfyUI after " +
            "updating the node pack.",
        create_unavailable:
            "ComfyUI could not create the Modern Plan node.",
    };
    globalThis.alert?.(messages[result.reason] ?? "The Plan could not be upgraded.");
}

function mountEditor(node) {
    if (node._h3ChainEditor || typeof node.addDOMWidget !== "function") return;
    const planWidget = node.widgets?.find((widget) => widget.name === "plan_json");
    if (!planWidget) return;
    const modern = (node.comfyClass ?? node.type) === MODERN_NODE_NAME;

    injectStyles();
    const root = element("div", "h3c-editor");
    root.title = "Build an ordered MiniMax H3 scene plan. Hover individual controls for wiring, timing, and formatting guidance.";
    for (const eventName of [
        "pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick",
    ]) {
        root.addEventListener(eventName, (event) => event.stopPropagation());
    }
    root.addEventListener("wheel", (event) => event.stopPropagation());
    const savedLayout = planLayout(node);
    const state = {
        plan: null,
        advanced: Boolean(savedLayout.advanced),
        jsonOpen: Boolean(savedLayout.jsonOpen),
        settingsOpen: modern && savedLayout.settingsOpen !== false,
        lastWidgetValue: "",
        syncing: false,
        draggedIndex: null,
        resizeObservers: [],
        seedRefreshers: [],
    };
    node._h3ChainEditor = state;

    collapseWidget(planWidget);
    collapseModernBackingWidgets(node);

    const domWidget = node.addDOMWidget("h3_chain_scene_editor", "h3-chain-editor", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => EDITOR_HEIGHT,
    });
    domWidget.serialize = false;

    function graphDirty() {
        node.graph?.setDirtyCanvas?.(true, true);
        app.graph?.setDirtyCanvas?.(true, true);
    }

    function savePanelState() {
        const layout = planLayout(node);
        layout.advanced = state.advanced;
        layout.jsonOpen = state.jsonOpen;
        layout.settingsOpen = state.settingsOpen;
        graphDirty();
    }

    function disconnectResizeObservers() {
        for (const observer of state.resizeObservers) observer.disconnect?.();
        state.resizeObservers = [];
    }

    function bindTextareaHeight(textarea, key, minimum) {
        const heights = planLayout(node).promptHeights;
        const saved = Number(heights[key]);
        if (Number.isFinite(saved) && saved >= minimum) {
            textarea.style.height = `${Math.round(saved)}px`;
        }
        if (typeof ResizeObserver !== "function") return;
        let initialized = false;
        const observer = new ResizeObserver(() => {
            if (!textarea.isConnected) return;
            const next = Math.round(textarea.offsetHeight);
            if (!Number.isFinite(next) || next < minimum) return;
            if (!initialized) {
                initialized = true;
                return;
            }
            const current = planLayout(node).promptHeights;
            if (Number(current[key]) === next) return;
            current[key] = next;
            node.properties[LAYOUT_PROPERTY] = {
                ...planLayout(node), promptHeights: {...current},
            };
            graphDirty();
        });
        observer.observe(textarea);
        state.resizeObservers.push(observer);
    }

    function saveSceneColor(key, value) {
        const colors = colorOverrides(node);
        if (value) colors[key] = value.toLowerCase();
        else delete colors[key];
        node.properties[SCENE_COLOR_PROPERTY] = {...colors};
        graphDirty();
    }

    function applyResponsiveSize() {
        collapseWidget(planWidget);
        collapseModernBackingWidgets(node);
        const width = Math.max(Number(node.size?.[0]) || MIN_WIDTH, MIN_WIDTH);
        // Preserve the workflow/user-selected height. The DOM widget receives
        // all free vertical space above its minimum, so taller nodes reveal
        // more scene cards and shorter nodes scroll internally.
        const height = Math.max(Number(node.size?.[1]) || 0, EDITOR_HEIGHT + 120);
        // Call even when dimensions are unchanged so ComfyUI recomputes the
        // DOM widget's free-space allocation after plan_json was collapsed.
        node.setSize?.([width, height]);
        graphDirty();
    }

    function scheduleResponsiveSize() {
        // Workflow configuration restores serialized dimensions after
        // onNodeCreated. Reallocate the DOM viewport once layout has settled.
        requestAnimationFrame(() => requestAnimationFrame(applyResponsiveSize));
        setTimeout(applyResponsiveSize, 150);
    }

    function syncProjectAssetManagedWidgets() {
        const managed = inputConnected(node, "project_assets");
        setProjectAssetManagedWidget(
            node.widgets?.find((item) => item.name === "run_name"), managed,
        );
        setProjectAssetManagedWidget(
            node.widgets?.find((item) => item.name === "generation_fingerprint"),
            managed,
        );
        root.classList.toggle("h3c-project-assets-managed", managed);
        root.title = managed
            ? "Run name and reference fingerprint are managed by the connected Project Assets node."
            : "Build an ordered MiniMax H3 scene plan. Hover individual controls for wiring, timing, and formatting guidance.";
    }

    function currentSettings() {
        const transition = resolveTransitionPolicy(node);
        const audioPolicy = resolveAudioPolicy(node);
        return {
            contextLength: transition.known
                ? transition.contextLength
                : widgetValue(node, "context_length", 22),
            audioContextLength: resolveAudioContextLength(node),
            videoBlendFrames: widgetValue(node, "video_blend_frames", 0),
            encodeMode: widgetValue(node, "encode_mode", "video"),
            anchorMode: widgetValue(node, "anchor_mode", "head"),
            continuationMode: transition.known
                ? transition.continuationMode
                : widgetValue(node, "continuation_mode", "guide"),
            generatedContinuity: audioPolicy.known
                ? audioPolicy.generatedContinuity : "on",
            sourceAudioTarget: audioPolicy.known
                ? audioPolicy.sourceAudioTarget ?? "off" : "off",
            transitionPreset: transition.known ? transition.preset : "custom",
            audioPolicy,
            defaultDurationSeconds: widgetValue(node, "default_duration_seconds", 15),
            defaultSteps: widgetValue(node, "default_steps", 20),
        };
    }

    function timing() {
        return calculatePlanTiming(state.plan, currentSettings());
    }

    function syncPlan() {
        if (!state.plan) return;
        const value = planToJson(state.plan);
        state.syncing = true;
        planWidget.value = value;
        state.lastWidgetValue = value;
        state.syncing = false;
        const jsonArea = root.querySelector(".h3c-json");
        if (jsonArea && document.activeElement !== jsonArea) jsonArea.value = value;
        updateTiming();
        graphDirty();
    }

    function updateTiming() {
        if (!state.plan) return;
        const result = timing();
        const summary = root.querySelector(".h3c-summary");
        if (summary) {
            summary.textContent = `${result.shots.length} scenes · ${result.totalFrames} delivered frames · ${formatClock(result.totalSeconds)}`;
        }
        for (const row of result.shots) {
            const card = root.querySelector(`.h3c-card[data-index="${row.index - 1}"]`);
            if (!card) continue;
            const label = card.querySelector(".h3c-timing");
            if (label) {
                label.textContent = `${row.rawFrames || "—"} raw / ${row.deliveredFrames || "—"} delivered · ${formatClock(row.deliveredSeconds)}${row.loraRoute === "base" ? "" : ` · LoRA ${row.loraRoute.toUpperCase()}`}`;
                label.title = row.errors.join("\n") ||
                    `Generation starts at delivered frame ${row.generationStartFrame}. ` +
                    `The incoming assembly boundary blends ${row.videoBlendFrames} frame(s).`;
            }
            card.classList.toggle("h3c-invalid", row.errors.length > 0);
        }
        const errors = root.querySelector(".h3c-errors");
        if (errors) {
            errors.textContent = result.errors.join("\n");
            errors.classList.toggle("h3c-open", result.errors.length > 0);
        }
    }

    function promptTools(textarea, scene = null) {
        const wrap = element("div", "h3c-prompt-tools");
        const references = button("@ Reference", "Insert a MiniMax reference tag", () => {
            const opening = !menu.classList.contains("h3c-open");
            if (opening) renderReferenceMenu();
            menu.classList.toggle("h3c-open", opening);
        });
        const dialogue = button("# Dialogue", "Wrap the selection in <d> dialogue tags", () => {
            insertDialogue(textarea);
        });
        const hint = element("span", "h3c-hint", "Shortcuts: @ reference · # dialogue");
        const menu = element("div", "h3c-ref-menu");
        function renderReferenceMenu() {
            menu.replaceChildren();
            const requestedScene = scene ?? 1;
            const referenceData = availableReferenceRecords(
                node, requestedScene, {
                    includeInactive: true,
                    prompt: [
                        scene == null ? "" : sharedPrompt(state.plan).text.trim(),
                        textarea.value.trim(),
                    ].filter(Boolean).join("\n\n"),
                },
            );
            const {mode} = referenceData;
            const records = mode === "tagged" || scene == null
                ? referenceData.records
                : referenceData.records.filter((record) => record.active);
            if (!records.length) {
                menu.append(element(
                    "span", "h3c-help",
                    scene == null
                        ? "No references connected to a downstream Ref2VA/I2V node."
                        : `No connected references are active in scene ${scene}.`,
                ));
                return;
            }
            menu.append(element(
                "span", "h3c-help",
                mode === "tagged"
                    ? "Connected prompt-driven references. Insert an @tag to activate that asset in this scene; it compiles to a native H3 label."
                    : mode === "scheduled"
                    ? "Connected scheduled references only. @aliases are optional authoring shortcuts that compile to native labels; the scheduler inserts no prompt text."
                    : "Connected core references only. These use native <Picture/Video/Audio N> labels; @aliases are not required.",
            ));
            for (const record of records) {
                const aliasMode = mode === "scheduled" || mode === "tagged";
                const mapping = aliasMode && record.label
                    ? ` → ${record.label}` : "";
                menu.append(button(
                    `${record.token}${mapping}`,
                    aliasMode
                        ? `Insert ${record.token}. It ${mode === "tagged" ? "activates this reference and " : ""}compiles to ${record.label ?? "a scene-local native label"}.`
                        : `Insert ${record.token} for the connected core reference.`,
                    () => {
                        insertText(textarea, record.token);
                        menu.classList.remove("h3c-open");
                    },
                ));
            }
        }
        textarea.addEventListener("keydown", (event) => {
            if (event.ctrlKey || event.metaKey || event.altKey) return;
            if (event.key === "@") {
                event.preventDefault();
                renderReferenceMenu();
                menu.classList.add("h3c-open");
                references.focus();
            } else if (event.key === "#") {
                event.preventDefault();
                insertDialogue(textarea);
            }
        });
        wrap.append(references, dialogue, hint);
        const group = element("div");
        group.append(wrap, menu);
        return group;
    }

    function renderCard(shot, index) {
        const card = element("section", "h3c-card");
        card.dataset.index = String(index);
        card.style.setProperty("--h3c-scene-color", sceneColor(node, shot, index));
        card.addEventListener("dragover", (event) => {
            event.preventDefault();
            card.classList.add("h3c-drag-over");
        });
        card.addEventListener("dragleave", () => card.classList.remove("h3c-drag-over"));
        card.addEventListener("drop", (event) => {
            event.preventDefault();
            card.classList.remove("h3c-drag-over");
            if (state.draggedIndex === null || state.draggedIndex === index) return;
            moveShot(state.plan.shots, state.draggedIndex, index);
            state.draggedIndex = null;
            syncPlan();
            render();
        });

        const head = element("div", "h3c-card-head");
        const drag = element("span", "h3c-drag", "⠿");
        drag.title = "Drag to reorder";
        drag.draggable = true;
        drag.addEventListener("dragstart", (event) => {
            state.draggedIndex = index;
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", String(index));
        });
        drag.addEventListener("dragend", () => {
            state.draggedIndex = null;
            root.querySelectorAll(".h3c-drag-over").forEach((item) => item.classList.remove("h3c-drag-over"));
        });
        const ordinal = element("span", "h3c-index", `Scene ${index + 1}`);
        const color = element("input", "h3c-color");
        color.type = "color";
        color.value = sceneColor(node, shot, index);
        color.title = "Scene border color. Double-click to restore the automatic color.";
        color.setAttribute("aria-label", `Scene ${index + 1} border color`);
        color.addEventListener("input", () => {
            saveSceneColor(sceneColorKey(shot, index), color.value);
            card.style.setProperty("--h3c-scene-color", color.value);
        });
        color.addEventListener("dblclick", (event) => {
            event.preventDefault();
            saveSceneColor(sceneColorKey(shot, index), null);
            color.value = automaticSceneColor(index);
            card.style.setProperty("--h3c-scene-color", color.value);
        });
        const id = element("input", "h3c-id");
        id.type = "text";
        id.placeholder = `clip_${String(index + 1).padStart(4, "0")}`;
        id.value = shot.id ?? "";
        id.title = "Stable scene ID used in checkpoint filenames and resume validation. Keep it unique and avoid changing it after rendering.";
        id.addEventListener("input", () => {
            const previousKey = sceneColorKey(shot, index);
            const previousId = safeShotId(
                shot.id, `clip_${String(index + 1).padStart(4, "0")}`,
            );
            if (id.value) shot.id = id.value;
            else delete shot.id;
            const nextId = safeShotId(
                shot.id, `clip_${String(index + 1).padStart(4, "0")}`,
            );
            for (const chapter of state.plan.chapters ?? []) {
                if (chapter.start_scene_id === previousId) {
                    chapter.start_scene_id = nextId;
                }
            }
            const nextKey = sceneColorKey(shot, index);
            const colors = colorOverrides(node);
            if (previousKey !== nextKey && colors[previousKey]) {
                colors[nextKey] = colors[previousKey];
                delete colors[previousKey];
                node.properties[SCENE_COLOR_PROPERTY] = {...colors};
            }
            const heights = planLayout(node).promptHeights;
            const previousPromptKey = `scene:${previousKey}`;
            const nextPromptKey = `scene:${nextKey}`;
            if (previousPromptKey !== nextPromptKey && heights[previousPromptKey]) {
                heights[nextPromptKey] = heights[previousPromptKey];
                delete heights[previousPromptKey];
            }
            syncPlan();
            void refreshSeedStatus();
        });
        const timingLabel = element("span", "h3c-timing");
        const up = button("↑", "Move scene up", () => {
            moveShot(state.plan.shots, index, index - 1);
            syncPlan();
            render();
        });
        up.disabled = index === 0;
        const down = button("↓", "Move scene down", () => {
            moveShot(state.plan.shots, index, index + 1);
            syncPlan();
            render();
        });
        down.disabled = index === state.plan.shots.length - 1;
        const copy = button("Duplicate", "Duplicate this scene", () => {
            if (state.plan.shots.length >= MAX_SHOTS) return;
            duplicateShot(state.plan.shots, index);
            syncPlan();
            render();
        });
        const remove = button("Delete", "Delete this scene", () => {
            if (state.plan.shots.length <= 1) return;
            if (!window.confirm(`Delete scene ${index + 1}?`)) return;
            saveSceneColor(sceneColorKey(shot, index), null);
            removePlanShot(state.plan, index);
            syncPlan();
            render();
        });
        remove.disabled = state.plan.shots.length <= 1;
        head.append(drag, color, ordinal, id, timingLabel, up, down, copy, remove);

        const lengthRow = element("div", "h3c-length-row");
        const mode = element("select");
        mode.title = "Choose whether this scene inherits the plan duration, requests seconds that are rounded up, or specifies an exact H3-valid frame count.";
        for (const [value, label] of [["default", "Plan default"], ["seconds", "Seconds"], ["frames", "Exact frames"]]) {
            const option = element("option", "", label);
            option.value = value;
            mode.append(option);
        }
        mode.value = shotLengthMode(shot);
        const value = numberInput("", {min: "0.01", step: "0.01"});
        const lengthHelp = element("span", "h3c-help");
        function refreshLengthControl() {
            const selected = shotLengthMode(shot);
            mode.value = selected;
            value.disabled = selected === "default";
            if (selected === "seconds") {
                value.min = "0.01";
                value.max = String(3592 / 24);
                value.step = "0.01";
                value.value = shot.duration_seconds ?? "";
                lengthHelp.textContent = "Rounded up to 17k+5.";
                value.title = "Requested seconds. The backend rounds up to the next H3-valid 17k+5 frame length at 24 fps.";
            } else if (selected === "frames") {
                value.min = "5";
                value.max = "3592";
                value.step = "17";
                value.value = shot.length ?? shot.frames ?? "";
                lengthHelp.textContent = "Must satisfy length % 17 = 5.";
                value.title = "Exact raw generation frames. Valid values satisfy frames % 17 = 5 and range from 5 to 3592.";
            } else {
                value.value = "";
                lengthHelp.textContent = "Uses the Plan node default.";
                value.title = "Disabled because this scene inherits the Plan node default duration.";
            }
        }
        mode.addEventListener("change", () => {
            const fallback = widgetValue(node, "default_duration_seconds", 15);
            setShotLengthMode(shot, mode.value, fallback);
            refreshLengthControl();
            syncPlan();
        });
        value.addEventListener("input", () => {
            if (!value.value) return;
            if (mode.value === "seconds") shot.duration_seconds = Number(value.value);
            if (mode.value === "frames") {
                shot.length = Number(value.value);
                delete shot.frames;
            }
            syncPlan();
        });
        refreshLengthControl();
        lengthRow.append(mode, value, lengthHelp);

        const basicPrompt = element("textarea", "h3c-basic-prompt");
        basicPrompt.value = String(shot.basic_prompt ?? "");
        basicPrompt.placeholder = "Optional plain-language scene idea, not H3-formatted. Optimize it into the scene prompt from Rich Scene Prompt Editor.";
        basicPrompt.title = "A simple draft description kept separately from the H3-formatted scene prompt below. It is never used for generation by itself; Rich Scene Prompt Editor's Optimize turns it into the scene prompt.";
        basicPrompt.spellcheck = true;
        basicPrompt.addEventListener("input", () => {
            shot.basic_prompt = basicPrompt.value;
            syncPlan();
        });
        bindTextareaHeight(
            basicPrompt, `scene-basic:${sceneColorKey(shot, index)}`, 72,
        );

        const prompt = element("textarea", "h3c-prompt");
        prompt.value = promptValueToText(shot.prompt, `Scene ${index + 1} prompt`);
        prompt.placeholder = "Optional with a shared prompt; otherwise describe this scene…";
        prompt.title = "Scene-specific action, camera, performance, dialogue, and ending continuity. The shared prompt is automatically prepended; at least one of the two must contain text.";
        prompt.spellcheck = true;
        prompt.addEventListener("input", () => {
            shot.prompt = promptTextToLines(prompt.value);
            syncPlan();
        });
        bindTextareaHeight(
            prompt, `scene:${sceneColorKey(shot, index)}`, 112,
        );

        const promptSeedControl = element("div", "h3c-seed-control");
        const promptSeedMode = element("select");
        for (const [value, label] of [
            ["inherit", "Stable derived"],
            ["fixed", "Fixed scene seed"],
            ["randomize", "Randomize each queue"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            promptSeedMode.append(option);
        }
        const promptSeed = element("input");
        promptSeed.type = "text";
        promptSeed.inputMode = "numeric";
        promptSeed.placeholder = "Scene prompt seed";
        promptSeed.title = "Unsigned 64-bit seed used only to choose this scene's {one|two} prompt alternatives. It never changes the sampler seed.";
        const newPromptSeed = button(
            "New random",
            "Create and store a new fixed alternative-choice seed for only this scene",
            () => {
                shot.prompt_seed_mode = "fixed";
                shot.prompt_seed = randomSceneSeed();
                refreshPromptSeedControl();
                syncPlan();
            },
        );
        function refreshPromptSeedControl() {
            const selected = scenePromptSeedMode(shot);
            promptSeedMode.value = selected;
            promptSeed.disabled = selected !== "fixed";
            newPromptSeed.disabled = selected !== "fixed";
            promptSeed.value = selected === "fixed" ? (shot.prompt_seed ?? "") : "";
            if (selected === "inherit") {
                promptSeed.title = "This scene derives a stable alternative-choice seed from its scene index and ID.";
            } else if (selected === "randomize") {
                promptSeed.title = "A fresh alternative-choice seed is generated whenever this Plan is queued. The exact resolved seed is still saved with the scene checkpoint.";
            } else {
                promptSeed.title = "Unsigned 64-bit seed used only to choose this scene's {one|two} prompt alternatives. It never changes the sampler seed.";
            }
        }
        promptSeedMode.addEventListener("change", () => {
            setScenePromptSeedMode(shot, promptSeedMode.value);
            refreshPromptSeedControl();
            syncPlan();
        });
        promptSeed.addEventListener("change", () => {
            if (promptSeed.value.trim()) shot.prompt_seed = promptSeed.value.trim();
            else shot.prompt_seed = randomSceneSeed();
            setScenePromptSeedMode(shot, "fixed");
            refreshPromptSeedControl();
            syncPlan();
        });
        promptSeedControl.append(promptSeedMode, promptSeed, newPromptSeed);
        refreshPromptSeedControl();

        const seedControl = element("div", "h3c-seed-control");
        const seed = element("input");
        seed.type = "text";
        seed.inputMode = "numeric";
        seed.placeholder = "Blank = stable derived seed";
        seed.value = shot.seed ?? "";
        seed.title = "Explicit unsigned 64-bit seed for this scene. Leave blank to use the displayed stable seed derived from base_seed, scene index, and scene ID.";
        const seedStatus = element("span", "h3c-seed-status");
        async function refreshSeedStatus() {
            const explicit = seed.value.trim();
            useDerivedSeed.disabled = !explicit;
            if (explicit) {
                seedStatus.textContent = `Explicit seed: ${explicit}`;
                seedStatus.title = "This exact seed is stored in plan_json.";
                return;
            }
            const baseSeed = widgetValue(node, "base_seed", 0);
            const shotId = safeShotId(
                shot.id, `clip_${String(index + 1).padStart(4, "0")}`,
            );
            const request = `${baseSeed}:${index + 1}:${shotId}`;
            seedStatus.dataset.request = request;
            seedStatus.textContent = "Resolving stable derived seed…";
            try {
                const resolved = await derivedSceneSeed(baseSeed, index + 1, shotId);
                if (seedStatus.isConnected && !seed.value.trim()
                        && seedStatus.dataset.request === request) {
                    seedStatus.textContent = `Derived seed: ${resolved}`;
                    seedStatus.title = `Stable result of base_seed ${baseSeed}, scene ${index + 1}, and ID ${shotId}. It will repeat until one of those values changes.`;
                }
            } catch (error) {
                seedStatus.textContent = error.message;
            }
        }
        seed.addEventListener("input", () => {
            if (seed.value.trim()) shot.seed = seed.value.trim();
            else delete shot.seed;
            syncPlan();
            void refreshSeedStatus();
        });
        const randomizeSeed = button("New random", "Create and store a new explicit uint64 seed for only this scene", () => {
            seed.value = randomSceneSeed();
            shot.seed = seed.value;
            syncPlan();
            void refreshSeedStatus();
        });
        const useDerivedSeed = button("Use derived", "Remove this scene's explicit override and return to its stable base_seed-derived value", () => {
            seed.value = "";
            delete shot.seed;
            syncPlan();
            void refreshSeedStatus();
        });
        useDerivedSeed.disabled = !seed.value.trim();
        seedControl.append(seed, randomizeSeed, useDerivedSeed, seedStatus);
        state.seedRefreshers.push(refreshSeedStatus);
        void refreshSeedStatus();

        const loraRoute = element("select", "h3c-lora-route");
        const selectedLoRARoute = sceneLoRARoute(shot);
        for (const route of availableLoRARoutes(
            node.graph ?? app.graph, [node], selectedLoRARoute,
        )) {
            const option = element("option", "", loraRouteLabel(route));
            option.value = route;
            loraRoute.append(option);
        }
        loraRoute.value = selectedLoRARoute;
        loraRoute.title = "Select Base or a connected A-Z MODEL branch on MiniMax H3 Scene LoRA Scheduler. Connecting the scheduler's last empty route reveals the next one automatically; the scheduler routes already-patched models and does not load a LoRA itself.";
        loraRoute.addEventListener("change", () => {
            if (loraRoute.value === "base") delete shot.lora_route;
            else shot.lora_route = loraRoute.value;
            sceneLoRARoute(shot);
            syncPlan();
        });

        const advanced = element("div", "h3c-advanced-fields");
        const steps = numberInput(shot.steps ?? "", {min: "1", max: "10000", step: "1"});
        steps.placeholder = String(widgetValue(node, "default_steps", 20));
        steps.title = "Optional sampler-step override for only this scene. Leave blank to inherit the Plan node default.";
        steps.addEventListener("input", () => {
            if (steps.value) shot.steps = Number(steps.value);
            else delete shot.steps;
            syncPlan();
        });
        const context = element("select", "h3c-context");
        const resolvedPlanSettings = currentSettings();
        const planContextLength = Number(resolvedPlanSettings.contextLength);
        function normalizeVisualLeadSpan() {
            if (!Object.hasOwn(shot, "visual_context_lead_source")) return;
            const resolved = sceneContextLength(shot, planContextLength);
            const allowed = visualContextCompositions()
                .filter((choice) => choice.total === resolved)
                .map((choice) => choice.lead);
            if (!allowed.length) {
                delete shot.visual_context_lead_source;
                delete shot.visual_context_lead_frames;
                delete shot.visual_context_lead_start_frame;
                return;
            }
            try {
                sceneVisualContextLeadFrames(shot, resolved);
            } catch (_error) {
                shot.visual_context_lead_frames = allowed[0];
            }
        }
        const incomingTransition = element("select", "h3c-incoming-transition");
        const inheritedPreset = resolvedPlanSettings.transitionPreset;
        const inheritOption = element(
            "option", "",
            `Inherit Chain Policy · ${transitionPresetLabel(inheritedPreset)}`,
        );
        inheritOption.value = "inherit";
        incomingTransition.append(inheritOption);
        for (const preset of primaryTransitionOptions()) {
            const option = element(
                "option", "",
                `${preset.label} · ${preset.description}`,
            );
            option.value = preset.name;
            incomingTransition.append(option);
        }
        function refreshIncomingTransition() {
            const selected = sceneTransitionPreset(
                shot, resolvedPlanSettings.continuationMode,
                resolvedPlanSettings.contextLength,
                resolvedPlanSettings.audioContextLength,
            );
            let custom = incomingTransition.querySelector(
                'option[value="custom"]',
            );
            if (selected === "custom" && !custom) {
                custom = element(
                    "option", "", transitionPresetLabel("custom"),
                );
                custom.value = "custom";
                incomingTransition.append(custom);
            }
            incomingTransition.value = selected;
        }
        refreshIncomingTransition();
        incomingTransition.title = "One semantic boundary choice. Inherit uses "
            + "the connected Chain Policy. Choosing a preset writes its tested "
            + "visual implementation/context pair and returns generated-audio "
            + "context to automatic behavior. Custom means this scene still "
            + "contains raw Advanced boundary overrides below.";
        incomingTransition.addEventListener("change", () => {
            if (incomingTransition.value === "custom") return;
            applySceneTransitionPreset(shot, incomingTransition.value);
            const nextContext = sceneContextLength(shot, planContextLength);
            if (Object.hasOwn(shot, "video_blend_frames")
                    && Number(shot.video_blend_frames) > nextContext) {
                shot.video_blend_frames = nextContext;
            }
            normalizeVisualLeadSpan();
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_start_frame;
            syncPlan();
            render();
        });
        for (const [value, label] of [
            ["", `Plan default · ${planContextLength}`],
            ["0", "0 · new visual"],
            ...H3_CONTEXT_LENGTHS.map((value) => [String(value), `${value} frames`]),
        ]) {
            const option = element("option", "", label);
            option.value = value;
            context.append(option);
        }
        context.value = Object.hasOwn(shot, "context_length")
            && shot.context_length !== null ? String(shot.context_length) : "";
        context.title = index === 0
            ? "Video context entering this scene. Blank inherits the Plan default. Zero makes scene 1 visually independent even when Existing Video Context is connected; the original may still be prepended during assembly."
            : "Video frames carried from the preceding scene into this one. Blank inherits the Plan default; zero creates a visually new scene. Audio context is controlled separately.";
        context.addEventListener("change", () => {
            if (context.value === "") delete shot.context_length;
            else shot.context_length = Number(context.value);
            sceneContextLength(shot, planContextLength);
            normalizeVisualLeadSpan();
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_start_frame;
            refreshBlendControl();
            refreshIncomingTransition();
            syncPlan();
            render();
        });
        const visualSource = element("select", "h3c-visual-source");
        if (index === 0) {
            const option = element(
                "option", "", "Existing Video Context (if connected)",
            );
            option.value = "";
            visualSource.append(option);
            visualSource.disabled = true;
        } else {
            const previous = element(
                "option", "",
                `Previous scene · ${index} ${safeShotId(
                    state.plan.shots[index - 1]?.id,
                    `clip_${String(index).padStart(4, "0")}`,
                )}`,
            );
            previous.value = "";
            visualSource.append(previous);
            for (let sourceOffset = 0;
                sourceOffset < index - 1; sourceOffset += 1) {
                const sourceId = safeShotId(
                    state.plan.shots[sourceOffset]?.id,
                    `clip_${String(sourceOffset + 1).padStart(4, "0")}`,
                );
                const option = element(
                    "option", "",
                    `Scene ${sourceOffset + 1} · ${sourceId}`,
                );
                option.value = sourceId;
                visualSource.append(option);
            }
            const rawSource = shot.visual_context_source;
            if (rawSource === undefined || rawSource === null
                    || ["", "previous", "immediate"].includes(
                        String(rawSource).trim().toLowerCase())) {
                visualSource.value = "";
            } else {
                try {
                    const resolved = sceneVisualContextSource(
                        state.plan, index + 1,
                    );
                    visualSource.value = resolved === index ? ""
                        : safeShotId(
                            state.plan.shots[resolved - 1]?.id,
                            `clip_${String(resolved).padStart(4, "0")}`,
                        );
                } catch (_error) {
                    const invalid = element(
                        "option", "",
                        `Invalid · ${String(rawSource)}`,
                    );
                    invalid.value = String(rawSource);
                    visualSource.append(invalid);
                    visualSource.value = String(rawSource);
                }
            }
        }
        visualSource.title = index === 0
            ? "Scene 1 visual context comes from Existing Video Context."
            : "Choose which earlier saved scene supplies the video/RGB context. Generated-audio continuity always comes from the immediately previous timeline scene. A non-linear source is a hard cut in final assembly.";
        visualSource.addEventListener("change", () => {
            if (!visualSource.value) {
                delete shot.visual_context_source;
            } else {
                shot.visual_context_source = visualSource.value;
                shot.video_blend_frames = 0;
            }
            delete shot.visual_context_start_frame;
            refreshBlendControl();
            syncPlan();
            render();
        });
        const visualLeadSource = element("select", "h3c-visual-lead-source");
        const noLead = element("option", "", "Off · one context block");
        noLead.value = "";
        visualLeadSource.append(noLead);
        const recentSourceIndex = index === 0 ? null
            : sceneVisualContextSource(state.plan, index + 1);
        if (recentSourceIndex !== null) {
            for (let sourceOffset = 0;
                sourceOffset < index; sourceOffset += 1) {
                const sourceId = safeShotId(
                    state.plan.shots[sourceOffset]?.id,
                    `clip_${String(sourceOffset + 1).padStart(4, "0")}`,
                );
                const sameSource = sourceOffset + 1 === recentSourceIndex;
                const sourceLabel = `Scene ${sourceOffset + 1} · ${sourceId}`
                    + (sameSource ? " · same scene, separate window" : "");
                const option = element(
                    "option", "", sourceLabel,
                );
                option.value = sourceId;
                visualLeadSource.append(option);
            }
        }
        visualLeadSource.disabled = index === 0;
        try {
            const resolvedLead = sceneVisualContextLeadSource(
                state.plan, index + 1,
            );
            visualLeadSource.value = resolvedLead === null ? ""
                : safeShotId(
                    state.plan.shots[resolvedLead - 1]?.id,
                    `clip_${String(resolvedLead).padStart(4, "0")}`,
                );
        } catch (_error) {
            visualLeadSource.value = "";
        }
        visualLeadSource.title = "Optional first block in one composed visual context. It may use a different scene or a second independently positioned window from the same scene. Visual context source supplies the block nearest generation; audio remains one continuous tail from the immediate timeline scene.";

        const visualLeadFrames = element("select", "h3c-visual-lead-frames");
        const resolvedVisualContext = sceneContextLength(
            shot, planContextLength,
        );
        const compositionChoices = visualContextCompositions();
        const defaultComposition = compositionChoices.find(
            (choice) => choice.total === resolvedVisualContext,
        ) ?? compositionChoices[0];
        visualLeadSource.disabled = visualLeadSource.disabled
            || !compositionChoices.length;
        for (const total of H3_CONTEXT_LENGTHS) {
            const groupChoices = compositionChoices.filter(
                (choice) => choice.total === total,
            );
            if (!groupChoices.length) continue;
            const group = element("optgroup", "");
            group.label = `${total} total frames`;
            for (const choice of groupChoices) {
                const option = element("option", "", choice.label);
                option.value = choice.value;
                group.append(option);
            }
            visualLeadFrames.append(group);
        }
        visualLeadFrames.disabled = !visualLeadSource.value
            || !compositionChoices.length;
        if (visualLeadSource.value) {
            try {
                const lead = sceneVisualContextLeadFrames(
                    shot, resolvedVisualContext,
                );
                visualLeadFrames.value = `${resolvedVisualContext}:${lead}`;
            } catch (_error) {
                visualLeadFrames.value = defaultComposition?.value ?? "";
            }
        } else {
            visualLeadFrames.value = defaultComposition?.value ?? "";
        }
        visualLeadFrames.title = "Select the total H3 context and its ordered two-block split. Both blocks may use the same scene with independent floating windows. For example, 39 total includes 5+34, 17+22, and their reverse orientations.";
        function applyVisualComposition() {
            const [totalRaw, leadRaw] = visualLeadFrames.value.split(":");
            const total = Number(totalRaw);
            const lead = Number(leadRaw);
            if (!Number.isInteger(total) || !Number.isInteger(lead)) return;
            shot.context_length = total;
            shot.visual_context_lead_frames = lead;
            sceneVisualContextLeadFrames(shot, total);
            shot.video_blend_frames = 0;
        }
        visualLeadSource.addEventListener("change", () => {
            if (!visualLeadSource.value) {
                delete shot.visual_context_lead_source;
                delete shot.visual_context_lead_frames;
                delete shot.visual_context_lead_start_frame;
            } else {
                shot.visual_context_lead_source = visualLeadSource.value;
                applyVisualComposition();
            }
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_start_frame;
            refreshBlendControl();
            syncPlan();
            render();
        });
        visualLeadFrames.addEventListener("change", () => {
            if (visualLeadSource.value) {
                applyVisualComposition();
            }
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_start_frame;
            refreshBlendControl();
            syncPlan();
            render();
        });
        const audioContext = numberInput(shot.audio_context_length ?? "", {
            min: "0", max: "240", step: "1",
        });
        const planAudioContextLength = Number(
            resolvedPlanSettings.audioContextLength,
        );
        audioContext.placeholder = planAudioContextLength
            ? String(planAudioContextLength)
            : `Follow video · ${planContextLength}`;
        audioContext.title = "Generated-audio context entering this scene. Blank inherits the Plan audio default (whose 0 follows video context). An explicit 0 carries no prior generated sound. A positive value can continue audio when video context is 0. AV mask modes ignore this override and keep audio synchronized to the video prefix; source_track uses its exact timeline slice instead.";
        audioContext.addEventListener("input", () => {
            if (audioContext.value === "") delete shot.audio_context_length;
            else shot.audio_context_length = Number(audioContext.value);
            refreshIncomingTransition();
            syncPlan();
        });
        const blendFrames = numberInput(shot.video_blend_frames ?? "", {
            min: "0", max: String(planContextLength), step: "1",
        });
        const planBlendFrames = Number(resolvedPlanSettings.videoBlendFrames);
        function refreshBlendControl() {
            const resolvedContext = sceneContextLength(shot, planContextLength);
            blendFrames.max = String(resolvedContext);
            blendFrames.placeholder = String(Math.min(
                planBlendFrames, resolvedContext,
            ));
        }
        refreshBlendControl();
        blendFrames.title = index === 0
            ? "Visible assembly blend entering scene 1 when Existing Video Context supplies a predecessor. Blank inherits the Plan default, capped to this scene's video context. This is assembly-only and does not alter sampling."
            : "Visible assembly blend at the boundary from the previous scene into this scene. Blank inherits the Plan default, capped to this scene's video context. Zero keeps a hard cut. This is assembly-only and does not alter sampling.";
        blendFrames.addEventListener("input", () => {
            if (blendFrames.value === "") delete shot.video_blend_frames;
            else shot.video_blend_frames = Number(blendFrames.value);
            sceneVideoBlendFrames(
                shot, planBlendFrames,
                sceneContextLength(shot, planContextLength),
            );
            syncPlan();
        });
        const continuation = element("select", "h3c-continuation");
        const planContinuationMode = resolvedPlanSettings.continuationMode;
        for (const [value, label] of [
            ["", `Plan default · ${planContinuationMode}`],
            ["guide", "Guide · new shot"],
            ["tone_carry_guide", "Tone Carry Guide · corrected RGB context"],
            ["latent_guide", "Latent Guide · direct generated latent"],
            ["tapered_guide", "Detail Guide · color injection"],
            ["tapered_av", "Detail AV · experimental latent taper"],
            ["drift_control_av", "Drift-Control AV · schedule-matched mask"],
            ["color_stable_drift_av", "Color-Stable Drift AV · tapered scene-one latent delta"],
            ["masked_av", "Masked AV · same shot"],
            ["feathered_av", "Feathered AV · experimental dual-stream feather"],
            ["audio_feathered_av", "Audio Feather AV · hard picture, soft sound"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            continuation.append(option);
        }
        continuation.value = Object.hasOwn(shot, "continuation_mode")
            ? sceneContinuationMode(shot, planContinuationMode) : "";
        continuation.title = index === 0
            ? "Continuation into this scene. Scene 1 uses it only when Existing Video Context supplies a predecessor. Guide re-encodes RGB; Latent Guide directly reuses a generated predecessor latent and falls back to RGB for imported context; Detail Guide adds a tapered chroma treatment; Detail AV applies a disposable video-only latent taper; Drift-Control AV applies a scheduler-matched video mask with a clean seam and needs the Chain Context MODEL path; Color-Stable Drift AV additionally carries a weak scene-one color correction as a tapered VAE delta on the copied video latent only; Masked AV protects an exact prefix; experimental Feathered AV softens both streams; Audio-Feathered AV keeps the picture exact and softens only the final audio ticks."
            : "Continuation from the preceding scene. Guide re-encodes RGB into persistent conditioning; Latent Guide supplies the saved sampled-latent tail as persistent conditioning; Detail Guide applies luma-preserving tapered chroma injection; Detail AV applies a disposable video-only latent taper; Drift-Control AV applies a scheduler-matched video mask with a clean seam and needs the Chain Context MODEL path; Color-Stable Drift AV additionally carries a weak scene-one color correction as a tapered VAE delta on the copied video latent only; Masked AV protects an exact prefix; experimental Feathered AV softens both streams; Audio-Feathered AV keeps the picture exact and softens only the final audio ticks.";
        continuation.addEventListener("change", () => {
            if (continuation.value) {
                shot.continuation_mode = continuation.value;
            } else {
                delete shot.continuation_mode;
            }
            // Resolve once here so malformed externally supplied values cannot
            // be introduced through the compact editor control.
            sceneContinuationMode(shot, planContinuationMode);
            refreshIncomingTransition();
            syncPlan();
        });
        const spatialProxy = element("select", "h3c-spatial-proxy");
        for (const [value, label] of [
            ["", "Off · native context"],
            ["rgb_5_6", "Low-grid 5/6 proxy · Guide"],
            ["latent_5_6", "Latent 5/6 proxy · AV"],
        ]) {
            const option = element("option", "", label);
            option.value = value;
            spatialProxy.append(option);
        }
        spatialProxy.value = shot.context_spatial_proxy ?? "";
        spatialProxy.title = "Scheduled only on the boundary entering scenes 2+. Low-grid 5/6 reduces and VAE-decodes the complete saved predecessor video latent at the proxy canvas, takes its delivered tail, then Motion Context restores those Guide frames to the target; 1376×768 uses 1152×640 (48×86 → 40×72 latent). This exact low-grid decode costs extra preparation time and peak memory. Latent 5/6 applies a cheaper latent down/up filter only to the copied AV video prefix. Audio, generated output, checkpoints, and assembly remain native size.";
        spatialProxy.addEventListener("change", () => {
            if (spatialProxy.value) {
                shot.context_spatial_proxy = spatialProxy.value;
            } else {
                delete shot.context_spatial_proxy;
            }
            syncPlan();
        });
        const boundary = element("div", "h3c-boundary-fields");
        boundary.append(
            field("Incoming transition", incomingTransition),
            field("Final assembly crossfade frames", blendFrames),
        );
        const planAudioPolicy = resolvedPlanSettings.audioPolicy;
        const effectiveAudioPolicy = sceneAudioPolicy(shot, planAudioPolicy);
        const lipSync = element("select");
        for (const [value, label] of [
            ["inherit", `Inherit · ${planAudioPolicy.sourceAudioTarget === "locked" ? "On" : "Off"}`],
            ["on", "On · vocals drive this scene"],
            ["off", "Off · no source-audio guidance"],
            ["custom", "Custom · advanced audio controls"],
        ]) {
            const option = element("option", "", label);
            option.value = value; option.disabled = value === "custom";
            lipSync.append(option);
        }
        lipSync.value = sceneLipSyncMode(shot);
        lipSync.title = "On locks grouped vocals (or a legacy single track). Off "
            + "disables source locking, reference, and audio carry without changing "
            + "the final soundtrack. Inherit resets these three audio overrides.";
        lipSync.addEventListener("change", () => {
            applySceneLipSync(shot, lipSync.value);
            syncPlan();
            render();
        });
        function audioOverrideSelect(key, inherited, choices, title) {
            const select = element("select", "h3c-audio-override");
            const inheritedOption = element(
                "option", "", `Inherit Chain Policy · ${inherited}`,
            );
            inheritedOption.value = "inherit";
            select.append(inheritedOption);
            for (const [value, label] of choices) {
                const option = element("option", "", label);
                option.value = value;
                select.append(option);
            }
            select.value = sceneAudioOverride(shot, key);
            select.title = title;
            select.addEventListener("change", () => {
                applySceneAudioOverride(shot, key, select.value);
                lipSync.value = sceneLipSyncMode(shot);
                syncPlan();
            });
            return select;
        }
        const sourceReference = audioOverrideSelect(
            "source_reference",
            planAudioPolicy.sourceReference ?? effectiveAudioPolicy.sourceReference,
            [["on", "On · use this source-track window as Ref2VA audio"],
             ["off", "Off · no source audio reference"]],
            "Controls only this scene's loose source-audio Ref2VA reference. "
                + "It does not choose final soundtrack. Lock source audio wins "
                + "over this switch.",
        );
        const generatedContinuity = audioOverrideSelect(
            "generated_continuity",
            planAudioPolicy.generatedContinuity
                ?? effectiveAudioPolicy.generatedContinuity,
            [["on", "On · continue prior generated audio"],
             ["off", "Off · generate an independent audio stream"]],
            "Controls whether this scene carries the predecessor's generated "
                + "audio latent. Lock source audio wins over this switch.",
        );
        const inheritedLock = (planAudioPolicy.sourceAudioTarget ?? "off")
            === "locked" ? "on" : "off";
        const lockSourceAudio = audioOverrideSelect(
            "source_audio_target", inheritedLock,
            [["locked", "On · protect this exact source window"],
             ["off", "Off · leave target audio denoisable"]],
            "Locks only this scene's exact source waveform into the H3 target "
                + "audio latent. While on, loose source reference and generated "
                + "continuity are effectively off. Final soundtrack remains "
                + "the global Chain Policy choice.",
        );
        const audioFields = element("div", "h3c-audio-fields");
        audioFields.append(
            field("Lip-sync", lipSync),
            field("Source reference", sourceReference),
            field("Generated continuity", generatedContinuity),
            field("Lock source audio", lockSourceAudio),
        );
        advanced.append(
            field("Steps (blank = default)", steps),
            field("Advanced visual context", context),
            field("Visual context source", visualSource),
            field("Context block 1 source", visualLeadSource),
            field("Composed total / split", visualLeadFrames),
            field("Advanced audio context", audioContext),
            field("Advanced implementation", continuation),
            field("Boundary spatial proxy", spatialProxy),
        );
        card.append(
            head,
            lengthRow,
            field("Basic prompt (plain language, optimized separately)", basicPrompt),
            field("Scene prompt (optional with shared prompt)", prompt),
            promptTools(prompt, index + 1),
            field("Prompt alternatives", promptSeedControl),
            field("Scene seed", seedControl),
            field("Scene LoRA route", loraRoute),
            boundary,
            audioFields,
            advanced,
        );
        return card;
    }

    function renderModernSettings() {
        if (!modern) return null;
        const managed = inputConnected(node, "project_assets");
        const panel = element(
            "section",
            `h3c-section h3c-settings${state.settingsOpen ? " h3c-open" : ""}`,
        );
        const heading = element("div", "h3c-settings-head");
        const profileConnected = inputConnected(node, "chain_policy");
        const policyStatus = element(
            "span",
            `h3c-policy-status${profileConnected ? "" : " h3c-missing"}`,
            profileConnected
                ? "Generation Profile connected"
                : "Generation Profile required",
        );
        heading.append(
            element("strong", "", "Plan settings"),
            element(
                "span", "h3c-help",
                "Continuity and audio live in Generation Profile.",
            ),
            policyStatus,
        );

        function group(title, ...fields) {
            const wrap = element("div", "h3c-settings-group");
            wrap.append(element("div", "h3c-settings-group-title", title));
            const grid = element("div", "h3c-settings-fields");
            grid.append(...fields);
            wrap.append(grid);
            return wrap;
        }

        function textSetting(name, label, options = {}) {
            const control = element("input");
            control.type = "text";
            if (options.numeric) control.inputMode = "numeric";
            control.value = String(widgetValue(node, name, options.fallback ?? ""));
            control.disabled = Boolean(options.disabled);
            control.title = options.title ?? "";
            control.addEventListener("change", () => {
                let value = control.value;
                if (options.numeric) {
                    value = value.trim();
                    if (!/^\d+$/.test(value)) {
                        control.value = String(widgetValue(
                            node, name, options.fallback ?? "0"));
                        return;
                    }
                }
                setWidgetValue(node, name, value);
                updateTiming();
            });
            const wrapped = field(label, control);
            if (options.wide) wrapped.classList.add("h3c-wide");
            return wrapped;
        }

        function numberSetting(name, label, options) {
            const control = numberInput(widgetValue(node, name, options.fallback), {
                min: options.min,
                max: options.max,
                step: options.step,
            });
            control.title = options.title ?? "";
            control.addEventListener("change", () => {
                if (!control.validity.valid || control.value === "") {
                    control.value = String(widgetValue(
                        node, name, options.fallback));
                    return;
                }
                setWidgetValue(node, name, Number(control.value));
                updateTiming();
            });
            return field(label, control);
        }

        function choiceSetting(name, label, choices, title) {
            const control = selectInput(widgetValue(node, name, choices[0][0]), choices);
            control.title = title;
            control.addEventListener("change", () => {
                setWidgetValue(node, name, control.value);
                updateTiming();
            });
            return field(label, control);
        }

        const run = textSetting("run_name", "Run name", {
            fallback: "h3_chain", disabled: managed,
            title: managed
                ? "Managed by the connected Project Asset Carousel."
                : "Folder and checkpoint identity for this production.",
        });
        const fingerprint = textSetting(
            "generation_fingerprint", "Generation fingerprint", {
                fallback: "", disabled: managed, wide: true,
                title: managed
                    ? "Project Assets contributes the active reference lineage."
                    : "Change this when model, VAE, LoRA, CFG, sampler, scheduler, or global references change.",
            },
        );
        const project = group("Project", run, fingerprint);
        if (managed) {
            project.append(element(
                "div", "h3c-managed-note",
                "Run identity and reference lineage are managed by Project Assets.",
            ));
        }

        const canvas = group(
            "Canvas & context fitting",
            numberSetting("width", "Width", {
                fallback:960, min:32, max:4096, step:32,
                title:"Generation width; must be a multiple of 32.",
            }),
            numberSetting("height", "Height", {
                fallback:544, min:32, max:4096, step:32,
                title:"Generation height; must be a multiple of 32.",
            }),
            choiceSetting("encode_mode", "Context encoding", [
                ["video", "Video · preserve motion"],
                ["frames", "Frames · still anchors"],
            ], "How carried picture context is encoded."),
            choiceSetting("crop", "Context fit", [
                ["disabled", "Resize to canvas"],
                ["center", "Preserve ratio + center crop"],
            ], "How saved picture context is fitted to the Plan canvas."),
        );
        const defaults = group(
            "Generation defaults",
            numberSetting("default_duration_seconds", "Default seconds", {
                fallback:15, min:0.1, max:15.0833333333, step:0.01,
                title:"Used only when a scene has no explicit duration or frame length.",
            }),
            numberSetting("default_steps", "Default steps", {
                fallback:20, min:1, max:10000, step:1,
                title:"Used only when a scene has no sampler-step override.",
            }),
            textSetting("base_seed", "Base seed", {
                fallback:"0", numeric:true, wide:true,
                title:"Stable uint64 base used to derive one seed per scene.",
            }),
        );
        const delivery = group(
            "Delivery",
            numberSetting("segment_crf", "Scene MP4 quality (CRF)", {
                fallback:18, min:0, max:51, step:1,
                title:"Lower is higher quality; 18 is visually high quality.",
            }),
            numberSetting("video_blend_frames", "Default crossfade frames", {
                fallback:0, min:0, max:243, step:1,
                title:"Default final-assembly picture blend entering a scene; 0 is a hard cut.",
            }),
        );
        const grid = element("div", "h3c-settings-grid");
        grid.append(project, canvas, defaults, delivery);
        panel.append(heading, grid);
        return panel;
    }

    function render() {
        if (!state.plan) return;
        disconnectResizeObservers();
        state.seedRefreshers = [];
        const scrollTop = root.scrollTop;
        root.replaceChildren();
        root.classList.toggle("h3c-show-advanced", state.advanced);

        const externalPlanConnected = inputConnected(node, "plan_json_input");
        const header = element("div", "h3c-header");
        const openOutput = button(
            "Output",
            "Open this Plan's output/h3_chains/<run_name> folder on the ComfyUI host. " +
            "If the host has no desktop session, its path is copied instead.",
            async () => {
                const runName = String(widgetValue(node, "run_name", "")).trim();
                openOutput.disabled = true;
                setOutputButtonLabel(openOutput, "Opening…");
                try {
                    const response = await api.fetchApi(
                        "/minimax_h3_context_loop/open-run-folder",
                        {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({run_name: runName}),
                        },
                    );
                    const payload = await response.json();
                    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
                    openOutput.title = payload.path;
                    if (payload.opened) {
                        setOutputButtonLabel(openOutput, "Opened ✓");
                    } else {
                        try {
                            await navigator.clipboard.writeText(payload.path);
                            setOutputButtonLabel(openOutput, "Path copied");
                        } catch (_error) {
                            setOutputButtonLabel(openOutput, "See tooltip");
                        }
                        if (payload.error) openOutput.title += `\n${payload.error}`;
                    }
                } catch (error) {
                    setOutputButtonLabel(openOutput, "Open failed");
                    openOutput.title = String(error?.message || error);
                } finally {
                    setTimeout(() => {
                        setOutputButtonLabel(openOutput, "Output", true);
                        openOutput.disabled = false;
                    }, 2200);
                }
            },
        );
        openOutput.classList.add("h3c-open-output");
        openOutput.setAttribute("aria-label", "Open project output folder");
        setOutputButtonLabel(openOutput, "Output", true);
        const headerActions = element("div", "h3c-header-actions");
        if (modern) {
            headerActions.append(button(
                state.settingsOpen ? "Hide settings" : "Settings",
                "Show or hide the organized Plan settings",
                () => {
                    state.settingsOpen = !state.settingsOpen;
                    savePanelState();
                    render();
                },
            ));
        }
        headerActions.append(openOutput, element("div", "h3c-summary"));
        header.append(element(
            "div", "h3c-title",
            modern ? "MiniMax H3 Modern Plan" : "MiniMax H3 Scene Plan",
        ), headerActions);
        const settingsPanel = renderModernSettings();

        const externalNotice = element("div", "h3c-external-plan");
        externalNotice.append(
            element("strong", "", "External plan input connected. "),
            document.createTextNode(
                "A non-empty upstream string controls execution. The editor below " +
                "shows and edits the local fallback used only when that string is empty " +
                "or disconnected.",
            ),
        );

        const prefix = element("textarea", "h3c-prefix");
        prefix.value = sharedPrompt(state.plan).text;
        prefix.placeholder = "Identity, wardrobe, style and continuity rules shared by every scene…";
        prefix.title = "Text automatically prepended to every scene prompt. Put identity, wardrobe, reference definitions, audio rules, style, and global continuity here instead of repeating them.";
        prefix.spellcheck = true;
        prefix.addEventListener("input", () => {
            setSharedPrompt(state.plan, prefix.value);
            syncPlan();
        });
        bindTextareaHeight(prefix, "shared", 88);
        const prefixSection = element("section", "h3c-section");
        prefixSection.append(
            field("Shared prompt — automatically prepended to every scene", prefix),
            promptTools(prefix, null),
        );

        const toolbar = element("div", "h3c-toolbar");
        const add = button("+ Add scene", "Append a new scene", () => {
            if (state.plan.shots.length >= MAX_SHOTS) return;
            state.plan.shots.push(makeShot(state.plan.shots));
            syncPlan();
            render();
        });
        add.disabled = state.plan.shots.length >= MAX_SHOTS;
        const advanced = button(state.advanced ? "Hide advanced" : "Show advanced", "Show or hide per-scene sampler-step and continuation overrides", () => {
            state.advanced = !state.advanced;
            savePanelState();
            render();
        });
        const json = button(state.jsonOpen ? "Hide raw JSON" : "Raw JSON", "Expand the raw plan JSON for direct editing, import, or export", () => {
            state.jsonOpen = !state.jsonOpen;
            savePanelState();
            render();
        });
        toolbar.append(add, advanced, element("span", "h3c-spacer"), json);

        const errors = element("div", "h3c-errors");
        const cards = element("div", "h3c-cards");
        state.plan.shots.forEach((shot, index) => cards.append(renderCard(shot, index)));

        const jsonPanel = element("section", `h3c-section h3c-json-panel${state.jsonOpen ? " h3c-open" : ""}`);
        const jsonArea = element("textarea", "h3c-json");
        jsonArea.value = planToJson(state.plan);
        jsonArea.spellcheck = false;
        jsonArea.title = "Canonical plan JSON. Apply JSON replaces the visual editor contents after validation; saving the workflow also serializes this value.";
        bindTextareaHeight(jsonArea, "json", 260);
        const jsonStatus = element("span", "h3c-json-status", "Raw JSON escape hatch");
        const apply = button("Apply JSON", "Validate and load this JSON into the scene editor", () => {
            try {
                state.plan = parsePlanJson(jsonArea.value);
                jsonStatus.textContent = "JSON applied";
                syncPlan();
                render();
            } catch (error) {
                jsonStatus.textContent = error.message;
            }
        });
        const copyJson = button("Copy", "Copy plan JSON", async () => {
            try {
                await navigator.clipboard.writeText(jsonArea.value);
                jsonStatus.textContent = "Copied";
            } catch (_error) {
                jsonArea.select();
                document.execCommand("copy");
                jsonStatus.textContent = "Copied";
            }
        });
        const saveJson = button("Save .json", "Download this plan as JSON", () => {
            const runName = String(widgetValue(node, "run_name", "h3_chain"));
            downloadJson(jsonArea.value, `${runName || "h3_chain"}_plan.json`);
        });
        const loadInput = element("input");
        loadInput.type = "file";
        loadInput.accept = ".json,application/json";
        loadInput.hidden = true;
        loadInput.addEventListener("change", async () => {
            const file = loadInput.files?.[0];
            if (!file) return;
            jsonArea.value = await file.text();
            apply.click();
            loadInput.value = "";
        });
        const loadJson = button("Load .json", "Load a plan JSON file", () => loadInput.click());
        const jsonActions = element("div", "h3c-json-actions");
        jsonActions.append(apply, copyJson, saveJson, loadJson, loadInput, jsonStatus);
        jsonPanel.append(jsonArea, jsonActions);

        const footer = element("div", "h3c-footer");
        footer.append(
            element(
                "span",
                "",
                externalPlanConnected
                    ? "Edits are serialized into fallback plan_json; non-empty external JSON takes precedence."
                    : "Edits are serialized into plan_json; connect plan_json_input for an external override.",
            ),
            Object.assign(element("a", "", "UI inspiration: nkxx188"), {
                href: "https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy",
                target: "_blank",
                rel: "noreferrer",
            }),
        );

        root.append(
            header,
            ...(settingsPanel ? [settingsPanel] : []),
            ...(externalPlanConnected ? [externalNotice] : []),
            prefixSection,
            toolbar,
            errors,
            cards,
            jsonPanel,
            footer,
        );
        updateTiming();
        requestAnimationFrame(() => { root.scrollTop = scrollTop; });
        graphDirty();
    }

    function loadFromWidget(force = false) {
        const value = String(planWidget.value ?? "");
        if (!force && value === state.lastWidgetValue) return;
        try {
            state.plan = parsePlanJson(value);
            state.lastWidgetValue = value;
            render();
        } catch (error) {
            root.replaceChildren();
            const failure = element("div", "h3c-errors h3c-open", error.message);
            const raw = element("textarea", "h3c-json");
            raw.value = value;
            const retry = button("Apply repaired JSON", "Parse this JSON and open the scene editor", () => {
                planWidget.value = raw.value;
                state.lastWidgetValue = "";
                loadFromWidget(true);
            });
            root.append(failure, raw, retry);
        }
    }

    const originalCallback = planWidget.callback;
    planWidget.callback = function (...args) {
        const result = originalCallback?.apply(this, args);
        if (!state.syncing) setTimeout(() => loadFromWidget(), 0);
        return result;
    };

    for (const name of [
        "context_length", "audio_context_length", "encode_mode", "anchor_mode", "continuation_mode",
        "default_duration_seconds", "default_steps", "base_seed",
        "video_blend_frames",
    ]) {
        const widget = node.widgets?.find((item) => item.name === name);
        if (!widget || widget._h3TimingWrapped) continue;
        const callback = widget.callback;
        widget.callback = function (...args) {
            const result = callback?.apply(this, args);
            setTimeout(() => updateTiming(), 0);
            if (name === "base_seed") {
                setTimeout(() => {
                    for (const refresh of state.seedRefreshers) void refresh();
                }, 0);
            }
            if (name === "continuation_mode") {
                setTimeout(() => render(), 0);
            }
            return result;
        };
        widget._h3TimingWrapped = true;
    }

    node._h3ChainEditorRefresh = () => {
        collapseWidget(planWidget);
        collapseModernBackingWidgets(node);
        syncProjectAssetManagedWidgets();
        const layout = planLayout(node);
        state.advanced = Boolean(layout.advanced);
        state.jsonOpen = Boolean(layout.jsonOpen);
        state.settingsOpen = modern && layout.settingsOpen !== false;
        loadFromWidget(true);
        scheduleResponsiveSize();
    };
    node._h3ChainEditorConnectionRefresh = () => {
        syncProjectAssetManagedWidgets();
        scheduleResponsiveSize();
        render();
    };
    node._h3ChainEditorFit = applyResponsiveSize;
    syncProjectAssetManagedWidgets();
    loadFromWidget(true);
    scheduleResponsiveSize();
    const onLoRARoutesChanged = () => render();
    document.addEventListener("h3-lora-routes-changed", onLoRARoutesChanged);
    const removed = node.onRemoved;
    node.onRemoved = function () {
        disconnectResizeObservers();
        document.removeEventListener(
            "h3-lora-routes-changed", onLoRARoutesChanged);
        return removed?.apply(this, arguments);
    };
}

app.registerExtension({
    name: EXTENSION,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!NODE_NAMES.has(nodeData.name)) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            setTimeout(() => mountEditor(this), 0);
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            setTimeout(() => this._h3ChainEditorRefresh?.(), 0);
            return result;
        };

        const onGraphConfigured = nodeType.prototype.onGraphConfigured;
        nodeType.prototype.onGraphConfigured = function () {
            const result = onGraphConfigured?.apply(this, arguments);
            setTimeout(() => this._h3ChainEditorRefresh?.(), 0);
            return result;
        };

        const onAdded = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function () {
            const result = onAdded?.apply(this, arguments);
            // ComfyUI registers DOM widgets again when an existing node is
            // removed and re-added. Retire the hidden plan_json surface again.
            setTimeout(() => this._h3ChainEditorRefresh?.(), 0);
            return result;
        };

        const onConnectionsChange = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = onConnectionsChange?.apply(this, arguments);
            setTimeout(() => this._h3ChainEditorConnectionRefresh?.(), 0);
            return result;
        };

        if (nodeData.name === NODE_NAME) {
            const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
            nodeType.prototype.getExtraMenuOptions = function (_, options) {
                const result = getExtraMenuOptions?.apply(this, arguments);
                options.push({
                    content: "Upgrade to Modern Plan…",
                    callback: () => replaceWithModernPlan(this),
                });
                return result;
            };
        }
    },
});
