import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {projectAssetEditAttempts} from "../web/h3_project_asset_sync_core.mjs";

// Execute the actual Carousel action; transport/dialogs are isolated. Nothing
// contacts a live ComfyUI project and no second repair implementation is used.
const source = readFileSync(new URL("../web/h3_project_asset_manager.js", import.meta.url), "utf8");
assert.match(source, /button\("Repair input…", \(\) => repairProjectInputs\(\)/);
const body = source.slice(source.indexOf("    const inputRepairAttempts = new Map();"),
    source.indexOf("    async function duplicateAssetProject()"));
assert.ok(body.length > 2000);
const mount = new Function("captureProjectOperation", "requireCurrentProjectOperation", "jsonRequest",
    "mutationRequest", "window", "refresh", "setStatus", body + "\nreturn repairProjectInputs;");
const plan = () => ({format:"h3_input_asset_repair_v1", project:"demo", repairable:true,
    input_pin:{root:"immutable-root"}, files:[{path:"catalog.json", action:"preserve_restore"},
        {path:"images/hero.png", action:"restore"}]});
function setup() {
    const t = {run:"demo", epoch:1, inspections:[], mutations:[], confirms:[], statuses:[], refreshes:0,
        confirm:true, fail:false, inspection:plan()};
    const attempts = projectAssetEditAttempts(null);
    function current(operation) {
        if (operation.run !== t.run || operation.epoch !== t.epoch) {
            throw Object.assign(new Error("Project changed"), {staleProject:true});
        }
    }
    t.action = mount(() => ({run:t.run, epoch:t.epoch}), current,
        async route => { t.inspections.push(route); await t.wait; return {inspection:t.inspection}; },
        async (route, options, operation) => {
            current(operation);
            // This is the existing mutation transport's identity boundary;
            // the production action must retain the exact original body.
            const attempt = attempts.prepare(route, options);
            t.mutations.push({route, options:attempt.options, operation});
            await t.writeWait;
            current(operation);
            if (t.fail) throw new Error("lost response");
            attempt.accept();
            return {repaired_files:["catalog.json","images/hero.png"], preserved_files:["catalog.json"]};
        }, {confirm:message => {t.confirms.push(message); return t.confirm;}},
        async () => { t.refreshes++; }, (message, error=false) => t.statuses.push({message,error}));
    return t;
}

{
    const t = setup();
    assert.equal(t.inspections.length, 0, "mount and normal browsing never inspect or repair");
    t.confirm = false;
    await t.action();
    assert.equal(t.inspections.length, 1);
    assert.equal(t.mutations.length, 0, "cancel is read-only");
    assert.match(t.confirms[0], /Damaged bytes will be preserved/);
    t.confirm = true;
    await t.action();
    assert.equal(t.inspections.length, 2, "a cancelled inspection is not a pending repair");
    assert.equal(t.mutations.length, 1);
    assert.match(JSON.parse(t.mutations[0].options.body).storage_operation_id, /^[0-9a-f]{32}$/);
    assert.equal(t.refreshes, 1);
    assert.match(t.statuses.at(-1).message, /Input repair complete/);
}
{
    const t = setup(); t.fail = true;
    await t.action();
    const first = t.mutations[0].options.body;
    t.inspection = {...plan(), files:[]}; // Server tree changed after partial repair.
    t.fail = false;
    await t.action();
    assert.equal(t.inspections.length, 1, "partial repair must use the original inspection");
    assert.equal(t.mutations[1].options.body, first, "exact retry keeps original pin, files and operation ID");
    assert.match(t.confirms[1], /^Resume input repair/);
    await t.action();
    assert.equal(t.inspections.length, 2, "successful acknowledgement clears the pending attempt");
    assert.equal(t.mutations.length, 2);
    assert.match(t.statuses.at(-1).message, /nothing to repair/);
}
{
    const t = setup(); t.inspection.repairable = false;
    await t.action();
    assert.equal(t.mutations.length, 0);
    assert.equal(t.confirms.length, 0);
    assert.match(t.statuses.at(-1).message, /different readable catalog/);
}
{
    const t = setup(); let finish;
    t.wait = new Promise(resolve => finish=resolve);
    const old = t.action();
    await t.action();
    assert.equal(t.inspections.length, 1, "double click cannot create concurrent repair attempts");
    t.run = "other"; t.epoch++;
    finish(); await old;
    assert.equal(t.mutations.length, 0, "switching projects while inspecting cannot repair the old one");
    assert.equal(t.confirms.length, 0);
}
{
    const t = setup(); let finish;
    t.writeWait = new Promise(resolve => finish=resolve);
    const old = t.action();
    for (let i=0; i<8; i++) await Promise.resolve();
    assert.equal(t.mutations.length, 1);
    t.run = "other"; t.epoch++;
    finish(); await old;
    assert.equal(t.refreshes, 0, "late repair reply cannot refresh another project's Carousel");
}
{
    const t = setup(); t.inspection = {project:"other"};
    await t.action();
    assert.equal(t.mutations.length, 0);
    assert.match(t.statuses.at(-1).message, /valid input repair inspection/);
}
{
    const t = setup(); let fail;
    t.wait = new Promise((_resolve, reject) => fail=reject);
    const old = t.action();
    const statuses = t.statuses.length;
    t.run = "other"; t.epoch++;
    fail(new Error("old project's request failed")); await old;
    assert.equal(t.statuses.length, statuses, "late old-project errors cannot replace the new project's status");
}
console.log("Actual Carousel input repair: inspect/confirm, conflict refusal, exact retry and project switching pass");
