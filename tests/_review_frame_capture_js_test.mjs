import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {projectAssetEditAttempts} from "../web/h3_project_asset_sync_core.mjs";
import {
    canCaptureFrame, captureCarousels, captureTargetProject, carouselProject,
} from "../web/h3_review_capture_core.mjs";

function graph(nodes) {
    const result = {_nodes: nodes, links: {}, getNodeById(id) {
        return nodes.find((node) => node.id === id);
    }};
    for (const [i, node] of nodes.entries()) Object.assign(node, {id: i + 1, graph: result});
    return result;
}
function connect(parent, child) {
    const id = Object.keys(child.graph.links).length;
    child.graph.links[id] = {origin_id: parent.id};
    (child.inputs ??= []).push({link: id});
}
function carousel(project) {
    return {type: "MiniMaxH3ProjectAssetManager", project, refreshed: 0,
        _h3ProjectAssetCurrentProject() { return this.project; },
        async _h3ProjectAssetRefresh() { this.refreshed++; }};
}
const reviewA = {}, reviewB = {}, a = carousel("A"), b = carousel("B");
const canvas = graph([b, reviewA, a, reviewB]); // Wrong project is first on canvas.
connect(a, reviewA); connect(b, reviewB);
assert.equal(captureTargetProject(reviewA), "A");
assert.equal(captureTargetProject(reviewB), "B");
a.project = "Renamed";
assert.equal(captureTargetProject(reviewA), "Renamed");
connect(b, reviewA);
assert.equal(captureTargetProject(reviewA), "", "ambiguous upstream projects require a choice");
reviewA.inputs = [];
assert.equal(captureTargetProject(reviewA), "", "never select an unrelated Carousel");
const plan = {type: "MiniMaxH3ChainPlanModern", widgets: [{name: "run_name", value: "Plan"}]};
const c = carousel("");
graph([plan, reviewA, c]); connect(plan, reviewA);
assert.equal(captureTargetProject(reviewA), "Plan");
connect(c, plan);
assert.equal(captureTargetProject(reviewA), "", "unresolved Carousel must not fall back to another run");
connect(reviewA, plan); // Malformed cyclic graph must not loop forever.
assert.equal(captureTargetProject(reviewA), "");
assert.equal(carouselProject({widgets: [{name: "run_name", value: "Restored"}]}), "Restored");
const nested = graph([carousel("Nested")]);
const outer = graph([{subgraph: nested}]);
nested._nodes.push({subgraph: outer});
assert.equal(captureCarousels(outer).length, 1);

// Minimal DOM event harness executes the actual production dialog, not a
// second implementation. No ComfyUI server or live project is contacted.
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.events = {}; this.value = ""; }
    append(...children) {
        for (const child of children) {
            this.children.push(child);
            if (typeof child === "object") child.parent = this;
        }
    }
    replaceChildren(...children) {
        for (const child of this.children) if (typeof child === "object") child.parent = null;
        this.children = []; this.append(...children);
    }
    remove() {
        if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this);
        this.parent = null;
    }
    get isConnected() { return this.connected || Boolean(this.parent?.isConnected); }
    contains(other) { return other === this || this.children.some((child) => child.contains?.(other)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0]; }
    querySelectorAll(selector) {
        return this.children.flatMap((child) => typeof child === "object" ? [
            ...(child.className?.split(" ").includes(selector.slice(1)) ? [child] : []),
            ...child.querySelectorAll(selector),
        ] : []);
    }
    addEventListener(name, callback) { (this.events[name] ??= []).push(callback); }
    async fire(name, detail = {}) {
        for (const fn of this.events[name] ?? []) await fn({target: this, preventDefault() {}, ...detail});
    }
    focus() {}
    getContext() { return {drawImage() {}}; }
    toDataURL() { return "data:image/png;base64,"; }
}
const source = readFileSync(new URL("../web/h3_chain_review_final.js", import.meta.url), "utf8");
const dialogCode = source.slice(source.indexOf('    const captureRow = document.createElement("div");'),
    source.indexOf('    const prefix = document.createElement("pre");'));
