#!/usr/bin/env node
// Run the real asynchronous handlers against deferred network responses.
// No live canvas, generated media or project files are touched.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as planCore from "../web/h3_chain_plan_core.mjs";
import {applyAssetBinding} from "../web/h3_run_assets_core.mjs";

const runSource = fs.readFileSync(new URL("../web/h3_chain_run_manager.js", import.meta.url), "utf8");
const studioSource = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const tick = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
function handler(source, name, indent = "    ") {
    const match = source.match(new RegExp(`^${indent}(?:async )?function ${name}\\([^]*?^${indent}}$`, "m"));
    assert.ok(match, name);
    return match[0];
}

function restoreFixture() {
    const nodes = new Map();
    const graph = {getNodeById:id => nodes.get(id), beforeChange() {}, afterChange() {}, setDirtyCanvas() {}};
    const plan = {id:1, graph, widgets:[{name:"run_name", value:"original"}, {name:"plan_json", value:"original plan"}]};
    const loader = {id:7, type:"LoadImage", graph, widgets:[{name:"image", value:"original.png"}], outputs:[{type:"IMAGE"}]};
    const node = {id:9, graph};
    for (const item of [plan, loader, node]) nodes.set(item.id, item);
    const state = {busy:false, disposed:false, restoreEpoch:0, watchedSources:new Set()};
    const requests = [];
    const context = vm.createContext({
        state, node, app:{graph}, URLSearchParams, applyAssetBinding,
        plan, loader, currentPlan:plan, run:{run_name:"archived", asset_count:1},
        selectedRun:() => context.run, upstreamPlanNode:() => context.currentPlan,
        status:{}, window:{confirm:() => true, queueMicrotask:fn => fn()},
        setBusy:value => { state.busy = value; }, renderSelection() {}, syncAssetBindings() {},
        restoreConnectedPolicyInputs:() => ({unavailable:[]}), refreshRestoredPlanEditors() {},
        jsonRequest:() => new Promise(resolve => requests.push(resolve)),
        removed:null, sourceChanged() {},
    });
    vm.runInContext([
        handler(runSource, "widgetByName", ""), handler(runSource, "applyPlanInputs", ""),
        ...["captureRestoreOperation", "requireCurrentRestore", "loadRun"].map(name => handler(runSource, name)),
        runSource.match(/^    const activeRunChanged = \(\) => {[^]*?^    };/m)[0],
        runSource.match(/^    node.onRemoved = function \(\) {[^]*?^    };/m)[0],
    ].join("\n"), context);
    const response = {
        plan_inputs:{run_name:"archived", plan_json:"archived plan"}, warnings:[],
        assets:{bindings:[{binding_id:"saved-binding", node_id:7, node_type:"LoadImage", output_slot:0,
            output_type:"IMAGE", widget_name:"image", restore_value:"archived.png"}]},
    };
    return {context, graph, nodes, plan, loader, node, state, requests, response};
}

for (const scenario of ["normal", "subgraph", "switch", "detach", "same-id", "rewire", "run-edit", "prompt-edit", "run-aba", "remove"]) {
    const f = restoreFixture();
    if (scenario === "subgraph") f.context.app.graph = {id:"root"};
    const pending = f.context.loadRun();
    assert.equal(f.requests.length, 1);
    const foreignLoader = {id:7, type:"LoadImage", widgets:[{name:"image", value:"other-workflow.png"}], outputs:[{type:"IMAGE"}]};
    const foreign = {getNodeById:id => id === 7 ? foreignLoader : null, _nodes:[foreignLoader]};
    if (scenario === "switch" || scenario === "detach") f.context.app.graph = foreign;
    if (scenario === "detach") { f.node.graph = null; f.plan.graph = null; }
    if (scenario === "same-id") f.nodes.set(9, {id:9, graph:f.graph});
    if (scenario === "rewire") f.context.currentPlan = {...f.plan};
    if (scenario === "run-edit") f.plan.widgets[0].value = "another-run";
    if (scenario === "prompt-edit") f.plan.widgets[1].value = "newer prompt";
    if (scenario === "run-aba") {
        f.plan.widgets[0].value = "temporary";
        vm.runInContext("activeRunChanged()", f.context);
        f.plan.widgets[0].value = "original";
        vm.runInContext("activeRunChanged()", f.context);
    }
    if (scenario === "remove") f.node.onRemoved();
    const before = JSON.stringify(f.plan.widgets);
    f.requests[0](f.response);
    await pending;
    assert.equal(foreignLoader.widgets[0].value, "other-workflow.png", scenario);
    if (scenario === "normal" || scenario === "subgraph") {
        assert.equal(f.loader.widgets[0].value, "archived.png");
        assert.equal(f.plan.widgets[1].value, "archived plan");
    } else {
        assert.equal(JSON.stringify(f.plan.widgets), before, scenario);
        assert.equal(f.loader.widgets[0].value, "original.png", scenario);
        assert.match(f.context.status.textContent, /load cancelled/, scenario);
    }
}

