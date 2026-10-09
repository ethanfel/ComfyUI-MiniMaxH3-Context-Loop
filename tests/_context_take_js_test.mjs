import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {applyContextTake} from "../web/h3_context_take_core.mjs";
import {duplicateShot, renamePlanShot, planToJson, parsePlanJson, safeShotId} from "../web/h3_chain_plan_core.mjs";
import {applyCheckpointRevisionSet} from "../web/h3_chain_review_core.mjs";

const revision = "a".repeat(32);
const plan = {prompt_prefix:["shared"], editorial:{keep:"final cut"}, shots:[
    {id:"one", seed:"18446744073709551615", prompt:["keep this"]},
    {id:"two", seed:"123", context_length:22, audio_context_length:39,
        continuation_mode:"guide", visual_context_blocks:[{source:"one", frames:22}],
        visual_context_start_frame:5, audio_context_source:"one", audio_context_start_frame:3},
    {id:"three"},
]};
const before = structuredClone(plan);
const selected = applyContextTake(plan, 1, revision);
assert.deepEqual(plan, before, "does not mutate the input Plan");
assert.deepEqual(selected.shots[0], before.shots[0]);
assert.deepEqual(selected.editorial, before.editorial);
assert.deepEqual(selected.shots[1], {id:"two", seed:"123", context_length:22,
    audio_context_length:39, continuation_mode:"guide", context_take:{source:"one", revision}});
assert.deepEqual(parsePlanJson(planToJson(selected)).shots[1].context_take, {source:"one", revision});
const cleared = applyContextTake(selected, 1);
assert.ok(!cleared.shots[1].context_take);
assert.deepEqual(selected.shots[1].context_take, {source:"one", revision});
assert.throws(() => applyContextTake(plan, 3, revision), /Add scene 4/);
assert.throws(() => applyContextTake(plan, 1, "latest"), /exact/);
assert.equal(applyContextTake({shots:[{}, {}]}, 1, revision).shots[1].context_take.source, 1);
assert.equal(applyContextTake({shots:[{id:"42"}, {}]}, 1, revision).shots[1].context_take.source, 1);

renamePlanShot(selected, 0, "renamed");
assert.deepEqual(selected.shots[1].context_take, {source:"renamed", revision});
duplicateShot(selected.shots, 0);
assert.deepEqual(selected.shots[2].context_take, {source:"renamed", revision});
assert.equal(selected.shots[2].visual_context_source, 1);
assert.equal(selected.shots[2].audio_context_source, 1);
assert.equal(selected.shots[2].audio_context_unlocked, true);
const copy = duplicateShot(selected.shots, 2);
assert.deepEqual(copy.context_take, selected.shots[2].context_take);
assert.equal(copy.visual_context_source, 1);

const saved = {scene:2, scene_id:"two", seed:"456", steps:8, raw_frames:39,
    scene_prompt:"saved prompt", prompt_prefix:"", context_take:{source:"one", revision}};
const restored = applyCheckpointRevisionSet(structuredClone(plan), [saved]);
assert.deepEqual(restored.shots[1].context_take, saved.context_take);
delete saved.context_take;
applyCheckpointRevisionSet(restored, [saved]);
assert.ok(!restored.shots[1].context_take, "restoring an unpinned recipe clears a stale pin");

// Execute the production preview resolver with only its network/UI edges stubbed.
const studio = readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const resolver = studio.slice(studio.indexOf("    const contextTakePreviews = new Map();"),
    studio.indexOf("    function renderAudioContextPanel("));
let run = "project-one", rendered = 0;
const requests = [];
const state = {plan:applyContextTake(plan, 1, revision), active:1, view:"context"};
// A saved pin may predate normalization of the source scene's display name.
state.plan.shots[0].id = "Street_Ride";
state.plan.shots[1].context_take.source = "Street Ride";
const api = {fetchApi(url) { return new Promise(resolve => requests.push({url,resolve})); }};
const {contextPlayerCheckpoint} = new Function("state", "runName", "api", "playerCheckpoint", "renderPanel", "safeShotId",
    resolver + "\nreturn {contextPlayerCheckpoint};")(
        state, () => run, api, () => ({video:"assigned"}), () => rendered++, safeShotId);
assert.deepEqual(contextPlayerCheckpoint(2), {video:"assigned"});
assert.equal(contextPlayerCheckpoint(0), null, "never substitute assigned media while the pin is loading");
assert.equal(contextPlayerCheckpoint(0), null);
assert.equal(requests.length, 1, "coalesce repeated preview reads");
assert.match(requests[0].url, /context_scene=1/);
assert.match(requests[0].url, new RegExp("context_revision=" + revision));
requests[0].resolve({ok:true, json:async () => ({context_take:{video:"alternate", audio:"alternate-audio"}})});
await new Promise(resolve => setTimeout(resolve, 0));
assert.deepEqual(contextPlayerCheckpoint(0), {video:"alternate", audio:"alternate-audio"});
assert.equal(rendered, 1);
run = "project-two";
assert.equal(contextPlayerCheckpoint(0), null, "preview caches cannot cross projects");
state.active = 2;
requests[1].resolve({ok:false, status:404, json:async () => ({error:"missing"})});
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(rendered, 1, "late preview responses cannot redraw a different scene");
state.active = 1;
assert.equal(contextPlayerCheckpoint(0), null, "missing exact preview never falls back");
console.log("Context takes: independent selection, reset, exact IDs, recovery, rename and duplication pass");
