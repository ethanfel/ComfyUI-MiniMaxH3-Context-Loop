import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {
    calculatePlanTiming, parsePlanJson, planToJson, planDefaultSteps,
    setPlanDefaultSteps, clearSceneStepOverrides,
} from "../web/h3_chain_plan_core.mjs";

const editor = fs.readFileSync(new URL("../web/h3_chain_plan_editor.js", import.meta.url), "utf8");
const studio = fs.readFileSync(new URL("../web/h3_chain_plan_studio.js", import.meta.url), "utf8");
const extract = (source, pattern) => {
    const match = source.match(pattern);
    assert.ok(match, String(pattern));
    return match[0];
};
const document = {
    defaults:{steps:20},
    shots:[
        {id:"one", prompt:"Opening", length:73, seed:"18446744073709551615", steps:12},
        {id:"two", prompt:"Continue", length:73, seed:19},
    ],
};
const plan = parsePlanJson(JSON.stringify(document));
const before = planToJson(plan);
assert.equal(planDefaultSteps(plan, 8), 20, "Show the backend's JSON default, not a misleading widget placeholder");
assert.equal(planDefaultSteps({steps:14}, 8), 14, "Legacy shorthand has the same precedence");
assert.equal(planDefaultSteps({}, 8), 8);
assert.equal(planToJson(plan), before, "Displaying/restoring defaults never rewrites a saved Plan");
assert.deepEqual(calculatePlanTiming(plan, {defaultSteps:8}).shots.map(s => s.steps), [12,20]);
setPlanDefaultSteps(plan, 8);
assert.deepEqual(calculatePlanTiming(plan, {defaultSteps:20}).shots.map(s => s.steps), [12,8]);
clearSceneStepOverrides(plan);
assert.deepEqual(calculatePlanTiming(plan, {defaultSteps:20}).shots.map(s => s.steps), [8,8]);
setPlanDefaultSteps(plan, 6);
assert.deepEqual(calculatePlanTiming(parsePlanJson(planToJson(plan))).shots.map(s => s.steps), [6,6]);
assert.equal(plan.shots[0].seed, document.shots[0].seed);
assert.deepEqual(plan.shots[0].prompt, parsePlanJson(before).shots[0].prompt);
for (const invalid of [0, 10001, 1.5, "bad"]) assert.throws(() => setPlanDefaultSteps(plan, invalid));

// Exercise Production Plan's actual default-control edit, including the case
// where the native widget already says 8 but JSON still says 20 (#64).
const events = {};
const control = {value:"", validity:{valid:true}, addEventListener:(name, fn) => { events[name] = fn; }};
const state = {plan:structuredClone(document)};
const node = {widgets:[{name:"default_steps",value:8}]};
let writes = 0;
const context = vm.createContext({state, node, planDefaultSteps, setPlanDefaultSteps,
    widgetValue:() => node.widgets[0].value,
    numberInput:value => { control.value = String(value); return control; },
    field:(_label,value) => value, syncPlan:() => { writes++; }, updateTiming:() => {},
    setWidgetValue:(_node,_name,value) => { node.widgets[0].value = value; },
});
vm.runInContext(extract(editor, /^        function numberSetting\([^]*?^        }/m), context);
context.numberSetting("default_steps", "Default steps", {fallback:20,min:1,max:10000,step:1});
assert.equal(control.value, "20");
control.value = "8"; events.change();
assert.equal(state.plan.defaults.steps, 8); assert.equal(writes, 1);
assert.equal(state.plan.shots[0].steps, 12, "No silent overwrite of explicit scene steps");

// Exercise Studio's actual handler and connected-owner mirror.
const owner = {widgets:[{name:"default_steps",value:20}]};
state.plan = structuredClone(document); state.planNode = owner;
Object.assign(context, {widget:(target,name) => target.widgets.find(w => w.name === name),
    writePlan:() => { writes++; }, runName:() => "test", settingsSignature:() => "test",
    dirty:() => {}, renderShell:() => {},
});
vm.runInContext(extract(studio, /^    function writePlanSetting\([^]*?^    }/m), context);
context.writePlanSetting("default_steps", 6);
assert.equal(state.plan.defaults.steps, 6);
assert.equal(owner.widgets[0].value, 6); assert.equal(node.widgets[0].value, 6);
assert.equal(state.plan.shots[0].steps, 12);

// Connected fingerprint sockets must survive the real widget-collapse path.
const widgets = vm.createContext({MODERN_NODE_NAME:"MiniMaxH3ChainPlanModern",
    MODERN_BACKING_WIDGETS:["generation_fingerprint", "default_steps"],
});
vm.runInContext(extract(editor, /^function collapseWidget\([^]*?^}/m) + "\n" +
    extract(editor, /^function collapseModernBackingWidgets\([^]*?^}/m), widgets);
const fingerprint = {name:"generation_fingerprint",type:"converted-widget",hidden:false,
    value:"saved-fingerprint",computeSize:() => [0,-4],draw:() => {}};
const ordinary = {name:"default_steps",type:"number",value:20};
const modern = {type:"MiniMaxH3ChainPlanModern",widgets:[fingerprint,ordinary],
    inputs:[{name:"generation_fingerprint",link:42,widget:{name:"generation_fingerprint"}}]};
const original = {...fingerprint};
widgets.collapseModernBackingWidgets(modern);
assert.equal(fingerprint.hidden, false);
assert.equal(fingerprint.type, original.type);
assert.equal(fingerprint.computeSize, original.computeSize);
assert.equal(fingerprint.draw, original.draw);
assert.equal(fingerprint.value, "saved-fingerprint");
assert.equal(ordinary.hidden, true);
// Converted-but-disconnected sockets are still usable when reconnecting.
modern.inputs[0].link = null;
widgets.collapseModernBackingWidgets(modern);
assert.equal(fingerprint.hidden, false);
console.log("Steps defaults/overrides, real Plan/Studio handlers, saved seeds, and fingerprint socket regressions pass");