assert.ok(dialogCode.length > 4000);
const mount = new Function("node", "root", "video", "api", "document", "canCaptureFrame",
    "captureCarousels", "captureTargetProject", "carouselProject", "projectMutationOptions", "projectAssetEditAttempts",
    dialogCode + "\nroot.append(captureRow); return {openCaptureDialog, captureButton, captureStatus};");
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const reply = (body, ok = true) => ({ok, status: ok ? 200 : 400, json: async () => body});
function setup(authorize = async (_node, _project, options) => ({
    ...options, headers: {...options.headers, "X-H3-Workflow-Owner": "test-owner"},
})) {
    const a = carousel("A"), b = carousel("B"), mirror = carousel("A"), node = {};
    const canvas = graph([b, node, a, mirror]); connect(a, node);
    const root = new Element("root"); root.connected = true;
    const requests = [];
    const api = {fetchApi(url, options) {
        return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
    }};
    const video = {h3CaptureItem: {filename: "scene.mp4", subfolder: "run", type: "output"},
        readyState: 2, videoWidth: 64, videoHeight: 48, currentTime: 0.5, pause() {}};
    const ui = mount(node, root, video, api, {createElement: (tag) => new Element(tag)},
        canCaptureFrame, captureCarousels, captureTargetProject, carouselProject, authorize, projectAssetEditAttempts);
    const find = (name) => root.querySelector(`.h3r-capture-${name}`);
    const project = () => root.querySelectorAll(".h3r-capture-tag")[0];
    const tag = () => root.querySelectorAll(".h3r-capture-tag")[1];
    const save = () => find("actions").children[1];
    return {root, video, ui, node, canvas, a, b, mirror, requests, find, project, tag, save};
}

