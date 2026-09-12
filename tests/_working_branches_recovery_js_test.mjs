import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {parsePlanJson, planToJson, promptValueToText} from "../web/h3_chain_plan_core.mjs";
import {StudioBranches, BranchDrafts, authoringSignature, branchWidgetTransaction} from "../web/h3_working_branches.mjs";
import {workingBranchId} from "../web/h3_working_branches.mjs";
import {branchPolicyNodes, captureBranchPolicyInputs, restoreBranchPolicyInputs} from "../web/h3_plan_restore_core.mjs";

const id = "a".repeat(32);
const authoring = seed => ({plan_json:JSON.stringify({shots:[{id:"one",prompt:"keep this",seed}],
    chapters:[{id:"one",start_scene_id:"one",text:"lyrics"}]}),width:1344});
const memoryStorage = () => {
    const values = new Map();
    return {getItem:key=>values.get(key) ?? null, setItem:(key,value)=>values.set(key,value), removeItem:key=>values.delete(key)};
};
{
    const saved = {...authoring('5056228374170984401'), base_seed:'0'};
    const live = {...saved, base_seed:0};
    assert.equal(authoringSignature(saved), authoringSignature(live),
        'the base-seed widget numeric/string representation is not an authoring edit');
    assert.equal(saved.base_seed, '0', 'comparison must not rewrite saved settings');
    for (const seed of [1, Number.MAX_SAFE_INTEGER]) {
        assert.equal(authoringSignature({...saved,base_seed:seed}),
            authoringSignature({...saved,base_seed:String(seed)}));
    }
    assert.notEqual(authoringSignature({...saved,base_seed:'18446744073709551614'}),
        authoringSignature({...saved,base_seed:'18446744073709551615'}), 'adjacent uint64 seeds remain distinct');
    assert.notEqual(authoringSignature({...saved,base_seed:9007199254740992}),
        authoringSignature({...saved,base_seed:'9007199254740992'}), 'unsafe numeric seeds must not imply exact equality');
    const addedScene = parsePlanJson(live.plan_json);
    addedScene.shots.push({id:'new_scene',prompt:'new scene',seed:'5056228374170984401'});
    assert.notEqual(authoringSignature(saved),authoringSignature({...live,plan_json:planToJson(addedScene)}),
        'adding a scene is still a real Plan difference requiring an explicit save');
}
{
    const saved = authoring('18446744073709551614');
    const parsed = JSON.parse(saved.plan_json);
    parsed.prompt_prefix = 'Shared\r\nwords';
    parsed.shots[0].prompt = 'Line 1\r\n\r\nLine 3';
    saved.plan_json = JSON.stringify(parsed);
    const live = {...saved, plan_json:planToJson(parsePlanJson(saved.plan_json))};
    assert.equal(authoringSignature(saved), authoringSignature(live),
        'opening string prompts as line arrays must not create an edit conflict');
    for (const edit of [
        p => { p.shots[0].prompt.push('real edit'); },
        p => { p.shots[0].seed = '18446744073709551615'; },
        p => { p.shots[0].steps = 8; },
        p => { p.chapters[0].resolution = {width:960,height:544}; },
    ]) {
        const changed = parsePlanJson(live.plan_json); edit(changed);
        assert.notEqual(authoringSignature(saved), authoringSignature({...live,plan_json:planToJson(changed)}));
    }
    const numericSeed = {...saved, plan_json:saved.plan_json.replace('"18446744073709551614"','18446744073709551614')};
    assert.equal(authoringSignature(numericSeed), authoringSignature(saved), 'legacy uint64 literals stay exact');
}
{
    // Exercise the real Studio serialization, not only the controller fixture.
    const source=fs.readFileSync(new URL('../web/h3_chain_plan_studio.js',import.meta.url),'utf8');
    const capture=source.match(/^        captureRecovery:\(\) => \{[^]*?^        },$/m)[0];
    const restore=source.match(/^        restoreRecovery:async recovery => \{[^]*?^        },$/m)[0];
    const normalize=source.match(/^    function normalizedEditorial\([^]*?^    }$/m)[0];
    const value={revision:'f'.repeat(32),placements:[],locked_scene_ids:[],replacements:[],
        trims:[{scene_id:'one',out_frame:81}],subtitles:{mode:'off',asset_id:'',offset_seconds:0},
        alternate_draft:{enabled:true,scene:1,scene_id:'one',base_revision:'b'.repeat(32),
            prompt:'alternate words',seed:'99',media_mode:'picture_only'}};
    const state={plan:{shots:[{id:'one'}]},editorial:structuredClone(value),editorialBaseline:{base:'old'},
        editorialStored:{revision:'f'.repeat(32)},editorialPending:{},editorialReady:true,
        editorialEditEpoch:4,history:{pendingDraft:{sceneId:'one',prompt:'local words'}}};
    let synced=0;
    const context=vm.createContext({state,structuredClone,MAX_SEED:2n**64n-1n,MAX_H3_FRAMES:1000,
        safeShotId:(id,fallback)=>id||fallback,runName:()=> 'demo',renderShell(){},syncAlternateTakeWidget(){synced++;}});
    vm.runInContext(`${normalize}\nvar recoveryCallbacks = {${capture}\n${restore}};`,context);
    const draft=context.recoveryCallbacks.captureRecovery();
    state.editorial={};state.editorialPending=null;state.history.pendingDraft=null;
    await context.recoveryCallbacks.restoreRecovery(draft);
    assert.deepEqual(JSON.parse(JSON.stringify(state.editorial)),value);
    assert.deepEqual(state.history.pendingDraft,{sceneId:'one',prompt:'local words'});
    assert.equal(state.editorialEditEpoch,5);
    assert.match(state.editorialSaveError,/recovered/);
    assert.equal(synced,1,'armed ALT must be restored into the serialized queue widget');
}
function fixture({live = authoring("18446744073709551614"), storage = memoryStorage(), binding = null} = {}) {
    const events = [], receipts = new Map();
    const disk = new Map([['main',{id:'main',revision:'1',authoring:authoring("18446744073709551614")}],
        [id,{id,revision:'2',authoring:authoring('2')}]]);
    let serial = 2, remembered = binding;
    const drafts = new BranchDrafts(storage,'workflow-node');
    const controller = new StudioBranches({selected:"main", capture:()=>structuredClone(live), drafts, binding,
        rememberBinding:value=>remembered=value,
        flush:async()=>events.push('flush'), changed(){},
        apply:async record=>{events.push(`apply:${record.id}`);live=structuredClone(record.authoring);},
        request:async body=>{
            events.push(body.action);
            if(body.action==='list') return {branches:[...disk.values()].map(({id,revision})=>({id,revision})),default_branch:'main'};
            if(body.action==='load') return structuredClone(disk.get(body.branch_id));
            if(body.operation_id && receipts.has(body.operation_id)) return structuredClone(receipts.get(body.operation_id));
            let record;
            if(body.action==='save') {
                if(disk.get(body.branch_id).revision!==body.revision) throw Object.assign(Error('revision conflict'),{status:400});
                record={id:body.branch_id,revision:String(++serial),authoring:structuredClone(body.authoring)};
            } else if(body.action==='create') {
                record={id:body.operation_id,revision:String(++serial),authoring:structuredClone(body.authoring)};
            } else if(body.action==='default') return {default_branch:body.branch_id};
            else throw Error('Unexpected action');
            disk.set(record.id,record); receipts.set(body.operation_id,record); return structuredClone(record);
        }});
    return {controller,events,disk,drafts,storage,getLive:()=>live,setLive:value=>live=value,getBinding:()=>remembered};
}
async function delayedLoad(t) {
    const original=t.controller.request;
    let finish, started;
    const ready=new Promise(resolve=>started=resolve);
    t.controller.request=async body=>body.action==='load'
        ? new Promise(resolve=>{finish=()=>resolve(structuredClone(t.disk.get(body.branch_id)));started();}) : original(body);
    return {ready,finish:()=>finish(),original};
}
{
    const t=fixture();
    const live=t.getLive();
    live.plan_json=planToJson(parsePlanJson(live.plan_json));
    live.base_seed=0;
    t.disk.get('main').authoring.base_seed='0';
    await t.controller.refresh('demo');
    assert.equal(t.controller.conflict,'','equivalent Plan and base-seed formatting loads without a false conflict');
    t.controller.observe();
    assert.equal(t.drafts.read('demo','main'),null,'normalization alone must not manufacture a recovery draft');
    t.drafts.save('demo','main',{authoring:t.disk.get('main').authoring,revision:'1'});
    t.controller.readDraft();
    assert.equal(t.controller.draftRecovery,null,'old formatting-only recovery entries do not block editing');
}
{
    // A stale authoring/cut snapshot must not trap the user on a named branch.
    const t=fixture(); await t.controller.refresh('demo');
    await t.controller.switchTo(id);
    t.setLive(authoring('unsaved scene-one edit'));
    const cut={editorial:{trims:[{scene:1,out_frame:81}]},history:{sceneId:'one',prompt:'local prompt'}};
    t.controller.captureRecovery=()=>structuredClone(cut);
    t.controller.flush=async()=>{throw Error('Editorial conflict');};
    await t.controller.switchTo('main');
    assert.equal(t.controller.selected,id);
    assert.equal(t.controller.switchTarget,'main');
    assert.match(t.controller.error,/Editorial conflict/);
    const before=JSON.stringify([...t.disk]);
    t.events.length=0;
    t.controller.settle=async()=>t.events.push('settle');
    await t.controller.switchTo('main',{save:false});
    assert.equal(t.controller.selected,'main');
    assert.equal(t.controller.switchTarget,null);
    assert.deepEqual(t.events,['settle','load','apply:main']);
    assert.equal(JSON.stringify([...t.disk]),before,'navigation must not rewrite either saved branch');
    const draft=t.drafts.read('demo',id);
    assert.equal(JSON.parse(draft.authoring.plan_json).shots[0].seed,'unsaved scene-one edit');
    assert.deepEqual(draft.recovery,cut,'pending cut/history edits must survive settling');
    t.controller.captureRecovery=()=>null;
    await t.controller.switchTo(id,{save:false});
    assert.ok(t.controller.draftRecovery);
    let restored;
    t.controller.restoreRecovery=async value=>restored=value;
    await t.controller.restoreDraft();
    assert.deepEqual(restored,cut);
    assert.equal(JSON.parse(t.getLive().plan_json).shots[0].seed,'unsaved scene-one edit');
    assert.equal(t.drafts.read('demo',id).recovery,null,'consumed cut recovery must not reappear as unsaved');
}
{
    const t=fixture({live:authoring('stale widgets')}); await t.controller.refresh('demo');
    assert.ok(t.controller.conflict);
    t.drafts.save('demo','main',{authoring:authoring('older crash draft'),revision:'1'});
    t.controller.readDraft();
    await t.controller.switchTo(id,{save:false});
    assert.equal(t.controller.selected,id,'explicit recovery switch bypasses only the failed save');
    const draft=t.drafts.read('demo','main');
    assert.equal(JSON.parse(draft.authoring.plan_json).shots[0].seed,'stale widgets');
    assert.equal(JSON.parse(draft.older[0].authoring.plan_json).shots[0].seed,'older crash draft');
}
{
    const t=fixture(); await t.controller.refresh('demo');
    t.controller.pending={action:'save'};
    await t.controller.switchTo(id,{save:false});
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Retry pending/);
    t.controller.pending=null;
    t.storage.setItem=()=>{throw Error('quota exceeded');};
    let settled=false;
    t.controller.settle=async()=>settled=true;
    await t.controller.switchTo(id,{save:false});
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/quota/);
    assert.equal(settled,false,'storage failure must not discard pending edits');
}
{
    const t=fixture(); await t.controller.refresh('demo');
    const load=await delayedLoad(t); const switching=t.controller.switchTo(id,{save:false}); await load.ready;
    t.controller.isCurrent=()=>false;
    load.finish(); await switching;
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Project or branch changed/);
}
{
    const t=fixture(); await t.controller.refresh('demo');
    const load=await delayedLoad(t); const switching=t.controller.switchTo(id); await load.ready;
    // The run widget/connection can change before the paused 500 ms poll
    // has updated the controller's run and epoch.
    t.controller.isCurrent=()=>false;
    load.finish(); await switching;
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Project or branch changed/);
    assert.ok(!t.events.some(event=>event.startsWith('apply:')));
}
{
    const t=fixture(); await t.controller.refresh('demo');
    const load=await delayedLoad(t); const switching=t.controller.switchTo(id); await load.ready;
    t.setLive(authoring('999')); load.finish(); await switching;
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Edits arrived/);
    assert.equal(JSON.parse(t.getLive().plan_json).shots[0].seed,'999');
    assert.equal(JSON.parse(t.drafts.read('demo','main').authoring.plan_json).shots[0].seed,'999');
}
for(const binding of [null,{run_name:'demo',branch_id:'main',revision:'old'}]) {
    const t=fixture({live:authoring('stale'),binding}); await t.controller.refresh('demo');
    assert.match(t.controller.conflict,/differs/); await t.controller.switchTo(id);
    assert.ok(!t.events.includes('save'),'fresh listing revision cannot authorize old widgets');
    await t.controller.create('Recovered edits');
    assert.notEqual(t.controller.selected,'main');
    assert.equal(JSON.parse(t.disk.get(t.controller.selected).authoring.plan_json).shots[0].seed,'stale');
    assert.equal(JSON.parse(t.disk.get('main').authoring.plan_json).shots[0].seed,'18446744073709551614');
}
{
    const t=fixture(); await t.controller.refresh('demo');
    let epoch=0, cut=null;
    t.controller.editStamp=()=>epoch;
    t.controller.captureRecovery=()=>cut;
    const load=await delayedLoad(t); const switching=t.controller.switchTo(id,{save:false}); await load.ready;
    epoch++; cut={editorial:{trims:[{scene:1,out_frame:81}]}};
    load.finish(); await switching;
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Edits arrived/);
    assert.deepEqual(t.drafts.read('demo','main').recovery,cut,'cut edits arriving during navigation must be kept');
}
{
    const t=fixture({live:authoring('intentional edit'),binding:{run_name:'demo',branch_id:'main',revision:'1'}});
    await t.controller.refresh('demo'); assert.equal(t.controller.conflict,'');
    await t.controller.perform(async()=>{});
    assert.equal(JSON.parse(t.disk.get('main').authoring.plan_json).shots[0].seed,'intentional edit');
    assert.equal(t.getBinding().revision,t.disk.get('main').revision);
}
{
    const t=fixture(); await t.controller.refresh('demo');
    const original=t.controller.request; let dropped=false;
    t.controller.request=async body=>{const saved=await original(body);if(body.action==='save'&&!dropped){dropped=true;throw Error('lost response');}return saved;};
    await t.controller.switchTo(id);
    assert.equal(t.controller.selected,id); assert.equal(t.controller.pending,null);
    assert.equal(t.disk.get('main').revision,'3','replay must not make another commit');
}
{
    const t=fixture(); await t.controller.refresh('demo'); const original=t.controller.request;
    t.controller.request=async body=>{const saved=await original(body);if(body.action==='create')throw Error('lost response');return saved;};
    await t.controller.create('Empty'); assert.equal(t.disk.size,3);
    assert.match(t.controller.error,/uncertain/); const request=t.controller.pending;
    assert.equal(t.drafts.pending().operation_id,request.operation_id);
    t.controller.request=original; await t.controller.retryPending();
    assert.equal(t.controller.pending,null); assert.equal(t.disk.size,3);
    assert.ok(t.controller.records.some(row=>row.id===request.operation_id));
}
{
    const t=fixture(); await t.controller.refresh('demo'); const original=t.controller.request;
    t.controller.request=async body=>{const saved=await original(body);if(body.action==='save')throw Error('lost response');return saved;};
    await t.controller.perform(async()=>{});
    assert.ok(t.controller.pending);
    t.controller.request=original; await t.controller.refresh('another_project');
    const binding=t.getBinding();
    await t.controller.retryPending();
    assert.equal(t.controller.pending,null,'old-project request can be reconciled without blocking this workflow forever');
    assert.deepEqual(t.getBinding(),binding,'old-project retry cannot bind current widgets to old-project settings');
}
{
    const t=fixture(); await t.controller.refresh('demo'); const original=t.controller.request;
    t.controller.request=async body=>{
        const saved=await original(body);
        if(body.action==='create')t.setLive(authoring('late edit'));
        return saved;
    };
    await t.controller.create('Already published');
    assert.equal(t.controller.selected,'main'); assert.match(t.controller.error,/Edits arrived/);
    assert.equal(t.controller.records.length,3,'successfully created branch remains available after a late edit cancels switching');
    assert.equal(JSON.parse(t.getLive().plan_json).shots[0].seed,'late edit');
}
{
    const t=fixture(); await t.controller.refresh('demo'); t.setLive(authoring('crash draft')); t.controller.observe();
    const restarted=fixture({storage:t.storage,binding:t.getBinding()});
    await restarted.controller.refresh('demo'); assert.ok(restarted.controller.draftRecovery);
    await restarted.controller.restoreDraft();
    assert.equal(JSON.parse(restarted.getLive().plan_json).shots[0].seed,'crash draft');
    await restarted.controller.perform(async()=>{});
    assert.equal(JSON.parse(restarted.disk.get('main').authoring.plan_json).shots[0].seed,'crash draft');
}
{
    const t=fixture({live:authoring('stale')}); await t.controller.refresh('demo');
    await t.controller.reloadSaved(); assert.equal(t.controller.conflict,'');
    assert.equal(JSON.parse(t.getLive().plan_json).shots[0].seed,'18446744073709551614');
    assert.equal(JSON.parse(t.drafts.read('demo','main').authoring.plan_json).shots[0].seed,'stale');
    await t.controller.refresh('demo');
    assert.equal(t.controller.draftRecovery,null,'explicit reload stays resolved after refresh');
    const reopened=fixture({storage:t.storage,binding:t.getBinding()});
    await reopened.controller.refresh('demo');
    assert.equal(reopened.controller.draftRecovery,null,'reopening must not resurrect an acknowledged conflict');
    await reopened.controller.switchTo(id,{save:false});
    await reopened.controller.switchTo('main',{save:false});
    assert.equal(reopened.controller.draftRecovery,null,'navigating away and back keeps old backups resolved');
    reopened.controller.readDraft({includeResolved:true});
    assert.equal(JSON.parse(reopened.controller.draftRecovery.authoring.plan_json).shots[0].seed,'stale',
        'the previous local edits remain explicitly recoverable');
    reopened.controller.readDraft();
    reopened.disk.get('main').revision='newer';
    await reopened.controller.refresh('demo');
    assert.ok(reopened.controller.draftRecovery,'acknowledgement does not apply to a newer server revision');
    reopened.disk.get('main').revision='1';
    await reopened.controller.refresh('demo');
    reopened.drafts.save('demo','main',{authoring:authoring('new unsaved edit'),revision:'1'});
    reopened.controller.readDraft();
    assert.ok(reopened.controller.draftRecovery,'a new draft still needs recovery');
    reopened.setLive(authoring('new unsaved edit'));
    reopened.controller.readDraft();
    assert.equal(reopened.controller.draftRecovery,null);
    assert.doesNotMatch(reopened.controller.draftStatus,/restore it before editing/,'clear a stale recovery warning');
}
{
    const storage=memoryStorage(); storage.setItem=()=>{throw Error('quota exceeded');};
    const t=fixture({storage}); await t.controller.refresh('demo'); t.setLive(authoring('unsaved'));t.controller.observe();
    assert.match(t.controller.draftStatus,/Draft not saved.*quota/);
    await t.controller.perform(async()=>{});
    assert.equal(JSON.parse(t.disk.get('main').authoring.plan_json).shots[0].seed,'unsaved',
        'browser quota must not prevent a real branch save');
}
{
    const a={properties:{branch:'old'},widgets:[{name:'seed',value:'18446744073709551614'}]};
    const b={properties:{},widgets:[{name:'prompt',value:'old'}]};
    assert.throws(()=>branchWidgetTransaction([a,b],()=>{
        a.properties.branch='new';a.widgets[0].value='2';b.widgets[0].value='new';throw Error('callback failed');
    }),/callback failed/);
    assert.equal(a.properties.branch,'old');assert.equal(a.widgets[0].value,'18446744073709551614');
    assert.equal(b.widgets[0].value,'old');
}
const reordered=authoring('2'); reordered.plan_json=JSON.stringify({...JSON.parse(reordered.plan_json),_branch_id:id});
assert.equal(authoringSignature(reordered),authoringSignature(authoring('2')));
{
    const source=fs.readFileSync(new URL('../web/h3_chain_plan_studio.js',import.meta.url),'utf8');
    const handler=source.match(/^    function captureBranchAuthoring\([^]*?^    }$/m)[0];
    const original=authoring('2').plan_json;
    const live=JSON.parse(original);live.shots[0].prompt='external JSON prompt-only edit';
    const node={};
    const context=vm.createContext({state:{plan:JSON.parse(original),lastValue:original,
        planWidget:{value:JSON.stringify(live)},planOwner:node},node,PLAN_SETTING_WIDGETS:[],
        captureBranchPolicyInputs, preserveDelegatedPrompts(){},parsePlanJson:JSON.parse,planToJson:JSON.stringify});
    vm.runInContext(handler,context);
    assert.equal(JSON.parse(context.captureBranchAuthoring().plan_json).shots[0].prompt,live.shots[0].prompt);
}
{
    // Execute the actual production callback, including its state rollback.
    const source=fs.readFileSync(new URL('../web/h3_chain_plan_studio.js',import.meta.url),'utf8');
    const handler=source.match(/^    async function applyWorkingBranch\([^]*?^    }$/m)[0];
    const branchWidget={name:'working_branch_id',value:'main'};
    const width={name:'width',value:64}, height={name:'height',value:64};
    const plan={name:'plan_json',value:authoring('old').plan_json};
    const node={properties:{},widgets:[branchWidget,width,height,plan]};
    const state={checkpointToken:1,presentationToken:1,history:{loadToken:1,sceneKey:'old'},
        promptEditors:[],planNode:null,lastBranchId:'main'};
    const branches={selected:'main'};
    const context=vm.createContext({branchPolicyNodes,restoreBranchPolicyInputs,branchWidgetTransaction,workingBranchId,branchWidget,state,branches,node,Map,
        CHECKPOINT_CACHE_PROPERTY:'cache',parsePlanJson:JSON.parse,planToJson:JSON.stringify,
        PLAN_SETTING_WIDGETS:['width','height','plan_json'],disposePlayer(){},
        writePlanSetting(name,value){node.widgets.find(w=>w.name===name).value=value;if(name==='height')throw Error('callback failure');},
        widget(){return null;},loadPlan(){},renderShell(){},dirty(){}});
    vm.runInContext(handler,context);
    await assert.rejects(context.applyWorkingBranch({id,authoring:{width:128,height:96,plan_json:authoring('new').plan_json}}),/callback failure/);
    assert.equal(branchWidget.value,'main');assert.equal(width.value,64);assert.equal(height.value,64);
    assert.equal(plan.value,authoring('old').plan_json);assert.equal(branches.selected,'main');
    assert.equal(state.history.sceneKey,'old');assert.ok(state.checkpointToken>1);
    context.writePlanSetting=(name,value)=>{node.widgets.find(w=>w.name===name).value=value;};
    context.loadPlan=(force,throwOnError)=>{
        assert.equal(force,true);assert.equal(throwOnError,true);
        state.history.sceneKey='failed new view';throw Error('render failure');
    };
    await assert.rejects(context.applyWorkingBranch({id,authoring:{width:128,height:96,plan_json:authoring('new').plan_json}}),/render failure/);
    assert.equal(branchWidget.value,'main');assert.equal(plan.value,authoring('old').plan_json);
    assert.equal(state.history.sceneKey,'old');
    context.loadPlan=()=>{};
    const recovered={...authoring('17115579879135537167'),width:960,height:544};
    await context.applyWorkingBranch({id,authoring:recovered});
    assert.equal(branchWidget.value,id);
    assert.equal(width.value,960);assert.equal(height.value,544);
    assert.equal(JSON.parse(plan.value).shots[0].seed,'17115579879135537167');
    assert.equal(JSON.parse(plan.value).shots[0].prompt,'keep this');
    assert.match(source,/if \(throwOnError\) throw error/);
    assert.match(source,/root\.inert = Boolean\(branches\?\.busy\)/);
    assert.match(source,/branches\?\.busy && !force/);
}
console.log('Branch recovery: stale workflows, edits during switch, revision binding, lost responses, crash drafts, quota errors and rollback pass');

