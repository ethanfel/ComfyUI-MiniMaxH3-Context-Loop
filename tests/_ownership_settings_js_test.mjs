import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {StudioBranches, BranchDrafts} from "../web/h3_working_branches.mjs";

const read = name => fs.readFileSync(new URL(`../web/${name}`, import.meta.url), "utf8");
const script = name => read(name).replace(/^import[\s\S]*?;\n/gm, "").replace(/^export /gm, "");
const settle = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };
const backend = {enabled:true, epoch:0, owner:"", ownerEpoch:0, reject:false, hold:null};
const requests = [];
const tabs = [];

function broadcast() {
    for (const tab of tabs) tab.listeners.get("minimax_h3_project_ownership_settings")({detail:{
        enabled:backend.enabled, epoch:backend.epoch,
    }});
}

function tab(cachedValue) {
    const graph = {};
    const listeners = new Map(); const timers = new Set(); const errors = [];
    let extension, definition, value = cachedValue, timerId = 0;
    const settings = {
        getSettingValue:() => value,
        setSettingValue(_id, next) {
            if (next === value) return;
            value = next;
            definition.onChange(next);
        },
        addSetting(next) { definition = next; next.onChange(value); },
    };
    const context = vm.createContext({
        console:{warn(){}, error:error => errors.push(error)},
        app:{graph, ui:{settings}, registerExtension:e => { extension = e; }},
        setTimeout:() => { const id = ++timerId; timers.add(id); return id; },
        clearTimeout:id => timers.delete(id),
        api:{addEventListener:(event, callback) => listeners.set(event, callback),
            fetchApi:async (route, options) => {
                const body = options.body ? JSON.parse(options.body) : null;
                requests.push({route, body});
                const result = payload => ({ok:true, json:async () => payload});
                if (route.endsWith("/settings")) {
                    if (options.method === "POST") {
                        if (backend.reject) return {ok:false, status:500, json:async () => ({error:"disk full"})};
                        if (backend.hold) await backend.hold;
                        if (backend.enabled !== body.enabled) {
                            backend.enabled = body.enabled; backend.epoch++; backend.owner = "";
                        }
                        broadcast();
                    }
                    return result({enabled:backend.enabled, epoch:backend.epoch});
                }
                if (backend.enabled && ["claim", "force"].includes(body.action)
                        && (!backend.owner || body.action === "force")) {
                    backend.owner = body.owner_id; backend.ownerEpoch++;
                }
                const payload = {run_name:body.run_name, locking_enabled:backend.enabled,
                    policy_epoch:backend.epoch, epoch:backend.ownerEpoch, owner_label:"Other workflow",
                    owned_by_requester:backend.enabled && backend.owner === body.owner_id};
                if (["claim", "force", "release"].includes(body.action)) {
                    for (const client of tabs) client.listeners.get("minimax_h3_project_ownership")({detail:payload});
                }
                if (backend.hold) await backend.hold;
                return result(payload);
            }},
    });
    vm.runInContext(script("h3_project_ownership.mjs"), context);
    vm.runInContext(script("h3_project_ownership_settings.js"), context);
    extension.init();
    const result = {context, graph, listeners, timers, errors, settings,
        setup:() => extension.setup(), value:() => value, definition:() => definition};
    tabs.push(result);
    return result;
}

const first = tab(false);
await first.setup();
assert.equal(first.value(), true, "server policy overrides a stale browser preference");
assert.equal(requests.filter(r => r.route.endsWith("/settings") && r.body).length, 0);
assert.equal(first.definition().defaultValue, true);
const controller = first.context.registerProjectOwnership({graph:first.graph});
await controller.select("film");
assert.equal(controller.owned, true);
assert.equal(first.timers.size, 1);
const oldProof = controller.proof();

const second = tab(true);
await second.setup();
const blocked = second.context.registerProjectOwnership({graph:second.graph});
await blocked.select("film");
assert.equal(blocked.owned, false);
await assert.rejects(second.context.projectMutationOptions({graph:second.graph}, "film"), /read-only/);

// A local permission refusal never reaches the branch mutation endpoint. It
// must not become an uncertain request that keeps Studio navigation blocked.
const denied = error => error.status === 423 && error.code === "h3_project_read_only"
    && error.requestNotSent === true && /read-only/.test(error.message);
await assert.rejects(second.context.queuedProjectOwnership({graph:second.graph}, "film"), denied);
const recovery = new Map();
const drafts = new BranchDrafts({getItem:key=>recovery.get(key) ?? null,
    setItem:(key,value)=>recovery.set(key,value), removeItem:key=>recovery.delete(key)}, "ownership-test");
