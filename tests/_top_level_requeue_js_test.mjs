#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {submitWithPromptIdentity, submissionFailure, createContinuationTracker} from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import * as coordinator from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import * as requeueCore from "../web/h3_chain_top_level_requeue_core.mjs";
import {
    DEFAULT_CLEANUP_DELAY_MS,
    EXECUTION_MODES,
    HANDOFF_API_BASE,
    LEGACY_MODE,
    RECURSIVE_MODE,
    migrateRecursiveExecutionMode,
    REQUEUE_MODE,
    checkpointPredecessorReady,
    cleanupDelayMs,
    executionModeFromValue,
    isQueueSafe,
    isRequeueMode,
    matchingNextSceneHandoff,
    pendingNextSceneHandoffs,
    predecessorScene,
    resumeHint,
    shouldScheduleTopLevelRequeueSuccess,
    handleTopLevelRequeueSuccessScheduling,
    loopEndMatchesObservedCurrent,
    topLevelRequeueCompletionMatches,
} from "../web/h3_chain_top_level_requeue_core.mjs";

assert.equal(shouldScheduleTopLevelRequeueSuccess({runName:"run",loopEndExecuted:true,executionMode:REQUEUE_MODE,clipIndex:1,endClip:2}),true);
assert.equal(shouldScheduleTopLevelRequeueSuccess({runName:"run",loopEndExecuted:false,executionMode:REQUEUE_MODE,clipIndex:1,endClip:2}),false);
assert.equal(shouldScheduleTopLevelRequeueSuccess({runName:"run",loopEndExecuted:true,executionMode:LEGACY_MODE,clipIndex:1,endClip:2}),false);
const currentA={}, currentB={};
assert.equal(loopEndMatchesObservedCurrent({record:{displayNode:"a"},loopEndNode:{},resolveDisplayNode:id=>id==="a"?currentA:null,findUpstreamCurrent:()=>currentA}),true);
assert.equal(loopEndMatchesObservedCurrent({record:{displayNode:"a"},loopEndNode:{},resolveDisplayNode:()=>currentA,findUpstreamCurrent:()=>currentB}),false);
assert.equal(loopEndMatchesObservedCurrent({record:{},loopEndNode:{},resolveDisplayNode:()=>currentA,findUpstreamCurrent:()=>currentA}),false);
const completionRecord={runName:"run-a",clipIndex:1,endClip:2,workflowFingerprint:"fp-a"};
const completionPayload={run_name:"run-a",predecessor_scene:1,scene:2,end_clip:2,workflow_fingerprint:"fp-a",handoff_id:"handoff-a"};
assert.equal(topLevelRequeueCompletionMatches(completionRecord,completionPayload),true);
for (const invalid of [
    {...completionPayload,run_name:"other"}, {...completionPayload,predecessor_scene:2},
    {...completionPayload,scene:3}, {...completionPayload,end_clip:3},
    {...completionPayload,workflow_fingerprint:"other"}, {...completionPayload,handoff_id:""},
]) assert.equal(topLevelRequeueCompletionMatches(completionRecord,invalid),false);
assert.equal(topLevelRequeueCompletionMatches(completionRecord,null),false);
let scheduled=0;
assert.equal(handleTopLevelRequeueSuccessScheduling({record:{runName:"run",loopEndExecuted:false,executionMode:REQUEUE_MODE,clipIndex:1,endClip:2},scheduleRequeue:()=>scheduled++}),false); assert.equal(scheduled,0);
assert.equal(handleTopLevelRequeueSuccessScheduling({record:{runName:"run",loopEndExecuted:true,executionMode:REQUEUE_MODE,clipIndex:1,endClip:2},scheduleRequeue:()=>scheduled++}),true); assert.equal(scheduled,1);

