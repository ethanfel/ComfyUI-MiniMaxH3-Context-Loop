import assert from "node:assert/strict";
import {applyContextTake} from "../web/h3_context_take_core.mjs";
import {
    duplicateShot, parsePlanJson, planToJson, renamePlanShot, safeShotId,
    sceneAudioContextSource, sceneAudioContextLeadSource,
    sceneVisualContextSource, sceneVisualContextLeadSource, sceneVisualContextBlocks,
} from "../web/h3_chain_plan_core.mjs";

const revision = "a".repeat(32);
for (const name of ["Street Ride", "  Street / Ride!  ", "Long Scene ".repeat(12)]) {
    const plan = parsePlanJson(planToJson({shots:[
        {id:name, prompt:"First scene"}, {id:"Middle", prompt:"Middle scene"},
        {id:"End", prompt:"Final scene", visual_context_source:name,
            visual_context_lead_source:name, audio_context_unlocked:true,
            audio_context_source:name, audio_context_lead_source:name},
    ]}));
    const canonical = safeShotId(name, "");
    const selected = applyContextTake(plan, 1, revision);
    assert.deepEqual(selected.shots[1].context_take, {source:canonical, revision});
    assert.equal(plan.shots[0].id, name, "Selecting a take must not rewrite the source scene");
    for (const source of [sceneVisualContextSource, sceneVisualContextLeadSource,
        sceneAudioContextSource, sceneAudioContextLeadSource]) {
        assert.equal(source(plan, 3), 1);
        const saved = structuredClone(plan);
        saved.shots[0].id = canonical;
        assert.equal(source(saved, 3), 1, "Old authored links resolve against saved canonical IDs");
    }
    plan.shots[2].context_take = {source:name, revision};
    plan.shots[2].visual_context_blocks = [
        {source:name, frames:17}, {source:"Middle", frames:22},
    ];
    for (const key of ["visual_context_source", "visual_context_lead_source"]) delete plan.shots[2][key];
    assert.equal(sceneVisualContextBlocks(plan, 3, 39)[0].source, 1);
    plan.shots[2].visual_context_source = name;
    plan.shots[2].visual_context_lead_source = name;
    plan.chapters = [{start_scene_id:name}];
    assert.equal(renamePlanShot(plan, 0, "Renamed scene").changed, true);
    assert.equal(plan.shots[0].id, "Renamed_scene");
    assert.equal(plan.chapters[0].start_scene_id, "Renamed_scene");
    assert.equal(plan.shots[2].context_take.source, "Renamed_scene");
    assert.equal(plan.shots[2].context_take.revision, revision);
    assert.equal(plan.shots[2].visual_context_blocks[0].source, "Renamed_scene");
    for (const field of ["visual_context_source", "visual_context_lead_source",
        "audio_context_source", "audio_context_lead_source"]) {
        assert.equal(plan.shots[2][field], "Renamed_scene");
    }
}

const spellingOnly = {shots:[{id:"Street Ride"}, {context_take:{source:"Street Ride", revision}}]};
assert.equal(renamePlanShot(spellingOnly, 0, "Street_Ride").changed, true);
assert.equal(spellingOnly.shots[0].id, "Street_Ride");
assert.equal(spellingOnly.shots[1].context_take.source, "Street_Ride");
assert.equal(renamePlanShot(spellingOnly, 0, "Street_Ride").changed, false);

const duplicate = {shots:[{id:"Street Ride"}, {id:"Next", visual_context_source:"Street Ride"}]};
duplicateShot(duplicate.shots, 0);
assert.equal(duplicate.shots[2].visual_context_source, "Street_Ride_copy",
    "A plain predecessor link follows the inserted copy, even with spaces in its old name");

const invalid = {shots:[{id:"Street_Ride"}, {id:"Middle"}, {id:"End"}]};
for (const source of [true, "missing scene", "!!!", 0, 3, "End"]) {
    invalid.shots[2].visual_context_source = source;
    assert.throws(() => sceneVisualContextSource(invalid, 3));
}
for (const source of [1, "1"]) {
    invalid.shots[2].visual_context_source = source;
    assert.equal(sceneVisualContextSource(invalid, 3), 1);
}
for (const source of [null, "", "previous", "immediate"]) {
    invalid.shots[2].visual_context_source = source;
    assert.equal(sceneVisualContextSource(invalid, 3), 2);
}
invalid.shots[1].id = "Street Ride";
invalid.shots[2].visual_context_source = "Street Ride";
assert.throws(() => sceneVisualContextSource(invalid, 3), /does not match/,
    "Never silently select a scene when normalized IDs collide");
console.log("Scene source names: canonical links, legacy spelling, rename, duplication and validation pass");