let live = {plan_json:JSON.stringify({shots:[{id:"one",prompt:["original"]}]})};
let record = {id:"main",run_name:"film",revision:"1",authoring:structuredClone(live)};
let branchSaves = 0;
const branches = new StudioBranches({drafts, capture:()=>live, isCurrent:()=>true,
    changed(){}, flush:async()=>{}, apply(){}, request:async body=>{
        if(body.action === "list") return {default_branch:"main",branches:[record]};
        if(body.action === "load") return record;
        await second.context.projectMutationOptions({graph:second.graph}, "film");
        branchSaves++;
        return record = {...record,revision:"2",authoring:body.authoring};
    }});
await branches.refresh("film");
live = {plan_json:JSON.stringify({shots:[{id:"one",prompt:["unsaved edit"]}]})};
await branches.preserveDraft();
await assert.rejects(branches.save(), denied);
assert.equal(branchSaves, 0);
assert.equal(branches.pending, null);
assert.equal(await drafts.pending(), null, "no false pending request survives a reload");
assert.equal((await drafts.read("film", "main")).authoring.plan_json, live.plan_json,
    "the local prompt recovery draft is preserved");

// A stale ownership HTTP reply must not revive a proof after a settings event.
let release;
backend.hold = new Promise(resolve => { release = resolve; });
const pending = controller.request("status");
backend.enabled = false; backend.epoch++; backend.owner = "";
broadcast();
backend.hold = null; release(); await pending; await settle();
for (const client of [first, second]) assert.equal(client.value(), false);
assert.equal(controller.proof(), null);
assert.equal(first.timers.size, 0);
assert.equal(await first.context.queuedProjectOwnership({graph:first.graph}, "film"), "");
const options = {method:"POST", headers:{"Content-Type":"application/json"}};
assert.equal(await second.context.projectMutationOptions({graph:second.graph}, "film", options), options);
await branches.save();
assert.equal(branchSaves, 1, "normal save works after disabling ownership, without Retry pending");
assert.equal(record.authoring.plan_json, live.plan_json);
const third = tab(true);
await third.setup();
assert.equal(third.value(), false, "new tabs load the persisted unlocked mode");
const unlocked = third.context.registerProjectOwnership({graph:third.graph});
await unlocked.select("film");
assert.equal(unlocked.proof(), null);
assert.equal(third.timers.size, 0);

first.settings.setSettingValue("unused", true);
await settle();
assert.equal(backend.enabled, true);
assert.equal(second.value(), true);
assert.equal(controller.owned, true);
assert.ok(controller.epoch > oldProof.epoch);
assert.equal(blocked.owned, false, "re-enabling does not give every tab ownership");
assert.equal(first.timers.size, 1);
const ownedOptions = await first.context.projectMutationOptions({graph:first.graph}, "film", options);
assert.equal(ownedOptions.headers["X-H3-Ownership-Epoch"], String(controller.epoch));

// Rapid toggles are serialized, including returning to the confirmed value.
first.settings.setSettingValue("unused", false);
first.settings.setSettingValue("unused", true);
await settle();
assert.equal(backend.enabled, true);
assert.equal(first.value(), true);
backend.reject = true;
first.settings.setSettingValue("unused", false);
await settle();
assert.equal(backend.enabled, true);
assert.equal(first.value(), true, "failed save restores the confirmed checkbox");
assert.equal(first.errors.length, 1);
backend.reject = false;

// A late broadcast cannot undo a newer setting.
first.listeners.get("minimax_h3_project_ownership_settings")({detail:{enabled:false, epoch:1}});
assert.equal(first.value(), true);
assert.equal(controller.owned, true);
for (const c of [controller, blocked, unlocked]) c.dispose();
for (const client of tabs) assert.equal(client.timers.size, 0);

// All consumers must share one module instance, including the settings extension.
for (const file of fs.readdirSync(new URL("../web/", import.meta.url))) {
    if (!/\.(mjs|js)$/.test(file)) continue;
    for (const match of read(file).matchAll(/h3_project_ownership\.mjs\?v=([^"']+)/g)) {
        assert.equal(match[1], "0.7.6", file);
    }
}
assert.match(read("h3_project_asset_manager.js"), /locking_enabled === false/);
console.log("Ownership settings UI: startup, cross-tab toggle, stale replies, fresh claims and failure rollback pass");
