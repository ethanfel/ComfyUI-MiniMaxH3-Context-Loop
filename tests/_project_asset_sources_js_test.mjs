import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

// Execute the actual source picker with isolated DOM and transport.
class Element {
    constructor(tag, name="", text="") { this.tag=tag; this.name=name; this.textContent=text; this.children=[]; this.value=""; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children=items; }
    remove() { this.removed=true; }
    addEventListener() {}
}
const source=readFileSync(new URL("../web/h3_project_asset_manager.js", import.meta.url),"utf8");
const body=source.slice(source.indexOf("    async function browseSource("), source.indexOf('    fileInput.addEventListener("change"'));
const mount=new Function("captureProjectOperation","sourceSelect","el","button","document","jsonRequest",
    "project","promptTag","requireCurrentProjectOperation","importAsset","setStatus",
    body+"\nreturn browseSource;");
function setup(kind, pin) {
    const t={project:"demo",requests:[],imports:[],buttons:[],status:[]};
    const browse=mount(() => ({run:t.project}), {value:kind}, (...args) => new Element(...args),
        (title,click) => { const b={title,click}; t.buttons.push(b); return b; },
        {body:new Element("body")}, async route => {
            t.requests.push(route);
            if (kind==="input") return {items:[{path:"loose.png",kind:"image"}]};
            return {items:[{project:"donor",run_name:"donor",source_pin:pin,assets:[{id:"hero",tag:"hero",kind:"image"}]}]};
        }, () => t.project, item => item.tag, operation => {
            if (operation.run!==t.project) throw Object.assign(new Error("Project changed"),{staleProject:true});
        }, async value => t.imports.push(value), message => t.status.push(message));
    return {t,browse};
}
const pin={format:"h3_storage_runtime_pin_v1",run_name:"donor",branch_id:"main",epoch:1,root:{sha256:"a".repeat(64)}};
for (const kind of ["project","chains"]) {
    const {t,browse}=setup(kind,pin);
    await browse();
    await t.buttons.find(item => item.title==="Import").click();
    assert.deepEqual(t.imports[0].source_pin,pin,"the selected row's exact source pin accompanies import");
    assert.equal(t.imports[0][kind==="project" ? "source_project" : "run_name"],"donor");
    const bound=setup(kind,pin);
    await bound.browse({id:"unassigned-slot"});
    await bound.t.buttons.find(item => item.title==="Bind").click();
    assert.equal(bound.t.imports[0].slot_id,"unassigned-slot");
    assert.deepEqual(bound.t.imports[0].source_pin,pin);
    const stale=setup(kind,pin);
    await stale.browse(); stale.t.project="different";
    await stale.t.buttons.find(item => item.title==="Import").click();
    assert.equal(stale.t.imports.length,0,"a source picker cannot mutate a different destination");
}
const legacy=setup("project",undefined); await legacy.browse();
await legacy.t.buttons.find(item => item.title==="Import").click();
assert.equal(Object.hasOwn(JSON.parse(JSON.stringify(legacy.t.imports[0])),"source_pin"),false);
const input=setup("input",undefined); await input.browse();
await input.t.buttons.find(item => item.title==="Import").click();
assert.deepEqual(input.t.imports,[{source:"input",path:"loose.png",slot_id:""}]);
console.log("Actual source picker: independent project/backup pins, slot binding, legacy compatibility and destination switching pass");
