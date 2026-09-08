import {
    GENERATION_AUDIO_PROFILES,
    GENERATION_SCENE_PROFILES,
    LEGACY_AUDIO_POLICIES,
    TRANSITION_PRESETS,
    transitionPreset,
    transitionPresetName,
} from "./h3_policy_core.mjs?v=0.6.8";

export const CHAIN_POLICY_NODE = "MiniMaxH3ChainPolicy";
export const PROFILE_POLICY_NODE = "MiniMaxH3GenerationProfile";
export const ADVANCED_POLICY_NODE = "MiniMaxH3AdvancedPolicy";
export const LEGACY_POLICY_NODE = "MiniMaxH3Legacy04PolicyAdapter";
export const PLAN_NODE = "MiniMaxH3ChainPlan";
export const MODERN_PLAN_NODE = "MiniMaxH3ChainPlanModern";
export const PLAN_NODES = new Set([PLAN_NODE, MODERN_PLAN_NODE]);

const CONDITIONAL_SOURCE_AUDIO_NODES = new Set([
    "MiniMaxH3ChainLoopStart",
    "MiniMaxH3ChainPlanStudio",
    "MiniMaxH3ChainPreflight",
    "MiniMaxH3ChainManifestLoad",
]);

const CURRENT_NODE = "MiniMaxH3ChainCurrent";
const REVIEW_NODE = "MiniMaxH3ChainReview";
const ASSEMBLE_NODE = "MiniMaxH3ChainAssemble";

const ADVANCED_OUTPUTS = Object.freeze({
    MiniMaxH3ChainPolicy: ["status"],
    MiniMaxH3ChainPlan: ["summary", "clip_count", "video_blend_frames"],
    MiniMaxH3ChainPlanModern: ["summary", "clip_count", "video_blend_frames"],
    MiniMaxH3ChainPlanStudio: [
        "status", "report_json", "plan_summary", "clip_count",
        "video_blend_frames",
    ],
    MiniMaxH3ChainPreflight: ["status", "report_json"],
    MiniMaxH3LazyMotionAVLoader: ["source_audio", "skip_first_frames", "status"],
    MiniMaxH3ChainCurrent: [
        "clip_count", "shot_id", "steps", "audio_start", "audio_duration",
        "status",
    ],
    MiniMaxH3ChainLoopEnd: [
        "manifest_json", "last_context_frames", "last_context_latent",
    ],
    MiniMaxH3ChainManifestLoad: ["manifest_json", "status"],
});

const ALWAYS_ADVANCED_WIDGETS = Object.freeze({
    MiniMaxH3ChainPlanStudio: [
        "verify_resume_history",
        // Backing values for the Studio's dedicated Plan settings tab. Keep
        // them serialized and recoverable through Show advanced without
        // duplicating the normal Studio interface above its timeline.
        "plan_json", "run_name", "generation_fingerprint", "width", "height",
        "context_length", "encode_mode", "anchor_mode", "crop", "audio_mode",
        "audio_context_length", "default_duration_seconds", "default_steps",
        "base_seed", "segment_crf", "video_blend_frames", "continuation_mode",
    ],
    MiniMaxH3ChainPreflight: ["verify_resume_history"],
    MiniMaxH3ChainUpscaleAdapter: ["recipe_json"],
});

export function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

export function isPlanNode(node) {
    return PLAN_NODES.has(typeof node === "string" ? node : nodeType(node));
}

export function widgetByName(node, name) {
    return node?.widgets?.find((widget) => widget.name === name) ?? null;
}

export function inputByName(node, name) {
    return node?.inputs?.find((input) => input.name === name) ?? null;
}

export function outputByName(node, name) {
    return node?.outputs?.find((output) => output.name === name) ?? null;
}

function graphLink(graph, id) {
    if (id == null) return null;
    return graph?.links?.[id] ?? graph?.links?.get?.(id) ?? null;
}

function linkedOrigin(node, input) {
    const link = graphLink(node?.graph, input?.link);
    return link ? node.graph?.getNodeById?.(link.origin_id) ?? null : null;
}

