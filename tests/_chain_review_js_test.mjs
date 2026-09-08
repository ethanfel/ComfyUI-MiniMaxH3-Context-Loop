import assert from "node:assert/strict";
import fs from "node:fs";
import {
    acceptedPreviewDisposition,
    applyCheckpointRevisionSeeds,
    applyCheckpointRevisionSet,
    applyReviewEdit,
    checkpointRevisionChain,
    checkpointResumeOptions,
    reviewCountdown,
    reviewDuration,
    reviewDurationText,
    reviewLocalDeadline,
    reviewPlanScenePrompt,
    reviewSeed,
} from "../web/h3_chain_review_core.mjs";

const acceptedPin = {
    runName: "project", clipIndex: 5, token: "batch", revision: "a".repeat(32),
};
assert.equal(acceptedPreviewDisposition(acceptedPin, {
    run_name: "project", clip_index: 4, token: "older",
}), "ignore", "an earlier scene cannot replace an accepted preview");
assert.equal(acceptedPreviewDisposition(acceptedPin, {
    run_name: "project", clip_index: 5, token: "batch",
}), "hold", "the accepted batch keeps its selected preview pinned");
assert.equal(acceptedPreviewDisposition(acceptedPin, {
    run_name: "project", clip_index: 5, token: "new-run",
}), "release", "a new run of the same scene may replace the old preview");
assert.equal(acceptedPreviewDisposition(acceptedPin, {
    run_name: "project", clip_index: 6, token: "next",
}), "release", "the next scene releases the accepted preview pin");

assert.equal(reviewSeed("18446744073709551615"), "18446744073709551615");
assert.throws(() => reviewSeed("18446744073709551616"), /uint64/);
assert.deepEqual(reviewDuration("15"), {seconds: 15, length: 362});
assert.equal(reviewDurationText(362), "15.083333");
for (const length of [5, 22, 39, 56, 73, 362, 3592]) {
    assert.equal(reviewDuration(reviewDurationText(length)).length, length);
}
assert.throws(() => reviewDuration("0"), /positive/);

assert.equal(reviewPlanScenePrompt({shots: [
    {id: "one", prompt: ["First."]},
    {id: "two", prompt: ["Second.", "", "CAMERA: Wide."]},
]}, 2, "two"), "Second.\n\nCAMERA: Wide.");
assert.equal(reviewPlanScenePrompt({shots: [
    {id: "two", prompt: ["Moved second."]},
    {id: "one", prompt: ["Moved first."]},
]}, 2, "two"), "Moved second.", "scene id wins if the Plan was reordered");
assert.equal(reviewPlanScenePrompt({shots: []}, 1, "missing"), null);

const plan = {
    prompt_prefix: ["Keep identity."],
    shots: [
        {id: "one", prompt: ["Old one."], seed: "1"},
        {id: "two", prompt: ["Old two."], seed: "2"},
    ],
};
applyReviewEdit(
    plan, 2, "New two.\n\nCAMERA: Close-up.", "9007199254740993", 56,
    "a simple plain-language idea",
);
assert.deepEqual(plan.shots[0].prompt, ["Old one."]);
assert.deepEqual(plan.shots[1].prompt, ["New two.", "", "CAMERA: Close-up."]);
assert.equal(plan.shots[1].seed, "9007199254740993");
assert.equal(plan.shots[1].length, 56);
assert.equal(plan.shots[1].basic_prompt, "a simple plain-language idea");
assert.equal(plan.shots[0].basic_prompt, undefined,
    "an untouched scene must not gain a basic_prompt field");
applyReviewEdit(plan, 1, "", "3");
assert.deepEqual(plan.shots[0].prompt, [""]);
assert.equal(plan.shots[0].seed, "3");
assert.throws(
    () => applyReviewEdit({shots: [{prompt: [""]}]}, 1, "", "4"),
    /scene prompt or shared prompt/i,
);

