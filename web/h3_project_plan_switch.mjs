import {parsePlanJson, planToJson} from "./h3_chain_plan_core.mjs?v=0.7.12";
import {StudioBranches, authoringSignature, branchWidgetTransaction, workingBranchId} from "./h3_working_branches.mjs?v=0.7.31";
import {branchPolicyNodes, captureBranchPolicyInputs, restoreBranchPolicyInputs,
    refreshRestoredPlanEditors} from "./h3_plan_restore_core.mjs?v=0.7.21";
import {connectedProjectAssetPlans} from "./h3_project_asset_sync_core.mjs?v=0.7.3";

export const PLAN_SETTING_WIDGETS = Object.freeze([
    "plan_json", "run_name", "generation_fingerprint", "width", "height",
    "context_length", "encode_mode", "anchor_mode", "crop", "audio_mode",
    "audio_context_length", "default_duration_seconds", "default_steps",
    "base_seed", "segment_crf", "video_blend_frames", "continuation_mode",
]);
const BINDING = "h3_working_branch_binding_v1";
const SELECTIONS = "h3_project_branch_selections_v1";
const sessions = new WeakMap();
const widget = (node, name) => node?.widgets?.find(item => item.name === name);
function graphNodes(graph) {
    return (graph?._nodes ?? graph?.nodes ?? []).flatMap(node => [node, ...graphNodes(node.subgraph)]);
}

export function captureProjectPlan(plan) {
    const result = {};
    for (const name of PLAN_SETTING_WIDGETS) {
        if (name !== "run_name" && widget(plan, name)) result[name] = widget(plan, name).value;
    }
    result.plan_json = planToJson(parsePlanJson(result.plan_json));
    result.policy_inputs = captureBranchPolicyInputs(plan);
    return result;
}

function applyProjectPlan(plan, record) {
    const authored = parsePlanJson(record.authoring.plan_json);
    if (record.id === "main") delete authored._branch_id;
    else authored._branch_id = workingBranchId(record.id);
    for (const name of PLAN_SETTING_WIDGETS) {
        if (name === "run_name" || !(name in record.authoring)) continue;
        const target = widget(plan, name);
        if (!target) continue;
        target.value = name === "plan_json" ? planToJson(authored) : record.authoring[name];
        target.callback?.(target.value);
    }
    restoreBranchPolicyInputs(plan, record.authoring);
}

// Studio supplies its existing controller, including prompt-editor flushes,
// recovery drafts and revision binding. Plain/Modern Plans use the same save
// protocol without needing a Studio node in the graph.
function projectSession(plan, nodes, request) {
    const studios = nodes.map(node => node._h3ProjectPlanSession)
        .filter(session => session?.owner() === plan);
    if (studios.length > 1) throw new Error("Use one Plan Studio per connected Plan when switching projects.");
    if (studios.length) return studios[0];
    let session = sessions.get(plan);
    if (!session) {
        plan.properties ??= {};
        const controller = new StudioBranches({
            selected:workingBranchId(parsePlanJson(widget(plan, "plan_json")?.value)._branch_id),
            binding:plan.properties[BINDING] ?? null,
            rememberBinding:value => { plan.properties[BINDING] = value; },
            capture:() => captureProjectPlan(plan), request,
            apply:record => applyProjectPlan(plan, record),
            flush:async () => {}, changed:() => {},
            isCurrent:(run, id) => widget(plan, "run_name")?.value === run
                && workingBranchId(parsePlanJson(widget(plan, "plan_json")?.value)._branch_id) === id,
        });
        session = {owner:() => plan, controller, retrySaveOnSwitch:true,
            nodes:() => [plan, ...branchPolicyNodes(plan)],
            refresh:() => refreshRestoredPlanEditors(plan)};
        sessions.set(plan, session);
    }
    session.controller.request = request;
    return session;
}

/** Prepare while the old run is still authoritative. No target widget or
 * ownership changes happen until commit; failed reads/saves keep the old Plan.
 * requestArchive returns null only when the project genuinely has no archive.
 */
