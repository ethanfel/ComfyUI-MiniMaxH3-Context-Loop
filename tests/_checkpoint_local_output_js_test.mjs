import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import * as core from "../web/h3_checkpoint_manager_core.mjs";
import * as workingBranches from "../web/h3_working_branches.mjs";

const a = "a".repeat(32), b = "b".repeat(32), c = "c".repeat(32), d = "d".repeat(32);
const payload = {
    revisions:[
        {scene:1, revision:a, active:true, ready:true},
        {scene:2, revision:b, active:true, ready:true, parent:{scene:1, revision:a}},
        {scene:2, revision:c, active:false, ready:true, parent:{scene:1, revision:a}},
        {scene:3, revision:d, active:true, ready:true, parent:{scene:2, revision:b}},
    ],
    scenes:[1, 2, 3].map(scene => ({scene})),
    branches:[
        {label:"Active branch", active:true, path:[{scene:1, revision:a}, {scene:2, revision:b}, {scene:3, revision:d}]},
        {label:"Alternate", active:false, path:[{scene:1, revision:a}, {scene:2, revision:c}]},
    ],
    summary:{scene_count:3, revision_count:4, branch_count:2, bytes:0},
};
const local = core.checkpointLocalSelectionJson(payload, "demo", payload.revisions[2], {start:1, end:3});
assert.equal(core.checkpointLocalSelection(local).output_mode, "workflow_local");
assert.equal(core.checkpointOutputSelectionJson(local, payload, "other", payload.revisions[3]), local);
assert.equal(core.checkpointLocalSelection(""), null);
assert.equal(core.checkpointLocalSelection("invalid"), null);
assert.equal(core.checkpointLocalSelection(core.checkpointSelectionJson(payload, "demo", payload.revisions[1])), null);
assert.throws(() => core.checkpointLocalSelectionJson({...payload, revisions:payload.revisions.map(
    item => item.scene === 1 ? {...item, ready:false} : item)}, "demo", payload.revisions[2]), /available/);
assert.throws(() => core.checkpointLocalSelectionJson(payload, "", payload.revisions[2]), /complete/);
const chapterPin = core.checkpointLocalSelectionJson(payload, "demo", payload.revisions[3], {start:3, end:3});
const switchedPrefix = structuredClone(payload);
switchedPrefix.revisions[1].active = false;
switchedPrefix.revisions[2].active = true;
assert.equal(core.checkpointOutputSelectionJson(chapterPin, switchedPrefix, "demo", payload.revisions[3], {start:3, end:3}), chapterPin,
    "previous chapters are pinned too, not rebuilt from new project pointers");
assert.equal(JSON.parse(chapterPin).lineage[1].revision, b);
const chapterOnlyPin = core.checkpointLocalSelectionJson({
    ...payload, revisions:payload.revisions.map(item => item.scene < 3 ? {...item, ready:false} : item),
}, "demo", payload.revisions[3], {start:3, end:3}, "chapter");
assert.equal(JSON.parse(chapterOnlyPin).output_scope, "chapter");
assert.deepEqual(JSON.parse(chapterOnlyPin).lineage, JSON.parse(chapterPin).lineage,
    "prior immutable timing metadata remains pinned, but its media is not required");
assert.equal(core.checkpointOutputSelectionJson(chapterOnlyPin, switchedPrefix, "demo", payload.revisions[2]), chapterOnlyPin);

// The whole extension is mounted with a lightweight DOM, so these exercise
// the real handlers, async refresh and serialized widget (not extracted mocks).
class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {};
        this.style = {setProperty(){}}; this.className = ""; this.textContent = "";
        this.classList = {
            add:(name) => { this.className += ` ${name}`; },
            toggle:(name, enabled) => {
                this.className = this.className.split(" ").filter(item => item !== name).join(" ");
                if (enabled) this.className += ` ${name}`;
            },
        };
    }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    load() {}
    pause() {}
    click() { assert.ok(!this.disabled, `${this.textContent} is disabled`); this.listeners.click(); }
}
const source = fs.readFileSync(new URL("../web/h3_chain_checkpoint_manager.js", import.meta.url), "utf8")
    .replace(/^import\s[\s\S]*?from\s+"[^"]+";\n/gm, "");
