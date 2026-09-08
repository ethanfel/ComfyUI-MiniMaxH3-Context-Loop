#!/usr/bin/env node
import assert from "node:assert/strict";
import {runRequeueLifecycle, submitWithPromptIdentity, selectAndClaim, authoritativeRunName, createContinuationTracker, deliverClaimed, finalizeAcceptedSubmission, handleConfirmedSubmissionRejection, handleUncertainSubmission, classifySubmissionOutcome, releaseHandoffChecked} from "../web/h3_chain_top_level_requeue_coordinator.mjs";
import {matchingNextSceneHandoff} from "../web/h3_chain_top_level_requeue_core.mjs";

const record = {runName:"run", clipIndex:3, endClip:6, workflowFingerprint:"wf-current", sourceRevision:"rev-current", checkpointSha:"sha-current"};
const exact = {handoff_id:"exact-handoff", action:"next_scene", status:"pending", predecessor_scene:3,start_clip:4,end_clip:6,source_revision:"rev-current",source_checkpoint_sha256:"sha-current",workflow_fingerprint:"wf-current"};
function gate() { let resolve; return {promise: new Promise(r => { resolve = r; }), resolve}; }
async function scenario(which) {
  let enabled = true, claims = 0, submits = 0, releases = 0;
  const poll = gate(), delay = gate(), claimed = gate();
  const current = () => { if (!enabled) throw Object.assign(new Error("disabled"), {preDelivery: true}); };
  const work = runRequeueLifecycle({current,
    waitSafe: async () => { if (which === "poll") await poll.promise; },
    cleanup: async () => { if (which === "cleanup") await delay.promise; },
    resolveRun: async () => record,
    loadCheckpoint: async () => ({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),
    listHandoffs: async () => ({handoffs:[exact]}), matchHandoff: matchingNextSceneHandoff,
    claimHandoff: async () => { claims++; if (which === "claim") enabled = false; },
    prepareResume: async () => {}, submit: async () => { submits++; return {prompt_id:"p"}; }, release: async () => { releases++; }}).catch(() => {});
  await Promise.resolve(); await Promise.resolve();
  if (which !== "claim") enabled = false;
  poll.resolve(); delay.resolve(); claimed.resolve(); await work;
  return {claims, submits, releases};
}
let r = await scenario("poll"); assert.deepEqual(r, {claims:0, submits:0, releases:0});
r = await scenario("cleanup"); assert.deepEqual(r, {claims:0, submits:0, releases:0});
r = await scenario("claim"); assert.deepEqual(r, {claims:1, submits:0, releases:1});
let cancellationClaims=0, cancellationReleases=0, cancellationSubmits=0, cancellationEnabled=true;
await assert.rejects(() => runRequeueLifecycle({current:()=>{if(!cancellationEnabled) throw Error("disabled");}, waitSafe:async()=>{}, cleanup:async()=>{}, resolveRun:async()=>record, loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}), listHandoffs:async()=>({handoffs:[exact]}), matchHandoff:matchingNextSceneHandoff, claimHandoff:async()=>{cancellationClaims++; cancellationEnabled=false;}, prepareResume:async()=>{}, submit:async()=>cancellationSubmits++, release:async()=>{cancellationReleases++; throw Error("HTTP 500");}}));
assert.deepEqual({cancellationClaims,cancellationReleases,cancellationSubmits},{cancellationClaims:1,cancellationReleases:1,cancellationSubmits:0});
const serializationGate=gate(), submissionOrder=[]; let submissionEnabled=true, directQueueCalls=0;
const pendingSubmission=submitWithPromptIdentity({app:{graph:{nodes:[{widgets:[{beforeQueued:()=>submissionOrder.push("beforeQueued")}]}]},graphToPrompt:async()=>{submissionOrder.push("serialize-start");await serializationGate.promise;submissionOrder.push("serialize-end");return {}; }},api:{queuePrompt:async()=>{directQueueCalls++;submissionOrder.push("queue");return {prompt_id:"never"};}},current:()=>{submissionOrder.push("current");if(!submissionEnabled)throw Error("disabled");}});
await Promise.resolve(); submissionEnabled=false; serializationGate.resolve();
await assert.rejects(pendingSubmission,error=>error.preDelivery===true);
assert.equal(directQueueCalls,0); assert.deepEqual(submissionOrder,["beforeQueued","serialize-start","serialize-end","current"]);
const enabledOrder=[];
await submitWithPromptIdentity({app:{graph:{nodes:[{widgets:[{beforeQueued:()=>enabledOrder.push("beforeQueued")}]}]},graphToPrompt:async()=>{enabledOrder.push("serialize-start");enabledOrder.push("serialize-end");return {}; }},api:{queuePrompt:async()=>{enabledOrder.push("queue");return {prompt_id:"ordered"};}},current:()=>enabledOrder.push("current")});
assert.deepEqual(enabledOrder,["beforeQueued","serialize-start","serialize-end","current","queue"]);
const stale = [
 {...exact,handoff_id:"revision",source_revision:"bad"}, {...exact,handoff_id:"sha",source_checkpoint_sha256:"bad"},
 {...exact,handoff_id:"wf",workflow_fingerprint:"bad"}, {...exact,handoff_id:"range",end_clip:5},
 {...exact,handoff_id:"pred",predecessor_scene:2}, {...exact,handoff_id:"missing",source_revision:null}, exact];
