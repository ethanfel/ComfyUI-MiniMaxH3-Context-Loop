import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {branchRequestPath} from '../web/h3_working_branches.mjs';

const source=fs.readFileSync(new URL('../web/h3_chain_checkpoint_manager.js',import.meta.url),'utf8');
const handler=name=>source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`,'m'))[0];
const secondary='a'.repeat(32);
function fixture() {
    const calls=[], planNode={};
    const lineage=Array.from({length:7},(_,i)=>({scene:i+1,revision:String(i+1).repeat(32)}));
    let marker={run:'demo',branch:secondary};
    const context=vm.createContext({
        state:{runName:'demo',stage:'original',selected:{scene:7,revision:'7'.repeat(32)},busy:false},
        node:{properties:{h3_working_branch_id:'main'}},status:{},
        currentPlanMarker:()=>marker,upstreamPlanNode:()=>planNode,
        selectedLineage:()=>lineage,selectedChapterRange:()=>({start:1,end:7,title:'Chapter 1'}),
        canLoadSelected:()=>true, workingBranchName:()=> '960x544',
        // Same label for both branches, so routing must rely exclusively on IDs.
        window:{confirm:()=>true},setBusy(value){context.state.busy=value;},
        projectMutationOptions:async(node,run,options)=>options,branchRequestPath,
        jsonRequest:async(path,options)=>{calls.push({path,body:JSON.parse(options.body)}); return {restored:lineage};},
        applyActivatedRevisions:(node,revisions,target)=>{calls.push({target});return true;},
        refreshCheckpoints:async()=>calls.push({refresh:true}),
    });
    const request=source.match(/^async function mutationRequest\([^]*?^}$/m)[0];
    vm.runInContext([request,handler('canAssignSelectedToPlan'),handler('assignSelectedToPlan')].join('\n'),context);
    return {context,calls,changeMarker:value=>marker=value};
}
{
    const t=fixture();
    assert.equal(t.context.canAssignSelectedToPlan(),true,'seven-scene path already active in Original is assignable to secondary');
    await t.context.assignSelectedToPlan();
    assert.equal(t.calls[0].path,`/minimax_h3_context_loop/checkpoint-revisions/restore?branch_id=${secondary}`);
    assert.equal(t.calls[0].body.branch_id,secondary);
    assert.equal(t.calls[0].body.revisions.length,7);
    assert.equal(t.calls[0].body.activate_only,true);
    assert.equal(t.calls[1].target,secondary);
    assert.equal(t.context.node.properties.h3_working_branch_id,'main','manager browsing/output must not change');
}
{
    const t=fixture();
    t.changeMarker({run:'other-project',branch:secondary});
    await t.context.assignSelectedToPlan();
    assert.equal(t.calls.length,0,'never assign across project bindings');
}
{
    const t=fixture(); const request=t.context.jsonRequest;
    t.context.jsonRequest=async(...args)=>{const result=await request(...args);t.changeMarker({run:'demo',branch:'main'});return result;};
    await t.context.assignSelectedToPlan();
    assert.ok(!t.calls.some(call=>call.target),'late response must not restore settings into a different Plan branch');
    assert.match(t.context.status.textContent,/Plan view changed/);
}
console.log('Checkpoint assignment: explicit Plan branch, already-active path, duplicate labels and late response isolation pass');