let currentGraph = structuredClone(payload), runs = ["demo", "other"], failRequests = false;
let confirms = true, mutations = 0, dirty = 0;
let namedWorkingBranch = "", allowWorkingAssignment = false;
let attachResponse = null;
let processingDeletion = false, deleteConflict = false, delayedProcessingPreview = null;
let retainedPixelTakes = [];
let snapshotRetirement = false, snapshotRetired = false, retirementError = 0, delayedRetirementPreview = null;
const snapshotAddress = `h3_chains/demo/chapters/01_one/manifests/${a}.json`;
const confirmations = [];
let extension;
const requests = [];
const context = vm.createContext({
    ...core, ...workingBranches, URLSearchParams, console,
    document:{head:new Element("head"), getElementById:() => null, createElement:tag => new Element(tag)},
    app:{registerExtension(value){ extension = value; }, graph:{setDirtyCanvas(){}}},
    api:{apiURL:path => path, fetchApi:async (path, options={}) => {
        requests.push({path, options});
        path = path.split("?branch_id=")[0];
        if (failRequests) throw new Error("offline");
        let data;
        if (path.endsWith("/runs")) data = {runs:runs.map(run_name => ({run_name, checkpoint_count:3}))};
        else if (path.includes("/working-branches?")) data = {default_branch:"main", branches:[{id:"main",name:"Original"},
            ...(namedWorkingBranch ? [{id:namedWorkingBranch,name:"New working branch"}] : [])]};
        else if (path.includes("/checkpoints?")) data = structuredClone(currentGraph);
        else if (path.endsWith("/processing-checkpoints/delete-preview") && processingDeletion) {
            const body = JSON.parse(options.body);
            data = {allowed:true, metadata_path:body.metadata_path, snapshot:"preview-token",
                owned_file_count:6, reclaimed_bytes:1024, files:[], not_deleted:["Originals and references"],
                retained_independent_takes:structuredClone(retainedPixelTakes)};
            if (delayedProcessingPreview) {
                const wait = delayedProcessingPreview; delayedProcessingPreview = null;
                await wait;
            }
        }
        else if (path.endsWith("/processing-checkpoints/delete") && processingDeletion) {
            mutations++;
            const body = JSON.parse(options.body);
            assert.equal(body.snapshot, "preview-token");
            assert.ok(currentGraph.processing_variants.some(item => item.key === body.metadata_path));
            if (deleteConflict) return {ok:false, status:409, json:async () => ({error:"Preview changed; refresh", preview:{allowed:false}})};
            currentGraph.processing_variants = currentGraph.processing_variants.filter(item => item.key !== body.metadata_path);
            data = {message:"Deleted processed version; originals unchanged.", reclaimed_bytes:1024};
        }
        else if (path.endsWith("/chapter-snapshots/retire-preview") && snapshotRetirement) {
            assert.equal(JSON.parse(options.body).path, snapshotAddress);
            data = {allowed:true, path:snapshotAddress, snapshot:"retirement-token", chapter_number:1,
                chapter_manifest_id:a, retired_path:snapshotAddress.replace("/manifests/", "/retired_manifests/"),
                scenes:[{scene:2, revision:c, active:false}], message:"No clips are deleted."};
            if (delayedRetirementPreview) {
                const wait = delayedRetirementPreview; delayedRetirementPreview = null;
                await wait;
            }
        }
        else if (path.endsWith("/chapter-snapshots/retire") && snapshotRetirement) {
            mutations++;
            const body = JSON.parse(options.body);
            assert.equal(body.path, snapshotAddress);
            assert.equal(body.snapshot, "retirement-token");
            if (retirementError) return {ok:false, status:retirementError,
                json:async () => ({error:retirementError === 423 ? "Project is read only" : "Retirement preview changed"})};
            snapshotRetired = true;
            data = {message:"Snapshot retired; no clips deleted."};
        }
        else if (path.endsWith("/delete-preview") && snapshotRetirement) {
            data = {allowed:snapshotRetired, blockers:snapshotRetired ? [] : ["Snapshot pins this take"], files:[],
                chapter_references:snapshotRetired ? [] : [{number:1, snapshot:a, path:snapshotAddress}]};
        }
        else if (path.endsWith("/delete-preview")) data = {allowed:false, blockers:["test"]};
        else if (path.endsWith("/attribute") && attachResponse) {
            mutations++;
            const request = JSON.parse(options.body);
            assert.equal(request.parent_revision, b);
            assert.equal(request.candidate_revision, d);
            const alias = {...currentGraph.revisions.find(item => item.revision === d),
                revision:attachResponse.revision, parent:{scene:2, revision:b}};
            currentGraph.revisions.push(alias);
            currentGraph.branches[0].path.push({scene:3, revision:alias.revision});
            delete currentGraph.branches[0].attribution_slot;
            data = attachResponse;
        }
        else if (path.endsWith("/checkpoint-revisions/restore") && allowWorkingAssignment) {
            mutations++;
            const chosen = JSON.parse(options.body).revisions;
            for (const item of currentGraph.revisions) {
                item.active = chosen.some(value => value.scene === item.scene && value.revision === item.revision);
            }
            data = {restored:[], retired_scope_pointers:0};
        }
        else { mutations++; throw new Error(`Unexpected request ${path}`); }
        return {ok:true, json:async () => data};
    }},
    window:{setTimeout:callback => callback(), confirm:message => { confirmations.push(message); return confirms; }},
    projectMutationOptions:(_node, _run, options) => {
        if (attachResponse || processingDeletion || snapshotRetirement || allowWorkingAssignment) return options;
        mutations++; throw new Error("Local output attempted project mutation");
    },
    promptCompanionSync:{},
});
vm.runInContext(source, context);
class NodeType {}
await extension.beforeRegisterNodeDef(NodeType, {name:"MiniMaxH3ChainCheckpointManager"});
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(setImmediate); };
function makeNode(saved="", properties={}) {
    const node = {properties:{h3_checkpoint_manager_run:"demo", ...properties},
        widgets:[{name:"selection_json", value:saved, callback:() => dirty++}],
        inputs:[], graph:{setDirtyCanvas:() => dirty++}, setSize(){},
        addDOMWidget(_name, _type, root) { this.root = root; return {}; },
    };
    context.mount(node);
    return node;
}
function elements(node) {
    const flatten = item => [item, ...item.children.flatMap(flatten)];
    return flatten(node.root);
}
const byText = (node, text) => elements(node).find(item => item.tag === "button" && item.textContent === text);
const byClass = (node, name) => elements(node).find(item => item.className.split(" ").includes(name));
const select = (node, scene, revision) => byText(node, `S${scene} · ${revision.slice(0, 8)}`).click();
const value = node => node.widgets[0].value;

const scoped = makeNode(chapterOnlyPin);
await settle();
assert.equal(byClass(scoped, "h3cm-output-scope").value, "chapter");
select(scoped, 2, c);
await settle();
const scopeControl = byClass(scoped, "h3cm-output-scope");
scopeControl.value = "project"; scopeControl.listeners.change();
assert.equal(JSON.parse(value(scoped)).lineage.at(-1).revision, d,
    "explicit scope changes retain the pinned tip, not the browsed revision");
