import {app} from "/scripts/app.js";
import {
    createLegacyWidgetWidthController,
    graphNodes,
} from "./h3_legacy_widget_width_fix_core.mjs?v=0.6.8";

const SETTING_ID = "MiniMaxH3ContexLoop.legacyWidgetWidthFix";
const LEGACY_FIX_NODE = "LegacyWidgetWidthFix";
const REPAIR_BUTTON = "Repair widget widths now";
const HOST_LIFECYCLE_PATCH = "__h3_lwwf_host_lifecycle";
const LEGACY_LIFECYCLE_PATCH = "__h3_lwwf_legacy_lifecycle";
const LEGACY_DISPLAY_PREFIX = "MiniMax H3 Contex Loop";
const CURRENT_DISPLAY_PREFIX = "MiniMax H3 Context Loop";

const H3_NODE_TYPES = new Set([
    "MiniMaxH3LoopTrim",
    "MiniMaxH3ContexLoopSeamProbe",
    "MiniMaxH3ChainPlan",
    "MiniMaxH3ChainPlanModern",
    "MiniMaxH3ChainCheckpointManager",
    "MiniMaxH3ChainScenePromptEditor",
    "MiniMaxH3ChainRunManager",
    "MiniMaxH3ReferenceVideoPrepare",
    "MiniMaxH3ScheduledPictureReference",
    "MiniMaxH3ScheduledVideoReference",
    "MiniMaxH3ScheduledAudioReference",
    "MiniMaxH3ScheduledReferenceToVideo",
    "MiniMaxH3TaggedPictureReference",
    "MiniMaxH3TaggedVideoReference",
    "MiniMaxH3TaggedMotionReference",
    "MiniMaxH3LazyMotionAVLoader",
    "MiniMaxH3TaggedMotionReferencePath",
    "MiniMaxH3LazyMotionScenePreview",
    "MiniMaxH3TaggedAudioReference",
    "MiniMaxH3TaggedReferenceToVideo",
    "MiniMaxH3ChainExternalVideo",
    "MiniMaxH3ChainLoopStart",
    "MiniMaxH3ChainCurrent",
    "MiniMaxH3ChainContext",
    "MiniMaxH3ChainSegmentSave",
    "MiniMaxH3ChainReview",
    "MiniMaxH3ChainLoopEnd",
    "MiniMaxH3ChainManifestLoad",
    "MiniMaxH3ChainExportPNG",
    "MiniMaxH3ChainLatentVideoAdapter",
    "MiniMaxH3ChainAssemble",
]);

function nodeType(node) {
    return node?.comfyClass ?? node?.constructor?.comfyClass ?? node?.type ?? null;
}

function isH3Host(node) {
    return H3_NODE_TYPES.has(nodeType(node));
}

function correctLegacyNodeTitle(node) {
    if (!isH3Host(node) || typeof node?.title !== "string"
            || !node.title.includes(LEGACY_DISPLAY_PREFIX)) return false;
    node.title = node.title.replaceAll(
        LEGACY_DISPLAY_PREFIX, CURRENT_DISPLAY_PREFIX);
    return true;
}

function correctLegacyGraphTitles(graph) {
    for (const node of graphNodes(graph)) correctLegacyNodeTitle(node);
}

function schedule(callback) {
    if (typeof globalThis.requestAnimationFrame === "function") {
        globalThis.requestAnimationFrame(callback);
    } else {
        globalThis.setTimeout?.(callback, 0);
    }
}

function askStandaloneFixToRepair() {
    schedule(() => {
        for (const node of graphNodes(app.graph)) {
            if (nodeType(node) !== LEGACY_FIX_NODE) continue;
            const button = node.widgets?.find((widget) =>
                widget.name === REPAIR_BUTTON);
            button?.callback?.();
        }
    });
}

const controller = createLegacyWidgetWidthController({
    getGraph: () => app.graph,
    requestFrame: schedule,
    onRelinquish: askStandaloneFixToRepair,
});

function patchHostLifecycle(nodeTypeDefinition) {
    const prototype = nodeTypeDefinition?.prototype;
    if (!prototype || prototype[HOST_LIFECYCLE_PATCH]) return;
    Object.defineProperty(prototype, HOST_LIFECYCLE_PATCH, {value: true});

    const originalAdded = prototype.onAdded;
    prototype.onAdded = function h3WidthFixHostAdded(...args) {
        const result = originalAdded?.apply(this, args);
        controller.addHost(this);
        return result;
    };

    const originalRemoved = prototype.onRemoved;
    prototype.onRemoved = function h3WidthFixHostRemoved(...args) {
        try {
            return originalRemoved?.apply(this, args);
        } finally {
            controller.removeHost(this);
        }
    };
}

function patchStandaloneRemoval(nodeTypeDefinition) {
    const prototype = nodeTypeDefinition?.prototype;
    if (!prototype || prototype[LEGACY_LIFECYCLE_PATCH]) return;
    Object.defineProperty(prototype, LEGACY_LIFECYCLE_PATCH, {value: true});
    const originalRemoved = prototype.onRemoved;
    prototype.onRemoved = function h3WidthFixStandaloneRemoved(...args) {
        try {
            return originalRemoved?.apply(this, args);
        } finally {
            controller.scheduleRepairAll();
        }
    };
}

function settingEnabled() {
    return app.ui?.settings?.getSettingValue?.(SETTING_ID) !== false;
}

app.registerExtension({
    name: "MiniMaxH3ContexLoop.LegacyWidgetWidthFix",

    init() {
        controller.installHooks(globalThis.LGraphNode?.prototype);
        app.ui?.settings?.addSetting?.({
            id: SETTING_ID,
            category: ["MiniMax H3 Context Loop", "Compatibility", "Widget widths"],
            name: "Repair legacy widget widths while H3 nodes are present",
            tooltip: "Works around ComfyUI frontend issue #12443 across the whole canvas. Disable only if your frontend has fixed the LiteGraph widget-width regression.",
            type: "boolean",
            defaultValue: true,
            onChange(value) {
                controller.setAllowed(value !== false);
            },
        });
        controller.setAllowed(settingEnabled());
    },

    beforeRegisterNodeDef(nodeTypeDefinition, nodeData) {
        controller.installHooks(globalThis.LGraphNode?.prototype);
        if (H3_NODE_TYPES.has(nodeData?.name)) {
            patchHostLifecycle(nodeTypeDefinition);
        } else if (nodeData?.name === LEGACY_FIX_NODE) {
            patchStandaloneRemoval(nodeTypeDefinition);
        }
    },

    nodeCreated(node) {
        controller.installHooks(globalThis.LGraphNode?.prototype);
        correctLegacyNodeTitle(node);
        if (isH3Host(node)) controller.addHost(node);
        controller.nodeCreated(node);
    },

    afterConfigureGraph() {
        correctLegacyGraphTitles(app.graph);
        controller.syncHosts(graphNodes(app.graph), isH3Host);
        controller.scheduleRepairAll();
    },

    setup() {
        controller.installHooks(globalThis.LGraphNode?.prototype);
        controller.setAllowed(settingEnabled());
        correctLegacyGraphTitles(app.graph);
        controller.syncHosts(graphNodes(app.graph), isH3Host);
        controller.scheduleRepairAll();
    },
});
