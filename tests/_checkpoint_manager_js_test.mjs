#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    checkpointBranchRows,
    checkpointChapterBranchRows,
    checkpointActivationMode,
    checkpointDeletionTitle,
    checkpointDependencyText,
    checkpointProjectLineage,
    checkpointRevisionKey,
    checkpointRevisionLineage,
    checkpointSelectionJson,
    formatCheckpointBytes,
    selectedCheckpointRevision,
} from "../web/h3_checkpoint_manager_core.mjs";

const a = "a".repeat(32);
const b = "b".repeat(32);
const c = "c".repeat(32);
const d = "d".repeat(32);
const e = "e".repeat(32);
const payload = {
    revisions: [
        {scene:1, scene_id:"arrival", revision:a, active:true,
            created_at:"2026-08-20T10:00:00Z"},
        {scene:2, scene_id:"hall", revision:b, active:true,
            created_at:"2026-08-20T10:10:00Z", context_length:39,
            audio_context_length:44, continuation_mode:"guide", ready:true,
            parent:{scene:1, revision:a}},
        {scene:2, scene_id:"hall_alt", revision:c, active:false,
            created_at:"2026-08-20T10:20:00Z", context_length:0,
            audio_context_length:0, continuation_mode:"guide", ready:true,
            parent:{scene:1, revision:a}},
        {scene:3, scene_id:"room", revision:d, active:false,
            created_at:"2026-08-20T10:30:00Z", context_length:0,
            audio_context_length:0, continuation_mode:"guide", ready:true,
            parent:{scene:2, revision:b}},
        {scene:3, scene_id:"room_alt", revision:e, active:true,
            created_at:"2026-08-20T10:40:00Z", context_length:0,
            audio_context_length:0, continuation_mode:"guide", ready:true,
            parent:{scene:2, revision:c}},
    ],
    branches: [
        {id:"active", label:"Active branch", active:true,
            path:[{scene:1, revision:a}, {scene:2, revision:b}]},
        {id:"alternate", label:"Branch alternate", active:false,
            path:[{scene:1, revision:a}, {scene:2, revision:c}],
            attribution_slot:{scene:3, parent_scene:2,
                parent_revision:c, candidates:[{scene:3, revision:d}]}},
        {id:"chapter_alt", label:"Branch chapter alternate", active:false,
            path:[{scene:1, revision:a}, {scene:2, revision:c},
                {scene:3, revision:e}]},
    ],
};

assert.equal(formatCheckpointBytes(0), "0 B");
assert.equal(formatCheckpointBytes(1536), "1.5 KB");
assert.equal(formatCheckpointBytes(2 * 1024 ** 3), "2.00 GB");
assert.equal(checkpointRevisionKey(2, c.toUpperCase()), `2:${c}`);
assert.equal(selectedCheckpointRevision(payload, 2, c).revision, c);
assert.equal(selectedCheckpointRevision(payload, 2).revision, b);
assert.equal(selectedCheckpointRevision(payload).revision, e,
    "default selection is the deepest active branch tip");
assert.equal(checkpointBranchRows(payload)[0].revisions[1].revision, b);
assert.equal(checkpointBranchRows(payload)[1].revisions[0].revision, a,
    "a shared ancestor appears in every inferred branch that uses it");
