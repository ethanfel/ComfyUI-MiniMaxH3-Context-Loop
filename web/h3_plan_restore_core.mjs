import {
    ADVANCED_TRANSITION_PRESETS,
    GENERATION_AUDIO_PROFILES,
    GENERATION_SCENE_PROFILES,
    LEGACY_AUDIO_POLICIES,
    PRIMARY_TRANSITION_PRESETS,
    transitionPreset,
    transitionPresetName,
} from "./h3_policy_core.mjs?v=0.6.8";

const CHAIN_POLICY_NODE = "MiniMaxH3ChainPolicy";
const PROFILE_POLICY_NODE = "MiniMaxH3GenerationProfile";
const ADVANCED_POLICY_NODE = "MiniMaxH3AdvancedPolicy";
const LEGACY_POLICY_NODE = "MiniMaxH3Legacy04PolicyAdapter";

function nodeType(node) {
    return node?.comfyClass ?? node?.type ?? null;
}

function graphLink(graph, linkId) {
    return graph?.links?.[linkId] ?? graph?.links?.get?.(linkId) ?? null;
}

function widgetByName(node, name) {
    return node?.widgets?.find((item) => item.name === name);
}

function allNodes(graph, output = []) {
    for (const node of graph?._nodes ?? []) {
        output.push(node);
        if (node.subgraph) allNodes(node.subgraph, output);
    }
    return output;
}

function linkedInputOrigin(node, inputName) {
    const input = node?.inputs?.find((item) => item.name === inputName);
    if (input?.link == null) return null;
    const link = graphLink(node.graph, input.link);
    return link ? node.graph?.getNodeById?.(link.origin_id) ?? null : null;
}

function findUpstreamType(start, wantedType) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (nodeType(node) === wantedType) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = graphLink(node.graph, input.link);
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function findFirstUpstreamType(start, wantedTypes) {
    const queue = [start];
    const seen = new Set();
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (wantedTypes.has(nodeType(node))) return node;
        for (const input of node.inputs ?? []) {
            if (input.link == null) continue;
            const link = graphLink(node.graph, input.link);
            const parent = link ? node.graph?.getNodeById?.(link.origin_id) : null;
            if (parent) queue.push(parent);
        }
    }
    return null;
}

function writeWidget(node, name, value, unavailable, label) {
    const widget = widgetByName(node, name);
    if (!widget) {
        unavailable.push(`${label}.${name}`);
        return false;
    }
    widget.value = value;
    widget.callback?.(value);
    return true;
}

function effectiveTransition(values) {
    if (!values || typeof values !== "object") return null;
    if (Boolean(values.expert_override)) {
        const continuationMode = String(
            values.expert_continuation_mode ?? values.continuation_mode ?? "");
        const contextLength = Number(
            values.expert_context_length ?? values.context_length);
        if (!continuationMode || !Number.isInteger(contextLength)) return null;
        return {continuationMode, contextLength};
    }
    const preset = transitionPreset(String(values.preset ?? ""));
    return preset ? {
        continuationMode: preset.continuationMode,
        contextLength: preset.contextLength,
    } : null;
}

function restoreCompactAudio(node, policyInputs, applied, unavailable) {
    const audio = policyInputs.audio_policy;
    if (audio && typeof audio === "object") {
        const entries = [
            ["final_audio", audio.final_audio],
            ["source_reference", audio.source_reference],
            ["generated_continuity", audio.generated_continuity],
        ].filter(([name]) => Object.hasOwn(audio, name));
        // The disabled spelling is intentionally omitted from canonical
        // policies. Restore its absence as false so loading an older run also
        // clears a currently enabled compact-policy switch.
        entries.push([
            "lock_source_audio",
            audio.source_audio_target === "locked",
        ]);
        const complete = entries.every(([name, value]) => writeWidget(
            node, name, value, unavailable, "audio_policy"));
        if (complete) applied.push("audio_policy");
    }
}

function restoreProfileAudio(node, policyInputs, applied, unavailable) {
    const audio = policyInputs.audio_policy;
    if (!audio || typeof audio !== "object") return;
    const completeAxes = [
        "final_audio", "source_reference", "generated_continuity",
    ].every((name) => Object.hasOwn(audio, name));
    if (!completeAxes) {
        unavailable.push("audio_policy (incomplete saved audio profile)");
        return;
    }
    const sourceAudioTarget = audio.source_audio_target === "locked"
        ? "locked" : "off";
    const selected = Object.entries(GENERATION_AUDIO_PROFILES).find(
        ([, profile]) => (
            profile.finalAudio === String(audio.final_audio)
            && profile.sourceReference === String(audio.source_reference)
            && profile.generatedContinuity === String(
                audio.generated_continuity)
            && profile.sourceAudioTarget === sourceAudioTarget
        ),
    )?.[0];
    if (!selected) {
        unavailable.push(
            "audio_policy (saved combination needs the Manual Chain Policy)",
        );
        return;
    }
    if (writeWidget(
        node, "audio_profile", selected, unavailable, "audio_policy",
    )) applied.push("audio_policy");
}