// --- behavioral delivery primitive -----------------------------------------
assert.deepEqual(await submitWithPromptIdentity({app: {graph: {}, graphToPrompt: async () => ({x: 1})}, api: {queuePrompt: async () => ({prompt_id: "accepted-123"})}}), {kind: "accepted", promptId: "accepted-123"});
const hookOrder=[]; const hookWidget={value:1,beforeQueued:()=>{hookOrder.push("hook");hookWidget.value=2}};
assert.deepEqual(await submitWithPromptIdentity({app:{graph:{nodes:[{widgets:[hookWidget]}]},graphToPrompt:async()=>{hookOrder.push(`serialize-${hookWidget.value}`);return {} }},api:{queuePrompt:async()=>{hookOrder.push("queue");return {prompt_id:"beforequeued-123"}}}}),{kind:"accepted",promptId:"beforequeued-123"}); assert.deepEqual(hookOrder,["hook","serialize-2","queue"]);
assert.deepEqual(await submitWithPromptIdentity({app: {graph: {}, graphToPrompt: async () => ({x: 1})}, api: {queuePrompt: async () => ({})}}), {kind: "uncertain", promptId: ""});
assert.deepEqual(await submitWithPromptIdentity({app: {queuePrompt: async () => false}, api: {}}), {kind: "rejected", promptId: ""});
assert.deepEqual(await submitWithPromptIdentity({app: {queuePrompt: async () => true}, api: {}}), {kind: "uncertain", promptId: ""});
assert.equal(submissionFailure({status: 422}), "rejected");
assert.equal(submissionFailure(new Error("network")), "uncertain");
const transitions = [];
const tracker = createContinuationTracker({transition: async (...args) => transitions.push(args)});
tracker.track("run", "handoff", "accepted-123");
assert.equal(await tracker.started("other"), false);
assert.deepEqual(transitions, []);
assert.equal(await tracker.started("accepted-123"), true);
assert.deepEqual(transitions, [["run", "handoff", "consumed"]]);

// --- mode contract ---------------------------------------------------------

assert.deepEqual(EXECUTION_MODES, [LEGACY_MODE, REQUEUE_MODE]);
assert.equal(EXECUTION_MODES[0], "recursive");
assert.equal(EXECUTION_MODES[1], "top_level_requeue");
assert.equal(executionModeFromValue("top_level_requeue"), REQUEUE_MODE);
assert.equal(executionModeFromValue(" top_level_requeue "), REQUEUE_MODE);
assert.equal(executionModeFromValue("recursive_legacy"), LEGACY_MODE);
assert.equal(executionModeFromValue("recursive"), RECURSIVE_MODE);
assert.equal(executionModeFromValue(""), LEGACY_MODE);
assert.equal(executionModeFromValue(null), LEGACY_MODE);
assert.equal(executionModeFromValue("banana"), LEGACY_MODE);
assert.equal(isRequeueMode("top_level_requeue"), true);
assert.equal(isRequeueMode("recursive_legacy"), false);
assert.equal(isRequeueMode("recursive"), false);
const oldEnd = {type:"MiniMaxH3ChainLoopEnd",widgets:[{name:"execution_mode",value:"recursive_legacy"}]};
migrateRecursiveExecutionMode(oldEnd);
assert.equal(oldEnd.widgets[0].value,"recursive");
for (const value of ["recursive", "top_level_requeue", "unknown"]) {
    oldEnd.widgets[0].value=value;
    migrateRecursiveExecutionMode(oldEnd);
    assert.equal(oldEnd.widgets[0].value,value);
}
oldEnd.type="AnotherNode";oldEnd.widgets[0].value="recursive_legacy";
migrateRecursiveExecutionMode(oldEnd);
assert.equal(oldEnd.widgets[0].value,"recursive_legacy");
assert.equal(HANDOFF_API_BASE, "/minimax_h3_context_loop");
assert.equal(DEFAULT_CLEANUP_DELAY_MS, 10750);

// --- cleanup interval ------------------------------------------------------

assert.equal(cleanupDelayMs(10750), 10750);
assert.equal(cleanupDelayMs(0), 0);
assert.equal(cleanupDelayMs("5000"), 5000);
assert.equal(cleanupDelayMs("-5"), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs("banana"), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs(Number.NaN), DEFAULT_CLEANUP_DELAY_MS);
assert.equal(cleanupDelayMs(null), DEFAULT_CLEANUP_DELAY_MS);

// --- handoff list interpretation -------------------------------------------

