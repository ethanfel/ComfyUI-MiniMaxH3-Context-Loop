import {parseH3Sections} from "./h3_prompt_schema_core.mjs?v=0.7.6";

// Asset Carousel descriptions are copied into scene prompts only by an
// explicit editor action, as ordinary editable text. The compiler never
// inserts them, so the prompt in the Plan stays exactly what is sent.

export const ASSET_DETAILS_CHANGED_EVENT = "h3-project-asset-details-changed";

const SUBJECT_TARGETS = ["subject_definitions", "integrated_multimodal_description"];
const SETTING_TARGETS = ["detailed_description", "integrated_multimodal_description"];
// A reference token as written in a prompt: @tag, #tag, or #tag[2.50s].
const LEADING_TOKEN = /^[ \t]*([@#])([A-Za-z][A-Za-z0-9_-]{0,63})((?:\[[0-9]+(?:\.[0-9]+)?s?\])?)/;
const DEFINITION_LINE = new RegExp(
    `${LEADING_TOKEN.source}(?:[ \\t]+(?:is|are|has|have)\\b|[ \\t]*:)`, "gm");

function escapedPattern(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function oneLine(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
}

function normalizedText(value) {
    return String(value ?? "").replace(/\r\n?/g, "\n");
}

/** Reference records that carry a Carousel description, one per tag. */
export function assetDetailEntries(records) {
    const entries = [];
    const seen = new Set();
    for (const record of records ?? []) {
        // A video's paired audio shares the video's asset and description.
        if (!record?.asset || record.pairedWith) continue;
        const description = oneLine(record.asset.description);
        const tag = String(record.tag ?? "");
        if (!description || !tag || seen.has(tag)) continue;
        seen.add(tag);
        const native = !record.semanticOnly;
        entries.push({
            tag,
            native,
            semantic: Boolean(record.semanticOnly || record.supportsSemantic),
            token: native ? `@${tag}` : `#${tag}`,
            assetId: String(record.asset.id ?? record.assetId ?? ""),
            tagType: String(record.asset.tag_type ?? ""),
            description,
            active: record.active !== false,
        });
    }
    return entries;
}

/** Compact descriptions for the prompt optimizer's request context. */
export function assetDetailContext(entries) {
    return (entries ?? []).filter((entry) => entry.active).map((entry) => ({
        tag: entry.token,
        ...(entry.tagType ? {tag_type: entry.tagType} : {}),
        description: entry.description,
    }));
}

/**
 * The exact reference text this prompt already uses for an asset, or null.
 * Inserted lines reuse it verbatim so they never add a new kind of use: a
 * timed semantic anchor stays timed and a picture used as #tag stays #tag.
 * Matching follows the compiler's case-sensitive @tag / #tag[seconds] rules.
 */
export function assetDetailUsage(text, entry) {
    const prompt = String(text ?? "");
    const tag = escapedPattern(entry.tag);
    if (entry.native && new RegExp(
        `(?<![A-Za-z0-9_])@${tag}(?![A-Za-z0-9_-])`).test(prompt)) return `@${entry.tag}`;
    if (entry.semantic) {
        const match = new RegExp(
            `(?<![A-Za-z0-9_])#${tag}(?:\\[[0-9]+(?:\\.[0-9]+)?s?\\]|(?!\\[))(?![A-Za-z0-9_-])`,
        ).exec(prompt);
        if (match) return match[0];
    }
    return null;
}

/** The editable line for one asset, led by the given reference text. */
export function assetDetailLine(entry, token = entry.token) {
    const description = oneLine(entry.description);
    return /^(?:is|are|has|have)\b/i.test(description)
        ? `${token} ${description}`
        : `${token}: ${description}`;
}

/** Tag names that already open a definition line, in any reference form. */
export function describedTags(text) {
    const result = new Set();
    for (const match of String(text ?? "").matchAll(DEFINITION_LINE)) result.add(match[2]);
    return result;
}

export function assetDetailTarget(tagType, sectionNames) {
    const names = new Set(sectionNames ?? []);
    const order = ["scene", "style"].includes(tagType) ? SETTING_TARGETS : SUBJECT_TARGETS;
    return order.find((name) => names.has(name)) ?? null;
}

function insertIntoSection(text, record, lines) {
    let at = record.end;
    while (at > record.contentStart && /\s/.test(text[at - 1])) at -= 1;
    const block = lines.join("\n");
    if (at > record.contentStart) {
        return text.slice(0, at) + "\n" + block + text.slice(at);
    }
    // Empty section: keep the header on its own line and the next one apart.
    const before = record.contentStart > record.headerEnd ? "" : "\n";
    const rest = text.slice(record.contentStart);
    const after = rest && !/^\s*\n/.test(rest) ? "\n\n" : "";
    return text.slice(0, record.contentStart) + before + block + after + rest;
}

/**
 * Insert a definition line for every described asset this prompt uses and
 * does not already define, and refresh lines whose description changed.
 *
 * ``refresh`` lists ``{tag, oldLine, newLine}`` from staleAssetDetails().
 * Placement follows the H3 sections when present: char/object (and custom
 * types) go to subject_definitions, scene/style to the detailed or main
 * description. Without H3 sections the lines open the prompt. Returns the
 * new text plus the inserted/refreshed tags and the exact line for each.
 */
export function insertAssetDetails(text, entries, {refresh = []} = {}) {
    let next = String(text ?? "");
    const refreshed = [];
    const lines = {};
    for (const item of refresh) {
        const rows = next.split("\n");
        const index = rows.findIndex((line) => line.trim() === item.oldLine);
        if (index < 0) continue;
        rows[index] = rows[index].match(/^[ \t]*/)[0] + item.newLine;
        next = rows.join("\n");
        refreshed.push(item.tag);
        lines[item.tag] = item.newLine;
    }
    const described = describedTags(next);
    const wanted = [];
    for (const entry of entries ?? []) {
        if (described.has(entry.tag)) continue;
        const usage = assetDetailUsage(next, entry);
        if (usage) wanted.push({entry, line: assetDetailLine(entry, usage)});
    }
    if (!wanted.length) return {text: next, inserted: [], refreshed, lines};
    const sections = parseH3Sections(next);
    const groups = new Map();
    for (const {entry, line} of wanted) {
        const target = assetDetailTarget(entry.tagType, sections.map((item) => item.name));
        if (!groups.has(target)) groups.set(target, []);
        groups.get(target).push(line);
        lines[entry.tag] = line;
    }
    // Insert from the end of the prompt backwards so earlier offsets hold.
    const placed = [...groups.entries()]
        .filter(([target]) => target)
        .map(([target, rows]) => [sections.find((item) => item.name === target), rows])
        .sort((left, right) => right[0].start - left[0].start);
    for (const [record, rows] of placed) next = insertIntoSection(next, record, rows);
    const loose = groups.get(null);
    if (loose?.length) {
        next = next.trim() ? `${loose.join("\n")}\n\n${next}` : loose.join("\n");
    }
    return {text: next, inserted: wanted.map(({entry}) => entry.tag), refreshed, lines};
}

/**
 * Lines this editor inserted that are still unedited in the prompt but no
 * longer match the asset's current description. ``insertedLines`` maps a tag
 * to the exact line inserted earlier; the refreshed line keeps that line's
 * reference text. Lines the user edited are never reported.
 */
export function staleAssetDetails(text, entries, insertedLines) {
    const present = new Set(String(text ?? "").split("\n").map((line) => line.trim()));
    const result = [];
    for (const entry of entries ?? []) {
        const oldLine = insertedLines?.[entry.tag];
        if (!oldLine || !present.has(oldLine)) continue;
        const lead = LEADING_TOKEN.exec(oldLine);
        const token = lead ? `${lead[1]}${lead[2]}${lead[3]}` : entry.token;
        const newLine = assetDetailLine(entry, token);
        if (newLine !== oldLine) result.push({tag: entry.tag, oldLine, newLine});
    }
    return result;
}

/**
 * Whether a finished generation may be saved directly. ``requested`` is the
 * description when Generate was pressed; ``saved`` is the catalog's current
 * value and ``live`` the editor field (null when not shown). Any difference
 * means the user changed it meanwhile, so the result needs explicit review.
 */
export function generatedDescriptionAction({requested, saved, live = null}) {
    const base = normalizedText(requested).trim();
    if (normalizedText(saved).trim() !== base) return "review";
    if (live !== null && normalizedText(live).trim() !== base) return "review";
    return "apply";
}

/** Why Generate description may not run, or "" when media may be sent. */
export function assetDescribeBlocker(config) {
    return config?.allow_media === true ? "" : (
        "Generate description sends this asset's media to the Direct API "
        + "provider, and \"Allow Direct API to read reference media\" is off. "
        + "Enable it in Settings → MiniMax H3 Context Loop → Prompt optimizer "
        + "first; nothing was sent.");
}
