// Real Carousel, Modern Plan, Studio and prompt editors; isolated from user data.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import {spawn} from "node:child_process";

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "h3-project-plan-switch-"));
const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.end(`<!doctype html><style>.host{width:920px;height:860px}</style>
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
        "--headless", "--disable-gpu", "--no-first-run", "--disable-extensions",
        "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--user-data-dir=" + path.join(temporary, "profile"), "--virtual-time-budget=22000",
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
    assert.ok(encoded, "Browser did not finish: " + stdout.slice(-1500) + stderr.slice(-500));
    const report = JSON.parse(Buffer.from(encoded, "base64").toString());
    console.log(report);
    assert.deepEqual(report.failures, []);
} finally { chrome?.kill(); server.close(); }

async function browserChecks() {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) throw Error(message); };
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const waitFor = async predicate => {
        for(let i=0;i<1000;i++){if(predicate())return;await wait(10);}
        throw Error("Timed out: "+document.body.innerText.slice(-1600));
    };
    window.addEventListener("error", event => report.failures.push(event.message));
    window.addEventListener("unhandledrejection", event => report.failures.push(String(event.reason?.stack || event.reason)));
    const extensions=[], records=new Map(), requests=[];
    const graph={_nodes:[],links:{},setDirtyCanvas(){},
        getNodeById(id){return this._nodes.find(node=>node.id===id);}};
    window.app={graph,configuringGraph:false,registerExtension(value){extensions.push(value);}};
    const widget=(node,name)=>node.widgets.find(item=>item.name===name);
    const planText=prompt=>JSON.stringify({shared_prompt:["shared "+prompt],shots:[
        {id:"one",prompt:[prompt],seed:"18446744073709551615",basic_prompt:"draft "+prompt}
    ]});
    let revision=0, failRead=false, locking=false, policyEpoch=0;
    const owners=new Map();
    window.api=Object.assign(new EventTarget(),{
        apiURL:value=>value,
        async fetchApi(route,options){
            const url=new URL(route,location.origin);
            const body=options?.body?JSON.parse(options.body):Object.fromEntries(url.searchParams);
            requests.push({path:url.pathname,...body});
            const run=body.run_name||body.project;
            let data={};
            if(url.pathname.endsWith("/project-ownership")) {
                if(locking && body.action==="claim" && !owners.has(run)) owners.set(run,body.owner_id);
                if(locking && body.action==="release" && owners.get(run)===body.owner_id) owners.delete(run);
                if(body.action==="force") throw Error("Project switch must not force ownership");
                data={run_name:run,locking_enabled:locking,policy_epoch:policyEpoch,
                    owned_by_requester:locking && owners.get(run)===body.owner_id,
                    available:!owners.has(run),owner_label:"another workflow",epoch:1};
            }
            else if(url.pathname.endsWith("/projects")) data={items:[{project:"alpha"},{project:"beta"},{project:"offline"}]};
            else if(url.pathname.endsWith("/project-assets")) {
                if(failRead && run==="offline") return {ok:false,status:503,json:async()=>({error:"offline test"})};
                data={project:run,revision:"catalog",folders:[],assets:[],reference_slots:[]};
            }
            else if(url.pathname.endsWith("/working-branches")){
                if(failRead && run==="offline") return {ok:false,status:503,json:async()=>({error:"offline test"})};
                const record=records.get(run)||{id:"main",run_name:run,name:"Original",revision:"",authoring:null};
                if(body.action==="list") data={default_branch:"main",branches:[{id:"main",name:"Original",revision:record.revision}]};
                else if(body.action==="load") data=record;
                else if(body.action==="save"){
                    if(locking && options?.headers?.["X-H3-Workflow-Owner"]!==owners.get(run))
                        return {ok:false,status:423,json:async()=>({error:"Missing project ownership proof"})};
                    if(body.revision!==record.revision) return {ok:false,status:409,json:async()=>({error:"newer saved branch"})};
                    data={...record,revision:String(++revision),authoring:body.authoring};records.set(run,structuredClone(data));
                } else throw Error("Unexpected branch mutation "+body.action);
            }
            else if(url.pathname.endsWith("/checkpoints")) data={run_name:run,working_branch_id:"main",checkpoints:[],editorial:{}};
            else if(url.pathname.endsWith("/prompt-history")) data={revisions:[],draft:null};
            else if(url.pathname.endsWith("/runs")) data={runs:[]};
            else if(options?.method==="POST") throw Error("Unexpected mutation "+route);
            return {ok:true,json:async()=>structuredClone(data)};
        }
    });
    let nextId=0,link=0;
    class BaseNode {
        constructor(type,settings){
            this.id=++nextId;this.type=this.comfyClass=type;this.graph=graph;
            this.inputs=[];this.outputs=[];this.properties={h3_plan_studio_view:"scene"};this.size=[1100,1000];
            this.widgets=Object.entries(settings).map(([name,value])=>({name,value,type:"text",options:{}}));
            graph._nodes.push(this);
        }
        setSize(size){this.size=size;}
        addDOMWidget(name,type,root){
            this.root=root;this.host=document.createElement("div");this.host.className="host";
            this.host.append(root);document.body.append(this.host);
            const item={name,type,element:root,serialize:false,options:{serialize:false}};
            this.widgets.push(item);return item;
        }
    }
    function connect(source,target,name){
        const id=++link;target.inputs.push({name,link:id});
        source.outputs[0]??={links:[]};source.outputs[0].links.push(id);
        graph.links[id]={origin_id:source.id,target_id:target.id,origin_slot:0};
    }
    try{
        await import("/web/h3_chain_plan_editor.js");
        await import("/web/h3_chain_plan_studio.js");
        await import("/web/h3_chain_rich_scene_prompt_editor.js");
        await import("/web/h3_project_asset_manager.js");
        const {captureProjectPlan}=await import("/web/h3_project_plan_switch.mjs?v=0.7.1");
        for(const lockingEnabled of [false,true])for(const useStudio of [false,true]){
            records.clear();requests.length=0;failRead=false;
            owners.clear();locking=lockingEnabled;policyEpoch++;
            const settings={run_name:"alpha",plan_json:planText("alpha"),width:960,height:544,base_seed:1,
                default_duration_seconds:5,default_steps:8,encode_mode:"video",crop:"disabled",
                generation_fingerprint:"",segment_crf:18,video_blend_frames:0};
            class Plan extends BaseNode{constructor(){super("MiniMaxH3ChainPlanModern",settings);}}
            class Studio extends BaseNode{constructor(){super("MiniMaxH3ChainPlanStudio",{...settings,working_branch_id:"main",alternate_take_json:""});}}
            class Carousel extends BaseNode{constructor(){super("MiniMaxH3ProjectAssetManager",
                {run_name:"alpha",catalog_json:"{}",operation_json:"",ownership_json:""});}}
            class Rich extends BaseNode{constructor(){super("MiniMaxH3ChainRichScenePromptEditor",{});}}
            for(const extension of extensions)for(const [Node,name]of [[Plan,"MiniMaxH3ChainPlanModern"],
                [Studio,"MiniMaxH3ChainPlanStudio"],[Carousel,"MiniMaxH3ProjectAssetManager"],
                [Rich,"MiniMaxH3ChainRichScenePromptEditor"]]){
                await extension.beforeRegisterNodeDef?.(Node,{name});
            }
            const carousel=new Carousel(),plan=new Plan(),studio=useStudio?new Studio():null;
            const rich=useStudio?new Rich():null;
            connect(carousel,plan,"project_assets");if(studio)connect(plan,studio,"plan");
            if(rich)connect(studio,rich,"plan");
            records.set("beta",{id:"main",run_name:"beta",revision:"beta",name:"Original",
                authoring:{...captureProjectPlan(plan),width:1280,plan_json:planText("beta")}});
            plan.onNodeCreated();studio?.onNodeCreated();rich?.onNodeCreated();carousel.onNodeCreated();
            const menu=()=>carousel.root?.querySelector('[aria-label="Switch Asset Carousel project"]');
            await waitFor(()=>[...(menu()?.options ?? [])].some(option=>option.value==="beta")
                &&(!studio||studio._h3ProjectPlanSession?.controller.ready));
            widget(plan,"plan_json").value=planText("alpha edited");
            plan._h3ChainEditorRefresh?.();studio?._h3PlanStudioRefresh?.();
            await wait(40);
            const switchTo=run=>{menu().value=run;menu().dispatchEvent(new Event("change"));};
            switchTo("beta");
            await waitFor(()=>widget(carousel,"run_name").value==="beta"
                &&JSON.parse(widget(plan,"plan_json").value).shots[0].prompt[0]==="beta");
            await wait(150);
            check(JSON.parse(records.get("alpha").authoring.plan_json).shots[0].prompt[0]==="alpha edited","source prompts saved");
            check(widget(plan,"width").value===1280,"target dimensions restored");
            check(JSON.parse(widget(plan,"plan_json").value).shots[0].seed==="18446744073709551615","exact scene seed retained");
            check([...plan.root.querySelectorAll("textarea")].some(field=>field.value==="beta"),"Modern Plan shows target prompt");
            if(studio){
                check(studio._h3PlanStudioState.plan.shots[0].prompt[0]==="beta","Studio state follows project switch");
                check(rich._h3RichPromptState.plan.shots[0].prompt[0]==="beta","Rich prompt editor follows project switch");
                check(rich._h3RichPromptState.plan.shots[0].basic_prompt==="draft beta","Basic draft follows project switch");
                check(studio._h3ProjectPlanSession.controller.run==="beta","Studio binding belongs to target");
            }
            switchTo("alpha");
            await waitFor(()=>widget(carousel,"run_name").value==="alpha");
            check(JSON.parse(widget(plan,"plan_json").value).shots[0].prompt[0]==="alpha edited","return restores edited source");
            // Actual queued DOM switches must never apply late data out of order.
            switchTo("beta");switchTo("alpha");
            await waitFor(()=>requests.filter(item=>item.action==="load"&&item.run_name==="alpha").length>=3);
            await wait(200);
            check(widget(plan,"run_name").value==="alpha","rapid switches keep the last selection");
            failRead=true;switchTo("offline");
            await waitFor(()=>carousel.root.textContent.includes("Stayed on alpha"));
            check(widget(plan,"run_name").value==="alpha","failed load keeps source project");
            check(JSON.parse(widget(plan,"plan_json").value).shots[0].prompt[0]==="alpha edited","failed load keeps prompt");
            if(locking){
                failRead=false;owners.set("beta","another-workflow");
                switchTo("beta");
                await waitFor(()=>widget(carousel,"run_name").value==="beta");
                check(carousel.root.textContent.includes("Read-only here"),"owned destination can be inspected read-only");
                widget(plan,"plan_json").value=planText("local read-only edit");
                plan._h3ChainEditorRefresh?.();studio?._h3PlanStudioRefresh?.();
                await wait(50);
                const savedBefore=records.get("beta").revision;
                switchTo("alpha");
                await waitFor(()=>carousel.root.textContent.includes("Stayed on beta"));
                check(widget(plan,"run_name").value==="beta","source write ownership conflict blocks the switch");
                check(JSON.parse(widget(plan,"plan_json").value).shots[0].prompt[0]==="local read-only edit","read-only edits stay local");
                check(records.get("beta").revision===savedBefore,"ownership conflict cannot overwrite saved prompts");
                check(owners.get("beta")==="another-workflow","switch never steals ownership");
            }
            for(const node of [carousel,rich,studio,plan].filter(Boolean)){node.onRemoved?.();node.host?.remove();}
            graph._nodes=[];graph.links={};await wait(30);
        }
    }catch(error){report.failures.push(String(error?.stack||error));report.lastRequests=requests.slice(-15);}
    document.body.dataset.report=btoa(unescape(encodeURIComponent(JSON.stringify(report))));
}
