import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

// Execute the real request coordinator with transport/DOM boundaries stubbed.
// A browser render test remains a separate qualification gate.
const source = fs.readFileSync(new URL("../web/h3_chain_review_final.js", import.meta.url), "utf8");
const start = source.indexOf("async function submitDeferredReview(");
const end = source.indexOf("async function submit(action)", start);
assert(start >= 0 && end > start);

async function run({activation = true, fail = "", legacy = false} = {}) {
    const calls = [];
    const prepared = {
        run_name: "copy", scene: 1, scene_prompt: "Exact saved prompt 雪", seed: "18446744073709551601",
        length: 5, resume_scene: 2, end_clip: 7, clip_count: 7,
        candidate_number: 1, candidate_count: 2, kept_candidate_count: 1,
        resume_revisions: [{scene: 1, revision: "a".repeat(32)}],
    };
    if (!legacy) prepared.activation_required = activation;
    const context = vm.createContext({
        node: {}, current: {}, keptCandidateRevisions: new Set(["a".repeat(32)]),
        status: {}, root: {classList: {add() {}}},
        requireReviewBranch() {},
        async projectMutationOptions(_node, _run, options) { return options; },
        api: {async fetchApi(url, options) {
            const body = JSON.parse(options.body);
            const action = url.endsWith("/restore") ? "restore" : body.action;
            calls.push({action, body});
            return {ok: action !== fail, status: action === fail ? 503 : 200,
                async json() {
                    if (action === fail) return {error: "injected " + action};
                    if (action === "prepare") return prepared;
                    return legacy ? {deleted_candidate_count: 1} : {quarantined_candidate_count: 1};
                }};
        }},
        updatePlan(_node, scene, prompt, seed, length) { calls.push({action: "update", scene, prompt, seed, length}); return true; },
        prepareResume() { calls.push({action: "resume"}); return true; },
        pinAcceptedPreview() {}, refreshDeferredReviews() {}, refreshResumeOptions() {},
        showAcceptedPreview() {}, setActionsEnabled() {},
    });
    vm.runInContext(source.slice(start, end), context);
    const invocation = context.submitDeferredReview({token: "b".repeat(32), run_name: "copy", _branch_id: "main"},
        {revision: "a".repeat(32)});
    if (fail) await assert.rejects(invocation, new RegExp("injected " + fail));
    else await invocation;
    return {calls, text: context.status.textContent};
}

const normal = await run();
assert.deepEqual(normal.calls.map(item => item.action), ["prepare", "restore", "finalize", "update", "resume"]);
assert.equal(normal.calls.find(item => item.action === "update").seed, "18446744073709551601");
assert.match(normal.text, /quarantined \(undo available; files retained\)/);
const retry = await run({activation: false});
assert.deepEqual(retry.calls.map(item => item.action), ["prepare", "finalize", "update", "resume"]);
for (const fail of ["prepare", "restore", "finalize"]) {
    const failed = await run({fail});
    assert(!failed.calls.some(item => item.action === "update" || item.action === "resume"));
}
const legacy = await run({legacy: true});
assert.equal(legacy.calls[1].action, "restore");
assert.match(legacy.text, /1 removed/);
assert.match(source, /finalizationChanged[\s\S]*incomingRevision <= previousRevision && !finalizationChanged/);
assert.match(source, /candidateKeep\.disabled = Boolean\(current\?\.finalization\)/);
console.log("Deferred Review request ordering, exact seed, cleanup retry and failure UI checks passed.");
