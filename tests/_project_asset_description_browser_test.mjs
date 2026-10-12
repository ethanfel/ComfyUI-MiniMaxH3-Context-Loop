// Real Carousel, mocked server and a delayed mocked provider; no ComfyUI
// server and no real provider calls. Regression for a generated description
// arriving while the user is typing: the review panel must not reset the
// live draft, and later autosaves must keep every typed sentence.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-description-race-"));
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(`<!doctype html><style>body{background:#222;color:#ddd}.host{width:1100px;height:1000px}</style>
            <script type="module">(${browserChecks.toString()})();</script>`);
        return;
    }
    res.setHeader("Content-Type", "text/javascript; charset=utf-8");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        try { res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return; }
        catch { /* Missing imports fail the browser check. */ }
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome;
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
        "--headless", "--window-size=1600,1000", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=30000",
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser test timed out")); }, 60000);
        chrome.once("error", error => { clearTimeout(timer); reject(error); });
        chrome.once("exit", code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(code, 0, stderr);
    const encoded = stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-1500) + stderr.slice(-500));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    assert.deepEqual(report.failures, []);
} finally { chrome?.kill(); server.close(); fs.rmSync(temporary, {recursive:true, force:true}); }

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const until = async (predicate, message, limit = 200) => {
        for (let i = 0; i < limit && !predicate(); i++) await wait(10);
        check(predicate(), message);
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    const projectCatalog = (project, description) => ({project, revision:"test", folders:[], assets:[
        {id:"cabinet", tag:"cabinet", kind:"image", role:"picture", enabled:true,
            tag_type:"object", description, input_path:`h3_projects/${project}/cabinet.png`},
    ]});
    // "A" and "B" are duplicated projects: same asset ID, different text.
    const catalogs = {
        synthetic: projectCatalog("synthetic", "Original."),
        A: projectCatalog("A", "A saved."),
        B: projectCatalog("B", "B saved."),
    };
    const catalog = catalogs.synthetic;
    const saves = [];
    const updates = [];
    let holdUpdates = false;
    const heldUpdates = [];
    const describeCalls = [];
    let releaseDescribe;
    const graph = {_nodes:[], links:{}, setDirtyCanvas() {}, getNodeById(id) { return this._nodes.find(n => n.id === id); }};
    const settings = {
        "MiniMaxH3ContexLoop.PromptOptimizer.Backend": "direct",
        "MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat": "openai",
        "MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl": "https://api.openai.com/v1",
        "MiniMaxH3ContexLoop.PromptOptimizer.Model": "mock-model",
        "MiniMaxH3ContexLoop.PromptOptimizer.AllowMedia": true,
    };
    window.app = {graph, ui:{settings:{getSettingValue: id => settings[id]}},
        registerExtension(extension) { window.extension = extension; }};
    const json = (value, status = 200) => ({ok:status < 400, status, json:async () => structuredClone(value)});
    window.api = {apiURL: value => value, addEventListener() {}, removeEventListener() {}, async fetchApi(route, options = {}) {
        if (route.includes("/project-ownership")) return json({locking_enabled:false});
        if (route.endsWith("/project-assets/describe")) {
            describeCalls.push(JSON.parse(options.body));
            // The mocked provider answers only when the test releases it.
            await new Promise(resolve => { releaseDescribe = resolve; });
            return json({description:"is a generated cabinet."});
        }
        if (route.endsWith("/project-assets/update")) {
            const body = JSON.parse(options.body);
            updates.push({project:body.project, description:body.changes.description});
            // The server applies the write now; only its response is held.
            if (holdUpdates) await new Promise(resolve => heldUpdates.push(resolve));
            const target = catalogs[body.project];
            const asset = target.assets.find(item => item.id === body.asset_id);
            if ("expected_description" in body.changes
                    && body.changes.expected_description.trim() !== String(asset.description ?? "").trim()) {
                return json({error:"changed", code:"h3_project_asset_conflict"}, 409);
            }
            if ("description" in body.changes) asset.description = body.changes.description;
            if (body.project === "synthetic") saves.push(body.changes.description);
            return json({catalog:target, asset});
        }
        if (route.endsWith("/projects")) return json({projects:[]});
        const requested = new URLSearchParams(route.split("?")[1] ?? "").get("project");
        return json(catalogs[requested] ?? catalog);
    }};
    let node;
    let switchNode;
    try {
        await import("/web/h3_project_asset_manager.js");
        class Node {
            constructor(run = "synthetic") {
                this.type = this.comfyClass = "MiniMaxH3ProjectAssetManager"; this.id = graph._nodes.length + 1;
                this.graph = graph; this.inputs = []; this.outputs = []; this.size = [1100, 700];
                this.properties = {};
                this.widgets = [{name:"run_name", value:run},
                    {name:"catalog_json", value:JSON.stringify(catalogs[run])}, {name:"operation_json", value:""}];
                graph._nodes.push(this);
            }
            setSize(size) { this.size = size; }
            addDOMWidget(name, kind, root) {
                this.root = root; this.host = document.createElement("div"); this.host.className = "host";
                this.host.append(root); document.body.append(this.host);
                return this.dom = {element:root};
            }
        }
        await window.extension.beforeRegisterNodeDef(Node, {name:"MiniMaxH3ProjectAssetManager"});
        node = new Node(); node.onNodeCreated();
        await until(() => node.root?.querySelector(".h3pa-card"), "Carousel mounts");
        node.root.querySelector(".h3pa-card").click();
        const field = () => node.root.querySelector("textarea.h3pa-description");
        await until(() => field(), "Selecting the asset shows its description");
        check(field().value === "Original.", "The saved description is shown");

        // 1. Generate from the saved text.
        const generate = [...node.root.querySelectorAll("button")]
            .find(item => item.textContent === "Generate description");
        check(Boolean(generate), "Generate description is available");
        generate.click();
        await until(() => describeCalls.length === 1 && releaseDescribe, "Generate sends one describe request");
        check(describeCalls[0].allow_media === true, "Media access was explicitly enabled for this request");

        // 2. Type while it runs, without waiting for autosave.
        const first = "Original. Manual first sentence.";
        field().focus();
        field().value = first;
        field().setSelectionRange(first.length, first.length);
        field().dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText"}));

        // 3. The provider answers before the 1.2-second autosave fires.
        releaseDescribe();
        await until(() => node.root.querySelector(".h3pa-description-review"),
            "A late result is held for review");
        check(saves.length === 0, "Nothing is saved before the autosave delay");
        check(field().value === first, "Showing the review keeps the live draft in the field");
        check(document.activeElement === field(), "Showing the review keeps focus in the field");
        check(field().selectionStart === first.length, "Showing the review keeps the caret position");
        check(node.root.querySelector(".h3pa-description-review").textContent.includes("is a generated cabinet."),
            "The review panel shows the generated text");

        // 4. Autosave keeps the first sentence and the field still shows it.
        await until(() => saves.length === 1, "Autosave fires after the delay", 300);
        check(saves[0] === first, "Autosave saves the first sentence");
        check(field().value === first, "The field still shows the saved draft");

        // 5. Continue typing; the next save keeps both sentences.
        const second = `${first} Manual second sentence.`;
        field().value = second;
        field().dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText"}));
        await until(() => saves.length === 2, "The second edit autosaves", 300);
        check(saves[1] === second, "The next save keeps the first sentence");
        check(catalog.assets[0].description === second, "The stored description has both sentences");
        check(!saves.includes("is a generated cabinet."), "The generated text was never saved");

        // 6. Discarding the generated text leaves the typed description.
        [...node.root.querySelectorAll(".h3pa-description-review button")]
            .find(item => item.textContent === "Discard").click();
        await until(() => !node.root.querySelector(".h3pa-description-review"), "Discard removes the review");
        check(field().value === second, "Discard keeps the typed description");
        await wait(1500);
        check(saves.length === 2, "No further saves after discard");
        node.onRemoved?.(); node.host?.remove(); node = null;

        // --- Duplicated projects: a delayed save must not leak across a Run
        // switch (review regression). A and B share the asset ID.
        switchNode = new Node("A");
        switchNode.onNodeCreated();
        const root = () => switchNode.root;
        const cardClick = async (label) => {
            await until(() => root()?.querySelector(".h3pa-card"), `${label}: Carousel shows the asset`);
            root().querySelector(".h3pa-card").click();
            await until(() => root().querySelector("textarea.h3pa-description"), `${label}: description editor opens`);
        };
        const switchTo = async (run) => {
            const input = root().querySelector("input.h3pa-project");
            input.value = run;
            input.dispatchEvent(new Event("change", {bubbles:true}));
            await until(() => switchNode._h3ProjectAssetCurrentProject() === run
                && JSON.parse(switchNode.widgets.find(w => w.name === "catalog_json").value).project === run,
            `Switching to ${run} loads its catalog`, 400);
        };
        const text = () => root().querySelector("textarea.h3pa-description").value;
        const typeInto = (value) => {
            const area = root().querySelector("textarea.h3pa-description");
            area.focus(); area.value = value;
            area.dispatchEvent(new InputEvent("input", {bubbles:true, inputType:"insertText"}));
            return area;
        };
        await cardClick("A");
        check(text() === "A saved.", "A shows its own saved description");

        // 1. Edit in A and start saving (blur); hold the save response.
        holdUpdates = true;
        const editedA = "A saved. A draft.";
        typeInto(editedA).dispatchEvent(new Event("change", {bubbles:true}));
        await until(() => heldUpdates.length === 1, "A's save request is in flight");
        check(updates.at(-1).project === "A" && updates.at(-1).description === editedA, "The held save belongs to A");
        holdUpdates = false;

        // 2. Switch to B before A's response returns, then release it.
        await switchTo("B");
        heldUpdates.shift()();
        await wait(50);

        // 3. B shows B's own saved description, not A's draft.
        await cardClick("B");
        check(text() === "B saved.", "B shows its own description, not A's draft");

        // 4. Editing in B saves only B's text into B.
        const editedB = "B saved. B edit.";
        typeInto(editedB);
        await until(() => updates.some(item => item.project === "B"), "B's edit autosaves", 300);
        const bSaves = updates.filter(item => item.project === "B");
        check(bSaves.length === 1 && bSaves[0].description === editedB, "B's autosave contains only B's text");
        check(catalogs.B.assets[0].description === editedB, "B's stored description is B's edit");
        check(catalogs.A.assets[0].description === editedA, "A's released save stayed in A");

        // 5. An unsaved draft in B whose timer fires after switching back to
        // A stays with B: it never writes into A and is restored in B.
        const unsavedB = "B saved. B edit. Unsaved tail.";
        typeInto(unsavedB);
        await switchTo("A");
        await wait(1500);
        check(!updates.some(item => item.project === "A" && item.description === unsavedB),
            "B's pending draft never writes into A");
        check(updates.filter(item => item.project === "B").length === 1,
            "B's pending draft does not save while B is closed");
        await cardClick("A again");
        check(text() === editedA, "A shows A's saved text after returning");
        await switchTo("B");
        await cardClick("B again");
        check(text() === unsavedB, "Returning to B restores its unsaved draft");
        await until(() => catalogs.B.assets[0].description === unsavedB,
            "B's preserved draft saves once B is open again", 300);
        check(updates.filter(item => item.project === "A").length === 1,
            "A received only its own single save throughout");
    } catch (error) { report.failures.push(error.stack || String(error)); }
    finally {
        for (const item of [node, switchNode]) { item?.onRemoved?.(); item?.host?.remove(); }
    }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