const handoffList = {
    run_name: "run",
    handoffs: [
        {
            handoff_id: "next_scene_0002",
            action: "next_scene",
            status: "pending",
            start_clip: 2,
            resume: {
                start_clip: 2,
                scene_range: "",
                end_clip: 3,
                total_scenes: 3,
            },
        },
        {
            handoff_id: "next_scene_0001",
            action: "next_scene",
            status: "consumed",
            start_clip: 1,
            resume: {
                start_clip: 1,
                scene_range: "",
                end_clip: 3,
                total_scenes: 3,
            },
        },
        {
            handoff_id: "next_candidate_0001",
            action: "next_candidate",
            status: "pending",
            start_clip: 1,
        },
        {
            handoff_id: "",
            action: "next_scene",
            status: "pending",
        },
    ],
};
assert.deepEqual(
    pendingNextSceneHandoffs(handoffList).map((item) => item.handoff_id),
    ["next_scene_0002"],
);
assert.deepEqual(pendingNextSceneHandoffs({handoffs: "nope"}), []);
assert.deepEqual(pendingNextSceneHandoffs(null), []);
assert.deepEqual(pendingNextSceneHandoffs({}), []);
const committed = {clipIndex: 1, endClip: 3, workflowFingerprint: "wf",
    sourceRevision: "revision-a", checkpointSha: "checkpoint-a"};
const matching = matchingNextSceneHandoff({handoffs: [
    {...handoffList.handoffs[0], predecessor_scene: 1, end_clip: 3,
        workflow_fingerprint: "wf", source_revision: "revision-a",
        source_checkpoint_sha256: "checkpoint-a"},
    {handoff_id: "stale", action: "next_scene", status: "pending", start_clip: 2,
        predecessor_scene: 1, end_clip: 3, workflow_fingerprint: "old",
        source_revision: "revision-a", source_checkpoint_sha256: "checkpoint-a"},
]}, committed);
assert.equal(matching.handoff_id, "next_scene_0002");
assert.equal(matchingNextSceneHandoff({handoffs: [
    {handoff_id: "old", action: "next_scene", status: "pending", start_clip: 2,
        predecessor_scene: 1, workflow_fingerprint: "old"},
]}, committed), null);
for (const change of [
    {source_revision: "wrong"}, {source_checkpoint_sha256: "wrong"},
    {workflow_fingerprint: "wrong"}, {end_clip: 4}, {working_branch_id: "a".repeat(32)},
]) {
    assert.equal(matchingNextSceneHandoff({handoffs: [{
        handoff_id: "wrong", action: "next_scene", status: "pending",
        start_clip: 2, predecessor_scene: 1, end_clip: 3,
        workflow_fingerprint: "wf", source_revision: "revision-a",
        source_checkpoint_sha256: "checkpoint-a", ...change,
    }]}, committed), null);
}
assert.equal(matchingNextSceneHandoff({handoffs: [
    {handoff_id: "missing", action: "next_scene", status: "pending", start_clip: 2,
        predecessor_scene: 1, end_clip: 3, workflow_fingerprint: "wf"},
]}, committed), null, "missing durable identity is never wildcard-matched");

assert.deepEqual(resumeHint(handoffList.handoffs[0]), {
    startClip: 2,
    sceneRange: "",
    endClip: 3,
    totalScenes: 3,
});
assert.deepEqual(resumeHint({
    resume: {start_clip: 2, scene_range: "2:4", end_clip: 4, total_scenes: 6},
}), {startClip: 2, sceneRange: "2:4", endClip: 4, totalScenes: 6});
assert.equal(resumeHint({}), null);
assert.equal(resumeHint({resume: {start_clip: 0}}), null);
assert.equal(resumeHint({resume: {start_clip: "x"}}), null);

// --- predecessor checkpoint validation --------------------------------------

assert.equal(predecessorScene({startClip: 2}), 1);
assert.equal(predecessorScene({startClip: 1}), 0);
assert.equal(predecessorScene(null), 0);
assert.equal(checkpointPredecessorReady([{ready: true, scene: 1}], 0), true);
assert.equal(checkpointPredecessorReady([{ready: true, scene: 1}], 1), true);
assert.equal(
    checkpointPredecessorReady([{ready: false, scene: 1}], 1),
    false,
);
assert.equal(checkpointPredecessorReady([], 2), false);
assert.equal(checkpointPredecessorReady(null, 2), false);
assert.equal(checkpointPredecessorReady("nope", 2), false);

// --- queue-safe interpretation ----------------------------------------------

assert.equal(
    isQueueSafe({queue_running: [], queue_pending: []}),
    true,
);
assert.equal(
    isQueueSafe({queue_running: [{request_id: "1"}], queue_pending: []}),
    false,
);
assert.equal(
    isQueueSafe({queue_running: [], queue_pending: [{request_id: "2"}]}),
    false,
);
assert.equal(isQueueSafe({}), false);
assert.equal(isQueueSafe(null), false);