export async function prepareProjectPlanSwitch(manager, from, to, {request, requestArchive, flush}) {
    if (!from || from === to) return null;
    const graph = manager.graph?.rootGraph ?? manager.graph;
    const nodes = graphNodes(graph);
    const plans = connectedProjectAssetPlans(manager);
    // Two independent Plans writing the same branch would have ambiguous
    // prompts. Refuse rather than let the last writer silently win.
    const owners = [...new Set(plans.map(plan => plan._h3ProjectPlanSession?.owner() ?? plan))];
    if (!owners.length) return null;
    if (owners.length !== 1) throw new Error("Connect this Carousel to one independent Plan before switching projects.");
    const plan = owners[0];
    if (plan.inputs?.some(input => input.name === "plan_json_input" && input.link != null)) {
        throw new Error("Disconnect the external plan_json_input before switching project prompts; it overrides the saved Plan.");
    }
    if (widget(plan, "run_name")?.value !== from) throw new Error("Wait for the connected Plan's project to synchronize.");
    const session = projectSession(plan, nodes, request);
    const controller = session.controller;
    await controller.projectSwitchRead;
    if (controller.busy) throw new Error("Wait for the current branch operation to finish before switching projects.");
    if (controller.run !== from || !controller.ready) await controller.refresh(from);
    if (!controller.ready || controller.draftReading) throw new Error("Wait for saved branch settings to load before switching projects.");
    // A plain Plan has no Studio retry toolbar. A repeated user switch can
    // reconcile the exact uncertain save ID; it never starts a second save.
    if (controller.pending && session.retrySaveOnSwitch) {
        await controller.retryPending();
        if (controller.error) throw new Error(controller.error);
    }
    if (controller.pending) throw new Error("Retry the pending branch save before switching projects.");
    controller.busy = true;
    session.lock?.(true);
    controller.changed();
    let finished = false;
    const release = () => {
        if (finished) return;
        finished = true;
        controller.busy = false;
        session.lock?.(false);
        controller.changed();
    };
    const sourceId = controller.selected;
    const sourceEpoch = controller.epoch;
    const assertCurrent = () => {
        if (manager.graph?.rootGraph !== graph && manager.graph !== graph
                || !graphNodes(graph).includes(manager) || !graphNodes(graph).includes(plan)
                || widget(manager, "run_name")?.value !== from
                || !connectedProjectAssetPlans(manager).some(candidate =>
                    (candidate._h3ProjectPlanSession?.owner() ?? candidate) === plan)
                || controller.run !== from || controller.epoch !== sourceEpoch
                || controller.selected !== sourceId || !controller.isCurrent(from, sourceId)) {
            throw new Error("Project connections or branch changed during the switch; try again.");
        }
    };
    try {
        assertCurrent();
        await flush(from);
        assertCurrent();
        await controller.preserveDraft();
        assertCurrent();
        if (controller.conflict) throw new Error(controller.conflict);
        if (controller.draftRecovery) throw new Error("Resolve the local recovery draft before switching projects.");
        const signature = authoringSignature(controller.capture());
        // Read-only browsing can leave an unchanged branch without a write.
        if (signature !== controller.savedSignature) {
            await controller.save(assertCurrent);
            await controller.resolveDrafts(controller.binding.revision);
        }
        assertCurrent();
        const assertUnedited = () => {
            assertCurrent();
            if (signature !== authoringSignature(controller.capture())) {
                throw new Error("Prompts or Plan settings changed during the switch. Your edits were kept; switch again when finished editing.");
            }
        };
        const listing = await request({action:"list", run_name:to});
        assertUnedited();
        const remembered = plan.properties?.[SELECTIONS]?.[to];
        const id = workingBranchId(listing.branches.some(item => item.id === remembered && !item.hidden)
            ? remembered : listing.default_branch);
        const record = await request({action:"load", run_name:to, branch_id:id});
        assertUnedited();
        if (record.id !== id || record.run_name && record.run_name !== to) throw new Error("Saved Plan belongs to another project or branch.");
        const hadSnapshot = Boolean(record.authoring);
        if (!record.authoring && id === "main") {
            const archive = await requestArchive(to);
            assertUnedited();
            if (archive) record.authoring = archive;
        }
        if (!record.authoring) {
            if (id !== "main") throw new Error("The destination branch has no saved Plan. Reload or repair it in Plan Studio first.");
            // New projects retain the compatible generation controls, not the
            // previous project's prompts, @tags, chapters or context bindings.
            record.authoring = {...controller.capture(), generation_fingerprint:"",
                plan_json:planToJson({shots:[{id:"scene_01", prompt:[]}]})};
        }
        parsePlanJson(record.authoring.plan_json);
        authoringSignature(record.authoring);
        // Keep the empty snapshot baseline for a new/archive-only project so
        // leaving it will persist its first full branch snapshot, even unedited.
        const oldController = {run:controller.run, selected:controller.selected, binding:controller.binding,
            records:controller.records, defaultBranch:controller.defaultBranch, ready:controller.ready,
            savedSignature:controller.savedSignature, conflict:controller.conflict,
            draftRecovery:controller.draftRecovery, draftStatus:controller.draftStatus, error:controller.error};
        return {
            assertCurrent:assertUnedited,
            commit(changeRun) {
                assertUnedited();
                try {
                    branchWidgetTransaction([manager, plan, ...session.nodes()], () => {
                        controller.run = to; controller.selected = id;
                        controller.error = "";
                        controller.records = listing.branches; controller.defaultBranch = listing.default_branch;
                        controller.draftRecovery = null; controller.draftStatus = "";
                        controller.adopt(record);
                        if (!hadSnapshot) controller.savedSignature = "";
                        plan.properties ??= {};
                        plan.properties[SELECTIONS] = {...plan.properties[SELECTIONS], [from]:sourceId, [to]:id};
                        changeRun();
                        session.apply ? session.apply(record) : applyProjectPlan(plan, record);
                    });
                } catch (error) {
                    Object.assign(controller, oldController);
                    throw error;
                }
                controller.epoch += 1;
                controller.observedSignature = null;
                // The atomic commit is complete. A repaint failure must not
                // report a rollback or reclaim the old project's ownership.
                try { release(); session.refresh?.(); }
                catch (error) { console.warn("H3 project Plan switched, but an editor could not refresh:", error); }
                controller.projectSwitchRead = controller.readDraft().then(() => controller.changed())
                    .catch(error => { console.warn("H3 project recovery refresh failed:", error); });
            },
            cancel() {
                release();
                try { session.refresh?.(); }
                catch (error) { console.warn("H3 project switch cancelled; editor refresh failed:", error); }
            },
        };
    } catch (error) {
        controller.error = error.message;
        release();
        throw error;
    }
}
