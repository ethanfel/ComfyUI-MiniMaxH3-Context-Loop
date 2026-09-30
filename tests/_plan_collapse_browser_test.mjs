// Real base Plan editors with synthetic data. No live ComfyUI or user profile.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-plan-collapse-"));
const html = `<!doctype html><meta charset="utf-8"><style>
body{margin:12px;background:#191b20;display:flex;gap:16px;color:#ddd}
.host{width:700px;height:650px;flex:none}
</style><script type="module">(${browserChecks.toString()})();</script>`;
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(html); return;
    }
    res.setHeader("Content-Type", "text/javascript; charset=utf-8");
    if (["/scripts/app.js", "/scripts/api.js"].includes(url.pathname)) {
        const name = path.basename(url.pathname, ".js");
        res.end(`export const ${name} = window.${name};`); return;
    }
    if (/^\/web\/[\w.-]+\.(mjs|js)$/.test(url.pathname)) {
        try { res.end(fs.readFileSync(new URL(".." + url.pathname, import.meta.url))); return; }
        catch { /* Report a missing module through the browser, not ComfyUI. */ }
    }
    res.writeHead(404); res.end();
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let chrome;
try {
    chrome = spawn(process.env.H3_TEST_BROWSER || "/usr/bin/google-chrome-stable", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=7000",
        "--window-size=1900,1000", "--screenshot=" + path.join(temporary, "plan-collapse.png"),
        "--dump-dom", `http://127.0.0.1:${server.address().port}/`,
    ], {stdio:["ignore", "pipe", "pipe"]});
    let stdout = "", stderr = "";
    chrome.stdout.on("data", chunk => stdout += chunk);
    chrome.stderr.on("data", chunk => stderr += chunk);
    const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { chrome.kill(); reject(Error("Browser test timed out")); }, 25000);
        chrome.once("error", error => { clearTimeout(timer); reject(error); });
        chrome.once("exit", code => { clearTimeout(timer); resolve(code); });
    });
    assert.equal(code, 0, stderr);
    const encoded = stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-2000) + stderr.slice(-1000));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    console.log("Isolated screenshot: " + path.join(temporary, "plan-collapse.png"));
    assert.deepEqual(report.failures, []);
} finally {
    chrome?.kill();
    server.close();
}

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const waitFor = async test => {
        for (let i = 0; i < 100; i++) { if (test()) return; await wait(10); }
        throw Error("Editor did not mount");
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.requestAnimationFrame = callback => setTimeout(() => callback(performance.now()), 16);
    window.cancelAnimationFrame = clearTimeout;
    window.confirm = () => true;
    const graph = {_nodes:[], setDirtyCanvas() {}, getNodeById() { return null; }};
    let requests = 0;
    window.app = {graph, configuringGraph:false, registerExtension(value) { window.extension = value; }};
    window.api = {fetchApi() { requests++; throw Error("Unexpected API request"); }};
    try {
        await import("/web/h3_chain_plan_editor.js");
        const {MODERN_PLAN_WIDGET_NAMES} = await import("/web/h3_plan_upgrade_core.mjs");
        for (const type of ["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"]) {
            const modern = type === "MiniMaxH3ChainPlanModern";
            class Node {
                constructor(saved) {
                    this.id = graph._nodes.length + 1; this.type = this.comfyClass = type;
                    this.graph = graph; this.inputs = []; this.size = [920, 860];
                    this.properties = saved?.properties ?? {execution_marker:"keep", h3_chain_plan_layout:{
                        promptHeights:{"scene:one":224, shared:144}, advanced:true, settingsOpen:false}};
                    this.widgets = Object.entries({
                        plan_json:saved?.plan ?? JSON.stringify({prompt_prefix:["Shared identity.", "", "Keep @reference."], shots:[
                            {id:"one", prompt:["First scene.", "Keep this text."], length:22, seed:"18446744073709551615"},
                            {id:"two", prompt:["Second scene."], length:22, seed:"42"},
                            {id:"three", prompt:["Third scene."], length:22, seed:"43"},
                        ]}), run_name:"collapse_fixture", generation_fingerprint:"", width:640, height:384,
                        context_length:5, default_duration_seconds:5, default_steps:8, base_seed:1,
                        encode_mode:"video", crop:"disabled", segment_crf:18, video_blend_frames:0,
                        anchor_mode:"head", continuation_mode:"guide",
                    }).filter(([name]) => !modern || MODERN_PLAN_WIDGET_NAMES.includes(name))
                        .map(([name, value]) => ({name, value, type:typeof value === "number" ? "number" : "text", options:{}}));
                    // Match current ComfyUI: ordinary widgets already own an
                    // unlinked socket, before any user conversion/connection.
                    this.inputs = this.widgets.map(widget => ({name:widget.name, widget:{name:widget.name}, link:null}));
                    const json = this.widgets.find(widget => widget.name === "plan_json");
                    json.type = "customtext";
                    json.computeLayoutSize = () => ({minHeight:200});
                    graph._nodes.push(this);
                }
                setSize(value) { this.size = value; }
                addDOMWidget(name, type, root) {
                    this.root = root; this.host = document.createElement("div");
                    this.host.className = "host"; this.host.append(root); document.body.append(this.host);
                    const widget = {name, type, element:root, options:{}};
                    this.widgets.push(widget);
                    return widget;
                }
            }
            await window.extension.beforeRegisterNodeDef(Node, {name:type});
            let node = new Node(); node.onNodeCreated();
            await waitFor(() => node.root?.querySelectorAll(".h3c-card").length === 3);
            await wait(200);
            const checkBackingWidgets = () => {
                const visible = node.widgets.filter(widget => !widget.hidden);
                check(!visible.some(widget => widget.name === "plan_json"), type + ": no blank JSON textarea allocation");
                if (modern) {
                    check(visible.length === 1 && visible[0].name === "h3_chain_scene_editor",
                        "Modern Plan allocates canvas height only to the rich editor, not duplicate settings");
                    check(node.widgets.filter(widget => !widget.options?.hidden).length === 1,
                        "Modern Plan also excludes native rows from Vue layout");
                } else {
                    check(visible.some(widget => widget.name === "width"), "Legacy Plan keeps native settings");
                }
            };
            checkBackingWidgets();
            if (modern) {
                const width = node.widgets.find(widget => widget.name === "width");
                const socket = node.inputs.find(input => input.name === "width");
                socket.link = 42; node.onConnectionsChange();
                await waitFor(() => !width.hidden);
                check(width.value === 640 && socket.link === 42, "Connected backing socket and its value survive refresh");
                socket.link = null; node.onConnectionsChange();
                await waitFor(() => width.hidden);
                checkBackingWidgets();
            }
            await wait(40); // Let connection-refresh renders restore their scroll position first.
            const cards = () => [...node.root.querySelectorAll(".h3c-card")];
            const click = (text, root = node.root) => [...root.querySelectorAll("button")].find(b => b.textContent === text).click();
            const plan = () => node.widgets.find(w => w.name === "plan_json").value;
            const layout = () => node.properties.h3_chain_plan_layout;
            const collapsed = () => cards().filter(c => c.querySelector(".h3c-card-body").hidden);
            const input = (el, value) => { el.value = value; el.dispatchEvent(new Event("input", {bubbles:true})); };
            const checkScrollToBottom = async () => {
                const before = {plan:plan(), state:JSON.stringify(node._h3ChainEditor.plan),
                    properties:JSON.stringify(node.properties), size:JSON.stringify(node.size), requests};
                const firstCard = cards()[0];
                const others = graph._nodes.filter(other => other !== node && other.root?.isConnected)
                    .map(other => [other.root, other.root.scrollTop]);
                const page = [window.scrollX, window.scrollY];
                node.root.scrollTop = 0;
                const navigation = node.root.querySelector(".h3c-toolbar .h3c-scroll-bottom");
                check(navigation?.getAttribute("aria-label") === "Scroll to bottom",
                    type + ": accessible bottom navigation in the scene toolbar");
                const navigationBounds = navigation.getBoundingClientRect();
                const peers = [...navigation.parentElement.querySelectorAll("button")].filter(button => button !== navigation);
                check(navigationBounds.height > 0 && peers.every(button =>
                    Math.abs(button.getBoundingClientRect().height - navigationBounds.height) <= 1),
                    "Bottom navigation matches the other toolbar button heights");
                check(Math.abs(navigation.nextElementSibling.getBoundingClientRect().top - navigationBounds.top) <= 1,
                    "Bottom navigation aligns with the adjacent raw JSON button");
                navigation.click();
                await wait(40);
                check(Math.abs(node.root.scrollTop - (node.root.scrollHeight - node.root.clientHeight)) <= 1,
                    "Bottom navigation uses the current content height");
                const viewport = node.root.getBoundingClientRect();
                const footer = node.root.querySelector(".h3c-footer").getBoundingClientRect();
                const control = navigation.getBoundingClientRect();
                check(footer.bottom <= viewport.bottom && footer.top >= viewport.top, "Editor footer is visible at the bottom");
                check(control.top >= viewport.top && control.bottom <= viewport.bottom,
                    "Sticky bottom navigation stays reachable");
                check(others.every(([root, top]) => root.scrollTop === top)
                    && window.scrollX === page[0] && window.scrollY === page[1], "Only this editor scrolls");
                check(plan() === before.plan && JSON.stringify(node._h3ChainEditor.plan) === before.state
                    && JSON.stringify(node.properties) === before.properties, "Navigation leaves plan and properties unchanged");
                check(cards()[0] === firstCard && JSON.stringify(node.size) === before.size && requests === before.requests,
                    "Navigation does not rebuild cards, resize nodes, or request backend work");
                node.root.scrollTop = 0;
            };
            check(node.root.scrollHeight > node.root.clientHeight, "Expanded scenes overflow the editor for navigation testing");
            await checkScrollToBottom();
            check(collapsed().length === 0, type + ": old workflows start expanded");
            const authored = plan(), statePlan = JSON.stringify(node._h3ChainEditor.plan);
            const oldSize = JSON.stringify(node.size), oldRequests = requests;
            const first = cards()[0], textarea = first.querySelector(".h3c-prompt");
            const prefix = node.root.querySelector(".h3c-prefix");
            const prefixBody = () => node.root.querySelector(".h3c-prefix-body");
            const prefixToggle = () => node.root.querySelector(".h3c-prefix-collapse");
            check(prefixToggle() && !prefixBody().hidden, type + ": global prompt starts expanded");
            check(prefixToggle().textContent === "▾" && prefixToggle().getAttribute("aria-expanded") === "true",
                "Global prompt starts with an accessible expanded triangle");
            prefixToggle().click();
            check(prefixBody().hidden && prefixBody().getBoundingClientRect().height === 0,
                "Global prompt and its tools collapse");
            check(prefixToggle().textContent === "▸" && prefixToggle().getAttribute("aria-expanded") === "false"
                && prefixToggle().getAttribute("aria-label") === "Expand global prompt", "Accessible collapsed global prompt");
            check(layout().sharedPromptCollapsed === true && collapsed().length === 0,
                "Global collapse is saved separately from scenes");
            check(node.root.querySelector(".h3c-prefix-head").getBoundingClientRect().height > 0,
                "Global prompt heading stays visible");
            prefixToggle().click();
            check(!prefixBody().hidden && node.root.querySelector(".h3c-prefix") === prefix,
                "Expanding preserves the same editor");
            check(prefix.value === "Shared identity.\n\nKeep @reference.", "Global prompt text remains intact");
            prefixToggle().click();
            await wait(40);
            check(layout().promptHeights.shared === 144, "Hiding the prompt does not overwrite its saved height");
            check(graph._nodes.filter(other => other !== node && other.root?.isConnected).every(other =>
                other.root.querySelector(".h3c-prefix-body").hidden === false), "Global collapse is local to this node");
            first.querySelector(".h3c-collapse").click();
            check(collapsed().length === 1 && collapsed()[0] === first, "Only chosen scene collapses");
            check(first.querySelector(".h3c-card-body").getBoundingClientRect().height === 0, "Collapsed body is hidden");
            check(first.querySelector(".h3c-id").value === "one" && first.querySelector(".h3c-timing").textContent.includes("delivered"), "Name and timing stay visible");
            check(first.querySelector(".h3c-collapse").getAttribute("aria-expanded") === "false", "Accessible collapsed button");
            check(layout().collapsedScenes.one === true, "State lives in workflow properties");
            click("Collapse all"); check(collapsed().length === 3, "Collapse all");
            await checkScrollToBottom();
            click("Expand all"); check(collapsed().length === 0, "Expand all");
            check(prefixBody().hidden, "Scene bulk controls leave the global prompt state alone");
            check(graph._nodes.filter(other => other !== node && other.root?.isConnected).every(other =>
                [...other.root.querySelectorAll(".h3c-card-body")].every(body => body.hidden)), "Bulk action is local to this Plan node");
            check(cards()[0] === first && cards()[0].querySelector(".h3c-prompt") === textarea, "No editor rebuild on toggles");
            await wait(40);
            check(plan() === authored && JSON.stringify(node._h3ChainEditor.plan) === statePlan, "Prompts, seeds and generation JSON unchanged");
            check(requests === oldRequests && JSON.stringify(node.size) === oldSize, "No network work or node resizing");
            check(layout().promptHeights["scene:one"] === 224 && layout().advanced && node.properties.execution_marker === "keep", "Other UI and execution properties preserved");
            first.querySelector(".h3c-collapse").click();
            const saved = JSON.parse(JSON.stringify({plan:plan(), properties:node.properties}));
            node.onRemoved(); node.host.remove();
            node = new Node(saved); node.onNodeCreated(); node.onConfigure();
            await waitFor(() => node.root?.querySelectorAll(".h3c-card").length === 3);
            checkBackingWidgets();
            check(collapsed().length === 1 && collapsed()[0].querySelector(".h3c-id").value === "one", "Collapsed state survives workflow reload");
            check(prefixBody().hidden && prefixToggle().getAttribute("aria-expanded") === "false",
                "Global prompt collapse survives workflow reload");
            prefixToggle().click();
            check(node.root.querySelector(".h3c-prefix").value === prefix.value
                && node.root.querySelector(".h3c-prefix").style.height === "144px",
                "Global prompt contents and height survive reload");
            prefixToggle().click();
            check(plan() === authored, "Reload preserves generation JSON");
            // Rename and reorder keep the state attached to the same scene.
            input(cards()[0].querySelector(".h3c-id"), "renamed");
            check(layout().collapsedScenes.renamed === true && !layout().collapsedScenes.one, "Rename transfers collapse state");
            click("↓", cards()[0]);
            check(collapsed().length === 1 && collapsed()[0] === cards()[1], "Reorder follows scene ID, not row number");
            check(prefixBody().hidden, "Global prompt stays collapsed after editor rerender");
            click("Duplicate", cards()[1]);
            check(cards().length === 4 && !cards()[2].querySelector(".h3c-card-body").hidden, "New duplicate starts expanded");
            check(cards()[2].querySelector(".h3c-prompt").value.includes("Keep this text."), "Duplicate keeps prompt");
            click("Delete", cards()[1]);
            check(cards().length === 3 && !layout().collapsedScenes.renamed, "Delete clears only deleted scene state");
            click("Collapse all");
            check(collapsed().length === 3, "Bulk controls still work after edits");
            // Existing-node configure must use the newly restored properties too.
            node.properties = {...node.properties, h3_chain_plan_layout:{...layout(), collapsedScenes:{}, sharedPromptCollapsed:false}};
            node.onConfigure();
            await waitFor(() => collapsed().length === 0);
            checkBackingWidgets();
            check(collapsed().length === 0, "Configure restores expanded state without stale UI");
            check(!prefixBody().hidden && prefixToggle().getAttribute("aria-expanded") === "true",
                "Configure restores global prompt state without stale UI");
            input(node.root.querySelector(".h3c-prefix"), "Edited global prompt.");
            const edited = plan();
            prefixToggle().click(); prefixToggle().click();
            check(plan() === edited && node.root.querySelector(".h3c-prefix").value === "Edited global prompt.",
                "Collapse never reverts an edited global prompt");
            click("Raw JSON");
            await wait(40);
            await checkScrollToBottom();
            click("Hide raw JSON");
            await wait(40);
            // Navigation fits the minimum-width, tablet-sized editor viewport.
            await checkScrollToBottom();
            const toolbar = node.root.querySelector(".h3c-toolbar");
            check(toolbar.scrollWidth <= toolbar.clientWidth, "Scene toolbar fits the minimum editor width");
            click("Collapse all");
            node.root.scrollTop = 0;
        }
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.setAttribute("data-report", btoa(JSON.stringify(report)));
}