// --- wrapper source contract -------------------------------------------------

const source = fs.readFileSync(
    new URL("../web/h3_chain_top_level_requeue.js", import.meta.url),
    "utf8",
);

function gate() { let resolve; return {promise:new Promise(done=>resolve=done),resolve}; }
async function exerciseProcessRequeue({submission, transitionOk = true, cancelAfterClaim = false, serializeGate, queueGate, onReady, readOnly = false} = {}) {
    const calls={claim:0,serialize:0,queue:0,uncertain:0,queued:0,release:0}, notices=[];
    let enabled=true, registeredExtension;
    const serializeStarted=gate(), queueStarted=gate();
    const response = (ok=true, body={}) => ({ok,status:ok?200:500,json:async()=>body});
    const start={id:1,comfyClass:"MiniMaxH3ChainLoopStart",widgets:[{name:"start_clip",value:1},{name:"scene_range",value:""}]};
    const plan={id:2,comfyClass:"MiniMaxH3ChainPlan",widgets:[{name:"run_name",value:"run-a"}]};
    const current={id:3,comfyClass:"MiniMaxH3ChainCurrent",inputs:[{link:1},{link:2}]};
    const graph={nodes:[start,plan,current],links:{1:{origin_id:1},2:{origin_id:2}},getNodeById:id=>({1:start,2:plan,3:current})[id]};
    for (const node of graph.nodes) node.graph=graph;
    globalThis.__h3App={graph,graphToPrompt:async()=>{calls.serialize++; serializeStarted.resolve(); if(serializeGate) await serializeGate.promise; return {};},extensionManager:{setting:{get:id=>id.includes("Cleanup")?0:enabled},workflow:{activeWorkflow:null}},registerExtension:extension=>{registeredExtension=extension;}};
    globalThis.__h3Api={addEventListener:()=>{},queuePrompt:async()=>{calls.queue++; queueStarted.resolve(); if(queueGate) await queueGate.promise; if(submission === "rejected") throw Object.assign(Error("validation"),{status:400}); return submission === "accepted" ? {prompt_id:"prompt-a"} : (()=>{throw Error("network")})();},fetchApi:async (url, options={})=>{
        if (options.method === "POST" && url.includes("/handoffs/")) {
            assert.equal(options.headers["X-H3-Workflow-Owner"], "test-owner");
        }
        if(url === "/api/queue") return response(true,{queue_running:[],queue_pending:[]});
        if(url.includes("/checkpoints?")) return response(true,{checkpoints:[{scene:1,ready:true,revision:"rev-a",metadata_sha256:"sha-a"}]});
        if(url.includes("/handoffs?")) return response(true,{handoffs:[{action:"next_scene",status:"pending",handoff_id:"handoff-a",predecessor_scene:1,start_clip:2,end_clip:2,workflow_fingerprint:"fp-a",source_revision:"rev-a",source_checkpoint_sha256:"sha-a",resume:{start_clip:2,scene_range:"2"}}]});
        if(url.endsWith("/claim")){calls.claim++; enabled=!cancelAfterClaim; return response();}
        if(url.endsWith("/release")){calls.release++;return response();}
        if(url.endsWith("/transition")){const status=JSON.parse(options.body).status; calls[status]++; return response(transitionOk,{error:"transition unavailable"});}
        throw Error(`unexpected ${url}`);
    }};
    globalThis.__h3Notification={createNotificationStack:()=>({show:(_id,message)=>notices.push(message),clear:()=>{},clearAll:()=>{}})};
    globalThis.__h3Core=requeueCore; globalThis.__h3Coordinator=coordinator;
    globalThis.__h3MutationOptions=async (node,runName,options)=>{
        assert.equal(node.graph,graph); assert.equal(runName,"run-a");
        if (readOnly) throw Error("Project is read-only");
        return {...options,headers:{...options.headers,"X-H3-Workflow-Owner":"test-owner"}};
    };
    globalThis.window={setTimeout};
    let moduleSource=source.replace('import {app} from "/scripts/app.js";', 'const app=globalThis.__h3App;')
        .replace('import {api} from "/scripts/api.js";', 'const api=globalThis.__h3Api;')
        .replace(/import \{activeSceneFromOutput\} from ".*?";/, 'const activeSceneFromOutput=()=>null;')
        .replace(/import \{[\s\S]*?\} from "\.\/h3_chain_top_level_requeue_core\.mjs\?v=.*?";/, 'const {DEFAULT_CLEANUP_DELAY_MS,HANDOFF_API_BASE,RECURSIVE_MODE,migrateRecursiveExecutionMode,checkpointPredecessorReady,cleanupDelayMs,isQueueSafe,matchingNextSceneHandoff,pendingNextSceneHandoffs,predecessorScene,resumeHint,handleTopLevelRequeueSuccessScheduling,loopEndMatchesObservedCurrent,topLevelRequeueCompletionMatches}=globalThis.__h3Core;')
        .replace(/import \{createNotificationStack\} from ".*?";/, 'const {createNotificationStack}=globalThis.__h3Notification;')
        .replace(/import \{projectMutationOptions\} from ".*?";/, 'const projectMutationOptions=globalThis.__h3MutationOptions;')
        .replace(/import \{submitWithPromptIdentity,[\s\S]*?\} from "\.\/h3_chain_top_level_requeue_coordinator\.mjs\?v=.*?";/, 'const {submitWithPromptIdentity,submissionFailure,createContinuationTracker,runRequeueLifecycle,authoritativeRunName,finalizeAcceptedSubmission,handleConfirmedSubmissionRejection,handleUncertainSubmission,classifySubmissionOutcome,releaseHandoffChecked}=globalThis.__h3Coordinator;')
        .replace(/export function /g, 'function ') + '\nglobalThis.__h3ProcessRequeue=processRequeue;';
    await import(`data:text/javascript,${encodeURIComponent(moduleSource)}#${Math.random()}`);
    const setting=registeredExtension?.settings?.filter(item=>item.id === "MiniMaxH3ContextLoop.topLevelRequeue");
    assert.equal(registeredExtension?.name,"minimax_h3_context_loop.top_level_requeue"); assert.equal(setting?.length,1); assert.equal(typeof setting[0].onChange,"function");
    class LoopEnd {
        constructor() {this.type="MiniMaxH3ChainLoopEnd";this.widgets=[{name:"execution_mode",value:"recursive_legacy"}];}
        onConfigure(value) {this.widgets[0].value=value;return "configured";}
    }
    registeredExtension.beforeRegisterNodeDef(LoopEnd,{name:"MiniMaxH3ChainLoopEnd"});
    const loopEnd=new LoopEnd();registeredExtension.nodeCreated(loopEnd);
    assert.equal(loopEnd.widgets[0].value,"recursive");
    assert.equal(loopEnd.onConfigure("recursive_legacy"),"configured");
    assert.equal(loopEnd.widgets[0].value,"recursive","Opening an old workflow updates the label without changing execution");
    loopEnd.onConfigure("top_level_requeue");assert.equal(loopEnd.widgets[0].value,"top_level_requeue");
    const work=globalThis.__h3ProcessRequeue({promptId:"source",runName:"run-a",clipIndex:1,endClip:2,workflowFingerprint:"fp-a",displayNode:"3"},0);
    await onReady?.({calls,setting:setting[0],setEnabled:value=>{enabled=value;},serializeStarted,queueStarted});
    await work;
    return {calls,notices};
}
let processCase=await exerciseProcessRequeue({submission:"network",transitionOk:true});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:1,uncertain:1,queued:0,release:0});
assert.match(processCase.notices.at(-1),/Queue delivery is uncertain/);
processCase=await exerciseProcessRequeue({submission:"network",transitionOk:false});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:1,uncertain:1,queued:0,release:0});
assert.match(processCase.notices.at(-1),/delivery may have occurred[\s\S]*not released[\s\S]*Reconcile/);
processCase=await exerciseProcessRequeue({submission:"accepted",transitionOk:false});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:1,uncertain:0,queued:1,release:0});
assert.match(processCase.notices.at(-1),/delivery may have occurred/);
processCase=await exerciseProcessRequeue({submission:"rejected"});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:1,uncertain:0,queued:0,release:1});
processCase=await exerciseProcessRequeue({submission:"accepted",cancelAfterClaim:true});
assert.deepEqual(processCase.calls,{claim:1,serialize:0,queue:0,uncertain:0,queued:0,release:1});
const serializationGate=gate();
processCase=await exerciseProcessRequeue({submission:"accepted",serializeGate:serializationGate, onReady:async ({calls,setting,setEnabled,serializeStarted})=>{await serializeStarted.promise; assert.deepEqual(calls,{claim:1,serialize:1,queue:0,uncertain:0,queued:0,release:0}); setEnabled(false); setting.onChange(false); serializationGate.resolve();}});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:0,uncertain:0,queued:0,release:1});
const acceptedGate=gate(); let acceptedTransition;
processCase=await exerciseProcessRequeue({submission:"accepted",queueGate:acceptedGate,onReady:async ({calls,setting,setEnabled,queueStarted})=>{await queueStarted.promise; assert.equal(calls.queue,1); setEnabled(false); setting.onChange(false); acceptedGate.resolve();}});
assert.deepEqual(processCase.calls,{claim:1,serialize:1,queue:1,uncertain:0,queued:1,release:0});

