import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";
import {
    ASSET_ROLES,
    MAX_ASSET_BINDINGS,
    applyAssetBinding,
    assetInputNumber,
    collectAssetBindings,
    nodeType,
} from "./h3_run_assets_core.mjs?v=0.6.8";
import {
    runArchiveOptionLabel,
    runManagerIdentity,
} from "./h3_run_manager_core.mjs?v=0.6.8";
import {
    refreshRestoredPlanEditors,
    restoreConnectedPolicyInputs,
} from "./h3_plan_restore_core.mjs?v=0.6.8";

const NODE_NAME = "MiniMaxH3ChainRunManager";
const PLAN_NAME = "MiniMaxH3ChainPlan";
const PLAN_NAMES = new Set([PLAN_NAME, "MiniMaxH3ChainPlanModern"]);
const ASSET_WIDGETS = [
    "archive_images", "archive_audio", "archive_video", "asset_bindings_json",
];

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

function widgetByName(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function element(tag, className = "", text = undefined) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action) {
    const item = element("button", "", label);
    item.type = "button";
    item.title = title;
    item.addEventListener("click", action);
    return item;
}

function injectStyles() {
    if (document.getElementById("h3-run-manager-style")) return;
    const style = document.createElement("style");
    style.id = "h3-run-manager-style";
    style.textContent = `
        .h3rm-root { --h3rm-bg:color-mix(in srgb,var(--comfy-menu-bg,#202124) 91%,#111827);
            --h3rm-panel:var(--comfy-input-bg,#15171d); --h3rm-border:var(--border-color,#586174);
            --h3rm-text:var(--input-text,#eceef5); --h3rm-muted:color-mix(in srgb,var(--h3rm-text) 58%,transparent);
            box-sizing:border-box; width:100%; height:100%; min-height:210px; display:flex;
            flex-direction:column; gap:9px; overflow:auto; padding:10px; border:1px solid var(--h3rm-border);
            border-radius:8px; background:var(--h3rm-bg); color:var(--h3rm-text);
            font:12px/1.4 system-ui,sans-serif; }
        .h3rm-root *, .h3rm-root *::before, .h3rm-root *::after { box-sizing:border-box; }
        .h3rm-title { font-size:15px; font-weight:750; }
        .h3rm-identity { display:grid; grid-template-columns:1fr 1fr; gap:6px; }
        .h3rm-identity > span { min-width:0; padding:5px 7px; border:1px solid var(--h3rm-border);
            border-radius:5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3rm-identity-active { color:#b8d2ff; }
        .h3rm-identity-selected { color:var(--h3rm-muted); }
        .h3rm-identity-selected.h3rm-same { color:#a8e6b1; border-color:#4d8a58; }
        .h3rm-section { padding:8px; border:1px solid color-mix(in srgb,var(--h3rm-border) 72%,transparent);
            border-radius:6px; background:var(--h3rm-panel); }
        .h3rm-section-title { display:flex; justify-content:space-between; align-items:center;
            gap:8px; margin-bottom:6px; font-weight:700; }
        .h3rm-policy { display:flex; flex-wrap:wrap; gap:8px; color:var(--h3rm-muted); font-weight:400; }
        .h3rm-policy label { display:inline-flex; align-items:center; gap:4px; }
        .h3rm-policy input { margin:0; }
        .h3rm-assets { display:flex; flex-direction:column; gap:5px; }
        .h3rm-asset { display:grid; grid-template-columns:minmax(120px,1fr) 125px;
            align-items:center; gap:6px; min-width:0; }
        .h3rm-asset-main { min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
        .h3rm-asset-path { display:block; color:var(--h3rm-muted); font-size:11px;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .h3rm-asset select { min-width:0; width:100%; padding:4px; border:1px solid var(--h3rm-border);
            border-radius:5px; background:var(--h3rm-bg); color:var(--h3rm-text); }
        .h3rm-empty { color:var(--h3rm-muted); }
        .h3rm-select { width:100%; min-width:0; padding:7px 8px; border:1px solid var(--h3rm-border);
            border-radius:6px; background:var(--h3rm-panel); color:var(--h3rm-text); }
        .h3rm-details { min-height:48px; padding:8px; border:1px solid color-mix(in srgb,var(--h3rm-border) 72%,transparent);
            border-radius:6px; background:var(--h3rm-panel); color:var(--h3rm-muted);
            white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3rm-actions { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
        .h3rm-actions button { padding:6px 9px; border:1px solid var(--h3rm-border); border-radius:6px;
            background:var(--h3rm-panel); color:var(--h3rm-text); cursor:pointer; }
        .h3rm-actions button:hover { border-color:#7fa8ff; }
        .h3rm-actions button:disabled { cursor:not-allowed; opacity:.45; }
        .h3rm-load { font-weight:700; border-color:#6d91d8 !important; }
        .h3rm-status { min-width:0; flex:1 1 170px; color:var(--h3rm-muted); text-align:right;
            white-space:pre-wrap; overflow-wrap:anywhere; }
        .h3rm-error { color:#ffb3b3; }
    `;
    document.head.appendChild(style);
}

