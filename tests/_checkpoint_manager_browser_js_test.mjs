// Isolated real-DOM layout check. No running ComfyUI or project files are used.
import assert from "node:assert/strict";
import {readFileSync, writeFileSync, mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";

const read = name => readFileSync(new URL("../web/" + name, import.meta.url), "utf8");
const modules = ["h3_dom_wheel.mjs", "h3_chain_plan_core.mjs", "h3_context_take_core.mjs", "h3_checkpoint_manager_core.mjs", "h3_working_branches.mjs", "h3_checkpoint_graph.mjs", "h3_storage_inspector.mjs", "h3_checkpoint_multiselect.mjs"]
    .map(name => read(name).replace(/^import\s[\s\S]*?from\s+"[^"]+";\n/gm, "")
        .replace(/^export /gm, "")).join("\n");
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
    + "," + process.argv.includes("--storage") + "," + process.argv.includes("--inspector") + ");</script>";
const file = join(out, "fixture.html");
writeFileSync(file, html);
const run = spawnSync(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
    "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--host-resolver-rules=MAP * ~NOTFOUND",
    "--user-data-dir=" + join(out, "profile"), "--virtual-time-budget=6000", "--window-size=1900,1100",
    "--screenshot=" + join(out, "checkpoint-manager.png"), "--dump-dom", pathToFileURL(file).href,
], {encoding:"utf8",timeout:25000,maxBuffer:2 * 1024 * 1024});
assert.equal(run.status,0,run.error?.message || run.stderr);
const encoded = run.stdout.match(/data-report="([^"]+)"/)?.[1];
assert.ok(encoded,"Browser fixture did not finish: " + run.stdout.slice(-2000));
const report = JSON.parse(Buffer.from(encoded,"base64").toString());
console.log(report);
console.log("Isolated screenshot: " + join(out,"checkpoint-manager.png"));
assert.deepEqual(report.failures,[]);

