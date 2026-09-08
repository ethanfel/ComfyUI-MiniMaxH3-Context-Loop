import {H3_ALL_SECTIONS} from "./h3_prompt_schema_core.mjs?v=0.6.8";

export const RICH_PROMPT_GUIDES = Object.freeze([
    {id: "auto", label: "Auto · H3 mode"},
    {id: "general", label: "H3 General"},
    {id: "continuity", label: "Chain continuity"},
    {id: "music_video", label: "Music video"},
    {id: "dialogue", label: "Dialogue + voice"},
    {id: "brand_promo", label: "Brand / product"},
    {id: "animation", label: "3D animation"},
    {id: "handdrawn", label: "Hand-drawn + live"},
    {id: "paper", label: "Paper / stop motion"},
]);

const GUIDE_RULES = Object.freeze({
    auto: "Choose the most appropriate H3 treatment from the prompt and connected conditioning mode.",
    general: "Prioritize concrete composition, action, camera, performance, lighting, synchronized sound, and an executable ending state.",
    continuity: "Prioritize a physically continuous opening from the previous scene and a visible unfinished handoff into the next scene. Do not add a cut unless requested.",
    music_video: "Prioritize beat-aware performance, exact lyric/dialogue timing, camera rhythm, readable performer identity, and explicit diegetic versus non-diegetic audio.",
    dialogue: "Prioritize speaker identity, vocal-reference mapping, exact <d> dialogue, language, delivery, mouth timing, ambience, and separation from soundtrack references.",
    brand_promo: "Prioritize verified product appearance, a clear visual selling point, restrained readable text, precise beat structure, and no invented brand claims.",
    animation: "Prioritize character silhouettes, readable staging, expressive poses, material/style continuity, motivated camera movement, and clean action beats.",
    handdrawn: "Prioritize physical contact between live action and drawn elements, one continuous transformation, tactile line behavior, and camera reaction lag.",
    paper: "Prioritize tactile paper materials, layered depth, handmade shadows, stop-motion cadence, practical transitions, and restrained paper sound effects.",
});

export function normalizeRichGuide(value) {
    const id = String(value ?? "auto");
    return RICH_PROMPT_GUIDES.some((item) => item.id === id) ? id : "auto";
}

export function richGenerationMode(referenceMode) {
    if (["tagged", "scheduled", "native"].includes(referenceMode)) return "Ref2VA";
    if (referenceMode === "native_keyframes") return "I2VA/FL2VA";
    return "H3 chain scene";
}

export function richGuideInstruction(guide, generationMode) {
    const selected = normalizeRichGuide(guide);
    const mode = String(generationMode || "H3 chain scene");
    const schema = mode === "Ref2VA"
        ? "If the source already uses the Ref2VA six-section format, preserve its order: subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music. Introduce that complete format only when the user explicitly asks for a full H3 rewrite. Keep every reference label, @alias, bare #picture semantic visual, and #picture[timestamp] semantic anchor stable."
        : "If the source already uses the base H3 format, preserve integrated_multimodal_description, overall_soundscape, and non_diegetic_music. Introduce the complete format or keyframe-alignment sentence only when the user explicitly asks for a full H3 rewrite; do not force headings onto a compact prompt.";
    return [
        `Return a complete replacement string for the current MiniMax H3 ${mode} scene, but change only what the request requires.`,
        schema,
        GUIDE_RULES[selected],
        "Preserve exact dialogue and lyrics inside <d> tags, their language, explicit timing, subject identity, wardrobe, camera continuity, and all valid media references unless the source explicitly asks to change them.",
        "Connected references prove only that an asset and its media type are available. Do not invent image content, motion, lyrics, voice, timbre, or an audio copy/reference role. Use only facts stated in the prompt or shared/adjacent context, or media actually attached by the configured backend and directly observable to its model; otherwise preserve the reference token without elaboration.",
        "Keep all described events and cut times inside the supplied scene duration.",
        "Return a complete replacement rather than commentary, a patch, or an ellipsis.",
    ].join(" ");
}

