import assert from "node:assert/strict";

const {
    assetDetailContext,
    assetDetailEntries,
    assetDetailLine,
    assetDetailTarget,
    describedTokens,
    insertAssetDetails,
    staleAssetDetails,
} = await import("../web/h3_asset_details_core.mjs");

const asset = (id, tag_type, description) => ({id, tag_type, description});
const records = [
    {token: "@hero", active: true, asset: asset("a", "char", "is a  tall woman\nin a red coat.")},
    {token: "@cabinet", active: true, asset: asset("b", "object", "A tall wooden linen cabinet.")},
    {token: "@cabinet", active: true, asset: asset("b", "object", "duplicate")},
    {token: "@hall", active: true, asset: asset("c", "scene", "is a dim hallway.")},
    {token: "@look", active: false, asset: asset("d", "style", "is grainy 16mm film.")},
    {token: "@clip_audio", active: true, pairedWith: {}, asset: asset("e", "", "x")},
    {token: "@plain", active: true, asset: asset("f", "char", "  ")},
];
const entries = assetDetailEntries(records);
assert.deepEqual(entries.map((item) => item.token), ["@hero", "@cabinet", "@hall", "@look"]);
assert.equal(entries[0].description, "is a tall woman in a red coat.");
assert.deepEqual(assetDetailContext(entries).map((item) => item.tag), ["@hero", "@cabinet", "@hall"]);
assert.deepEqual(assetDetailContext(entries)[0], {
    tag: "@hero", tag_type: "char", description: "is a tall woman in a red coat.",
});

assert.equal(assetDetailLine(entries[0]), "@hero is a tall woman in a red coat.");
assert.equal(assetDetailLine(entries[1]), "@cabinet: A tall wooden linen cabinet.");
assert.deepEqual([...describedTokens("  @hero is x\nsee @hall here\n@cabinet: y")], ["@hero", "@cabinet"]);
assert.equal(assetDetailTarget("char", ["subject_definitions", "detailed_description"]), "subject_definitions");
assert.equal(assetDetailTarget("scene", ["subject_definitions", "detailed_description"]), "detailed_description");
assert.equal(assetDetailTarget("style", ["integrated_multimodal_description"]), "integrated_multimodal_description");
assert.equal(assetDetailTarget("object", []), null);

// Ref2VA prompt: subjects and objects to subject_definitions, scenes to the
// detailed description; unused tags and existing definitions are skipped.
const h3 = [
    "subject_definitions:",
    "<Subject 1> comes from @hero.",
    "",
    "summary:",
    "[reference generation] @hero opens @cabinet in @hall.",
    "",
    "detailed_description:",
    "The camera pushes in.",
    "",
].join("\n");
const filled = insertAssetDetails(h3, entries);
assert.deepEqual(filled.inserted, ["@hero", "@cabinet", "@hall"]);
assert.equal(filled.text, [
    "subject_definitions:",
    "<Subject 1> comes from @hero.",
    "@hero is a tall woman in a red coat.",
    "@cabinet: A tall wooden linen cabinet.",
    "",
    "summary:",
    "[reference generation] @hero opens @cabinet in @hall.",
    "",
    "detailed_description:",
    "The camera pushes in.",
    "@hall is a dim hallway.",
    "",
].join("\n"));
// Idempotent: a second run inserts nothing.
assert.deepEqual(insertAssetDetails(filled.text, entries).inserted, []);
assert.equal(insertAssetDetails(filled.text, entries).text, filled.text);

// Empty section bodies keep the following header on its own line.
const empty = insertAssetDetails("subject_definitions:\nsummary: @hero walks.", entries).text;
assert.equal(empty, "subject_definitions:\n@hero is a tall woman in a red coat.\n\nsummary: @hero walks.");

// Unformatted prompts get a leading block.
assert.equal(
    insertAssetDetails("@cabinet stands by the door.", entries).text,
    "@cabinet: A tall wooden linen cabinet.\n\n@cabinet stands by the door.",
);
// A tag prefix of a longer tag is not a use.
assert.deepEqual(insertAssetDetails("@heroine waves.", entries).inserted, []);

// Stale detection reports only unedited inserted lines whose asset changed.
const changed = assetDetailEntries([
    {token: "@hero", active: true, asset: asset("a", "char", "is a short man.")},
    {token: "@cabinet", active: true, asset: asset("b", "object", "A tall wooden linen cabinet.")},
    {token: "@hall", active: true, asset: asset("c", "scene", "is a bright hallway.")},
]);
const insertedLines = {
    "@hero": "@hero is a tall woman in a red coat.",
    "@cabinet": "@cabinet: A tall wooden linen cabinet.",
    "@hall": "@hall is a dim hallway.",
};
const edited = filled.text.replace("@hall is a dim hallway.", "@hall is a dim, narrow hallway.");
const stale = staleAssetDetails(edited, changed, insertedLines);
assert.deepEqual(stale, [{
    token: "@hero",
    oldLine: "@hero is a tall woman in a red coat.",
    newLine: "@hero is a short man.",
}]);
const refreshed = insertAssetDetails(edited, changed, {refresh: stale});
assert.deepEqual(refreshed.refreshed, ["@hero"]);
assert.match(refreshed.text, /^@hero is a short man\.$/m);
assert.match(refreshed.text, /@hall is a dim, narrow hallway\./);
assert.deepEqual(staleAssetDetails(refreshed.text, changed, {
    ...insertedLines, "@hero": "@hero is a short man.",
}), []);

console.log("H3 asset details: entries, placement, idempotent insertion, and stale refresh pass");