function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 1) return "0 KB";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function collapseWidget(widget) {
    if (!widget) return;
    widget._h3OriginalType ??= widget.type;
    widget._h3OriginalComputeSize ??= widget.computeSize;
    widget._h3OriginalDraw ??= widget.draw;
    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
    // Some ComfyUI canvas/front-end combinations still invoke draw() for a
    // widget after its type and geometry have been collapsed. Keep the value
    // serializable, but make the internal archive state visually inert.
    widget.draw = () => {};
    for (const item of new Set([widget.inputEl, widget.element])) {
        if (!item?.style) continue;
        item.style.setProperty("display", "none", "important");
        item.style.setProperty("pointer-events", "none", "important");
        item.setAttribute?.("aria-hidden", "true");
    }
    if (widget.element && widget.id && typeof widget.onRemove === "function") {
        widget.onRemove();
    }
}

function removeLegacyStatusOutput(node) {
    const index = node.outputs?.findIndex((output) => output.name === "asset_status") ?? -1;
    if (index >= 0) node.removeOutput?.(index);
}

function stabilizeAssetInputs(node) {
    const connected = (node.inputs ?? []).filter((input) =>
        assetInputNumber(input) != null && input.link != null);
    const used = new Set(connected.map((input) => assetInputNumber(input)));
    let next = 0;
    while (used.has(next) && next < MAX_ASSET_BINDINGS) next += 1;
    for (let index = (node.inputs?.length ?? 0) - 1; index >= 0; index -= 1) {
        const input = node.inputs[index];
        const number = assetInputNumber(input);
        if (number != null && input.link == null && number !== next) {
            node.removeInput(index);
        }
    }
    if (next < MAX_ASSET_BINDINGS
            && !node.inputs?.some((input) => input.name === `asset_${next}`)) {
        node.addInput(`asset_${next}`, "*");
    }
    const empty = node.inputs?.find((input) =>
        assetInputNumber(input) === next && input.link == null);
    if (empty) empty.label = "Connect loader asset";
    node.graph?.setDirtyCanvas?.(true, true);
}

function localTime(value) {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : "unknown time";
}

async function jsonRequest(path) {
    const response = await api.fetchApi(path);
    let payload = {};
    try {
        payload = await response.json();
    } catch (_error) {
        // Preserve a useful HTTP error when a proxy emits a non-JSON body.
    }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
}

function applyPlanInputs(planNode, inputs, policyInputs = {}) {
    if (!planNode) throw new Error("Connect this Run Manager to the active H3 Chain Plan.");
    if (!inputs || typeof inputs !== "object") throw new Error("The saved run has no Plan inputs.");
    const names = Object.keys(inputs).sort((left, right) =>
        Number(left === "plan_json") - Number(right === "plan_json"));
    const applied = [];
    const unavailable = [];
    const graph = planNode.graph;
    if (!graph || graph.getNodeById?.(planNode.id) !== planNode) {
        throw new Error("The target Plan is no longer in its workflow.");
    }
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
    const policies = restoreConnectedPolicyInputs(
        planNode, policyInputs, inputs);
    refreshRestoredPlanEditors(planNode);
    graph.setDirtyCanvas?.(true, true);
    return {applied, unavailable, policies};
}