// Terminal-success-only trigger; never sampler completion.
processCase=await exerciseProcessRequeue({submission:"accepted",readOnly:true});
assert.deepEqual(processCase.calls,{claim:0,serialize:0,queue:0,uncertain:0,queued:0,release:0});
assert.match(source, /api\.addEventListener\("execution_success"/);
assert.doesNotMatch(source, /MiniMaxH3H3Sample/);
assert.match(source, /createNotificationStack\(/);
assert.match(source, /anchorSelector:\s*"\.h3cr-root"/);
assert.match(source, /showTransient\(/);
assert.match(source, /showWarning\(/);
assert.match(source, /showError\(/);
assert.match(source, /clearNotifications\(/);
assert.doesNotMatch(source, /left:18px/);
assert.doesNotMatch(source, /Scene \$\{resume\.startClip\} queued as a new top-level prompt/);
// Failure paths keep the handoff durable and never auto-retry.
assert.match(source, /api\.addEventListener\("execution_interrupted"/);
assert.match(source, /api\.addEventListener\("execution_error"/);
assert.match(source, /The handoff stays queued/);
// Safe queue + configurable cleanup interval measured from terminal success.
assert.match(source, /\/api\/queue/);
assert.match(source, /waitForSafeQueue\(epoch\)/);
assert.match(source, /cleanupDelayMs\(/);
assert.match(source, /MiniMaxH3ContextLoop\.topLevelRequeueCleanupDelay/);
assert.match(source, /defaultValue:\s*DEFAULT_CLEANUP_DELAY_MS/);
// Opt-in master switch.
assert.match(source, /MiniMaxH3ContextLoop\.topLevelRequeue\b/);
assert.match(source, /defaultValue:\s*false/);
assert.match(source, /settings:\s*\[/);
assert.equal((source.match(/"MiniMaxH3ContextLoop\.topLevelRequeue"/g) ?? []).length, 1);
assert.equal((source.match(/"MiniMaxH3ContextLoop\.topLevelRequeueCleanupDelay"/g) ?? []).length, 1);
assert.doesNotMatch(source, /settings\.addSetting/);
assert.match(source, /extensionManager\?\.setting\?\.get\?\.\(SETTING_ID\) === true/);
assert.match(source, /extensionManager\?\.setting\?\.get\?\.\(DELAY_SETTING_ID\)/);
const executedBody = source.slice(source.indexOf("function onExecuted"), source.indexOf("function enqueueRequeue"));
assert.match(executedBody, /detail\?\.output\?\.h3_chain_top_level_requeue/);
assert.match(executedBody, /topLevelRequeueCompletionMatches\(record, payload\)/);
assert.doesNotMatch(executedBody, /widgetByName\(node, "execution_mode"\)/);
const terminalFailureBody = source.slice(source.indexOf("function onTerminalFailure"), source.indexOf("async function processRequeue"));
assert.doesNotMatch(terminalFailureBody, /requeueEpoch \+= 1/);
assert.match(terminalFailureBody, /sceneRecords\.delete\(promptId\)/);
assert.match(terminalFailureBody, /continuationTracker\?\.failed\(promptId\)/);
const settingBody = source.slice(source.indexOf("onChange(value)"), source.indexOf("api.addEventListener"));
assert.match(settingBody, /value !== true/);
assert.match(settingBody, /requeueEpoch \+= 1/);
assert.match(settingBody, /clearNotifications/);
const booleanCategory = ["MiniMax H3 Context Loop", "Interface", "Top-level requeue"];
const cleanupCategory = ["MiniMax H3 Context Loop", "Interface", "Top-level requeue cleanup"];
assert.match(source, /category: \["MiniMax H3 Context Loop", "Interface", "Top-level requeue"\],[\s\S]*type: "boolean"/);
assert.match(source, /category: \["MiniMax H3 Context Loop", "Interface", "Top-level requeue cleanup"\],[\s\S]*type: "number"/);
assert.equal(booleanCategory[0], cleanupCategory[0]);
assert.equal(booleanCategory[1], cleanupCategory[1]);
assert.notEqual(booleanCategory.join("/"), cleanupCategory.join("/"),
    "complete Settings tree paths must not collide");
assert.equal(booleanCategory.join("/"),
    ["MiniMax H3 Context Loop", "Interface", "Top-level requeue"].join("/"),
    "the old identical cleanup path would have collided with the boolean leaf");
// Workflow/run identity validation before requeue.
assert.match(source, /function requireVisibleWorkflow\(record\)/);
assert.match(source, /workflowIdentity !== record\.workflowIdentity/);
// Predecessor checkpoint validation (spec: queue-safe requirement).
assert.match(source, /checkpoints\?run_name=/);
assert.match(source, /Checkpoint \$\{predecessor\} is not ready/);
// Claim exactly once; duplicate terminal events queue nothing.
assert.match(source, /handoffs\/claim/);
assert.match(source, /response\.status === 409/);
assert.match(source, /The handoff was already claimed; nothing was queued/);
// Existing Loop Start widgets only; the Plan JSON is never rewritten here.
assert.match(source, /prepareResume: async/);
assert.match(source, /widgetByName\(context\.startNode, "start_clip"\)/);
assert.match(source, /widgetByName\(context\.startNode, "scene_range"\)/);
assert.match(source, /startWidget\.callback\?\.\(resume\.startClip\)/);
// Reading branch identity is required; requeue must still never rewrite Plan JSON.
assert.match(source, /JSON\.parse\(String\(widgetByName\(planNode, "plan_json"\)/);
assert.doesNotMatch(source, /widgetByName\([^\n]*"plan_json"[^\n]*\.value\s*=/);
// Same workflow queued as a NEW top-level prompt.
// queued/consumed lifecycle via the durable routes.
assert.match(source, /handoffs\/transition/);
assert.match(source, /finalizeAcceptedSubmission/);
assert.match(source, /handleUncertainSubmission/);
assert.match(source, /classifySubmissionOutcome/);
assert.match(source, /requireCurrentOperation\(epoch\)/);
assert.match(source, /execution_start/);
// Both release paths use the coordinator's checked HTTP helper.
assert.match(source, /releaseHandoffChecked/);
assert.match(source, /release: \(handoff, releasedRun\) => releaseHandoffChecked/);
assert.match(source, /releaseHandoff: \(releasedRun, releasedHandoff\) => releaseHandoffChecked/);
assert.doesNotMatch(source, /fetchApi\([^\n]*handoffs\/release/);
assert.match(source, /queue the workflow manually/);
// Browser/server restart: show pending state, never auto-run.
assert.match(source, /graphChanged/);
assert.match(source, /Nothing auto-runs on startup/);
assert.match(source, /Pending H3 handoff for run/);
// Offline constraint: icons/decorations are inline SVG; nothing external is
// loaded (the w3.org string is the SVG XML namespace, not a fetched asset).
assert.doesNotMatch(source, /https?:\/\/(?!www\.w3\.org\/2000\/svg)/);
assert.doesNotMatch(source, /\bsrc\s*=\s*["']http/);
assert.doesNotMatch(source, /url\(\s*["']?https?:/);
assert.doesNotMatch(source, /new Image\(\)/);
assert.match(source, /createElementNS\(namespace, "svg"\)/);

// The core module must stay import-free of ComfyUI.
const coreSource = fs.readFileSync(
    new URL("../web/h3_chain_top_level_requeue_core.mjs", import.meta.url),
    "utf8",
);
assert.doesNotMatch(coreSource, /\/scripts\/(app|api)\.js/);
assert.doesNotMatch(coreSource, /https?:\/\//);

console.log("H3 top-level requeue helpers: ok");