{
    const t = setup();
    t.video.seeking = true;
    await t.ui.openCaptureDialog();
    assert.equal(t.requests.length, 0);
    assert.match(t.ui.captureStatus.textContent, /loading or seeking/);
    t.video.seeking = false; t.video.readyState = 0;
    assert.equal(canCaptureFrame(t.video), false);
    t.video.readyState = 2; t.video.currentTime = NaN;
    assert.equal(canCaptureFrame(t.video), false);
}
{
    const t = setup(); await t.ui.openCaptureDialog();
    assert.equal(t.project().value, "A");
    assert.match(t.requests[0].url, /create=false/);
    t.project().value = "B"; await t.project().fire("input");
    t.requests[1].resolve(reply({assets: [{tag: "B_only"}]})); await flush();
    t.requests[0].resolve(reply({assets: [{tag: "A_stale"}]})); await flush();
    await t.find("tag-picker").fire("click"); // Keyboard click also works.
    assert.deepEqual(t.find("tag-menu").children.map((child) => child.textContent), ["B_only"]);
    await t.find("tag-option").fire("click");
    assert.equal(t.tag().value, "B_only");
    t.project().value = ""; await t.project().fire("input");
    assert.equal(t.requests.length, 2, "blank project does not trigger a GET");
    t.project().value = "A"; await t.project().fire("input");
    t.requests[2].resolve(reply({error: "Permission denied"}, false)); await flush();
    assert.match(t.find("tag-empty").textContent, /Could not load tags: Permission denied/);
}
{
    const t = setup(); await t.ui.openCaptureDialog();
    t.tag().value = "hero";
    t.requests[0].resolve(reply({assets: []})); await flush();
    const saving = t.save().fire("click"); await flush();
    assert.equal(t.project().disabled, true);
    assert.equal(t.tag().disabled, true);
    assert.equal(t.ui.captureButton.disabled, true);
    assert.equal(t.requests[1].options.headers["X-H3-Workflow-Owner"], "test-owner");
    const {storage_operation_id, ...requestBody} = JSON.parse(t.requests[1].options.body);
    assert.match(storage_operation_id,/^[a-f0-9]{32}$/);
    assert.deepEqual(requestBody, {
        project: "A", filename: "scene.mp4", subfolder: "run", type: "output", time_seconds: 0.5, tag: "hero",
    });
    await t.save().fire("click");
    await t.find("dialog").fire("click");
    await t.ui.openCaptureDialog();
    assert.equal(t.requests.length, 2, "double click cannot create a duplicate save");
    assert.ok(t.find("dialog").isConnected, "cannot dismiss an in-flight save");
    t.requests[1].resolve(reply({asset: {tag: "hero1"}, catalog: {project: "A"}}));
    await saving;
    assert.equal(t.a.refreshed, 1);
    assert.equal(t.mirror.refreshed, 1, "all matching Carousels refresh");
    assert.equal(t.b.refreshed, 0);
    assert.equal(t.find("dialog"), undefined);
    assert.match(t.ui.captureStatus.textContent, /Saved @hero1 to the A/);
}
{
    const t = setup(); await t.ui.openCaptureDialog();
    t.requests[0].resolve(reply({assets: []})); await flush();
    const saving = t.save().fire("click"); await flush();
    t.a.project = "B";
    t.mirror._h3ProjectAssetRefresh = async () => { throw new Error("Refresh failed"); };
    t.requests[1].resolve(reply({asset: {tag: "hero"}, catalog: {project: "A"}}));
    await saving;
    assert.equal(t.a.refreshed, 0, "never refresh a Carousel that changed projects during save");
    assert.match(t.ui.captureStatus.textContent, /Saved.*Refresh the Carousel/);
    assert.equal(t.find("dialog"), undefined, "refresh failure is not a failed save");
}
{
    const t = setup(); await t.ui.openCaptureDialog();
    t.requests[0].resolve(reply({assets: []})); await flush();
    const saving = t.save().fire("click"); await flush();
    t.requests[1].resolve(reply({error: "Video source is missing"}, false)); await saving;
    assert.equal(t.project().disabled, false);
    assert.match(t.find("error").textContent, /Video source is missing/);
    assert.equal(t.a.refreshed, 0);
    const firstId = JSON.parse(t.requests[1].options.body).storage_operation_id;
    const retry = t.save().fire("click"); await flush();
    assert.equal(JSON.parse(t.requests[2].options.body).storage_operation_id,firstId);
    t.requests[2].resolve(reply({error:"Still unavailable"},false)); await retry;
    t.tag().value = "changed_tag";
    const changed = t.save().fire("click"); await flush();
    assert.notEqual(JSON.parse(t.requests[3].options.body).storage_operation_id,firstId);
    t.requests[3].resolve(reply({error:"Still unavailable"},false)); await changed;
    t.node.graph = null;
    await t.save().fire("click");
    assert.equal(t.requests.length, 4);
    assert.match(t.find("error").textContent, /changed workflows/);
}
{
    const t = setup(async () => { throw new Error("Project is read-only here"); });
    await t.ui.openCaptureDialog();
    t.requests[0].resolve(reply({assets: []})); await flush();
    await t.save().fire("click");
    assert.equal(t.requests.length, 1, "no capture POST without ownership permission");
    assert.match(t.find("error").textContent, /read-only/);
    assert.equal(t.save().disabled, false);
}
{
    let authorize;
    const t = setup(() => new Promise((resolve) => { authorize = resolve; }));
    await t.ui.openCaptureDialog();
    t.requests[0].resolve(reply({assets: []})); await flush();
    const saving = t.save().fire("click"); await flush();
    t.node.graph = null;
    authorize({}); await saving;
    assert.equal(t.requests.length, 1, "moving workflows during ownership lookup cancels the POST");
    assert.match(t.find("error").textContent, /changed workflows/);
}
assert.match(source, /seedRow, candidateRow, actions, status, deferred, resume/);
console.log("Review frame capture: routing, dialog events, races, ownership and deferred review pass");
