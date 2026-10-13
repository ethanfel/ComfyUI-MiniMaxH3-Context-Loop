#!/usr/bin/env node

import assert from "node:assert/strict";
import {
    branchPolicyNodes,
    captureBranchPolicyInputs,
    restoreBranchPolicyInputs,
    refreshRestoredPlanEditors,
    restoreConnectedPolicyInputs,
} from "../web/h3_plan_restore_core.mjs";
import {branchWidgetTransaction} from "../web/h3_working_branches.mjs";
import {captureProjectPlan} from "../web/h3_project_plan_switch.mjs";

// Old Plans retain hidden 0.4 defaults. Connected policy nodes override them
// at runtime, including when the Plan itself has never executed.
for (const planType of ["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern", "MiniMaxH3ChainPlanStudio"]) {
    for (const [type, settings, mode, context, audioContext] of [
        ["MiniMaxH3GenerationProfile", {scene_continuity:"Hard picture + smooth audio", audio_profile:"Generate audio"}, "audio_feathered_av", 39, 39],
        ["MiniMaxH3ChainPolicy", {incoming_transition:"hard_av"}, "masked_av", 39, 39],
        ["MiniMaxH3AdvancedPolicy", {incoming_transition:"drift_av"}, "drift_control_av", 39, 39],
        ["MiniMaxH3Legacy04PolicyAdapter", {continuation_mode:"guide", context_length:17, audio_context_length:22}, "guide", 17, 22],
        ["MiniMaxH3Legacy04PolicyAdapter", {continuation_mode:"masked_av", context_length:39, audio_context_length:17}, "masked_av", 39, 17],
        ["MiniMaxH3GenerationProfile", {scene_continuity:"Independent scenes", audio_profile:"Generate audio"}, "guide", 0, 0],
    ]) {
        const policy = {id:2, type, inputs:[], widgets:Object.entries(settings).map(([name,value])=>widget(name,value))};
        const plan = {id:1, type:planType, inputs:[{name:"chain_policy",link:1}], widgets:[
            widget("plan_json", JSON.stringify({shots:[{id:"one",prompt:"Keep this prompt"}]})),
            widget("context_length",22), widget("continuation_mode","guide"), widget("audio_context_length",22),
        ]};
        const graph = {links:{1:{origin_id:2}},getNodeById:id=>id===2?policy:plan};
        plan.graph=policy.graph=graph;
        const original = plan.widgets.map(w=>w.value);
        const saved = captureProjectPlan(plan);
        assert.equal(saved.context_length, context);
        assert.equal(saved.continuation_mode, mode);
        assert.equal(saved.audio_context_length, audioContext);
        assert.equal(saved.policy_inputs.audio_context_length, audioContext);
        assert.deepEqual(plan.widgets.map(w=>w.value), original, "capture is read-only");
        const expected = policy.widgets.map(w=>w.value);
        restoreBranchPolicyInputs(plan, saved);
        assert.deepEqual(policy.widgets.map(w=>w.value), expected, `${planType}/${type} round trip`);
        // Runtime archive policy also wins over a stale widget retained in an
        // old workflow/API archive. It must not mutate the saved record.
        const archived = {...saved, audio_context_length:99};
        restoreBranchPolicyInputs(plan, archived);
        assert.deepEqual(policy.widgets.map(w=>w.value), expected);
        assert.equal(archived.audio_context_length,99);
    }
}

