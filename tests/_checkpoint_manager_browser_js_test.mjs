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
    "--user-data-dir=" + join(out, "profile"), "--virtual-time-budget=2500", "--window-size=1900,1100",
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
        const alternate = {scene:1,scene_id:"scene_1",revision:"e".repeat(32),alternate_of_revision:seven[0].revision,
            take_kind:"editorial_alternate",ready:true,used_in_final_cut:true,created_at:"2026-09-10T10:00:00Z"};
        seven[0].alternates = [alternate];
        payload.revisions = [...seven, alternate];
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
        const output = node.widgets[0].value;
        const zoomInput = root.querySelector(".h3cm-graph-zoom");
        const graph = root.querySelector(".h3cm-fork-graph");
        const firstCard = graph.querySelector(".h3cm-revision");
        const originalWidth = firstCard.getBoundingClientRect().width;
        const sliderWidth = zoomInput.getBoundingClientRect().width;
        const previewWidth = root.querySelector(".h3cm-preview").getBoundingClientRect().width;
        const setZoom = async value => {
            zoomInput.value=String(value);zoomInput.dispatchEvent(new Event("input",{bubbles:true}));
            await new Promise(resolve=>setTimeout(resolve,70));
        };
        const checkEdges = label => {
            for (const path of graph.querySelectorAll(".h3cm-fork-edge")) {
                const from = [...graph.querySelectorAll(".h3cm-fork-node")].find(item=>item.dataset.graphKey===path.dataset.from)
                    .querySelector(".h3cm-revision").getBoundingClientRect();
                const to = [...graph.querySelectorAll(".h3cm-fork-node")].find(item=>item.dataset.graphKey===path.dataset.to)
                    .querySelector(".h3cm-revision").getBoundingClientRect();
                const coords = path.getAttribute("d").match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi).map(Number);
                const matrix = path.getScreenCTM();
                const start = new DOMPoint(coords[0],coords[1]).matrixTransform(matrix);
                const tip = new DOMPoint(coords[10],coords[11]).matrixTransform(matrix);
                check(Math.abs(start.x-from.right)<2 && Math.abs(start.y-(from.top+from.height/2))<2,
                    label+": connector starts at original card");
                check(Math.abs(tip.x+3*matrix.a-to.left)<2 && Math.abs(tip.y-(to.top+to.height/2))<2,
                    label+": connector points to exact child");
            }
        };
        await setZoom(50);
        check(Math.abs(firstCard.getBoundingClientRect().width-originalWidth/2)<1,"Graph cards scale to 50 percent");
        check(zoomInput.getBoundingClientRect().width===sliderWidth,"Zoom toolbar controls are not scaled");
        check(root.querySelector(".h3cm-preview").getBoundingClientRect().width===previewWidth,"Preview/inspector is not scaled");
        checkEdges("50 percent graph zoom");
        document.getElementById("host").style.transform="scale(0.7)";
        document.getElementById("host").style.transformOrigin="top left";
        await setZoom(125);
        checkEdges("125 percent graph plus 70 percent Comfy canvas zoom");
        document.getElementById("host").style.transform="";
        root.querySelector(".h3cm-graph-zoom-fit").click();
        await new Promise(resolve=>setTimeout(resolve,70));
        const viewport=root.querySelector(".h3cm-fork-scroll");
        check(viewport.scrollWidth<=viewport.clientWidth+2,"Fit width removes unnecessary horizontal scrolling");
        root.querySelector(".h3cm-graph-zoom-reset").click();
        await new Promise(resolve=>setTimeout(resolve,70));
        check(zoomInput.value==="100","Percentage button resets zoom to 100 percent");
        check(node.widgets[0].value===output,"Graph zoom cannot change source selection");
        check(root.querySelectorAll(".h3cm-alternate").length === 1,"Original graph shows ALT once under its base");
        const tab = label => [...root.querySelectorAll('[role="tab"]')].find(item=>item.textContent === label);
        check(tab("Original · 8")?.getAttribute("aria-selected") === "true","Original stage includes its ALT takes");
        check(!tab("ALT · 1"),"ALT editor tabs belong in Plan Studio, not Checkpoint Manager");
        const originalCell = root.querySelector('.h3cm-fork-node[data-graph-key="1:' + seven[0].revision + '"]');
        check(Boolean(originalCell.querySelector(".h3cm-alternate")),"ALT is nested under the exact original");
        check(originalCell.querySelector(".h3cm-final-cut-alt").textContent === "Final cut: ALT · eeeeeeee",
            "The original checkpoint line identifies the selected final-cut ALT");
        check(Boolean(root.querySelector(".h3cm-alternate-used")),"Used ALT is marked");
        check(!root.querySelector(".h3cm-branches [aria-expanded]"),"Inline ALTs have no collapse controls");
        root.querySelector(".h3cm-alternate").click(); await new Promise(resolve=>setTimeout(resolve,100));
        check(action.disabled || [...root.querySelectorAll("button")].find(item=>item.textContent === "Assign path to Original").disabled,
            "Previewing ALT cannot assign it as generation lineage");
        check(node.widgets[0].value === output,"Previewing ALT preserves output selection");
        check(root.querySelector(".h3cm-stage-note").textContent.length < 120,"Help text stays concise");
        for (const width of [900,1500]) {
            document.getElementById("host").style.width = width + "px";
            check(root.scrollWidth <= root.clientWidth + 1,`ALT has no root overflow at ${width}px`);
        }
        const other = seven.map((item,i)=>({...item, revision:("8" + i).repeat(16), active:false,
            alternates:[], compatibility:{width:1344,height:768},
            ...(i ? {parent:{scene:i,revision:("8" + (i - 1)).repeat(16)}} : {})}));
        const fork = [...seven.slice(0,5), {...seven[5],revision:"c".repeat(32)},
            {...seven[6],revision:"d".repeat(32),parent:{scene:6,revision:"c".repeat(32)}}];
        payload.revisions.push(...other,...fork.slice(5));
        payload.branches.push({active:false,path:other},{active:false,path:fork});
        node._h3CheckpointManagerRefresh(); await new Promise(resolve=>setTimeout(resolve,100));
        const nodes = [...root.querySelectorAll(".h3cm-fork-node")];
        const findCell = revision => nodes.find(item=>item.dataset.graphKey.endsWith(":" + revision));
        const rootY = findCell(seven[0].revision).getBoundingClientRect().top;
        const forkY = findCell(fork[5].revision).getBoundingClientRect().top;
        const otherY = findCell(other[0].revision).getBoundingClientRect().top;
        check(rootY < forkY && forkY < otherY,"Related fork stays above the unrelated branch in the real grid");
        check(findCell(other[6].revision).getBoundingClientRect().top === otherY,"Unrelated seven-scene family stays together");
        check(node.widgets[0].value === output,"Layout grouping cannot change the output path");
        payload.run_name = "demo";
        alternate.used_in_final_cut = false; // Old assignment-view badge is wrong.
        payload.final_cut_contexts = [
            {id:"main",name:"Original",lineage:other.map(({scene,revision})=>({scene,revision})),replacements:[]},
            {id:named,name:"960x544",lineage:seven.map(({scene,revision})=>({scene,revision})),replacements:[
                {scene:1,base_revision:seven[0].revision,alternate_revision:alternate.revision}]},
        ];
        node._h3CheckpointManagerRefresh(); await new Promise(resolve=>setTimeout(resolve,100));
        const cut = root.querySelector(".h3cm-final-cut-select");
        check(root.querySelector(".h3cm-final-cut-status").textContent.includes("Resolved: 960x544"),"Local output resolves named final cut");
        check(Boolean(root.querySelector(".h3cm-alternate-used")),"Named final cut marks ALT despite Original assignment view");
        check(root.querySelector(".h3cm-final-cut-alt").textContent.includes("eeeeeeee"),"Base line marks the resolved ALT");
        check(node.widgets[0].value===output,"Auto final cut does not rewrite the pin or output folder");
        cut.value="main";cut.dispatchEvent(new Event("change",{bubbles:true}));
        check(!root.querySelector(".h3cm-alternate-used"),"Explicit Original changes only final-cut picture selection");
        check(JSON.parse(node.widgets[0].value).final_cut_branch_id==="main","Explicit cut is serialized");
        cut.value="auto";cut.dispatchEvent(new Event("change",{bubbles:true}));
        check(Boolean(root.querySelector(".h3cm-alternate-used")),"Auto restores the selected path's ALT marker");
        check(node.widgets[0].value===output,"Auto restores the original pin bytes");
        for (const width of [900,1500]) {
            document.getElementById("host").style.width=width+"px";
            check(root.scrollWidth<=root.clientWidth+1,`Final-cut control has no root overflow at ${width}px`);
        }
        const host = document.getElementById("host"); host.style.width="1850px";host.style.height="1040px";
        root.querySelector(".h3cm-main").style.gridTemplateColumns="minmax(0,1fr)";
        root.querySelector(".h3cm-detail").style.display="none";
        await new Promise(resolve=>setTimeout(resolve,100));
        await setZoom(65);
        node.onRemoved?.();
    } catch (error) {report.failures.push(error.stack || String(error));}
    document.body.dataset.report = btoa(JSON.stringify(report));
}
