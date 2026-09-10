import {test} from 'node:test';
import assert from 'node:assert/strict';
import {StudioBranches, BranchDrafts} from '../web/h3_working_branches.mjs';
import {studioBranchCommands} from '../web/h3_branch_commands.mjs';

async function fixture() {
    let authoring = {plan_json:JSON.stringify({shots:[{id:'one',prompt:'first',seed:'18446744073709551615'}]})};
    const writes = [], values = new Map(), records = new Map([
        ['main',{id:'main',name:'Original',revision:'1',authoring:structuredClone(authoring)}],
        ['a'.repeat(32),{id:'a'.repeat(32),name:'Second',revision:'2',authoring:{plan_json:JSON.stringify({shots:[{id:'one',prompt:'second',seed:'1'}]})}}],
    ]);
    const controller = new StudioBranches({capture:() => structuredClone(authoring), apply:async record => { authoring = structuredClone(record.authoring); },
        drafts:new BranchDrafts({getItem:key => values.get(key) ?? null,setItem:(key,value) => values.set(key,value),removeItem:key => values.delete(key)},'test'),
        changed(){}, flush:async () => {}, request:async body => {
            if (body.action === 'list') return {branches:[...records.values()],default_branch:'main'};
            if (body.action === 'load') return structuredClone(records.get(body.branch_id));
            writes.push(body);
            if (body.action === 'save' && records.get(body.branch_id).revision !== body.revision) throw Object.assign(Error('Stale saved branch'),{status:400});
            const id = body.action === 'create' ? body.operation_id : body.branch_id;
            const record = {id,name:body.name || records.get(id)?.name,revision:String(writes.length+2),authoring:body.authoring};
            records.set(id,structuredClone(record)); return record;
        }});
    const owner = {}, bridge = studioBranchCommands(controller,{owner:() => owner});
    await controller.refresh('film'); controller.observe();
    return {controller,bridge,owner,writes,records,values,edit:() => {authoring.plan_json=authoring.plan_json.replace('first','edited');controller.observe();},capture:() => authoring};
}

test('snapshot is detached and exposes status without prompt or recovery bodies',async () => {
    const f = await fixture(), state = f.bridge.snapshot();
    assert.equal(f.bridge.owner,f.owner); assert.equal(state.dirty,false);
    assert.equal(JSON.stringify(state).includes('plan_json'),false);
    state.branches[0].name='changed'; assert.equal(f.bridge.snapshot().branches[0].name,'Original');
    f.edit(); assert.equal(f.bridge.snapshot().dirty,true);
    assert.ok(f.bridge.snapshot().revision > state.revision);
});
test('save and switch use native saves, preserve exact seeds, and apply the saved branch',async () => {
    const f = await fixture(); f.edit();
    const result = await f.bridge.command('switch',{target:'a'.repeat(32)},f.bridge.snapshot());
    assert.equal(result.warning,undefined); assert.equal(result.state.busy,false);
    assert.equal(result.state.selected,'a'.repeat(32));
    assert.equal(JSON.parse(f.writes[0].authoring.plan_json).shots[0].seed,'18446744073709551615');
    assert.equal(JSON.parse(f.capture().plan_json).shots[0].prompt,'second');
});
test('navigation without saving keeps the native recovery draft',async () => {
    const f = await fixture(); f.edit();
    await f.bridge.command('switch',{target:'a'.repeat(32),save:false},f.bridge.snapshot());
    assert.equal(f.writes.length,0);
    assert.match(f.controller.drafts.read('film','main').authoring.plan_json,/edited/);
});
test('stale views, busy native operations and unsupported actions never write',async () => {
    const f = await fixture(), old=f.bridge.snapshot(); f.edit();
    await assert.rejects(f.bridge.command('save',{},old),/state changed/);
    f.controller.busy=true;
    await assert.rejects(f.bridge.command('save',{},f.bridge.snapshot()),/in progress/);
    f.controller.busy=false;
    await assert.rejects(f.bridge.command('erase',{},f.bridge.snapshot()),/Unsupported/);
    assert.equal(f.writes.length,0);
});
test('attachment loss after flush cannot save or apply a new branch',async () => {
    const f = await fixture(); let attached=true;
    f.controller.flush=async () => { attached=false; };
    await assert.rejects(f.bridge.command('switch',{target:'a'.repeat(32)},f.bridge.snapshot(),() => {if(!attached)throw Error('Detached');}),/Detached/);
    assert.equal(f.writes.length,0); assert.equal(f.controller.selected,'main');
    assert.equal(f.bridge.snapshot().busy,false);
});
test('stale server save produces a warning and leaves the current Plan intact',async () => {
    const f=await fixture(); f.records.get('main').revision='remote';
    const result=await f.bridge.command('switch',{target:'a'.repeat(32)},f.bridge.snapshot());
    assert.match(result.warning,/Stale/); assert.equal(result.state.selected,'main');
});
test('create and uncertain retry retain the native operation ID',async () => {
    const f=await fixture(), request=f.controller.request; let fail=true;
    f.controller.request=async body => { if(body.action==='create' && fail)throw Error('Network gone');return request(body); };
    const result=await f.bridge.command('create',{name:'New'},f.bridge.snapshot());
    assert.match(result.warning,/uncertain/); const pending=f.bridge.snapshot().pending;
    assert.equal(pending.action,'create'); assert.equal(JSON.stringify(pending).includes('plan_json'),false);
    fail=false; await f.bridge.command('retry',{},f.bridge.snapshot());
    assert.equal(f.writes.at(-1).operation_id,pending.operation_id);
    assert.equal(f.bridge.snapshot().pending,null);
    assert.ok(f.bridge.snapshot().branches.some(item => item.id===pending.operation_id));
});
