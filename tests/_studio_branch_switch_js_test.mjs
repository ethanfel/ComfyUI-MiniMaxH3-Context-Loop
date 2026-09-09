import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {matchingStudioCheckpoint, studioCheckpointSignature} from "../web/h3_chain_plan_studio_core.mjs";

const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const functions = ["refreshCheckpointsNow", "refreshTimelineCheckpoints", "updateTimelineCheckpointCard"];
const handlers = functions.map(name => {
    const match = source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`, "m"));
    assert.ok(match, name);
    return match[0];
}).join("\n");
const rows = [1, 2, 3].map(scene => ({id:`scene_${scene}`, deliveredFrames:100}));
const oldRecords = rows.map((row, index) => ({
    scene:index + 1, scene_id:row.id, delivered_frames:100, ready:true,
    revision:`old-${index}`, video:`old-${index}.mp4`,
}));
const cards = oldRecords.map((record, index) => {
    const classes = new Set(["h3studio-rendered"]);
    const card = {
        dataset:{sceneIndex:index, checkpointThumbnail:record.video},
        classes, classList:{toggle(name, on) { on ? classes.add(name) : classes.delete(name); }},
        querySelector() { return card.thumbnail; },
        prepend(image) { card.thumbnail = image; },
    };
    card.thumbnail = {remove() { card.thumbnail = null; }};
    return card;
});
const state = {
    disposed:false, checkpoints:new Map(oldRecords.map(record => [record.scene, record])),
    checkpointSignature:studioCheckpointSignature("run", oldRecords),
    checkpointError:"", checkpointToken:0, editorialEditEpoch:0,
    plan:{shots:rows}, timelineHost:{querySelectorAll:() => cards},
    view:"player", player:{paused:false, dataset:{source:"old-2.mp4"}},
    playerIndex:2, playerAudio:{dataset:{source:"old-2.wav"}},
};
let response = {checkpoints:[oldRecords[0], {...oldRecords[1], revision:"new-1"}]};
let panelRenders = 0;
const context = vm.createContext({
    state, Map, URLSearchParams, studioCheckpointSignature, matchingStudioCheckpoint,
    runName:() => "run", timing:() => ({shots:rows}),
    currentBranch:() => "main",
    api:{fetchApi:async () => ({ok:true, json:async () => response})},
    applyEditorialPayload:() => false, cacheStudioPresentation(){},
    renderStatus(){}, renderTimeline(){},
    renderPanel() { panelRenders += 1; },
    checkpointThumbnailUrl:(_index, record) => record?.video ?? "",
    playerCheckpoint:index => state.checkpoints.get(index + 1),
    videoUrl:video => video,
});
vm.runInContext(handlers, context);
await context.refreshCheckpointsNow();
assert.equal(state.checkpoints.size, 2);
assert.equal(state.checkpoints.get(2).revision, "new-1",
    "metadata-only changes refresh the actual checkpoint map");
assert.equal(cards[2].thumbnail, null, "inactive tail thumbnail is removed");
assert.equal(cards[2].classes.has("h3studio-rendered"), false,
    "inactive tail must not look rendered on the selected branch");
assert.equal(cards[2].dataset.checkpointThumbnail, undefined);
assert.equal(panelRenders, 1, "old video/audio is replaced even during playback");
assert.ok(cards[0].thumbnail, "unchanged selected clips keep their preview");
assert.equal(state.plan.shots.length, 3, "authored scene slots are not deleted");
response = {checkpoints:[]};
await context.refreshCheckpointsNow();
assert.equal(state.checkpoints.size, 0);
assert.ok(cards.every(card => card.thumbnail == null));
console.log("Studio branch switch: inactive thumbnails, playing media, shared-media revisions and Plan preservation pass");