{
    // Real restore + real widget setter, not a mock that conceals edit effects.
    const source=fs.readFileSync(new URL('../web/h3_chain_plan_studio.js',import.meta.url),'utf8');
    const functions=['applyWorkingBranch','writePlanSetting'].map(name =>
        source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`,'m'))[0]).join('\n');
    const oldPlan=parsePlanJson(authoring('old').plan_json);
    const branchWidget={name:'working_branch_id',value:'main'};
    const planWidget={name:'plan_json',value:planToJson(oldPlan)};
    const node={properties:{},widgets:[branchWidget,planWidget,
        ...Object.entries({width:1344,height:768,default_steps:20}).map(([name,value])=>({name,value}))]};
    const state={plan:oldPlan,planOwner:node,planNode:null,promptEditors:[],history:{loadToken:1},
        checkpointToken:1,presentationToken:1};
    const context=vm.createContext({node,state,branchWidget,branches:{selected:'main'},Map,
        branchPolicyNodes,restoreBranchPolicyInputs,branchWidgetTransaction,workingBranchId,
        parsePlanJson,planToJson,promptValueToText,CHECKPOINT_CACHE_PROPERTY:'cache',
        PLAN_SETTING_WIDGETS:['width','height','default_steps','plan_json'],
        widget:(target,name)=>target.widgets.find(w=>w.name===name),disposePlayer(){},dirty(){},renderShell(){},
        runName:()=> 'demo',settingsSignature:()=> '',
        writePlan:()=>{throw Error('Restore must not invoke the editing path');},
        loadPlan:()=>{state.plan=parsePlanJson(planWidget.value);},
    });
    vm.runInContext(functions,context);
    const saved={...authoring('18446744073709551615'),default_steps:8,width:960,height:544};
    await context.applyWorkingBranch({id,authoring:saved});
    assert.equal(authoringSignature({...saved,plan_json:planWidget.value}),authoringSignature(saved));
    assert.equal(oldPlan.defaults,undefined,'the outgoing Plan is untouched');
    assert.equal(state.plan.defaults,undefined,'do not inject defaults absent from the saved Plan');
    assert.equal(state.plan.shots[0].seed,'18446744073709551615');
    assert.equal(node.widgets.find(w=>w.name==='default_steps').value,8);
}