assert.deepEqual(reviewCountdown(130, 100_000), {seconds: 30, text: "0:30"});
assert.deepEqual(reviewCountdown(100, 100_001), {seconds: 0, text: "0:00"});
assert.equal(reviewCountdown(null, 0), null);
assert.equal(reviewLocalDeadline(null, 100, 100_000), null);
assert.equal(reviewLocalDeadline(undefined, 100, 100_000), null);
assert.equal(reviewLocalDeadline("", 100, 100_000), null);
assert.equal(reviewLocalDeadline(130, 100, 100_000), 130);

assert.deepEqual(checkpointResumeOptions([
    {scene: 2, resume_scene: 3, scene_id: "second", ready: true,
        video: {filename: "second.mp4"}},
    {scene: 1, resume_scene: 2, scene_id: "first", ready: true,
        partial_video: {filename: "partial.mp4"}},
    {scene: 3, resume_scene: 4, scene_id: "final", ready: true},
    {scene: 1, resume_scene: 2, scene_id: "broken", ready: false},
], 3), [
    {savedScene: 1, resumeScene: 2, sceneId: "first", video: null,
        partialVideo: {filename: "partial.mp4"}},
    {savedScene: 2, resumeScene: 3, sceneId: "second",
        video: {filename: "second.mp4"}, partialVideo: null},
]);

const revisionA = "a".repeat(32);
const revisionB = "b".repeat(32);
const revisionC = "c".repeat(32);
assert.deepEqual(checkpointRevisionChain([
    {scene: 1, revision: revisionA, active: false, ready: true,
        created_at: "2026-08-14T09:00:00", seed: "11", size_bytes: 1024},
    {scene: 1, revision: revisionB, active: true, ready: true,
        created_at: "2026-08-14T10:00:00", seed: "12", size_bytes: 2048},
    {scene: 2, revision: revisionC, active: true, ready: true,
        created_at: "2026-08-14T11:00:00", seed: "13", size_bytes: 4096},
    {scene: 2, revision: "invalid", active: false, ready: true},
], 3), [
    {scene: 1, revisions: [
        {scene: 1, sceneId: "clip_0001", revision: revisionB, active: true,
            createdAt: "2026-08-14T10:00:00", seed: "12", sizeBytes: 2048,
            promptPreview: "", video: null},
        {scene: 1, sceneId: "clip_0001", revision: revisionA, active: false,
            createdAt: "2026-08-14T09:00:00", seed: "11", sizeBytes: 1024,
            promptPreview: "", video: null},
    ]},
    {scene: 2, revisions: [
        {scene: 2, sceneId: "clip_0002", revision: revisionC, active: true,
            createdAt: "2026-08-14T11:00:00", seed: "13", sizeBytes: 4096,
            promptPreview: "", video: null},
    ]},
]);
assert.deepEqual(checkpointRevisionChain([
    {scene: 2, revision: revisionC, active: true, ready: true},
], 3), [], "a recoverable chain must include every predecessor scene");

const recoveredPlan = applyCheckpointRevisionSet({
    prompt_prefix: ["new prefix"],
    shots: [
        {id: "one", prompt: ["new one"], length: 362, steps: 8, seed: "1",
            context_length: 39, audio_context_length: 44},
        {id: "two", prompt: ["new two"], length: 362, steps: 8, seed: "2",
            context_length: 39, audio_context_length: 44},
    ],
}, [
    {scene: 1, scene_id: "old_one", scene_prompt: "old one", seed: "101",
        raw_frames: 345, steps: 6, prompt_prefix: "old prefix", context_length: 0,
        audio_context_length: 33},
    {scene: 2, scene_id: "old_two", scene_prompt: "old two", seed: "102",
        raw_frames: 328, steps: 7, prompt_prefix: "old prefix"},
]);
assert.deepEqual(recoveredPlan.prompt_prefix, ["old prefix"]);
assert.deepEqual(recoveredPlan.shots, [
    {id: "old_one", prompt: ["old one"], length: 345, steps: 6, seed: "101",
        context_length: 0, audio_context_length: 33},
    {id: "old_two", prompt: ["old two"], length: 328, steps: 7, seed: "102"},
]);