export function policyPlanConsumers(policyNode) {
    const type = nodeType(policyNode);
    const inputNames = (type === PROFILE_POLICY_NODE
            || type === CHAIN_POLICY_NODE
            || type === ADVANCED_POLICY_NODE
            || type === LEGACY_POLICY_NODE)
        ? ["chain_policy"] : [];
    if (!inputNames.length) return [];
    const graph = policyNode?.graph;
    return (graph?._nodes ?? []).filter((candidate) =>
        isPlanNode(candidate)
        && inputNames.some((name) => {
            const origin = linkedOrigin(candidate, inputByName(candidate, name));
            return upstreamNodes(origin).includes(policyNode);
        }),
    );
}

function linked(slot, output = false) {
    if (!slot) return false;
    if (!output) return slot.link != null;
    return Array.isArray(slot.links) ? slot.links.length > 0 : slot.links != null;
}

export function upstreamNodes(start) {
    const result = [];
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        result.push(node);
        for (const input of node.inputs ?? []) {
            const parent = linkedOrigin(node, input);
            if (parent) queue.push(parent);
        }
    }
    return result;
}

function audioPolicyFromWidgets(node) {
    if (nodeType(node) === LEGACY_POLICY_NODE) {
        if (linkedOrigin(node, inputByName(node, "chain_policy"))) return null;
        const mode = String(widgetByName(node, "audio_mode")?.value ?? "");
        const mapped = LEGACY_AUDIO_POLICIES[mode];
        if (!mapped) return null;
        return {
            known: true,
            finalAudio: mapped[0],
            sourceReference: mapped[1],
            generatedContinuity: mapped[2],
            source: "legacy_adapter",
        };
    }
    const type = nodeType(node);
    if (type === PROFILE_POLICY_NODE) {
        const selected = String(
            widgetByName(node, "audio_profile")?.value ?? "");
        const mapped = GENERATION_AUDIO_PROFILES[selected];
        if (!mapped) return null;
        const policy = {
            known: true,
            finalAudio: mapped.finalAudio,
            sourceReference: mapped.sourceReference,
            generatedContinuity: mapped.generatedContinuity,
            source: "profile",
        };
        if (mapped.sourceAudioTarget === "locked") {
            policy.sourceAudioTarget = "locked";
        }
        return policy;
    }
    if (type !== CHAIN_POLICY_NODE) return null;
    const finalAudio = widgetByName(node, "final_audio")?.value;
    const sourceReference = widgetByName(node, "source_reference")?.value;
    const generatedContinuity = widgetByName(node, "generated_continuity")?.value;
    const sourceAudioTargetLocked = Boolean(
        widgetByName(node, "lock_source_audio")?.value);
    if (finalAudio == null || sourceReference == null
            || generatedContinuity == null) return null;
    const policy = {
        known: true,
        finalAudio: String(finalAudio),
        sourceReference: sourceAudioTargetLocked
            ? "off" : String(sourceReference),
        generatedContinuity: sourceAudioTargetLocked
            ? "off" : String(generatedContinuity),
        source: "compact",
    };
    if (sourceAudioTargetLocked) policy.sourceAudioTarget = "locked";
    return policy;
}

function legacyAudioPolicy(plan) {
    const mode = widgetByName(plan, "audio_mode")?.value;
    if (mode == null) return null;
    const mapped = LEGACY_AUDIO_POLICIES[String(mode)];
    if (!mapped) return null;
    return {
        known: true,
        finalAudio: mapped[0],
        sourceReference: mapped[1],
        generatedContinuity: mapped[2],
        source: "legacy",
    };
}

export function resolveAudioPolicy(start) {
    let planFallback = null;
    for (const node of upstreamNodes(start)) {
        const direct = audioPolicyFromWidgets(node);
        if (direct) return direct;
        if (!isPlanNode(node)) continue;
        if (!linkedOrigin(node, inputByName(node, "chain_policy"))) {
            planFallback ??= legacyAudioPolicy(node);
        }
    }
    if (planFallback) return planFallback;
    return {
        known: false,
        finalAudio: null,
        sourceReference: null,
        generatedContinuity: null,
        source: "unknown",
    };
}

