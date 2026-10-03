// Real IndexedDB/localStorage regression tests in an isolated Chrome profile.
// Usage: CHROME_PATH=/usr/bin/google-chrome-stable node tests/_branch_recovery_storage_browser_test.mjs
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile} from "node:fs/promises";
import {createServer} from "node:http";
import {tmpdir} from "node:os";
import {join} from "node:path";

async function browserTests() {
    const {BranchRecoveryStorage, RECOVERY_BYTE_LIMIT} = await import("/web/h3_branch_recovery_storage.mjs");
    const {BranchDrafts, StudioBranches, branchWidgetTransaction} = await import("/web/h3_working_branches.mjs");
    const results = [];
    const check = (condition, message) => { if (!condition) throw Error(message); };
    const rejects = async (promise, pattern) => {
        try { await promise; } catch (error) { check(pattern.test(error.message), error.message); return; }
        throw Error("Expected rejection");
    };
    const key = "h3-branch-draft-v1:test:run:main";
    const pendingKey = "h3-branch-pending-v1:test";
    const draft = seed => ({authoring:{plan_json:JSON.stringify({shots:[{id:"s1",seed,prompt:["unchanged prompt"]}]})}});
    const memory = entries => {
        const values = new Map(entries);
        return {get length() { return values.size; },key:i=>[...values.keys()][i],
            getItem:k=>values.get(k) ?? null, setItem:(k,v)=>values.set(k,v), removeItem:k=>values.delete(k)};
    };
    const store = (options = {}) => new BranchRecoveryStorage({indexedDB, name:crypto.randomUUID(), ...options});
    const close = async storage => (await storage.ready).close();
    check(RECOVERY_BYTE_LIMIT === 64 * 1024 * 1024, "recovery has a finite byte budget");

    // Issue #104: real structuredClone/DOM objects, reactive node properties,
    // and IndexedDB recovery while reloading a conflicting saved branch.
    const reloadStorage = store();
    const reloadDrafts = new BranchDrafts(reloadStorage, "reactive-reload");
    const localPlan = draft("18446744073709551615").authoring.plan_json;
    const savedPlan = draft("18446744073709551614").authoring.plan_json;
    const planWidget = {name:"plan_json", value:localPlan};
    const callback = () => {};
    const element = document.createElement("span");
    const node = {properties:new Proxy({cache:new Proxy({prompt:"local"}, {})}, {}), widgets:[
        planWidget, {name:"button", value:callback}, {name:"runtime", value:element},
        {options:{serialize:false}, get value() { throw Error("Do not snapshot a non-serialized widget"); }},
    ]};
    let failApply = true;
    const reload = new StudioBranches({drafts:reloadDrafts,
        capture:()=>({plan_json:planWidget.value}), flush:async()=>{}, changed(){},
        apply:async record => branchWidgetTransaction([node], () => {
            planWidget.value = record.authoring.plan_json;
            node.properties.cache.prompt = "saved";
            if (failApply) throw Error("fixture callback failure");
        }),
        request:async body => {
            if (body.action === "list") return {branches:[{id:"main",revision:"1"}],default_branch:"main"};
            check(body.action === "load", "reload must not write server authoring");
            return {id:"main",revision:"1",authoring:{plan_json:savedPlan}};
        },
    });
    await reload.refresh("run");
    check(Boolean(reload.conflict), "a different local Plan requires a reload decision");
    await reload.reloadSaved();
    check(reload.error === "fixture callback failure", "clone must succeed and expose the real callback failure");
    check(planWidget.value === localPlan && node.properties.cache.prompt === "local", "failed reload rolls back all edits");
    check((await reloadDrafts.read("run", "main")).authoring.plan_json === localPlan, "failed reload keeps the exact local draft");
    failApply = false;
    // Model the frontend re-wrapping restored node properties reactively.
    node.properties = new Proxy(node.properties, {});
    await reload.reloadSaved();
    check(!reload.error && !reload.conflict, "reactive branch reload succeeds");
    check(planWidget.value === savedPlan, "saved prompts and exact uint64 seed loaded");
    check(node.widgets[1].value === callback && node.widgets[2].value === element, "UI runtime identities unchanged");
    check((await reloadDrafts.read("run", "main")).authoring.plan_json === localPlan, "successful reload also retains the old draft");
    await close(reloadStorage);
    results.push("reactive branch reload with DOM/callback widgets: rollback, exact seeds and IndexedDB recovery");

    // A gap-only edit must survive closing/reopening the recovery database,
    // even though the saved Plan has not changed at all.
    const gapDatabase = crypto.randomUUID();
    const gapStorage = store({name:gapDatabase});
    const authoring = draft("18446744073709551615").authoring;
    const pendingGap = {editorial:{value:{placements:[{scene_id:"s1",start_frame:96}]},
        baseline:{placements:[]},stored:{placements:[]},ready:true}};
    const gapController = (storage, recovery) => new StudioBranches({
        drafts:new BranchDrafts(storage,"gap-reload"), capture:()=>structuredClone(authoring),
        captureRecovery:()=>structuredClone(recovery), apply:async()=>{}, flush:async()=>{}, changed(){},
        request:async body => {
            if (body.action === "list") return {branches:[{id:"main",revision:"1"}],default_branch:"main"};
            check(body.action === "load", "gap recovery never writes a server branch");
            return {id:"main",revision:"1",authoring:structuredClone(authoring)};
        },
    });
    const editing = gapController(gapStorage,pendingGap);
    await editing.refresh("run");
    await editing.observe();
    await close(gapStorage);
    const reopenedStorage = store({name:gapDatabase});
    const reopened = gapController(reopenedStorage,null);
    await reopened.refresh("run");
    check(reopened.draftRecovery?.recovery.editorial.value.placements[0].start_frame === 96,
        "reopened Studio recovers gap-only edits from IndexedDB");
    await close(reopenedStorage);
    results.push("gap-only recovery survives workflow/database reopen without prompt/settings edits or server writes");

    // Exhaust REAL localStorage as the old node could, without touching the
    // user's origin/profile. Preserve full history, exact uint64 seeds and
    // uncertain operations from every H3 client, not only the mounted node.
    const original = JSON.stringify({...draft("18446744073709551615"),older:[draft("18446744073709551614")],padding:"x".repeat(900000)});
    localStorage.setItem("Comfy.Workflow.DraftV2:sentinel", "do not delete");
    localStorage.setItem("h3-branch-client-v1:workflow:1930", "test");
    localStorage.setItem(key, original);
    localStorage.setItem(pendingKey, JSON.stringify({operation_id:"same-replay-id",authoring:draft("8").authoring}));
    let inserted = 0;
    try { for (; inserted < 100; inserted++) localStorage.setItem(`h3-branch-draft-v1:other:${inserted}:main`, original); }
    catch (error) { check(error.name === "QuotaExceededError", "real localStorage quota reached"); }
    check(inserted > 0 && inserted < 100, "fixture filled localStorage");
    const before = Object.fromEntries(Object.entries(localStorage));
    const migrated = store({legacyStorage:localStorage});
    await migrated.ready;
    for (const [k,v] of Object.entries(before)) {
        if (/^h3-branch-(draft|pending)-v1:/.test(k)) {
            check(await migrated.getItem(k) === v, "migration preserves exact bytes");
            check(localStorage.getItem(k) === null, "verified legacy entry released");
        } else check(localStorage.getItem(k) === v, "unrelated storage unchanged");
    }
    localStorage.setItem("Comfy.Workflow.DraftV2:now-fits", "w".repeat(900000));
    const localSize = JSON.stringify(localStorage).length;
    const drafts = new BranchDrafts(migrated, "test");
    await drafts.save("run", "main", draft("18446744073709551613"));
    const recovered = await drafts.read("run", "main");
    check(recovered.older[0].authoring.plan_json === draft("18446744073709551614").authoring.plan_json, "older history remains accessible");
    check((await drafts.pending()).operation_id === "same-replay-id", "pending replay survives migration");
    await drafts.pending(null);
    check(JSON.stringify(localStorage).length === localSize, "new drafts/pending requests never grow localStorage");
    await close(migrated);
    results.push("real full localStorage freed; H3 histories/pending operations preserved; workflow-sized draft can be written again");

    const bounded = store({limit:1000});
    await bounded.setItem(key, "a".repeat(200));
    await rejects(bounded.setItem(key, "b".repeat(600)), /size limit/);
    check(await bounded.getItem(key) === "a".repeat(200), "budget rejection preserves previous data");
    await rejects(bounded.setItem(`${key}:other`, "b".repeat(600)), /size limit/);
    await bounded.removeItem(key);
    await bounded.setItem(key, "c".repeat(400));
    check(await bounded.getItem(key) === "c".repeat(400), "deletion releases accounted capacity");
    await close(bounded);
    results.push("atomic global byte cap; no eviction or partial writes; deletion accounting");

    const big = "b".repeat(1000), legacy = memory([[key,big]]);
    const oversize = store({limit:500,legacyStorage:legacy});
    await oversize.ready;
    check(await oversize.getItem(key) === big && legacy.length === 0, "oversized legacy history preserved");
    await rejects(oversize.setItem(key,big+"x"), /size limit/);
    await oversize.setItem(key,"small");
    await close(oversize);
    results.push("oversized legacy data migrates intact but cannot grow further");

    // Request success is not transaction success. Abort after put succeeds.
    class AbortMigration extends BranchRecoveryStorage {
        async open(...args) {
            const db = await super.open(...args), transact = db.transaction.bind(db);
            db.transaction = (...args) => {
                const tx = transact(...args);
                if (args[1] === "readwrite") {
                    const objectStore = tx.objectStore.bind(tx);
                    tx.objectStore = name => {
                        const s = objectStore(name), put = s.put.bind(s);
                        s.put = (...args) => {
                            const request = put(...args);
                            request.addEventListener("success", () => { try { tx.abort(); } catch {} });
                            return request;
                        };
                        return s;
                    };
                }
                return tx;
            };
            return db;
        }
    }
    const interruptedLegacy = memory([[key, original]]), interruptedName = crypto.randomUUID();
    const interrupted = new AbortMigration({indexedDB,name:interruptedName,legacyStorage:interruptedLegacy});
    await rejects(interrupted.ready, /abort/i);
    check(interruptedLegacy.getItem(key) === original, "aborted commit cannot remove legacy data");
    const retry = store({name:interruptedName,legacyStorage:interruptedLegacy});
    await retry.ready;
    check(await retry.getItem(key) === original && interruptedLegacy.length === 0, "retry after interruption is safe");
    await close(retry);
    results.push("abort after request success keeps source; reload safely retries migration");

    const unavailableLegacy = memory([[key,original]]);
    const unavailable = store({indexedDB:null,legacyStorage:unavailableLegacy});
    await rejects(unavailable.ready, /unavailable/);
    check(unavailableLegacy.getItem(key) === original, "unavailable IndexedDB never deletes old drafts");
    results.push("unavailable storage preserves legacy data without localStorage fallback");

    const name = crypto.randomUUID(), first = store({name});
    await first.setItem(key,"new database draft");
    const conflictingLegacy = memory([[key,"different old-tab draft"]]);
    const conflict = store({name,legacyStorage:conflictingLegacy});
    await conflict.ready;
    check(await conflict.getItem(key) === "new database draft", "existing IndexedDB draft not overwritten");
    check(conflictingLegacy.getItem(key) === "different old-tab draft", "conflicting legacy draft not removed");
    check(/older.*tab/.test(conflict.warning), "conflict warning visible");
    await close(first); await close(conflict);
    const changedLegacy = memory([[key,original]]), get = changedLegacy.getItem;
    let reads = 0;
    changedLegacy.getItem = k => {
        if (++reads === 2) changedLegacy.setItem(k,"new old-tab edit");
        return get(k);
    };
    const changed = store({legacyStorage:changedLegacy});
    await changed.ready;
    check(await changed.getItem(key) === original && get(key) === "new old-tab edit", "concurrent legacy edit preserves both versions");
    await close(changed);
    results.push("old-tab conflicts and edits during migration cannot overwrite or delete either copy");

    const sharedName = crypto.randomUUID(), a = store({name:sharedName}), b = store({name:sharedName});
    const da = new BranchDrafts(a,"shared"), db = new BranchDrafts(b,"shared");
    await Promise.all(Array.from({length:30}, (_,i) => (i%2 ? da : db).stash("run","main",draft(String(i)))));
    const history = await da.read("run","main");
    check([history,...history.older].length === 30, "concurrent read/modify/write retains all versions");
    await close(a); await close(b);
    results.push("concurrent IndexedDB connections retain all 30 distinct recovery versions");
    return results;
}

