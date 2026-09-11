#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {parsePlanJson, planToJson} from "../web/h3_chain_plan_core.mjs";
import {
    activeSceneFromOutput,
    applySceneReroll,
    resumeSelection,
} from "../web/h3_chain_cancel_reroll_core.mjs";

const plan = {
    shots: [
        {id: "one", prompt: ["One."]},
        {id: "two", prompt: ["Two."], seed: "2"},
        {id: "three", prompt: ["Three."]},
    ],
};
applySceneReroll(plan, 2, "18446744073709551615");
assert.equal(plan.shots[1].seed, "18446744073709551615");
assert.throws(() => applySceneReroll(plan, 4, "1"), /does not exist/);
assert.throws(
    () => applySceneReroll(plan, 1, "18446744073709551616"),
    /unsigned 64-bit/,
);

assert.deepEqual(resumeSelection(2, 3, 3), {
    startClip: 2,
    sceneRange: "",
});
assert.deepEqual(resumeSelection(3, 5, 8), {
    startClip: 3,
    sceneRange: "3:5",
});
assert.deepEqual(resumeSelection(5, 5, 8), {
    startClip: 5,
    sceneRange: "5",
});
assert.throws(() => resumeSelection(6, 5, 8), /Invalid reroll range/);

assert.deepEqual(activeSceneFromOutput({
    h3_chain_active_scene: [{
        run_name: "project",
        clip_index: 2,
        clip_count: 8,
        end_clip: 5,
        shot_id: "hallway",
        seed: "9007199254740993",
    }],
}), {
    runName: "project",
    branchId: "main",
    clipIndex: 2,
    clipCount: 8,
    endClip: 5,
    shotId: "hallway",
    seed: "9007199254740993",
    workflowFingerprint: "",
});
assert.equal(activeSceneFromOutput({h3_chain_active_scene: []}), null);

const branchId = "e".repeat(32);
const sceneOutput = {
    h3_chain_active_scene: [{
        run_name: "project", _branch_id: branchId,
        clip_index: 15, clip_count: 20, end_clip: 20,
        shot_id: "scene_15", seed: "9007199254740993",
    }],
};
const branchScene = activeSceneFromOutput(sceneOutput);
assert.equal(branchScene.branchId, branchId, "keep the execution-stamped branch");
assert.equal(activeSceneFromOutput({h3_chain_active_scene: [{
    ...sceneOutput.h3_chain_active_scene[0], _branch_id: "invalid",
}]}), null, "invalid branch events must not offer a reroll");

const source = fs.readFileSync(
    new URL("../web/h3_chain_cancel_reroll.js", import.meta.url),
    "utf8",
);
assert.match(source, /refreshRestoredPlanEditors\(planNode\)/,
    "reroll seed changes refresh every Plan companion");
