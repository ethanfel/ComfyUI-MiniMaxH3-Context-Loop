import assert from "node:assert/strict";
import {checkpointProcessingBranchRows as rows} from "../web/h3_checkpoint_manager_core.mjs";

function take(scene, id, date, profile = "pixel", stage = "pixel_upscale") {
    return {scene, key:`demo/${profile}/${id}`, revision:id.repeat(32),
        checkpoint_sha256:id.repeat(64), stage, profile,
        profile_path:`demo/${profile}`, ready:true, latent_saved:false,
        created_at:date, originals:[{scene, revision:`original${scene}`}],
    };
}
function history(...takes) {
    return {path:takes.at(-1).key, kind:"metadata", stage:takes[0].stage,
        profile:takes[0].profile, profile_path:takes[0].profile_path,
        lineage:takes.map(t => ({scene:t.scene, revision:t.revision,
            metadata_path:t.key, checkpoint_sha256:t.checkpoint_sha256})),
    };
}
const a = take(1, "a", "2026-09-08T10:00:00Z");
const b = take(2, "b", "2026-09-08T11:00:00Z");
const c = take(2, "c", "2026-09-08T12:00:00Z");
const d = take(3, "d", "2026-09-08T13:00:00Z");
const input = {
    processing_variants:[d, c, b, a],
    processing_branches:[history(a), history(a, b), history(a, c), history(a, c, d), history(a, c, d)],
    // Sharing an original prefix across source branches must not duplicate runs.
    branches:[{path:[{scene:1, revision:"original1"}, {scene:2, revision:"original2"}]},
        {path:[{scene:1, revision:"original1"}, {scene:2, revision:"new-original2"}]}],
};
const before = JSON.stringify(input);
let actual = rows(input, "pixel_upscale");
assert.equal(actual.length, 2, "Duplicates and strict prefixes collapse to real branch leaves");
assert.deepEqual(actual.map(r => r.entries.map(e => e.record.key)), [[a.key, c.key, d.key], [a.key, b.key]]);
assert.ok(actual[0].latest);
assert.ok(!actual[1].latest);
assert.equal(actual[0].created_at, d.created_at);
assert.equal(actual[0].entries[0].shared_count, 2);
assert.equal(actual[1].entries[0].shared_key, actual[0].entries[0].shared_key);
assert.equal(actual[0].entries[1].shared_count, 1);
assert.equal(JSON.stringify(input), before, "Presentation cannot modify payload/output lineage");

actual = rows(input, "pixel_upscale", {start:1, end:1});
assert.equal(actual.length, 1, "Identical visible chapter prefixes collapse");
assert.equal(actual[0].created_at, a.created_at, "Later chapter saves do not redate this chapter");
actual = rows(input, "pixel_upscale", {start:2, end:3});
assert.deepEqual(actual.map(r => r.entries.map(e => e.scene)), [[2, 3], [2]]);
assert.equal(rows(input, "derope").length, 0);
assert.equal(rows(input, "pixel_upscale", {start:8, end:10}).length, 0);

const changed = structuredClone(input);
changed.processing_variants = [a, b, d]; // Delete c while preserving d's history.
actual = rows(changed, "pixel_upscale");
assert.equal(actual[0].entries[1].record, null, "The old b cannot substitute for deleted c");
assert.equal(actual[0].entries[1].revision, c.revision);
assert.equal(actual[0].missing_count, 1);
assert.ok(actual[0].latest);
const wrongHash = {...c, checkpoint_sha256:"wrong"};
actual = rows({...input, processing_variants:[a, b, wrongHash, d]}, "pixel_upscale");
assert.equal(actual[0].entries[1].record, null, "A matching address with changed content is not the saved take");
assert.equal(actual.filter(r => !r.history_known).length, 1);

const unlinked = {...take(6, "f", "2026-09-08T14:00:00Z"), originals:[]};
actual = rows({...input, processing_variants:[...input.processing_variants, unlinked]}, "pixel_upscale");
assert.equal(actual.length, 3);
assert.equal(actual[0].entries[0].record, unlinked);
assert.ok(actual[0].latest);
assert.ok(!actual[0].history_known, "No history means standalone, not an invented complete run");
assert.equal(actual.flatMap(r => r.entries).filter(e => e.record === unlinked).length, 1);

const otherProfile = take(1, "a", "2026-09-08T09:00:00Z", "pixel2");
actual = rows({...input, processing_variants:[...input.processing_variants, otherProfile],
    processing_branches:[...input.processing_branches, history(otherProfile)]}, "pixel_upscale");
assert.equal(actual.length, 3, "Profiles are separate even when revision text matches");
assert.equal(actual.at(-1).entries[0].shared_count, 1);
const wrongProfile = history(a, c, d);
wrongProfile.lineage[0] = history(otherProfile).lineage[0];
actual = rows({processing_variants:[a, c, d, otherProfile], processing_branches:[wrongProfile]}, "pixel_upscale");
assert.equal(actual[0].entries[0].record, null, "Foreign-profile reference cannot become a usable edge");

const broken = {...b, ready:false};
actual = rows({processing_variants:[a, broken], processing_branches:[history(a, b)]}, "pixel_upscale");
assert.equal(actual[0].missing_count, 1);
assert.equal(actual[0].entries[1].record, broken, "Broken takes retain an inspectable card");

// An old server can still supply an intact processing_branch, but unrelated
// legacy takes with no lineage must remain separate even on adjacent scenes.
actual = rows({processing_variants:[{...a, processing_branch:history(a, b)}, b, c]}, "pixel_upscale");
assert.equal(actual.length, 2);
assert.equal(actual.filter(r => r.history_known).length, 1);
actual = rows({processing_variants:[{...a, created_at:""}, {...b, created_at:"invalid"}]}, "pixel_upscale");
assert.equal(actual.length, 2);
assert.ok(actual.every(r => !r.latest && !r.history_known));
const timezone = {...b, created_at:"2026-09-08T12:30:00+02:00"};
actual = rows({processing_variants:[timezone, a], processing_branches:[]}, "pixel_upscale");
assert.equal(actual[0].entries[0].record, timezone, "Creation times compare instants, not date strings");
actual = rows({processing_variants:[{...a, created_at:b.created_at}, b]}, "pixel_upscale");
assert.ok(actual.every(r => r.latest), "Equal saved timestamps must not imply a false order");
assert.deepEqual(rows({}, "pixel_upscale"), []);
console.log("Processing branch rows: exact histories, shared prefixes, recency, chapter scopes, gaps and legacy fallback pass");
