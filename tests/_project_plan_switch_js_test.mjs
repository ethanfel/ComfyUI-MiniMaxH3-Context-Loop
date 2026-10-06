import assert from "node:assert/strict";
import {prepareProjectPlanSwitch, captureProjectPlan} from "../web/h3_project_plan_switch.mjs";
import {syncProjectAssetPlanRun} from "../web/h3_project_asset_sync_core.mjs";
import {StudioBranches} from "../web/h3_working_branches.mjs";

const widget = (node, name) => node.widgets.find(item => item.name === name);
const id = "a".repeat(32);
const planText = prompt => JSON.stringify({shared_prompt:["shared " + prompt],
    shots:[{id:"one", prompt:[prompt], seed:"18446744073709551615", basic_prompt:"draft " + prompt}],
    chapters:[{id:"chapter",start_scene_id:"one",text:"lyrics"}]});
function fixture({studio = false} = {}) {
    const graph = {_nodes:[], links:new Map([[1, {origin_id:1, target_id:2, origin_slot:0}]]),
        getNodeById(id) { return this._nodes.find(node => node.id === id); },setDirtyCanvas(){}};
    const manager = {id:1, graph, type:"MiniMaxH3ProjectAssetManager", properties:{}, inputs:[],
        outputs:[{name:"project_assets", links:[1]}],widgets:[{name:"run_name",value:"alpha"}]};
    const plan = {id:2, graph, type:"MiniMaxH3ChainPlanModern", properties:{},
        inputs:[{name:"project_assets",link:1}], widgets:Object.entries({run_name:"alpha",width:960,
            height:544,plan_json:planText("alpha"),base_seed:"18446744073709551615"})
            .map(([name,value])=>({name,value}))};
    graph._nodes.push(manager, plan);
    const data = new Map([["alpha",new Map([["main",{id:"main",revision:"1",authoring:captureProjectPlan(plan)}]])],
        ["beta",new Map([["main",{id:"main",revision:"2",authoring:{...captureProjectPlan(plan),width:1280,plan_json:planText("beta")}}],
            [id,{id,revision:"3",authoring:{...captureProjectPlan(plan),width:640,plan_json:planText("beta alt")}}]])]]);
    const events = [], defaults = new Map(), archives = new Map();
    let intercept = async () => {}, serial = 10;
    const request = async body => {
        events.push(structuredClone(body));
        await intercept(body);
        const records = data.get(body.run_name) ?? new Map([["main",{id:"main",revision:"",authoring:null}]]);
        if (body.action === "list") return {default_branch:defaults.get(body.run_name) ?? "main",
            branches:[...records.values()].map(({authoring,...record})=>record)};
        const current = records.get(body.branch_id);
        assert.ok(current,"valid branch in correct project");
        if (body.action === "load") return structuredClone(current);
        assert.equal(body.action,"save");
        if (body.revision !== current.revision) throw Object.assign(Error("edited elsewhere"),{status:409});
        const saved = {id:body.branch_id,revision:String(++serial),authoring:body.authoring};
        records.set(body.branch_id,structuredClone(saved));data.set(body.run_name,records);
        return saved;
    };
    let controller;
    if (studio) {
        controller = new StudioBranches({request,capture:()=>captureProjectPlan(plan),changed(){},flush:async()=>{},
            apply(){},isCurrent:(run,branch)=>widget(plan,"run_name").value === run
                && (JSON.parse(widget(plan,"plan_json").value)._branch_id ?? "main") === branch});
        const view = {id:3,graph,properties:{},widgets:[]};
        view._h3ProjectPlanSession = {owner:()=>plan,controller,nodes:()=>[plan,view],
            lock:value=>{view.locked=value;}, refresh(){},
            apply(record) {
                widget(plan,"plan_json").value = JSON.stringify({...JSON.parse(record.authoring.plan_json),_branch_id:record.id});
                widget(plan,"width").value = record.authoring.width;
            }};
        graph._nodes.push(view);
    }
    const prepare = to => prepareProjectPlanSwitch(manager, widget(manager,"run_name").value, to, {
        request,requestArchive:async run=>archives.get(run) ?? null,
        flush:async run=>events.push({action:"flush",run_name:run}),
    });
    const commit = (switcher,to) => switcher.commit(()=>{
        widget(manager,"run_name").value=to;syncProjectAssetPlanRun(manager,to);
    });
    return {graph,manager,plan,data,events,defaults,archives,prepare,commit,controller,
        intercept:fn=>{intercept=fn;}};
}
for (const studio of [false,true]) {
    const f=fixture({studio});
    // Initial snapshot binding must precede authoring edits.
    if(studio) await f.controller.refresh("alpha");
    else f.plan.properties.h3_working_branch_binding_v1={run_name:"alpha",branch_id:"main",revision:"1"};
    widget(f.plan,"plan_json").value=planText("alpha edited");
    f.defaults.set("beta",id);
    const pending=await f.prepare("beta");
    assert.equal(widget(f.manager,"run_name").value,"alpha");
    assert.equal(JSON.parse(f.data.get("alpha").get("main").authoring.plan_json).shots[0].prompt[0],"alpha edited");
    f.commit(pending,"beta");
    assert.equal(widget(f.plan,"run_name").value,"beta");
    assert.equal(widget(f.plan,"width").value,640);
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value).shots[0].prompt[0],"beta alt");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value).shots[0].seed,"18446744073709551615");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value)._branch_id,id);
    const saved=JSON.parse(widget(f.plan,"plan_json").value);saved.shots[0].prompt=["beta edited"];
    widget(f.plan,"plan_json").value=JSON.stringify(saved);
    f.commit(await f.prepare("alpha"),"alpha");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value).shots[0].prompt[0],"alpha edited");
    f.defaults.set("beta","main");
    f.commit(await f.prepare("beta"),"beta");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value)._branch_id,id,"remember last branch, not another project's branch or changed default");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value).shots[0].prompt[0],"beta edited");
}
{
    const f=fixture();
    f.commit(await f.prepare("fresh"),"fresh");
    const plan=JSON.parse(widget(f.plan,"plan_json").value);
    assert.equal(plan.shots.length,1);assert.equal(plan.shots[0].prompt.join(""),"");
    assert.ok(!plan.chapters && !plan.shared_prompt && !plan._branch_id,"new project never inherits old prompts, chapters or branch");
    f.commit(await f.prepare("alpha"),"alpha");
    assert.ok(f.data.get("fresh").get("main").authoring,"new Plan saved even if left unedited");
    f.archives.set("legacy",{...captureProjectPlan(f.plan),plan_json:planText("from archive")});
    f.commit(await f.prepare("legacy"),"legacy");
    assert.equal(JSON.parse(widget(f.plan,"plan_json").value).shots[0].prompt[0],"from archive");
    f.commit(await f.prepare("alpha"),"alpha");
    assert.ok(f.data.get("legacy").get("main").authoring,"legacy archive becomes a branch snapshot without regeneration");
}
for(const failure of ["read","conflict","edit","rewire","callback","external"]){
    const f=fixture();
    f.plan.properties.h3_working_branch_binding_v1={run_name:"alpha",branch_id:"main",revision:"1"};
    if(failure === "external") f.plan.inputs.push({name:"plan_json_input",link:99});
    if(failure === "conflict"){
        f.data.get("alpha").get("main").revision="other";
        widget(f.plan,"plan_json").value=planText("local edits");
    }
    f.intercept(async body=>{
        if(body.run_name !== "beta") return;
        if(failure === "read") throw Error("offline");
        if(failure === "edit") widget(f.plan,"plan_json").value=planText("typed while waiting");
        if(failure === "rewire") f.plan.inputs[0].link=null;
    });
    if(failure === "callback"){
        const pending=await f.prepare("beta");
        widget(f.plan,"width").callback=()=>{throw Error("widget callback");};
        assert.throws(()=>f.commit(pending,"beta"),/widget callback/);
        pending.cancel();
        assert.equal(widget(f.plan,"width").value,960);
    } else await assert.rejects(f.prepare("beta"));
    assert.equal(widget(f.manager,"run_name").value,"alpha",failure);
    assert.equal(widget(f.plan,"run_name").value,"alpha",failure);
    assert.notEqual(JSON.parse(widget(f.plan,"plan_json").value).shots[0].prompt[0],"beta");
    assert.equal(f.data.get("beta").get("main").revision,"2","no destination writes");
}
{
    const f=fixture({studio:true});await f.controller.refresh("alpha");
    f.controller.pending={action:"save"};
    await assert.rejects(f.prepare("beta"),/pending/);
    f.controller.pending=null;f.controller.draftRecovery={};
    await assert.rejects(f.prepare("beta"),/recovery draft/);
    assert.equal(f.controller.busy,false,"failed preparation releases the editor");
}
console.log("Project Plan switch: Plain/Studio save-load roundtrips, branch memory, exact seeds, empty/legacy projects, stale writes, late edits, external inputs, failed reads and rollback pass");