function restoreCompactTransition(node, policyInputs, planInputs,
                                  applied, unavailable) {
    const transition = policyInputs.transition_policy;
    if (!transition || typeof transition !== "object") return;
    const effective = effectiveTransition(transition);
    const preset = effective ? transitionPresetName(
        effective.continuationMode, effective.contextLength) : "custom";
    const savedAudioContext = Number(planInputs?.audio_context_length);
    const audioContextMatches = !Number.isInteger(savedAudioContext)
        || savedAudioContext === effective?.contextLength;
    if (!PRIMARY_TRANSITION_PRESETS.includes(preset)
            || !audioContextMatches) {
        unavailable.push(
            "transition_policy (saved boundary needs Advanced Policy or the "
            + "Legacy 0.4 Policy Adapter)",
        );
        return;
    }
    if (writeWidget(
        node, "incoming_transition", preset, unavailable,
        "transition_policy",
    )) applied.push("transition_policy");
}

function restoreProfileTransition(node, policyInputs, planInputs,
                                  applied, unavailable) {
    const transition = policyInputs.transition_policy;
    if (!transition || typeof transition !== "object") return;
    const effective = effectiveTransition(transition);
    const preset = effective ? transitionPresetName(
        effective.continuationMode, effective.contextLength) : "custom";
    const selected = Object.entries(GENERATION_SCENE_PROFILES).find(
        ([, profilePreset]) => profilePreset === preset,
    )?.[0];
    const savedAudioContext = Number(planInputs?.audio_context_length);
    const audioContextMatches = !Number.isInteger(savedAudioContext)
        || savedAudioContext === effective?.contextLength;
    if (!selected || !audioContextMatches) {
        unavailable.push(
            "transition_policy (saved boundary needs Advanced Policy or the "
            + "Legacy 0.4 Policy Adapter)",
        );
        return;
    }
    if (writeWidget(
        node, "scene_continuity", selected, unavailable,
        "transition_policy",
    )) applied.push("transition_policy");
}

function restoreLegacyAudio(node, policyInputs, applied, unavailable) {
    const audio = policyInputs.audio_policy;
    if (audio && typeof audio === "object") {
        if (audio.source_audio_target === "locked") {
            unavailable.push(
                "audio_policy (source target lock needs Generation Profile "
                + "or Manual Chain Policy)");
        } else {
            const completeAxes = [
                "final_audio", "source_reference", "generated_continuity",
            ].every((name) => Object.hasOwn(audio, name));
            if (!completeAxes) {
                unavailable.push(
                    "audio_policy (incomplete saved legacy 0.4 axes)");
            } else {
                const axes = [
                    String(audio.final_audio), String(audio.source_reference),
                    String(audio.generated_continuity),
                ];
                const audioMode = Object.entries(LEGACY_AUDIO_POLICIES).find(
                    ([, value]) => value.every(
                        (item, index) => item === axes[index]),
                )?.[0];
                if (!audioMode) {
                    unavailable.push(
                        "audio_policy (unsupported legacy 0.4 axes)");
                } else if (writeWidget(
                    node, "audio_mode", audioMode, unavailable, "audio_policy",
                )) applied.push("audio_policy");
            }
        }
    }
}

function restoreLegacyTransition(node, policyInputs, planInputs,
                                 applied, unavailable) {
    const transition = policyInputs.transition_policy;
    if (!transition || typeof transition !== "object") return;
    const effective = effectiveTransition(transition);
    if (!effective) {
        unavailable.push("transition_policy (invalid saved boundary)");
        return;
    }
    const audioContext = Number(planInputs?.audio_context_length);
    const resolvedAudioContext = Number.isInteger(audioContext)
        ? audioContext : effective.contextLength;
    const complete = [
        ["continuation_mode", effective.continuationMode],
        ["context_length", effective.contextLength],
        ["audio_context_length", resolvedAudioContext],
    ].every(([name, value]) => writeWidget(
        node, name, value, unavailable, "transition_policy"));
    if (complete) applied.push("transition_policy");
}

