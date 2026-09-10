import assert from "node:assert/strict";
import {checkpointFinalCutContext, checkpointFinalCutAlternate} from "../web/h3_checkpoint_manager_core.mjs";

const a = "a".repeat(32), b = "b".repeat(32), c = "c".repeat(32), alt = "d".repeat(32);
const branch = "1".repeat(32), other = "2".repeat(32);
const selection = {lineage:[{scene:1,revision:a},{scene:2,revision:b}]};
const contexts = [
    {id:"main",name:"Original",lineage:[{scene:1,revision:c}],replacements:[]},
    {id:branch,name:"960x544",lineage:selection.lineage,replacements:[
        {scene:1,base_revision:a,alternate_revision:alt}]},
];
assert.equal(checkpointFinalCutContext(selection,contexts).id,branch);
assert.equal(checkpointFinalCutContext({...selection,final_cut_branch_id:"main"},contexts).id,"main");
assert.throws(()=>checkpointFinalCutContext({...selection,final_cut_branch_id:other},contexts),/unavailable/);
for (const bad of [null, false, 0, "", []]) {
    assert.throws(()=>checkpointFinalCutContext({...selection,final_cut_branch_id:bad},contexts),/Invalid final-cut branch/);
}
const duplicate = {...contexts[1],id:other,name:"Another cut"};
assert.throws(()=>checkpointFinalCutContext(selection,[...contexts,duplicate]),/multiple final-cut branches/);
assert.equal(checkpointFinalCutContext(selection,[...contexts,duplicate],branch).id,branch);
assert.equal(checkpointFinalCutContext({...selection,final_cut_branch_id:other},[...contexts,duplicate]).id,other);
assert.equal(checkpointFinalCutContext({lineage:[{scene:1,revision:alt}]},contexts).id,"main",
    "Unassigned historical paths keep their current namespace unless chosen explicitly");
const chapter = {...selection,output_scope:"chapter",scope_start_scene:2};
assert.equal(checkpointFinalCutContext(chapter,[contexts[0],{
    ...contexts[1],lineage:[{scene:1,revision:c},{scene:2,revision:b}]}]).id,branch);
const take = {scene:1,revision:alt,alternate_of_revision:a};
assert.ok(checkpointFinalCutAlternate(contexts[1],take,selection));
assert.ok(!checkpointFinalCutAlternate(contexts[0],take,selection));
assert.ok(!checkpointFinalCutAlternate(contexts[1],{...take,alternate_of_revision:b},selection));
assert.ok(!checkpointFinalCutAlternate(contexts[1],take,chapter),"Do not mark an ALT outside the output chapter");
assert.equal(checkpointFinalCutContext(selection,undefined),null,"No claims for older servers without final-cut inventory");
console.log("Final-cut resolution: exact path, current match, explicit choice, ambiguity, chapter scope and ALT identity pass");
