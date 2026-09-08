import {app} from "/scripts/app.js";
import {
    LORA_ROUTE_LETTERS,
    LORA_SCHEDULER_NODE,
    connectedLoRARoutes,
    loraInputRoute,
    nextLoRARoute,
} from "./h3_lora_scheduler_core.mjs?v=0.6.8";

const ROUTES_CHANGED_EVENT = "h3-lora-routes-changed";

function publishRoutes(node, routes) {
    const signature = routes.join(",");
    if (node._h3LoRARouteSignature === signature) return;
    node._h3LoRARouteSignature = signature;
    document.dispatchEvent(new CustomEvent(ROUTES_CHANGED_EVENT, {
        detail: {nodeId: node.id, routes},
    }));
}

function stabilizeRouteInputs(node) {
    const routes = connectedLoRARoutes(node);
    const next = nextLoRARoute(node);
    for (let index = (node.inputs?.length ?? 0) - 1; index >= 0; index -= 1) {
        const input = node.inputs[index];
        const route = loraInputRoute(input.name);
        if (route && input.link == null && route !== next) node.removeInput(index);
    }
    if (next && !node.inputs?.some(
        (input) => input.name === `lora_${next}`,
    )) node.addInput(`lora_${next}`, "MODEL");
    for (const input of node.inputs ?? []) {
        const route = loraInputRoute(input.name);
        if (!route) continue;
        input.label = input.link == null
            ? `Connect next LoRA route · ${route.toUpperCase()}`
            : `LoRA ${route.toUpperCase()}`;
    }
    node.properties ??= {};
    node.properties.h3_lora_routes = routes;
    publishRoutes(node, routes);
    node.graph?.setDirtyCanvas?.(true, true);
}

function mount(node) {
    if (node._h3LoRASchedulerMounted) return;
    node._h3LoRASchedulerMounted = true;
    const changed = node.onConnectionsChange;
    node.onConnectionsChange = function () {
        const result = changed?.apply(this, arguments);
        setTimeout(() => stabilizeRouteInputs(this), 0);
        return result;
    };
    node._h3LoRASchedulerRefresh = () => stabilizeRouteInputs(node);
    setTimeout(() => stabilizeRouteInputs(node), 0);
}

app.registerExtension({
    name: "minimax_h3_context_loop.dynamic_lora_scheduler",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== LORA_SCHEDULER_NODE) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setTimeout(() => mount(this), 0);
            return result;
        };
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            setTimeout(() => this._h3LoRASchedulerRefresh?.(), 0);
            return result;
        };
        const graphConfigured = nodeType.prototype.onGraphConfigured;
        nodeType.prototype.onGraphConfigured = function () {
            const result = graphConfigured?.apply(this, arguments);
            setTimeout(() => this._h3LoRASchedulerRefresh?.(), 0);
            return result;
        };
    },
    async nodeCreated(node) {
        if ((node?.comfyClass ?? node?.type) === LORA_SCHEDULER_NODE) mount(node);
    },
    async afterConfigureGraph() {
        for (const node of app.graph?._nodes ?? []) {
            if ((node?.comfyClass ?? node?.type) === LORA_SCHEDULER_NODE) {
                setTimeout(() => node._h3LoRASchedulerRefresh?.(), 0);
            }
        }
    },
});