function mount(node) {
    if (node._h3RunManagerMounted || typeof node.addDOMWidget !== "function") return;
    node._h3RunManagerMounted = true;
    injectStyles();
    removeLegacyStatusOutput(node);

    const root = element("div", "h3rm-root");
    for (const eventName of [
        "pointerdown", "pointerup", "mousedown", "mouseup", "click", "dblclick",
    ]) root.addEventListener(eventName, (event) => event.stopPropagation());
    root.addEventListener("wheel", (event) => event.stopPropagation());

    for (const name of ASSET_WIDGETS) collapseWidget(widgetByName(node, name));
    const state = {
        runs: [], selected: "", busy: false, bindings: [], watchedSources: new Set(),
        watchedPlanWidget: null,
        disposed: false, restoreEpoch: 0,
    };
    const title = element("div", "h3rm-title", "H3 Run Manager");
    const identity = element("div", "h3rm-identity");
    const activeIdentity = element("span", "h3rm-identity-active");
    const selectedIdentity = element("span", "h3rm-identity-selected");
    identity.append(activeIdentity, selectedIdentity);
    const select = element("select", "h3rm-select");
    select.title = "Saved projects discovered under the ComfyUI host's output/h3_chains folder.";
    const details = element("div", "h3rm-details", "Loading saved runs…");
    const assetSection = element("div", "h3rm-section");
    const assetHeader = element("div", "h3rm-section-title");
    const assetTitle = element("span", "", "Reference assets");
    const assetList = element("div", "h3rm-assets");
    const policies = element("div", "h3rm-policy");
    const actions = element("div", "h3rm-actions");
    const status = element("span", "h3rm-status");

    function policyCheckbox(widgetName, label) {
        const wrap = element("label");
        const control = element("input");
        control.type = "checkbox";
        const widget = widgetByName(node, widgetName);
        control.checked = Boolean(widget?.value);
        control.addEventListener("change", () => {
            if (widget) {
                widget.value = control.checked;
                widget.callback?.(control.checked);
            }
            node.graph?.setDirtyCanvas?.(true, true);
        });
        wrap.append(control, document.createTextNode(label));
        return wrap;
    }

    policies.append(
        policyCheckbox("archive_images", "Archive images"),
        policyCheckbox("archive_audio", "Archive audio"),
        policyCheckbox("archive_video", "Archive video"),
    );
    assetHeader.append(assetTitle, policies);

    function activeRunName() {
        const planNode = upstreamPlanNode(node);
        return String(widgetByName(planNode, "run_name")?.value ?? "").trim();
    }

    const activeRunChanged = () => {
        state.restoreEpoch += 1;
        const defer = window.queueMicrotask ?? ((callback) => window.setTimeout(callback, 0));
        defer(() => { if (!state.disposed) renderSelection(); });
    };

    function updatePlanWatch() {
        const planWidget = widgetByName(upstreamPlanNode(node), "run_name") ?? null;
        if (state.watchedPlanWidget === planWidget) return;
        state.watchedPlanWidget?._h3RunManagerWatchers?.delete(activeRunChanged);
        state.watchedPlanWidget = planWidget;
        if (!planWidget) return;
        planWidget._h3RunManagerWatchers ??= new Set();
        if (!planWidget._h3RunManagerWatchWrapped) {
            planWidget._h3RunManagerWatchWrapped = true;
            const changed = planWidget.callback;
            planWidget.callback = function () {
                const result = changed?.apply(this, arguments);
                for (const listener of this._h3RunManagerWatchers ?? []) listener();
                return result;
            };
        }
        planWidget._h3RunManagerWatchers.add(activeRunChanged);
    }

    function renderActiveRun() {
        updatePlanWatch();
        const runName = activeRunName();
        assetTitle.textContent = runName
            ? `Reference assets → ${runName}`
            : "Reference assets → no active run_name";
        assetTitle.title = runName
            ? `Save/update assets writes to the connected Plan run “${runName}”.`
            : "Set run_name on the connected Plan before saving assets.";
    }

    function writeBindingsWidget() {
        const widget = widgetByName(node, "asset_bindings_json");
        if (!widget) return;
        const value = JSON.stringify(state.bindings);
        if (widget.value === value) return;
        widget.value = value;
        widget.callback?.(value);
        node.graph?.setDirtyCanvas?.(true, true);
    }

    const sourceChanged = () => {
        const defer = window.queueMicrotask ?? ((callback) => window.setTimeout(callback, 0));
        defer(() => { if (!state.disposed) syncAssetBindings(); });
    };

    function updateSourceWatches() {
        const current = new Set();
        for (const input of node.inputs ?? []) {
            if (assetInputNumber(input) == null || input.link == null) continue;
            const link = node.graph?.links?.[input.link];
            const source = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (!source) continue;
            current.add(source);
            source._h3AssetWatchers ??= new Set();
            if (!source._h3AssetWatchWrapped) {
                source._h3AssetWatchWrapped = true;
                const changed = source.onWidgetChanged;
                source.onWidgetChanged = function () {
                    const result = changed?.apply(this, arguments);
                    for (const listener of this._h3AssetWatchers ?? []) listener();
                    return result;
                };
            }
            source._h3AssetWatchers.add(sourceChanged);
        }
        for (const binding of state.bindings) {
            const source = node.graph?.getNodeById?.(binding.node_id)
                ?? node.graph?.getNodeById?.(Number(binding.node_id));
            if (!source || current.has(source)) continue;
            current.add(source);
            source._h3AssetWatchers ??= new Set();
            if (!source._h3AssetWatchWrapped) {
                source._h3AssetWatchWrapped = true;
                const changed = source.onWidgetChanged;
                source.onWidgetChanged = function () {
                    const result = changed?.apply(this, arguments);
                    for (const listener of this._h3AssetWatchers ?? []) listener();
                    return result;
                };
            }
            source._h3AssetWatchers.add(sourceChanged);
        }
        for (const source of state.watchedSources) {
            if (!current.has(source)) source._h3AssetWatchers?.delete(sourceChanged);
        }
        state.watchedSources = current;
    }

    function renderAssetBindings() {
        assetList.replaceChildren();
        if (!state.bindings.length) {
            assetList.append(element(
                "div", "h3rm-empty",
                "Connect an image, video, or audio loader to the asset socket.",
            ));
            return;
        }
        for (const binding of state.bindings) {
            const row = element("div", "h3rm-asset");
            const main = element("div", "h3rm-asset-main", binding.label);
            const path = element(
                "span", "h3rm-asset-path",
                binding.original_value || "No loader filename detected",
            );
            main.title = `${binding.node_type} #${binding.node_id}`;
            path.title = binding.original_value || main.title;
            main.append(path);
            const role = element("select");
            for (const [value, label] of ASSET_ROLES) {
                const option = element("option", "", label);
                option.value = value;
                role.append(option);
            }
            role.value = binding.role;
            role.addEventListener("change", () => {
                node.properties ??= {};
                node.properties.h3_asset_roles ??= {};
                node.properties.h3_asset_roles[binding.binding_id] = role.value;
                syncAssetBindings();
            });
            row.append(main, role);
            assetList.append(row);
        }
    }

    function syncAssetBindings() {
        if (state.disposed) return;
        state.bindings = collectAssetBindings(node);
        updateSourceWatches();
        writeBindingsWidget();
        renderAssetBindings();
    }

    function selectedRun() {
        return state.runs.find((item) => item.run_name === state.selected) ?? null;
    }

    function setBusy(value) {
        if (state.disposed) return;
        state.busy = Boolean(value);
        select.disabled = state.busy;
        refresh.disabled = state.busy;
        load.disabled = state.busy || !selectedRun()?.restorable;
        open.disabled = state.busy || !selectedRun();
        saveAssets.disabled = state.busy || !state.bindings.length;
    }

    function renderSelection() {
        if (state.disposed) return;
        renderActiveRun();
        const run = selectedRun();
        const runIdentity = runManagerIdentity(activeRunName(), run);
        activeIdentity.textContent = runIdentity.activeLabel;
        activeIdentity.title = "Generation and asset saving use this run_name from the connected Plan.";
        selectedIdentity.textContent = runIdentity.selectedLabel;
        selectedIdentity.title = runIdentity.same
            ? "The selected saved archive matches the connected Plan."
            : "Selection alone does not change the Plan. Load the selected archive to apply it.";
        selectedIdentity.classList.toggle("h3rm-same", runIdentity.same);
        load.textContent = runIdentity.loadLabel;
        saveAssets.textContent = "Save assets to active Plan";
        saveAssets.title = runIdentity.saveLabel;
        for (const option of select.options ?? []) {
            const optionRun = state.runs.find(
                (item) => item.run_name === option.value);
            if (optionRun) option.textContent = runArchiveOptionLabel(
                optionRun, runIdentity.active);
        }
        if (!run) {
            details.textContent = state.runs.length
                ? "Select a saved H3 run." : "No saved H3 runs were found.";
            load.disabled = true;
            open.disabled = true;
            return;
        }
        const scenes = run.scene_count == null ? "unknown scenes" : `${run.scene_count} scenes`;
        const source = Object.entries(run.sources ?? {}).filter(([, ready]) => ready)
            .map(([name]) => name.replace("_", " ")).join(", ") || "no archive";
        details.textContent =
            `${scenes} · ${run.checkpoint_count} checkpoints · ${run.asset_count ?? 0} assets · ${formatBytes(run.archive_bytes)}\n` +
            `Modified ${localTime(run.modified_at)} · ${source}`;
        load.disabled = state.busy || !run.restorable;
        open.disabled = state.busy;
    }

    async function refreshRuns(preferredRunName = "") {
        setBusy(true);
        status.className = "h3rm-status";
        status.textContent = "Scanning host output…";
        try {
            const payload = await jsonRequest("/minimax_h3_context_loop/runs");
            if (state.disposed) return;
            const previous = state.selected;
            state.runs = Array.isArray(payload.runs) ? payload.runs : [];
            const active = activeRunName();
            const candidates = [preferredRunName, previous, active];
            state.selected = candidates.find((candidate) =>
                candidate && state.runs.some((item) => item.run_name === candidate))
                ?? state.runs.find((item) => item.restorable)?.run_name
                ?? state.runs[0]?.run_name ?? "";
            select.replaceChildren();
            for (const run of state.runs) {
                const option = element(
                    "option", "", runArchiveOptionLabel(run, active));
                option.value = run.run_name;
                select.append(option);
            }
            select.value = state.selected;
            status.textContent = `${state.runs.length} saved archive${state.runs.length === 1 ? "" : "s"}`;
        } catch (error) {
            state.runs = [];
            state.selected = "";
            select.replaceChildren();
            status.className = "h3rm-status h3rm-error";
            status.textContent = error?.message || String(error);
        } finally {
            setBusy(false);
            renderSelection();
        }
    }

    function captureRestoreOperation(planNode, run) {
        return {
            graph:node.graph, activeGraph:app.graph, planNode, run:run.run_name,
            epoch:state.restoreEpoch,
            planValues:JSON.stringify((planNode.widgets ?? []).map(
                (widget) => [widget.name, widget.value],
            )),
        };
    }

    function requireCurrentRestore(operation) {
        const graph = operation.graph;
        if (state.disposed || state.restoreEpoch !== operation.epoch
                || !graph || node.graph !== graph || app.graph !== operation.activeGraph
                || graph.getNodeById?.(node.id) !== node
                || graph.getNodeById?.(operation.planNode.id) !== operation.planNode
                || upstreamPlanNode(node) !== operation.planNode
                || selectedRun()?.run_name !== operation.run
                || JSON.stringify((operation.planNode.widgets ?? []).map(
                    (widget) => [widget.name, widget.value],
                )) !== operation.planValues) {
            throw new Error(
                "Archive load cancelled: the workflow, connected Plan, or Run " +
                "changed while loading. Select the intended archive and load it again.",
            );
        }
    }

    async function loadRun() {
        const run = selectedRun();
        const planNode = upstreamPlanNode(node);
        if (!run || !planNode || state.busy || state.disposed) {
            if (!planNode) {
                status.className = "h3rm-status h3rm-error";
                status.textContent = "Connect the active Plan first.";
            }
            return;
        }
        const current = String(widgetByName(planNode, "run_name")?.value ?? "").trim();
        const assetNotice = run.asset_count
            ? ` It will also attempt to restore ${run.asset_count} loader asset${run.asset_count === 1 ? "" : "s"}.`
            : "";
        const message = `Load saved run “${run.run_name}” into the connected Plan?\n\n` +
            `This replaces all active scene prompts, archived Plan settings, and connected 0.5 policies${current ? ` from “${current}”` : ""}.${assetNotice}`;
        if (!window.confirm(message)) return;
        const operation = captureRestoreOperation(planNode, run);
        setBusy(true);
        status.className = "h3rm-status";
        status.textContent = "Loading archive…";
        try {
            const query = new URLSearchParams({run_name: run.run_name});
            const payload = await jsonRequest(`/minimax_h3_context_loop/run?${query}`);
            // Never apply a delayed archive to a different tab, replacement
            // node with the same ID, or Plan edited since the load began.
            requireCurrentRestore(operation);
            const result = applyPlanInputs(
                planNode, payload.plan_inputs, payload.policy_inputs);
            const assetResults = [];
            const graph = operation.graph;
            graph?.beforeChange?.();
            try {
                for (const binding of payload.assets?.bindings ?? []) {
                    assetResults.push(applyAssetBinding(graph, binding));
                }
            } finally {
                graph?.afterChange?.();
            }
            const assetFailures = assetResults.filter((item) => !item.applied);
            const assetApplied = assetResults.length - assetFailures.length;
            const warning = [
                ...(payload.warnings ?? []),
                ...(result.unavailable.length
                    ? [`Unavailable current widgets: ${result.unavailable.join(", ")}`] : []),
                ...(result.policies.unavailable.length
                    ? [`Unavailable 0.5 policies: ${result.policies.unavailable.join(", ")}`] : []),
                ...assetFailures.map((item) =>
                    `${item.binding?.label ?? "Asset"}: ${item.reason.replaceAll("_", " ")}`),
            ];
            status.className = warning.length
                ? "h3rm-status h3rm-error" : "h3rm-status";
            status.textContent = warning.length
                ? `Active Plan is now “${run.run_name}”: loaded ${payload.scene_count ?? "saved"} scenes and ${assetApplied} assets · ${warning.join(" · ")}`
                : `Active Plan is now “${run.run_name}”: loaded ${payload.scene_count ?? "saved"} scenes and ${assetApplied} assets`;
            syncAssetBindings();
            renderSelection();
            graph?.setDirtyCanvas?.(true, true);
        } catch (error) {
            status.className = "h3rm-status h3rm-error";
            status.textContent = error?.message || String(error);
        } finally {
            setBusy(false);
        }
    }

    async function openRunFolder() {
        const run = selectedRun();
        if (!run || state.busy) return;
        setBusy(true);
        status.className = "h3rm-status";
        status.textContent = "Opening run folder…";
        try {
            const response = await api.fetchApi(
                "/minimax_h3_context_loop/open-run-folder",
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({run_name: run.run_name}),
                },
            );
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            if (payload.opened) status.textContent = "Opened on ComfyUI host";
            else {
                try {
                    await navigator.clipboard.writeText(payload.path);
                    status.textContent = "Host path copied";
                } catch (_error) {
                    status.textContent = payload.path;
                }
                status.title = `${payload.path}${payload.error ? `\n${payload.error}` : ""}`;
            }
        } catch (error) {
            status.className = "h3rm-status h3rm-error";
            status.textContent = error?.message || String(error);
        } finally {
            setBusy(false);
        }
    }

    async function saveRunAssets() {
        const planNode = upstreamPlanNode(node);
        const runName = String(widgetByName(planNode, "run_name")?.value ?? "").trim();
        if (!planNode || !runName || !state.bindings.length || state.busy) {
            status.className = "h3rm-status h3rm-error";
            status.textContent = !planNode
                ? "Connect the active Plan first."
                : !runName
                    ? "Set run_name on the connected Plan first."
                    : "Connect at least one loader asset.";
            return;
        }
        syncAssetBindings();
        setBusy(true);
        status.className = "h3rm-status";
        status.textContent = "Saving asset manifest…";
        try {
            const response = await api.fetchApi(
                "/minimax_h3_context_loop/run-assets",
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        run_name: runName,
                        bindings: state.bindings,
                        archive_images: Boolean(widgetByName(node, "archive_images")?.value),
                        archive_audio: Boolean(widgetByName(node, "archive_audio")?.value),
                        archive_video: Boolean(widgetByName(node, "archive_video")?.value),
                    }),
                },
            );
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            const warning = payload.warnings?.length
                ? ` · ${payload.warnings.join(" · ")}` : "";
            const savedRunName = String(payload.run_name || runName);
            await refreshRuns(savedRunName);
            status.className = warning ? "h3rm-status h3rm-error" : "h3rm-status";
            status.textContent = `Saved ${payload.asset_count} bindings to “${savedRunName}”, ${payload.archived_asset_count ?? 0} archived${warning}`;
        } catch (error) {
            status.className = "h3rm-status h3rm-error";
            status.textContent = error?.message || String(error);
        } finally {
            setBusy(false);
        }
    }

    select.addEventListener("change", () => {
        state.restoreEpoch += 1;
        state.selected = select.value;
        status.className = "h3rm-status";
        status.textContent = "";
        renderSelection();
    });
    const load = button("Load selected archive into Plan", "Replace the connected Plan after confirmation", () => {
        void loadRun();
    });
    load.classList.add("h3rm-load");
    const refresh = button("Refresh", "Rescan output/h3_chains on the ComfyUI host", () => {
        void refreshRuns();
    });
    const open = button("Open selected folder", "Open the selected archive folder on the ComfyUI host", () => {
        void openRunFolder();
    });
    const saveAssets = button(
        "Save assets to active Plan",
        "Write loader paths and enabled fallback copies into the active run folder",
        () => { void saveRunAssets(); },
    );
    actions.append(load, refresh, open, saveAssets, status);
    assetSection.append(assetHeader, assetList);
    root.append(title, identity, select, details, assetSection, actions);

    const widget = node.addDOMWidget("h3_run_manager", "h3-run-manager", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 340,
    });
    widget.serialize = false;
    node.setSize?.([
        Math.max(Number(node.size?.[0]) || 0, 520),
        Math.max(Number(node.size?.[1]) || 0, 500),
    ]);
    const connectionsChanged = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        state.restoreEpoch += 1;
        const result = connectionsChanged?.apply(this, arguments);
        window.setTimeout(() => {
            if (state.disposed) return;
            stabilizeAssetInputs(node);
            syncAssetBindings();
            renderSelection();
        }, 0);
        return result;
    };
    const serialized = node.onSerialize;
    node.onSerialize = function () {
        syncAssetBindings();
        return serialized?.apply(this, arguments);
    };
    const removed = node.onRemoved;
    node.onRemoved = function () {
        state.disposed = true;
        state.restoreEpoch += 1;
        for (const source of state.watchedSources) {
            source._h3AssetWatchers?.delete(sourceChanged);
        }
        state.watchedSources.clear();
        state.watchedPlanWidget?._h3RunManagerWatchers?.delete(activeRunChanged);
        state.watchedPlanWidget = null;
        return removed?.apply(this, arguments);
    };
    node._h3RunManagerRefresh = () => {
        if (state.disposed) return;
        removeLegacyStatusOutput(node);
        for (const name of ASSET_WIDGETS) collapseWidget(widgetByName(node, name));
        stabilizeAssetInputs(node);
        syncAssetBindings();
        renderSelection();
    };
    window.setTimeout(() => {
        node._h3RunManagerRefresh?.();
    }, 100);
    void refreshRuns(activeRunName());
}

app.registerExtension({
    name: "minimax_h3_context_loop.run_manager",
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
            window.setTimeout(() => this._h3RunManagerRefresh?.(), 0);
            return result;
        };
    const graphConfigured = nodeTypeClass.prototype.onGraphConfigured;
        nodeTypeClass.prototype.onGraphConfigured = function () {
            const result = graphConfigured?.apply(this, arguments);
            window.setTimeout(() => this._h3RunManagerRefresh?.(), 0);
            return result;
        };
    },
    async nodeCreated(node) {
        if (nodeType(node) === NODE_NAME) mount(node);
    },
});
