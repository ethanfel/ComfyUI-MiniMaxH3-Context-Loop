import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/h3_project_ownership.mjs", import.meta.url), "utf8");
const graph = {}; const otherGraph = {};
const listeners = new Map();
let owned = false;
const ownerContext = vm.createContext({
    console, app:{graph}, setTimeout:() => 1, clearTimeout(){},
    api:{addEventListener:(event, callback) => listeners.set(event, callback),
        fetchApi:async (_route, options) => {
            const request = JSON.parse(options.body);
            if (request.action === "force") owned = true;
            return {ok:true, json:async () => ({run_name:request.run_name,
                owned_by_requester:owned, owner_label:"Other tab", epoch:owned ? 2 : 1})};
        }},
});
vm.runInContext(source.replace(/^import .*;\n/gm, "").replace(/^export /gm, ""), ownerContext);
function handler(text, name) {
    const match = text.match(new RegExp(`^    (?:async )?function ${name}\\([^]*?^    }$`, "m"));
    assert.ok(match, name);
    return match[0];
}
const controller = ownerContext.registerProjectOwnership({graph});
await controller.select("project_a");
const denied = await ownerContext.projectMutationOptions({graph}, "project_a").catch(error => error);
assert.match(denied.message, /is read-only here/);
const fixtures = [];
for (const filename of ["h3_chain_scene_prompt_editor.js", "h3_chain_rich_scene_prompt_editor.js", "h3_chain_plan_studio.js"]) {
    const text = fs.readFileSync(new URL(`../web/${filename}`, import.meta.url), "utf8");
    const requests = []; const status = {};
    let run = "project_a";
    const context = vm.createContext({
        state:{disposed:false, active:7, status,
            history:{sceneKey:"project_a\u0000main\u0000scene_8", error:denied.message, loadToken:0,
                status, textarea:{value:"Unsubmitted prompt", selectionStart:4, selectionEnd:9}}},
        isProjectReadOnlyError:ownerContext.isProjectReadOnlyError,
        planRunName:() => run, runName:() => run,
        planBranchId:() => 'main', currentBranch:() => 'main',
        historySceneKey:(runName, id) => `${runName}\u0000main\u0000${id}`,
        historyKey:id => `${run}\u0000main\u0000${id}`,
        renderHistory(){},
        historyRequest:async (query, body = null) => {
            requests.push({query, body});
            return {history:{active_revision:"saved", revisions:[]}};
        },
    });
    vm.runInContext(["loadHistory", "onProjectOwnershipChanged"].map(name => handler(text, name)).join("\n"), context);
    const unsubscribe = ownerContext.subscribeProjectOwnership({graph}, context.onProjectOwnershipChanged);
    fixtures.push({context, requests, unsubscribe, setRun:value => { run = value; }});
    assert.match(text, /unsubscribeOwnership\(\)/, "listeners are removed with the node");
    assert.match(text, /h3_project_ownership\.mjs\?v=0\.7\.4/);
}
let foreignEvents = 0;
ownerContext.subscribeProjectOwnership({graph:otherGraph}, () => foreignEvents++);
await controller.force();
await Promise.resolve();
for (const {context, requests} of fixtures) {
    assert.equal(context.state.history.error, "", "unlock clears the cached denial immediately");
    assert.equal(context.state.history.revisionId, "saved");
    assert.equal(requests.length, 1);
    assert.equal(requests[0].query.scene_id, "scene_8");
    assert.equal(requests[0].body, null, "unlock refresh is read-only, never a replayed POST");
    assert.equal(context.state.active, 7);
    assert.equal(context.state.history.textarea.value, "Unsubmitted prompt");
    assert.equal(context.state.history.textarea.selectionStart, 4);
    assert.equal(context.state.history.textarea.selectionEnd, 9);
    assert.match(context.state.status.textContent, /not retried/);
}
assert.equal(foreignEvents, 0, "ownership updates are scoped to the owning workflow graph");
for (const {context} of fixtures) context.state.history.error = "Disk full";
await controller.request("heartbeat");
for (const {context, requests} of fixtures) {
    assert.equal(context.state.history.error, "Disk full", "unrelated errors stay visible");
    assert.equal(requests.length, 1);
    context.state.history.error = denied.message;
}
owned = false;
await controller.request("status");
for (const {context} of fixtures) assert.equal(context.state.history.error, denied.message);
owned = true;
listeners.get("minimax_h3_project_ownership")({detail:{run_name:"project_a"}});
for (let i = 0; i < 12; i++) await Promise.resolve();
for (const fixture of fixtures) {
    assert.equal(fixture.context.state.history.error, "", "server ownership events also refresh the current entry");
    fixture.context.state.history.error = denied.message;
    fixture.setRun("project_b");
}
await controller.force();
for (const fixture of fixtures) {
    assert.equal(fixture.context.state.history.error, denied.message, "old Run event is ignored after switching");
    fixture.setRun("project_a");
    fixture.unsubscribe();
}
await controller.force();
for (const {context} of fixtures) assert.equal(context.state.history.error, denied.message, "disposed subscription is silent");
controller.dispose();
console.log("Ownership/editor refresh: force, websocket, graph/run isolation, read-only refresh and cleanup pass");