scopeControl.value = "chapter"; scopeControl.listeners.change();
assert.match(byClass(scoped, "h3cm-output-summary").textContent, /scenes 3–3 \(1 clip; selected chapter only\)/);
assert.equal(mutations, 0);

const first = makeNode(), second = makeNode();
await settle();
assert.equal(JSON.parse(value(first)).lineage.at(-1).revision, d, "legacy default still follows selection");
const wholeActive = value(first);
const wholeActiveSummary = byClass(first, "h3cm-output-summary").textContent;
assert.match(wholeActiveSummary, /Will send to connected nodes: Original checkpoints/);
assert.match(wholeActiveSummary, /demo · original branch through scene 3 \/ dddddddd/);
assert.match(wholeActiveSummary, /scenes 1–3 \(3 clips;/);
assert.match(wholeActiveSummary, /follows branch selection/);
select(first, 2, b); await settle();
assert.equal(value(first), wholeActive, "previewing an earlier clip cannot truncate output");
assert.equal(byClass(first, "h3cm-output-summary").textContent, wholeActiveSummary);
select(first, 1, a); await settle();
assert.equal(value(first), wholeActive, "even a shared ancestor is preview-only");
byText(first, "Use branch locally").click();
assert.equal(JSON.parse(value(first)).lineage.at(-1).revision, d,
    "Use branch locally chooses the clicked row's full branch, not the previewed ancestor");
byText(first, "Follow branch selection").click();
select(first, 2, c);
await settle();
assert.equal(value(first), wholeActive, "previewing a different branch alone does not change output");
assert.equal(byClass(first, "h3cm-output-summary").textContent, wholeActiveSummary);
byText(first, "Use branch locally").click();
const pin = value(first), otherBefore = value(second);
assert.equal(pin, local);
assert.ok(elements(first).some(item => item.textContent === "local output"));
select(first, 3, d);
await settle();
assert.equal(value(first), pin, "browsing does not move pinned output");
assert.match(byClass(first, "h3cm-output-summary").textContent, /through scene 2 \/ cccccccc/);
assert.match(byClass(first, "h3cm-output-summary").textContent, /pinned to this workflow/);
assert.equal(value(second), otherBefore, "other manager unaffected");
assert.equal(mutations, 0);
assert.ok(dirty > 0, "widget changes are serialized and mark the workflow dirty");
const connectedPlan = {type:"MiniMaxH3ChainPlan", widgets:[
    {name:"run_name", value:"other"}, {name:"plan_json", value:"unchanged Plan"},
]};
first.inputs = [{link:1}];
first.graph.links = {1:{origin_id:99}};
first.graph.getNodeById = () => connectedPlan;
first.onConnectionsChange();
await settle();
assert.equal(value(first), pin, "connecting a Plan from another Run must not move the local output");
assert.equal(connectedPlan.widgets[1].value, "unchanged Plan");

currentGraph.revisions[1].active = false;
currentGraph.revisions[2].active = true;
first._h3CheckpointManagerRefresh();
await settle();
assert.equal(value(first), pin, "project-wide active changes do not move local pin");
const reopened = makeNode(pin, {h3_checkpoint_manager_scene:3, h3_checkpoint_manager_revision:d});
await settle();
assert.equal(value(reopened), pin, "shorter pinned branch survives startup's deepest-tip preference");
assert.match(byClass(reopened, "h3cm-output-summary").textContent, /through scene 2 \/ cccccccc/);
// ComfyUI reconfiguration replaces the saved widget value; it remains the
// sole pin authority even when an old browse selection is still in memory.
reopened.widgets[0].value = core.checkpointLocalSelectionJson(payload, "demo", payload.revisions[0], {start:1, end:3});
const newPin = value(reopened);
reopened._h3CheckpointManagerRefresh();
await settle();
assert.equal(value(reopened), newPin);

currentGraph.revisions = currentGraph.revisions.filter(item => item.revision !== c);
first._h3CheckpointManagerRefresh();
await settle();
assert.equal(value(first), pin);
assert.match(byClass(first, "h3cm-output-summary").textContent, /unavailable.*no fallback/);
runs = ["other"];
first._h3CheckpointManagerRefresh();
await settle();
assert.equal(value(first), pin);
assert.equal(byClass(first, "h3cm-run-select").value, "demo", "missing pinned run is not replaced by another run");
failRequests = true;
first._h3CheckpointManagerRefresh();
await settle();
assert.equal(value(first), pin, "transient network errors retain pin");
failRequests = false; runs = ["demo", "other"]; currentGraph = structuredClone(payload);
first._h3CheckpointManagerRefresh();
await settle();
const runSelect = byClass(first, "h3cm-run-select");
confirms = false;
runSelect.value = "other"; runSelect.listeners.change();
assert.equal(runSelect.value, "demo");
assert.equal(value(first), pin);
confirms = true;
runSelect.value = "other"; runSelect.listeners.change();
await settle();
assert.equal(core.checkpointLocalSelection(value(first)), null);
assert.equal(JSON.parse(value(first)).run_name, "other");

select(second, 2, c); await settle();
byText(second, "Use branch locally").click();
select(second, 3, d); await settle();
byText(second, "Follow branch selection").click();
assert.equal(core.checkpointLocalSelection(value(second)), null);
assert.equal(JSON.parse(value(second)).lineage.at(-1).revision, d);
assert.equal(mutations, 0, "local use, release, browsing and reload never call project mutation APIs");
assert.ok(requests.every(({path, options}) => !options.method || path.endsWith("/delete-preview")));
console.log("Checkpoint local output: real mounted UI, isolation, pin persistence, missing runs/takes, refresh and release pass");

// ComfyUI mounts nodes before applying the saved widget values. Unpinned
// chapter output must be restored too, not overwritten by the mounted default.
const restoredScope = makeNode();
await settle();
restoredScope.widgets[0].value = core.checkpointSelectionJson(
    payload, "demo", payload.revisions[2], {start:1, end:3}, "chapter");
restoredScope.properties.h3_checkpoint_manager_scene = 2;
restoredScope.properties.h3_checkpoint_manager_revision = c;
NodeType.prototype.onConfigure.call(restoredScope);
await settle();
assert.equal(byClass(restoredScope, "h3cm-output-scope").value, "chapter",
    "un-pinned saved chapter scope must hydrate the mounted selector");
assert.equal(JSON.parse(value(restoredScope)).output_scope, "chapter");
assert.equal(JSON.parse(value(restoredScope)).lineage.at(-1).revision, c);

// A late widget restore must not queue whole-project output while the visible
// selector still says chapter. Check both API and saved-workflow serialization.
const staleScope = core.checkpointSelectionJson(payload, "demo", payload.revisions[2]);
restoredScope.widgets[0].value = staleScope;
assert.equal(byClass(restoredScope, "h3cm-output-scope").value, "chapter");
assert.equal(JSON.parse(await restoredScope.widgets[0].serializeValue()).output_scope, "chapter");
restoredScope.widgets[0].value = staleScope;
const savedScope = {widgets_values:[staleScope], widgets_values_named:{selection_json:staleScope}, properties:{}};
restoredScope.onSerialize(savedScope);
assert.equal(JSON.parse(savedScope.widgets_values[0]).output_scope, "chapter");
assert.equal(JSON.parse(savedScope.widgets_values_named.selection_json).output_scope, "chapter");
assert.equal(savedScope.properties.h3_checkpoint_manager_output_scope, "chapter");
const emptyScope = makeNode("", {h3_checkpoint_manager_output_scope:"chapter"});
await settle();
assert.equal(byClass(emptyScope, "h3cm-output-scope").value, "chapter",
    "scope survives saving while no checkpoint lineage is available");
// Replacing widgets during reconfiguration must not leave handlers writing
// into the old closed-over widget instead of the one ComfyUI queues.
const detachedWidget = restoredScope.widgets[0];
restoredScope.widgets[0] = {name:"selection_json", value:staleScope};
restoredScope.properties.h3_checkpoint_manager_output_scope = "project";
NodeType.prototype.onConfigure.call(restoredScope);
await settle();
const reboundScope = byClass(restoredScope, "h3cm-output-scope");
assert.equal(reboundScope.value, "project");
reboundScope.value = "chapter";
reboundScope.listeners.change();
assert.equal(JSON.parse(await restoredScope.widgets[0].serializeValue()).output_scope, "chapter");
assert.notEqual(restoredScope.widgets[0], detachedWidget);
const malformed = '{"output_scope":"unknown","lineage":[]}';
restoredScope.widgets[0].value = malformed;
assert.equal(await restoredScope.widgets[0].serializeValue(), malformed, "do not hide invalid selection data");
console.log("Checkpoint scope: configure, API serialization, workflow save and empty selection pass");

// Follow the actual candidate card -> attach -> refreshed alias -> queued
// output path. Earlier chapters remain timing-only when scope is chapter.
currentGraph = structuredClone(payload);
currentGraph.editorial = {chapters:[
    {id:"one", title:"Chapter 1", start_scene:1},
    {id:"two", title:"Chapter 2", start_scene:2},
]};
currentGraph.revisions[3].parent = {scene:2, revision:c};
currentGraph.revisions[3].active = false;
currentGraph.branches[0].path.pop();
currentGraph.branches[0].attribution_slot = {
    scene:3, parent_scene:2, parent_revision:b, candidates:[{scene:3, revision:d}],
};
currentGraph.branches[1].path.push({scene:3, revision:d});
const attaching = makeNode(core.checkpointSelectionJson(
    currentGraph, "demo", currentGraph.revisions[1], {start:2, end:3}, "chapter"));
await settle();
byText(attaching, "S3 · reuse saved clip").click();
assert.equal(byClass(attaching, "h3cm-output-scope").value, "chapter");
attachResponse = {scene:3, revision:"e".repeat(32), message:"Attached"};
byText(attaching, "Attach selected candidate").click();
await settle();
const attachedOutput = JSON.parse(await attaching.widgets[0].serializeValue());
assert.equal(attachedOutput.output_scope, "chapter");
assert.equal(attachedOutput.scope_start_scene, 2);
assert.equal(attachedOutput.lineage.at(-1).revision, attachResponse.revision);
assert.equal(mutations, 1, "only explicit attachment writes; no automatic project activation");
console.log("Checkpoint attachment: candidate -> alias -> chapter-only API output passes");

// Processing tabs mount the real extension. Merely inspecting a derivative
// must not retarget the execution widget, activate a branch, or delete a base.
currentGraph = structuredClone(payload);
currentGraph.processing_variants = [
    {key:"demo/motion/one", scene:2, revision:"1".repeat(32), stage:"derope", profile:"motion",
        profile_path:"demo/upscaled/motion", originals:[{scene:2, revision:b}], ready:true,
        width:960, height:544, raw_frames:175, delivered_frames:175, latent_saved:false, context_steps:12,
        video:{filename:"motion.mp4"}, audio:{filename:"motion.wav"}},
    {key:"demo/motion/two", scene:2, revision:"2".repeat(32), stage:"derope", profile:"motion2",
        profile_path:"demo/upscaled/motion2", originals:[{scene:2, revision:b}], ready:true,
        checkpoint_sha256:"2".repeat(64), processing_branch:{path:"demo/motion/two", kind:"metadata",
            lineage:[{scene:2, revision:"2".repeat(32), metadata_path:"demo/motion/two", checkpoint_sha256:"2".repeat(64)}]},
        width:960, height:544, raw_frames:175, delivered_frames:175, latent_saved:true, latent_layout:"joint_av",
        video:{filename:"motion2.mp4"}},
    {key:"demo/hq/one", scene:2, revision:"3".repeat(32), stage:"latent_upscale", profile:"hq",
        profile_path:"demo/upscaled/hq", originals:[{scene:2, revision:b}], ready:true,
        width:1920, height:1088, latent_saved:true, latent_layout:"single", video:{filename:"hq.mp4"}},
    {key:"demo/pixel/orphan", scene:2, revision:"4".repeat(32), stage:"pixel_upscale", profile:"pixel",
        profile_path:"demo/upscaled/pixel", originals:[], ready:false, latent_saved:false,
        source_status:"original unavailable or source mismatch", video:{filename:"orphan.mp4"}},
];
const variants = makeNode();
await settle();
select(variants, 2, b);
await settle();
const originalOutput = value(variants), mutationsBefore = mutations;
const originalSummary = byClass(variants, "h3cm-output-summary").textContent;
byText(variants, "DeRoPE · 2").click();
assert.equal(byClass(variants, "h3cm-stage-tab")["role"], "tab");
assert.equal(value(variants), originalOutput);
assert.equal(byClass(variants, "h3cm-output-summary").textContent, originalSummary,
    "browsing DeRoPE still describes the Original source that will be sent");
assert.equal(byClass(variants, "h3cm-preview").dataset.source, "/view?filename=motion.mp4&subfolder=&type=output");
assert.ok(elements(variants).some(item => /Continuation tail only/.test(item.textContent)));
byText(variants, "Use DeRoPE branch locally").click();
assert.equal(value(variants), originalOutput, "an unusable processing branch never changes output");
assert.match(byClass(variants, "h3cm-status").textContent, /unambiguous saved branch/);
assert.ok(byText(variants, "Assign to working branch").disabled);
assert.ok(byText(variants, "Delete processed version").disabled);
select(variants, 2, "2".repeat(32));
assert.equal(value(variants), originalOutput);
assert.equal(byClass(variants, "h3cm-audio").hidden, true, "no stale sidecar from another take");
assert.ok(elements(variants).some(item => /Full latent saved \(joint_av\)/.test(item.textContent)));
const savedVariantProperties = structuredClone(variants.properties);
const processingPin = makeNode(originalOutput, savedVariantProperties);
await settle();
select(processingPin, 2, "2".repeat(32));
byText(processingPin, "Use DeRoPE branch locally").click();
const pinnedProcessing = JSON.parse(value(processingPin));
assert.equal(pinnedProcessing.processing_source.stage, "derope");
assert.equal(pinnedProcessing.output_mode, "workflow_local");
assert.equal(pinnedProcessing.lineage.at(-1).scene, 3, "processing preview does not trim the original branch");
assert.deepEqual(pinnedProcessing.processing_source.branch.lineage.map(item => item.scene), [2], "absent processing scenes remain original fallbacks");
const processedSummary = byClass(processingPin, "h3cm-output-summary").textContent;
assert.match(processedSummary, /DeRoPE checkpoints for scenes 2; Original fallback for scenes 1, 3/);
assert.match(processedSummary, /DeRoPE branch 22222222 \(demo\/upscaled\/motion2\)/);
const restoredPin = makeNode(value(processingPin), structuredClone(processingPin.properties));
await settle();
assert.deepEqual(JSON.parse(value(restoredPin)), pinnedProcessing);
byText(restoredPin, "Original · 4").click();
assert.deepEqual(JSON.parse(value(restoredPin)), pinnedProcessing, "tab switch cannot reset DeRoPE output");
assert.equal(byClass(restoredPin, "h3cm-output-summary").textContent, processedSummary);
byText(restoredPin, "Use branch locally").click();
assert.equal(JSON.parse(value(restoredPin)).processing_source, undefined, "explicit Original selection resets source stage");
assert.match(byClass(restoredPin, "h3cm-output-summary").textContent, /Will send to connected nodes: Original checkpoints/);
const reopenedVariant = makeNode(originalOutput, savedVariantProperties);
await settle();
assert.equal(byClass(reopenedVariant, "h3cm-preview").dataset.source, "/view?filename=motion2.mp4&subfolder=&type=output");
assert.equal(reopenedVariant.properties.h3_checkpoint_manager_scene, 2,
    "restoring a processing view must not promote its source selection to the deepest original tip");
assert.equal(value(reopenedVariant), originalOutput, "reopening a processing tab never rewrites original output");
byClass(variants, "h3cm-scenes").children[0].click();
assert.equal(byClass(variants, "h3cm-preview").src, undefined, "missing stage does not impersonate original preview");
assert.equal(value(variants), originalOutput, "even browsing another scene in a derivative tab leaves output unchanged");
byText(variants, "Latent Upscale · 1").click();
select(variants, 2, "3".repeat(32));
assert.equal(value(variants), originalOutput);
assert.match(byClass(variants, "h3cm-preview").src, /hq.mp4/);
byText(variants, "Pixel Upscale · 1").click();
select(variants, 2, "4".repeat(32));
assert.ok(elements(variants).some(item => item.textContent === "original unavailable or source mismatch"));
assert.equal(value(variants), originalOutput);
assert.equal(byClass(variants, "h3cm-output-summary").textContent, originalSummary,
    "Pixel Upscale tab browsing cannot claim to send a processed source");
byText(reopenedVariant, "Original · 4").click();
assert.equal(value(reopenedVariant), originalOutput);
assert.equal(mutations, mutationsBefore, "no processing-tab operation mutates the project");
byText(reopenedVariant, "DeRoPE · 2").click();
currentGraph.processing_variants = currentGraph.processing_variants.filter(item => item.key !== "demo/motion/two");
reopenedVariant._h3CheckpointManagerRefresh();
await settle();
assert.equal(byClass(reopenedVariant, "h3cm-preview").src, undefined,
    "a removed explicitly browsed take does not become another saved take on refresh");
assert.match(byClass(reopenedVariant, "h3cm-prompt").textContent, /previously browsed processing take is unavailable/);
assert.equal(value(reopenedVariant), originalOutput);
console.log("Checkpoint processing tabs: retained takes, previews, missing versions, saved view, and output isolation pass");

// Processing deletion uses its own endpoint, confirmation and immutable path.
processingDeletion = true;
const deleting = makeNode(); await settle();
select(deleting, 2, b); await settle();
byText(deleting, "Latent Upscale · 1").click(); await settle();
select(deleting, 2, "3".repeat(32)); await settle();
const outputBeforeDelete = value(deleting), originalsBeforeDelete = JSON.stringify(currentGraph.revisions);
assert.equal(byText(deleting, "Delete processed version").disabled, false);
const beforeCancel = mutations;
confirms = false;
byText(deleting, "Delete processed version").click(); await settle();
assert.equal(mutations, beforeCancel, "cancel does not send a deletion request");
confirms = true; deleteConflict = true;
byText(deleting, "Delete processed version").click(); await settle();
assert.match(byClass(deleting, "h3cm-status").textContent, /Preview changed/);
assert.equal(byText(deleting, "Delete processed version").disabled, true);
assert.ok(currentGraph.processing_variants.some(v => v.key === "demo/hq/one"));
deleteConflict = false;
select(deleting, 2, "3".repeat(32)); await settle();
byText(deleting, "Delete processed version").click(); await settle();
assert.ok(!currentGraph.processing_variants.some(v => v.key === "demo/hq/one"));
assert.equal(JSON.stringify(currentGraph.revisions), originalsBeforeDelete);
assert.equal(value(deleting), outputBeforeDelete);
assert.equal(byClass(deleting, "h3cm-preview").src, undefined);
assert.equal(byText(deleting, "Delete processed version").disabled, true);
assert.match(byClass(deleting, "h3cm-status").textContent, /Reclaimed/);
// Orphaned versions must also be deletable (no original selection exists).
byText(deleting, "Pixel Upscale · 1").click(); await settle();
select(deleting, 2, "4".repeat(32)); await settle();
assert.equal(byText(deleting, "Delete processed version").disabled, false);
byText(deleting, "Delete processed version").click(); await settle();
assert.ok(!currentGraph.processing_variants.some(v => v.key === "demo/pixel/orphan"));
// Sequence-order history must not force the user to delete independent later clips.
const independentPixel = (scene, digit) => ({key:`demo/pixel/${scene}`, scene,
    revision:digit.repeat(32), stage:"pixel_upscale", profile:"pixel",
    profile_path:"demo/upscaled/pixel", originals:[{scene, revision:scene === 2 ? b : c}],
    ready:true, latent_saved:false, context_steps:0, video:{filename:`pixel-${scene}.mp4`}});
currentGraph.processing_variants.push(independentPixel(2, "5"), independentPixel(3, "6"));
const laterPixelBeforeDelete = JSON.stringify(currentGraph.processing_variants.at(-1));
retainedPixelTakes = [{scene:3, revision:"6".repeat(32), metadata_path:"demo/pixel/3"}];
deleting._h3CheckpointManagerRefresh(); await settle();
select(deleting, 2, "5".repeat(32)); await settle();
assert.ok(elements(deleting).some(item => /Independent pixel takes kept: Scene 3 · 66666666/.test(item.textContent)));
byText(deleting, "Delete processed version").click(); await settle();
assert.match(confirmations.at(-1), /Later independent pixel clips are kept/);
assert.ok(!currentGraph.processing_variants.some(v => v.key === "demo/pixel/2"));
assert.equal(JSON.stringify(currentGraph.processing_variants.find(v => v.key === "demo/pixel/3")), laterPixelBeforeDelete);
assert.equal(JSON.stringify(currentGraph.revisions), originalsBeforeDelete);
assert.equal(value(deleting), outputBeforeDelete);
retainedPixelTakes = [];
// A slow processing preview arriving after switching tabs cannot enable original deletion.
let releasePreview;
delayedProcessingPreview = new Promise(resolve => { releasePreview = resolve; });
byText(deleting, "DeRoPE · 1").click(); await settle();
byText(deleting, "Original · 4").click(); await settle();
releasePreview(); await settle();
assert.ok(byText(deleting, "Delete selected revision").disabled);
assert.equal(value(deleting), outputBeforeDelete);
processingDeletion = false;
console.log("Processing deletion UI: preview, cancel, conflict, success, orphan/independent pixel cleanup, stale response and original/output isolation pass");

// Retirement is separate from media deletion and preserves the output selection.
currentGraph = structuredClone(payload);
snapshotRetirement = true;
const retiring = makeNode(local); await settle();
select(retiring, 2, c); await settle();
const retireLabel = "Retire Chapter 1 snapshot aaaaaaaa…";
const retirementPin = value(retiring), retirementGraph = JSON.stringify(currentGraph);
assert.equal(byText(retiring, "Delete selected revision").disabled, true);
const beforeRetirementCancel = mutations;
confirms = false;
byText(retiring, retireLabel).click(); await settle();
assert.equal(mutations, beforeRetirementCancel);
assert.equal(snapshotRetired, false);
assert.match(confirmations.at(-1), /Scene 2 · cccccccc/);
assert.match(confirmations.at(-1), /Deleting its inputs later makes full recovery unavailable/);
assert.match(byClass(retiring, "h3cm-status").textContent, /cancelled/);
confirms = true;
for (const code of [423, 409]) {
    retirementError = code;
    byText(retiring, retireLabel).click(); await settle();
    assert.equal(snapshotRetired, false);
    assert.equal(byText(retiring, "Delete selected revision").disabled, true);
    assert.match(byClass(retiring, "h3cm-status").textContent, code === 423 ? /read only/ : /preview changed/);
}
retirementError = 0;
let releaseRetirement;
delayedRetirementPreview = new Promise(resolve => { releaseRetirement = resolve; });
const beforeSlowRetirement = mutations, beforeSlowConfirmation = confirmations.length;
byText(retiring, retireLabel).click(); await settle();
select(retiring, 3, d); await settle();
releaseRetirement(); await settle();
assert.equal(mutations, beforeSlowRetirement, "slow retirement preview cannot apply to another selection");
assert.equal(confirmations.length, beforeSlowConfirmation);
select(retiring, 2, c); await settle();
byText(retiring, retireLabel).click(); await settle();
assert.equal(snapshotRetired, true);
assert.equal(byText(retiring, retireLabel), undefined);
assert.equal(byText(retiring, "Delete selected revision").disabled, false);
assert.equal(JSON.stringify(currentGraph), retirementGraph, "retirement never deletes clips or changes active pointers");
assert.equal(value(retiring), retirementPin, "retirement never moves a workflow-local pin");
assert.match(byClass(retiring, "h3cm-status").textContent, /Snapshot retired/);
snapshotRetirement = false;
console.log("Chapter retirement UI: preview, cancel, ownership, conflict, stale response and output/media isolation pass");

// Reproduce the reported Chapter 2 pin at scene 8, shared with another branch.
currentGraph = structuredClone(payload);
currentGraph.revisions.forEach(item => {
    item.scene += 7;
    if (item.parent) item.parent.scene += 7;
});
currentGraph.scenes = [8, 9, 10].map(scene => ({scene}));
currentGraph.branches.forEach(branch => branch.path.forEach(item => item.scene += 7));
// Earlier chapters are immutable timing metadata in a chapter-only selection.
const previous = Array.from({length:7}, (_, index) => ({
    scene:index + 1, revision:String(index + 1).repeat(32), active:true, ready:false,
}));
currentGraph.revisions.unshift(...previous);
currentGraph.editorial = {chapters:[{id:"two", title:"Chapter 2", start_scene:8}]};
const partialPin = core.checkpointLocalSelectionJson(currentGraph, "demo",
    currentGraph.revisions[7], {start:8, end:10}, "chapter");
const repair = makeNode(partialPin);
await settle();
assert.equal(value(repair), partialPin, "never guess which descendant of an old shared pin was intended");
assert.match(byClass(repair, "h3cm-output-summary").textContent, /old partial selection/);
const activeHeading = elements(repair).find(item => item.className.includes("h3cm-branch-head")
    && item.children.some(child => child.textContent === "Project active branch"));
activeHeading.click(); await settle();
select(repair, 8, a); await settle();
byText(repair, "Use branch locally").click();
const fullPin = value(repair);
assert.deepEqual(JSON.parse(fullPin).lineage.slice(-3).map(item => item.scene), [8, 9, 10]);
assert.equal(JSON.parse(fullPin).lineage.at(-1).revision, d);
assert.equal(JSON.parse(fullPin).scope_start_scene, 8);
assert.equal(JSON.parse(fullPin).output_scope, "chapter");
assert.match(byClass(repair, "h3cm-output-summary").textContent, /scenes 8–10 \(3 clips; selected chapter only\)/);
select(repair, 9, b); await settle();
assert.equal(value(repair), fullPin);
repair._h3CheckpointManagerRefresh(); await settle();
assert.equal(value(repair), fullPin, "full branch remains selected through previews and refresh");
assert.equal(core.checkpointOutputBranchTip(currentGraph, currentGraph.revisions[7], {start:8, end:10}), null,
    "a shared ancestor alone cannot decide between two descendant branches");
// Selecting a branch header explicitly also chooses full output in follow mode.
const following = makeNode(); await settle();
const alternateHeading = elements(following).find(item => item.className.includes("h3cm-branch-head")
    && item.children.some(child => child.textContent === "Branch cccccccc"));
alternateHeading.click(); await settle();
assert.equal(JSON.parse(value(following)).lineage.at(-1).revision, c);
select(following, 8, a); await settle();
assert.equal(JSON.parse(value(following)).lineage.at(-1).revision, c);
console.log("Checkpoint branch range: previews never trim output; explicit full-branch selection repairs legacy scene-8 pins");

// Render actual processing histories, not source-branch stacks. Exercise the
// real mounted renderer and handlers with shared prefixes, chapter tabs and
// a deleted intermediate take, without changing execution selection.
currentGraph = structuredClone(payload);
currentGraph.editorial = {chapters:[{id:"first", title:"Chapter 1", start_scene:1},
    {id:"second", title:"Chapter 2", start_scene:3}]};
function processingTake(scene, id, hour) {
    return {scene, key:`demo/pixel/${id}`, revision:id.repeat(32),
        checkpoint_sha256:id.repeat(64), profile:"pixel", profile_path:"demo/pixel",
        stage:"pixel_upscale", ready:true, latent_saved:false,
        created_at:`2026-09-08T${hour}:00:00Z`,
        originals:[{scene, revision:({1:a, 2:b, 3:d})[scene]}],
        video:{filename:`${id}.mp4`}};
}
const p1 = processingTake(1, "1", "10"), p2 = processingTake(2, "2", "11"),
    p2new = processingTake(2, "3", "12"), p3 = processingTake(3, "4", "13");
function processingHistory(...items) {
    return {path:items.at(-1).key, kind:"metadata", stage:"pixel_upscale",
        profile:"pixel", profile_path:"demo/pixel",
        lineage:items.map(item => ({scene:item.scene, revision:item.revision,
            checkpoint_sha256:item.checkpoint_sha256, metadata_path:item.key}))};
}
currentGraph.processing_variants = [p3, p2new, p2, p1];
currentGraph.processing_branches = [processingHistory(p1), processingHistory(p1, p2),
    processingHistory(p1, p2new), processingHistory(p1, p2new, p3)];
const branchView = makeNode(); await settle();
const outputBeforePreview = value(branchView), mutationsBeforePreview = mutations;
byText(branchView, "Pixel Upscale · 4").click();
byText(branchView, "Chapter 1").click();
const processingRows = () => elements(branchView).filter(item => item.className.split(" ").includes("h3cm-branch"));
const cardNames = row => row.children.at(-1).children.filter(item => item.tag === "button").map(item => item.textContent);
let displayed = processingRows();
assert.equal(displayed.length, 2, "Source branches are not duplicated into stacks");
assert.deepEqual(cardNames(displayed[0]), ["S1 · 11111111", "S2 · 33333333"]);
assert.deepEqual(cardNames(displayed[1]), ["S1 · 11111111", "S2 · 22222222"]);
assert.equal(elements(branchView).filter(item => item.textContent === "shared ×2").length, 2);
assert.ok(displayed[0].children[0].children.some(item => item.textContent === "Latest save"));
assert.ok(!displayed[1].children[0].children.some(item => item.textContent === "Latest save"));
assert.ok(elements(branchView).some(item => item.textContent.startsWith("Created: ")));
assert.ok(!elements(branchView).some(item => item.className.includes("h3cm-variant-group")));
displayed[1].children[0].click(); await settle();
assert.equal(byClass(branchView, "h3cm-preview").dataset.source, "/view?filename=2.mp4&subfolder=&type=output");
assert.equal(value(branchView), outputBeforePreview, "Branch heading is preview only");
processingRows()[0].children[0].listeners.keydown({key:"Enter", preventDefault(){}});
await settle();
assert.equal(byClass(branchView, "h3cm-preview").dataset.source, "/view?filename=3.mp4&subfolder=&type=output");
assert.equal(value(branchView), outputBeforePreview, "Keyboard heading selection is preview only");
assert.ok(byText(branchView, "Assign to working branch").disabled);
byText(branchView, "Chapter 2").click();
assert.equal(processingRows().length, 1);
assert.deepEqual(cardNames(processingRows()[0]), ["S3 · 44444444"]);
byText(branchView, "Chapter 1").click();
currentGraph.processing_variants = [p1, p2, p3];
branchView._h3CheckpointManagerRefresh(); await settle();
displayed = processingRows();
assert.equal(displayed.length, 2);
assert.ok(elements(branchView).some(item => item.textContent === "Missing saved take"));
assert.ok(elements(branchView).some(item => /history incomplete/.test(item.textContent)));
assert.equal(displayed.flatMap(row => cardNames(row)).filter(name => name === "S2 · 22222222").length, 1,
    "Another take never fills a deleted branch member");
assert.equal(value(branchView), outputBeforePreview);
assert.equal(mutations, mutationsBeforePreview, "Rendering, headings and chapter tabs never mutate saved projects");
console.log("Processing branch UI: shared colors, latest dates, real paths, missing slots, keyboard previews and output isolation pass");

// Named branch selection preserves the original assign/reuse feature and
// scopes both its mutation and the source manifest serialized for execution.
currentGraph = structuredClone(payload);
namedWorkingBranch = "e".repeat(32);
allowWorkingAssignment = true;
const workingManager = makeNode(); await settle();
const otherOutput = value(branchView);
const workingDropdown = elements(workingManager).find(item => item.title?.startsWith("Working branch for"));
workingDropdown.value = namedWorkingBranch;
await workingDropdown.listeners.change(); await settle();
assert.equal(JSON.parse(value(workingManager))._branch_id, namedWorkingBranch);
select(workingManager, 2, c);
byText(workingManager, "Assign to working branch").click(); await settle();
const assignment = requests.findLast(item => item.path.includes("/checkpoint-revisions/restore"));
assert.ok(assignment.path.endsWith(`?branch_id=${namedWorkingBranch}`));
assert.deepEqual(JSON.parse(assignment.options.body).revisions, [{scene:1,revision:a}, {scene:2,revision:c}]);
assert.equal(JSON.parse(value(workingManager))._branch_id, namedWorkingBranch);
assert.equal(value(branchView), otherOutput, "Assignment must not change another manager's output");
console.log("Named working branch UI: selector, output identity and retained assign/reuse action pass");
