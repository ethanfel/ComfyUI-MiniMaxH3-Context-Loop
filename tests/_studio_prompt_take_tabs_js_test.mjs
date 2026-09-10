import assert from "node:assert/strict";
import {readFileSync, writeFileSync, mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";
import vm from "node:vm";

const source = readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const helper = source.match(/^    function promptTakeTabs\([^]*?^    }$/m)?.[0];
assert.ok(helper, "Exercise the production tab controller");
assert.equal((source.match(/promptTakeTabs\(original, alternate, String\(row.id\)/g) ?? []).length, 2,
    "Both built-in and delegated prompt editors use the same tabs");
assert.match(source, /original.append\(prompt, tools, tray, history\)/);
assert.match(source, /original.append\(delegated\)/);
assert.match(source, /\.h3studio-prompt-takes > \[role="tabpanel"\]\[hidden\] \{ display:none; \}/);

class Element {
    constructor(tag, className = "", textContent = "") {
        Object.assign(this, {tag, className, textContent, children:[], attrs:{}, listeners:{}});
    }
    append(...children) { this.children.push(...children); }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    click() { this.listeners.click(); }
    focus() { this.focused = true; }
}
const original = new Element("div"), alternate = new Element("section");
const prompt = new Element("textarea"), altPrompt = new Element("textarea"), seed = new Element("input");
prompt.value = "Unsaved original text"; altPrompt.value = "Unsaved alternate text";
seed.value = "18446744073709551615";
original.append(prompt); alternate.append(altPrompt, seed);
const state = {plan:{shots:[{id:"one", prompt:"Saved original", seed:seed.value}]}, editorial:{
    replacements:[{scene_id:"one",base_revision:"base",alternate_revision:"alt"}],
    alternate_draft:{enabled:false,scene_id:"one",base_revision:"base",prompt:altPrompt.value,seed:seed.value},
}};
let dirty = 0;
const node = {properties:{}, graph:{setDirtyCanvas(){dirty++;}}};
const context = vm.createContext({state, node, promptTakeTabsSerial:0,
    PROMPT_TAKE_TAB_PROPERTY:"h3_plan_studio_prompt_take_tab",
    element:(...args)=>new Element(...args),
    button:(label,title,callback)=>{
        const result = new Element("button", "", label);
        result.title = title; result.addEventListener("click", callback); return result;
    },
});
vm.runInContext(helper, context);
const host = context.promptTakeTabs(original, alternate, "one", "base");
const [tabs, originalPanel, altPanel] = host.children;
const [originalTab, altTab] = tabs.children;
assert.equal(originalPanel,original); assert.equal(altPanel,alternate);
assert.equal(tabs.attrs.role,"tablist");
assert.equal(original.hidden,false); assert.equal(alternate.hidden,true);
assert.equal(originalTab.attrs["aria-selected"],"true");
assert.equal(altTab.textContent,"ALT · used in final cut");
assert.equal(altTab.attrs["aria-controls"],alternate.id);
assert.equal(alternate.attrs["aria-labelledby"],altTab.id);
const before = JSON.stringify(state);
altTab.click();
assert.equal(original.hidden,true); assert.equal(alternate.hidden,false);
assert.equal(altTab.attrs["aria-selected"],"true");
assert.equal(node.properties.h3_plan_studio_prompt_take_tab,"alt");
assert.equal(JSON.stringify(state),before,"Opening ALT cannot arm generation or select a different final-cut take");
originalTab.click();
assert.equal(JSON.stringify(state),before,"Opening Original cannot disarm a draft or replace an ALT selection");
assert.equal(prompt.value,"Unsaved original text"); assert.equal(altPrompt.value,"Unsaved alternate text");
assert.equal(seed.value,"18446744073709551615","Tab changes never round or rewrite a seed");
assert.equal(original.children[0],prompt,"Tab switches keep live editor DOM, text and cursor identity");
state.editorial.alternate_draft.enabled = true;
alternate.listeners.change();
assert.equal(altTab.textContent,"ALT · armed","An enabled alternate remains visible even in the Original tab");
assert.equal(alternate.hidden,true);
let prevented = false;
originalTab.listeners.keydown({key:"ArrowRight",preventDefault(){prevented = true;}});
assert.ok(prevented); assert.ok(altTab.focused);
assert.equal(altTab.tabIndex,0); assert.equal(originalTab.tabIndex,-1);
assert.equal(alternate.hidden,false);
assert.equal(state.editorial.alternate_draft.enabled,true);
const recreated = context.promptTakeTabs(new Element("div"),new Element("section"),"one","base");
assert.equal(recreated.children[1].hidden,true,"Rerender/reload restores the view preference from node properties");
assert.notEqual(recreated.children[2].id,alternate.id,"Tab/panel IDs remain unique across renders");
const otherScene = context.promptTakeTabs(new Element("div"),new Element("section"),"two","base");
assert.equal(otherScene.children[0].children[1].textContent,"ALT","Another scene's armed draft is not attributed here");
const otherBase = context.promptTakeTabs(new Element("div"),new Element("section"),"one","new-base");
assert.equal(otherBase.children[0].children[1].textContent,"ALT","A stale alternate for an old base is not marked used/armed");
altTab.listeners.keydown({key:"Home",preventDefault(){}});
assert.equal(original.hidden,false); assert.ok(originalTab.focused);
assert.equal(state.plan.shots[0].prompt,"Saved original");
assert.ok(dirty > 0,"Only the workflow view preference is persisted");
console.log("Plan Studio prompt tabs: visibility, used/armed labels, keyboard access, drafts/seeds, delegated editor and view-only restore pass");

if (process.argv.includes("--browser")) {
    const out = mkdtempSync(join(tmpdir(), "h3-studio-prompt-tabs-"));
    const file = join(out, "fixture.html");
    const inject = source.match(/^function injectStyles\(\)[^]*?^}/m)[0];
    writeFileSync(file, '<!doctype html><meta charset="utf-8"><style>body{background:#202124;margin:12px}</style><body><script>'
        + `(${browserChecks.toString()})(${JSON.stringify(helper)},${JSON.stringify(inject)})</script>`);
    const result = spawnSync(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
        "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
        "--disable-component-update", "--disable-sync", "--host-resolver-rules=MAP * ~NOTFOUND",
        "--user-data-dir=" + join(out,"profile"), "--window-size=1250,850",
        "--screenshot=" + join(out,"prompt-tabs.png"), "--dump-dom", pathToFileURL(file).href,
    ], {encoding:"utf8",timeout:25000,maxBuffer:2*1024*1024});
    assert.equal(result.status,0,result.error?.message || result.stderr);
    const encoded = result.stdout.match(/data-report="([^"]+)"/)?.[1];
    assert.ok(encoded,"Browser tab fixture did not complete");
    const report = JSON.parse(Buffer.from(encoded,"base64").toString());
    console.log(report); console.log("Plan Studio tab screenshot: " + join(out,"prompt-tabs.png"));
    assert.deepEqual(report.failures,[]);
}