let calls=[]; let selected=await selectAndClaim({record,handoffs:{handoffs:stale},match:matchingNextSceneHandoff,resolveRun:r=>r.runName,claim:async (...x)=>calls.push(x)});
assert.equal(selected.handoff_id,exact.handoff_id); assert.deepEqual(calls,[["run",exact.handoff_id]]);
calls=[]; selected=await selectAndClaim({record,handoffs:{handoffs:stale.slice(0,-1)},match:matchingNextSceneHandoff,resolveRun:r=>r.runName,claim:async (...x)=>calls.push(x)});
assert.equal(selected,null); assert.deepEqual(calls,[]);
calls=[]; selected=await selectAndClaim({record,handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,resolveRun:()=>"actual-run",claim:async (...x)=>calls.push(x)});
assert.equal(selected,null); assert.deepEqual(calls,[]);
const projectRecord={...record,runName:"actual-run"}; calls=[]; selected=await selectAndClaim({record:projectRecord,handoffs:{handoffs:[{...exact,workflow_fingerprint:"wf-current"}]},match:matchingNextSceneHandoff,resolveRun:()=>"actual-run",claim:async (...x)=>calls.push(x)});
assert.equal(selected.handoff_id,exact.handoff_id); assert.deepEqual(calls,[["actual-run",exact.handoff_id]]);
async function lifecycleClaim(inventory) { const log=[]; const result=await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>record,loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async run=>{log.push(["list",run]);return {handoffs:inventory};},matchHandoff:matchingNextSceneHandoff,claimHandoff:async (run,handoff)=>log.push(["claim",run,handoff.handoff_id])}); return {log,result}; }
let lifecycleCase=await lifecycleClaim(stale); assert.deepEqual(lifecycleCase.log,[["list","run"],["claim","run",exact.handoff_id]]); assert.equal(lifecycleCase.result.handoff.handoff_id,exact.handoff_id);
lifecycleCase=await lifecycleClaim(stale.slice(0,-1)); assert.deepEqual(lifecycleCase.log,[["list","run"]]); assert.equal(lifecycleCase.result,null);
lifecycleCase=await lifecycleClaim([{...exact,handoff_id:"missing-sha",source_checkpoint_sha256:null}]); assert.deepEqual(lifecycleCase.log,[["list","run"]]); assert.equal(lifecycleCase.result,null);
for (const mode of ["rejected", "network"]) {
 let claim=0, submit=0, release=0, uncertain=0;
 await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},select:async()=>exact,claim:async()=>claim++,prepare:async()=>{},submit:async()=>{submit++; if(mode==="network") throw Error("network"); return {kind:"rejected"};},release:async()=>release++,uncertain:async()=>uncertain++});
 assert.equal(claim,1); assert.equal(submit,1); assert.equal(release,mode==="rejected"?1:0); assert.equal(uncertain,mode==="network"?1:0);
}
const releaseCalls=[];
const releaseApi={fetchApi:async (url, options) => { releaseCalls.push({url,options}); return {ok:true,status:200}; }};
await releaseHandoffChecked({api:releaseApi,apiBase:"/base",runName:"run",handoffId:"handoff",reason:"cancelled"});
assert.equal(releaseCalls.length,1); assert.equal(releaseCalls[0].url,"/base/handoffs/release"); assert.equal(releaseCalls[0].options.method,"POST"); assert.deepEqual(JSON.parse(releaseCalls[0].options.body),{run_name:"run",handoff_id:"handoff",reason:"cancelled"});
for (const [response, expected] of [
 [{ok:false,status:409,json:async()=>({error:"handoff is not claimed"})},"handoff is not claimed"],
 [{ok:false,status:500,json:async()=>({error:"backend exploded"})},"backend exploded"],
 [{ok:false,status:500,json:async()=>{throw Error("bad json");}},"HTTP 500"],
]) await assert.rejects(() => releaseHandoffChecked({api:{fetchApi:async()=>response},apiBase:"/base",runName:"run",handoffId:"handoff",reason:"x"}), new RegExp(expected));
await assert.rejects(() => releaseHandoffChecked({api:{fetchApi:async()=>{throw Error("network down");}},apiBase:"/base",runName:"run",handoffId:"handoff",reason:"x"}),/network down/);
let missingFetches=0;
await assert.rejects(() => releaseHandoffChecked({api:{fetchApi:async()=>missingFetches++},apiBase:"/base",runName:"",handoffId:"handoff",reason:"x"}),/run name/);
await assert.rejects(() => releaseHandoffChecked({api:{fetchApi:async()=>missingFetches++},apiBase:"/base",runName:"run",handoffId:"",reason:"x"}),/handoff ID/);
assert.equal(missingFetches,0);
const manager={comfyClass:"MiniMaxH3ProjectAssetManager",widgets:[{name:"run_name",value:"actual-run"}]};
const plan={widgets:[{name:"run_name",value:"stale-plan-name"}],inputs:[{name:"project_assets",link:1}],graph:{links:{1:{origin_id:2}},getNodeById:()=>manager}};
assert.equal(authoritativeRunName(plan),"actual-run");
assert.equal(authoritativeRunName({widgets:[{name:"run_name",value:"plain-run"}]}),"plain-run");
const emptyManager={comfyClass:"MiniMaxH3ProjectAssetManager",widgets:[{name:"run_name",value:""}]};
const emptyPlan={widgets:[{name:"run_name",value:"fallback-run"}],inputs:[{name:"project_assets",link:2}],graph:{links:{2:{origin_id:9}},getNodeById:id=>id===9?emptyManager:null}};
assert.equal(authoritativeRunName(emptyPlan),"fallback-run");
const events=[];
const failureTracker = createContinuationTracker({transition: async (...args) => events.push(args)});
failureTracker.track("run-a", "handoff-a", "prompt-123");
assert.equal(failureTracker.failed("prompt-999"), null);
assert.equal(failureTracker.current().promptId, "prompt-123");
assert.deepEqual(failureTracker.failed("prompt-123"), {runName:"run-a", handoffId:"handoff-a", promptId:"prompt-123"});
assert.equal(failureTracker.current(), null);
failureTracker.track("run-a", "handoff-a", "prompt-123");
assert.equal(await failureTracker.started("prompt-999"), false);
assert.equal(failureTracker.current().promptId, "prompt-123");
assert.equal(await failureTracker.started("prompt-123"), true);
assert.equal(failureTracker.current(), null);
assert.equal(failureTracker.failed("prompt-123"), null);
assert.equal(events.length, 1);
for (const mode of ["rejected","network","accepted","disabled"]) {
 let submit=0, release=0, transitions=[], tracked=[];
 const result=await deliverClaimed({current:()=>{if(mode==="disabled") throw Object.assign(Error(),{preDelivery:true})},handoff:{handoff_id:"h"},prepare:async()=>{},submit:async()=>{submit++;if(mode==="network")throw Error();return mode==="accepted"?{kind:"accepted",promptId:"accepted-123"}:{kind:"rejected"}},release:async()=>release++,transition:async (...x)=>transitions.push(x),track:x=>tracked.push(x)});
 assert.equal(submit,mode==="disabled"?0:1); if(mode==="rejected"||mode==="disabled")assert.equal(release,1); if(mode==="network")assert.equal(transitions[0][1],"uncertain"); if(mode==="accepted")assert.deepEqual(tracked,["accepted-123"]);
}
let routed=[];
const projectClaim=await selectAndClaim({planNode:plan,record:{...record,runName:"actual-run"},handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async (run,id)=>routed.push(["claim",run,id])});
assert.equal(projectClaim._resolvedRunName,"actual-run"); assert.deepEqual(routed.map(x=>x[1]),["actual-run","actual-run"]);
routed=[];
await selectAndClaim({planNode:{widgets:[{name:"run_name",value:"plain-run"}]},record:{...record,runName:"plain-run"},handoffs:{handoffs:[exact]},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async run=>routed.push(["claim",run])});
assert.deepEqual(routed.map(x=>x[1]),["plain-run","plain-run"]);
routed=[];
assert.equal(await selectAndClaim({planNode:{...plan,graph:plan.graph},record:{...record,runName:"different-run"},match:matchingNextSceneHandoff,loadHandoffs:async run=>{routed.push(["list",run]);return {handoffs:[exact]}},claim:async run=>routed.push(["claim",run])}),null); assert.equal(routed.length,0);
const acceptedCalls=[];
assert.deepEqual(await finalizeAcceptedSubmission({runName:"actual-run",handoffId:"handoff-123",promptId:"prompt-456",transitionQueued:async (...x)=>acceptedCalls.push(["queued",...x]),trackContinuation:(...x)=>acceptedCalls.push(["track",...x])}),{kind:"accepted",promptId:"prompt-456"});
assert.deepEqual(acceptedCalls,[["queued","actual-run","handoff-123","queued","prompt-456"],["track","actual-run","handoff-123","prompt-456"]]);
await assert.rejects(finalizeAcceptedSubmission({runName:"actual-run",handoffId:"handoff-123",promptId:"",transitionQueued:async()=>acceptedCalls.push("bad"),trackContinuation:()=>acceptedCalls.push("bad")}));
assert.equal(acceptedCalls.length,2);
const releases=[];
assert.deepEqual(await handleConfirmedSubmissionRejection({runName:"actual-run",handoffId:"handoff-123",releaseHandoff:async (...x)=>releases.push(x)}),{kind:"rejected",released:true});
assert.deepEqual(releases,[["actual-run","handoff-123"]]);
let failedRejectionReleases=0;
await assert.rejects(() => handleConfirmedSubmissionRejection({runName:"actual-run",handoffId:"handoff-123",releaseHandoff:(runName,handoffId)=>releaseHandoffChecked({api:{fetchApi:async()=>{failedRejectionReleases++; return {ok:false,status:500,json:async()=>({error:"release unavailable"})};}},apiBase:"/base",runName,handoffId,reason:"rejected"})}),/release unavailable/);
assert.equal(failedRejectionReleases,1);
await assert.rejects(handleConfirmedSubmissionRejection({runName:"",handoffId:"handoff-123",releaseHandoff:async()=>releases.push("bad")})); assert.equal(releases.length,1);
const uncertainCalls=[];
assert.deepEqual(await handleUncertainSubmission({runName:"actual-run",handoffId:"handoff-123",markUncertain:async (...x)=>uncertainCalls.push(x)}),{kind:"uncertain"});
assert.deepEqual(uncertainCalls,[["actual-run","handoff-123","uncertain"]]);
await assert.rejects(handleUncertainSubmission({runName:"",handoffId:"handoff-123",markUncertain:async()=>uncertainCalls.push("bad")})); assert.equal(uncertainCalls.length,1);
const projectFlow=[];
const claimedProject = await selectAndClaim({planNode:plan, record:{...record,runName:"actual-run"}, match:matchingNextSceneHandoff,
 loadHandoffs:async run=>{projectFlow.push(["list",run]); return {handoffs:[exact]};},
 claim:async (run,id)=>projectFlow.push(["claim",run,id])});
