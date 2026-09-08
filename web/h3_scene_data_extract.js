import {app} from "../../scripts/app.js";
import {sceneDataFieldPresentation} from "./h3_scene_data_core.mjs?v=0.6.8";

const NODE_NAME = "MiniMaxH3SceneDataExtract";

function fieldWidget(node) {
    return node.widgets?.find((widget) => widget.name === "field") ?? null;
}

function refreshOutput(node) {
    const output = node.outputs?.[0];
    if (!output) return;
    const presentation = sceneDataFieldPresentation(fieldWidget(node)?.value);
    output.name = presentation.name;
    output.label = presentation.name;
    output.type = presentation.type;
    node.setDirtyCanvas?.(true, true);
}

function install(node) {
    const widget = fieldWidget(node);
    if (!widget || widget._h3SceneDataWrapped) {
        refreshOutput(node);
        return;
    }
    widget._h3SceneDataWrapped = true;
    const original = widget.callback;
    widget.callback = function () {
        const result = original?.apply(this, arguments);
        refreshOutput(node);
        return result;
    };
    refreshOutput(node);
}

app.registerExtension({
    name: "minimax.h3.sceneDataExtract",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            install(this);
            return result;
        };

        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = configured?.apply(this, arguments);
            install(this);
            return result;
        };
    },
});