assert.match(source, /\/api\/jobs\/\$\{encodeURIComponent\(record\.promptId\)\}\/cancel/);
assert.match(source, /execution_interrupted/);
assert.match(source, /await waiter\.promise/);
assert.match(source, /requireVisibleWorkflow\(record\);[\s\S]*verifyPredecessorCheckpoint/);
assert.match(source, /function activeWorkflowIdentity\(\)/);
assert.match(source, /workflowIdentity !== record\.workflowIdentity/);
assert.match(source, /widgetByName\(planNode, "run_name"\)/);
assert.match(source, /record\.currentNode = currentNode/);
assert.doesNotMatch(source, /currentNode !== record\.currentNode/);
assert.match(source, /active = null;[\s\S]*Waiting for ComfyUI to finish interrupting/);
assert.match(source, /applySceneReroll/);
assert.match(source, /resumeSelection/);
assert.match(source, /await app\.queuePrompt\(0, 1\)/);
assert.match(source, /checkpoint \$\{predecessor\} is not ready/);
assert.match(source, /queue the workflow manually/);
assert.match(source, /MiniMaxH3ContexLoop\.cancelRerollControl/);
assert.match(source, /Show floating Cancel & reroll control/);
assert.match(source, /defaultValue:\s*true/);
assert.match(source, /onChange\(value\)[\s\S]*setControlAllowed\(value\)/);
assert.match(source, /if \(!controlAllowed \|\| !active \|\| busy\) return/);
assert.match(source, /root\.hidden = !controlAllowed \|\| !wantsVisible/);
assert.match(source, /\.h3cr-status[\s\S]*max-width:100%[\s\S]*overflow-wrap:anywhere/);
assert.doesNotMatch(source, /fetchApi\("\/interrupt"/);

// Run the actual frontend handlers, with no browser, server, or live queue.
function rerollFixture(selectedBranch = branchId, scene = branchScene) {
    const planWidget = {name: "plan_json", value: JSON.stringify({
        _branch_id: selectedBranch,
        shots: Array.from({length: 20}, (_, index) => ({
            id: `scene_${index + 1}`, prompt: `Prompt ${index + 1}`, seed: "1",
        })),
    })};
    const planNode = {id: 1, type: "MiniMaxH3ChainPlanModern", widgets: [
        {name: "run_name", value: "project"}, planWidget,
    ]};
    const startNode = {id: 2, type: "MiniMaxH3ChainLoopStart", inputs: [{link: 1}],
        widgets: [{name: "start_clip", value: 1}, {name: "scene_range", value: ""}]};
    const currentNode = {id: 3, type: "MiniMaxH3ChainCurrent", inputs: [{link: 2}]};
    const nodes = [planNode, startNode, currentNode];
    const graph = {links: {1: {origin_id: 1}, 2: {origin_id: 2}},
        getNodeById: (id) => nodes.find((node) => node.id === id)};
    for (const node of nodes) node.graph = graph;
    const requests = [];
    const api = {async fetchApi(url) {
        requests.push(url);
        const query = new URL(url, "http://test.invalid").searchParams;
        // Original has no scene 14; the named branch does.
        return {ok: true, async json() {
            return {checkpoints: query.get("branch_id") === branchId
                ? [{scene: 14, ready: true}] : []};
        }};
    }};
    const context = vm.createContext({
        app: {graph, registerExtension() {}}, api, URLSearchParams,
        parsePlanJson, planToJson, applySceneReroll, resumeSelection,
        activeSceneFromOutput, refreshRestoredPlanEditors() {},
        randomSceneSeed() { throw new Error("Unexpected seed mutation"); },
    });
    vm.runInContext(source.replace(/^import[\s\S]*?from "[^"]+";\n/gm, ""), context);
    vm.runInContext(`
        root = {hidden: false}; actionButton = {disabled: false};
        status = {className: "", textContent: ""};
    `, context);
    const record = {scene, displayNode: "3", promptId: "running-prompt"};
    return {context, api, record, requests, planWidget, startNode};
}

const fixture = rerollFixture();
await fixture.context.verifyPredecessorCheckpoint(fixture.record);
const query = new URL(fixture.requests[0], "http://test.invalid").searchParams;
assert.equal(query.get("branch_id"), branchId);
assert.equal(query.get("include_graph"), "false", "read only the resume inventory");
assert.equal(query.get("run_name"), "project");

fixture.context.updateWorkflowForReroll(fixture.record, "18446744073709551615");
const rerolled = parsePlanJson(fixture.planWidget.value);
assert.equal(rerolled._branch_id, branchId);
assert.equal(rerolled.shots[14].seed, "18446744073709551615");
assert.equal(rerolled.shots[13].seed, "1");
assert.deepEqual(rerolled.shots[14].prompt, ["Prompt 15"]);
assert.equal(fixture.startNode.widgets[0].value, 15);

// Do not borrow a ready checkpoint from Original if the running branch lacks it.
fixture.api.fetchApi = async () => ({ok: true, async json() {
    return {checkpoints: [{scene: 14, ready: false}]};
}});
await assert.rejects(fixture.context.verifyPredecessorCheckpoint(fixture.record),
    /checkpoint 14 is not ready/);

// A branch switch is rejected before cancellation, and again before editing
// after an interruption has completed. Neither Plan nor Loop Start is changed.
const switched = rerollFixture("main");
const before = switched.planWidget.value;
switched.context.record = switched.record;
await vm.runInContext("active = record; cancelAndReroll();", switched.context);
assert.match(vm.runInContext("status.textContent", switched.context), /branch/i);
assert.deepEqual(switched.requests, [], "do not cancel or fetch another branch");
assert.throws(() => switched.context.updateWorkflowForReroll(switched.record, "2"), /branch/i);
assert.equal(switched.planWidget.value, before);
assert.equal(switched.startNode.widgets[0].value, 1);

const switchedDuringFetch = rerollFixture();
const fetchCheckpoints = switchedDuringFetch.api.fetchApi;
switchedDuringFetch.api.fetchApi = async (url) => {
    const response = await fetchCheckpoints(url);
    const plan = parsePlanJson(switchedDuringFetch.planWidget.value);
    delete plan._branch_id;
    switchedDuringFetch.planWidget.value = planToJson(plan);
    return response;
};
switchedDuringFetch.context.record = switchedDuringFetch.record;
await vm.runInContext("active = record; cancelAndReroll();", switchedDuringFetch.context);
assert.match(vm.runInContext("status.textContent", switchedDuringFetch.context), /branch/i);
assert.equal(switchedDuringFetch.requests.length, 1, "do not cancel after switching during the checkpoint request");

const originalScene = activeSceneFromOutput({h3_chain_active_scene: [{
    ...sceneOutput.h3_chain_active_scene[0], _branch_id: undefined,
}]});
const original = rerollFixture("main", originalScene);
original.context.requireVisibleWorkflow(original.record);
await assert.rejects(original.context.verifyPredecessorCheckpoint(original.record), /not ready/);
assert.equal(new URL(original.requests[0], "http://test.invalid").searchParams.get("branch_id"), "main");
await original.context.verifyPredecessorCheckpoint({scene: {...originalScene, clipIndex: 1}});
assert.equal(original.requests.length, 1, "scene 1 needs no predecessor");

console.log("H3 cancel-and-reroll helpers: ok");