{
    const policy = {id:2, type:"MiniMaxH3ChainPolicy", inputs:[], widgets:[
        widget("incoming_transition", "soft_av"), widget("final_audio", "generated"),
        widget("source_reference", "off"), widget("generated_continuity", "on"),
        widget("lock_source_audio", false),
    ]};
    const plan = {id:1,type:"MiniMaxH3ChainPlanModern",inputs:[{name:"chain_policy",link:1}],widgets:[]};
    const graph = {links:{1:{origin_id:2}},getNodeById:id=>id===2?policy:plan};
    plan.graph=policy.graph=graph;
    const saved = {audio_context_length:39,policy_inputs:captureBranchPolicyInputs(plan)};
    assert.equal(saved.policy_inputs.transition_policy.expert_context_length,39);
    policy.widgets[0].value="guide";
    policy.widgets[1].value="source";
    branchWidgetTransaction(branchPolicyNodes(plan),()=>restoreBranchPolicyInputs(plan,saved));
    assert.deepEqual(captureBranchPolicyInputs(plan),saved.policy_inputs);
    const before = policy.widgets.map(w=>w.value);
    const unsupported = structuredClone(saved);
    unsupported.policy_inputs.audio_policy.final_audio="source";
    unsupported.policy_inputs.transition_policy={expert_override:true,expert_continuation_mode:"guide",expert_context_length:73};
    assert.throws(()=>branchWidgetTransaction(branchPolicyNodes(plan),()=>restoreBranchPolicyInputs(plan,unsupported)),/Cannot restore/);
    assert.deepEqual(policy.widgets.map(w=>w.value),before,"failed branch policy application must roll back all widgets");
}

function widget(name, value) {
    return {
        name,
        value,
        callback(next) { this.callbackValue = next; },
    };
}

const graph = {
    links: {
        10: {origin_id: 3, target_id: 1},
        12: {origin_id: 2, target_id: 3},
    },
    _nodes: [],
    beforeCount: 0,
    afterCount: 0,
    dirtyCount: 0,
    getNodeById(id) { return this._nodes.find((node) => node.id === id); },
    beforeChange() { this.beforeCount += 1; },
    afterChange() { this.afterCount += 1; },
    setDirtyCanvas() { this.dirtyCount += 1; },
};
const plan = {
    id: 1,
    type: "MiniMaxH3ChainPlan",
    graph,
    inputs: [{name: "chain_policy", link: 10}],
    widgets: [],
    _h3ChainEditorRefresh() { this.refreshCount = (this.refreshCount ?? 0) + 1; },
};
const audio = {
    id: 2,
    type: "MiniMaxH3ChainPolicy",
    graph,
    inputs: [],
    widgets: [
        widget("incoming_transition", "guide"),
        widget("final_audio", "generated"),
        widget("source_reference", "off"),
        widget("generated_continuity", "on"),
        widget("lock_source_audio", false),
    ],
};
const reroute = {
    id: 3,
    type: "Reroute",
    graph,
    inputs: [{name: "", link: 12}],
    widgets: [],
};
const sceneEditor = {
    id: 5,
    type: "MiniMaxH3ChainScenePromptEditor",
    graph,
    _h3ScenePromptEditorRefresh() { this.refreshCount = (this.refreshCount ?? 0) + 1; },
};
const richEditor = {
    id: 6,
    type: "MiniMaxH3ChainRichScenePromptEditor",
    graph,
    _h3RichPromptRefresh() { this.refreshCount = (this.refreshCount ?? 0) + 1; },
};
const studio = {
    id: 7,
    type: "MiniMaxH3ChainPlanStudio",
    graph,
    _h3PlanStudioRefresh() { this.refreshCount = (this.refreshCount ?? 0) + 1; },
};
graph._nodes.push(plan, audio, reroute, sceneEditor, richEditor, studio);

const result = restoreConnectedPolicyInputs(plan, {
    audio_policy: {
        final_audio: "source",
        source_reference: "on",
        generated_continuity: "off",
    },
    transition_policy: {
        preset: "soft_av", expert_override: false,
        expert_continuation_mode: "audio_feathered_av",
        expert_context_length: 39,
    },
}, {audio_context_length: 39});
assert.deepEqual(result, {
    applied: ["audio_policy", "transition_policy"],
    unavailable: [],
});
assert.deepEqual(audio.widgets.map((item) => item.value), [
    "soft_av", "source", "on", "off", false,
]);
assert.ok(audio.widgets.every((item) => item.callbackValue === item.value));
assert.equal(graph.beforeCount, 1);
assert.equal(graph.afterCount, 1);

