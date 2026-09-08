#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    activeSceneFromOutput,
    applySceneReroll,
    resumeSelection,
} from "../web/h3_chain_cancel_reroll_core.mjs";

const plan = {
    shots: [
        {id: "one", prompt: ["One."]},
        {id: "two", prompt: ["Two."], seed: "2"},
        {id: "three", prompt: ["Three."]},
    ],
};
applySceneReroll(plan, 2, "18446744073709551615");
assert.equal(plan.shots[1].seed, "18446744073709551615");
assert.throws(() => applySceneReroll(plan, 4, "1"), /does not exist/);
assert.throws(
    () => applySceneReroll(plan, 1, "18446744073709551616"),
    /unsigned 64-bit/,
);

assert.deepEqual(resumeSelection(2, 3, 3), {
    startClip: 2,
    sceneRange: "",
});
assert.deepEqual(resumeSelection(3, 5, 8), {
    startClip: 3,
    sceneRange: "3:5",
});
assert.deepEqual(resumeSelection(5, 5, 8), {
    startClip: 5,
    sceneRange: "5",
});
assert.throws(() => resumeSelection(6, 5, 8), /Invalid reroll range/);

assert.deepEqual(activeSceneFromOutput({
    h3_chain_active_scene: [{
        run_name: "project",
        clip_index: 2,
        clip_count: 8,
        end_clip: 5,
        shot_id: "hallway",
        seed: "9007199254740993",
    }],
}), {
    runName: "project",
    clipIndex: 2,
    clipCount: 8,
    endClip: 5,
    shotId: "hallway",
    seed: "9007199254740993",
    workflowFingerprint: "",
});
assert.equal(activeSceneFromOutput({h3_chain_active_scene: []}), null);

const source = fs.readFileSync(
    new URL("../web/h3_chain_cancel_reroll.js", import.meta.url),
    "utf8",
);
assert.match(source, /refreshRestoredPlanEditors\(planNode\)/,
    "reroll seed changes refresh every Plan companion");
assert.match(source, /\/api\/jobs\/\$\{encodeURIComponent\(record\.promptId\)\}\/cancel/);
assert.match(source, /execution_interrupted/);
assert.match(source, /await waiter\.promise/);
assert.match(source, /requireVisibleWorkflow\(record\);[\s\S]*verifyPredecessorCheckpoint/);
assert.match(source, /function activeWorkflowIdentity\(\)/);
assert.match(source, /workflowIdentity !== record\.workflowIdentity/);
assert.match(source, /widgetByName\(planNode, "run_name"\)/);
assert.match(source, /record\.currentNode = currentNode/);
assert.doesNotMatch(source, /currentNode !== record\.currentNode/);
assert.match(source, /active = null;[\s\S]*Waiting for ComfyUI to finish interrupting/);
assert.match(source, /applySceneReroll/);
assert.match(source, /resumeSelection/);
assert.match(source, /await app\.queuePrompt\(0, 1\)/);
assert.match(source, /checkpoint \$\{predecessor\} is not ready/);
assert.match(source, /queue the workflow manually/);
assert.match(source, /MiniMaxH3ContexLoop\.cancelRerollControl/);
assert.match(source, /Show floating Cancel & reroll control/);
assert.match(source, /defaultValue:\s*true/);
assert.match(source, /onChange\(value\)[\s\S]*setControlAllowed\(value\)/);
assert.match(source, /if \(!controlAllowed \|\| !active \|\| busy\) return/);
assert.match(source, /root\.hidden = !controlAllowed \|\| !wantsVisible/);
assert.match(source, /\.h3cr-status[\s\S]*max-width:100%[\s\S]*overflow-wrap:anywhere/);
assert.doesNotMatch(source, /fetchApi\("\/interrupt"/);

console.log("H3 cancel-and-reroll helpers: ok");
