#!/usr/bin/env node

// Run the production size helpers and extension lifecycle hooks. This fixture
// reproduces creation before configure, host auto-fit during tab restoration,
// and reconstruction from a serialized workflow without mounting the editor.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const timers = [];
const app = {configuringGraph:false, graph:{_nodes:[]}, registerExtension(value) { this.extension = value; }};
const context = vm.createContext({
    app, Number, setTimeout:callback => timers.push(callback),
    nodeType:node => node.type, allNodes:graph => graph._nodes,
    mount:node => {
        if (node._h3PlanStudioMounted) return;
        node._h3PlanStudioMounted = true;
        context.restoreStudioNodeSize(node);
    },
});
for (const name of ["NODE_NAME", "MIN_WIDTH", "MIN_HEIGHT", "SIZE_PROPERTY"]) {
    vm.runInContext(source.match(new RegExp(`^const ${name} = [^;]+;`, "m"))[0], context);
}
for (const name of ["studioNodeSize", "restoreStudioNodeSize"]) {
    vm.runInContext(source.match(new RegExp(`^function ${name}\\([^]*?^}`, "m"))[0], context);
}
vm.runInContext(source.slice(source.lastIndexOf("app.registerExtension({")), context);
const extension = app.extension;
const sizeKey = "h3_plan_studio_size";
const pair = value => Array.from(value);

class Studio {
    constructor() {
        this.type = "MiniMaxH3ChainPlanStudio";
        this.properties = {};
        this.size = [391, 806];
        this.graph = {setDirtyCanvas() {}};
        this.resizeCalls = 0;
        this.configureCalls = 0;
        this.refreshes = 0;
        this._h3PlanStudioRefresh = () => this.refreshes++;
    }
    onConfigure() {
        this.configureCalls++;
        // Another host configure callback computes a plain widget-fit size.
        this.setSize([391, 806]);
        return "configured";
    }
    onResize() { this.resizeCalls++; return "resized"; }
    setSize(size) { this.size = pair(size); this.onResize(this.size); }
    serialize() { return JSON.parse(JSON.stringify({size:this.size, properties:this.properties})); }
}
await extension.beforeRegisterNodeDef(Studio, {name:"MiniMaxH3ChainPlanStudio"});

async function openTab(saved, mountFirst = true) {
    app.configuringGraph = true;
    const node = new Studio();
    app.graph._nodes = [node];
    if (mountFirst) await extension.nodeCreated(node);
    node.properties = structuredClone(saved.properties ?? {});
    node.size = pair(saved.size);
    assert.equal(node.onConfigure(saved), "configured");
    // A later host pass must not overwrite the saved viewport, even if its
    // temporary size is larger than the minimum and invokes onResize.
    node.setSize([900, 800]);
    if (!mountFirst) await extension.nodeCreated(node);
    await extension.afterConfigureGraph();
    app.configuringGraph = false;
    while (timers.length) timers.shift()();
    return node;
}

for (const mountFirst of [true, false]) {
    const oldWorkflow = {size:[2528, 1024], properties:{unrelated:"keep"}};
    let node = await openTab(oldWorkflow, mountFirst);
    assert.deepEqual(node.size, [2528, 1024], "restore the old workflow's serialized dimensions");
    assert.deepEqual(oldWorkflow, {size:[2528, 1024], properties:{unrelated:"keep"}}, "do not mutate the input snapshot");
    assert.equal(node.configureCalls, 1);
    assert.equal(node.refreshes, 1);
    assert.equal(node.properties.unrelated, "keep");

    for (const requested of [[2800, 1500], [1100, 900], [820, 690]]) {
        node.setSize(requested);
        assert.deepEqual(pair(node.properties[sizeKey]), requested, "remember both growing and deliberate shrinking");
        const snapshot = node.serialize();
        // The explicit viewport survives even if a host/tab snapshot contains
        // the transient native-widget size rather than the editor viewport.
        snapshot.size = [391, 806];
        node = await openTab(snapshot, !mountFirst);
        assert.deepEqual(node.size, requested, "tab round trip preserves the chosen size");
    }
    const before = node.resizeCalls;
    await extension.afterConfigureGraph();
    assert.equal(node.resizeCalls, before, "no resize feedback loop for an unchanged viewport");
}

// Existing undersized files recover a usable minimum; the original height is
// kept. Sizes and preferences belong to this node, not a run, branch or ID.
const narrow = await openTab({size:[390.8501953125, 806]});
assert.deepEqual(narrow.size, [820, 806]);
assert.deepEqual((await openTab({size:[1400, 1200]})).size, [1400, 1200]);
assert.deepEqual((await openTab(narrow.serialize())).size, [820, 806]);
for (const invalid of [[NaN, 10], [Infinity, 1000], [-1, 1000], [0, 0], null]) {
    const node = await openTab({size:[1600, 1000], properties:{[sizeKey]:invalid}});
    assert.deepEqual(node.size, [1600, 1000], "invalid viewport data falls back to node.size");
    const before = pair(node.properties[sizeKey]);
    node.onResize(invalid);
    assert.deepEqual(pair(node.properties[sizeKey]), before, "ignore invalid resize callbacks");
}

// No prototype/global resize patch: other node types remain untouched.
class Other { onConfigure() {} onResize() {} }
const originalResize = Other.prototype.onResize;
await extension.beforeRegisterNodeDef(Other, {name:"MiniMaxH3ChainPlan"});
assert.equal(Other.prototype.onResize, originalResize);
assert.match(source, /domWidget\.serialize = false;\s+restoreStudioNodeSize\(node\);/);
assert.doesNotMatch(source, /setInterval\([^\n]*restoreStudioNodeSize/);
console.log("H3 Plan Studio node size: tab restore, manual resize, minimum, isolation and callback contracts pass");