let projectSubmits=0;
await deliverClaimed({current:()=>{}, handoff:claimedProject, prepare:async()=>{}, submit:async()=>{projectSubmits++; return {kind:"accepted",promptId:"project-prompt-123"};}, release:async()=>{},
 transition:async()=>{}, track:()=>{}});
await finalizeAcceptedSubmission({runName:claimedProject._resolvedRunName,handoffId:claimedProject.handoff_id,promptId:"project-prompt-123",transitionQueued:async (...x)=>projectFlow.push(["queued",...x]),trackContinuation:(...x)=>projectFlow.push(["track",...x])});
assert.deepEqual(projectFlow,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["queued","actual-run",exact.handoff_id,"queued","project-prompt-123"],["track","actual-run",exact.handoff_id,"project-prompt-123"]]);
assert.equal(projectSubmits,1);
const rejectedFlow=[];
const rejectedClaim = await selectAndClaim({planNode:plan,record:{...record,runName:"actual-run"},match:matchingNextSceneHandoff,
 loadHandoffs:async run=>{rejectedFlow.push(["list",run]);return {handoffs:[exact]};},claim:async (run,id)=>rejectedFlow.push(["claim",run,id])});
let rejectedSubmits=0;
await deliverClaimed({current:()=>{},handoff:rejectedClaim,prepare:async()=>{},submit:async()=>{rejectedSubmits++;return {kind:"rejected"};},release:async ()=>rejectedFlow.push(["release",rejectedClaim._resolvedRunName,rejectedClaim.handoff_id]),transition:async()=>rejectedFlow.push(["transition"]),track:()=>rejectedFlow.push(["track"])});
assert.deepEqual(rejectedFlow,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["release","actual-run",exact.handoff_id]]);assert.equal(rejectedSubmits,1);
const uncertainFlow=[];
const uncertainClaim = await selectAndClaim({planNode:plan,record:{...record,runName:"actual-run"},match:matchingNextSceneHandoff,
 loadHandoffs:async run=>{uncertainFlow.push(["list",run]);return {handoffs:[exact]};},claim:async (run,id)=>uncertainFlow.push(["claim",run,id])});
