import assert from "node:assert/strict";

const {
    assetDescribeBlocker,
    assetDetailContext,
    assetDetailEntries,
    assetDetailLine,
    assetDetailTarget,
    assetDetailUsage,
    describedTags,
    generatedDescriptionAction,
    insertAssetDetails,
    staleAssetDetails,
} = await import("../web/h3_asset_details_core.mjs");

const asset = (id, tag_type, description) => ({id, tag_type, description});
// Shapes follow projectAssetReferenceRecords(): pictures support both @tag
// and #tag, semantic anchors only #tag, video/audio only @tag.
const picture = (tag, item, extra = {}) => ({
    tag, token: `@${tag}`, nativeToken: `@${tag}`, supportsSemantic: true,
    kind: "picture", active: true, asset: item, ...extra,
});
const anchor = (tag, item) => ({
    tag, token: `#${tag}`, nativeToken: null, semanticOnly: true,
    kind: "picture", active: true, asset: item,
});
const video = (tag, item, extra = {}) => ({
    tag, token: `@${tag}`, nativeToken: `@${tag}`, kind: "video", active: true,
    asset: item, ...extra,
});
const records = [
    picture("hero", asset("a", "char", "is a  tall woman\nin a red coat.")),
    picture("cabinet", asset("b", "object", "A tall wooden linen cabinet.")),
    picture("cabinet", asset("b", "object", "duplicate")),
    video("hall", asset("c", "scene", "is a dim hallway.")),
    picture("look", asset("d", "style", "is grainy 16mm film."), {active: false}),
    {...video("clip_audio", asset("e", "", "x")), kind: "audio", pairedWith: {}},
    picture("plain", asset("f", "char", "  ")),
];
const entries = assetDetailEntries(records);
assert.deepEqual(entries.map((item) => item.tag), ["hero", "cabinet", "hall", "look"]);
assert.equal(entries[0].description, "is a tall woman in a red coat.");
assert.deepEqual(assetDetailContext(entries).map((item) => item.tag), ["@hero", "@cabinet", "@hall"]);
assert.deepEqual(assetDetailContext(entries)[0], {
    tag: "@hero", tag_type: "char", description: "is a tall woman in a red coat.",
});

assert.equal(assetDetailLine(entries[0]), "@hero is a tall woman in a red coat.");
assert.equal(assetDetailLine(entries[1]), "@cabinet: A tall wooden linen cabinet.");
assert.deepEqual([...describedTags("  @hero is x\nsee @hall here\n@cabinet: y\n#hall[2.50s] is z")],
    ["hero", "cabinet", "hall"]);
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
assert.deepEqual(filled.inserted, ["hero", "cabinet", "hall"]);
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
assert.equal(filled.lines.hall, "@hall is a dim hallway.");
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
// A tag prefix of a longer tag is not a use; tags are case-sensitive.
assert.deepEqual(insertAssetDetails("@heroine waves. @Hero too.", entries).inserted, []);