function restoreAdvancedTransition(node, policyInputs, planInputs,
                                   applied, unavailable) {
    const transition = policyInputs.transition_policy;
    if (!transition || typeof transition !== "object") return;
    const effective = effectiveTransition(transition);
    if (!effective) {
        unavailable.push("transition_policy (invalid saved boundary)");
        return;
    }
    const preset = transitionPresetName(
        effective.continuationMode, effective.contextLength);
    const savedAudioContext = Number(planInputs?.audio_context_length);
    const audioContextMatches = !Number.isInteger(savedAudioContext)
        || savedAudioContext === effective.contextLength;
    if (!ADVANCED_TRANSITION_PRESETS.includes(preset)
            || !audioContextMatches) {
        unavailable.push(
            "transition_policy (saved raw boundary needs the Legacy 0.4 "
            + "Policy Adapter as the final policy layer)",
        );
        return;
    }
    if (writeWidget(
        node, "incoming_transition", preset, unavailable,
        "transition_policy",
    )) applied.push("transition_policy");
}

/** Restore normalized policy records onto the one-wire policy node already
 * connected to Plan. Connections are never replaced or invented. */
export function restoreConnectedPolicyInputs(
    planNode, policyInputs, planInputs = {},
) {
    const applied = [];
    const unavailable = [];
    if (!policyInputs || typeof policyInputs !== "object") {
        return {applied, unavailable};
    }
    const graph = planNode?.graph;
    graph?.beforeChange?.();
    try {
        const combinedOrigin = linkedInputOrigin(planNode, "chain_policy");
        const profile = findUpstreamType(
            combinedOrigin, PROFILE_POLICY_NODE);
        const compact = findUpstreamType(combinedOrigin, CHAIN_POLICY_NODE);
        const legacy = findUpstreamType(combinedOrigin, LEGACY_POLICY_NODE);
        if (profile) {
            restoreProfileAudio(
                profile, policyInputs, applied, unavailable);
            profile.graph?.setDirtyCanvas?.(true, true);
        } else if (compact) {
            restoreCompactAudio(
                compact, policyInputs, applied, unavailable);
            compact.graph?.setDirtyCanvas?.(true, true);
        } else if (legacy) {
            restoreLegacyAudio(legacy, policyInputs, applied, unavailable);
            legacy.graph?.setDirtyCanvas?.(true, true);
        }

        const transitionTarget = findFirstUpstreamType(
            combinedOrigin,
            new Set([
                ADVANCED_POLICY_NODE, LEGACY_POLICY_NODE,
                PROFILE_POLICY_NODE, CHAIN_POLICY_NODE,
            ]),
        );
        if (nodeType(transitionTarget) === ADVANCED_POLICY_NODE) {
            restoreAdvancedTransition(
                transitionTarget, policyInputs, planInputs,
                applied, unavailable);
        } else if (nodeType(transitionTarget) === LEGACY_POLICY_NODE) {
            restoreLegacyTransition(
                transitionTarget, policyInputs, planInputs,
                applied, unavailable);
        } else if (nodeType(transitionTarget) === PROFILE_POLICY_NODE) {
            restoreProfileTransition(
                transitionTarget, policyInputs, planInputs,
                applied, unavailable);
        } else if (nodeType(transitionTarget) === CHAIN_POLICY_NODE) {
            restoreCompactTransition(
                transitionTarget, policyInputs, planInputs,
                applied, unavailable);
        } else {
            unavailable.push(
                "chain_policy (connect MiniMax H3 Generation Profile, "
                + "Manual Chain Policy, Advanced Policy, or the Legacy 0.4 "
                + "Policy Adapter)",
            );
        }
    } finally {
        graph?.afterChange?.();
    }
    return {applied, unavailable};
}

/** Force every authoring surface bound to the restored Plan to reparse it.
 * This avoids waiting for polling and prevents a prompt editor from continuing
 * to display a stale pre-restore scene prompt. */
export function refreshRestoredPlanEditors(planNode) {
    planNode?._h3ChainEditorRefresh?.();
    const graph = planNode?.graph?.rootGraph ?? planNode?.graph;
    for (const node of allNodes(graph)) {
        if (node === planNode) continue;
        node._h3ScenePromptEditorRefresh?.();
        node._h3RichPromptRefresh?.();
        node._h3PlanStudioRefresh?.();
    }
    planNode?.graph?.setDirtyCanvas?.(true, true);
    graph?.setDirtyCanvas?.(true, true);
}