let uncertainSubmits=0;
await deliverClaimed({current:()=>{},handoff:uncertainClaim,prepare:async()=>{},submit:async()=>{uncertainSubmits++;throw Error("network");},release:async()=>uncertainFlow.push(["release"]),transition:async (_handoff,status)=>uncertainFlow.push(["transition",uncertainClaim._resolvedRunName,uncertainClaim.handoff_id,status]),track:()=>uncertainFlow.push(["track"])});
assert.deepEqual(uncertainFlow,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["transition","actual-run",exact.handoff_id,"uncertain"]]);assert.equal(uncertainSubmits,1);
let cancelled=false; const cancelCalls=[];
const cancelledResult=await runRequeueLifecycle({current:()=>{if(cancelled)throw Error("cancelled")},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>({...record,runName:"run-a"}),loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async run=>{cancelCalls.push(["list",run]);return {handoffs:[exact]}},matchHandoff:matchingNextSceneHandoff,claimHandoff:async (run,id)=>{cancelCalls.push(["claim",run,id.handoff_id]);cancelled=true},release:async (handoff,run)=>cancelCalls.push(["release",run,handoff.handoff_id]),prepareResume:async()=>cancelCalls.push(["prepare"]),submit:async()=>cancelCalls.push(["submit"])});
assert.equal(cancelledResult.kind,"cancelled");assert.deepEqual(cancelCalls,[["list","run-a"],["claim","run-a",exact.handoff_id],["release","run-a",exact.handoff_id]]);
for (const [outcome,kind] of [[{prompt_id:"prompt-123"},"accepted"],[false,"rejected"],[true,"uncertain"]]) { const c=[]; assert.equal((await classifySubmissionOutcome({outcome,accepted:async()=>c.push("a"),rejected:async()=>c.push("r"),uncertain:async()=>c.push("u")})).kind,kind); assert.equal(c.length,1); }
let c=[]; assert.equal((await classifySubmissionOutcome({error:Object.assign(Error(),{status:400}),accepted:async()=>c.push("a"),rejected:async()=>c.push("r"),uncertain:async()=>c.push("u")})).kind,"rejected");
c=[]; assert.equal((await classifySubmissionOutcome({error:Error("network"),accepted:async()=>c.push("a"),rejected:async()=>c.push("r"),uncertain:async()=>c.push("u")})).kind,"uncertain");
async function projectLifecycle(planNode, run, promptId) { const log=[]; const lifecycle=await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>({...record,runName:authoritativeRunName(planNode)}),loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async name=>{log.push(["list",name]);return {handoffs:[exact]};},matchHandoff:matchingNextSceneHandoff,claimHandoff:async (name,h)=>log.push(["claim",name,h.handoff_id]),prepareResume:async (name,h)=>log.push(["prepare",name,h.handoff_id]),submit:async (name,h)=>{log.push(["submit",name,h.handoff_id]);return {prompt_id:promptId};}}); if (!lifecycle) return log; await classifySubmissionOutcome({outcome:lifecycle.submission,accepted:async id=>finalizeAcceptedSubmission({runName:lifecycle.runName,handoffId:lifecycle.handoff.handoff_id,promptId:id,transitionQueued:async (...x)=>log.push(["queued",...x]),trackContinuation:(...x)=>log.push(["track",...x])}),rejected:async()=>{},uncertain:async()=>{}}); return log; }
const projectLog=await projectLifecycle(plan,"actual-run","project-prompt-123"); assert.deepEqual(projectLog,[["list","actual-run"],["claim","actual-run",exact.handoff_id],["prepare","actual-run",exact.handoff_id],["submit","actual-run",exact.handoff_id],["queued","actual-run",exact.handoff_id,"queued","project-prompt-123"],["track","actual-run",exact.handoff_id,"project-prompt-123"]]);
const plainLog=await projectLifecycle({widgets:[{name:"run_name",value:"plain-run"}]},"plain-run","plain-prompt-123"); assert.equal(plainLog[0][1],"plain-run");assert.equal(plainLog[1][1],"plain-run");
const mismatchLog=[]; const mismatchPlan={...plan,graph:{links:{1:{origin_id:2}},getNodeById:()=>({comfyClass:"MiniMaxH3ProjectAssetManager",widgets:[{name:"run_name",value:"project-run"}]})}}; const mismatch=await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>({...record,runtimeRunName:"different-run",runName:authoritativeRunName(mismatchPlan)}),loadCheckpoint:async()=>{},listHandoffs:async()=>{mismatchLog.push("list");return {handoffs:[exact]}},matchHandoff:matchingNextSceneHandoff,claimHandoff:async()=>mismatchLog.push("claim")}); assert.equal(mismatch,null);assert.deepEqual(mismatchLog,[]);
async function lifecycleSubmit(inventory) { const log=[]; const result=await runRequeueLifecycle({current:()=>{},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>record,loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async run=>{log.push(["list",run]);return {handoffs:inventory};},matchHandoff:matchingNextSceneHandoff,claimHandoff:async (run,h)=>log.push(["claim",run,h.handoff_id]),prepareResume:async (_run,h)=>log.push(["prepare",h.handoff_id]),submit:async (_run,h)=>{log.push(["submit",h.handoff_id]);return {prompt_id:"exact-prompt-123"};}}); return {log,result}; }
let submissionCase=await lifecycleSubmit(stale); assert.deepEqual(submissionCase.log,[["list","run"],["claim","run","exact-handoff"],["prepare","exact-handoff"],["submit","exact-handoff"]]);assert.equal(submissionCase.result.submission.prompt_id,"exact-prompt-123");
submissionCase=await lifecycleSubmit(stale.slice(0,-1));assert.deepEqual(submissionCase.log,[["list","run"]]);assert.equal(submissionCase.result,null);
let preSubmitCancelled=false; const preSubmitCalls=[];
const preSubmitResult=await runRequeueLifecycle({current:()=>{if(preSubmitCancelled)throw Error("cancelled")},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>record,loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async run=>{preSubmitCalls.push(["list",run]);return {handoffs:[exact]}},matchHandoff:matchingNextSceneHandoff,claimHandoff:async (run,h)=>preSubmitCalls.push(["claim",run,h.handoff_id]),prepareResume:async (run,h)=>{preSubmitCalls.push(["prepare",run,h.handoff_id]);preSubmitCancelled=true},submit:async()=>preSubmitCalls.push(["submit"]),release:async (h,run)=>preSubmitCalls.push(["release",run,h.handoff_id])});
assert.equal(preSubmitResult.kind,"cancelled");assert.deepEqual(preSubmitCalls,[["list","run"],["claim","run",exact.handoff_id],["prepare","run",exact.handoff_id],["release","run",exact.handoff_id]]);
const disabledCalls=[];
await assert.rejects(runRequeueLifecycle({current:()=>{throw Error("disabled")},waitSafe:async()=>disabledCalls.push("wait"),cleanup:async()=>disabledCalls.push("cleanup"),resolveRun:async()=>{disabledCalls.push("resolve");return record},loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async()=>{disabledCalls.push("list");return {handoffs:[exact]}},matchHandoff:matchingNextSceneHandoff,claimHandoff:async()=>disabledCalls.push("claim"),prepareResume:async()=>disabledCalls.push("prepare"),submit:async()=>disabledCalls.push("submit"),release:async()=>disabledCalls.push("release")}));
assert.deepEqual(disabledCalls,[]);
let enabledAfterSubmit=true; let resolveSubmitted, submitStarted; const startedSubmit=new Promise(resolve=>submitStarted=resolve); const postSubmitLog=[];
const postSubmitWork=runRequeueLifecycle({current:()=>{if(!enabledAfterSubmit)throw Error("disabled")},waitSafe:async()=>{},cleanup:async()=>{},resolveRun:async()=>record,loadCheckpoint:async()=>({revision:record.sourceRevision,metadata_sha256:record.checkpointSha}),listHandoffs:async()=>({handoffs:[exact]}),matchHandoff:matchingNextSceneHandoff,claimHandoff:async()=>postSubmitLog.push("claim"),prepareResume:async()=>postSubmitLog.push("prepare"),submit:async()=>{postSubmitLog.push("submit");submitStarted();return new Promise(resolve=>resolveSubmitted=resolve)},release:async()=>postSubmitLog.push("release")});
await startedSubmit; enabledAfterSubmit=false; resolveSubmitted({prompt_id:"accepted-after-disable-123"}); const postSubmitResult=await postSubmitWork;
await classifySubmissionOutcome({outcome:postSubmitResult.submission,accepted:async id=>finalizeAcceptedSubmission({runName:postSubmitResult.runName,handoffId:postSubmitResult.handoff.handoff_id,promptId:id,transitionQueued:async (...x)=>postSubmitLog.push(["queued",...x]),trackContinuation:(...x)=>postSubmitLog.push(["track",...x])}),rejected:async()=>postSubmitLog.push("rejected"),uncertain:async()=>postSubmitLog.push("uncertain")});
assert.deepEqual(postSubmitLog,["claim","prepare","submit",["queued","run",exact.handoff_id,"queued","accepted-after-disable-123"],["track","run",exact.handoff_id,"accepted-after-disable-123"]]);
const earlyEvents=[]; const earlyTracker=createContinuationTracker({transition:async (...x)=>earlyEvents.push(x)});
assert.equal(await earlyTracker.started("prompt-race"),false); await earlyTracker.track("run","handoff","prompt-race"); assert.deepEqual(earlyEvents,[["run","handoff","consumed"]]); assert.equal(earlyTracker.current(),null);
await earlyTracker.started("other-prompt"); await earlyTracker.track("run","handoff","real-prompt"); assert.equal(earlyTracker.current().promptId,"real-prompt"); await earlyTracker.started("real-prompt"); await earlyTracker.started("real-prompt"); assert.equal(earlyEvents.filter(x=>x[1]==="handoff").length,2);
console.log("top-level requeue coordinator lifecycle: ok");