const activatedAlternate = applyCheckpointRevisionSet({
    prompt_prefix: ["current prefix"],
    shots: [
        {id: "one", prompt: ["current one"], length: 362, steps: 8,
            seed: "1", lora_route: "d", prompt_seed_mode: "randomize"},
        {id: "two", prompt: ["current two"], length: 362, steps: 8,
            seed: "2", context_spatial_proxy: "rgb_5_6"},
    ],
}, [
    {scene: 1, scene_id: "old_one", scene_prompt: "{template|one}",
        effective_scene_prompt: "executed one", seed: "101", raw_frames: 345,
        steps: 6, prompt_prefix: "older prefix", context_length: 0,
        audio_context_length: 0, continuation_mode: "guide",
        video_blend_frames: 0, source_reference: "off",
        generated_continuity: "off", source_audio_target: "off",
        lora_route: "a", prompt_seed_mode: "fixed", prompt_seed: "9001"},
    {scene: 2, scene_id: "old_two", scene_prompt: "{template|two}",
        effective_scene_prompt: "executed two", seed: "102", raw_frames: 328,
        steps: 7, prompt_prefix: "tip prefix", context_length: 39,
        audio_context_length: 39, visual_context_source: "old_one",
        continuation_mode: "masked_av", video_blend_frames: 2,
        context_spatial_proxy: "latent_5_6", source_reference: "on",
        generated_continuity: "on", source_audio_target: "locked",
        prompt_seed_mode: "randomize"},
], {useEffectivePrompts: true, useTipSharedPrompt: true});
assert.deepEqual(activatedAlternate.prompt_prefix, ["tip prefix"]);
assert.deepEqual(activatedAlternate.shots, [
    {id: "old_one", prompt: ["executed one"], length: 345, steps: 6,
        seed: "101", context_length: 0, audio_context_length: 0,
        continuation_mode: "guide", video_blend_frames: 0,
        source_reference: "off", generated_continuity: "off",
        source_audio_target: "off", lora_route: "a",
        prompt_seed_mode: "fixed", prompt_seed: "9001"},
    {id: "old_two", prompt: ["executed two"], length: 328, steps: 7,
        seed: "102", context_length: 39, audio_context_length: 39,
        visual_context_source: "old_one", continuation_mode: "masked_av",
        video_blend_frames: 2, context_spatial_proxy: "latent_5_6",
        source_reference: "on", generated_continuity: "on",
        source_audio_target: "locked", prompt_seed_mode: "randomize"},
]);

const restoredComposition = applyCheckpointRevisionSet({
    prompt_prefix: ["keep"],
    shots: [
        {id:"one", prompt:["one"], length:39, steps:8, seed:"1"},
        {id:"two", prompt:["two"], length:39, steps:8, seed:"2"},
        {id:"three", prompt:["three"], length:39, steps:8, seed:"3"},
    ],
}, [{
    scene:3, scene_id:"three", scene_prompt:"three", seed:"303",
    raw_frames:39, steps:9, prompt_prefix:"keep", context_length:39,
    visual_context_source:"one", visual_context_lead_source:"two",
    visual_context_start_frame:2, visual_context_lead_frames:5,
    visual_context_lead_start_frame:3, video_blend_frames:0,
}]);
assert.equal(restoredComposition.shots[2].visual_context_source, "one");
assert.equal(restoredComposition.shots[2].visual_context_start_frame, 2);
assert.equal(restoredComposition.shots[2].visual_context_lead_source, "two");
assert.equal(restoredComposition.shots[2].visual_context_lead_frames, 5);
assert.equal(restoredComposition.shots[2].visual_context_lead_start_frame, 3);

const restoredSameSource = applyCheckpointRevisionSet({
    prompt_prefix: ["keep"],
    shots: [
        {id:"one", prompt:["one"], length:39, steps:8, seed:"1"},
        {id:"two", prompt:["two"], length:39, steps:8, seed:"2"},
        {id:"three", prompt:["three"], length:39, steps:8, seed:"3"},
    ],
}, [{
    scene:3, scene_id:"three", scene_prompt:"three", seed:"303",
    raw_frames:39, steps:9, prompt_prefix:"keep", context_length:39,
    visual_context_source:"one", visual_context_lead_source:"one",
    visual_context_lead_frames:5, video_blend_frames:0,
}]);
assert.equal(restoredSameSource.shots[2].visual_context_source, "one");
assert.equal(restoredSameSource.shots[2].visual_context_lead_source, "one");

