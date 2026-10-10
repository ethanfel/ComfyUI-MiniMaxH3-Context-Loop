import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {StudioBranches, BranchDrafts} from "../web/h3_working_branches.mjs";

const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const schedule = source.match(/^    function scheduleEditorialSave\([^]*?^    }$/m)[0];
const flush = source.match(/^    async function flushProjectWrites\([^]*?^    }$/m)[0];
const clone = value => JSON.parse(JSON.stringify(value));

async function fixture({missingProject = false} = {}) {
    const storage = new Map();
    const drafts = new BranchDrafts({getItem:key=>storage.get(key) ?? null,
        setItem:(key,value)=>storage.set(key,value), removeItem:key=>storage.delete(key)}, "test-client");
    const authoring = {plan_json:JSON.stringify({shots:[{id:"one",prompt:["keep my prompt"]}]})};
    let record = {id:"main",revision:missingProject ? "" : "saved", authoring:missingProject ? null : authoring};
    const writes = [], timers = new Map();
    const baseline = {run_name:"demo",scene_order:[{scene:1,scene_id:"one"}],trims:[]};
    const local = {...clone(baseline),trims:[{scene_id:"one",out_frame:80}]};
    const state = {plan:{},editorialReady:true,editorialRun:"demo",editorialBindingError:"",
        editorialStored:clone(baseline),editorialBaseline:clone(baseline),editorialDraft:null,
        lastEditorialSignature:JSON.stringify(baseline),editorialBranchBlocked:false,
        editorialSaveError:"",checkpoints:new Map(),editorialTimer:null,editorialPending:null,
        editorialSavePromise:null};
    const branches = new StudioBranches({drafts,capture:()=>authoring,apply:()=>{},flush:async()=>{},changed:()=>{},
        request:async body=>{
            if(body.action === "list") return {branches:[record],default_branch:"main"};
            if(body.action === "load") return clone(record);
            assert.equal(body.action,"save");
            assert.equal(body.revision,record.revision,"recovery uses CAS, not a forced overwrite");
            record = {...record,revision:"updated",authoring:clone(body.authoring)};
            return clone(record);
        }});
    await branches.refresh("demo");
    await drafts.save("demo","main",{authoring:{...authoring,plan_json:authoring.plan_json.replace("keep my prompt","old draft")},revision:"old"});
    await branches.readDraft();
    assert.ok(branches.draftRecovery);
    const context = vm.createContext({state,branches,structuredClone,
        runName:()=>"demo",currentBranch:()=>"main",syncAlternateTakeWidget(){},
        editorialPayload:()=>clone(local),refreshEditorialBinding(){},cacheStudioPresentation(){},
        editorialSignature:JSON.stringify,renderStatus(){},flushHistoryDraft:async()=>{},
        setTimeout:fn=>{const id=timers.size+1;timers.set(id,fn);return id;},
        clearTimeout:id=>timers.delete(id),
        persistEditorial:async(payload,signature)=>{
            writes.push(clone(payload));state.editorialSaveError="";state.editorialBranchBlocked=false;
            state.editorialStored=clone(payload);state.lastEditorialSignature=signature;state.editorialDraft=null;
        }});
    vm.runInContext(schedule+"\n"+flush,context);
    return {state,branches,drafts,writes,context,local,timers,record:()=>record};
}

for (const missingProject of [false,true]) {
    const f=await fixture({missingProject});
    f.context.scheduleEditorialSave();
    assert.equal(f.state.editorialBranchBlocked,true);
    assert.match(f.state.editorialSaveError,/recovery draft/);
    await assert.rejects(f.context.flushProjectWrites(),/recovery draft/);
    assert.equal(f.writes.length,0,"unresolved recovery never publishes a cut");
    await f.branches.updateActive(()=>true);
    assert.equal(f.branches.error,"");assert.equal(f.branches.draftRecovery,null);
    assert.equal(JSON.parse(f.record().authoring.plan_json).shots[0].prompt[0],"keep my prompt");
    assert.ok(await f.drafts.read("demo","main"),"recovery backups survive the choice");
    assert.match(f.state.editorialSaveError,/recovery draft/,"fixture reproduces the cached guard");
    await f.context.flushProjectWrites();
    assert.equal(f.writes.length,1,"explicit switch retry saves the previously blocked edit");
    assert.deepEqual(f.writes[0].trims,f.local.trims);
    assert.equal(f.state.editorialSaveError,"");assert.equal(f.state.editorialBranchBlocked,false);
    assert.equal(f.timers.size,0,"flush drains the newly scheduled timer exactly once");
    await f.context.flushProjectWrites();
    assert.equal(f.writes.length,1,"no duplicate save");
}
for (const guard of ["conflict","not_ready","binding","other_run","cancel"]) {
    const f=await fixture();f.context.scheduleEditorialSave();
    if(guard === "cancel") await f.branches.updateActive(()=>false);
    else {
        await f.branches.updateActive(()=>true);
        if(guard === "conflict") f.branches.conflict="Newer branch exists";
        if(guard === "not_ready") f.branches.ready=false;
        if(guard === "binding") f.state.editorialBindingError="Scene map changed";
    }
    if(guard === "other_run") await f.context.flushProjectWrites("elsewhere");
    else await assert.rejects(f.context.flushProjectWrites());
    assert.equal(f.writes.length,0,guard+" must not bypass a live guard");
    assert.ok(f.state.editorialDraft,"local edits stay intact");
}
for (const message of ["HTTP 409", "HTTP 423", "offline", "Local cut edits recovered; use Retry save or Reload saved cut."]) {
    const f=await fixture();await f.branches.updateActive(()=>true);
    f.state.editorialSaveError=message;f.state.editorialBranchBlocked=false;
    await assert.rejects(f.context.flushProjectWrites(),error=>error.message === message);
    assert.equal(f.writes.length,0,"real save failures and recovered cuts need explicit resolution");
}
console.log("Project recovery: cached branch guard rechecked, deleted-project Plan recreated with consent, backups preserved, no silent retry of cut/ownership/CAS conflicts");