refreshRestoredPlanEditors(plan);
assert.equal(plan.refreshCount, 1);
assert.equal(sceneEditor.refreshCount, 1);
assert.equal(richEditor.refreshCount, 1);
assert.equal(studio.refreshCount, 1);

// Assignment to a standalone Studio can leave Plan JSON unchanged (same
// prompts/settings, different checkpoint aliases). Do not rely on polling:
// checkpoint polling is suspended during generation.
refreshRestoredPlanEditors(studio);
assert.equal(studio.refreshCount, 2, "refresh the owner Studio exactly once");
assert.equal(sceneEditor.refreshCount, 2);
assert.equal(richEditor.refreshCount, 2);

plan.inputs[0].link = null;
const missing = restoreConnectedPolicyInputs(plan, {
    audio_policy: {final_audio: "generated"},
});
assert.deepEqual(missing.applied, []);
assert.match(missing.unavailable[0], /chain_policy.*connect/);

const compactGraph = {
    links: {
        20: {origin_id: 9, target_id: 8},
    },
    _nodes: [],
    beforeChange() {}, afterChange() {}, setDirtyCanvas() {},
    getNodeById(id) { return this._nodes.find((node) => node.id === id); },
};
const compactPlan = {
    id: 8, type: "MiniMaxH3ChainPlan", graph: compactGraph,
    inputs: [{name: "chain_policy", link: 20}], widgets: [],
};
const compactPolicy = {
    id: 9, type: "MiniMaxH3ChainPolicy", graph: compactGraph, inputs: [],
    widgets: [
        widget("incoming_transition", "guide"),
        widget("final_audio", "generated"),
        widget("source_reference", "off"),
        widget("generated_continuity", "on"),
        widget("lock_source_audio", false),
    ],
};
compactGraph._nodes.push(compactPlan, compactPolicy);
const compactResult = restoreConnectedPolicyInputs(compactPlan, {
    audio_policy: {
        final_audio: "source", source_reference: "on",
        generated_continuity: "off",
    },
    transition_policy: {
        preset: "hard_av", expert_override: false,
        expert_continuation_mode: "masked_av", expert_context_length: 39,
    },
}, {audio_context_length: 39});
assert.deepEqual(compactResult, {
    applied: ["audio_policy", "transition_policy"], unavailable: [],
});
assert.deepEqual(compactPolicy.widgets.map((item) => item.value), [
    "hard_av", "source", "on", "off", false,
]);
const compactLocked = restoreConnectedPolicyInputs(compactPlan, {
    audio_policy: {
        final_audio: "source", source_reference: "off",
        generated_continuity: "off", source_audio_target: "locked",
    },
});
assert.deepEqual(compactLocked, {
    applied: ["audio_policy"], unavailable: [],
});
assert.deepEqual(compactPolicy.widgets.map((item) => item.value), [
    "hard_av", "source", "off", "off", true,
]);
const compactMismatch = restoreConnectedPolicyInputs(compactPlan, {
    transition_policy: {
        preset: "hard_av", expert_override: false,
        expert_continuation_mode: "masked_av", expert_context_length: 39,
    },
}, {audio_context_length: 22});
assert.deepEqual(compactMismatch.applied, []);
assert.match(compactMismatch.unavailable[0], /Advanced Policy.*Legacy 0\.4/);
assert.match(compactMismatch.unavailable[0], /context_length=39, audio_context_length=22/);
assert.match(compactMismatch.unavailable[0], /require audio_context_length=39/);
// Explicit mixed contexts are still rejected, even if the raw Plan fallback
// happens to match the destination preset.
const explicitMismatch = restoreConnectedPolicyInputs(compactPlan, {
    audio_context_length:17,
    transition_policy:{preset:"hard_av",expert_override:false},
}, {audio_context_length:39});
assert.deepEqual(explicitMismatch.applied,[]);
assert.match(explicitMismatch.unavailable[0],/audio_context_length=17/);
for (const invalid of [null, "39", -1, 1.5, true]) {
    const before = compactPolicy.widgets.map(w=>w.value);
    const result = restoreConnectedPolicyInputs(compactPlan, {
        audio_context_length:invalid, transition_policy:{preset:"guide"},
    });
    assert.match(result.unavailable[0], /invalid saved effective policy/);
    assert.deepEqual(compactPolicy.widgets.map(w=>w.value),before);
}

