import assert from "node:assert/strict";
import {checkpointForkGraph, checkpointGraphOutput, checkpointGraphKey, checkpointGraphEdgeKey,
    checkpointSaveOrder, mountCheckpointGraphEdges} from "../web/h3_checkpoint_graph.mjs";

const take = (scene, id) => ({scene, revision:id.repeat(32)});
const a = take(1, "a"), b = take(2, "b"), c = take(3, "c"), d = take(2, "d"), e = take(3, "e");
const key = item => checkpointGraphKey("original", item);
const edge = (left, right) => checkpointGraphEdgeKey(key(left), key(right));
const rows = [{revisions:[a,b,c]}, {revisions:[a,d,e]}, {revisions:[a,b]}];
const before = JSON.stringify(rows);
const graph = checkpointForkGraph(rows);
assert.equal(graph.nodes.length, 5, "Shared clips are rendered once, including prefix-only paths");
assert.equal(graph.lanes, 2);
assert.equal(graph.columns, 3);
assert.deepEqual(graph.nodes.find(item => item.key === key(a)).paths, [0,1,2]);
assert.deepEqual(graph.nodes.find(item => item.key === key(b)).ends.map(item => item.index), [2]);
assert.deepEqual(new Set(graph.edges.map(item => item.key)), new Set([edge(a,b), edge(b,c), edge(a,d), edge(d,e)]));
assert.equal(graph.nodes.find(item => item.key === key(d)).column, 1);
assert.equal(graph.nodes.find(item => item.key === key(d)).lane, 1);
assert.equal(JSON.stringify(rows), before);

const reuse = (parent, candidates = [e]) => ({scene:parent.scene + 1,
    parent_scene:parent.scene, parent_revision:parent.revision, candidates});
const reuseRows = [{revisions:[a,b,c]}, {revisions:[a,d], attribution_slot:reuse(d)},
    {revisions:[a,b], attribution_slot:reuse(b)},
    {revisions:[a,b], attribution_slot:reuse(b)}];
const reuseBefore = JSON.stringify(reuseRows);
const reusable = checkpointForkGraph(reuseRows);
assert.equal(reusable.nodes.length,4,"Reuse slots are not saved revisions");
assert.equal(reusable.slots.length,2,"A repeated tip has a single attachment control");
assert.equal(reusable.slots[0].column,2,"Scene 3 reuse belongs in the scene 3 column");
assert.equal(reusable.slots[0].lane,1,"A leaf's reuse slot follows horizontally");
assert.equal(reusable.slots[1].lane,2,"A shared tip with an existing continuation needs a free lane");
assert.equal(new Set([...reusable.nodes,...reusable.slots].map(item=>`${item.column}:${item.lane}`)).size,6);
assert.equal(reusable.edges.filter(item=>item.kind==="reuse").length,2);
assert.equal(JSON.stringify(reuseRows),reuseBefore);
const extended = checkpointForkGraph([{revisions:[a,b,c], attribution_slot:reuse(c)}]);
assert.equal(extended.columns,4,"The next-scene column exists even without a saved take there");
assert.equal(extended.slots[0].column,3);
assert.equal(checkpointForkGraph([{revisions:[a,b], attribution_slot:{...reuse(b,[]),blocked_candidates:[e]}}]).slots.length,1);
for (const slot of [null, reuse(b,[]), {...reuse(b),scene:4}, {...reuse(b),parent_revision:d.revision},
    {...reuse(b),parent_scene:1}]) {
    assert.equal(checkpointForkGraph([{revisions:[a,b],attribution_slot:slot}]).slots.length,0);
}

// Ordering uses the full available scene inventory and actual instants. It
// must not infer chronology from branch lane, UUID, current selection or mtime.
const old = {...b, created_at:"2026-09-08T10:00:00Z"};
const newer = {...d, created_at:"2026-09-08T13:00:00+02:00"};
let order = checkpointSaveOrder([newer,a,old,old]);
assert.equal(order.get(key(old)).label,"Save #1 of 2");
assert.equal(order.get(key(newer)).label,"Save #2 of 2 · Latest");
assert.equal(order.get(key(a)).label,"Save order unknown");
order = checkpointSaveOrder([old,{...newer,created_at:old.created_at}]);
assert.equal(order.get(key(old)).label,"Save #1–2 of 2 · same time · Latest");
assert.equal(order.get(key(newer)).label,order.get(key(old)).label);
order = checkpointSaveOrder([old,{...newer,created_at:"bad date"}]);
assert.equal(order.get(key(old)).label,"Dated save #1 of 1 · Latest dated");
assert.equal(order.get(key(newer)).latest,false);

