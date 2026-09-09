import assert from "node:assert/strict";
import {StudioBranches, branchSelectionJson, branchRequestPath} from "../web/h3_working_branches.mjs";

const id = "a".repeat(32), fresh = "b".repeat(32);
const authoring = {plan_json:JSON.stringify({shots:[{id:"one",prompt:"keep this",seed:"18446744073709551614"}],
    chapters:[{id:"one",start_scene_id:"one",text:"lyrics"}]}),width:1344};
function fixture() {
    const events = [];
    const controller = new StudioBranches({selected:"main", capture:() => structuredClone(authoring),
        flush:async () => events.push("flush"), changed() {},
        apply:async record => events.push(`apply:${record.id}`),
        request:async body => {
            events.push(body.action);
            if (body.action === "list") return {branches:[{id:"main",revision:"1"},{id,revision:"2"}],default_branch:"main"};
            if (body.action === "save") return {id:body.branch_id,revision:"3",authoring:body.authoring};
            if (body.action === "load") return {id:body.branch_id,authoring:structuredClone(authoring)};
            if (body.action === "create") {
                assert.deepEqual(body.authoring, authoring);
                assert.equal(body.through_scene, 0);
                return {id:fresh,revision:"4",authoring:structuredClone(authoring)};
            }
            if (body.action === "default") return {default_branch:body.branch_id};
            throw new Error("Unexpected action");
        }});
    return {controller,events};
}
{
    const {controller,events} = fixture();
    await controller.refresh("demo");
    await controller.switchTo(id);
    assert.deepEqual(events, ["list","flush","save","load",`apply:${id}`]);
    assert.equal(controller.selected,id);
    assert.equal(controller.defaultBranch,"main");
    await controller.create("Empty");
    assert.equal(controller.selected,fresh);
    await controller.makeDefault();
    assert.equal(controller.defaultBranch,fresh);
    assert.equal(controller.records.length,3);
}
{
    const {controller,events} = fixture();
    await controller.refresh("demo");
    controller.flush = async () => { throw new Error("Editorial conflict"); };
    await controller.switchTo(id);
    assert.equal(controller.selected,"main");
    assert.equal(controller.error,"Editorial conflict");
    assert.deepEqual(events,["list"]);
}
{
    const {controller,events} = fixture();
    await controller.refresh("demo");
    let finish;
    controller.request = async body => {
        if (body.action === "save") return {revision:"3"};
        if (body.action === "load") return new Promise(resolve => { finish=resolve; });
        return {branches:[],default_branch:"main"};
    };
    const switching = controller.switchTo(id);
    for (let i=0;i<10 && !finish;i++) await Promise.resolve();
    assert.ok(finish);
    await controller.refresh("another");
    finish({id,authoring});
    await switching;
    assert.equal(controller.selected,"main");
    assert.ok(!events.some(event => event.startsWith("apply:")), "Late response must not change another project");
    assert.match(controller.error,/Project changed/);
}
const selection = {run_name:"demo",lineage:[{scene:1,revision:"f".repeat(32)}],output_mode:"workflow_local"};
assert.deepEqual(JSON.parse(branchSelectionJson(JSON.stringify(selection),id)),{...selection,_branch_id:id});
assert.equal(branchSelectionJson(JSON.stringify({...selection,_branch_id:id}),"main"), JSON.stringify(selection));
assert.equal(branchRequestPath("/test?run_name=demo",id),`/test?run_name=demo&branch_id=${id}`);
assert.throws(() => branchRequestPath("/test","../../unsafe"));
console.log("Working branch UI: save-before-switch, empty copy, default isolation, conflict/stale response and output scope pass");
