// Isolated real-DOM layout check. No running ComfyUI or project files are used.
import assert from "node:assert/strict";
import {readFileSync, writeFileSync, mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";

const read = name => readFileSync(new URL("../web/" + name, import.meta.url), "utf8");
const modules = ["h3_checkpoint_manager_core.mjs", "h3_working_branches.mjs", "h3_checkpoint_graph.mjs"]
    .map(name => read(name).replace(/^export /gm, "")).join("\n");
const extension = read("h3_chain_checkpoint_manager.js")
    .replace(/^import\s[\s\S]*?from\s+"[^"]+";\n/gm, "");
if (!process.argv.includes("--browser")) {
    console.log("Checkpoint browser fixture available; use --browser to run isolated Chrome checks.");
    process.exit(0);
}
const out = mkdtempSync(join(tmpdir(), "h3-checkpoint-ui-"));
const html = '<!doctype html><meta charset="utf-8"><style>body{margin:8px;background:#171717}'
    + '#host{width:1500px;height:960px}</style><div id="host"></div><script>\n'
    + modules + "\n" + "(" + browserChecks.toString() + ")(" + JSON.stringify(extension).replace(/<\/script/gi,"<\\/script")
    + ");</script>";
const file = join(out, "fixture.html");
writeFileSync(file, html);
const run = spawnSync(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
    "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--host-resolver-rules=MAP * ~NOTFOUND",
    "--user-data-dir=" + join(out, "profile"), "--virtual-time-budget=2000", "--window-size=1530,1000",
    "--screenshot=" + join(out, "checkpoint-manager.png"), "--dump-dom", pathToFileURL(file).href,
], {encoding:"utf8",timeout:25000,maxBuffer:2 * 1024 * 1024});
assert.equal(run.status,0,run.error?.message || run.stderr);
const encoded = run.stdout.match(/data-report="([^"]+)"/)?.[1];
assert.ok(encoded,"Browser fixture did not finish: " + run.stdout.slice(-2000));
const report = JSON.parse(Buffer.from(encoded,"base64").toString());
console.log(report);
console.log("Isolated screenshot: " + join(out,"checkpoint-manager.png"));
assert.deepEqual(report.failures,[]);

async function browserChecks(extensionSource) {
    const report = {checks:0,failures:[]};
    const check = (condition, message) => {report.checks++; if (!condition) report.failures.push(message);};
    try {
        // Chrome's --dump-dom virtual clock can exhaust timers before its
        // compositor paints. Drive the frame clock with virtual timers here;
        // the production SVG renderer still measures the real browser DOM.
        window.requestAnimationFrame = callback => setTimeout(()=>callback(performance.now()),16);
        window.cancelAnimationFrame = clearTimeout;
        const seven = Array.from({length:7},(_,i)=>({scene:i + 1,revision:String(i + 1).repeat(32),
            active:i === 0,ready:true,compatibility:{width:960,height:544},
            created_at:`2026-09-09T${String(10 + i).padStart(2,"0")}:00:00Z`,
            ...(i ? {parent:{scene:i,revision:String(i).repeat(32)}} : {})}));
        const payload = {revisions:seven,scenes:seven.map(item=>({scene:item.scene,scene_id:`scene_${item.scene}`,revision_count:1})),
            branches:[{active:true,path:[seven[0]],attribution_slot:{scene:2,candidates:[],blocked_candidates:[seven[1]]}},
                {active:false,path:seven}],summary:{scene_count:7,revision_count:7,branch_count:2,bytes:0}};
        const named = "a".repeat(32);
        const studio = {type:"MiniMaxH3ChainPlanStudio",inputs:[],widgets:[{name:"run_name",value:"demo"},
            {name:"plan_json",value:'{"shots":[{"id":"one"}]}'},{name:"working_branch_id",value:named}]};
        const app = {registerExtension(){},graph:{setDirtyCanvas(){}}};
        const api = {apiURL:path=>path,fetchApi:async path=>{
            let data;
            if (path.endsWith("/runs")) data = {runs:[{run_name:"demo",checkpoint_count:7}]};
            else if (path.includes("/working-branches?")) data = {default_branch:"main",branches:[{id:"main",name:"Original"},{id:named,name:"960x544"}]};
            else if (path.includes("/checkpoints?")) data = payload;
            else if (path.endsWith("/delete-preview")) data = {allowed:false,blockers:["A saved dependency uses this take."],
                files:Array.from({length:50},(_,i)=>({exists:true,label:"checkpoint",path:`demo/checkpoints/file_${i}`,size_bytes:100}))};
            else throw new Error("Unexpected request " + path);
            return {ok:true,json:async()=>data};
        }};
        const node = {properties:{h3_checkpoint_manager_run:"demo"},inputs:[{link:11}],
            widgets:[{name:"selection_json",value:""}],graph:{links:{11:{origin_id:12}},getNodeById:()=>studio,setDirtyCanvas(){}},
            setSize(){},addDOMWidget(_name,_type,root){document.getElementById("host").append(root);return {};}};
        // Production module, with only the ComfyUI imports replaced by stubs.
        eval(extensionSource + "\nmount(node);");
        await new Promise(resolve=>setTimeout(resolve,100));
        const root = document.querySelector(".h3cm-root");
        const card = [...root.querySelectorAll("button")].find(item=>item.textContent.startsWith("S7 · 77777777"));
        check(Boolean(card),"Seven-scene saved path renders");
        card.click(); await new Promise(resolve=>setTimeout(resolve,100));
        const action = [...root.querySelectorAll("button")].find(item=>item.textContent === "Assign path to Original");
        check(Boolean(action && !action.disabled),"Original assignment is enabled for the saved 960x544 path");
        check(!root.querySelector(".h3cm-fork-slot"),"Blocked-only fake slot is absent");
        check(root.querySelector(".h3cm-assignment-context").textContent.includes("scenes 1–7"),"Assignment range is explicit");
        check(JSON.parse(node.widgets[0].value).lineage.length === 1,"Preview cannot expand output");
        check(action.getBoundingClientRect().bottom <= root.querySelector(".h3cm-main").getBoundingClientRect().top,
            "Assignment is visible above the graph");
        const deletion = root.querySelector(".h3cm-delete"), remove = deletion.querySelector("button");
        const details = deletion.querySelector("details");
        check(!details.open,"The file inventory starts collapsed, with its controls still visible");
        details.open = true;
        check(remove.getBoundingClientRect().bottom <= deletion.querySelector(".h3cm-delete-body").getBoundingClientRect().top,
            "Delete is outside and before the scrollable file inventory");
        check(root.querySelector(".h3cm-delete-body").clientHeight <= 135,"Large file inventories stay bounded");
        details.open = false;
        for (const [width,height] of [[900,620],[1500,960]]) {
            const host = document.getElementById("host"); host.style.width = width + "px";host.style.height = height + "px";
            check(root.querySelector(".h3cm-main").getBoundingClientRect().height >= 240,
                `Graph cannot collapse beneath toolbar at ${width}×${height}`);
            check(root.scrollWidth <= root.clientWidth + 1,`No root horizontal overflow at ${width}×${height}`);
        }
        await new Promise(resolve=>setTimeout(resolve,100));
        check(root.querySelectorAll(".h3cm-fork-edge").length === 6,"All six saved continuation arrows render after resize");
        node.onRemoved?.();
    } catch (error) {report.failures.push(error.stack || String(error));}
    document.body.dataset.report = btoa(JSON.stringify(report));
}