// --- Semantic references keep their exact syntax (review regression) ---
// The compiler's own reference usage: native @tags plus #tag[t] pairs.
function referenceUsage(text) {
    const native = new Set([...text.matchAll(/(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_-]{0,63})/g)]
        .map((match) => match[1]));
    const semantic = new Set([...text.matchAll(
        /(?<![A-Za-z0-9_])#([A-Za-z][A-Za-z0-9_-]{0,63})(?:\[([0-9]+(?:\.[0-9]+)?)s?\]|(?!\[))(?![A-Za-z0-9_-])/g,
    )].map((match) => `${match[1]}@${match[2] ?? "untimed"}`));
    return {native: [...native].sort(), semantic: [...semantic].sort()};
}
const semanticEntries = assetDetailEntries([
    anchor("hallway", asset("g", "scene", "is a dim hallway.")),
    picture("hero", asset("a", "char", "is a tall woman.")),
]);
// 1. A semantic anchor used only as a timed reference stays timed.
const timed = "subject_definitions:\nA woman.\n\ndetailed_description:\nShe walks #hallway[2.50s] slowly.";
assert.equal(assetDetailUsage(timed, semanticEntries[0]), "#hallway[2.50s]");
const timedResult = insertAssetDetails(timed, semanticEntries);
assert.deepEqual(timedResult.inserted, ["hallway"]);
assert.match(timedResult.text, /^#hallway\[2\.50s\] is a dim hallway\.$/m);
assert.doesNotMatch(timedResult.text, /^#hallway is/m);
assert.deepEqual(referenceUsage(timedResult.text), referenceUsage(timed));
// 2. A picture used only as #hero is described with #hero, not skipped and
// not turned into a native @hero reference.
const asAnchor = "summary: #hero stands still.";
assert.equal(assetDetailUsage(asAnchor, semanticEntries[1]), "#hero");
const anchorResult = insertAssetDetails(asAnchor, semanticEntries);
assert.deepEqual(anchorResult.inserted, ["hero"]);
assert.match(anchorResult.text, /^#hero is a tall woman\.$/m);
assert.deepEqual(referenceUsage(anchorResult.text), referenceUsage(asAnchor));
// A picture used natively still prefers its native token.
assert.equal(assetDetailUsage("@hero and #hero[1s]", semanticEntries[1]), "@hero");
// A semantic-only anchor never gains a native @ form.
assert.equal(assetDetailUsage("@hallway", semanticEntries[0]), null);
// An existing timed definition line counts as already described.
assert.deepEqual(insertAssetDetails(timedResult.text, semanticEntries).inserted, []);

// Stale detection reports only unedited inserted lines whose asset changed.
const changed = assetDetailEntries([
    picture("hero", asset("a", "char", "is a short man.")),
    picture("cabinet", asset("b", "object", "A tall wooden linen cabinet.")),
    video("hall", asset("c", "scene", "is a bright hallway.")),
]);
const insertedLines = {
    hero: "@hero is a tall woman in a red coat.",
    cabinet: "@cabinet: A tall wooden linen cabinet.",
    hall: "@hall is a dim hallway.",
};
const edited = filled.text.replace("@hall is a dim hallway.", "@hall is a dim, narrow hallway.");
const stale = staleAssetDetails(edited, changed, insertedLines);
assert.deepEqual(stale, [{
    tag: "hero",
    oldLine: "@hero is a tall woman in a red coat.",
    newLine: "@hero is a short man.",
}]);
const refreshed = insertAssetDetails(edited, changed, {refresh: stale});
assert.deepEqual(refreshed.refreshed, ["hero"]);
assert.equal(refreshed.lines.hero, "@hero is a short man.");
assert.match(refreshed.text, /^@hero is a short man\.$/m);
assert.match(refreshed.text, /@hall is a dim, narrow hallway\./);
assert.deepEqual(staleAssetDetails(refreshed.text, changed, {
    ...insertedLines, hero: "@hero is a short man.",
}), []);
// A refreshed timed line keeps its timestamp.
const timedStale = staleAssetDetails(timedResult.text, assetDetailEntries([
    anchor("hallway", asset("g", "scene", "is a bright hallway.")),
]), {hallway: timedResult.lines.hallway});
assert.equal(timedStale[0].newLine, "#hallway[2.50s] is a bright hallway.");

// --- A late generated description never overwrites newer edits ---
assert.equal(generatedDescriptionAction({requested: "old", saved: "old", live: "old"}), "apply");
assert.equal(generatedDescriptionAction({requested: "old\r\n", saved: "old", live: null}), "apply");
assert.equal(generatedDescriptionAction({requested: "", saved: "", live: null}), "apply");
// Edited and saved while generating.
assert.equal(generatedDescriptionAction({requested: "old", saved: "newer", live: "newer"}), "review");
// Edited but not yet saved while generating.
assert.equal(generatedDescriptionAction({requested: "old", saved: "old", live: "old, typing"}), "review");

// --- Media access must be enabled explicitly; there is no bypass ---
assert.equal(assetDescribeBlocker({allow_media: true}), "");
for (const config of [{allow_media: false}, {}, {allow_media: "true"}, null]) {
    assert.match(assetDescribeBlocker(config), /Allow Direct API to read reference media/);
}

console.log("H3 asset details: entries, placement, semantic syntax, stale refresh, late results, and media gate pass");