assert.equal(
    checkpointBranchRows(payload)[1].attribution_slot.candidates[0].revision,
    d,
    "attribution slots resolve their lightweight candidate keys to records",
);
assert.deepEqual(
    checkpointRevisionLineage(payload, payload.revisions[1]),
    [{scene:1, revision:a}, {scene:2, revision:b}],
);
assert.equal(
    checkpointSelectionJson(payload, " demo-run ", payload.revisions[2]),
    JSON.stringify({
        run_name:"demo-run",
        lineage:[{scene:1, revision:a}, {scene:2, revision:c}],
        scope_start_scene:1,
        scope_end_scene:2,
    }),
    "selecting an alternate branch serializes its complete lineage",
);
assert.equal(checkpointSelectionJson(payload, "", payload.revisions[2]), "");
const rollbackPayload = {
    revisions: [
        {scene:1, scene_id:"one", revision:a, active:true, ready:true},
        {scene:2, scene_id:"two", revision:b, active:true, ready:true,
            parent:{scene:1, revision:a}},
        {scene:3, scene_id:"three", revision:d, active:true, ready:true,
            parent:{scene:2, revision:b}},
        {scene:2, scene_id:"two_alt", revision:c, active:false, ready:true,
            parent:{scene:1, revision:a}},
    ],
};
assert.equal(
    checkpointActivationMode(rollbackPayload, rollbackPayload.revisions[0]),
    "rollback",
    "an earlier active checkpoint can retire its later active pointers",
);
assert.equal(
    checkpointActivationMode(rollbackPayload, rollbackPayload.revisions[2]),
    "current",
    "the current active tip does not need activation",
);
assert.equal(
    checkpointActivationMode(rollbackPayload, rollbackPayload.revisions[3]),
    "activate",
    "an inactive branch remains a normal activation",
);
const chapterTwo = {id:"chapter_2", start:3, end:3};
assert.deepEqual(
    checkpointRevisionLineage(payload, payload.revisions[4], chapterTwo),
    [{scene:3, revision:e}],
    "a chapter starts a lineage without inheriting its immutable parent branch",
);
assert.deepEqual(
    checkpointProjectLineage(payload, payload.revisions[4], chapterTwo),
    [{scene:1, revision:a}, {scene:2, revision:b}, {scene:3, revision:e}],
    "the selected chapter is combined with the currently active earlier chapter",
);
assert.deepEqual(
    JSON.parse(checkpointSelectionJson(
        payload, "demo-run", payload.revisions[4], chapterTwo)),
    {
        run_name:"demo-run",
        lineage:[{scene:1, revision:a}, {scene:2, revision:b},
            {scene:3, revision:e}],
        scope_start_scene:3,
        scope_end_scene:3,
    },
);
assert.equal(
    checkpointChapterBranchRows(payload, chapterTwo).filter(
        (branch) => branch.active).length,
    1,
    "chapter rows determine activity without requiring the prior chapter path",
);
assert.match(checkpointDependencyText(payload.revisions[1]),
    /Scene 2 · hall uses Video 39f \/ Audio 44f via guide/);
assert.match(checkpointDependencyText(payload.revisions[2]),
    /structural continuation edge \(Video 0f \/ Audio 0f\)/);
assert.match(checkpointDeletionTitle({allowed:true, owned_file_count:5,
    reclaimed_bytes:1536}), /Safe leaf deletion · 5 files · 1.5 KB/);
assert.match(checkpointDeletionTitle({allowed:true, rollback:true,
    owned_file_count:6, reclaimed_bytes:2048}), /Safe active-tip rollback/);
assert.equal(checkpointDeletionTitle({allowed:false,
    blockers:["A child depends on it."]}), "A child depends on it.");

