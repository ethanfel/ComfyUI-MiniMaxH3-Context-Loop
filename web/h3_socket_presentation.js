import {app} from "/scripts/app.js";
import {
    ADVANCED_POLICY_NODE,
    CHAIN_POLICY_NODE,
    LEGACY_POLICY_NODE,
    MODERN_PLAN_NODE,
    PLAN_NODE,
    PROFILE_POLICY_NODE,
    applySocketPresentation,
    hasAdvancedPresentation,
    nodeType,
    policyPlanConsumers,
    presentationForNode,
} from "./h3_socket_presentation_core.mjs?v=0.6.8";

const EXTENSION = "minimax_h3_context_loop.socket_presentation";
const WATCHED_POLICY_NODES = new Set([
    PROFILE_POLICY_NODE, CHAIN_POLICY_NODE, ADVANCED_POLICY_NODE,
    LEGACY_POLICY_NODE, PLAN_NODE, MODERN_PLAN_NODE,
]);
const WIDGET_LABELS = Object.freeze({
    [PROFILE_POLICY_NODE]: Object.freeze({
        scene_continuity:"Scene continuity",
        audio_profile:"Audio profile",
    }),
});

function collapseWidget(widget) {
    if (!widget || widget.h3PresentationHidden) return;
    widget.h3PresentationHidden = true;
    widget.h3PresentationOriginal = {
        type: widget.type,
        hidden: widget.hidden,
        computeSize: widget.computeSize,
        draw: widget.draw,
    };
    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
    widget.draw = () => {};
    for (const item of new Set([widget.inputEl, widget.element])) {
        if (!item?.style) continue;
        item.style.setProperty("display", "none", "important");
        item.style.setProperty("pointer-events", "none", "important");
        item.setAttribute?.("aria-hidden", "true");
    }
}

function restoreWidget(widget) {
    if (!widget?.h3PresentationHidden) return;
    const original = widget.h3PresentationOriginal ?? {};
    widget.type = original.type;
    widget.hidden = original.hidden;
    widget.computeSize = original.computeSize;
    widget.draw = original.draw;
    widget.h3PresentationHidden = false;
    for (const item of new Set([widget.inputEl, widget.element])) {
        if (!item?.style) continue;
        item.style.removeProperty("display");
        item.style.removeProperty("pointer-events");
        item.removeAttribute?.("aria-hidden");
    }
}

function refreshNode(node) {
    if (!node) return;
    node.properties ??= {};
    const labels = WIDGET_LABELS[nodeType(node)] ?? {};
    for (const widget of node.widgets ?? []) {
        if (labels[widget.name]) widget.label = labels[widget.name];
    }
    const advanced = Boolean(node.properties.h3_show_advanced_sockets);
    const presentation = presentationForNode(node, advanced);
    applySocketPresentation(node, advanced);
    for (const widget of node.widgets ?? []) {
        if (presentation.hiddenWidgets.has(widget.name)) collapseWidget(widget);
        else restoreWidget(widget);
    }
    node.graph?.setDirtyCanvas?.(true, true);
}

function refreshGraph(graph) {
    for (const node of graph?._nodes ?? app.graph?._nodes ?? []) refreshNode(node);
}

function scheduleGraphRefresh(node) {
    if (node._h3PresentationRefreshPending) return;
    node._h3PresentationRefreshPending = true;
    queueMicrotask(() => {
        node._h3PresentationRefreshPending = false;
        refreshGraph(node.graph ?? app.graph);
        refreshPolicyConsumers(node);
    });
}

function refreshPolicyConsumers(node) {
    const plans = new Set(policyPlanConsumers(node));
    if (!plans.size) return;
    for (const plan of plans) {
        plan._h3ChainEditorConnectionRefresh?.();
        plan.graph?.setDirtyCanvas?.(true, true);
    }
    // Companion editors cache the resolved timing policy separately from the
    // Plan JSON. A policy widget edit is not a LiteGraph connection event, so
    // explicitly invalidate companions bound to one of the affected Plans.
    for (const candidate of node.graph?._nodes ?? []) {
        if (candidate._h3PlanStudioState?.planNode
                && !plans.has(candidate._h3PlanStudioState.planNode)) continue;
        candidate._h3PlanStudioRefresh?.();
        candidate._h3ScenePromptEditorRefresh?.();
        candidate._h3RichPromptRefresh?.();
    }
}

function watchWidgets(node) {
    for (const widget of node.widgets ?? []) {
        if (widget.h3PresentationCallbackWrapped) continue;
        widget.h3PresentationCallbackWrapped = true;
        const original = widget.callback;
        widget.callback = function () {
            const result = original?.apply(this, arguments);
            scheduleGraphRefresh(node);
            return result;
        };
    }
}

app.registerExtension({
    name: EXTENSION,
    async beforeRegisterNodeDef(nodeClass, nodeData) {
        const relevant = String(nodeData.name ?? "").startsWith("MiniMaxH3")
            || WATCHED_POLICY_NODES.has(nodeData.name);
        if (!relevant) return;

        const originalCreated = nodeClass.prototype.onNodeCreated;
        nodeClass.prototype.onNodeCreated = function () {
            const result = originalCreated?.apply(this, arguments);
            watchWidgets(this);
            refreshNode(this);
            return result;
        };

        const originalConfigure = nodeClass.prototype.onConfigure;
        nodeClass.prototype.onConfigure = function () {
            const result = originalConfigure?.apply(this, arguments);
            watchWidgets(this);
            refreshNode(this);
            scheduleGraphRefresh(this);
            return result;
        };

        const originalConnectionsChange = nodeClass.prototype.onConnectionsChange;
        nodeClass.prototype.onConnectionsChange = function () {
            const result = originalConnectionsChange?.apply(this, arguments);
            scheduleGraphRefresh(this);
            return result;
        };

        const originalMenu = nodeClass.prototype.getExtraMenuOptions;
        nodeClass.prototype.getExtraMenuOptions = function (_, options) {
            const result = originalMenu?.apply(this, arguments);
            if (!hasAdvancedPresentation(this)) return result;
            const advanced = Boolean(this.properties?.h3_show_advanced_sockets);
            options.push({
                content: advanced
                    ? "Hide advanced H3 controls"
                    : "Show advanced H3 controls",
                callback: () => {
                    this.properties ??= {};
                    this.properties.h3_show_advanced_sockets = !advanced;
                    refreshNode(this);
                },
            });
            return result;
        };
    },
});
