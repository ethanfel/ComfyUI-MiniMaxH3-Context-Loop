import assert from "node:assert/strict";
import {checkpointForkGraph, checkpointGraphOutput, checkpointGraphKey, checkpointGraphEdgeKey,
    mountCheckpointGraphEdges} from "../web/h3_checkpoint_graph.mjs";

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
console.log("Checkpoint fork graph: exact shared nodes/edges, output scopes, profile isolation, missing takes, zoom/reflow and cleanup pass");