const profile = await mkdtemp(join(tmpdir(), "h3-recovery-browser-test-"));
const root = new URL("../", import.meta.url);
const server = createServer(async (req,res) => {
    try {
        const path = new URL(req.url,"http://localhost").pathname;
        if (path === "/") { res.end("<!doctype html><title>H3 isolated recovery test</title>"); return; }
        if (!/^\/web\/[\w.-]+\.mjs$/.test(path)) { res.writeHead(404); res.end(); return; }
        res.setHeader("Content-Type","text/javascript");
        res.end(await readFile(new URL(path.slice(1),root)));
    } catch { res.writeHead(500); res.end(); }
});
await new Promise((resolve,reject)=>{server.once("error",reject);server.listen(0,"127.0.0.1",resolve);});
const chrome = spawn(process.env.CHROME_PATH || "/usr/bin/google-chrome-stable",[
    "--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check",
    "--remote-debugging-port=0",`--user-data-dir=${profile}`,"about:blank",
],{stdio:["ignore","ignore","pipe"]});
let socket;
const timeout = setTimeout(()=>{chrome.kill("SIGTERM");server.close();},45000);
try {
    const address = await new Promise((resolve,reject)=>{
        chrome.once("error",reject);
        chrome.once("exit",code=>reject(Error(`Test browser exited: ${code}`)));
        chrome.stderr.on("data",data=>{
            const found = String(data).match(/DevTools listening on (ws:\/\/[^\s]+)/);
            if (found) resolve(found[1]);
        });
    });
    socket = new WebSocket(address);
    await new Promise((resolve,reject)=>{socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
    let serial=0;
    const pending = new Map();
    socket.addEventListener("message",event=>{
        const message=JSON.parse(event.data), callback=pending.get(message.id);
        if(callback) {pending.delete(message.id);message.error?callback.reject(Error(JSON.stringify(message.error))):callback.resolve(message.result);}
    });
    const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{
        const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,sessionId}));
    });
    const url=`http://127.0.0.1:${server.address().port}/`;
    const {targetId}=await call("Target.createTarget",{url:"about:blank"});
    const {sessionId}=await call("Target.attachToTarget",{targetId,flatten:true});
    await call("Page.enable",{},sessionId);
    await call("Page.navigate",{url},sessionId);
    // Evaluate after the new document has a same-origin execution context.
    for(let i=0;i<100;i++) {
        const r=await call("Runtime.evaluate",{expression:"location.href",returnByValue:true},sessionId);
        if(r.result?.value===url) break;
        await new Promise(resolve=>setTimeout(resolve,20));
    }
    const result=await call("Runtime.evaluate",{expression:`(${browserTests.toString()})()`,awaitPromise:true,returnByValue:true},sessionId);
    assert.equal(result.exceptionDetails,undefined,JSON.stringify(result.exceptionDetails));
    assert.ok(Array.isArray(result.result.value));
    for(const passed of result.result.value) console.log(`PASS: ${passed}`);
    console.log(`Isolated test profile: ${profile}`);
} finally {
    clearTimeout(timeout);socket?.close();chrome.kill("SIGTERM");server.close();
}
