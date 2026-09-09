import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_chain_plan_core.mjs";

const carousel = fs.readFileSync(new URL("../web/h3_project_asset_manager.js", import.meta.url), "utf8");
const studio = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
function handler(source, name) {
    const match = source.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`, "m"));
    assert.ok(match, name);
    return match[0];
}

// Exercise the real handlers, including their await boundaries (not regex-only checks).
function carouselContext() {
    const requests = [];
    const runNameWidget = {value:"run_a"};
    const context = vm.createContext({
        state:{catalog:{project:"run_a"}, selected:"keep", uploading:false},
        runNameWidget, runNameInput:{value:"run_a"}, catalogWidget:{},
        node:{graph:{setDirtyCanvas(){}}}, project:() => runNameWidget.value,
        ownership:{runName:"run_a", async select(run) { this.runName = run; }},
        projectMutationOptions:async (_node, _run, options) => options,
        jsonRequest:(route, options) => new Promise((resolve, reject) => requests.push({route, options, resolve, reject})),
        syncDownstreamPlan:(_node, run) => { context.planRun = run; },
        publishProjectAssetCatalogChanged(){}, render(){}, setStatus(){},
        promptTag:asset => asset.tag, window:{confirm:() => true},
        FormData:class { constructor() { this.fields = {}; } append(key, value) { this.fields[key] = value; } },
    });
    vm.runInContext("let projectEpoch = 0; let projectDisposed = false;\n" + [
        "captureProjectOperation", "isCurrentProjectOperation", "requireCurrentProjectOperation",
        "mutationRequest", "persistCatalog", "importAsset", "uploadFiles",
        "updateAsset", "reorderAssets", "deleteAsset", "duplicateAsset", "folderRequest",
    ].map(name => handler(carousel, name)).join("\n"), context);
    return {context, requests, switchRun(run) {
        vm.runInContext("projectEpoch += 1", context);
        runNameWidget.value = run; context.runNameInput.value = run; context.planRun = run;
    }};
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const result = {catalog:{project:"run_a", assets:[]}, asset:{id:"late", tag:"ref", role:"picture"}};
for (const returnToA of [false, true]) {
    for (const method of ["importAsset", "updateAsset", "reorderAssets", "deleteAsset", "duplicateAsset", "folderRequest"]) {
        const fixture = carouselContext();
        const pending = fixture.context[method]({id:"asset", tag:"ref"}, {});
        // importAsset intentionally lets its dialog handle errors.
        const settled = Promise.resolve(pending).catch(error => assert.equal(error.staleProject, true));
        await tick();
        assert.equal(fixture.requests.length, 1);
        fixture.switchRun("run_b");
        if (returnToA) fixture.switchRun("run_a");
        fixture.requests[0].resolve(result);
        await settled;
        assert.equal(fixture.context.state.selected, "keep", method);
        assert.equal(fixture.context.planRun, returnToA ? "run_a" : "run_b", method);
        assert.equal(fixture.context.state.catalog.assets, undefined, method);
    }
}
{
    const fixture = carouselContext();
    const pending = fixture.context.uploadFiles([{name:"one.png"}, {name:"two.png"}]);
    await tick();
    assert.equal(fixture.requests[0].options.body.fields.project, "run_a");
    fixture.switchRun("run_b");
    fixture.requests[0].resolve(result);
    await pending;
    assert.equal(fixture.requests.length, 1, "remaining files must not leak into B");
    assert.equal(fixture.context.state.uploading, false);
    assert.equal(fixture.context.planRun, "run_b");
}
{
    const fixture = carouselContext();
    const pending = fixture.context.importAsset({source:"input"});
    await tick(); fixture.requests[0].resolve(result); await pending;
    assert.equal(fixture.context.state.selected, "late", "current-run import still applies");
    const token = fixture.context.captureProjectOperation();
    fixture.switchRun("run_b");
    await assert.rejects(fixture.context.mutationRequest("unused", {}, token), /Run changed/);
    assert.equal(fixture.requests.length, 1, "stale dialogs must not send a mutation");
    vm.runInContext("projectDisposed = true", fixture.context);
    await assert.rejects(fixture.context.importAsset({}), /Run changed/);
}

function studioContext() {
    const timers = new Map(); const writes = []; let id = 0;
    const context = vm.createContext({
        ...core, console, structuredClone,
        state:{plan:{shots:[{id:"scene_a", prompt:["A"], length:345}], chapters:[]},
            editorial:{}, checkpoints:new Map()},
        node:{properties:{}}, alternateTakeWidget:{value:"stale"},
        runName:() => "run_b", cacheStudioPresentation(){}, dirty(){}, renderStatus(){},
        currentBranch:() => "main", scopedPath:path => path,
        setTimeout:fn => { timers.set(++id, fn); return id; }, clearTimeout:key => timers.delete(key),
        projectMutationOptions:async (_node, _run, options) => options,
        api:{fetchApi:async (_route, options) => {
            writes.push(JSON.parse(options.body));
            return {ok:true, json:async () => ({editorial:{revision:"c".repeat(32)}})};
        }},
    });
    const names = ["normalizedEditorial", "editorialPayload", "applyEditorialPayload", "syncAlternateTakeWidget", "scheduleEditorialSave"];
    if (studio.includes("function editorialSignature(")) names.push("editorialSignature", "persistEditorial");
    vm.runInContext(names.map(name => handler(studio, name)).join("\n"), context);
    return {context, writes, async flush() {
        for (const fn of timers.values()) await fn(); timers.clear();
        await context.state.editorialSavePromise;
    }};
}
const incoming = {
    format:"h3_chain_editorial_v1", run_name:"run_b", revision:"b".repeat(32),
    chapters:[{id:"b_chapter", title:"Saved B", start_scene:1, start_scene_id:"scene_b"}],
    scene_order:[{scene:1, scene_id:"scene_b"}],
    placements:[{scene:1, scene_id:"scene_b", start_frame:48}],
    trims:[{scene:1, scene_id:"scene_b", out_frame:90}], locked_scene_ids:["scene_b"],
    subtitles:{mode:"off", asset_id:"", offset_seconds:0}, alternate_draft:null, replacements:[],
};
{
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(incoming);
    await fixture.flush();
    assert.equal(fixture.writes.length, 0, "GET must never POST destination editorial");
    assert.match(fixture.context.state.editorialBindingError, /matching Plan/);
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "mismatched Plan must not rewrite B after an unrelated edit");
    assert.equal(fixture.context.alternateTakeWidget.value, "null", "hidden widget still synchronizes");
}
{
    const fixture = studioContext();
    fixture.context.state.plan.shots[0].id = "scene_b";
    fixture.context.applyEditorialPayload(incoming);
    fixture.context.state.plan.shots[0].seed = 42;
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "seed edit must not overwrite saved chapters");
    fixture.context.state.editorial.trims[0].out_frame = 81;
    fixture.context.scheduleEditorialSave();
    fixture.context.applyEditorialPayload(incoming);
    assert.equal(fixture.context.state.editorial.trims[0].out_frame, 81, "refresh must not clobber pending edits");
    await fixture.flush();
    assert.equal(fixture.writes.length, 1);
    assert.equal(fixture.writes[0].trims[0].out_frame, 81);
    assert.deepEqual(fixture.writes[0].chapters, incoming.chapters);
    assert.deepEqual(fixture.writes[0].placements, incoming.placements);
    fixture.context.applyEditorialPayload(incoming, 0);
    assert.equal(fixture.context.state.editorial.trims[0].out_frame, 81,
        "a GET started before the edit must not clobber it after POST completes");
}
assert.doesNotMatch(handler(studio, "loadPlan"), /scheduleEditorialSave\(/);

// A removed/renamed empty placeholder is not evidence of a different Run.
// Only a fresh backend inventory may establish that it has no saved renders.
const placeholderEditorial = {
    ...incoming, chapters:[], placements:[], trims:[], locked_scene_ids:[],
    scene_order:[{scene:1, scene_id:"scene_a"}, {scene:2, scene_id:"old_empty"}],
};
{
    const fixture = studioContext();
    fixture.context.state.plan.shots.push({id:"new_empty", prompt:[], length:306});
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, ["old_empty"]);
    assert.equal(fixture.context.state.editorialBindingError, "");
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "hydration/seed edits must not reconcile on disk");
    fixture.context.state.editorial.placements.push({scene_id:"new_empty", start_frame:400});
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 1);
    assert.deepEqual(fixture.writes[0].scene_order, [
        {scene:1, scene_id:"scene_a"}, {scene:2, scene_id:"new_empty"},
    ], "an explicit edit reconciles only verified unused entries");
    assert.deepEqual(placeholderEditorial.scene_order[1], {scene:2, scene_id:"old_empty"},
        "the fetched document is never mutated");
}
for (const proof of [undefined, []]) {
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, proof);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/,
        "missing proof or a rendered missing scene remains protected");
}
for (const protectedField of [
    {chapters:[{id:"chapter", start_scene:2, start_scene_id:"old_empty"}]},
    {placements:[{scene:2, scene_id:"old_empty", start_frame:400}]},
    {trims:[{scene:2, scene_id:"old_empty", out_frame:90}]},
    {locked_scene_ids:["old_empty"]},
    {replacements:[{scene:2, scene_id:"old_empty"}]},
    {alternate_draft:{scene:2, scene_id:"old_empty"}},
]) {
    const fixture = studioContext();
    fixture.context.applyEditorialPayload({...placeholderEditorial, ...protectedField}, 0, ["old_empty"]);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/);
    fixture.context.scheduleEditorialSave(); await fixture.flush();
    assert.equal(fixture.writes.length, 0, "saved edits are never considered an unused placeholder");
}
{
    const fixture = studioContext();
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, ["old_empty"]);
    fixture.context.applyEditorialPayload(placeholderEditorial, 0, []);
    assert.match(fixture.context.state.editorialBindingError, /old_empty/,
        "a later checkpoint inventory must revoke the unused-scene allowance");
}
console.log("Run-switch safety: hydration, explicit saves, stale mutations, ABA, batches and disposal passed");