function editorialFixture() {
    const requests = [], timers = new Map(); let timerId = 0;
    const state = {plan:{shots:[{id:"one", prompt:[], length:345}], chapters:[]},
        editorial:{}, checkpoints:new Map()};
    const context = vm.createContext({
        ...planCore, state, structuredClone, node:{properties:{}}, alternateTakeWidget:null,
        branches:{ready:true, conflict:"", draftRecovery:null},
        currentRun:"run_a", runName:() => context.currentRun,
        currentBranch:() => "main", scopedPath:path => path,
        dirty() {}, renderStatus() {}, cacheStudioPresentation() {}, flushHistoryDraft:async () => {},
        console:{warn() {}}, projectMutationOptions:async (_node, _run, options) => options,
        setTimeout:fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout:id => timers.delete(id),
        api:{fetchApi:(_route, options) => new Promise(resolve => requests.push({body:JSON.parse(options.body), resolve}))},
    });
    vm.runInContext(["normalizedEditorial", "editorialPayload", "applyEditorialPayload", "editorialSignature",
        "syncAlternateTakeWidget", "persistEditorial", "scheduleEditorialSave", "flushProjectWrites"]
        .map(name => handler(studioSource, name)).join("\n"), context);
    const incoming = {run_name:"run_a", revision:"a".repeat(32), chapters:[], scene_order:[{scene:1, scene_id:"one"}],
        placements:[], trims:[], locked_scene_ids:[], replacements:[], alternate_draft:null,
        subtitles:{mode:"off", asset_id:"", offset_seconds:0}};
    context.applyEditorialPayload(incoming);
    return {context, state, requests, incoming,
        edit(frame) { state.editorial.placements = [{scene:1, scene_id:"one", start_frame:frame}]; context.scheduleEditorialSave(); },
        async fire() { const work = [...timers.values()]; timers.clear(); for (const fn of work) fn(); await tick(); },
        async respond(index, revision, ok = true) {
            requests[index].resolve({ok, status:ok ? 200 : 409, json:async () => ok
                ? {editorial:{...requests[index].body, revision}}
                : {error:"Newer editorial data was not overwritten."}});
            await tick();
        },
    };
}

for (const blocked of [{ready:false}, {conflict:"stale workflow"}, {draftRecovery:{}}]) {
    const f = editorialFixture();
    Object.assign(f.context.branches, blocked);
    f.edit(24); await f.fire();
    assert.equal(f.requests.length, 0, "Unbound or unresolved branch must not publish editorial edits");
}
{
    const f = editorialFixture();
    f.edit(24); await f.fire();
    f.edit(48); await f.fire();
    assert.equal(f.requests.length, 1, "The second edit must wait for the first save");
    assert.equal(f.requests[0].body.base_revision, "a".repeat(32));
    await f.respond(0, "b".repeat(32));
    assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].body.base_revision, "b".repeat(32));
    assert.equal(f.requests[1].body.placements[0].start_frame, 48);
    await f.respond(1, "c".repeat(32));
    assert.equal(f.state.editorial.placements[0].start_frame, 48);
    assert.equal(f.state.editorial.revision, "c".repeat(32));
}
{
    const f = editorialFixture();
    f.edit(24);
    const flush = f.context.flushProjectWrites("run_a");
    await tick();
    assert.equal(f.requests.length, 1, "Closing/switching flushes the debounced edit");
    f.context.currentRun = "run_b";
    f.state.editorialRun = "run_b";
    f.state.editorial = {revision:"d".repeat(32)};
    await f.respond(0, "b".repeat(32)); await flush;
    assert.equal(f.requests[0].body.run_name, "run_a");
    assert.equal(f.state.editorial.revision, "d".repeat(32), "Old response cannot alter new Run");
    await f.fire();
    assert.equal(f.requests.length, 1, "The cleared debounce cannot send twice");
}
{
    const f = editorialFixture();
    f.edit(24); await f.fire();
    f.edit(48); await f.fire();
    await f.respond(0, "", false);
    assert.equal(f.requests[1].body.base_revision, "a".repeat(32), "A conflict must never advance the read revision");
    await f.respond(1, "", false);
    assert.equal(f.state.editorial.placements[0].start_frame, 48);
    assert.equal(f.state.editorial.revision, "a".repeat(32));
    assert.match(f.state.editorialSaveError, /not overwritten/);
    assert.equal(f.context.applyEditorialPayload(f.incoming), false,
        "Periodic refresh must not erase failed, unsaved edits");
    assert.equal(f.state.editorial.placements[0].start_frame, 48);
}
{
    const f = editorialFixture();
    f.edit(24); await f.fire();
    const getEpoch = f.state.editorialEditEpoch;
    await f.respond(0, "b".repeat(32));
    assert.equal(f.context.applyEditorialPayload(f.incoming, getEpoch), false,
        "A GET started before the save committed must be ignored");
    assert.equal(f.state.editorial.placements[0].start_frame, 24);
}
{
    const f = editorialFixture();
    f.edit(24); await f.fire();
    f.edit(48);
    const flush = f.context.flushProjectWrites("run_a");
    await tick();
    f.context.currentRun = "run_b";
    f.state.editorialRun = "run_b";
    f.state.editorial = {};
    // Return to A before either old POST finishes (a fresh binding).
    f.context.currentRun = "run_a";
    f.state.editorialRun = "run_a";
    f.state.editorial = {revision:"d".repeat(32)};
    await f.respond(0, "b".repeat(32));
    assert.equal(f.requests[1].body.run_name, "run_a");
    assert.equal(f.requests[1].body.base_revision, "b".repeat(32),
        "Queued old-Run saves inherit their own preceding commit, not the new view");
    await f.respond(1, "c".repeat(32)); await flush;
    assert.equal(f.state.editorial.revision, "d".repeat(32),
        "An ABA Run switch must not adopt an old view's revision");
}
console.log("Release audit: archive restore isolation, editorial ordering, flush and revision conflicts pass");