const restoredBuilder = applyCheckpointRevisionSet({
    prompt_prefix:["keep"],
    shots:[
        {id:"one", prompt:["one"], length:90, steps:8, seed:"1"},
        {id:"two", prompt:["two"], length:90, steps:8, seed:"2"},
        {id:"three", prompt:["three"], length:90, steps:8, seed:"3",
            visual_context_source:"one"},
    ],
}, [{
    scene:3, scene_id:"three", scene_prompt:"three", seed:"303",
    raw_frames:90, steps:9, prompt_prefix:"keep", context_length:39,
    visual_context_blocks:[
        {source:"one", frames:5},
        {source:"two", frames:12, start_frame:0},
        {source:"one", frames:22},
    ], video_blend_frames:0,
}]);
assert.deepEqual(restoredBuilder.shots[2].visual_context_blocks, [
    {source:"one", frames:5},
    {source:"two", frames:12, start_frame:0},
    {source:"one", frames:22},
]);
assert.equal(
    Object.hasOwn(restoredBuilder.shots[2], "visual_context_source"), false,
);

const seedOnlyPlan = applyCheckpointRevisionSeeds({
    prompt_prefix: ["keep prefix"],
    shots: [
        {id: "one", prompt: ["keep one"], length: 362, steps: 8, seed: "1"},
        {id: "two", prompt: ["keep two"], length: 345, steps: 6, seed: "2"},
    ],
}, [
    {scene: 1, seed: "101", scene_prompt: "ignored", raw_frames: 5, steps: 1},
    {scene: 2, seed: "102", scene_prompt: "ignored", raw_frames: 5, steps: 1},
]);
assert.deepEqual(seedOnlyPlan, {
    prompt_prefix: ["keep prefix"],
    shots: [
        {id: "one", prompt: ["keep one"], length: 362, steps: 8, seed: "101"},
        {id: "two", prompt: ["keep two"], length: 345, steps: 6, seed: "102"},
    ],
});