function directAudioContextLength(node) {
    const type = nodeType(node);
    if (type === PROFILE_POLICY_NODE) {
        const selected = String(
            widgetByName(node, "scene_continuity")?.value ?? "");
        const preset = transitionPreset(GENERATION_SCENE_PROFILES[selected]);
        return preset?.contextLength ?? null;
    }
    if (type === CHAIN_POLICY_NODE || type === ADVANCED_POLICY_NODE) {
        const preset = transitionPreset(String(
            widgetByName(node, "incoming_transition")?.value ?? ""));
        return preset?.contextLength ?? null;
    }
    if (type === LEGACY_POLICY_NODE) {
        const value = Number(widgetByName(node, "audio_context_length")?.value);
        return Number.isInteger(value) ? value : null;
    }
    return null;
}

export function resolveAudioContextLength(start) {
    let planFallback = null;
    for (const node of upstreamNodes(start)) {
        const direct = directAudioContextLength(node);
        if (direct != null) return direct;
        if (!isPlanNode(node)) continue;
        if (!linkedOrigin(node, inputByName(node, "chain_policy"))) {
            const legacy = Number(
                widgetByName(node, "audio_context_length")?.value);
            if (Number.isInteger(legacy)) planFallback ??= legacy;
        }
    }
    return planFallback ?? 22;
}

function transitionPolicyFromWidgets(node) {
    const type = nodeType(node);
    if (type === LEGACY_POLICY_NODE) {
        const continuationMode = String(
            widgetByName(node, "continuation_mode")?.value ?? "");
        const contextLength = Number(
            widgetByName(node, "context_length")?.value);
        if (!Number.isInteger(contextLength) || !continuationMode) return null;
        const preset = transitionPresetName(continuationMode, contextLength);
        return {
            known: true, preset, continuationMode, contextLength,
            expertOverride: preset === "custom", source: "legacy_adapter",
        };
    }
    if (type === PROFILE_POLICY_NODE) {
        const selected = String(
            widgetByName(node, "scene_continuity")?.value ?? "");
        const preset = GENERATION_SCENE_PROFILES[selected];
        const pair = transitionPreset(preset);
        if (!pair) return null;
        return {
            known: true, preset,
            continuationMode: pair.continuationMode,
            contextLength: pair.contextLength,
            expertOverride: false,
            source: "profile",
        };
    }
    if (type === CHAIN_POLICY_NODE || type === ADVANCED_POLICY_NODE) {
        const preset = String(
            widgetByName(node, "incoming_transition")?.value ?? "");
        const pair = transitionPreset(preset);
        if (!pair) return null;
        return {
            known: true, preset,
            continuationMode: pair.continuationMode,
            contextLength: pair.contextLength,
            expertOverride: false,
            source: type === ADVANCED_POLICY_NODE ? "advanced" : "compact",
        };
    }
    return null;
}

function legacyTransitionPolicy(plan) {
    const continuationMode = widgetByName(plan, "continuation_mode")?.value;
    const contextLength = Number(widgetByName(plan, "context_length")?.value);
    if (continuationMode == null || !Number.isInteger(contextLength)) return null;
    const preset = transitionPresetName(continuationMode, contextLength);
    return {
        known: true,
        preset,
        continuationMode: String(continuationMode),
        contextLength,
        expertOverride: preset === "custom",
        source: "legacy",
    };
}

export function resolveTransitionPolicy(start) {
    let planFallback = null;
    for (const node of upstreamNodes(start)) {
        const direct = transitionPolicyFromWidgets(node);
        if (direct) return direct;
        if (!isPlanNode(node)) continue;
        if (!linkedOrigin(node, inputByName(node, "chain_policy"))) {
            planFallback ??= legacyTransitionPolicy(node);
        }
    }
    if (planFallback) return planFallback;
    return {
        known: false,
        preset: null,
        continuationMode: null,
        contextLength: null,
        expertOverride: false,
        source: "unknown",
    };
}

export function hasSourceTimeline(start) {
    return upstreamNodes(start).some((node) =>
        linked(inputByName(node, "source_timeline")));
}

