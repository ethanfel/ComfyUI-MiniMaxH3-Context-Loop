import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    coupledOutputDimensions,
    AUDIO_TRACK_ROLES,
    projectAudioTrackBindings,
    setProjectAudioTrack,
    dimensionsForMegapixels,
    formatMegapixels,
    imageMegapixels,
} from "./h3_project_asset_editor_core.mjs?v=0.7.9";
import {
    publishProjectAssetCatalogChanged,
    serializedProjectAssetCatalog,
    serializedProjectAssetIdentity,
} from "./h3_project_asset_sync_core.mjs?v=0.7.2";
import {
    projectMutationOptions,
    registerProjectOwnership,
} from "./h3_project_ownership.mjs?v=0.7.4";

const NODE_NAME = "MiniMaxH3ProjectAssetManager";
const SEMANTIC_SETTING_WIDGETS = [
    "semantic_anchor_size", "semantic_anchor_mode",
];
const PLAN_TYPES = new Set([
    "MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern",
    "MiniMaxH3ChainPlanStudio",
]);
const ROLES = {
    image: ["picture", "semantic_anchor"],
    video: ["video", "motion", "source_track"],
    audio: ["audio_reference", "source_track"],
};
const ROLE_LABELS = {
    picture: "picture reference",
    semantic_anchor: "semantic anchor",
    video: "video reference",
    motion: "motion reference",
    audio_reference: "tagged audio reference",
    source_track: "source track",
};

function displayRole(role) {
    return ROLE_LABELS[role] ?? String(role || "").replaceAll("_", " ");
}

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? "";
}

function widget(node, name) {
    return node?.widgets?.find((item) => item.name === name) ?? null;
}

function partialExecutionId(node) {
    const graph = node?.graph;
    const root = graph?.rootGraph ?? app.graph;
    if (!graph || !root || graph === root || graph.isRootGraph) return String(node.id);
    function pathTo(target, current) {
        for (const candidate of current?.nodes ?? current?._nodes ?? []) {
            const subgraph = candidate?.subgraph;
            if (!subgraph) continue;
            if (subgraph === target) return String(candidate.id);
            const child = pathTo(target, subgraph);
            if (child !== undefined) return `${candidate.id}:${child}`;
        }
        return undefined;
    }
    const parent = pathTo(graph, root);
    if (parent === undefined) {
        throw new Error("Could not resolve this Carousel inside its subgraph.");
    }
    return `${parent}:${node.id}`;
}

function el(tag, className = "", text = null) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== null) item.textContent = text;
    return item;
}

function button(label, action, title = "") {
    const item = el("button", "h3pa-button", label);
    item.type = "button";
    item.title = title;
    item.addEventListener("click", action);
    return item;
}

async function jsonRequest(route, options = {}) {
    const response = await api.fetchApi(route, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `${response.status}`);
    return payload;
}

function mediaUrl(project, asset, variant) {
    const query = new URLSearchParams({
        project, asset: String(asset.id), variant,
    });
    return api.apiURL(
        `/minimax_h3_context_loop/project-assets/media?${query}`,
    );
}