const source = fs.readFileSync(
    new URL("../web/h3_chain_checkpoint_manager.js", import.meta.url), "utf8",
);
assert.match(source, /MiniMaxH3ChainCheckpointManager/);
assert.match(source, /MiniMaxH3ChainPlan/);
assert.match(source, /selection_json/);
assert.match(source, /checkpointRevisionLineage/);
assert.match(source, /selectionWidget\.callback\?\.\(value\)/);
assert.match(source, /node\.graph\?\.setDirtyCanvas\?\.\(true, true\)/);
assert.match(source, /Select this whole branch/);
assert.match(source, /checkpoint-revisions\/delete-preview/);
assert.match(source, /checkpoint-revisions\/delete/);
assert.match(source, /run-folder\/delete-preview/);
assert.match(source, /run-folder\/delete/);
assert.match(source, /Delete run folder/);
assert.match(source, /window\.prompt/);
assert.match(source, /Second validation/);
assert.match(source, /confirmation:typed/);
assert.match(source, /Original input project assets are kept/);
assert.match(source, /checkpoint-revisions\/restore/);
assert.match(source, /checkpoint-revisions\/attribute/);
assert.match(source, /Load selected branch/);
assert.match(source, /Make branch active/);
assert.match(source, /S\$\{slot\.scene\} · reuse saved clip/);
assert.match(source, /Attach selected candidate/);
assert.match(source, /async function attributeCandidate/);
assert.match(source, /Nothing is regenerated or copied/);
assert.match(source, /function canActivateSelected/);
assert.match(source, /async function activateSelected/);
assert.match(source, /resume_scene:Number\(record\.scene\) \+ 1/);
assert.match(source, /revisions:lineage/);
assert.match(source, /activate_only:true/);
assert.match(source, /applyActivatedRevisions\(planNode, payload\.restored \?\? \[\]\)/);
assert.match(source, /useEffectivePrompts:\s*true/);
assert.match(source, /useTipSharedPrompt:\s*true/);
assert.match(source, /connected Plan scene settings were restored/);
assert.match(source, /cache_bust:String\(Date\.now\(\)\)/);
assert.match(source, /\{cache:"no-store"\}/);
assert.match(source, /No saved revision, workflow, reference, or assembled video is deleted/);
assert.match(source, /Other chapters keep their active branches/);
assert.match(source, /scope_start_scene:scope\.start/);
assert.match(source, /retired_scope_pointers/);
assert.match(source, /prepareResume\(resumeScene\)/);
assert.match(source, /snapshot:plan\.snapshot/);
assert.match(source, /window\.confirm/);
assert.match(source, /Permanent deletion is blocked by dependent revisions/);
assert.match(source, /Roll working branch back/);
assert.match(source, /clears later active pointers but keeps every saved take/);
assert.match(source, /shared, kept/);
assert.match(source, /checkpointForkGraph\(rows, state\.stage\)/);
assert.match(source, /`shared ×\$\{sharedCount\}`/);
assert.match(source, /card\.dataset\.sharedKey = item\.key/);
assert.match(source, /mountCheckpointGraphEdges/);
assert.match(source, /shared clips shown once/);
assert.match(source, /h3cm-output-path/);
assert.match(source, /h3cm-plan-marker/);
assert.match(source, /h3cm-chapter-tabs/);
assert.match(source, /h3_checkpoint_manager_collapsed_chapters/);
assert.match(source, /function setChapterCollapsed/);
assert.match(source, /heading\.setAttribute\("aria-expanded", String\(!collapsed\)\)/);
assert.match(source, /body\.hidden = collapsed/);
assert.match(source, /if \(!collapsed\) renderBranchRows\(body, rows\)/);
assert.match(source, /function chapterRanges/);
assert.match(source, /function selectedChapterRange\(\)/);
assert.match(source, /All scenes/);
assert.match(source, /Unassigned/);
assert.doesNotMatch(source, /prior scene/);
assert.match(source, /state\.payload\?\.editorial\?\.chapters/);
assert.doesNotMatch(source, /pathData \+= ` M \$\{laneX\} \$\{anchor\.y\} H \$\{anchor\.x\}`/);
assert.doesNotMatch(source, /stroke-dasharray/);
assert.doesNotMatch(source, /new ResizeObserver\(scheduleSharedLinks\)/);
assert.doesNotMatch(source, /sharedLinksResizeObserver/);
assert.match(source, /Video \$\{record\.context_length\}f · Audio \$\{record\.audio_context_length\}f/);
assert.match(source, /h3_checkpoint_manager_preview_height/);
assert.match(source, /function previewHeight\(value\)/);
assert.match(source, /Math\.max\(MIN_PREVIEW_HEIGHT, Math\.min\(MAX_PREVIEW_HEIGHT/);
assert.match(source, /h3cm-preview-resizer/);
assert.match(source, /Resize clip preview height/);
assert.match(source, /startHeight \+ moveEvent\.clientY - startY/);
assert.match(source, /setPreviewHeight\(state\.previewHeight, true\)/);
assert.match(source, /setPreviewHeight\(DEFAULT_PREVIEW_HEIGHT, true\)/);
assert.match(source, /event\.key === "ArrowDown"/);
assert.match(source, /addDOMWidget\("h3_checkpoint_manager"/);

const backend = fs.readFileSync(
    new URL("../chain_nodes.py", import.meta.url), "utf8",
);
assert.match(backend, /class MiniMaxH3ChainCheckpointManager/);
assert.match(backend,
    /def passthrough\(self, selection_json="", plan=None\):/);
assert.match(backend, /_checkpoint_selection_manifest\(selection_json\)/);
assert.match(backend, /RETURN_NAMES = \("selected_manifest",\)/);
assert.doesNotMatch(
    backend.slice(
        backend.indexOf("class MiniMaxH3ChainCheckpointManager"),
        backend.indexOf("class MiniMaxH3ChainFirstSceneImage"),
    ),
    /ExecutionBlocker/,
);

const staleTail = {
    revisions:[
        {scene:1, revision:a, active:true, pointer_active:true, ready:true},
        {scene:2, revision:b, active:false, pointer_active:true, ready:true,
            parent:{scene:1, revision:c}},
    ],
};
assert.equal(checkpointActivationMode(staleTail, staleTail.revisions[0]),
    "rollback", "a stale physical pointer can still be explicitly cleared");
const blockedSlot = checkpointBranchRows({
    revisions:payload.revisions,
    branches:[{path:[{scene:2, revision:c}], attribution_slot:{
        scene:3, parent_scene:2, parent_revision:c, candidates:[],
        blocked_candidates:[{scene:3, revision:d, reason:"Saved audio context"}],
    }}],
})[0].attribution_slot;
assert.equal(blockedSlot.blocked_candidates[0].reason, "Saved audio context");
assert.equal(blockedSlot.blocked_candidates[0].scene_id, "room");
assert.match(source, /state\.busy \|\| !state\.attribution\?\.candidate/);
assert.match(source, /attribution\.blocked \?\? \[\]/);
console.log("H3 Checkpoint Manager frontend: branches, manifest selection, inspection and guarded deletion pass");
