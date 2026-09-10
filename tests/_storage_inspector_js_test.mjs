import assert from "node:assert/strict";
import {mountStorageInspector} from "../web/h3_storage_inspector.mjs";

class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.listeners = {}; this.value = "";
        this.classList = {add(){}};
    }
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children = items;}
    setAttribute(name, value) {this[name] = value;}
    addEventListener(name, callback) {this.listeners[name] = callback;}
    click() {this.listeners.click?.();}
}
globalThis.document = {createElement:tag => new Element(tag)};
const all = root => [root, ...root.children.flatMap(all)];
const find = (root, text) => all(root).find(item=>item.textContent === text);
const report = run => ({format:"h3_storage_inventory_v1",run_name:run,scan_complete:true,
    totals:{files:0,logical_bytes:0,allocated_bytes:0},categories:{},limitations:[],
    files:[],longest_paths:[],issue_counts:{},issues:[],issues_omitted:0});

let run = "first";
const pending = [];
const host = new Element("section"); host.hidden = true;
const ui = mountStorageInspector(host, {currentRun:()=>run,request:(path,options)=>
    new Promise((resolve,reject)=>pending.push({path,options,resolve,reject}))});
assert.equal(pending.length,0,"Mounting never triggers an inventory scan");
let response = ui.open();
assert.equal(pending[0].options.method,"GET");
assert.match(pending[0].path,/run_name=first$/);
run = "second"; ui.syncRun();
assert.equal(host.hidden,true,"Changing project dismisses the old inventory");
assert.equal(pending[0].options.signal.aborted,true);
pending[0].resolve(report("first")); await response;
assert.equal(find(host,"Download inventory JSON").disabled,true,"Stale response is not downloadable");
response = ui.open();
pending[1].resolve(report("second")); await response;
assert.equal(host.hidden,false);
assert.equal(find(host,"Download inventory JSON").disabled,false);
response = ui.open();
assert.equal(find(host,"Download inventory JSON").disabled,true,"Rescan invalidates old download immediately");
pending[2].reject(new Error("offline")); await response;
assert.ok(find(host,"Storage inspection failed: offline"));
assert.equal(find(host,"Rescan").disabled,false);
response = ui.open();
pending[3].resolve(report("wrong_project")); await response;
assert.ok(find(host,"Storage inspection failed: Unexpected storage inventory response."));
response = ui.open(); ui.dismiss();
pending[4].resolve(report("second")); await response;
assert.equal(host.hidden,true,"Late closed response cannot reopen the inspector");
assert.equal(find(host,"Download inventory JSON").disabled,true);
const previousRequests = pending.length;
run = ""; await ui.open();
assert.equal(pending.length,previousRequests,"No request for an empty run");
console.log("Storage Inspector: on-demand GET, stale-project/closed-response isolation, errors, retry and download invalidation pass");