async function browserChecks(extensionSource, keepStorageOpen, inspectorOnly) {
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
            metadata_path:`h3_chains/demo/checkpoints/clip_${String(i+1).padStart(4,"0")}.${"long-metadata-path".repeat(12)}.json`,
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
            {name:"plan_json",value:JSON.stringify({shots:Array.from({length:8}, (_,i)=>({id:`scene_${i+1}`,seed:String(i+1)}))})},{name:"working_branch_id",value:named}]};
        let contextRefreshes = 0;
        const refreshRestoredPlanEditors = () => { contextRefreshes++; };
        const app = {registerExtension(){},graph:{setDirtyCanvas(){}}};
        let storageRequests = 0;
        const bulkRequests = [];
        let bulkAllowed = true;
        let releaseCutPreview = false;
        let bulkDeletes = 0, bulkDeleteError = false;
        let obsoleteAllowed = true, obsoleteError = "", obsoleteDeleteError = "", obsoleteDeletes = 0;
        const obsoleteRequests = [];
        const branchCleanupRequests = [];
        let branchCleanupDeletes = 0, branchCleanupError = "";
        let branchList = {default_branch:"main",branches:[{id:"main",name:"Original"},{id:named,name:"960x544"}]};
        const emptyActions = [];
        let emptyBlocked = true;
        const projectMutationOptions = async (_node, _run, options) => options;
        const storageReport = {format:"h3_storage_inventory_v1",run_name:"demo",scan_complete:true,
            totals:{files:61,logical_bytes:6100,allocated_bytes:8192,allocation_unknown_files:0},
            categories:{takes:{files:61,logical_bytes:6100,allocated_bytes:8192,allocation_unknown_files:0}},
            limitations:["Not a deletion authority."],longest_paths:[],issues:[],issue_counts:{},issues_omitted:0,
            files:Array.from({length:61},(_,i)=>({path:i ? `segments/clip_${i}.mp4` : '<img src=x onerror=alert(1)>',
                category:"takes",branch_id:"main",profile:null,logical_bytes:100,
                referenced_by:[],flags:["unverified","unreferenced_candidate"]}))};
        const api = {apiURL:path=>path,fetchApi:async (path,options={})=>{
            let data;
            if (path.endsWith("/runs")) data = {runs:[{run_name:"demo",checkpoint_count:7}]};
            else if (path.includes("/working-branches?") && options.method !== 'POST') data = structuredClone(branchList);
            else if (path.split('?')[0].endsWith('/working-branches') && ['empty-preview','delete-empty','hide-original','show-original'].includes(JSON.parse(options.body).action)) {
                const body = JSON.parse(options.body);
                emptyActions.push(body);
                const target = branchList.branches.find(item => item.id === body.branch_id);
                if (body.action === 'empty-preview') {
                    data = {allowed:!emptyBlocked,blockers:emptyBlocked ? ['Saved clips remain.'] : [],
                        branch_name:target.name,keep_branch_name:'960x544',snapshot:'empty-test',
                        changes_default:branchList.default_branch === body.branch_id,
                        action:body.branch_id === 'main' ? 'hide-original' : 'delete-empty',
                        message:'Plan metadata retained; no media deleted.'};
                } else {
                    if (body.action === 'show-original') delete target.hidden;
                    else {
                        check(body.snapshot === 'empty-test' && body.keep_branch_id === named,
                            'Empty branch removal confirms the exact preview and keeps the active Plan branch');
                        if (body.action === 'hide-original') target.hidden = true;
                        else branchList.branches = branchList.branches.filter(item => item.id !== body.branch_id);
                        if (branchList.default_branch === body.branch_id) branchList.default_branch = named;
                    }
                    data = {ok:true,message:'Empty branch action completed; metadata retained.'};
                }
            }
            else if (path.endsWith('/working-branches')) {
                const body = JSON.parse(options.body);
                branchCleanupRequests.push(body);
                check(body.branch_id === 'main' && body.keep_branch_id === named,
                    'Branch cleanup targets Original and explicitly keeps the Plan Studio branch');
                if (body.action === 'delete-path') {
                    branchCleanupDeletes++;
                    check(body.snapshot === 'branch-cleanup-test', 'Branch cleanup confirms the exact preview');
                }
                if (branchCleanupError) return {ok:false,status:409,json:async()=>({error:branchCleanupError})};
                data = body.action === 'delete-path' ? {ok:true,message:'Cleared Original; shared takes kept.'} : {
                    allowed:true,branch_name:'Original',snapshot:'branch-cleanup-test',retired_snapshots:3,
                    revisions:[seven[6]],retained_revisions:[seven[0]],reclaimed_bytes:50000,
                    message:'Release Original assignments and edit. Keep other branches.',
                    files:Array.from({length:500},(_,i)=>({path:`old/file_${i}`,exists:true,owned:true}))};
            }
            else if (path.includes("/checkpoints?")) data = payload;
            else if (path.endsWith('/obsolete-preview')) {
                const body = JSON.parse(options.body);
                obsoleteRequests.push(body);
                if (obsoleteError) return {ok:false,status:400,json:async()=>({error:obsoleteError})};
                data = {allowed:obsoleteAllowed, snapshot:'obsolete-test',
                    revisions:[{scene:body.scene,revision:body.revision}], retained_revisions:[],
                    owned_file_count:500,reclaimed_bytes:50000,
                    blockers:obsoleteAllowed ? [] : ['A sealed chapter snapshot still protects this take.'],
                    files:Array.from({length:500},(_,i)=>({exists:true,owned:true,size_bytes:100,path:`demo/obsolete_${i}.mp4`}))};
            }
            else if (path.endsWith('/obsolete-delete')) {
                const body = JSON.parse(options.body);
                check(body.snapshot === 'obsolete-test', 'Obsolete deletion sends the exact preview snapshot');
                check(body.scene === 7 && body.revision === seven[6].revision, 'Obsolete deletion targets only the previewed take');
                obsoleteDeletes++;
                if (obsoleteDeleteError) return {ok:false,status:423,json:async()=>({error:obsoleteDeleteError})};
                data = {ok:true,message:'Deleted obsolete fixture path.',reclaimed_bytes:50000};
            }
            else if (path.endsWith('/bulk-preview')) {
                const body = JSON.parse(options.body);
                bulkRequests.push(body);
                data = {allowed:bulkAllowed, revisions:body.revisions, snapshot:'bulk-test', rollback_scenes:[],
                    editorial_releases:releaseCutPreview ? [{scene:1,alternate_revision:alternate.revision,base_revision:seven[0].revision}] : [],
                    blockers:bulkAllowed ? [] : ['An unselected scene still uses this take.'],
                    owned_file_count:2,reclaimed_bytes:2048,files:[],not_deleted:['Shared media']};
            }
            else if (path.includes('/bulk-delete')) {
                const body = JSON.parse(options.body);
                check(body.snapshot === 'bulk-test', 'Bulk confirmation sends the preview snapshot');
                bulkDeletes++;
                if (bulkDeleteError) return {ok:false,status:409,json:async()=>({error:'Preview changed; preview again.'})};
                data = {ok:true,message:'Deleted selected fixture revisions; shared files kept.'};
            }
            else if (path.includes("/storage-inventory?")) {
                storageRequests++;
                check(options.method === "GET", "Storage inspection only uses GET");
                check(!path.includes("branch_id"), "Storage inspection is project-wide, not scoped to a working branch");
                data = storageReport;
            }
            else if (path.endsWith("/delete-preview")) data = {allowed:false,blockers:["A saved dependency uses this take."],
                final_cut_selection:releaseCutPreview && JSON.parse(options.body).revision === alternate.revision,
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
        const initialViewport = root.querySelector(".h3cm-fork-scroll");
        initialViewport.scrollLeft = initialViewport.scrollWidth;
        const initialScroll = initialViewport.scrollLeft;
        check(initialScroll > 0, "Saved path overflows horizontally before selecting its last clip");
        card.click(); await new Promise(resolve=>setTimeout(resolve,100));
        // #116: the standalone checkbox must not consume one cell and invert
        // every later dt/dd pair. Use actual rendering, long paths and widths.
        const inspector = root.querySelector(".h3cm-inspector");
        const inspectSnapshot = node.widgets[0].value;
        const pair = name => [...inspector.children].find(item => item.tagName === "DT" && item.textContent === name);
        for (const width of [650, 390, 260]) {
            inspector.style.width = `${width}px`;
            const area = inspector.getBoundingClientRect();
            for (const name of ["Identity", "Incoming", "Parent", "Following", "Storage", "Canvas", "Audio mode", "Encoding", "Metadata"]) {
                const term = pair(name), value = term?.nextElementSibling;
                check(Boolean(term && value?.tagName === "DD"), `${name} remains a label/value pair`);
                if (!term || !value) continue;
                const labelBox = term.getBoundingClientRect(), valueBox = value.getBoundingClientRect();
                check(Math.abs(labelBox.top - valueBox.top) < 1 && labelBox.right < valueBox.left,
                    `${width}px: ${name} label stays left of its value on the same row`);
                check(valueBox.width > width / 2 && valueBox.right <= area.right + 1,
                    `${width}px: ${name} has room for readable values`);
            }
            const option = inspector.querySelector(".h3cm-inspector-option");
            check(option && Math.abs(option.getBoundingClientRect().width - area.width) < 1,
                `${width}px: pixel upscale option spans both columns`);
            check(inspector.scrollWidth <= inspector.clientWidth + 1,
                `${width}px: long metadata wraps without horizontal overflow`);
        }
        inspector.style.removeProperty("width");
        check(node.widgets[0].value === inspectSnapshot, "Inspector layout never changes output selection");
        if (inspectorOnly) {
            inspector.scrollIntoView({block:"center"});
            node.onRemoved?.();
            document.body.dataset.report = btoa(JSON.stringify(report));
            return;
        }
        const contextButton = [...root.querySelectorAll("button")].find(item=>item.textContent === "Use as context for Scene 8");
        check(contextButton && !contextButton.disabled, "Saved take can be selected as context with standalone Studio");
        const originalPlan = parsePlanJson(studio.widgets.find(item=>item.name === "plan_json").value);
        const originalOutput = node.widgets[0].value;
        contextButton.click();
        const pinnedPlan = JSON.parse(studio.widgets.find(item=>item.name === "plan_json").value);
        check(pinnedPlan.shots[7].context_take?.revision === "7".repeat(32), "Action pins the exact selected revision on the following scene");
        check(JSON.stringify(pinnedPlan.shots.slice(0,7)) === JSON.stringify(originalPlan.shots.slice(0,7)), "Context action does not edit earlier scenes");
        check(node.widgets[0].value === originalOutput && contextRefreshes === 1, "Context action leaves output selection alone and refreshes Plan editors");
        [...root.querySelectorAll("button")].find(item=>item.textContent === "Use assigned take").click();
        check(studio.widgets.find(item=>item.name === "plan_json").value.indexOf('context_take') < 0, "Reset removes the context override");
        check(root.querySelector(".h3cm-fork-scroll").scrollLeft === initialScroll,
            "Selecting a clip preserves horizontal scroll without chapter metadata");
        const action = [...root.querySelectorAll("button")].find(item=>item.textContent === "Assign path to Original");
        check(Boolean(action && !action.disabled),"Original assignment is enabled for the saved 960x544 path");
        check(!root.querySelector(".h3cm-fork-slot"),"Blocked-only fake slot is absent");
        check(root.querySelector(".h3cm-assignment-context").textContent.includes("scenes 1–7"),"Assignment range is explicit");
        check(JSON.parse(node.widgets[0].value).lineage.length === 1,"Preview cannot expand output");
        check(action.getBoundingClientRect().bottom <= root.querySelector(".h3cm-main").getBoundingClientRect().top,
            "Assignment is visible above the graph");
        const deletion = root.querySelector(".h3cm-delete"), remove = deletion.querySelector(".h3cm-delete-actions .h3cm-delete-button");
        const details = deletion.querySelector("details");
        check(!details.open,"The file inventory starts collapsed, with its controls still visible");
        details.open = true;
        check(remove.getBoundingClientRect().bottom <= deletion.querySelector(".h3cm-delete-body").getBoundingClientRect().top,
            "Delete is outside and before the scrollable file inventory");
        check(root.querySelector(".h3cm-delete-body").clientHeight <= 135,"Large file inventories stay bounded");
        details.open = false;
        const obsolete = [...deletion.querySelectorAll('button')].find(item=>item.textContent === 'Delete obsolete path…');
        const obsoletePanel = deletion.querySelector('.h3cm-obsolete-preview');
        const deleteGap = obsolete.getBoundingClientRect().left - remove.getBoundingClientRect().right;
        check(deleteGap >= 0 && deleteGap <= 10, 'Related delete buttons stay together instead of opposite edges');
        obsolete.click(); await new Promise(resolve=>setTimeout(resolve,30));
        check(obsoleteRequests.length === 1 && obsoleteDeletes === 0, 'Obsolete action only previews on first click');
        const confirmObsolete = obsoletePanel.querySelector('button');
        const obsoleteDetails = obsoletePanel.querySelector('details');
        check(Boolean(confirmObsolete) && !confirmObsolete.disabled, 'Allowed obsolete path has an enabled confirmation');
        check(!obsoleteDetails.open && obsoletePanel.getBoundingClientRect().height < 160,
            'Five hundred files cannot bury obsolete confirmation');
        obsoleteDetails.open = true;
        check(obsoleteDetails.querySelector('.h3cm-delete-body').clientHeight <= 135,
            'Expanded obsolete inventory has bounded height');
        check(confirmObsolete.getBoundingClientRect().bottom <= obsoleteDetails.getBoundingClientRect().top,
            'Obsolete confirmation remains above the inventory');
        window.confirm = () => false;
        confirmObsolete.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(obsoleteDeletes === 0, 'Cancelled obsolete confirmation never deletes');
        window.confirm = () => true;
        confirmObsolete.click(); await new Promise(resolve=>setTimeout(resolve,50));
        check(obsoleteDeletes === 1 && obsoletePanel.hidden, 'Confirmed obsolete deletion completes and clears its preview');
        obsolete.click(); await new Promise(resolve=>setTimeout(resolve,20));
        obsoleteDeleteError = 'This workflow is read-only.';
        obsoletePanel.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,30));
        check(!obsoletePanel.hidden && obsoletePanel.textContent.includes(obsoleteDeleteError)
            && !obsoletePanel.querySelector('button'), 'Deletion rejection is visible beside the controls, with no stale confirmation');
        obsoleteDeleteError = '';
        obsoleteAllowed = false;
        obsolete.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(!obsoletePanel.querySelector('button') && obsoletePanel.textContent.includes('sealed chapter'),
            'Protected obsolete path displays its blocker without offering deletion');
        obsoleteError = 'Cannot read checkpoint metadata.';
        obsolete.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(!obsoletePanel.hidden && obsoletePanel.textContent.includes(obsoleteError),
            'Preview failures stay visible next to obsolete-path controls');
        obsoleteError = ''; obsoleteAllowed = true;
        obsolete.click(); await new Promise(resolve=>setTimeout(resolve,20));
        const staleConfirm = obsoletePanel.querySelector('button');
        [...root.querySelectorAll('button')].find(item=>item.textContent.startsWith('S2 · 22222222')).click();
        await new Promise(resolve=>setTimeout(resolve,30));
        staleConfirm.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(obsoletePanel.hidden && obsoleteDeletes === 2, 'Changing selection invalidates obsolete confirmation');
        [...root.querySelectorAll('button')].find(item=>item.textContent.startsWith('S7 · 77777777')).click();
        await new Promise(resolve=>setTimeout(resolve,30));
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
        const bulkCard = scene => root.querySelector(`[data-bulk-key="${scene}:${String(scene).repeat(32)}"]`);
        const modifiedClick = (scene, mods) => bulkCard(scene).dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,...mods}));
        const selectedKeys = () => [...root.querySelectorAll('.h3cm-bulk-selected')].map(card=>card.dataset.bulkKey);
        const previewed = root.querySelector('.h3cm-revision-selected').dataset.bulkKey;
        modifiedClick(2,{ctrlKey:true}); modifiedClick(4,{ctrlKey:true});
        check(selectedKeys().length === 2, 'Ctrl-click adds two takes');
        modifiedClick(2,{ctrlKey:true});
        check(selectedKeys().length === 1 && selectedKeys()[0].startsWith('4:'), 'Ctrl-click toggles a take off');
        modifiedClick(3,{metaKey:true});
        check(selectedKeys().length === 2, 'Cmd-click also adds a take');
        modifiedClick(5,{shiftKey:true});
        check(selectedKeys().map(key=>key.split(':')[0]).join(',') === '3,4,5', 'Shift-click replaces selection with anchor range');
        modifiedClick(7,{ctrlKey:true,shiftKey:true});
        check(selectedKeys().length === 5, 'Ctrl-Shift-click adds a range');
        check(node.widgets[0].value === output && root.querySelector('.h3cm-revision-selected').dataset.bulkKey === previewed,
            'Modified clicks change neither output pin nor preview cursor');
        bulkCard(2).click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(selectedKeys().length === 0, 'Normal click clears bulk selection and previews normally');
        modifiedClick(4,{shiftKey:true});
        check(selectedKeys().map(key=>key.split(':')[0]).join(',') === '2,3,4', 'Normal preview click establishes the range anchor');
        const batchFooter = [...root.querySelectorAll('button')].find(item=>item.textContent === 'Delete selected (3)…');
        check(batchFooter && !batchFooter.disabled, 'Footer delete operates on the batch, not a disabled single take');
        batchFooter.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(bulkRequests.length === 1 && bulkRequests[0].revisions.map(item=>item.scene).join(',') === '2,3,4',
            'One bulk preview contains precisely the explicit selection');
        check(root.querySelector('.h3cm-bulk-preview').textContent.includes('Confirm bulk deletion'), 'Allowed preview requires explicit confirmation');
        const deletionPanel = root.querySelector('.h3cm-delete');
        const deletionActions = deletionPanel.querySelector('.h3cm-delete-actions');
        check(deletionActions.nextElementSibling === root.querySelector('.h3cm-bulk-preview')
            && deletionPanel.contains(root.querySelector('.h3cm-bulk-tools')),
            'Selection, delete and confirmation controls stay together below the graph');
        check(!root.querySelector('.h3cm-bulk-delete'), 'No duplicate batch-delete button above the graph');
        check(deletionPanel.querySelector(':scope > .h3cm-delete-title').hidden
            && deletionPanel.querySelector(':scope > .h3cm-delete-details').hidden,
            'Batch preview replaces stale single-take warnings and inventory');
        modifiedClick(4,{ctrlKey:true});
        check(root.querySelector('.h3cm-bulk-preview').hidden, 'Selection change invalidates the prior confirmation');
        bulkAllowed = false;
        deletionActions.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(!root.querySelector('.h3cm-bulk-preview button') && root.querySelector('.h3cm-bulk-preview').textContent.includes('unselected scene'),
            'Protected selection displays the reason without a confirm button');
        root.querySelector('.h3cm-branches').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
        check(selectedKeys().length === 0, 'Escape clears selection');
        releaseCutPreview = true; bulkAllowed = true;
        root.querySelector(`[data-bulk-key="1:${alternate.revision}"]`).click();
        await new Promise(resolve=>setTimeout(resolve,20));
        const releaseAlt = [...root.querySelectorAll('button')].find(item=>item.textContent === 'Remove from cut and delete…');
        check(releaseAlt && !releaseAlt.disabled, 'Selected final-cut ALT offers a combined release/delete preview');
        releaseAlt.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(bulkRequests.at(-1).revisions.length === 1 && bulkRequests.at(-1).revisions[0].revision === alternate.revision,
            'Single ALT release uses the exact same batch transaction with one target');
        check(root.querySelector('.h3cm-bulk-preview').textContent.includes('Removes final-cut ALT selections in Original'),
            'Preview identifies which branch cut will change');
        check(releaseAlt.textContent === 'Remove from cut and delete…',
            'Single ALT action keeps its label after becoming a one-item batch');
        check(deletionPanel.contains(root.querySelector('.h3cm-bulk-preview'))
            && !deletionPanel.classList.contains('h3cm-delete-blocked'),
            'Allowed ALT confirmation is in the clicked panel without the stale blocked state');
        const confirmationGap = root.querySelector('.h3cm-bulk-preview button').getBoundingClientRect().top
            - releaseAlt.getBoundingClientRect().bottom;
        check(confirmationGap >= 0 && confirmationGap < 180,
            'ALT confirmation appears directly below the clicked button, not across the graph');
        root.querySelector('.h3cm-bulk-cancel').click();
        check(root.querySelector('.h3cm-bulk-preview').hidden && bulkDeletes === 0 && selectedKeys().length === 1,
            'Cancel closes the inline preview without deleting or losing the selection');
        releaseAlt.click(); await new Promise(resolve=>setTimeout(resolve,20));
        let releaseConfirmation = '';
        window.confirm = message => { releaseConfirmation = message; return false; };
        root.querySelector('.h3cm-bulk-preview button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(releaseConfirmation.includes('Final-cut ALT selections in Original') && bulkDeletes === 0,
            'Final-cut changes require explicit confirmation; cancelling changes nothing');
        window.confirm = () => true;
        releaseCutPreview = false;
        bulkCard(2).click(); await new Promise(resolve=>setTimeout(resolve,20));
        await setZoom(50);
        document.getElementById('host').style.transform='scale(0.7)';
        document.getElementById('host').style.transformOrigin='top left';
        const boxViewport = root.querySelector('.h3cm-fork-scroll'); boxViewport.scrollLeft = 0;
        const r2 = bulkCard(2).getBoundingClientRect(), r3 = bulkCard(3).getBoundingClientRect();
        const point = {pointerId:44,button:0,buttons:1,shiftKey:true,bubbles:true,cancelable:true};
        const boxGraph = root.querySelector('.h3cm-fork-graph');
        boxGraph.dispatchEvent(new PointerEvent('pointerdown',{...point,clientX:r2.left-2,clientY:r2.top-2}));
        window.dispatchEvent(new PointerEvent('pointermove',{...point,clientX:r3.right+2,clientY:Math.max(r2.bottom,r3.bottom)+2}));
        window.dispatchEvent(new PointerEvent('pointerup',{...point,buttons:0,clientX:r3.right+2,clientY:Math.max(r2.bottom,r3.bottom)+2}));
        check(selectedKeys().map(key=>key.split(':')[0]).join(',') === '2,3', 'Shift rectangle selects exact cards under graph and canvas zoom');
        check(!document.querySelector('.h3cm-selection-box'), 'Rectangle overlay is removed on pointerup');
        check(node.widgets[0].value === output, 'Rectangle does not change the output pin');
        await new Promise(resolve=>setTimeout(resolve,10));
        root.querySelector('.h3cm-bulk-tools button').click();
        document.getElementById('host').style.transform='';
        await setZoom(100);
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
        check(storageRequests === 0, "Normal manager refreshes never scan storage");
        const beforeStorage = JSON.stringify(node.properties);
        [...root.querySelectorAll("button")].find(item=>item.textContent === "Storage").click();
        await new Promise(resolve=>setTimeout(resolve,50));
        const storage = root.querySelector(".h3-storage");
        check(storage && !storage.hidden, "On-demand Storage Inspector opens");
        check(storage.textContent.includes("61 files"), "Storage totals render");
        check(storage.querySelectorAll(".h3-storage-scroll tbody tr").length === 50, "Storage file table is paginated");
        check(!storage.querySelector("img"), "Untrusted filenames render as text, never HTML");
        [...storage.querySelectorAll("button")].find(item=>item.textContent === "Next").click();
        check(storage.querySelectorAll(".h3-storage-scroll tbody tr").length === 11, "Storage next page works");
        const filter = storage.querySelector("input"); filter.value = "clip_60";filter.dispatchEvent(new Event("input"));
        check(storage.querySelectorAll(".h3-storage-scroll tbody tr").length === 1, "Storage filter resets pagination");
        check(JSON.stringify(node.properties) === beforeStorage && node.widgets[0].value === output,
            "Storage inspection leaves Plan/output/preview selections unchanged");
        for (const width of [900,1500]) {
            document.getElementById("host").style.width=width+"px";
            check(root.scrollWidth<=root.clientWidth+1,`Storage Inspector has no root overflow at ${width}px`);
        }
        [...storage.querySelectorAll("button")].find(item=>item.textContent === "Close").click();
        check(storage.hidden, "Storage Inspector closes without changing the graph");
        for (const width of [900,1500]) {
            document.getElementById("host").style.width=width+"px";
            check(root.scrollWidth<=root.clientWidth+1,`Final-cut control has no root overflow at ${width}px`);
        }
        // A long second chapter must not jump to scene 8 whenever a take is clicked.
        const long = Array.from({length:21}, (_,i)=>({scene:i+8,scene_id:`scene_${i+8}`,
            revision:(i+100).toString(16).padStart(32,"0"), ready:true,active:false,
            compatibility:{width:960,height:544},created_at:"2026-09-12T10:00:00Z",
            ...(i ? {parent:{scene:i+7,revision:(i+99).toString(16).padStart(32,"0")}} : {})}));
        const lateAlt = {...long.at(-1),revision:"f".repeat(32),take_kind:"editorial_alternate",
            alternate_of_revision:long.at(-1).revision};
        long.at(-1).alternates = [lateAlt];
        payload.revisions.push(...long,lateAlt);
        payload.scenes.push(...long.map(item=>({scene:item.scene,scene_id:item.scene_id,revision_count:1})));
        payload.branches.push({active:false,path:long});
        payload.editorial = {chapters:[{id:"one",title:"Chapter 1",start_scene:1},
            {id:"two",title:"Chapter 2",start_scene:8}]};
        node._h3CheckpointManagerRefresh(); await new Promise(resolve=>setTimeout(resolve,100));
        const viewports = () => [...root.querySelectorAll(".h3cm-fork-scroll")];
        const chapter = title => [...root.querySelectorAll(".h3cm-chapter-tab")]
            .find(item=>item.textContent===title).click();
        const toggleChapterOne = () => [...root.querySelectorAll(".h3cm-branch-chapter-title")]
            .find(item=>item.textContent.includes("Chapter 1")).click();
        viewports()[0].scrollLeft = 120;
        viewports()[1].scrollLeft = viewports()[1].scrollWidth;
        const chapterOneScroll = viewports()[0].scrollLeft;
        const chapterTwoScroll = viewports()[1].scrollLeft;
        check(chapterTwoScroll > 3000, "Chapter 2 fixture needs substantial horizontal scrolling");
        const sceneStrip = root.querySelector(".h3cm-scenes");
        sceneStrip.scrollLeft = sceneStrip.scrollWidth;
        const sceneStripScroll = sceneStrip.scrollLeft;
        check(sceneStripScroll > 0, "Long chapter also overflows the scene selector strip");
        const lateCard = () => root.querySelector('.h3cm-fork-node[data-graph-key="28:'
            + long.at(-1).revision + '"] .h3cm-revision');
        lateCard().click(); await new Promise(resolve=>setTimeout(resolve,40));
        check(viewports()[0].scrollLeft === chapterOneScroll && viewports()[1].scrollLeft === chapterTwoScroll,
            "Clicking a late Chapter 2 clip preserves both chapter viewports independently");
        check(lateCard().classList.contains("h3cm-revision-selected"), "Scrolled-to clip is still selected normally");
        sceneStrip.querySelector(".h3cm-scene:last-child").click();
        check(sceneStrip.scrollLeft === sceneStripScroll && viewports()[1].scrollLeft === chapterTwoScroll,
            "Selecting from the scrolled scene strip preserves both the strip and graph positions");
        root.querySelectorAll(".h3cm-alternate")[1].click();
        check(viewports()[1].scrollLeft === chapterTwoScroll, "Selecting a late ALT preserves the graph position");
        node._h3CheckpointManagerRefresh(); await new Promise(resolve=>setTimeout(resolve,100));
        check(viewports()[1].scrollLeft === chapterTwoScroll, "Checkpoint refresh preserves the scrolled chapter");
        toggleChapterOne();
        check(viewports().length === 1 && viewports()[0].scrollLeft === chapterTwoScroll,
            "Collapsing Chapter 1 does not transfer its scroll position to Chapter 2");
        toggleChapterOne();
        check(viewports()[0].scrollLeft === chapterOneScroll && viewports()[1].scrollLeft === chapterTwoScroll,
            "Expanding a chapter restores its own position without moving the other chapter");
        chapter("Chapter 2");
        check(viewports().length === 1 && viewports()[0].scrollLeft === Math.min(
            chapterTwoScroll, viewports()[0].scrollWidth - viewports()[0].clientWidth),
            "Chapter 2 tab retains its position, clamped to the wider viewport without chapter borders");
        chapter("Chapter 1");
        check(viewports()[0].scrollLeft === chapterOneScroll, "Chapter tabs retain separate scroll positions");
        chapter("Chapter 2");
        await setZoom(125);
        viewports()[0].scrollLeft = viewports()[0].scrollWidth;
        const zoomedScroll = viewports()[0].scrollLeft;
        lateCard().click();
        check(viewports()[0].scrollLeft === zoomedScroll, "Clip selection preserves scroll with graph zoom applied");
        check(node.widgets[0].value === output, "Scrolling, browsing chapters and previewing takes never change output selection");
        const toggleLast = () => lateCard().dispatchEvent(new MouseEvent('click',{ctrlKey:true,bubbles:true}));
        toggleLast();
        check(selectedKeys().length === 1, 'Late chapter node supports bulk selection');
        chapter('Chapter 1');
        check(selectedKeys().length === 0, 'Changing chapter clears the old selection');
        chapter('Chapter 2');
        bulkAllowed = true;
        toggleLast();
        deletionActions.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        window.confirm = () => false;
        root.querySelector('.h3cm-bulk-preview button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(bulkDeletes === 0, 'Cancelled bulk confirmation sends no delete');
        window.confirm = () => true;
        bulkDeleteError = true;
        root.querySelector('.h3cm-bulk-preview button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(bulkDeletes === 1 && !root.querySelector('.h3cm-bulk-preview').hidden
            && !root.querySelector('.h3cm-bulk-preview button')
            && root.querySelector('.h3cm-bulk-preview').textContent.includes('Preview changed'),
            'Server conflict keeps its error visible and discards the stale confirmation');
        check(selectedKeys().length === 1, 'A rejected deletion keeps the selection available for another preview');
        bulkDeleteError = false;
        deletionActions.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        root.querySelector('.h3cm-bulk-preview button').click(); await new Promise(resolve=>setTimeout(resolve,50));
        check(bulkDeletes === 2 && selectedKeys().length === 0, 'Successful bulk deletion clears selection and refreshes');
        check(node.widgets[0].value === output, 'Bulk deletion never rewrites a pinned output');
        const branchCleanup = [...root.querySelectorAll('button')].find(item=>item.textContent === 'Delete branch clips…');
        const branchPanel = root.querySelector('.h3cm-branch-cleanup');
        check(branchCleanup && !branchCleanup.disabled && branchPanel.hidden,
            'Original clips can be cleared while the Plan remains on its other branch');
        branchCleanup.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(branchCleanupRequests.length === 1 && branchCleanupDeletes === 0,
            'Branch cleanup starts with a read-only preview');
        check(!branchPanel.hidden && !branchPanel.querySelector('details').open
            && branchPanel.getBoundingClientRect().height < 180,
            'Branch cleanup confirmation stays visible above a collapsed large inventory');
        const originalPlanBranch = studio.widgets.find(item=>item.name === 'working_branch_id');
        const staleBranchConfirm = branchPanel.querySelector('button');
        originalPlanBranch.value = 'main';
        staleBranchConfirm.click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(branchCleanupDeletes === 0, 'Changing the Plan branch invalidates the pending deletion');
        originalPlanBranch.value = named;
        branchCleanup.click(); await new Promise(resolve=>setTimeout(resolve,20));
        window.confirm = () => false;
        branchPanel.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(branchCleanupDeletes === 0, 'Cancelling branch deletion sends no mutation');
        window.confirm = () => true;
        branchCleanupError = 'Branch changed; preview again.';
        branchPanel.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,20));
        check(branchCleanupDeletes === 1 && branchPanel.textContent.includes(branchCleanupError)
            && !branchPanel.querySelector('button'), 'Branch conflict stays visible and discards the confirmation');
        branchCleanupError = '';
        branchCleanup.click(); await new Promise(resolve=>setTimeout(resolve,20));
        branchPanel.querySelector('button').click(); await new Promise(resolve=>setTimeout(resolve,50));
        check(branchCleanupDeletes === 2 && branchPanel.hidden, 'Confirmed branch cleanup refreshes and closes its preview');
        check(originalPlanBranch.value === named, 'Branch deletion never switches Plan Studio');
        const branchSelect = root.querySelector('[aria-label="Working branch whose assignments are shown"]');
        const emptyButton = [...root.querySelectorAll('button')].find(item => item.textContent === 'Hide empty Original…');
        const showOriginal = [...root.querySelectorAll('button')].find(item => item.textContent === 'Show Original');
        check(emptyButton && !emptyButton.disabled && showOriginal.hidden,
            'Empty branch action is beside the selector; Show Original is initially hidden');
        check(emptyButton.parentElement === branchSelect.parentElement && emptyButton.parentElement === branchCleanup.parentElement,
            'All branch cleanup actions stay together beside the branch selector');
        emptyButton.click(); await new Promise(resolve=>setTimeout(resolve,30));
        check(emptyActions.length === 1 && root.querySelector('.h3cm-status').textContent.includes('Saved clips remain'),
            'Nonempty branch displays its blocker without sending a removal');
        emptyBlocked = false; window.confirm = () => false;
        emptyButton.click(); await new Promise(resolve=>setTimeout(resolve,30));
        check(emptyActions.every(item=>item.action === 'empty-preview'), 'Cancelled empty-branch confirmation changes nothing');
        window.confirm = () => true;
        emptyButton.click(); await new Promise(resolve=>setTimeout(resolve,80));
        check(![...branchSelect.options].some(item=>item.value === 'main') && !showOriginal.hidden,
            'Hiding Original removes its dropdown entry and reveals Show Original');
        check(branchSelect.value === named && originalPlanBranch.value === named && emptyButton.disabled,
            'Manager returns to the surviving branch without switching Plan Studio or allowing its removal');
        showOriginal.click(); await new Promise(resolve=>setTimeout(resolve,80));
        check([...branchSelect.options].some(item=>item.value === 'main') && showOriginal.hidden && branchList.default_branch === named,
            'Showing Original restores its dropdown entry without changing the default');
        const emptyId = 'd'.repeat(32);
        branchList.branches.push({id:emptyId,name:'Empty test'});
        node._h3CheckpointManagerRefresh(); await new Promise(resolve=>setTimeout(resolve,80));
        branchSelect.value = emptyId; branchSelect.dispatchEvent(new Event('change'));
        await new Promise(resolve=>setTimeout(resolve,80));
        check(emptyButton.textContent === 'Delete empty branch…' && !emptyButton.disabled,
            'Named empty branch has its own delete-entry action');
        emptyButton.click(); await new Promise(resolve=>setTimeout(resolve,80));
        check(emptyActions.at(-1).action === 'delete-empty' && branchSelect.value === named
            && ![...branchSelect.options].some(item=>item.value === emptyId),
            'Deleting a named empty branch removes its entry and returns to the surviving branch');
        const host = document.getElementById("host"); host.style.width="1850px";host.style.height="1040px";
        root.querySelector(".h3cm-main").style.gridTemplateColumns="minmax(0,1fr)";
        root.querySelector(".h3cm-detail").style.display="none";
        await new Promise(resolve=>setTimeout(resolve,100));
        await setZoom(65);
        node.onRemoved?.();
        if (keepStorageOpen) {
            [...root.querySelectorAll("button")].find(item=>item.textContent === "Storage").click();
            await new Promise(resolve=>setTimeout(resolve,50));
            root.scrollTop = 0;
        }
    } catch (error) {report.failures.push(error.stack || String(error));}
    document.body.dataset.report = btoa(JSON.stringify(report));
}
