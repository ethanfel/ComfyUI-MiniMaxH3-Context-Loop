import assert from "node:assert/strict";
import {checkpointOutputSummary} from "../web/h3_checkpoint_manager_core.mjs";

const lineage = Array.from({length:10}, (_, index) => ({
    scene:index + 1, revision:String(index).repeat(32),
}));
const selected = {run_name:"demo", lineage, scope_start_scene:8, scope_end_scene:13};
const project = checkpointOutputSummary(JSON.stringify(selected));
assert.match(project, /Original checkpoints · demo · original branch through scene 10 \/ 99999999/);
assert.match(project, /scenes 1–10 \(10 clips; selected branch \+ earlier chapters\)/);
assert.doesNotMatch(project, /13/, "the planned chapter end is not an available output scene");
assert.match(project, /Clip and tab previews do not change this output/);
assert.match(project, /Set the processing range downstream/);

const chapter = {...selected, output_scope:"chapter", output_mode:"workflow_local"};
assert.match(checkpointOutputSummary(chapter), /scenes 8–10 \(3 clips; selected chapter only\)/);
assert.match(checkpointOutputSummary(chapter), /pinned to this workflow/);

const processing_source = {
    stage:"derope", profile_path:"demo/chapters/two/upscaled/recovered",
    branch:{lineage:lineage.filter(item => [1, 2, 8, 9].includes(item.scene))},
};
const processed = {...chapter, processing_source};
const before = JSON.stringify(processed);
const summary = checkpointOutputSummary(processed);
assert.match(summary, /DeRoPE checkpoints for scenes 8–9; Original fallback for scenes 10/);
assert.match(summary, /DeRoPE branch 88888888 \(demo\/chapters\/two\/upscaled\/recovered\)/);
assert.doesNotMatch(summary, /scenes 1–2/, "earlier chapter timing metadata is not a sent clip");
assert.equal(JSON.stringify(processed), before, "describing output cannot mutate serialized selection");
assert.equal(checkpointOutputSummary(before), summary);
const complete = {...processed, processing_source:{...processing_source, branch:{lineage}}};
assert.match(checkpointOutputSummary(complete), /DeRoPE checkpoints for scenes 8–10; no Original fallback needed/);
const sparse = {...selected, processing_source:{...processing_source,
    branch:{lineage:lineage.filter(item => [2, 4, 5, 6, 9].includes(item.scene))}}};
assert.match(checkpointOutputSummary(sparse), /DeRoPE checkpoints for scenes 2, 4–6, 9; Original fallback for scenes 1, 3, 7–8, 10/);

for (const empty of [undefined, null, "", "null"]) {
    assert.match(checkpointOutputSummary(empty), /No source selected/);
}
for (const invalid of ["{", {}, {...selected, lineage:[null]}, {...selected, lineage:[lineage[2]]},
    {...chapter, scope_start_scene:"bad"}, {...selected, output_scope:"bad"},
    {...selected, output_mode:"bad"}, {...processed, processing_source:{stage:"pixel_upscale"}},
    {...processed, processing_source:{...processing_source, branch:{lineage:{}}}},
    {...processed, processing_source:{...processing_source, branch:{lineage:[null]}}}]) {
    assert.match(checkpointOutputSummary(invalid), /saved output selection is invalid/);
}
console.log("Checkpoint output summary: exact source, branch, scope, DeRoPE fallback and preview distinction pass");