const reviewSource = fs.readFileSync(
    new URL("../web/h3_chain_review_final.js", import.meta.url),
    "utf8",
);
const backendSource = fs.readFileSync(
    new URL("../chain_nodes.py", import.meta.url),
    "utf8",
);
assert.match(reviewSource, /minimax_h3_context_loop\/review/);
assert.match(reviewSource, /minimax_h3_context_loop_review_resolved/);
assert.match(reviewSource, /item\.name === "scene_range"/);
assert.match(reviewSource, /rangeWidget\.value = range/);
assert.match(reviewSource, /_h3QueuedReview/);
assert.match(reviewSource, /setInterval[\s\S]*fetchPending/);
assert.match(reviewSource, /addEventListener\("status", fetchPending\)/);
assert.match(reviewSource, /async nodeCreated\(node\)/);
assert.match(reviewSource, /gates\.length === 1/);
assert.match(reviewSource, /data\?\.run_name/);
assert.match(reviewSource, /mountedReviewNodes/);
assert.match(reviewSource, /split\(\/\[\.:\]\//);
assert.match(reviewSource, /No pending review is available for this project yet/);
assert.match(reviewSource, /button\.disabled = false/);
assert.doesNotMatch(
    reviewSource,
    /await fetchPending\(\);\s*return;/,
    "an action click must continue after recovering its pending token",
);
assert.match(reviewSource, /"pointerdown", "pointerup", "mousedown", "mouseup", "click"/);
assert.match(reviewSource, /preview_revision/);
assert.match(reviewSource, /sameToken/);
assert.match(
    reviewSource,
    /if \(!sameToken\)[\s\S]*setTimeout\(\(\) => void refreshResumeOptions\(\{automatic: true\}\), 0\)/,
    "a newly persisted review scene must refresh checkpoint history",
);
assert.match(
    reviewSource,
    /data\.action === "approve" \|\| data\.action === "stop"[\s\S]*setTimeout\(\(\) => void refreshResumeOptions\(\{automatic: true\}\), 0\)/,
    "final approval must refresh checkpoint history",
);
assert.match(reviewSource, /Checkpoint history/);
assert.match(reviewSource, /const refreshToken = \+\+resumeRefreshToken/);
assert.match(reviewSource, /if \(refreshToken !== resumeRefreshToken\) return/);
assert.match(reviewSource, /candidate_revision/);
assert.match(reviewSource, /Generate next candidate/);
assert.match(reviewSource, /Accept now & continue/);
assert.match(reviewSource, /review_each_candidate/);
assert.match(reviewSource, /review-candidate-batch/);
assert.match(reviewSource, /candidate_batch_active/);
assert.match(reviewSource, /function reviewRunName\(planNode\)/);
assert.match(reviewSource, /item\.name === "project_assets"/);
assert.match(
    reviewSource,
    /widgetByName\(manager, "run_name"\)/,
    "Project Assets must provide the authoritative run identity for review routing",
);
assert.match(
    reviewSource,
    /const matchingRun = gates\.filter\([\s\S]*reviewRunName\(findUpstreamNode\(item, PLAN_NAMES\)\) === expectedRun/,
    "fallback routing must match Project Assets' authoritative run identity",
);
assert.match(reviewSource, /Pause candidate run/);
assert.match(reviewSource, /\/api\/jobs\/\$\{encodeURIComponent\(execution\.promptId\)\}\/cancel/);
assert.match(reviewSource, /activate_only: true/);
assert.match(reviewSource, /action: "finalize"/);
assert.match(reviewSource, /await app\.queuePrompt\(0, 1\)/);
assert.doesNotMatch(reviewSource, /fetchApi\("\/interrupt"/);
assert.match(reviewSource, /candidate_revisions/);
assert.match(reviewSource, /keptCandidateRevisions/);
assert.match(reviewSource, /h3r-candidate-dots/);
assert.match(reviewSource, /moveCandidate/);
assert.match(reviewSource, /exact video and audio continuation tensors/);
assert.match(reviewSource, /function setReviewVideo/);
assert.match(reviewSource, /video\.dataset\.source === source/);
assert.match(reviewSource, /const carriedActiveCandidateRevision/);
assert.match(reviewSource, /setReviewVideo\(candidate\.video, sameCandidate\)/);
assert.match(reviewSource, /pinAcceptedPreview\(submittedReview, submittedCandidate\)/);
assert.match(reviewSource, /acceptedDisposition === "ignore"/);
assert.match(reviewSource, /if \(acceptedDisposition === "hold"\) showAcceptedPreview\(\)/);
const showCandidateStart = reviewSource.indexOf("function showCandidate");
const showCandidateSource = reviewSource.slice(
    showCandidateStart,
    reviewSource.indexOf("function renderCandidateDots", showCandidateStart),
);
assert.doesNotMatch(
    showCandidateSource,
    /video\.load\(\)/,
    "candidate progress refreshes must not reload the active preview",
);
assert.match(reviewSource, /Candidate \$\{candidate\.number\}\/\$\{current\.candidate_count\}/);
assert.match(
    reviewSource,
    /checkpointRevisionChain\(\s*checkpointRevisions, planClipCount \+ 1/,
    "checkpoint history must include the final scene, even though it cannot be a resume predecessor",
);
assert.match(reviewSource, /revision\.scene < selectedResumeScene/);
assert.match(reviewSource, /data\.final_video \?\? data\.partial_video/);
assert.match(reviewSource, /final assembled video/);
assert.match(reviewSource, /Duration \(s\)/);
assert.match(reviewSource, /body\.length/);
const submitStart = reviewSource.indexOf("async function submit");
const submitSource = reviewSource.slice(
    submitStart,
    reviewSource.indexOf("node._h3ReviewHandler", submitStart),
);
assert.match(submitSource, /const submittedToken = submittedReview\.token/);
assert.match(submitSource, /const submittedIndex = submittedReview\.clip_index/);
assert.match(submitSource, /promptEditedInGate[\s\S]*planScenePrompt/);
assert.match(submitSource, /reviewPromptEditorEnabled\(\)/);
assert.match(submitSource, /planScenePrompt/);
assert.match(submitSource, /token: submittedToken/);
assert.match(submitSource, /scene_prompt: submittedPrompt/);
assert.doesNotMatch(submitSource, /processRequeue\(/,
    "Review approval must not bypass Loop End; top-level requeue is driven by the Loop End terminal coordinator");
assert.match(
    submitSource,
    /updatePlan\(\s*node, submittedIndex, acceptedPrompt, body\.seed, body\.length,\s*acceptedBasicPrompt\)/,
);
assert.match(reviewSource, /publishCompanionPrompt/);
assert.match(reviewSource, /publishCompanionBasicPrompt/);
assert.match(reviewSource, /publishPlanCompanionScene/);
assert.match(reviewSource, /basic_prompt: submittedBasicPrompt/);
assert.match(reviewSource, /_h3PromptCompanionSetBasicPrompt/);
assert.match(reviewSource, /_h3PromptCompanionSetScenePrompt/);
assert.match(reviewSource, /reviewDurationText\(data\.raw_frames\)/);
assert.match(reviewSource, /h3r-video-panel/);
assert.match(reviewSource, /checkpoint-revisions\/restore/);
assert.match(reviewSource, /checkpoint-revisions\/delete-preview/);
assert.match(reviewSource, /checkpoint-revisions\/delete/);
assert.match(reviewSource, /snapshot: preview\.snapshot/);
const checkpointLoadStart = reviewSource.indexOf(
    'loadResume.addEventListener("click"',
);
const checkpointLoadSource = reviewSource.slice(
    checkpointLoadStart,
    reviewSource.indexOf("function stopCountdown", checkpointLoadStart),
);
assert.match(checkpointLoadSource, /include_assets: "false"/);
assert.match(checkpointLoadSource, /let restoredPolicyInputs = runBody\.policy_inputs/);
assert.match(checkpointLoadSource, /restoredPolicyInputs = body\.policy_inputs/);
assert.match(
    checkpointLoadSource,
    /restoreSavedPlanInputs\(\s*node, runBody\.plan_inputs, restoredPolicyInputs\)/,
);
assert.match(reviewSource, /planNode, policyInputs, inputs/);
assert.match(reviewSource, /refreshRestoredPlanEditors\(planNode\)/);
assert.match(
    backendSource,
    /"policy_inputs": archive_policy_inputs\(\{\s*"compatibility": compatibility/,
);
assert.match(checkpointLoadSource, /if \(selections\.length\)/);
assert.ok(
    checkpointLoadSource.indexOf("restoreSavedPlanInputs")
        < checkpointLoadSource.indexOf("prepareResume"),
    "the complete saved Plan must be restored before Loop Start is armed",
);
assert.match(reviewSource, /Permanently delete scene/);
assert.match(reviewSource, /Restore & load/);
assert.match(reviewSource, /h3r-video-grip/);
assert.match(reviewSource, /h3_chain_review_video_height/);
assert.match(reviewSource, /h3_chain_review_prompt_height/);
assert.match(reviewSource, /h3r-prompt-grip/);
assert.match(reviewSource, /function setPromptHeight\(/);
assert.match(reviewSource, /promptGrip\.hidden = !enabled/);
assert.match(reviewSource, /MiniMaxH3ContexLoop\.ReviewGate\.PromptEditor/);
assert.match(reviewSource, /Enable prompt editing inside Review Gate/);
assert.match(reviewSource, /defaultValue: false/);
assert.match(reviewSource, /Prompt editing in Review Gate is disabled by default in 0\.5/);
assert.match(reviewSource, /Scene Prompt Editor or Rich Scene Prompt Editor/);
assert.match(reviewSource, /promptLabel\.hidden = !enabled/);
assert.match(reviewSource, /promptNotice\.hidden = enabled/);
assert.match(reviewSource, /_h3ReviewApplyLayout/);
assert.match(reviewSource, /PROJECT_ASSET_MANAGER_NODE/);
assert.match(reviewSource, /function reviewRunName\(planNode\)/);
assert.match(reviewSource, /widgetByName\(manager, "run_name"\)/);
assert.match(reviewSource, /reviewRunName\(findUpstreamNode\(item, PLAN_NAMES\)\)/);
assert.doesNotMatch(reviewSource, /function planRunNameTrusted/);
assert.match(reviewSource, /nodeType\.prototype\.onConfigure/);
assert.match(reviewSource, /setPointerCapture/);
assert.match(reviewSource, /visualHeight \/ layoutHeight/);
assert.match(reviewSource, /videoPanel\.offsetHeight, true/);
assert.doesNotMatch(reviewSource, /\/h3_motion_context\/review/);
const fallbackStart = reviewSource.indexOf("function reviewFallbackNode");
const fallbackSource = reviewSource.slice(fallbackStart, reviewSource.indexOf("function routeReview", fallbackStart));
assert.match(fallbackSource, /matchingRun\.length === 1\) return matchingRun\[0\];[\s\S]*data\?\.durable === true\) return null;[\s\S]*matchingLeaf[\s\S]*gates\.length === 1/,
    "durable recovery may use only an authoritative run match, not leaf or singleton fallback");
const routeStart = reviewSource.indexOf("function routeReview(data)");
const routeSource = reviewSource.slice(routeStart, reviewSource.indexOf("function routeReviewResolved", routeStart));
assert.match(routeSource, /data\?\.durable !== true[\s\S]*Pending token/,
    "expected unrelated durable inventory must not warn on every poll");
const reviewHandlerStart = reviewSource.indexOf("node._h3ReviewHandler =");
const reviewHandlerSource = reviewSource.slice(reviewHandlerStart);
assert.match(reviewHandlerSource, /const candidateBatchComplete = Boolean\(current\?\.candidate_generation_complete\) \|\|[\s\S]*current\.candidates\.length >=[\s\S]*candidate_count/);
assert.match(reviewHandlerSource, /sameToken && current\?\.actionable !== false &&[\s\S]*candidate_count\) > 1 && candidateBatchComplete &&[\s\S]*!current\?\.candidate_batch_command_pending/);
assert.match(reviewHandlerSource, /candidateBatchComplete &&[\s\S]*root\.classList\.remove\("h3r-busy"\);[\s\S]*setActionsEnabled\(true\)/);
assert.match(reviewSource, /activeResumeExecutionIds = new Set\(\)/);
assert.match(reviewSource, /automatic && activeResumeExecutionIds\.size > 0[\s\S]*deferredAutomaticResumeRefresh = true/);
assert.match(reviewSource, /resumeRefreshPromise[\s\S]*queuedExplicitResumeRefresh[\s\S]*queuedAutomaticResumeRefresh/);
assert.match(reviewSource, /api\.addEventListener\("execution_start", onResumeExecutionStart\)/);
assert.match(reviewSource, /api\.addEventListener\("execution_success", onResumeExecutionTerminal\)/);
assert.match(reviewSource, /api\.addEventListener\("execution_error", onResumeExecutionTerminal\)/);
assert.match(reviewSource, /api\.addEventListener\("execution_interrupted", onResumeExecutionTerminal\)/);
assert.match(reviewSource, /nextResumeChoices[\s\S]*resumeSelect\.replaceChildren\(\)/,
    "resume UI replacement occurs only after the checkpoint response is parsed");
assert.match(reviewSource, /if \(enabled && current\?\.candidate_batch_active\) \{[\s\S]*retryButton\.disabled = true;[\s\S]*rerollButton\.disabled = true;[\s\S]*stopButton\.disabled = true;/);

console.log("H3 Chain Review editor helpers: ok");
