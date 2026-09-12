import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {workingBranchId} from '../web/h3_working_branches.mjs';
import {parsePlanJson, promptValueToText, promptTextToLines} from '../web/h3_chain_plan_core.mjs';
import {promptRevisionLabel, promptRevisionNavigation} from '../web/h3_prompt_history_core.mjs';
import {PromptUndoHistory} from '../web/h3_rich_prompt_editor_core.mjs';
import {rebaseScenePrompt} from '../web/h3_prompt_companion_sync.mjs';

const filename=process.argv.includes('--rich')?'h3_chain_rich_scene_prompt_editor.js':'h3_chain_scene_prompt_editor.js';
const source=fs.readFileSync(new URL(`../web/${filename}`,import.meta.url),'utf8');
const extract=name=>{
    const match=source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`,'m'));
    assert.ok(match,name); return match[0];
};
const id='a'.repeat(32), second='b'.repeat(32), scene='scene_10';
const plan=branch=>({_branch_id:branch,shots:[{id:scene,prompt:['Saved chapter two prompt']} ]});
function fixture(branch=id) {
    const calls=[], timers=new Map(), pending=[];
    const state={plan:plan(branch),planNode:{widgets:[{name:'run_name',value:'demo'}]},active:0,editor:{focus(){}},
        history:{sceneKey:'',loadToken:0,saveTimer:null,pendingDraft:null,savePromise:null,
            textarea:{value:'',focus(){}},status:{}},undoByScene:new Map()};
    let counter=0, delay=false, writes=0;
    const history=selected=>({active_revision:`${selected}-revision`,revisions:[{
        id:`${selected}-revision`,prompt:'Saved chapter two prompt',
        ...(selected==='main'?{}:{executed_at:'2026-09-11T10:55:32.321Z',execution_count:1}),
    }]});
    const context=vm.createContext({state,node:{},URLSearchParams,workingBranchId,
        parsePlanJson,promptValueToText,promptTextToLines,PromptUndoHistory,rebaseScenePrompt,
        ACTIVE_SCENE_PROPERTY:'active',
        renderHistory(){},renderRichEditorText(){},renderEditorText(){},recordPromptReplacement(){},writePlan(){writes++;},
        projectMutationOptions:async(_node,_run,options)=>options,
        window:{setTimeout:fn=>{timers.set(++counter,fn);return counter;},clearTimeout:n=>timers.delete(n)},
        api:{fetchApi:async(url,options)=>{
            const selected=new URL(url,'http://fixture').searchParams.get('branch_id')??'main';
            const body=options?.body?JSON.parse(options.body):null;
            calls.push({selected,body,url});
            const h=history(selected);
            const result={ok:true,json:async()=>({history:h,revision:h.revisions[0]})};
            if(delay)return new Promise(resolve=>pending.push(()=>resolve(result)));
            return result;
        }},
    });
    vm.runInContext(['planRunName','planBranchId','historySceneKey','historyRequest','loadHistory',
        'scheduleHistoryDraft','flushHistoryDraft','mutateHistoryRevision','selectHistoryRevision',
        'promptUndoForScene','rebaseActivePromptOntoLivePlan','onProjectOwnershipChanged'].map(extract).join('\n'),context);
    vm.runInContext(source.match(/^    const onPromptExecuted = \(event\) => \{[^]*?^    };/m)[0]+
        '\nthis.onPromptExecuted=onPromptExecuted;',context);
    return {state,context,calls,timers,pending,setDelay:value=>{delay=value;},get writes(){return writes;}};
}
{
    const t=fixture();
    await t.context.loadHistory(scene,'Saved chapter two prompt');
    assert.equal(t.calls[0].selected,id);
    assert.equal(t.calls[0].body.branch_id,id);
    assert.match(promptRevisionLabel(promptRevisionNavigation(t.state.history.data)),/^Active executed/);
    await t.context.loadHistory(scene,'',false);
    assert.equal(t.calls.at(-1).body,null,'execution refresh is read-only');
    assert.equal(t.calls.at(-1).selected,id);
    for(const action of ['label','archive','delete']){
        await t.context.mutateHistoryRevision(action,`${id}-revision`,{label:'Take'});
        assert.equal(t.calls.at(-1).selected,id);
        assert.equal(t.calls.at(-1).body.action,action);
    }
    await t.context.selectHistoryRevision(`${id}-revision`);
    assert.equal(t.calls.at(-1).selected,id);
    assert.equal(t.writes,1);
    assert.equal(promptValueToText(t.state.plan.shots[0].prompt),'Saved chapter two prompt');
    t.state.plan=plan('main');
    await t.context.loadHistory(scene,'',false);
    assert.equal(t.calls.at(-1).selected,'main','legacy projects retain root history');
    assert.match(promptRevisionLabel(promptRevisionNavigation(t.state.history.data)),/^Active draft/);
    assert.notEqual(t.context.historySceneKey('demo',scene,id),t.context.historySceneKey('demo',scene,'main'));
}
{
    const t=fixture();
    await t.context.loadHistory(scene,'',false);
    const undo=t.context.promptUndoForScene(scene,'Branch A');
    t.context.scheduleHistoryDraft(scene,'Unsent A edit');
    t.state.plan=plan(second);
    await t.context.loadHistory(scene,'',false);
    const current=t.state.history.data;
    await t.context.flushHistoryDraft();
    assert.equal(t.calls.at(-1).selected,id,'a delayed save keeps the branch where it was typed');
    assert.equal(t.calls.at(-1).body.prompt,'Unsent A edit');
    assert.equal(t.calls.at(-1).body.parent_revision,null,'never parent a draft to another branch revision');
    assert.equal(t.state.history.data,current,'old-branch save cannot replace the current history');
    assert.notEqual(t.context.promptUndoForScene(scene,'Branch B'),undo,'undo is branch-local too');
    t.state.plan=plan(id);
    assert.equal(t.context.promptUndoForScene(scene,'Branch A'),undo);
}
{
    const t=fixture(); t.setDelay(true);
    const old=t.context.loadHistory(scene,'',false);
    t.state.plan=plan(second);t.setDelay(false);
    await t.context.loadHistory(scene,'',false);
    const current=t.state.history.data;
    t.pending.shift()(); await old;
    assert.equal(t.state.history.data,current,'late GET cannot overwrite the selected branch');
}
{
    const t=fixture();await t.context.loadHistory(scene,'',false);t.setDelay(true);
    const activation=t.context.selectHistoryRevision(`${id}-revision`);
    for(let i=0;i<10&&!t.pending.length;i++)await Promise.resolve();
    assert.ok(t.pending.length);
    t.state.plan=plan(second);t.setDelay(false);
    await t.context.loadHistory(scene,'',false);
    t.pending.shift()();await activation;
    assert.equal(t.writes,0,'late activation must not restore a prompt into another branch');
}
for(const action of ['activate','delete']) {
    const t=fixture();await t.context.loadHistory(scene,'',false);
    let settle;
    t.state.history.savePromise=new Promise(resolve=>{settle=()=>{t.state.history.savePromise=null;resolve();};});
    const operation=action==='activate'?t.context.selectHistoryRevision(`${id}-revision`)
        :t.context.mutateHistoryRevision('delete',`${id}-revision`);
    t.state.plan=plan(second);await t.context.loadHistory(scene,'',false);
    const count=t.calls.length;
    settle();await operation;
    assert.equal(t.calls.length,count,'switch during draft flush must cancel the old history action');
}
{
    const t=fixture();await t.context.loadHistory(scene,'',false);
    const event=branch=>({detail:{output:{h3_chain_active_scene:[{run_name:'demo',shot_id:scene,_branch_id:branch}]}}});
    t.context.onPromptExecuted(event('main'));
    assert.equal(t.timers.size,0,'ignore execution events from another branch');
    t.context.onPromptExecuted(event(id));
    assert.equal(t.timers.size,1);
    t.state.plan=plan(second);await t.context.loadHistory(scene,'',false);
    const count=t.calls.length;
    for(const fn of t.timers.values())fn();
    assert.equal(t.calls.length,count,'switching before the event timer fires cancels its refresh');
}
{
    const t=fixture();
    t.state.planWidget={value:JSON.stringify(plan(second))};
    assert.equal(t.context.rebaseActivePromptOntoLivePlan(),false,'old edit cannot rebase across branches');
    assert.equal(t.state.plan._branch_id,id);
}
{
    const t=fixture();await t.context.loadHistory(scene,'',false);
    t.context.isProjectReadOnlyError=()=>true;
    t.state.history.error='read-only';
    t.context.onProjectOwnershipChanged({owned_by_requester:true,run_name:'demo'});
    await Promise.resolve();
    const call=t.calls.at(-1);
    assert.equal(call.selected,id);
    assert.equal(new URL(call.url,'http://fixture').searchParams.get('scene_id'),scene,
        'ownership recovery extracts the scene after the branch-scoped key');
    assert.equal(call.body,null);
}
console.log(`${filename}: branch-scoped reads/writes, executed labels, undo, delayed saves/loads/activation, execution events and ownership recovery pass`);