const selection = {run_name:"demo", lineage:[a,b,c], scope_start_scene:1, scope_end_scene:3};
let used = checkpointGraphOutput(JSON.stringify(selection), "demo");
assert.deepEqual(used.nodes, new Set([key(a), key(b), key(c)]));
assert.deepEqual(used.edges, new Set([edge(a,b), edge(b,c)]));
assert.ok(!used.edges.has(edge(a,d)), "Alternative edge cannot light up just because its parent is used");
used = checkpointGraphOutput({...selection, lineage:[a,d,e]}, "demo");
assert.deepEqual(used.edges, new Set([edge(a,d), edge(d,e)]));
assert.equal(used.tip, key(e));
used = checkpointGraphOutput({...selection, output_scope:"chapter", scope_start_scene:2}, "demo");
assert.deepEqual(used.nodes, new Set([key(b),key(c)]));
assert.deepEqual(used.edges, new Set([edge(b,c)]));
for (const [value,run,branch] of [["invalid","demo","main"], [selection,"other","main"],
    [selection,"demo","f".repeat(32)], [{...selection,lineage:[a,c,b]},"demo","main"]]) {
    assert.equal(checkpointGraphOutput(value,run,branch).nodes.size,0);
}
const p = {scene:1, revision:a.revision, metadata_path:"demo/pixel/a.json", checkpoint_sha256:"a".repeat(64), record:a};
const q = {...p, scene:2, revision:b.revision, metadata_path:"demo/pixel/b.json", record:null};
const r = {...q, revision:d.revision, metadata_path:"demo/pixel/d.json", record:d};
const processed = checkpointForkGraph([{profile_path:"demo/pixel", entries:[p,q]}, {profile_path:"demo/pixel", entries:[p,r]},
    {profile_path:"another/profile", entries:[p]}], "pixel_upscale");
assert.equal(processed.nodes.length,4,"Profile identity is part of the key");
assert.equal(processed.nodes.find(item=>item.entry===q).entry.record,null,"Missing exact take stays a gap");
assert.equal(checkpointGraphOutput(selection,"demo","main","pixel_upscale").nodes.size,0,"A preview is not a processing output selection");
const derope = {...selection, processing_source:{stage:"derope",profile_path:"demo/pixel",branch:{lineage:[p,q]}}};
assert.equal(checkpointGraphOutput(derope,"demo","main","derope").nodes.size,2);
const firstProfile = {...old,profile_path:"profile1",key:"profile1/b"};
const secondProfile = {...newer,profile_path:"profile2",key:"profile2/d"};
order = checkpointSaveOrder([secondProfile,firstProfile],"pixel_upscale");
assert.equal(order.get(checkpointGraphKey("pixel_upscale",secondProfile,"profile2")).label,"Save #2 of 2 · Latest");
assert.equal(checkpointForkGraph([{entries:[p],attribution_slot:reuse(a)}],"pixel_upscale").slots.length,0);

// SVG connectors must stay attached under ComfyUI CSS/canvas zoom and reflow.
class Element {
    constructor(){this.children=[];this.attrs={};this.dataset={};}
    setAttribute(name,value){this.attrs[name]=value;}
    append(item){this.children.push(item);}
    prepend(item){this.children.unshift(item);}
    replaceChildren(){this.children=[];}
}
const host = new Element(); host.isConnected=true;host.offsetWidth=408;host.offsetHeight=220;
host.getBoundingClientRect=()=>({left:100,top:50,width:204,height:110}); // 50% zoom
let y=125, observer, cancelled=0;
const measured = new Map([[key(a),{getBoundingClientRect:()=>({left:100,right:190,top:50,height:40})}],
    [key(b),{getBoundingClientRect:()=>({left:214,right:304,top:y,height:40})}]]);
const callbacks = new Map();let serial=0;
const win = {requestAnimationFrame:fn=>{callbacks.set(++serial,fn);return serial;},
    cancelAnimationFrame:id=>{callbacks.delete(id);cancelled++;},
    ResizeObserver:class {constructor(fn){observer=this;this.fn=fn;} observe(){} disconnect(){this.closed=true;}},
    addEventListener(){},removeEventListener(){}};
const cleanup = mountCheckpointGraphEdges(host,{edges:[{key:edge(a,b),from:key(a),to:key(b)}]},measured,
    {edges:new Set([edge(a,b)])},{createElementNS:()=>new Element()},win);
const paint=()=>{for(const fn of callbacks.values())fn();callbacks.clear();};
paint();
let path = host.children[0].children[0];
assert.match(path.attrs.d,/^M 180 40 C/);
assert.match(path.attrs.d,/223 190/);
assert.match(path.attrs.class,/edge-output/);
y=150;observer.fn();paint();
path=host.children[0].children[0];assert.match(path.attrs.d,/223 240/);
observer.fn();cleanup();assert.ok(observer.closed);assert.equal(cancelled,1);
const cleanupReuse = mountCheckpointGraphEdges(host,{edges:[{key:edge(a,b),from:key(a),to:key(b),kind:"reuse"}]},measured,
    {edges:new Set([edge(a,b)])},{createElementNS:()=>new Element()},win);
paint();path=host.children[0].children[0];
assert.match(path.attrs.class,/edge-reuse/);
assert.ok(!path.attrs.class.includes("edge-output"),"Proposed reuse is never highlighted as saved output");
cleanupReuse();
console.log("Checkpoint fork graph: exact shared paths, next-scene reuse slots, save order/ties, output scopes, zoom/reflow and cleanup pass");