function recordTokens(record) {
    const tokens = [record?.token, record?.label];
    const explicitTag = String(record?.tag ?? "")
        .trim().replace(/^[@#]+/, "");
    const tokenTag = /^@([A-Za-z][A-Za-z0-9_-]{0,63})$/
        .exec(String(record?.nativeToken ?? record?.token ?? "").trim())?.[1];
    const canonicalTag = explicitTag || tokenTag;
    if (canonicalTag) tokens.push(`#${canonicalTag}`);
    return tokens
        .map((value) => String(value ?? "").trim())
        .filter(Boolean);
}

export function referenceRecordMap(records) {
    const map = new Map();
    for (const record of Array.isArray(records) ? records : []) {
        for (const token of recordTokens(record)) map.set(token, record);
    }
    return map;
}

// Keep this alias grammar aligned with chain_nodes.py's reference compiler.
// In particular, sentence punctuation such as the period in "Use @hero."
// belongs to the surrounding prompt, not to the decorated reference token.
const RICH_SECTION_ALTERNATION = H3_ALL_SECTIONS.join("|");
const RICH_TOKEN_PATTERN = new RegExp(
    `^(${RICH_SECTION_ALTERNATION}):|`
    + "((?<![A-Za-z0-9_])#[A-Za-z][A-Za-z0-9_-]{0,63}(?:\\[[0-9]+(?:\\.[0-9]+)?s?\\]|(?!\\[))(?![A-Za-z0-9_-])"
    + "|(?<![A-Za-z0-9_])@[A-Za-z][A-Za-z0-9_-]{0,63}(?![A-Za-z0-9_-])"
    + "|<(?:Picture|Video|Audio|Subject)\\s+\\d+>|<\\/?d>|<scenetrans>|<cutoff>|<\\|(?:cutoff|lyrics_start|lyrics_end|caption_start|caption_end)\\|>"
    + "|(?<![A-Za-z0-9_])\\(S\\d+(?:,S\\d+)*\\))",
    "gim",
);

export function tokenizeRichPrompt(text, records = []) {
    const source = String(text ?? "");
    const recordMap = referenceRecordMap(records);
    const parts = [];
    let offset = 0;
    for (const match of source.matchAll(RICH_TOKEN_PATTERN)) {
        const index = match.index ?? 0;
        if (index > offset) parts.push({type: "text", text: source.slice(offset, index)});
        const token = match[0];
        const lower = token.toLowerCase();
        const section = lower.endsWith(":")
            ? H3_ALL_SECTIONS.find((item) => `${item}:` === lower) : null;
        const semanticMatch = /^#([a-z][a-z0-9_-]{0,63})(?:\[([0-9]+(?:\.[0-9]+)?)s?\])?$/i.exec(token);
        // A semantic-only asset has no native @tag token. Resolve bare #tag
        // and #tag[time] through the timestamp-independent registered key.
        const recordKey = semanticMatch ? `#${semanticMatch[1]}` : token;
        const record = recordMap.get(recordKey) ?? null;
        if (section) {
            parts.push({type:"section", kind:"section", text:token, section, unresolved:false});
        } else if (record || lower.startsWith("@") || semanticMatch || /^<(?:picture|video|audio)\s/i.test(lower)) {
            const namedKind = lower.startsWith("<picture") ? "picture"
                : lower.startsWith("<video") ? "video"
                    : lower.startsWith("<audio") ? "audio" : null;
            const semanticValid = !semanticMatch || record?.kind === "picture";
            parts.push({
                type: "reference",
                text: token,
                kind: record?.kind ?? namedKind ?? "unknown",
                record,
                unresolved: !record || !semanticValid,
                semantic: Boolean(semanticMatch),
                timestamp: semanticMatch?.[2] !== undefined
                    ? Number(semanticMatch[2]) : null,
            });
        } else if (lower.startsWith("<subject")) {
            parts.push({type: "subject", text: token});
        } else if (lower === "<scenetrans>" || lower === "<cutoff>" || lower === "<|cutoff|>") {
            parts.push({type:"flow", kind:"flow", text:token, unresolved:false});
        } else if (/^<\|(?:lyrics|caption)_/.test(lower)) {
            parts.push({type:"dialogue", text:token});
        } else if (lower.startsWith("(s")) {
            parts.push({type:"speaker", kind:"speaker", text:token, unresolved:false});
        } else {
            parts.push({type: "dialogue", text: token});
        }
        offset = index + token.length;
    }
    if (offset < source.length) parts.push({type: "text", text: source.slice(offset)});
    return parts;
}

export function optimizerSource(currentPrompt, previous = null) {
    const current = String(currentPrompt ?? "");
    if (previous && current === String(previous.result ?? "")) {
        return String(previous.source ?? current);
    }
    return current;
}

function undoInputGroup(inputType) {
    const type = String(inputType ?? "");
    if (type === "insertText") return "typing";
    if (type === "deleteContentBackward") return "backspace";
    if (type === "deleteContentForward") return "delete";
    return "";
}

export function promptUndoDirection(event) {
    if (!event || event.altKey || !(event.ctrlKey || event.metaKey)) return null;
    const key = String(event.key ?? "").toLowerCase();
    if (key === "z") return event.shiftKey ? "redo" : "undo";
    if (key === "y" && !event.shiftKey) return "redo";
    return null;
}

/** Text-level undo survives contenteditable token decoration, which replaces
 * DOM children and therefore discards the browser's native undo manager. */
export class PromptUndoHistory {
    constructor(initialText = "", {limit = 100, coalesceMs = 750} = {}) {
        this.limit = Math.max(1, Math.trunc(Number(limit) || 100));
        this.coalesceMs = Math.max(0, Number(coalesceMs) || 0);
        this.reset(initialText);
    }

    reset(text = "") {
        this.current = String(text ?? "");
        this.undoStack = [];
        this.redoStack = [];
        this.lastGroup = "";
        this.lastTime = 0;
        return this.current;
    }

    align(text = "") {
        const value = String(text ?? "");
        if (value === this.current) return false;
        this.reset(value);
        return true;
    }

    record(text, {inputType = "", now = Date.now()} = {}) {
        const value = String(text ?? "");
        if (value === this.current) return false;
        const group = undoInputGroup(inputType);
        const timestamp = Number(now) || 0;
        const coalesced = Boolean(
            group && group === this.lastGroup
            && timestamp - this.lastTime >= 0
            && timestamp - this.lastTime <= this.coalesceMs
        );
        if (!coalesced) {
            this.undoStack.push(this.current);
            if (this.undoStack.length > this.limit) this.undoStack.shift();
        }
        this.current = value;
        this.redoStack = [];
        this.lastGroup = group;
        this.lastTime = timestamp;
        return true;
    }

    undo() {
        if (!this.undoStack.length) return null;
        this.redoStack.push(this.current);
        this.current = this.undoStack.pop();
        this.lastGroup = "";
        this.lastTime = 0;
        return this.current;
    }

    redo() {
        if (!this.redoStack.length) return null;
        this.undoStack.push(this.current);
        this.current = this.redoStack.pop();
        this.lastGroup = "";
        this.lastTime = 0;
        return this.current;
    }
}
