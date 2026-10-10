import {parseH3Sections} from "./h3_prompt_schema_core.mjs?v=0.7.6";

// Asset Carousel descriptions are copied into scene prompts only by an
// explicit editor action, as ordinary editable text. The compiler never
// inserts them, so the prompt in the Plan stays exactly what is sent.

export const ASSET_DETAILS_CHANGED_EVENT = "h3-project-asset-details-changed";

const SUBJECT_TARGETS = ["subject_definitions", "integrated_multimodal_description"];
const SETTING_TARGETS = ["detailed_description", "integrated_multimodal_description"];

function escapedPattern(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function tokenPattern(token, flags = "") {
    return new RegExp(
        `(?<![A-Za-z0-9_])${escapedPattern(token)}(?![A-Za-z0-9_-])`, flags);
}

function oneLine(value) {
    return String(value ?? "").replace(/\s+/g, " ").trim();
}

/** Reference records that carry a Carousel description, one per token. */
export function assetDetailEntries(records) {
    const entries = [];
    const seen = new Set();
    for (const record of records ?? []) {
        // A video's paired audio shares the video's asset and description.
        if (!record?.asset || record.pairedWith) continue;
        const description = oneLine(record.asset.description);
        const token = String(record.token ?? "");
        if (!description || !token || seen.has(token)) continue;
        seen.add(token);
        entries.push({
            token,
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

/** The editable line inserted for one asset, e.g. "@cabinet is a tall …". */
export function assetDetailLine(entry) {
    const description = oneLine(entry.description);
    return /^(?:is|are|has|have)\b/i.test(description)
        ? `${entry.token} ${description}`
        : `${entry.token}: ${description}`;
}

/** Tokens that already open a definition line ("@tag is …" / "@tag: …"). */
export function describedTokens(text) {
    const result = new Set();
    for (const match of String(text ?? "").matchAll(
        /^[ \t]*([@#][A-Za-z][A-Za-z0-9_-]{0,63})(?:[ \t]+(?:is|are|has|have)\b|[ \t]*:)/gim,
    )) result.add(match[1]);
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
 * ``refresh`` lists ``{oldLine, newLine}`` pairs from staleAssetDetails().
 * Placement follows the H3 sections when present: char/object (and custom
 * types) go to subject_definitions, scene/style to the detailed or main
 * description. Without H3 sections the lines open the prompt.
 */
export function insertAssetDetails(text, entries, {refresh = []} = {}) {
    let next = String(text ?? "");
    const refreshed = [];
    for (const item of refresh) {
        const lines = next.split("\n");
        const index = lines.findIndex((line) => line.trim() === item.oldLine);
        if (index < 0) continue;
        const indent = lines[index].match(/^[ \t]*/)[0];
        lines[index] = indent + item.newLine;
        next = lines.join("\n");
        refreshed.push(item.token);
    }
    const described = describedTokens(next);
    const wanted = (entries ?? []).filter((entry) =>
        !described.has(entry.token) && tokenPattern(entry.token).test(next));
    if (!wanted.length) return {text: next, inserted: [], refreshed};
    const sections = parseH3Sections(next);
    const groups = new Map();
    for (const entry of wanted) {
        const target = assetDetailTarget(entry.tagType, sections.map((item) => item.name));
        if (!groups.has(target)) groups.set(target, []);
        groups.get(target).push(assetDetailLine(entry));
    }
    // Insert from the end of the prompt backwards so earlier offsets hold.
    const placed = [...groups.entries()]
        .filter(([target]) => target)
        .map(([target, lines]) => [sections.find((item) => item.name === target), lines])
        .sort((left, right) => right[0].start - left[0].start);
    for (const [record, lines] of placed) next = insertIntoSection(next, record, lines);
    const loose = groups.get(null);
    if (loose?.length) {
        next = next.trim() ? `${loose.join("\n")}\n\n${next}` : loose.join("\n");
    }
    return {text: next, inserted: wanted.map((entry) => entry.token), refreshed};
}

/**
 * Lines this editor inserted that are still unedited in the prompt but no
 * longer match the asset's current description. ``insertedLines`` maps a
 * token to the exact line inserted earlier; lines the user edited are never
 * reported, so their wording is never overwritten.
 */
export function staleAssetDetails(text, entries, insertedLines) {
    const lines = new Set(String(text ?? "").split("\n").map((line) => line.trim()));
    const result = [];
    for (const entry of entries ?? []) {
        const oldLine = insertedLines?.[entry.token];
        if (!oldLine || !lines.has(oldLine)) continue;
        const newLine = assetDetailLine(entry);
        if (newLine !== oldLine) result.push({token: entry.token, oldLine, newLine});
    }
    return result;
}