const profileGraph = {
    links: {60: {origin_id: 17, target_id: 16}},
    _nodes: [],
    beforeChange() {}, afterChange() {}, setDirtyCanvas() {},
    getNodeById(id) { return this._nodes.find((node) => node.id === id); },
};
const profilePlan = {
    id: 16, type: "MiniMaxH3ChainPlan", graph: profileGraph,
    inputs: [{name: "chain_policy", link: 60}], widgets: [],
};
const generationProfile = {
    id: 17, type: "MiniMaxH3GenerationProfile", graph: profileGraph,
    inputs: [],
    widgets: [
        widget("scene_continuity", "Visual continuity"),
        widget("audio_profile", "Generate audio"),
    ],
};
profileGraph._nodes.push(profilePlan, generationProfile);
const profileResult = restoreConnectedPolicyInputs(profilePlan, {
    audio_policy: {
        final_audio: "source", source_reference: "off",
        generated_continuity: "off", source_audio_target: "locked",
    },
    transition_policy: {
        preset: "soft_av", expert_override: false,
        expert_continuation_mode: "audio_feathered_av",
        expert_context_length: 39,
    },
}, {audio_context_length: 39});
assert.deepEqual(profileResult, {
    applied: ["audio_policy", "transition_policy"], unavailable: [],
});
assert.deepEqual(generationProfile.widgets.map((item) => item.value), [
    "Hard picture + smooth audio", "Lip-sync to source audio",
]);
const unsupportedProfile = restoreConnectedPolicyInputs(profilePlan, {
    audio_policy: {
        final_audio: "source", source_reference: "on",
        generated_continuity: "on",
    },
});
assert.deepEqual(unsupportedProfile.applied, []);
assert.match(unsupportedProfile.unavailable[0], /Manual Chain Policy/);

compactPolicy.type = "MiniMaxH3Legacy04PolicyAdapter";
compactPolicy.widgets = [
    widget("audio_mode", "generated_audio"),
    widget("continuation_mode", "guide"),
    widget("context_length", 22),
    widget("audio_context_length", 22),
];
const legacyResult = restoreConnectedPolicyInputs(compactPlan, {
    audio_policy: {
        final_audio: "source", source_reference: "on",
        generated_continuity: "on",
    },
    transition_policy: {
        preset: "guide", expert_override: true,
        expert_continuation_mode: "drift_control_av",
        expert_context_length: 39,
    },
}, {audio_context_length: 17});
assert.deepEqual(legacyResult, {
    applied: ["audio_policy", "transition_policy"], unavailable: [],
});
assert.deepEqual(compactPolicy.widgets.map((item) => item.value), [
    "source_plus_timeline", "drift_control_av", 39, 17,
]);