function browserChecks(helper, inject) {
    const report = {checks:0,failures:[]};
    const check = (ok,message) => {report.checks++; if (!ok) report.failures.push(message);};
    try {
        new Function(inject + "; injectStyles();")();
        const element = (tag,cls="",text="") => {
            const node = document.createElement(tag);node.className=cls;node.textContent=text;return node;
        };
        const button = (text,title,callback) => {
            const node=element("button","",text);node.title=title;node.addEventListener("click",callback);return node;
        };
        const node = {properties:{},graph:{setDirtyCanvas(){}}};
        const state = {editorial:{alternate_draft:null,replacements:[{scene_id:"one",base_revision:"base",alternate_revision:"alt"}]}};
        const tabs = new Function("node","state","element","button",
            'const PROMPT_TAKE_TAB_PROPERTY="h3_plan_studio_prompt_take_tab"; let promptTakeTabsSerial=0;'
            + helper + ";return promptTakeTabs;")(node,state,element,button);
        const root=element("div","h3studio");root.style.width="1200px";root.style.height="700px";
        const heading=element("h3","h3studio-title","Plan Studio · Scene prompt");
        const original=element("div","h3studio-original-prompt-panel");
        const originalText=element("textarea","h3studio-prompt");originalText.value="Original generation prompt. Unsaved edits stay here.";
        original.append(originalText,element("div","h3studio-history","Original prompt history"));
        const alternate=element("section","h3studio-alternate");
        const altText=element("textarea","h3studio-prompt h3studio-alternate-prompt");altText.value="Alternate picture-only prompt. Unsaved edits stay here.";
        const seed=element("input");seed.value="18446744073709551615";
        alternate.append(element("strong","","Alternate final-cut take"),
            element("p","h3studio-hint","Original checkpoint and audio remain unchanged."),altText,seed);
        const panel=element("div","h3studio-panel");const host=tabs(original,alternate,"one","base");
        panel.append(host);root.append(heading,panel);document.body.append(root);
        const [originalTab,altTab]=host.querySelectorAll('[role="tab"]');
        const before=JSON.stringify(state);
        for (const width of [820,1200]) {
            root.style.width=width+"px";
            originalTab.click();
            check(originalText.getClientRects().length===1 && altText.getClientRects().length===0,"Only Original editor visible at "+width);
            altTab.click();
            check(altText.getClientRects().length===1 && originalText.getClientRects().length===0,"Only ALT editor visible at "+width);
            check(!original.querySelector(".h3studio-history").getClientRects().length,"Original history hides with its prompt");
            check(root.scrollWidth<=root.clientWidth+1,"No horizontal overflow at "+width);
        }
        check(seed.value==="18446744073709551615","Exact seed survives real DOM tab switches");
        check(originalText.value.includes("Unsaved") && altText.value.includes("Unsaved"),"Both prompt drafts survive tab switches");
        check(JSON.stringify(state)===before,"Tab switches leave editorial selection and draft arming unchanged");
        check(altTab.textContent==="ALT · used in final cut","ALT final-cut status is visible on tab");
    } catch(error) {report.failures.push(error.stack || String(error));}
    document.body.dataset.report=btoa(JSON.stringify(report));
}