function formatBytes(value) {
    let number = Number(value) || 0;
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let index = 0;
    while (number >= 1024 && index < units.length - 1) {
        number /= 1024;
        index += 1;
    }
    return `${number.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatLyricsTimestamp(seconds) {
    const centiseconds = Math.max(0, Math.round((Number(seconds) || 0) * 100));
    const minutes = Math.floor(centiseconds / 6000);
    const remainder = centiseconds - minutes * 6000;
    return `[${String(minutes).padStart(2, "0")}:${String(
        Math.floor(remainder / 100),
    ).padStart(2, "0")}.${String(remainder % 100).padStart(2, "0")}]`;
}

function assetDetail(asset) {
    const meta = asset.metadata ?? {};
    const pieces = [asset.kind, formatBytes(asset.size)];
    if (meta.width && meta.height) pieces.push(`${meta.width}×${meta.height}`);
    if (meta.duration_seconds) pieces.push(`${Number(meta.duration_seconds).toFixed(2)}s`);
    if (meta.fps) pieces.push(`${Number(meta.fps).toFixed(3)} fps`);
    if (meta.sample_rate) pieces.push(`${meta.sample_rate} Hz`);
    if (meta.has_audio) pieces.push("embedded audio");
    return pieces.join(" · ");
}

function promptTag(asset) {
    const prefix = asset?.role === "semantic_anchor" ? "#" : "@";
    return `${prefix}${asset?.tag ?? ""}`;
}

function matchesTab(asset, filter) {
    if (filter === "all") return true;
    // Assignment is an orthogonal status. Unresolved templates stay visible
    // in their declared media/role category as well as the Unassigned queue.
    if (filter === "unassigned") return Boolean(asset._unresolved);
    if (filter === "semantic") return asset.role === "semantic_anchor";
    if (filter === "image") return asset.role === "picture";
    if (filter === "video") return ["video", "motion"].includes(asset.role);
    if (filter === "audio") return asset.role === "audio_reference";
    if (filter === "source_track") return asset.role === "source_track";
    return false;
}

function injectStyles() {
    if (document.getElementById("h3-project-assets-style")) return;
    const style = document.createElement("style");
    style.id = "h3-project-assets-style";
    style.textContent = `
        /* Keep text and surfaces paired with ComfyUI's palette. Dialogs live
           on body, so they need the same tokens as the in-node carousel. */
        .h3pa-root,.h3pa-modal{
          --h3pa-text:var(--input-text,#eee);--h3pa-bg:var(--comfy-menu-bg,#202124);
          --h3pa-panel:var(--comfy-input-bg,#151820);--h3pa-border:var(--border-color,#566174);
          --h3pa-muted:color-mix(in srgb,var(--h3pa-text) 82%,var(--h3pa-panel));
          --h3pa-soft:color-mix(in srgb,var(--h3pa-text) 6%,var(--h3pa-panel));
          --h3pa-accent:color-mix(in srgb,var(--h3pa-text) 65%,#3979da);
          --h3pa-selected:color-mix(in srgb,var(--h3pa-accent) 18%,var(--h3pa-panel));
          --h3pa-danger:color-mix(in srgb,var(--h3pa-text) 60%,#e53935);
          --h3pa-warning:color-mix(in srgb,var(--h3pa-text) 65%,#c88a26);
          --h3pa-success:color-mix(in srgb,var(--h3pa-text) 65%,#219653);
          color:var(--h3pa-text)}
        .h3pa-root{box-sizing:border-box;height:100%;min-height:520px;padding:12px;
          display:flex;flex-direction:column;gap:10px;overflow:hidden;color:var(--h3pa-text);
          background:var(--h3pa-bg);font:12px/1.35 system-ui}
        .h3pa-root *{box-sizing:border-box}.h3pa-row{display:flex;gap:7px;align-items:center;min-width:0}
        .h3pa-row input,.h3pa-row select,.h3pa-editor input,.h3pa-editor select,.h3pa-editor textarea{
          min-width:0;padding:6px 8px;border:1px solid var(--h3pa-border);border-radius:6px;
          background:var(--h3pa-panel);color:var(--h3pa-text)}.h3pa-project-picker{display:flex;flex:1;min-width:160px}.h3pa-project{flex:1;font-weight:650;border-radius:6px 0 0 6px!important}.h3pa-project-menu{flex:0 0 38px;width:38px;padding:6px 4px!important;border-left:0!important;border-radius:0 6px 6px 0!important;cursor:pointer}
        .h3pa-button{padding:6px 9px;border:1px solid var(--h3pa-border);border-radius:6px;
          background:var(--h3pa-panel);color:var(--h3pa-text);cursor:pointer}.h3pa-button:hover{border-color:var(--h3pa-accent)}
        .h3pa-status{min-height:18px;color:var(--h3pa-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .h3pa-ownership .h3pa-status{flex:1 1 auto;min-width:0}.h3pa-ownership .h3pa-button{flex:0 0 auto}
        .h3pa-tabs{display:flex;gap:5px;overflow-x:auto;align-items:center;flex:0 0 auto}.h3pa-tab.active{background:var(--h3pa-selected);border-color:var(--h3pa-accent)}
        .h3pa-folder-tools{display:flex;gap:5px;align-items:center;margin-left:auto;padding-left:7px;border-left:1px solid var(--h3pa-border);flex:0 0 auto}.h3pa-folder-tools select{max-width:190px;min-width:110px;padding:6px 8px;border:1px solid var(--h3pa-border);border-radius:6px;background:var(--h3pa-panel);color:var(--h3pa-text)}
        .h3pa-stage{flex:1 1 auto;min-height:230px;display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:10px;overflow:hidden}
        .h3pa-preview{position:relative;display:grid;place-items:center;min-width:0;min-height:220px;
          overflow:hidden;border:1px solid var(--h3pa-border);border-radius:9px;background:#090b10;color:#eee}
        .h3pa-preview>img,.h3pa-preview>video{position:absolute;inset:0;display:block;width:100%;height:100%;
          min-width:0;min-height:0;object-fit:contain}
        .h3pa-preview>.h3pa-empty{color:#9eabc0}
        .h3pa-preview audio{width:min(94%,650px)}.h3pa-empty{color:var(--h3pa-muted);text-align:center;padding:24px}
        .h3pa-preview.h3pa-audio-preview{background:var(--h3pa-bg);color:var(--h3pa-text);display:grid;grid-template-columns:minmax(240px,.72fr) minmax(300px,1.28fr);
          place-items:stretch;gap:18px;padding:18px}
        .h3pa-audio-player{display:flex;flex-direction:column;justify-content:center;align-items:center;gap:10px;
          min-width:0;padding:18px;border:1px solid var(--h3pa-border);border-radius:9px;background:var(--h3pa-panel)}
        .h3pa-audio-player strong{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .h3pa-audio-player audio{width:100%}
        .h3pa-lyrics{display:flex;flex-direction:column;min-width:0;min-height:0;padding:12px;border:1px solid var(--h3pa-border);
          border-radius:9px;background:var(--h3pa-panel);text-align:left}
        .h3pa-lyrics-head{display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:8px}
        .h3pa-lyrics-head strong{font-size:14px}.h3pa-lyrics-head small{color:var(--h3pa-muted);text-align:right}
        .h3pa-lyrics-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 8px}
        .h3pa-lyrics-time{color:var(--h3pa-muted);font-variant-numeric:tabular-nums}
        .h3pa-lyrics textarea{flex:1;min-width:0;min-height:150px;width:100%;padding:10px 11px;resize:none;
          border:1px solid var(--h3pa-border);border-radius:7px;background:var(--h3pa-panel);color:var(--h3pa-text);font:13px/1.55 system-ui;
          white-space:pre-wrap}.h3pa-lyrics textarea:focus{outline:1px solid var(--h3pa-accent);border-color:var(--h3pa-accent)}
        .h3pa-editor{display:flex;flex-direction:column;gap:8px;padding:10px;overflow:auto;border:1px solid
          var(--h3pa-border);border-radius:9px;background:var(--h3pa-panel)}
        .h3pa-editor label{display:flex;flex-direction:column;gap:3px;color:var(--h3pa-muted)}.h3pa-editor textarea{min-height:62px;resize:vertical}
        .h3pa-editor label.h3pa-toggle,.h3pa-crop-controls label.h3pa-toggle{display:grid;grid-template-columns:18px minmax(0,1fr);gap:8px;align-items:start;
          padding:8px;border:1px solid color-mix(in srgb,var(--h3pa-border) 72%,transparent);border-radius:7px;
          background:var(--h3pa-soft);cursor:pointer}
        .h3pa-editor .h3pa-toggle input,.h3pa-crop-controls .h3pa-toggle input{width:16px;height:16px;min-width:16px;margin:2px 0 0;padding:0;cursor:pointer}
        .h3pa-toggle-copy{display:flex;flex-direction:column;gap:2px;min-width:0}.h3pa-toggle-copy strong{color:inherit}
        .h3pa-toggle-copy small{color:var(--h3pa-muted);font-size:11px;line-height:1.3}
        .h3pa-carousel{position:relative;display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;padding:4px 1px 7px;min-height:126px;
          border:1px solid transparent;border-radius:9px;transition:border-color .12s ease,background .12s ease,box-shadow .12s ease}
        .h3pa-carousel.h3pa-drop-active{border-color:var(--h3pa-accent);background:var(--h3pa-selected);box-shadow:inset 0 0 0 2px color-mix(in srgb,var(--h3pa-accent) 35%,transparent)}
        .h3pa-carousel.h3pa-drop-active::before{content:"Drop to create assets";position:sticky;left:10px;align-self:flex-start;z-index:20;
          flex:0 0 auto;margin:7px 0 0 -1px;padding:6px 9px;border:1px dashed var(--h3pa-accent);border-radius:6px;
          background:var(--h3pa-panel);color:var(--h3pa-text);font-size:12px;font-weight:700;pointer-events:none}
        .h3pa-carousel-empty{flex:1 0 100%;display:grid;place-items:center;min-height:112px;color:var(--h3pa-muted);text-align:center}
        .h3pa-card{position:relative;flex:0 0 150px;height:112px;padding:0;border:1px solid var(--h3pa-border);border-radius:8px;
          overflow:hidden;background:var(--h3pa-panel);color:var(--h3pa-text);text-align:left;cursor:pointer}.h3pa-card.selected{border:2px solid var(--h3pa-accent)}
        .h3pa-card[draggable="true"]{cursor:grab}.h3pa-card.dragging{opacity:.42;cursor:grabbing}
        .h3pa-card.drag-over{border-color:var(--h3pa-success);box-shadow:0 0 0 2px color-mix(in srgb,var(--h3pa-success) 40%,transparent)}
        .h3pa-folder-group{flex:0 0 auto;display:flex;gap:8px;align-items:stretch;padding:0;border-radius:10px}
        .h3pa-folder-group.expanded{padding:4px;background:var(--h3pa-selected);box-shadow:inset 0 0 0 1px var(--h3pa-border)}
        .h3pa-folder-card{position:relative;flex:0 0 112px;height:112px;padding:7px;border:1px solid var(--h3pa-border);border-radius:10px;
          overflow:hidden;background:var(--h3pa-panel);color:var(--h3pa-text);text-align:left;cursor:pointer}
        .h3pa-folder-card:hover,.h3pa-folder-card.selected{border-color:var(--h3pa-accent)}.h3pa-folder-card.expanded{background:var(--h3pa-selected);border-color:var(--h3pa-accent)}
        .h3pa-folder-card.drag-over{border-color:var(--h3pa-success);box-shadow:0 0 0 2px color-mix(in srgb,var(--h3pa-success) 40%,transparent)}
        .h3pa-folder-mosaic{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:3px;height:70px;padding:3px;
          overflow:hidden;border-radius:7px;background:var(--h3pa-bg)}
        .h3pa-folder-tile{display:grid;place-items:center;min-width:0;min-height:0;overflow:hidden;border-radius:4px;background:var(--h3pa-soft);color:var(--h3pa-accent);font-size:15px}
        .h3pa-folder-tile img{display:block;width:100%;height:100%;object-fit:cover}.h3pa-folder-name{display:block;margin-top:5px;padding-right:22px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:650}.h3pa-folder-count{position:absolute;right:7px;bottom:7px;color:var(--h3pa-muted);font-size:10px}
        .h3pa-folder-member{border-color:var(--h3pa-accent)}
        .h3pa-card.unresolved{border-style:dashed;background:var(--h3pa-panel)}.h3pa-card.unresolved.stale{opacity:.58}
        .h3pa-card.unresolved .fallback{color:var(--h3pa-warning)}.h3pa-unassigned{color:var(--h3pa-warning);font-weight:650}
        .h3pa-card img{width:100%;height:78px;object-fit:cover;background:#090b10}.h3pa-card .fallback{height:78px;display:grid;
          place-items:center;font-size:24px;color:var(--h3pa-accent)}.h3pa-card span{display:block;padding:4px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .h3pa-badge{position:absolute;top:5px;left:5px;padding:2px 5px!important;border-radius:4px;background:#111c;color:#fff;font-size:10px}
        .h3pa-drag-handle{position:absolute;top:4px;right:4px;width:24px;padding:2px 4px!important;border-radius:4px;
          background:#111c;color:#dce8ff;text-align:center;font-size:14px;line-height:16px}
        .h3pa-editor-actions{display:flex;flex-direction:column;gap:6px;margin-top:auto;padding-top:8px;
          border-top:1px solid color-mix(in srgb,var(--h3pa-border) 55%,transparent)}
        .h3pa-editor-actions .h3pa-button{height:30px;padding:3px 7px;min-width:0;white-space:nowrap}
        .h3pa-action-label{color:var(--h3pa-muted);font-size:11px}.h3pa-action-primary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
        .h3pa-action-primary>.h3pa-button:only-child{grid-column:1/-1}
        .h3pa-action-manage{display:grid;grid-template-columns:34px 34px minmax(0,1fr);gap:6px}
        .h3pa-editor-actions .h3pa-button:disabled{opacity:.35;cursor:default}
        .h3pa-button.danger{border-color:var(--h3pa-danger);color:var(--h3pa-danger)}.h3pa-button.danger:hover{border-color:var(--h3pa-danger)}
        .h3pa-modal{position:fixed;z-index:100000;left:50%;top:50%;transform:translate(-50%,-50%);
          width:min(760px,calc(100vw - 32px));height:min(520px,calc(100vh - 48px));
          display:flex;flex-direction:column;gap:8px;padding:14px;
          border:1px solid var(--h3pa-border);border-radius:10px;background:var(--h3pa-bg);color:var(--h3pa-text);box-shadow:0 20px 70px #000b}
        .h3pa-source-list{overflow:auto;display:flex;flex-direction:column;gap:5px}.h3pa-source-item{display:grid;
          grid-template-columns:1fr auto auto;gap:8px;align-items:center;padding:7px;border:1px solid var(--h3pa-border);border-radius:6px}
        .h3pa-crop-modal{width:min(1180px,calc(100vw - 28px));height:min(780px,calc(100vh - 32px))}
        .h3pa-crop-layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:12px;min-height:0;flex:1}
        .h3pa-crop-canvas-wrap{min-width:0;min-height:0;display:grid;place-items:center;overflow:hidden;border:1px solid var(--h3pa-border);border-radius:8px;background:#080a0f}
        @media (max-width:900px){.h3pa-preview.h3pa-audio-preview{grid-template-columns:1fr;grid-template-rows:auto minmax(180px,1fr)}}
        .h3pa-crop-canvas{display:block;width:100%;height:100%;touch-action:none;cursor:crosshair;outline:none;user-select:none}
        .h3pa-crop-controls{display:flex;flex-direction:column;gap:9px;min-height:0;overflow:auto;padding:10px;border:1px solid var(--h3pa-border);border-radius:8px;background:var(--h3pa-panel)}
        .h3pa-crop-controls label{display:flex;flex-direction:column;gap:3px;color:var(--h3pa-muted)}.h3pa-crop-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}
        .h3pa-crop-controls input,.h3pa-crop-controls select{width:100%;min-width:0;padding:6px 7px;border:1px solid var(--h3pa-border);border-radius:6px;background:var(--h3pa-panel);color:var(--h3pa-text)}
        .h3pa-mp-presets{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:4px}.h3pa-mp-presets .h3pa-button{min-width:0;padding:4px 2px;font-size:10px;white-space:nowrap}
        .h3pa-snap-options{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;align-items:center}.h3pa-snap-options .h3pa-button{min-width:0;padding:4px 3px;white-space:nowrap}
        .h3pa-snap-options .h3pa-button.active{background:var(--h3pa-selected);border-color:var(--h3pa-accent)}.h3pa-snap-label{grid-column:1/-1;color:var(--h3pa-muted);font-size:11px}
        .h3pa-size-summary{padding:8px;border:1px solid var(--h3pa-border);border-radius:7px;background:var(--h3pa-soft);color:var(--h3pa-muted);line-height:1.4}
        .h3pa-crop-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto}.h3pa-crop-note{color:var(--h3pa-muted);font-size:11px}
        @media(max-width:850px){.h3pa-crop-layout{grid-template-columns:1fr}.h3pa-crop-controls{max-height:320px}}
        @media(max-width:800px){.h3pa-stage{grid-template-columns:1fr}.h3pa-editor{max-height:190px}}
    `;
    document.head.appendChild(style);
}

function collapseWidget(item) {
    if (!item || item._h3paCollapsed) return;
    item._h3paOriginalPresentation = {
        type: item.type,
        hidden: item.hidden,
        computeSize: item.computeSize,
        draw: item.draw,
    };
    item._h3paCollapsed = true;
    item.hidden = true;
    item.computeSize = () => [0, -4];
    item.type = "hidden";
    item.draw = () => {};
}

function restoreWidget(item) {
    if (!item?._h3paCollapsed) return;
    const original = item._h3paOriginalPresentation ?? {};
    item.type = original.type;
    item.hidden = original.hidden;
    item.computeSize = original.computeSize;
    item.draw = original.draw;
    item._h3paCollapsed = false;
}

function refreshSemanticSettingVisibility(node) {
    const visible = node?.properties?.h3_show_semantic_anchor_settings !== false;
    for (const name of SEMANTIC_SETTING_WIDGETS) {
        const item = widget(node, name);
        if (visible) restoreWidget(item);
        else collapseWidget(item);
    }
    node?.graph?.setDirtyCanvas?.(true, true);
}

function syncDownstreamPlan(node, runName) {
    for (const output of node.outputs ?? []) {
        for (const linkId of output.links ?? []) {
            const link = node.graph?.links?.[linkId];
            const target = link ? node.graph?.getNodeById?.(link.target_id) : null;
            if (!target || !PLAN_TYPES.has(nodeType(target))) continue;
            const run = widget(target, "run_name");
            if (run && run.value !== runName) {
                run.value = runName;
                run.callback?.(runName);
            }
        }
    }
}

function downstreamPlanRunName(node) {
    for (const output of node.outputs ?? []) {
        for (const linkId of output.links ?? []) {
            const link = node.graph?.links?.[linkId];
            const target = link ? node.graph?.getNodeById?.(link.target_id) : null;
            if (!target || !PLAN_TYPES.has(nodeType(target))) continue;
            const value = String(widget(target, "run_name")?.value ?? "").trim();
            if (value) return value;
        }
    }
    return "";
}

function mount(node) {
    if (node._h3ProjectAssetMounted) return;
    node._h3ProjectAssetMounted = true;
    injectStyles();
    const runNameWidget = widget(node, "run_name");
    const catalogWidget = widget(node, "catalog_json");
    const operationWidget = widget(node, "operation_json");
    const ownershipWidget = widget(node, "ownership_json");
    [runNameWidget, catalogWidget, operationWidget, ownershipWidget]
        .forEach(collapseWidget);
    if (ownershipWidget) {
        ownershipWidget.value = "";
        // ComfyUI deliberately separates workflow persistence
        // (widget.serialize) from API-prompt inclusion
        // (widget.options.serialize). The proof must take the latter path
        // only: execution needs it, saved workflows must not retain it.
        ownershipWidget.serialize = false;
    }

    const root = el("div", "h3pa-root");
    const top = el("div", "h3pa-row");
    const runNameInput = el("input", "h3pa-project");
    runNameInput.placeholder = "Run name";
    runNameInput.title = "Type a new Run name, then press Enter or leave the field to load it. Spaces are converted to underscores when the name is committed, or choose an existing Asset Carousel project to switch this Carousel and its connected Plan.";
    runNameInput.setAttribute("aria-label", "Run name");
    runNameInput.autocomplete = "off";
    const projectPicker = el("div", "h3pa-project-picker");
    const projectMenu = el("select", "h3pa-project-menu");
    projectMenu.title = "Switch to an existing Asset Carousel project";
    projectMenu.setAttribute("aria-label", "Switch Asset Carousel project");
    const projectMenuPlaceholder = el("option", "", "▾");
    projectMenuPlaceholder.value = "";
    projectMenu.append(projectMenuPlaceholder);
    projectPicker.append(runNameInput, projectMenu);
    const savedRunName = serializedProjectAssetIdentity(
        runNameWidget?.value, catalogWidget?.value,
    );
    const connectedRunName = downstreamPlanRunName(node);
    runNameInput.value = savedRunName || connectedRunName;
    if (runNameWidget && runNameWidget.value !== runNameInput.value) {
        runNameWidget.value = runNameInput.value;
        runNameWidget.callback?.(runNameInput.value);
    }
    const sourceSelect = el("select");
    for (const [value, label] of [
        ["input", "ComfyUI input"], ["project", "Other Run"],
        ["chains", "H3 backups"],
    ]) {
        const option = el("option", "", label); option.value = value;
        sourceSelect.append(option);
    }
    sourceSelect.title = "Choose where Import browses for media to copy into this Run. Other Run reads the live Asset Carousel; H3 backups reads recovery snapshots.";
    const previewSelect = el("select");
    for (const [value, label] of [
        ["light", "Light previews"],
        ["full", "Full previews"],
    ]) {
        const option = el("option", "", label); option.value = value;
        previewSelect.append(option);
    }
    previewSelect.value = String(
        node.properties?.h3_project_asset_preview_mode ?? "light");
    previewSelect.title = "Display quality for this Carousel only. Generation always uses the original stored asset. Light uses cached thumbnails and bounded JPEG previews; Full loads the selected original into the Carousel UI.";
    previewSelect.setAttribute("aria-label", "Carousel display preview quality; generation always uses original assets");
    top.append(el("span", "", "Run name"), projectPicker, sourceSelect, previewSelect);
    const fileInput = el("input");
    fileInput.type = "file"; fileInput.accept = "image/*,video/*,audio/*";
    fileInput.hidden = true;
    top.append(
        button("Upload", () => {
            state.bindingSlot = selectedUnassignedSlot();
            fileInput.click();
        }, "Choose a local media file, or drop files directly onto the Carousel. A selected Unassigned slot is bound; otherwise a new asset is created."),
        button("Import", () => browseSource(selectedUnassignedSlot()),
            "Create a project asset from the selected source: ComfyUI input, another Run, or an H3 backup. A selected Unassigned slot is bound instead."),
        button("Duplicate project…", () => duplicateAssetProject(),
            "Create a new Run containing this asset catalog, folders, lyrics, and project-owned media. Generated clips, checkpoints, and assembled renders are not copied. This Carousel stays on the current Run."),
        button("Refresh", () => refresh()), fileInput,
    );
    const status = el("div", "h3pa-status", "Loading project assets…");
    const ownershipRow = el("div", "h3pa-row h3pa-ownership");
    const ownershipStatus = el("span", "h3pa-status", "Checking workflow ownership…");
    const forceOwnership = button("Force ownership", () => {},
        "Invalidate the previous workflow owner and make this workflow the only project writer.");
    const releaseOwnership = button("Release", () => {},
        "Release this project's workflow ownership. The next workflow must claim it before writing.");
    ownershipRow.append(ownershipStatus, forceOwnership, releaseOwnership);
    const tabs = el("div", "h3pa-tabs");
    const stage = el("div", "h3pa-stage");
    const preview = el("div", "h3pa-preview");
    const editor = el("div", "h3pa-editor");
    stage.append(preview, editor);
    const carousel = el("div", "h3pa-carousel");
    carousel.title = "Drop one or more image, video, or audio files here to create project assets immediately.";
    root.append(top, ownershipRow, status, tabs, stage, carousel);
    const dom = node.addDOMWidget("project_asset_carousel", "div", root, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 560,
    });
    dom.serialize = false;
    node.setSize?.([Math.max(node.size?.[0] ?? 680, 760), Math.max(node.size?.[1] ?? 650, 700)]);

    const savedCatalog = serializedProjectAssetCatalog(
        catalogWidget?.value, runNameInput.value,
    );
    const state = {
        catalog:savedCatalog ?? {assets: [], reference_slots: [], folders: []}, selected: "",
        filter: "all", folder: "", media: null, bindingSlot: null,
        dragging: "", uploading: false, duplicatingProject: false,
        projects: [], projectNames: new Set(),
        expandedFolders: new Set(Array.isArray(
            node.properties?.h3_project_asset_expanded_folders,
        ) ? node.properties.h3_project_asset_expanded_folders.map(String) : []),
        previewMode: previewSelect.value === "full" ? "full" : "light",
    };
    // The text field is only a draft until switchProject finishes flushing
    // the previous Run. Every mutation continues to target the serialized,
    // committed widget value meanwhile.
    const project = () => String(
        runNameWidget?.value ?? runNameInput.value ?? "",
    ).trim();
    // Review Gate capture must target the committed Run, never a rename draft.
    node._h3ProjectAssetCurrentProject = project;
    const validProjectName = (value) => (
        /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?$/.test(
            String(value ?? "").trim(),
        )
    );
    function showOwnership(payload) {
        const proof = ownership.proof();
        if (ownershipWidget) ownershipWidget.value = proof
            ? JSON.stringify(proof) : "";
        if (!payload) {
            ownershipStatus.textContent = project()
                ? "Checking workflow ownership…" : "No project selected.";
            forceOwnership.hidden = true;
            releaseOwnership.hidden = true;
            return;
        }
        if (payload.owned_by_requester) {
            ownershipStatus.textContent = "Owner: this workflow · project writes enabled";
            ownershipStatus.style.color = "var(--h3pa-success)";
            forceOwnership.hidden = true;
            releaseOwnership.hidden = false;
        } else {
            const owner = payload.owner_label || "another workflow";
            ownershipStatus.textContent = payload.available
                ? "Ownership is available; claim before writing."
                : payload.expired
                    ? `Read-only here · stale owner: ${owner} · Force to take over`
                    : `Read-only here · owner: ${owner}`;
            ownershipStatus.style.color = payload.available
                ? "var(--h3pa-warning)" : "var(--h3pa-danger)";
            forceOwnership.hidden = false;
            forceOwnership.textContent = payload.available ? "Take ownership" : "Force ownership";
            releaseOwnership.hidden = true;
        }
    }
    const ownership = registerProjectOwnership(node, showOwnership);
    forceOwnership.onclick = async () => {
        if (!project()) return;
        if (ownership.status && !ownership.status.available
                && !window.confirm(
                    `Force ownership of ${project()}?\n\n` +
                    "The previous workflow will immediately become read-only, including any render that has not published its checkpoint yet.",
                )) return;
        try {
            await ownership.force();
            setStatus(`This workflow now owns ${project()}.`);
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    };
    releaseOwnership.onclick = async () => {
        try {
            await ownership.release();
            setStatus(`Released ownership of ${project()}.`);
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    };
    // Tokens distinguish A -> B -> A from an uninterrupted operation on A.
    let projectEpoch = 0;
    let projectDisposed = false;
    function captureProjectOperation() {
        return {run:project(), epoch:projectEpoch};
    }
    function isCurrentProjectOperation(operation) {
        return !projectDisposed && operation.epoch === projectEpoch
            && operation.run === project();
    }
    function requireCurrentProjectOperation(operation) {
        if (isCurrentProjectOperation(operation)) return;
        const error = new Error("The Run changed; the old asset operation will not update this Carousel.");
        error.staleProject = true;
        throw error;
    }
    async function mutationRequest(route, options = {}, operation = captureProjectOperation()) {
        requireCurrentProjectOperation(operation);
        if (state.catalog?.project && state.catalog.project !== operation.run) {
            throw new Error("Wait for the selected Run's assets to finish loading.");
        }
        if (ownership.runName !== operation.run) {
            await ownership.select(operation.run);
            requireCurrentProjectOperation(operation);
        }
        options = await projectMutationOptions(node, operation.run, options);
        requireCurrentProjectOperation(operation);
        let result;
        try {
            result = await jsonRequest(route, options);
        } catch (error) {
            requireCurrentProjectOperation(operation);
            throw error;
        }
        requireCurrentProjectOperation(operation);
        return result;
    }
    let refreshSequence = 0;
    const graphSyncTimers = new Set();
    function setStatus(text, error = false) {
        status.textContent = text;
        status.style.color = error ? "var(--h3pa-danger)" : "";
    }
    function stopMedia() {
        if (state.media?.pause) state.media.pause();
        state.media = null;
        preview.classList.remove("h3pa-audio-preview");
        preview.replaceChildren();
    }
    async function refreshProjectSuggestions() {
        try {
            const payload = await jsonRequest(
                "/minimax_h3_context_loop/project-assets/projects");
            const projects = Array.isArray(payload.items) ? payload.items : [];
            state.projects = projects;
            state.projectNames = new Set(projects.map(
                (item) => String(item.project ?? ""),
            ).filter(Boolean));
            projectMenu.replaceChildren(projectMenuPlaceholder);
            for (const item of projects) {
                const option = el("option");
                option.value = String(item.project ?? "");
                option.textContent = (
                    `${String(item.project ?? "")} — ` +
                    `${Number(item.asset_count ?? 0)} asset${Number(item.asset_count ?? 0) === 1 ? "" : "s"}` +
                    ` · ${Number(item.unassigned_count ?? 0)} unassigned`
                );
                projectMenu.append(option);
            }
        } catch {
            state.projects = [];
            state.projectNames = new Set();
            projectMenu.replaceChildren(projectMenuPlaceholder);
        }
    }
    function persistCatalog(catalog) {
        if (projectDisposed || (catalog?.project && catalog.project !== project())) return false;
        state.catalog = catalog ?? {assets: [], reference_slots: [], folders: []};
        state.catalog.folders ??= [];
        const canonicalProject = String(state.catalog.project || project());
        runNameInput.value = canonicalProject;
        if (catalogWidget) {
            catalogWidget.value = JSON.stringify(state.catalog);
            catalogWidget.callback?.(catalogWidget.value);
        }
        runNameWidget.value = canonicalProject;
        runNameWidget.callback?.(canonicalProject);
        syncDownstreamPlan(node, canonicalProject);
        publishProjectAssetCatalogChanged(node, state.catalog);
        node.graph?.setDirtyCanvas?.(true, true);
    }
    function allItems() {
        return [
            ...(state.catalog.assets ?? []),
            ...(state.catalog.reference_slots ?? []).map((slot) => ({
                ...slot, _unresolved: true,
            })),
        ];
    }
    function selectedUnassignedSlot() {
        return allItems().find((asset) => (
            asset.id === state.selected && asset._unresolved
        )) ?? null;
    }
    function saveExpandedFolders() {
        node.properties ??= {};
        node.properties.h3_project_asset_expanded_folders = [
            ...state.expandedFolders,
        ];
        node.graph?.setDirtyCanvas?.(true, true);
    }
    function setFolderExpanded(folderId, expanded) {
        const id = String(folderId ?? "");
        if (!id) return;
        if (expanded) state.expandedFolders.add(id);
        else state.expandedFolders.delete(id);
        saveExpandedFolders();
    }
    function filteredAssets() {
        return allItems().filter((asset) => matchesTab(asset, state.filter));
    }
    function renderTabs() {
        tabs.replaceChildren();
        for (const [key, label] of [["all", "All"], ["image", "Images"],
            ["semantic", "Semantic"], ["video", "Video"],
            ["audio", "Audio"], ["unassigned", "Unassigned"],
            ["source_track", "Source track"]]) {
            const count = allItems().filter((asset) => matchesTab(asset, key)).length;
            const item = button(`${label} ${count}`, () => {
                state.filter = key; render();
            });
            item.classList.add("h3pa-tab");
            item.classList.toggle("active", state.filter === key);
            tabs.append(item);
        }
    }
    async function folderRequest(body, success) {
        try {
            setStatus("Saving asset folders…");
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/folder", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({project: project(), ...body}),
                });
            persistCatalog(result.catalog); render(); setStatus(success);
            return result;
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); return null; }
    }
    function renderFolders() {
        const assetList = state.catalog.assets ?? [];
        const folderList = state.catalog.folders ?? [];
        const tools = el("div", "h3pa-folder-tools");
        const addFolder = button("+ Folder", async () => {
            const name = window.prompt("New asset folder name");
            if (!name?.trim()) return;
            const result = await folderRequest(
                {action: "create", name}, `Created folder ${name.trim()}.`,
            );
            if (result?.folder?.id) {
                state.folder = result.folder.id;
                setFolderExpanded(result.folder.id, true);
                render();
            }
        }, "Create a presentation-only folder. Folders never affect prompts, fingerprints, or generation.");
        tools.append(addFolder);
        if (!folderList.length) {
            state.folder = "";
            tabs.append(tools);
            return;
        }
        const folderSelect = el("select");
        folderSelect.title = "Choose a folder to rename or remove. Folder cards in the Carousel expand and collapse their assets.";
        const placeholder = el("option", "", "Manage folders…");
        placeholder.value = ""; folderSelect.append(placeholder);
        for (const folder of folderList.map((folder) => ({
            ...folder,
            count: assetList.filter((asset) => asset.folder_id === folder.id).length,
        }))) {
            const option = el("option", "", `${folder.name} (${folder.count})`);
            option.value = folder.id;
            folderSelect.append(option);
        }
        folderSelect.value = state.folder;
        folderSelect.addEventListener("change", () => {
            state.folder = folderSelect.value;
            if (state.folder) setFolderExpanded(state.folder, true);
            render();
        });
        tools.append(folderSelect);
        const active = folderList.find(
            (folder) => folder.id === state.folder,
        );
        if (active) {
            tools.append(
                button("Rename", async () => {
                    const name = window.prompt("Rename asset folder", active.name);
                    if (!name?.trim() || name.trim() === active.name) return;
                    await folderRequest({
                        action: "update", folder_id: active.id,
                        changes: {name: name.trim()},
                    }, `Renamed folder to ${name.trim()}.`);
                }),
                button("Remove folder", async () => {
                    if (!window.confirm(
                        `Remove folder ${active.name}? Its assets will move to No folder; no media is deleted.`,
                    )) return;
                    const result = await folderRequest({
                        action: "delete", folder_id: active.id,
                    }, `Removed folder ${active.name}.`);
                    if (result) {
                        state.expandedFolders.delete(active.id);
                        saveExpandedFolders();
                        state.folder = "";
                        render();
                    }
                }),
            );
        }
        tabs.append(tools);
    }
    function renderPreview(asset) {
        const operation = captureProjectOperation();
        stopMedia();
        if (!asset) {
            preview.append(el("div", "h3pa-empty", "Upload or import a project asset."));
            return;
        }
        if (asset._unresolved) {
            preview.append(el(
                "div", "h3pa-empty",
                asset.available === false
                    ? `${promptTag(asset)} is no longer present on the connected reference line.`
                    : `${promptTag(asset)} is unassigned. Choose its ${asset.kind} from ComfyUI input or upload it.`,
            ));
            return;
        }
        if (asset.kind === "image") {
            const image = el("img");
            image.alt = asset.tag;
            image.src = mediaUrl(
                project(), asset,
                state.previewMode === "full" ? "original" : "poster",
            );
            preview.append(image); state.media = image;
        } else if (asset.kind === "video") {
            const video = el("video");
            video.controls = true; video.preload = "metadata";
            video.poster = mediaUrl(project(), asset, "poster");
            video.src = mediaUrl(project(), asset, "preview");
            preview.append(video); state.media = video;
        } else {
            preview.classList.add("h3pa-audio-preview");
            const player = el("section", "h3pa-audio-player");
            player.append(el("strong", "", asset.original_name || promptTag(asset)));
            const audio = el("audio");
            audio.controls = true; audio.preload = "metadata";
            audio.src = mediaUrl(project(), asset, "original");
            player.append(audio);
            const lyricsPanel = el("section", "h3pa-lyrics");
            const lyricsHead = el("div", "h3pa-lyrics-head");
            lyricsHead.append(
                el("strong", "", "Lyrics"),
                el("small", "", "Saved with the project · generation unchanged"),
            );
            const lyrics = el("textarea");
            lyrics.value = String(asset.lyrics ?? "");
            lyrics.placeholder = "Paste lyrics, then stamp each line while the song plays…";
            lyrics.title = "Lyrics are project notes stored with this audio asset. Add [MM:SS.xx] timestamps or paste SRT to show them in sync in Plan Studio. They do not enter prompts, generation, or the reference fingerprint. Changes save when you leave the field; Ctrl/Cmd+Enter saves immediately.";
            lyrics.setAttribute("aria-label", `Lyrics for ${asset.original_name || promptTag(asset)}`);
            let savedLyrics = lyrics.value;
            lyrics.addEventListener("change", async () => {
                const nextLyrics = lyrics.value.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
                if (nextLyrics === savedLyrics) return;
                const result = await updateAsset(
                    asset, {lyrics: nextLyrics}, {
                        operation, renderAfter: false,
                        success: `Saved lyrics for ${promptTag(asset)}.`,
                    },
                );
                if (!result) return;
                savedLyrics = String(result.asset?.lyrics ?? "");
                lyrics.value = savedLyrics;
            });
            lyrics.addEventListener("keydown", (event) => {
                if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
                event.preventDefault(); lyrics.blur();
            });
            const lyricsActions = el("div", "h3pa-lyrics-actions");
            const currentTime = el(
                "span", "h3pa-lyrics-time", formatLyricsTimestamp(0),
            );
            audio.addEventListener("timeupdate", () => {
                currentTime.textContent = formatLyricsTimestamp(audio.currentTime);
            });
            const stamp = button(
                "Stamp selected line",
                () => {
                    const caret = lyrics.selectionStart ?? 0;
                    const start = lyrics.value.lastIndexOf("\n", Math.max(0, caret - 1)) + 1;
                    const nextBreak = lyrics.value.indexOf("\n", caret);
                    const end = nextBreak < 0 ? lyrics.value.length : nextBreak;
                    const line = lyrics.value.slice(start, end)
                        .replace(/^(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+\s*/, "");
                    const stamped = `${formatLyricsTimestamp(audio.currentTime)}${line}`;
                    lyrics.setRangeText(stamped, start, end, "end");
                    const nextStart = Math.min(
                        lyrics.value.length,
                        start + stamped.length + (nextBreak < 0 ? 0 : 1),
                    );
                    const followingBreak = lyrics.value.indexOf("\n", nextStart);
                    lyrics.setSelectionRange(
                        nextStart,
                        followingBreak < 0 ? lyrics.value.length : followingBreak,
                    );
                    lyrics.focus();
                },
                "Insert the player's current [MM:SS.xx] time at the beginning of the selected lyric line, then select the next line.",
            );
            lyricsActions.append(stamp, currentTime);
            lyricsPanel.append(lyricsHead, lyricsActions, lyrics);
            preview.append(player, lyricsPanel); state.media = audio;
        }
    }
    async function updateAsset(asset, changes, options = {}) {
        const operation = options.operation ?? captureProjectOperation();
        try {
            requireCurrentProjectOperation(operation);
            setStatus(`Updating ${promptTag(asset)}…`);
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/update", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({project: operation.run, asset_id: asset.id, changes}),
                }, operation);
            persistCatalog(result.catalog);
            if (options.renderAfter !== false) render();
            setStatus(options.success || `Updated ${promptTag(result.asset)}.`);
            return result;
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); return null; }
    }
    async function reorderAssets(assetIds) {
        try {
            setStatus("Saving asset order…");
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/reorder", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({project: project(), asset_ids: assetIds}),
                });
            persistCatalog(result.catalog); render();
            setStatus("Asset order saved.");
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    }
    function moveAsset(asset, offset) {
        const assetIds = (state.catalog.assets ?? []).map((item) => item.id);
        const index = assetIds.indexOf(asset.id);
        const target = Math.max(0, Math.min(assetIds.length - 1, index + offset));
        if (index < 0 || index === target) return;
        assetIds.splice(index, 1);
        assetIds.splice(target, 0, asset.id);
        void reorderAssets(assetIds);
    }
    async function deleteAsset(asset) {
        const confirmed = window.confirm(
            `Delete ${promptTag(asset)} from this project?\n\n` +
            "This removes the Carousel-owned input copy and its H3 backup. " +
            "The original file you imported is not touched.",
        );
        if (!confirmed) return;
        const previous = state.catalog.assets ?? [];
        const previousIndex = previous.findIndex((item) => item.id === asset.id);
        try {
            setStatus(`Deleting ${promptTag(asset)}…`);
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/delete", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({project: project(), asset_id: asset.id}),
                });
            persistCatalog(result.catalog);
            const remaining = result.catalog.assets ?? [];
            state.selected = remaining[
                Math.min(Math.max(previousIndex, 0), remaining.length - 1)
            ]?.id ?? "";
            render();
            setStatus(`Deleted ${promptTag(result.asset)}.`);
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    }
    async function duplicateAsset(asset) {
        try {
            setStatus(`Duplicating ${promptTag(asset)}…`);
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/duplicate", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({project: project(), asset_id: asset.id}),
                });
            persistCatalog(result.catalog); state.selected = result.asset.id; render();
            setStatus(`Duplicated ${promptTag(asset)} as ${promptTag(result.asset)} without copying media bytes.`);
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    }
    async function duplicateAssetProject() {
        const sourceProject = project();
        if (!sourceProject) {
            setStatus("Enter a Run name before duplicating its asset project.", true);
            return;
        }
        if (state.duplicatingProject) {
            setStatus("This asset project is already being duplicated.");
            return;
        }
        const requested = window.prompt(
            `New Run name for a copy of ${sourceProject}\n\n` +
            "Only Carousel assets, folders, lyrics, and their recovery mirror are copied. Generated clips, checkpoints, and rendered outputs are not copied.",
            `${sourceProject}_copy`,
        );
        if (requested === null) return;
        const newProject = String(requested).trim();
        if (!newProject) {
            setStatus("The duplicated asset project needs a new Run name.", true);
            return;
        }
        state.duplicatingProject = true;
        try {
            setStatus(`Duplicating ${sourceProject} assets into ${newProject}…`);
            const result = await mutationRequest(
                "/minimax_h3_context_loop/project-assets/duplicate-project", {
                    method: "POST", headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        project: sourceProject, new_project: newProject,
                    }),
                });
            setStatus(
                `Created ${result.target_project} with ${result.asset_count} asset${result.asset_count === 1 ? "" : "s"} ` +
                `and ${result.media_file_count} copied media file${result.media_file_count === 1 ? "" : "s"}. ` +
                "No generated clips, checkpoints, or renders were copied; the current Run remains selected.",
            );
            await refreshProjectSuggestions();
        } catch (error) {
            if (!error.staleProject) setStatus(error.message, true);
        } finally {
            state.duplicatingProject = false;
        }
    }
    function openImageEditor(asset) {
        const operation = captureProjectOperation();
        const modal = el("div", "h3pa-modal h3pa-crop-modal");
        const heading = el("div", "h3pa-row");
        heading.append(
            el("strong", "h3pa-project", `Edit / upscale image variant · ${promptTag(asset)}`),
            button("Close", () => modal.remove()),
        );
        const layout = el("div", "h3pa-crop-layout");
        const canvasWrap = el("div", "h3pa-crop-canvas-wrap");
        const canvas = el("canvas", "h3pa-crop-canvas"); canvas.tabIndex = 0;
        canvas.setAttribute("aria-label", "Crop selector; drag outside the crop to draw, drag inside to move, drag any corner to resize, arrow keys nudge");
        canvasWrap.append(canvas);
        const controls = el("div", "h3pa-crop-controls");
        layout.append(canvasWrap, controls); modal.append(heading, layout); document.body.append(modal);

        const image = new Image(); image.decoding = "async";
        const crop = {x: 0, y: 0, width: 1, height: 1};
        let sourceWidth = 1; let sourceHeight = 1;
        let drawScale = 1; let drawX = 0; let drawY = 0;
        let drag = null;
        let lockedRatio = 1;
        let unlockedRatio = 1;
        let outputMultiple = 8;
        let requestedMegapixels = 0.01;
        const cropInputs = {};
        function numberField(name, label, value, minimum = 0) {
            const wrapper = el("label", "", label);
            const input = el("input"); input.type = "number"; input.step = "1";
            input.min = String(minimum); input.value = String(value);
            wrapper.append(input); cropInputs[name] = input; return wrapper;
        }
        const cropGrid = el("div", "h3pa-crop-grid");
        cropGrid.append(
            numberField("x", "Crop X", 0), numberField("y", "Crop Y", 0),
            numberField("width", "Crop width", 1, 1),
            numberField("height", "Crop height", 1, 1),
        );
        const targetGrid = el("div", "h3pa-crop-grid");
        targetGrid.append(
            numberField("targetWidth", "Output width", 1, 1),
            numberField("targetHeight", "Output height", 1, 1),
        );
        const megapixelLabel = el("label", "", "Target megapixels");
        megapixelLabel.title = "Set the final asset size by megapixels. This works with the full image or a crop, and with ordinary resampling or the connected upscale model.";
        const megapixelInput = el("input"); megapixelInput.type = "number";
        megapixelInput.min = "0.01"; megapixelInput.step = "0.05";
        megapixelInput.value = "0.01"; megapixelLabel.append(megapixelInput);
        const megapixelPresets = el("div", "h3pa-mp-presets");
        for (const value of [0.25, 0.5, 1, 2, 4, 8]) {
            megapixelPresets.append(button(`${value} MP`, () => {
                megapixelInput.value = String(value);
                applyMegapixelTarget();
            }, `Set the final image near ${value} megapixels while preserving the current output ratio`));
        }
        const snapOptions = el("div", "h3pa-snap-options");
        snapOptions.title = "Round both dimensions to the nearest model-friendly multiple. The requested megapixels stay unchanged; actual size and aspect ratio may vary slightly.";
        snapOptions.append(el("span", "h3pa-snap-label", "Output multiple"));
        const snapButtons = new Map();
        for (const [label, value] of [["Off", 1], ["8", 8], ["16", 16], ["32", 32], ["64", 64]]) {
            const item = button(label, () => applyOutputMultiple(value),
                value === 1 ? "Allow any integer output dimensions" : `Keep both output dimensions divisible by ${value}`);
            item.classList.toggle("active", value === outputMultiple);
            snapButtons.set(value, item); snapOptions.append(item);
        }
        const ratioLabel = el("label", "h3pa-toggle");
        const ratioLock = el("input"); ratioLock.type = "checkbox"; ratioLock.checked = true;
        const ratioCopy = el("span", "h3pa-toggle-copy");
        ratioCopy.append(
            el("strong", "", "Lock output aspect ratio"),
            el("small", "", "Changing output width updates height (and vice versa); crop resizing uses the same ratio."),
        );
        ratioLabel.append(ratioLock, ratioCopy);
        const resampleLabel = el("label", "", "Resampling");
        const resample = el("select");
        for (const value of ["lanczos", "bicubic", "bilinear", "nearest"]) {
            const option = el("option", "", value); option.value = value; resample.append(option);
        }
        resampleLabel.append(resample);
        const tagLabel = el("label", "", "Variant prompt tag");
        const variantTag = el("input"); variantTag.value = `${asset.tag}_variant`;
        tagLabel.append(variantTag);
        const sizeStatus = el("div", "h3pa-size-summary", "Loading full source image…");
        const cropStatus = el("div", "h3pa-crop-note", "");
        controls.append(
            el("div", "h3pa-crop-note", "Coordinates are oriented source-image pixels. Drag anywhere to draw a crop; drag inside it to move; drag any corner to resize. Arrow keys nudge by 1 px (Shift: 10 px)."),
            cropGrid, targetGrid, ratioLabel, megapixelLabel, megapixelPresets,
            snapOptions, resampleLabel, tagLabel, sizeStatus, cropStatus,
        );
        const actions = el("div", "h3pa-crop-actions");
        const reset = button("Use full image", () => {
            crop.x = 0; crop.y = 0; crop.width = sourceWidth; crop.height = sourceHeight;
            if (ratioLock.checked) {
                lockedRatio = sourceWidth / sourceHeight;
                setTargetSize(dimensionsForMegapixels(
                    requestedMegapixels, lockedRatio, outputMultiple));
            }
            syncInputs(); draw();
        }, "Remove the crop while keeping the current megapixel target; with ratio lock enabled, the target follows the full image ratio");
        const resetAll = button("Reset all", () => resetImageEditor(),
            "Restore the full image, source aspect ratio, model-friendly multiple of 8, source-sized output, Lanczos, and the default variant tag");
        const startCrop = button("Start centered crop", () => {
            const ratio = outputRatio();
            let width = Math.max(1, Math.round(sourceWidth * 0.8));
            let height = Math.max(1, Math.round(width / ratio));
            if (height > sourceHeight * 0.8) {
                height = Math.max(1, Math.round(sourceHeight * 0.8));
                width = Math.max(1, Math.round(height * ratio));
            }
            crop.width = Math.min(sourceWidth, width);
            crop.height = Math.min(sourceHeight, height);
            crop.x = Math.round((sourceWidth - crop.width) / 2);
            crop.y = Math.round((sourceHeight - crop.height) / 2);
            syncInputs(); draw();
        }, "Create a visible centered crop using the selected output ratio");
        const save = button("Save resize / crop", async () => {
            const payload = operationPayload("resample"); if (!payload) return;
            try {
                save.disabled = true; modelButton.disabled = true;
                cropStatus.textContent = "Creating full-resolution variant…";
                const result = await mutationRequest(
                    "/minimax_h3_context_loop/project-assets/derive", {
                        method: "POST", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({...payload, resample: resample.value}),
                    }, operation);
                persistCatalog(result.catalog); state.selected = result.asset.id;
                modal.remove(); render();
                setStatus(`Created ${promptTag(result.asset)} from the full stored image.`);
            } catch (error) {
                cropStatus.textContent = error.message; save.disabled = false;
                modelButton.disabled = !modelConnected();
            }
        }, "Create a new full-quality PNG variant; the source asset remains unchanged");
        const modelButton = button("Model upscale", async () => {
            const payload = operationPayload("model"); if (!payload) return;
            if (!modelConnected()) {
                cropStatus.textContent = "Connect a core UPSCALE_MODEL to the Carousel first."; return;
            }
            try {
                operationWidget.value = JSON.stringify(payload);
                operationWidget.callback?.(operationWidget.value);
                node.graph?.setDirtyCanvas?.(true, true);
                save.disabled = true; modelButton.disabled = true;
                cropStatus.textContent = "Queued asset-only model upscale…";
                await app.queuePrompt(0, 1, [partialExecutionId(node)]);
                // queuePrompt has already serialized and submitted this one-shot
                // operation. Clear the workflow state immediately so a failed
                // model run cannot poison the user's next normal H3 queue.
                operationWidget.value = ""; operationWidget.callback?.("");
                modal.remove();
                setStatus("Asset-only model upscale queued; the H3 chain was not queued.");
            } catch (error) {
                operationWidget.value = ""; operationWidget.callback?.("");
                save.disabled = false; modelButton.disabled = !modelConnected();
                cropStatus.textContent = error.message;
            }
        }, "Run only this Carousel and its connected core upscale model; downstream H3 nodes are excluded");
        function modelConnected() {
            return (node.inputs ?? []).some((input) => (
                input.name === "upscale_model" && input.link != null
            ));
        }
        modelButton.disabled = !modelConnected();
        actions.append(startCrop, reset, resetAll, save, modelButton); controls.append(actions);

        function operationPayload(mode) {
            if (!isCurrentProjectOperation(operation)) {
                cropStatus.textContent = "Run changed. Reopen the editor from the selected Run.";
                return null;
            }
            applyNumericInputs();
            const targetWidth = Math.round(Number(cropInputs.targetWidth.value));
            const targetHeight = Math.round(Number(cropInputs.targetHeight.value));
            if (!targetWidth || !targetHeight) {
                cropStatus.textContent = "Output width and height must be positive."; return null;
            }
            return {
                project: project(), asset_id: asset.id, mode,
                operation_id: globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`,
                crop: {x: crop.x, y: crop.y, width: crop.width, height: crop.height},
                target: {width: targetWidth, height: targetHeight},
                tag: variantTag.value, folder_id: asset.folder_id ?? "",
            };
        }
        function currentTargetSize() {
            return {
                width: Math.max(1, Math.round(Number(
                    cropInputs.targetWidth.value) || 1)),
                height: Math.max(1, Math.round(Number(
                    cropInputs.targetHeight.value) || 1)),
            };
        }
        function setTargetSize(size) {
            cropInputs.targetWidth.value = String(Math.max(1, Math.round(size.width)));
            cropInputs.targetHeight.value = String(Math.max(1, Math.round(size.height)));
        }
        function updateSnapButtons() {
            for (const [value, item] of snapButtons) {
                item.classList.toggle("active", value === outputMultiple);
            }
        }
        function applyOutputMultiple(value) {
            outputMultiple = value;
            updateSnapButtons();
            setTargetSize(dimensionsForMegapixels(
                requestedMegapixels, outputRatio(), outputMultiple));
            cropStatus.textContent = "";
            syncInputs("position"); draw();
        }
        function resetImageEditor() {
            outputMultiple = 8; updateSnapButtons();
            ratioLock.checked = true;
            lockedRatio = sourceWidth / sourceHeight;
            unlockedRatio = lockedRatio;
            crop.x = 0; crop.y = 0;
            crop.width = sourceWidth; crop.height = sourceHeight;
            requestedMegapixels = imageMegapixels(sourceWidth, sourceHeight);
            setTargetSize(dimensionsForMegapixels(
                requestedMegapixels,
                lockedRatio, outputMultiple));
            resample.value = "lanczos";
            variantTag.value = `${asset.tag}_variant`;
            cropStatus.textContent = "";
            syncInputs(); draw();
        }
        function outputRatio() {
            if (ratioLock.checked) return lockedRatio;
            return unlockedRatio;
        }
        function isFullCrop() {
            return crop.x === 0 && crop.y === 0
                && crop.width === sourceWidth && crop.height === sourceHeight;
        }
        function clampCrop(preference = "size") {
            if (preference === "position") {
                crop.width = Math.max(1, Math.min(sourceWidth, Math.round(crop.width)));
                crop.height = Math.max(1, Math.min(sourceHeight, Math.round(crop.height)));
                crop.x = Math.max(0, Math.min(sourceWidth - crop.width, Math.round(crop.x)));
                crop.y = Math.max(0, Math.min(sourceHeight - crop.height, Math.round(crop.y)));
                return;
            }
            // Numeric X/Y are authoritative: when a full-width crop is moved,
            // shorten it to the remaining source area instead of resetting X/Y.
            crop.x = Math.max(0, Math.min(sourceWidth - 1, Math.round(crop.x)));
            crop.y = Math.max(0, Math.min(sourceHeight - 1, Math.round(crop.y)));
            if (ratioLock.checked) {
                let width = Math.max(1, Math.round(crop.width));
                let height = Math.max(1, width / lockedRatio);
                const fit = Math.min(
                    1,
                    (sourceWidth - crop.x) / width,
                    (sourceHeight - crop.y) / height,
                );
                width *= fit; height *= fit;
                crop.width = Math.max(1, Math.round(width));
                crop.height = Math.max(1, Math.round(height));
                return;
            }
            crop.width = Math.max(1, Math.min(sourceWidth - crop.x, Math.round(crop.width)));
            crop.height = Math.max(1, Math.min(sourceHeight - crop.y, Math.round(crop.height)));
        }
        function updateSizeSummary() {
            const target = currentTargetSize();
            const inputMp = formatMegapixels(imageMegapixels(crop.width, crop.height));
            const targetMp = formatMegapixels(imageMegapixels(
                target.width, target.height));
            megapixelInput.value = String(requestedMegapixels);
            const inputName = isFullCrop() ? "full image" : "selected crop";
            const snapText = outputMultiple > 1
                ? ` Output dimensions are multiples of ${outputMultiple}.`
                : " Output dimension snapping is off.";
            if (modelConnected()) {
                sizeStatus.textContent = `Connected model input (${inputName}): ${crop.width}×${crop.height} (${inputMp} MP). Final saved asset: ${target.width}×${target.height} (${targetMp} MP).${snapText} The model runs at its native scale before the final fit.`;
                modelButton.title = `Run only this Carousel. The connected model receives ${crop.width}×${crop.height}; its result is fitted to ${target.width}×${target.height}.`;
            } else {
                sizeStatus.textContent = `${inputName[0].toUpperCase()}${inputName.slice(1)}: ${crop.width}×${crop.height} (${inputMp} MP) → final saved asset ${target.width}×${target.height} (${targetMp} MP).${snapText} Connect UPSCALE_MODEL to enable model upscale.`;
                modelButton.title = "Connect a core UPSCALE_MODEL to enable asset-only model upscale.";
            }
        }
        function syncInputs(preference = "size") {
            clampCrop(preference);
            for (const key of ["x", "y", "width", "height"]) cropInputs[key].value = String(crop[key]);
            updateSizeSummary();
        }
        function fitCropToLockedRatio() {
            const centerX = crop.x + crop.width / 2;
            const centerY = crop.y + crop.height / 2;
            let width = crop.width;
            let height = width / lockedRatio;
            if (height > crop.height) {
                height = crop.height;
                width = height * lockedRatio;
            }
            crop.width = width; crop.height = height;
            crop.x = centerX - width / 2; crop.y = centerY - height / 2;
            clampCrop("position");
        }
        function applyNumericInputs(changed = "") {
            for (const key of ["x", "y", "width", "height"]) {
                const value = Math.round(Number(cropInputs[key].value));
                if (Number.isFinite(value)) crop[key] = value;
            }
            let target = currentTargetSize();
            if (["targetWidth", "targetHeight"].includes(changed)) {
                target = coupledOutputDimensions(
                    target.width, target.height,
                    changed === "targetHeight" ? "height" : "width",
                    lockedRatio, ratioLock.checked, outputMultiple,
                );
                requestedMegapixels = imageMegapixels(target.width, target.height);
                unlockedRatio = target.width / target.height;
            }
            if (ratioLock.checked && ["width", "height"].includes(changed)) {
                if (changed === "height") crop.width = Math.round(crop.height * lockedRatio);
                else crop.height = Math.round(crop.width / lockedRatio);
            }
            setTargetSize(target);
            syncInputs(["x", "y", "width", "height"].includes(changed)
                ? "size" : "position");
            draw();
        }
        for (const [name, input] of Object.entries(cropInputs)) {
            input.addEventListener("change", () => applyNumericInputs(name));
        }
        function applyMegapixelTarget() {
            const value = Number(megapixelInput.value);
            if (!Number.isFinite(value) || value <= 0) {
                cropStatus.textContent = "Target megapixels must be positive.";
                return;
            }
            requestedMegapixels = value;
            setTargetSize(dimensionsForMegapixels(
                value, outputRatio(), outputMultiple));
            cropStatus.textContent = "";
            syncInputs("position"); draw();
        }
        megapixelInput.addEventListener("change", applyMegapixelTarget);
        ratioLock.addEventListener("change", () => {
            const target = currentTargetSize();
            if (ratioLock.checked) {
                lockedRatio = target.width / target.height;
                fitCropToLockedRatio();
            } else {
                unlockedRatio = target.width / target.height;
            }
            syncInputs("position"); draw();
        });
        function canvasPoint(event) {
            const bounds = canvas.getBoundingClientRect();
            return {
                x: (event.clientX - bounds.left) * canvas.width / bounds.width,
                y: (event.clientY - bounds.top) * canvas.height / bounds.height,
            };
        }
        function sourcePoint(point) {
            return {x: (point.x - drawX) / drawScale, y: (point.y - drawY) / drawScale};
        }
        function boundedSourcePoint(point) {
            return {
                x: Math.max(0, Math.min(sourceWidth, point.x)),
                y: Math.max(0, Math.min(sourceHeight, point.y)),
            };
        }
        function cornerPoints() {
            return {
                nw: {x: crop.x, y: crop.y},
                ne: {x: crop.x + crop.width, y: crop.y},
                se: {x: crop.x + crop.width, y: crop.y + crop.height},
                sw: {x: crop.x, y: crop.y + crop.height},
            };
        }
        function hitCorner(point) {
            const threshold = Math.max(14 / drawScale, 7);
            for (const [name, corner] of Object.entries(cornerPoints())) {
                if (Math.hypot(point.x - corner.x, point.y - corner.y) <= threshold) return name;
            }
            return "";
        }
        function oppositeCorner(name) {
            return ({nw: "se", ne: "sw", se: "nw", sw: "ne"})[name];
        }
        function rectangleFromPoints(anchor, moving) {
            let width = Math.max(1, Math.abs(moving.x - anchor.x));
            let height = Math.max(1, Math.abs(moving.y - anchor.y));
            const horizontal = moving.x >= anchor.x ? 1 : -1;
            const vertical = moving.y >= anchor.y ? 1 : -1;
            if (ratioLock.checked) {
                const ratio = outputRatio();
                if (width / height > ratio) height = width / ratio;
                else width = height * ratio;
                const maximumWidth = horizontal > 0 ? sourceWidth - anchor.x : anchor.x;
                const maximumHeight = vertical > 0 ? sourceHeight - anchor.y : anchor.y;
                const fit = Math.min(1, maximumWidth / width, maximumHeight / height);
                width *= fit; height *= fit;
            }
            return {
                x: horizontal > 0 ? anchor.x : anchor.x - width,
                y: vertical > 0 ? anchor.y : anchor.y - height,
                width, height,
            };
        }
        function insideCrop(point) {
            return point.x >= crop.x && point.x <= crop.x + crop.width
                && point.y >= crop.y && point.y <= crop.y + crop.height;
        }
        function updateCanvasCursor(point) {
            const corner = hitCorner(point);
            if (corner === "nw" || corner === "se") canvas.style.cursor = "nwse-resize";
            else if (corner === "ne" || corner === "sw") canvas.style.cursor = "nesw-resize";
            else if (!isFullCrop() && insideCrop(point)) canvas.style.cursor = "move";
            else canvas.style.cursor = "crosshair";
        }
        function draw() {
            const bounds = canvasWrap.getBoundingClientRect();
            const density = Math.min(globalThis.devicePixelRatio || 1, 2);
            const width = Math.max(320, Math.round(bounds.width * density));
            const height = Math.max(260, Math.round(bounds.height * density));
            if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
            const context = canvas.getContext("2d"); context.clearRect(0, 0, width, height);
            context.fillStyle = "#080a0f"; context.fillRect(0, 0, width, height);
            if (!image.complete || !image.naturalWidth) return;
            drawScale = Math.min((width - 24) / sourceWidth, (height - 24) / sourceHeight);
            drawX = (width - sourceWidth * drawScale) / 2;
            drawY = (height - sourceHeight * drawScale) / 2;
            context.drawImage(image, drawX, drawY, sourceWidth * drawScale, sourceHeight * drawScale);
            const x = drawX + crop.x * drawScale; const y = drawY + crop.y * drawScale;
            const w = crop.width * drawScale; const h = crop.height * drawScale;
            context.save(); context.fillStyle = "#0009"; context.beginPath();
            context.rect(drawX, drawY, sourceWidth * drawScale, sourceHeight * drawScale);
            context.rect(x, y, w, h); context.fill("evenodd");
            context.strokeStyle = "#72a9ff"; context.lineWidth = Math.max(2, density * 1.5);
            context.strokeRect(x, y, w, h);
            context.strokeStyle = "#dbe9ff99"; context.lineWidth = Math.max(1, density);
            for (const fraction of [1 / 3, 2 / 3]) {
                context.beginPath(); context.moveTo(x + w * fraction, y); context.lineTo(x + w * fraction, y + h); context.stroke();
                context.beginPath(); context.moveTo(x, y + h * fraction); context.lineTo(x + w, y + h * fraction); context.stroke();
            }
            context.fillStyle = "#72a9ff";
            const handle = 12 * density;
            for (const corner of [
                [x, y], [x + w, y], [x + w, y + h], [x, y + h],
            ]) {
                context.fillRect(corner[0] - handle / 2, corner[1] - handle / 2, handle, handle);
            }
            context.restore();
        }
        canvas.addEventListener("pointerdown", (event) => {
            if (!image.naturalWidth) return;
            const point = boundedSourcePoint(sourcePoint(canvasPoint(event)));
            const corner = hitCorner(point);
            const previous = {...crop};
            if (corner) {
                const corners = cornerPoints();
                drag = {
                    mode: "resize", anchor: corners[oppositeCorner(corner)],
                    previous,
                };
            } else if (!isFullCrop() && insideCrop(point)) {
                drag = {mode: "move", point, start: {...crop}, previous};
            } else {
                crop.x = point.x; crop.y = point.y; crop.width = 1; crop.height = 1;
                drag = {mode: "create", anchor: point, previous};
                syncInputs("position"); draw();
            }
            canvas.setPointerCapture(event.pointerId); canvas.focus(); event.preventDefault();
        });
        canvas.addEventListener("pointermove", (event) => {
            const point = boundedSourcePoint(sourcePoint(canvasPoint(event)));
            if (!drag) { updateCanvasCursor(point); return; }
            if (drag.mode === "move") {
                const dx = point.x - drag.point.x;
                const dy = point.y - drag.point.y;
                crop.x = drag.start.x + dx; crop.y = drag.start.y + dy;
            } else {
                Object.assign(crop, rectangleFromPoints(drag.anchor, point));
            }
            clampCrop("position"); syncInputs("position"); draw(); event.preventDefault();
        });
        const endDrag = () => {
            if (drag && drag.mode === "create" && crop.width <= 2 && crop.height <= 2) {
                Object.assign(crop, drag.previous);
                syncInputs("position"); draw();
            }
            drag = null;
        };
        canvas.addEventListener("pointerup", endDrag); canvas.addEventListener("pointercancel", endDrag);
        canvas.addEventListener("keydown", (event) => {
            const step = event.shiftKey ? 10 : 1;
            if (event.key === "ArrowLeft") crop.x -= step;
            else if (event.key === "ArrowRight") crop.x += step;
            else if (event.key === "ArrowUp") crop.y -= step;
            else if (event.key === "ArrowDown") crop.y += step;
            else return;
            clampCrop("position"); syncInputs("position"); draw(); event.preventDefault();
        });
        image.addEventListener("load", () => {
            sourceWidth = image.naturalWidth; sourceHeight = image.naturalHeight;
            resetImageEditor();
        });
        image.addEventListener("error", () => { cropStatus.textContent = "Could not load the full stored source image."; });
        image.src = mediaUrl(project(), asset, "original");
        const observer = new ResizeObserver(draw); observer.observe(canvasWrap);
        const removeModal = modal.remove.bind(modal);
        modal.remove = () => { observer.disconnect(); removeModal(); };
    }
    function renderEditor(asset) {
        editor.replaceChildren();
        if (!asset) return;
        if (asset._unresolved) {
            editor.append(el("strong", "h3pa-unassigned", `${promptTag(asset)} · Unassigned`));
            editor.append(el(
                "div", "h3pa-status",
                `${asset.kind} · ${displayRole(asset.role)}`,
            ));
            const pairedTag = String(asset.options?.audio_tag ?? "");
            if (pairedTag) editor.append(el("div", "h3pa-status", `Paired alias @${pairedTag}`));
            if (asset.content_hash) editor.append(el(
                "small", "h3pa-status", `Source fingerprint ${asset.content_hash.slice(0, 16)}`,
            ));
            if (asset.available === false) editor.append(el(
                "div", "h3pa-status",
                "This tag disappeared from the connected carrier. The placeholder is retained and inactive.",
            ));
            editor.append(
                button("Choose from ComfyUI Input", () => browseSource(asset, "input"),
                    "Bind an existing ComfyUI input file without importing data from the Tagged Reference carrier"),
                button("Upload media", () => {
                    state.bindingSlot = asset;
                    fileInput.click();
                }),
                button("Choose another source", () => browseSource(asset)),
            );
            return;
        }
        editor.append(el("strong", "", asset.original_name || asset.tag));
        editor.append(el("div", "h3pa-status", assetDetail(asset)));
        const isAudio = asset.kind === "audio";
        const isSourceTrack = asset.role === "source_track";
        const audioMode = String(asset.options?.timeline_mode ?? "standalone");
        if (isAudio) {
            const audioUseLabel = el("label", "", "Audio use");
            audioUseLabel.title = "Tagged clip sends the complete audio only when @tag is present. Tagged timeline slice follows the current scene position only when @tag is present and requires Chain Policy Source reference=on. Project timeline source selects the project's full audio track. No @tag is required: Chain Policy decides whether scenes use it as generation reference audio and whether it becomes the final soundtrack.";
            const audioUse = el("select");
            const currentUse = isSourceTrack
                ? "source_track"
                : (audioMode === "source_timeline" ? "tagged_timeline" : "tagged_clip");
            for (const [value, label] of [
                ["tagged_clip", "Tagged clip (@tag)"],
                ["tagged_timeline", "Tagged timeline slice (@tag)"],
                ["source_track", "Project timeline source"],
            ]) {
                const option = el("option", "", label);
                option.value = value; option.selected = value === currentUse;
                audioUse.append(option);
            }
            audioUse.addEventListener("change", () => {
                const use = audioUse.value;
                updateAsset(asset, use === "source_track" ? {
                    role: "source_track",
                } : {
                    role: "audio_reference",
                    options: {
                        timeline_mode: use === "tagged_timeline"
                            ? "source_timeline" : "standalone",
                        ...(use === "tagged_clip"
                            ? {align_audio_reference: false} : {}),
                    },
                });
            });
            audioUseLabel.append(audioUse); editor.append(audioUseLabel);
        } else {
            const roleLabel = el("label", "", "Asset use");
            const role = el("select");
            for (const value of ROLES[asset.kind] ?? []) {
                const option = el("option", "", displayRole(value));
                option.value = value; option.selected = value === asset.role; role.append(option);
            }
            role.addEventListener("change", () => updateAsset(asset, {role: role.value}));
            roleLabel.append(role); editor.append(roleLabel);
        }
        if (isAudio && isSourceTrack) {
            const tracks = el("div", "h3pa-audio-tracks");
            tracks.append(el("strong", "", "Synchronized audio tracks"));
            tracks.append(el("small", "h3pa-status",
                "Keep every stem at the full song length, including silence. "
                + "The full mix is exported unchanged. Without it, vocals and "
                + "instrumental are mixed; stems are never layered over a full mix. "
                + "Use the scene Lip-sync control in Plan Studio."));
            const bindings = projectAudioTrackBindings(asset);
            for (const [role, title] of AUDIO_TRACK_ROLES) {
                const label = el("label", "", title);
                const select = el("select");
                const empty = el("option", "", role === "full_mix" ? "Auto mix supplied stems" : "Not supplied");
                empty.value = ""; select.append(empty);
                const audioAssets = (state.catalog.assets ?? []).filter((item) => item.kind === "audio" && !item._unresolved);
                for (const item of audioAssets) {
                    const option = el("option", "", `${item.id === asset.id ? "This file · " : ""}${item.original_name || item.tag}`);
                    option.value = item.id; select.append(option);
                }
                if (bindings[role] && !audioAssets.some((item) => item.id === bindings[role])) {
                    const missing = el("option", "", `Missing track · ${bindings[role]}`);
                    missing.value = bindings[role]; select.append(missing);
                }
                select.value = bindings[role];
                select.addEventListener("change", () => {
                    try {
                        void updateAsset(asset, {options: {
                            audio_tracks: setProjectAudioTrack(asset, role, select.value),
                        }});
                    } catch (error) {
                        select.value = bindings[role];
                        status.textContent = String(error.message || error);
                    }
                });
                label.append(select); tracks.append(label);
            }
            tracks.append(button("Reset to single track", () => updateAsset(
                asset, {options: {audio_tracks: null}},
            ), "Use this file alone, with the original source-track behavior"));
            editor.append(tracks);
        }
        const tagLabel = el("label", "", isSourceTrack ? "Catalog tag" : "Prompt tag");
        tagLabel.title = isSourceTrack
            ? "Internal catalog identifier. A Project source track is not activated from scene prompts."
            : `Scene prompts activate this asset by including ${promptTag(asset)}.`;
        const tag = el("input"); tag.value = asset.tag ?? "";
        tag.addEventListener("change", () => updateAsset(asset, {tag: tag.value}));
        tagLabel.append(tag); editor.append(tagLabel);
        const folderLabel = el("label", "", "Folder");
        folderLabel.title = "Presentation only. Folder membership never changes prompts, references, fingerprints, or generation.";
        const folderSelect = el("select");
        const noFolder = el("option", "", "No folder"); noFolder.value = "";
        folderSelect.append(noFolder);
        for (const folder of state.catalog.folders ?? []) {
            const option = el("option", "", folder.name); option.value = folder.id;
            option.selected = folder.id === String(asset.folder_id ?? "");
            folderSelect.append(option);
        }
        folderSelect.addEventListener("change", () => {
            if (folderSelect.value) {
                state.folder = folderSelect.value;
                setFolderExpanded(folderSelect.value, true);
            }
            void updateAsset(asset, {folder_id: folderSelect.value});
        });
        folderLabel.append(folderSelect); editor.append(folderLabel);
        const enabledTitle = isSourceTrack ? "Selected timeline source" : "Available to prompts";
        const enabledSummary = isSourceTrack
            ? "Makes this file available to the Plan; Chain Policy decides how it is used."
            : `${promptTag(asset)} is used only when a scene prompt includes its tag. Turn off to archive it from suggestions without deleting it.`;
        const enabledHelp = isSourceTrack
            ? "Select this file as the Plan's project timeline source. This does not activate it in sampling or final audio: Chain Policy Source reference and Final audio remain authoritative. Turn it off to retain the stored file without exporting its Source Timeline."
            : `Keep ${promptTag(asset)} registered in prompt suggestions and reference fingerprinting. Scene prompts still control where it is used. Turn it off to archive it without deleting the stored file.`;
        const enabledLabel = el("label", "h3pa-toggle");
        enabledLabel.title = enabledHelp;
        const enabled = el("input"); enabled.type = "checkbox"; enabled.checked = asset.enabled !== false;
        enabled.setAttribute("aria-label", enabledTitle);
        enabled.title = enabledHelp;
        enabled.addEventListener("change", () => updateAsset(asset, {enabled: enabled.checked}));
        const enabledCopy = el("span", "h3pa-toggle-copy");
        enabledCopy.append(
            el("strong", "", enabledTitle),
            el("small", "", enabledSummary),
        );
        enabledLabel.append(enabled, enabledCopy); editor.append(enabledLabel);
        if (asset.role === "audio_reference" && audioMode === "source_timeline") {
                const alignHelp = "Apply the optional H3 audio-grid alignment to the per-scene reference slice. The stored full track and final assembled audio are unchanged.";
                const alignLabel = el("label", "h3pa-toggle");
                alignLabel.title = alignHelp;
                const align = el("input"); align.type = "checkbox";
                align.checked = Boolean(asset.options?.align_audio_reference);
                align.title = alignHelp;
                align.setAttribute("aria-label", "Align tagged audio reference to the H3 grid");
                align.addEventListener("change", () => updateAsset(
                    asset, {options: {align_audio_reference: align.checked}},
                ));
                const alignCopy = el("span", "h3pa-toggle-copy");
                alignCopy.append(
                    el("strong", "", "Align audio reference"),
                    el("small", "", "Align only the derived per-scene reference slice."),
                );
                alignLabel.append(align, alignCopy); editor.append(alignLabel);
        }
        if (["video", "motion"].includes(asset.role)) {
            const timelineLabel = el("label", "", "Timeline mode");
            const timeline = el("select");
            for (const value of ["restart_each_scene", "sequential"]) {
                const option = el("option", "", value.replaceAll("_", " "));
                option.value = value; option.selected = value === (asset.options?.timeline_mode ?? "restart_each_scene");
                timeline.append(option);
            }
            timeline.addEventListener("change", () => updateAsset(asset, {options: {timeline_mode: timeline.value}}));
            timelineLabel.append(timeline); editor.append(timelineLabel);
            if (asset.metadata?.has_audio) {
                const pairedLabel = el("label");
                const paired = el("input"); paired.type = "checkbox";
                paired.checked = Boolean(
                    asset.options?.use_embedded_audio ?? true);
                paired.addEventListener("change", () => updateAsset(
                    asset, {options: {use_embedded_audio: paired.checked}},
                ));
                pairedLabel.append(
                    paired, document.createTextNode(" Use embedded audio as paired reference"),
                );
                editor.append(pairedLabel);
            }
        }
        if (asset.role === "motion") {
            const edgeLabel = el("label", "", "Reference short edge");
            const edge = el("select");
            for (const value of ["source", "384", "512", "768"]) {
                const option = el("option", "", value);
                option.value = value;
                option.selected = value === String(
                    asset.options?.reference_short_edge ?? "384");
                edge.append(option);
            }
            edge.addEventListener("change", () => updateAsset(
                asset, {options: {reference_short_edge: edge.value}},
            ));
            edgeLabel.append(edge); editor.append(edgeLabel);
            for (const [name, label, fallback] of [
                ["target_subject", "Target Subject", "<Subject 1>"],
                ["motion_description", "Motion description", "the supplied pose sequence, action, and motion timing"],
            ]) {
                const wrapper = el("label", "", label);
                const input = name === "motion_description" ? el("textarea") : el("input");
                input.value = asset.options?.[name] ?? fallback;
                input.addEventListener("change", () => updateAsset(asset, {options: {[name]: input.value}}));
                wrapper.append(input); editor.append(wrapper);
            }
        }
        editor.append(el("small", "h3pa-status", `input/${asset.input_path}`));
        const actions = el("div", "h3pa-editor-actions");
        const primaryActions = el("div", "h3pa-action-primary");
        const manageActions = el("div", "h3pa-action-manage");
        const assetIndex = (state.catalog.assets ?? []).findIndex(
            (item) => item.id === asset.id,
        );
        const earlier = button("←", () => moveAsset(asset, -1),
            "Move this asset one position earlier in the project Carousel");
        earlier.setAttribute("aria-label", "Move asset earlier");
        earlier.disabled = assetIndex <= 0;
        const later = button("→", () => moveAsset(asset, 1),
            "Move this asset one position later in the project Carousel");
        later.setAttribute("aria-label", "Move asset later");
        later.disabled = assetIndex < 0 || assetIndex >= state.catalog.assets.length - 1;
        const remove = button("Delete", () => deleteAsset(asset),
            "Permanently remove this project-owned copy and its H3 backup");
        remove.setAttribute("aria-label", "Delete asset");
        remove.classList.add("danger");
        primaryActions.append(
            ...(asset.kind === "image" ? [button(
                "Edit / upscale", () => openImageEditor(asset),
                "Resize, crop, or model-upscale a nondestructive image variant from exact source pixels",
            )] : []),
            button("Duplicate", () => duplicateAsset(asset),
                "Create another catalog card that initially shares the same stored media bytes"),
        );
        manageActions.append(earlier, later, remove);
        actions.append(
            el("span", "h3pa-action-label", "Asset actions"),
            primaryActions, manageActions,
        );
        editor.append(actions);
    }
    function assetCard(asset, folderMember = false) {
        const card = el("button", "h3pa-card"); card.type = "button";
        card.classList.toggle("selected", asset.id === state.selected);
        card.classList.toggle("unresolved", Boolean(asset._unresolved));
        card.classList.toggle("stale", asset._unresolved && asset.available === false);
        card.classList.toggle("h3pa-folder-member", folderMember);
        if (!asset._unresolved) {
            card.draggable = true;
            card.title = folderMember
                ? "Drag to reorder this asset, or drop it onto another folder."
                : "Drag to reorder this project asset.";
        }
        if (!asset._unresolved && ["image", "video"].includes(asset.kind)) {
            const image = el("img"); image.loading = "lazy";
            image.draggable = false;
            image.src = mediaUrl(project(), asset, "thumbnail"); card.append(image);
        } else card.append(el(
            "div", "fallback", asset._unresolved ? "?" : "♫",
        ));
        card.append(el(
            "span", "h3pa-badge",
            `${displayRole(asset.role)}${asset._unresolved ? " · unassigned" : ""}`,
        ));
        if (!asset._unresolved) card.append(el(
            "span", "h3pa-drag-handle", "↔",
        ));
        card.append(el(
            "span", "",
            `${asset.available === false || asset.enabled === false ? "○ " : ""}${promptTag(asset)}`,
        ));
        card.addEventListener("click", () => { state.selected = asset.id; render(); });
        if (!asset._unresolved) {
            card.addEventListener("dragstart", (event) => {
                state.dragging = asset.id;
                clearFileDropState();
                card.classList.add("dragging");
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", asset.id);
            });
            card.addEventListener("dragover", (event) => {
                if (!state.dragging || state.dragging === asset.id) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                card.classList.add("drag-over");
            });
            card.addEventListener("dragleave", () => {
                card.classList.remove("drag-over");
            });
            card.addEventListener("drop", (event) => {
                event.preventDefault();
                card.classList.remove("drag-over");
                const dragged = state.dragging || event.dataTransfer.getData("text/plain");
                if (!dragged || dragged === asset.id) return;
                const assetIds = (state.catalog.assets ?? []).map((item) => item.id);
                const from = assetIds.indexOf(dragged);
                if (from < 0) return;
                assetIds.splice(from, 1);
                let target = assetIds.indexOf(asset.id);
                if (target < 0) return;
                const bounds = card.getBoundingClientRect();
                if (event.clientX >= bounds.left + bounds.width / 2) target += 1;
                assetIds.splice(target, 0, dragged);
                state.selected = dragged;
                void reorderAssets(assetIds);
            });
            card.addEventListener("dragend", () => {
                state.dragging = "";
                clearFileDropState();
                for (const item of carousel.querySelectorAll(
                    ".h3pa-card,.h3pa-folder-card",
                )) item.classList.remove("dragging", "drag-over");
            });
        }
        return card;
    }
    function folderCard(folder, previewAssets, totalCount) {
        const folderId = String(folder.id);
        const expanded = state.expandedFolders.has(folderId);
        const card = el("button", "h3pa-folder-card"); card.type = "button";
        card.classList.toggle("expanded", expanded);
        card.classList.toggle("selected", state.folder === folderId);
        card.title = `${expanded ? "Collapse" : "Expand"} ${folder.name}. Drag an asset here to move it into this folder.`;
        const mosaic = el("span", "h3pa-folder-mosaic");
        const tiles = previewAssets.slice(0, 4);
        if (!tiles.length) tiles.push(null);
        for (const asset of tiles) {
            const tile = el("span", "h3pa-folder-tile");
            if (asset && ["image", "video"].includes(asset.kind)) {
                const image = el("img"); image.loading = "lazy";
                image.draggable = false;
                image.src = mediaUrl(project(), asset, "thumbnail");
                tile.append(image);
            } else {
                tile.textContent = asset?.kind === "audio" ? "♫" : "▦";
            }
            mosaic.append(tile);
        }
        card.append(
            mosaic,
            el("span", "h3pa-folder-name", `${expanded ? "▾" : "▸"} ${folder.name}`),
            el("span", "h3pa-folder-count", String(totalCount)),
        );
        card.addEventListener("click", () => {
            state.folder = folderId;
            setFolderExpanded(folderId, !expanded);
            render();
        });
        card.addEventListener("dragover", (event) => {
            if (!state.dragging) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
            card.classList.add("drag-over");
        });
        card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
        card.addEventListener("drop", (event) => {
            if (!state.dragging) return;
            event.preventDefault();
            event.stopPropagation();
            card.classList.remove("drag-over");
            const asset = (state.catalog.assets ?? []).find(
                (item) => item.id === state.dragging,
            );
            if (!asset) return;
            state.folder = folderId;
            setFolderExpanded(folderId, true);
            if (String(asset.folder_id ?? "") !== folderId) {
                void updateAsset(asset, {folder_id: folderId});
            }
        });
        return card;
    }
    function renderCarousel() {
        carousel.replaceChildren();
        const assets = filteredAssets();
        if (!assets.some((asset) => asset.id === state.selected)) {
            state.selected = assets[0]?.id ?? "";
        }
        const folders = state.catalog.folders ?? [];
        const folderById = new Map(folders.map(
            (folder) => [String(folder.id), folder],
        ));
        const allMembers = new Map(folders.map(
            (folder) => [String(folder.id), []],
        ));
        for (const asset of state.catalog.assets ?? []) {
            const members = allMembers.get(String(asset.folder_id ?? ""));
            if (members) members.push(asset);
        }
        const visibleMembers = new Map(folders.map(
            (folder) => [String(folder.id), []],
        ));
        for (const asset of assets) {
            const members = visibleMembers.get(String(asset.folder_id ?? ""));
            if (members && !asset._unresolved) members.push(asset);
        }
        const renderedFolders = new Set();
        let renderedItems = 0;
        for (const asset of assets) {
            const folder = !asset._unresolved
                ? folderById.get(String(asset.folder_id ?? "")) : null;
            if (!folder) {
                carousel.append(assetCard(asset));
                renderedItems += 1;
                continue;
            }
            const folderId = String(folder.id);
            if (renderedFolders.has(folderId)) continue;
            renderedFolders.add(folderId);
            const members = visibleMembers.get(folderId) ?? [];
            const group = el("div", "h3pa-folder-group");
            const expanded = state.expandedFolders.has(folderId);
            group.classList.toggle("expanded", expanded);
            group.append(folderCard(
                folder, members, (allMembers.get(folderId) ?? []).length,
            ));
            if (expanded) {
                for (const member of members) group.append(assetCard(member, true));
            }
            carousel.append(group);
            renderedItems += 1;
        }
        if (state.filter === "all") {
            for (const folder of folders) {
                if (renderedFolders.has(String(folder.id))) continue;
                const group = el("div", "h3pa-folder-group");
                group.append(folderCard(folder, [], 0));
                carousel.append(group);
                renderedItems += 1;
            }
        }
        if (!renderedItems) {
            carousel.append(el(
                "div", "h3pa-carousel-empty",
                "Drop image, video, or audio files here, or use Upload / Import.",
            ));
        }
    }
    function render() {
        const folderIds = new Set((state.catalog.folders ?? []).map(
            (folder) => String(folder.id),
        ));
        let changed = false;
        for (const folderId of state.expandedFolders) {
            if (folderIds.has(folderId)) continue;
            state.expandedFolders.delete(folderId);
            changed = true;
        }
        if (changed) saveExpandedFolders();
        if (state.folder && !folderIds.has(state.folder)) state.folder = "";
        renderTabs(); renderFolders(); renderCarousel();
        const selected = allItems().find((asset) => asset.id === state.selected);
        renderPreview(selected); renderEditor(selected);
    }
    function restoreConfiguredProjectIdentity() {
        const configuredProject = serializedProjectAssetIdentity(
            runNameWidget?.value, catalogWidget?.value,
        );
        const changed = project() !== configuredProject;
        if (changed) {
            refreshSequence += 1;
            runNameInput.value = configuredProject;
        }
        if (runNameWidget && runNameWidget.value !== configuredProject) {
            // Old workflows may only carry the project in catalog_json.
            runNameWidget.value = configuredProject;
        }
        return {configuredProject, changed};
    }
    function hydrateSerializedCatalog({fromConfiguration = false} = {}) {
        if (fromConfiguration) projectEpoch += 1;
        const restored = fromConfiguration
            ? restoreConfiguredProjectIdentity()
            : {configuredProject: project(), changed: false};
        const catalog = serializedProjectAssetCatalog(
            catalogWidget?.value, restored.configuredProject,
        );
        if (!catalog) {
            if (restored.changed) {
                stopMedia();
                state.selected = "";
                state.catalog = {
                    project: restored.configuredProject,
                    assets: [], reference_slots: [], folders: [],
                };
                render();
            }
            return false;
        }
        const currentSignature = `${String(state.catalog?.project ?? "")}\u0000${String(
            state.catalog?.revision ?? "",
        )}`;
        const cachedSignature = `${String(catalog.project ?? "")}\u0000${String(
            catalog.revision ?? "",
        )}`;
        if (currentSignature === cachedSignature
                && (state.catalog?.assets?.length ?? 0) === catalog.assets.length
                && (state.catalog?.reference_slots?.length ?? 0) ===
                    catalog.reference_slots.length) return false;
        state.catalog = catalog;
        render();
        setStatus(
            `${catalog.assets.length} cached project assets · checking for changes…`,
        );
        return true;
    }
    async function refresh({switchPrepared = false} = {}) {
        const requestedProject = project();
        if (!switchPrepared && ownership.runName
                && ownership.runName !== requestedProject) {
            await switchProject(requestedProject, false);
            return;
        }
        const sequence = ++refreshSequence;
        if (!requestedProject) {
            void refreshProjectSuggestions();
            setStatus("Enter a Run name, or connect this node to a named Plan.");
            return;
        }
        try {
            setStatus(`Loading ${requestedProject}…`);
            await ownership.select(requestedProject);
            const catalog = await jsonRequest(
                `/minimax_h3_context_loop/project-assets?project=${encodeURIComponent(requestedProject)}`,
            );
            if (sequence !== refreshSequence || project() !== requestedProject) return;
            persistCatalog(catalog); render();
            void refreshProjectSuggestions();
            setStatus(`${catalog.assets.length} project assets · ${(catalog.reference_slots ?? []).length} unassigned · revision ${(catalog.revision || "empty").slice(0, 12)}`);
        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
    }
    function adoptConnectedRunName() {
        const current = project();
        if (current && current !== "h3_project") return false;
        const connected = downstreamPlanRunName(node);
        if (!connected) return false;
        refreshSequence += 1;
        runNameInput.value = connected;
        if (runNameWidget) {
            runNameWidget.value = connected;
            runNameWidget.callback?.(connected);
        }
        void refresh();
        return true;
    }
    function scheduleGraphRunNameSync() {
        for (const timer of graphSyncTimers) clearTimeout(timer);
        graphSyncTimers.clear();
        for (const delay of [0, 50, 200, 750]) {
            const timer = setTimeout(() => {
                graphSyncTimers.delete(timer);
                if (adoptConnectedRunName()) {
                    for (const pending of graphSyncTimers) clearTimeout(pending);
                    graphSyncTimers.clear();
                }
            }, delay);
            graphSyncTimers.add(timer);
        }
    }
    async function importAsset(body) {
        const result = await mutationRequest(
            "/minimax_h3_context_loop/project-assets/import", {
                method: "POST", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({project: project(), ...body}),
            });
        persistCatalog(result.catalog); state.selected = result.asset.id; render();
        setStatus(`${result.bound_slot_id ? "Bound" : "Imported"} ${promptTag(result.asset)} as ${result.asset.role}.`);
    }
    async function uploadFiles(fileList, {slot = null, dropped = false} = {}) {
        const files = Array.from(fileList ?? []).filter(
            (file) => file && typeof file.name === "string" && file.name,
        );
        if (!files.length) {
            setStatus("No media files were dropped.", true);
            return;
        }
        if (!project()) {
            setStatus("Enter a Run name, or connect this node to a named Plan before adding assets.", true);
            return;
        }
        if (state.uploading) {
            setStatus("Project assets are already being added. Wait for that batch to finish.", true);
            return;
        }
        const operation = captureProjectOperation();
        state.uploading = true;
        let completed = 0;
        let lastResult = null;
        const failures = [];
        try {
            for (let index = 0; index < files.length; index += 1) {
                if (!isCurrentProjectOperation(operation)) break;
                const file = files[index];
                const targetSlot = index === 0 ? slot : null;
                const data = new FormData();
                data.append("project", operation.run);
                if (targetSlot?.id) data.append("slot_id", targetSlot.id);
                data.append("file", file, file.name);
                setStatus(
                    `${targetSlot ? `Binding ${promptTag(targetSlot)} from` : "Creating asset from"} ${file.name}`
                    + `${files.length > 1 ? ` (${index + 1}/${files.length})` : ""}…`,
                );
                try {
                    lastResult = await mutationRequest(
                        "/minimax_h3_context_loop/project-assets/upload",
                        {method: "POST", body: data}, operation,
                    );
                    completed += 1;
                } catch (error) {
                    failures.push(`${file.name}: ${error.message}`);
                }
            }
            if (!isCurrentProjectOperation(operation)) {
                if (!projectDisposed) setStatus(
                    `Upload batch stopped because the Run changed. Check ${operation.run} for files already submitted; no further files were submitted.`,
                );
                return;
            }
            if (lastResult) {
                persistCatalog(lastResult.catalog);
                state.selected = lastResult.asset.id;
                render();
            }
            if (failures.length) {
                setStatus(
                    `${completed ? `Created ${completed} asset${completed === 1 ? "" : "s"}; ` : ""}`
                    + `${failures.length} file${failures.length === 1 ? "" : "s"} failed: ${failures.join(" | ")}`,
                    true,
                );
            } else if (slot && lastResult) {
                setStatus(`Bound ${promptTag(lastResult.asset)} from ${files[0].name}.`);
            } else if (dropped && lastResult) {
                setStatus(files.length === 1
                    ? `Created ${promptTag(lastResult.asset)} from dropped file.`
                    : `Created ${completed} project assets from dropped files.`);
            } else if (lastResult) {
                setStatus(`Uploaded ${promptTag(lastResult.asset)}.`);
            }
        } finally {
            state.uploading = false;
        }
    }
    async function browseSource(slot = null, forcedSource = "") {
        const operation = captureProjectOperation();
        let source = forcedSource || sourceSelect.value;
        const modal = el("div", "h3pa-modal");
        const row = el("div", "h3pa-row");
        const search = el("input", "h3pa-project"); search.placeholder = "Filter assets";
        const sourceTitle = source === "input"
            ? "ComfyUI input"
            : (source === "project" ? "Other Run assets" : "H3 chain backups");
        row.append(el("strong", "", sourceTitle), search,
            button("Close", () => modal.remove()));
        const list = el("div", "h3pa-source-list"); modal.append(row, list); document.body.append(modal);
        async function load() {
            list.textContent = "Loading…";
            try {
                const payload = await jsonRequest(
                    `/minimax_h3_context_loop/project-assets/sources?source=${source}&q=${encodeURIComponent(search.value)}&project=${encodeURIComponent(project())}`,
                );
                list.replaceChildren();
                const items = source === "chains"
                    ? payload.items.flatMap((run) => run.assets.map((asset) => ({...asset, run_name: run.run_name})))
                    : (source === "project"
                        ? payload.items.flatMap((run) => run.assets.map((asset) => ({...asset, source_project: run.project})))
                        : payload.items);
                if (!items.length) {
                    list.append(el("div", "h3pa-empty", source === "project"
                        ? "No matching assets were found in another Run."
                        : "No matching media was found."));
                }
                for (const item of items) {
                    const rowItem = el("div", "h3pa-source-item");
                    const sourceLabel = source === "chains"
                        ? `${item.run_name} · ${promptTag(item)}`
                        : (source === "project"
                            ? `${item.source_project} · ${promptTag(item)} · ${item.original_name || "asset"}`
                            : item.path);
                    rowItem.append(el("span", "", sourceLabel),
                    el("span", "", item.kind ?? ""), button(slot ? "Bind" : "Import", async () => {
                        try {
                            requireCurrentProjectOperation(operation);
                            if (source === "chains") await importAsset({
                                source, run_name: item.run_name, asset_id: item.id,
                                slot_id: slot?.id ?? "",
                            });
                            else if (source === "project") await importAsset({
                                source, source_project: item.source_project,
                                asset_id: item.id, slot_id: slot?.id ?? "",
                            });
                            else await importAsset({
                                source, path: item.path, slot_id: slot?.id ?? "",
                            });
                            modal.remove();
                        } catch (error) { if (!error.staleProject) setStatus(error.message, true); }
                    }));
                    list.append(rowItem);
                }
            } catch (error) { list.textContent = error.message; }
        }
        let timer = 0;
        search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
        await load();
    }
    fileInput.addEventListener("change", async () => {
        await uploadFiles(fileInput.files, {slot: state.bindingSlot});
        state.bindingSlot = null;
        fileInput.value = "";
    });
    const dropController = new AbortController();
    const dropListenerOptions = {signal: dropController.signal};
    let fileDragDepth = 0;
    function hasDraggedFiles(event) {
        if (state.dragging) return false;
        return Array.from(event.dataTransfer?.types ?? []).includes("Files");
    }
    function clearFileDropState() {
        fileDragDepth = 0;
        carousel.classList.remove("h3pa-drop-active");
    }
    carousel.addEventListener("dragenter", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        fileDragDepth += 1;
        carousel.classList.add("h3pa-drop-active");
    }, dropListenerOptions);
    carousel.addEventListener("dragover", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        carousel.classList.add("h3pa-drop-active");
    }, dropListenerOptions);
    carousel.addEventListener("dragleave", (event) => {
        if (!hasDraggedFiles(event)) return;
        fileDragDepth = Math.max(0, fileDragDepth - 1);
        if (!fileDragDepth) carousel.classList.remove("h3pa-drop-active");
    }, dropListenerOptions);
    carousel.addEventListener("drop", (event) => {
        if (!hasDraggedFiles(event)) return;
        event.preventDefault();
        event.stopPropagation();
        const files = Array.from(event.dataTransfer?.files ?? []);
        clearFileDropState();
        void uploadFiles(files, {dropped: true});
    }, dropListenerOptions);
    async function flushProjectEditors(runName) {
        if (!runName) return;
        const rootGraph = node.graph?.rootGraph ?? node.graph ?? app.graph;
        const editors = [];
        const visit = (graph) => {
            for (const candidate of graph?._nodes ?? graph?.nodes ?? []) {
                if (candidate !== node
                        && typeof candidate?._h3FlushProjectWrites === "function") {
                    editors.push(candidate);
                }
                if (candidate?.subgraph) visit(candidate.subgraph);
            }
        };
        visit(rootGraph);
        const results = await Promise.allSettled(editors.map(
            (editorNode) => editorNode._h3FlushProjectWrites(runName),
        ));
        const failed = results.find((result) => result.status === "rejected");
        if (failed) throw failed.reason;
    }

    async function performProjectSwitch(selectedProject, requireExisting = true) {
        selectedProject = String(selectedProject ?? "").trim();
        if (requireExisting && !state.projectNames.has(selectedProject)) return false;
        if (!validProjectName(selectedProject)) {
            setStatus(
                "Run name must be 1-96 characters, start/end with a letter or number, and use only letters, numbers, '.', '_' or '-'.",
                true,
            );
            return false;
        }
        const previousProject = String(ownership.runName ?? "");
        if (previousProject && previousProject !== selectedProject) {
            try {
                setStatus(`Saving pending edits for ${previousProject}…`);
                await flushProjectEditors(previousProject);
                if (ownership.owned) await ownership.release();
            } catch (error) {
                setStatus(
                    `Stayed on ${previousProject}: pending edits could not be saved (${error.message}).`,
                    true,
                );
                runNameInput.value = previousProject;
                return false;
            }
        }
        projectEpoch += 1;
        refreshSequence += 1;
        state.selected = "";
        state.folder = "";
        stopMedia();
        runNameInput.value = selectedProject;
        if (runNameWidget) {
            runNameWidget.value = selectedProject;
            runNameWidget.callback?.(selectedProject);
        }
        syncDownstreamPlan(node, selectedProject);
        node.graph?.setDirtyCanvas?.(true, true);
        await refresh({switchPrepared:true});
        return true;
    }
    let projectSwitchPromise = Promise.resolve(false);
    function switchProject(selectedProject, requireExisting = true) {
        // Blur, Enter, graph synchronization, and the project dropdown can
        // request a switch nearly together. Serialize them so an older slow
        // flush/refresh can never commit after the user's newer selection.
        const operation = projectSwitchPromise
            .catch(() => false)
            .then(() => performProjectSwitch(
                selectedProject, requireExisting,
            ));
        projectSwitchPromise = operation;
        return operation;
    }
    runNameInput.addEventListener("input", () => {
        refreshSequence += 1;
        const draft = String(runNameInput.value || "");
        setStatus(validProjectName(draft)
            ? "Press Enter or leave the Run name field to load this project."
            : "Run names use letters, numbers, '.', '_' and '-' only.",
            !validProjectName(draft));
    });
    runNameInput.addEventListener("change", () => {
        void switchProject(runNameInput.value, false);
    });
    runNameInput.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        runNameInput.blur();
    });
    projectMenu.addEventListener("change", () => {
        const selectedProject = String(projectMenu.value || "");
        projectMenu.value = "";
        if (selectedProject) void switchProject(selectedProject);
    });
    previewSelect.addEventListener("change", () => {
        state.previewMode = previewSelect.value === "full" ? "full" : "light";
        node.properties ??= {};
        node.properties.h3_project_asset_preview_mode = state.previewMode;
        render();
    });
    const connectionsChanged = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = connectionsChanged?.apply(this, arguments);
        scheduleGraphRunNameSync();
        return result;
    };
    const removed = node.onRemoved;
    node.onRemoved = function () {
        projectDisposed = true;
        projectEpoch += 1;
        for (const timer of graphSyncTimers) clearTimeout(timer);
        graphSyncTimers.clear();
        dropController.abort();
        ownership.dispose();
        stopMedia();
        return removed?.apply(this, arguments);
    };
    const executed = node.onExecuted;
    node.onExecuted = function () {
        const result = executed?.apply(this, arguments);
        if (operationWidget?.value) {
            operationWidget.value = "";
            operationWidget.callback?.("");
        }
        void refresh();
        return result;
    };
    node._h3ProjectAssetRefresh = ({fromConfiguration = false} = {}) => {
        refreshSemanticSettingVisibility(node);
        hydrateSerializedCatalog({fromConfiguration});
        if (!adoptConnectedRunName()) void refresh();
        scheduleGraphRunNameSync();
    };
    if (savedCatalog) render();
    node._h3ProjectAssetRefresh({fromConfiguration: true});
}

app.registerExtension({
    name: "minimax_h3_context_loop.project_asset_manager",
    async beforeRegisterNodeDef(nodeClass, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeClass.prototype.onNodeCreated;
        nodeClass.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setTimeout(() => mount(this), 0); return result;
        };
        const configured = nodeClass.prototype.onConfigure;
        nodeClass.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            setTimeout(() => this._h3ProjectAssetRefresh?.({
                fromConfiguration: true,
            }), 0); return result;
        };
        const graphConfigured = nodeClass.prototype.onGraphConfigured;
        nodeClass.prototype.onGraphConfigured = function () {
            const result = graphConfigured?.apply(this, arguments);
            setTimeout(() => this._h3ProjectAssetRefresh?.({
                fromConfiguration: true,
            }), 0); return result;
        };
        const extraMenuOptions = nodeClass.prototype.getExtraMenuOptions;
        nodeClass.prototype.getExtraMenuOptions = function (_, options) {
            const result = extraMenuOptions?.apply(this, arguments);
            const visible = this.properties?.h3_show_semantic_anchor_settings !== false;
            options.push({
                content: visible
                    ? "Hide semantic anchor settings"
                    : "Show semantic anchor settings",
                callback: () => {
                    this.properties ??= {};
                    this.properties.h3_show_semantic_anchor_settings = !visible;
                    refreshSemanticSettingVisibility(this);
                },
            });
            return result;
        };
    },
    async nodeCreated(node) { if (nodeType(node) === NODE_NAME) mount(node); },
    async afterConfigureGraph() {
        for (const node of app.graph?._nodes ?? []) {
            if (nodeType(node) === NODE_NAME) {
                setTimeout(() => node._h3ProjectAssetRefresh?.({
                    fromConfiguration: true,
                }), 0);
            }
        }
    },
});