const layeredGraph = {
    links: {
        30: {origin_id: 12, target_id: 10},
        31: {origin_id: 11, target_id: 12},
    },
    _nodes: [],
    beforeChange() {}, afterChange() {}, setDirtyCanvas() {},
    getNodeById(id) { return this._nodes.find((node) => node.id === id); },
};
const layeredPlan = {
    id: 10, type: "MiniMaxH3ChainPlan", graph: layeredGraph,
    inputs: [{name: "chain_policy", link: 30}], widgets: [],
};
const layeredBase = {
    id: 11, type: "MiniMaxH3ChainPolicy", graph: layeredGraph, inputs: [],
    widgets: [
        widget("incoming_transition", "guide"),
        widget("final_audio", "generated"),
        widget("source_reference", "off"),
        widget("generated_continuity", "off"),
        widget("lock_source_audio", false),
    ],
};
const layeredAdvanced = {
    id: 12, type: "MiniMaxH3AdvancedPolicy", graph: layeredGraph,
    inputs: [{name: "chain_policy", link: 31}],
    widgets: [widget("incoming_transition", "detail_av")],
};
layeredGraph._nodes.push(layeredPlan, layeredBase, layeredAdvanced);
const layeredResult = restoreConnectedPolicyInputs(layeredPlan, {
    audio_policy: {
        final_audio: "source", source_reference: "on",
        generated_continuity: "on",
    },
    transition_policy: {
        preset: "drift_av", expert_override: false,
        expert_continuation_mode: "drift_control_av",
        expert_context_length: 39,
    },
}, {audio_context_length: 39});
assert.deepEqual(layeredResult, {
    applied: ["audio_policy", "transition_policy"], unavailable: [],
});
assert.deepEqual(layeredBase.widgets.map((item) => item.value), [
    "guide", "source", "on", "on", false,
]);
assert.equal(layeredAdvanced.widgets[0].value, "drift_av");

const rawLayered = restoreConnectedPolicyInputs(layeredPlan, {
    transition_policy: {
        preset: "guide", expert_override: true,
        expert_continuation_mode: "feathered_av",
        expert_context_length: 39,
    },
}, {audio_context_length: 17});
assert.deepEqual(rawLayered.applied, []);
assert.match(rawLayered.unavailable[0], /Legacy 0\.4 Policy Adapter/);

const legacyLayerGraph = {
    links: {
        50: {origin_id: 15, target_id: 13},
        51: {origin_id: 14, target_id: 15},
    },
    _nodes: [],
    beforeChange() {}, afterChange() {}, setDirtyCanvas() {},
    getNodeById(id) { return this._nodes.find((node) => node.id === id); },
};
const legacyLayerPlan = {
    id: 13, type: "MiniMaxH3ChainPlan", graph: legacyLayerGraph,
    inputs: [{name: "chain_policy", link: 50}], widgets: [],
};
const legacyLayerBase = {
    id: 14, type: "MiniMaxH3ChainPolicy", graph: legacyLayerGraph, inputs: [],
    widgets: [
        widget("incoming_transition", "guide"),
        widget("final_audio", "generated"),
        widget("source_reference", "off"),
        widget("generated_continuity", "off"),
        widget("lock_source_audio", false),
    ],
};
const legacyLayer = {
    id: 15, type: "MiniMaxH3Legacy04PolicyAdapter", graph: legacyLayerGraph,
    inputs: [{name: "chain_policy", link: 51}],
    widgets: [
        widget("audio_mode", "generated_audio"),
        widget("continuation_mode", "guide"),
        widget("context_length", 22),
        widget("audio_context_length", 22),
    ],
};
legacyLayerGraph._nodes.push(
    legacyLayerPlan, legacyLayerBase, legacyLayer);
const legacyLayerResult = restoreConnectedPolicyInputs(legacyLayerPlan, {
    audio_policy: {
        final_audio: "source", source_reference: "on",
        generated_continuity: "on",
    },
    transition_policy: {
        preset: "guide", expert_override: true,
        continuation_mode: "feathered_av", context_length: 39,
    },
}, {audio_context_length: 17});
assert.deepEqual(legacyLayerResult, {
    applied: ["audio_policy", "transition_policy"], unavailable: [],
});
assert.deepEqual(legacyLayerBase.widgets.map((item) => item.value), [
    "guide", "source", "on", "on", false,
]);
assert.deepEqual(legacyLayer.widgets.map((item) => item.value), [
    "generated_audio", "feathered_av", 39, 17,
]);

console.log("H3 Plan restore: compact, composable advanced, 0.4 legacy, and prompt refresh pass");