function sourceAudioInputNeeded(node, policy) {
    const type = nodeType(node);
    if (hasSourceTimeline(node)) return false;
    if (type === CURRENT_NODE) {
        return !policy.known || policy.sourceReference === "on"
            || policy.sourceAudioTarget === "locked";
    }
    if (type === REVIEW_NODE) {
        return String(widgetByName(node, "partial_audio_source")?.value)
            === "source";
    }
    if (type === ASSEMBLE_NODE) {
        const selection = String(widgetByName(node, "audio_source")?.value ?? "plan");
        if (selection === "source") return true;
        if (selection !== "plan") return false;
        return !policy.known || policy.finalAudio === "source";
    }
    if (CONDITIONAL_SOURCE_AUDIO_NODES.has(type)) {
        return !policy.known || policy.finalAudio === "source"
            || policy.sourceReference === "on"
            || policy.sourceAudioTarget === "locked";
    }
    return true;
}

function advancedOutputNames(node) {
    const configured = ADVANCED_OUTPUTS[nodeType(node)] ?? [];
    const names = new Set(configured);
    // Status is a human diagnostic for every node in this pack. Keeping this
    // generic means new 0.5 nodes inherit the same presentation rule without
    // changing their backend result tuple.
    if (outputByName(node, "status")) names.add("status");
    return names;
}

function advancedWidgetNames(node, policy) {
    const names = new Set(ALWAYS_ADVANCED_WIDGETS[nodeType(node)] ?? []);
    if (nodeType(node) === PLAN_NODE) {
        names.add("audio_mode");
        names.add("continuation_mode");
        names.add("context_length");
        names.add("audio_context_length");
        names.add("encode_mode");
        names.add("anchor_mode");
        names.add("crop");
        names.add("video_blend_frames");
    }
    if (nodeType(node) === CURRENT_NODE
            && policy.known && policy.sourceReference !== "on") {
        names.add("align_audio_reference");
    }
    return names;
}

export function presentationForNode(node, showAdvanced = false) {
    const policy = resolveAudioPolicy(node);
    const hiddenInputs = new Set();
    const hiddenOutputs = new Set();
    const hiddenWidgets = new Set();
    if (!showAdvanced) {
        for (const name of advancedOutputNames(node)) hiddenOutputs.add(name);
        for (const name of advancedWidgetNames(node, policy)) hiddenWidgets.add(name);
        const sourceAudio = inputByName(node, "source_audio");
        if (sourceAudio && !sourceAudioInputNeeded(node, policy)) {
            hiddenInputs.add("source_audio");
        }
        if (nodeType(node) === CURRENT_NODE && policy.known
                && policy.sourceReference !== "on") {
            hiddenOutputs.add("source_audio_slice");
        }
        // Converted widgets retain their backend input names. Apply the same
        // presentation decision without removing their slot from the array.
        for (const name of hiddenWidgets) {
            if (inputByName(node, name)) hiddenInputs.add(name);
        }
    }
    // A chained Legacy Adapter is a raw boundary layer. Its 0.4 audio_mode is
    // deliberately ignored by the backend because the upstream modern policy
    // owns audio intent, so do not present an inert control in either view.
    if (nodeType(node) === LEGACY_POLICY_NODE
            && linkedOrigin(node, inputByName(node, "chain_policy"))) {
        hiddenWidgets.add("audio_mode");
        if (inputByName(node, "audio_mode")) hiddenInputs.add("audio_mode");
    }
    return {hiddenInputs, hiddenOutputs, hiddenWidgets, policy};
}

function setSlotPresentation(slot, hide, output = false) {
    if (!slot) return;
    const effective = Boolean(hide) && !linked(slot, output);
    slot.hidden = effective;
    slot.h3Advanced = Boolean(hide);
    slot.h3PresentationHidden = effective;
}

export function applySocketPresentation(node, showAdvanced = undefined) {
    const advanced = showAdvanced ?? Boolean(
        node?.properties?.h3_show_advanced_sockets);
    const presentation = presentationForNode(node, advanced);
    for (const slot of node?.inputs ?? []) {
        setSlotPresentation(
            slot, presentation.hiddenInputs.has(slot.name), false);
    }
    for (const slot of node?.outputs ?? []) {
        setSlotPresentation(
            slot, presentation.hiddenOutputs.has(slot.name), true);
    }
    return presentation;
}

export function hasAdvancedPresentation(node) {
    const compact = presentationForNode(node, false);
    return compact.hiddenInputs.size > 0 || compact.hiddenOutputs.size > 0
        || compact.hiddenWidgets.size > 0;
}
