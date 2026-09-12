// Pure data helpers for the H3 Chain Plan editor. Keep this module free of
// ComfyUI/browser dependencies so its timing and serialization can be tested.

export const FPS = 24;
export const MAX_SHOTS = 128;
export const MAX_CHAPTERS = MAX_SHOTS;
export const MAX_H3_FRAMES = 3592;
export const MAX_SEED = 18446744073709551615n;
export const CONTINUATION_MODES = Object.freeze([
    "guide", "tone_carry_guide", "latent_guide", "tapered_guide",
    "masked_av", "tapered_av", "feathered_av", "audio_feathered_av",
    "drift_control_av", "color_stable_drift_av",
]);
export const CONTEXT_SPATIAL_PROXY_MODES = Object.freeze([
    "off", "rgb_5_6", "latent_5_6",
]);
export const SCENE_LORA_ROUTES = Object.freeze([
    "base", ..."abcdefghijklmnopqrstuvwxyz",
]);
export const SCENE_PROMPT_SEED_MODES = Object.freeze([
    "inherit", "fixed", "randomize",
]);
const RETIRED_CONTINUATION_MODES = Object.freeze({
    feathered_av_rgb: "feathered_av",
});
export const H3_CONTEXT_LENGTHS = Object.freeze([
    1, 5, 22, 39, 56, 73, 90, 107, 124,
    141, 158, 175, 192, 209, 226, 243,
]);
export const AV_CONTEXT_LENGTHS = Object.freeze([39, 90, 141, 192, 243]);
export const VIDEO_ONLY_AV_CONTEXT_LENGTHS = Object.freeze(
    H3_CONTEXT_LENGTHS.filter((value) => value >= 5),
);
export const AUTO_SCENE_COLORS = Object.freeze([
    "#6ea8fe", "#ffb86b", "#63d69f", "#c493ff",
    "#ff7fa6", "#55d6e8", "#e6cb65", "#ff7878",
    "#83c5ff", "#b7db68", "#d991ff", "#72d4c2",
]);

export function automaticSceneColor(index) {
    const ordinal = Number.isFinite(Number(index)) ? Math.trunc(Number(index)) : 0;
    return AUTO_SCENE_COLORS[((ordinal % AUTO_SCENE_COLORS.length)
        + AUTO_SCENE_COLORS.length) % AUTO_SCENE_COLORS.length];
}

function normalizedSeedInteger(value, label = "Seed") {
    try {
        const seed = typeof value === "bigint" ? value : BigInt(value ?? 0);
        if (seed < 0n || seed > MAX_SEED) throw new Error();
        return seed;
    } catch (_error) {
        throw new Error(`${label} must be an unsigned 64-bit integer.`);
    }
}

const SHA256_CONSTANTS = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotateRight(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
}

function sha256Fallback(bytes) {
    const length = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(length);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const bitLength = BigInt(bytes.length) * 8n;
    const view = new DataView(padded.buffer);
    view.setUint32(length - 8, Number((bitLength >> 32n) & 0xffffffffn));
    view.setUint32(length - 4, Number(bitLength & 0xffffffffn));
    const state = new Uint32Array([
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]);
    const words = new Uint32Array(64);
    for (let offset = 0; offset < length; offset += 64) {
        for (let index = 0; index < 16; index += 1) {
            words[index] = view.getUint32(offset + index * 4);
        }
        for (let index = 16; index < 64; index += 1) {
            const left = words[index - 15];
            const right = words[index - 2];
            const sigma0 = rotateRight(left, 7) ^ rotateRight(left, 18) ^ (left >>> 3);
            const sigma1 = rotateRight(right, 17) ^ rotateRight(right, 19) ^ (right >>> 10);
            words[index] = (words[index - 16] + sigma0
                + words[index - 7] + sigma1) >>> 0;
        }
        let [a, b, c, d, e, f, g, h] = state;
        for (let index = 0; index < 64; index += 1) {
            const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
            const choose = (e & f) ^ (~e & g);
            const temporary1 = (h + sum1 + choose
                + SHA256_CONSTANTS[index] + words[index]) >>> 0;
            const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
            const majority = (a & b) ^ (a & c) ^ (b & c);
            const temporary2 = (sum0 + majority) >>> 0;
            h = g;
            g = f;
            f = e;
            e = (d + temporary1) >>> 0;
            d = c;
            c = b;
            b = a;
            a = (temporary1 + temporary2) >>> 0;
        }
        for (const [index, value] of [a, b, c, d, e, f, g, h].entries()) {
            state[index] = (state[index] + value) >>> 0;
        }
    }
    const digest = new Uint8Array(32);
    const digestView = new DataView(digest.buffer);
    state.forEach((value, index) => digestView.setUint32(index * 4, value));
    return digest;
}

export async function derivedSceneSeed(
    baseSeed, index, shotId, cryptoSource = globalThis.crypto,
) {
    const seedBase = normalizedSeedInteger(baseSeed, "Base seed");
    const ordinal = Number(index);
    if (!Number.isInteger(ordinal) || ordinal < 1) {
        throw new Error("Scene index must be a positive integer.");
    }
    const payload = `${seedBase}:${ordinal}:${String(shotId)}`;
    const encoded = new TextEncoder().encode(payload);
    const digest = typeof cryptoSource?.subtle?.digest === "function"
        ? new Uint8Array(await cryptoSource.subtle.digest("SHA-256", encoded))
        : sha256Fallback(encoded);
    const bytes = digest.subarray(0, 8);
    let result = 0n;
    for (const byte of bytes) result = (result << 8n) | BigInt(byte);
    return result.toString();
}

export function randomSceneSeed(randomSource = globalThis.crypto) {
    if (typeof randomSource?.getRandomValues !== "function") {
        throw new Error("Secure browser randomness is unavailable.");
    }
    const words = new Uint32Array(2);
    randomSource.getRandomValues(words);
    return ((BigInt(words[0]) << 32n) | BigInt(words[1])).toString();
}

export function scenePromptSeedMode(shot = {}) {
    const raw = shot?.prompt_seed_mode;
    if (raw == null || String(raw).trim() === "") {
        return Object.hasOwn(shot ?? {}, "prompt_seed") ? "fixed" : "inherit";
    }
    const mode = String(raw).trim().toLowerCase();
    if (!SCENE_PROMPT_SEED_MODES.includes(mode)) {
        throw new Error(
            `Prompt seed mode must be one of ${SCENE_PROMPT_SEED_MODES.join(", ")}.`,
        );
    }
    return mode;
}

export function setScenePromptSeedMode(
    shot, requested, randomSource = globalThis.crypto,
) {
    const mode = String(requested ?? "inherit").trim().toLowerCase();
    if (!SCENE_PROMPT_SEED_MODES.includes(mode)) {
        throw new Error(
            `Prompt seed mode must be one of ${SCENE_PROMPT_SEED_MODES.join(", ")}.`,
        );
    }
    if (mode === "inherit") {
        delete shot.prompt_seed_mode;
        delete shot.prompt_seed;
    } else if (mode === "randomize") {
        shot.prompt_seed_mode = "randomize";
        delete shot.prompt_seed;
    } else {
        shot.prompt_seed_mode = "fixed";
        if (!Object.hasOwn(shot, "prompt_seed")) {
            shot.prompt_seed = randomSceneSeed(randomSource);
        } else {
            shot.prompt_seed = normalizedSeedInteger(
                shot.prompt_seed, "Prompt seed",
            ).toString();
        }
    }
    return mode;
}

function clone(value) {
    return JSON.parse(JSON.stringify(value));
}

function protectSeedIntegers(source) {
    // JSON.parse rounds integers above 2^53. The Python node accepts seed
    // strings, so quote numeric `seed` values before parsing and preserve all
    // uint64 digits exactly. This scanner only touches actual JSON object keys,
    // never text that happens to contain `\"seed\": 123` inside a prompt.
    let output = "";
    let index = 0;
    while (index < source.length) {
        if (source[index] !== '"') {
            output += source[index++];
            continue;
        }

        const start = index;
        index += 1;
        while (index < source.length) {
            if (source[index] === "\\") {
                index += 2;
                continue;
            }
            if (source[index] === '"') {
                index += 1;
                break;
            }
            index += 1;
        }
        const token = source.slice(start, index);
        output += token;

        let key;
        try {
            key = JSON.parse(token);
        } catch (_error) {
            continue;
        }
        if (key !== "seed" && key !== "prompt_seed") {
            continue;
        }

        let cursor = index;
        while (/\s/.test(source[cursor] || "")) cursor += 1;
        if (source[cursor] !== ":") continue;
        cursor += 1;
        while (/\s/.test(source[cursor] || "")) cursor += 1;
        const match = source.slice(cursor).match(/^-?\d+(?=\s*[,}])/);
        if (!match) continue;

        output += source.slice(index, cursor);
        output += JSON.stringify(match[0]);
        index = cursor + match[0].length;
    }
    return output;
}

export function promptValueToText(value, label = "prompt") {
    if (Array.isArray(value)) {
        if (!value.every((line) => typeof line === "string")) {
            throw new Error(`${label} line arrays may contain only strings.`);
        }
        return value.join("\n");
    }
    if (value === undefined || value === null) return "";
    return String(value);
}

export function promptTextToLines(value) {
    return String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
}

export function parsePlanJson(source) {
    let raw;
    try {
        raw = JSON.parse(protectSeedIntegers(String(source ?? "")));
    } catch (error) {
        throw new Error(`Invalid plan JSON: ${error.message}`);
    }
    if (Array.isArray(raw)) raw = {shots: raw};
    if (!raw || typeof raw !== "object") {
        throw new Error("The plan must be an object or a bare list of scenes.");
    }
    if (!Array.isArray(raw.shots) || raw.shots.length === 0) {
        throw new Error("The plan needs at least one scene in shots.");
    }
    if (raw.shots.length > MAX_SHOTS) {
        throw new Error(`The plan supports at most ${MAX_SHOTS} scenes.`);
    }

    const plan = clone(raw);
    const hasTopLevelDefaults = Object.hasOwn(plan, "duration_seconds")
        || Object.hasOwn(plan, "steps");
    if (hasTopLevelDefaults) {
        const defaults = plan.defaults
            && typeof plan.defaults === "object"
            && !Array.isArray(plan.defaults)
            ? {...plan.defaults} : {};
        // Accept the common human-authored shorthand and serialize it back in
        // the canonical shape. Explicit defaults.* values win when both forms
        // are present.
        if (!Object.hasOwn(defaults, "duration_seconds")
            && Object.hasOwn(plan, "duration_seconds")) {
            defaults.duration_seconds = plan.duration_seconds;
        }
        if (!Object.hasOwn(defaults, "steps") && Object.hasOwn(plan, "steps")) {
            defaults.steps = plan.steps;
        }
        plan.defaults = defaults;
        delete plan.duration_seconds;
        delete plan.steps;
    }
    plan.shots = plan.shots.map((shot, offset) => {
        if (typeof shot === "string") {
            return {prompt: promptTextToLines(shot)};
        }
        if (!shot || typeof shot !== "object" || Array.isArray(shot)) {
            throw new Error(`Scene ${offset + 1} must be an object or prompt string.`);
        }
        const normalized = {...shot};
        normalized.prompt = promptTextToLines(
            promptValueToText(shot.prompt, `Scene ${offset + 1} prompt`),
        );
        if (Object.hasOwn(normalized, "lora_route")) {
            const route = sceneLoRARoute(normalized);
            if (route === "base") delete normalized.lora_route;
            else normalized.lora_route = route;
        }
        if (Object.hasOwn(normalized, "prompt_seed_mode")
                || Object.hasOwn(normalized, "prompt_seed")) {
            const mode = scenePromptSeedMode(normalized);
            if (mode === "inherit") {
                delete normalized.prompt_seed_mode;
                delete normalized.prompt_seed;
            } else if (mode === "randomize") {
                normalized.prompt_seed_mode = "randomize";
                delete normalized.prompt_seed;
            } else {
                if (!Object.hasOwn(normalized, "prompt_seed")) {
                    throw new Error(
                        `Scene ${offset + 1} fixed prompt seed needs prompt_seed.`,
                    );
                }
                normalized.prompt_seed_mode = "fixed";
                normalized.prompt_seed = normalizedSeedInteger(
                    normalized.prompt_seed,
                    `Scene ${offset + 1} prompt seed`,
                ).toString();
            }
        }
        return normalized;
    });

    const shotIds = plan.shots.map((shot, offset) => safeShotId(
        shot?.id,
        `clip_${String(offset + 1).padStart(4, "0")}`,
    ));
    const shotIdSet = new Set(shotIds);
    if (Object.hasOwn(plan, "chapters")) {
        if (!Array.isArray(plan.chapters)) {
            throw new Error("chapters must be a list.");
        }
        if (plan.chapters.length > MAX_CHAPTERS) {
            throw new Error(`The plan supports at most ${MAX_CHAPTERS} chapters.`);
        }
        const usedIds = new Set();
        const usedStarts = new Set();
        plan.chapters = plan.chapters.map((chapter, offset) => {
            if (!chapter || typeof chapter !== "object" || Array.isArray(chapter)) {
                throw new Error(`Chapter ${offset + 1} must be an object.`);
            }
            const fallbackId = `chapter_${String(offset + 1).padStart(2, "0")}`;
            const id = safeChapterId(chapter.id, fallbackId);
            if (usedIds.has(id)) throw new Error(`Duplicate chapter id: ${id}.`);
            usedIds.add(id);
            const startSceneId = safeShotId(
                chapter.start_scene_id,
                shotIds[Math.min(offset, shotIds.length - 1)],
            );
            if (!shotIdSet.has(startSceneId)) {
                throw new Error(
                    `Chapter ${offset + 1} starts at missing scene ${startSceneId}.`,
                );
            }
            if (usedStarts.has(startSceneId)) {
                throw new Error(`Only one chapter may start at scene ${startSceneId}.`);
            }
            usedStarts.add(startSceneId);
            return {
                id,
                title: String(chapter.title ?? `Chapter ${offset + 1}`).trim()
                    .slice(0, 160) || `Chapter ${offset + 1}`,
                start_scene_id: startSceneId,
                text: promptValueToText(chapter.text ?? chapter.notes ?? "", `Chapter ${offset + 1} text`),
                ...(chapter.resolution != null
                    ? {resolution:normalizeChapterResolution(chapter.resolution)} : {}),
            };
        }).sort((left, right) => (
            shotIds.indexOf(left.start_scene_id) - shotIds.indexOf(right.start_scene_id)
        ));
        if (plan.chapters.length === 0) delete plan.chapters;
    }

    if (Object.hasOwn(plan, "prompt_prefix")) {
        plan.prompt_prefix = promptTextToLines(
            promptValueToText(plan.prompt_prefix, "prompt_prefix"),
        );
    } else if (Object.hasOwn(plan, "global_prompt")) {
        plan.global_prompt = promptTextToLines(
            promptValueToText(plan.global_prompt, "global_prompt"),
        );
    }
    return plan;
}

export function planToJson(plan) {
    return JSON.stringify(plan, null, 2);
}

export function sharedPrompt(plan) {
    const key = Object.hasOwn(plan, "prompt_prefix")
        ? "prompt_prefix"
        : Object.hasOwn(plan, "global_prompt") ? "global_prompt" : "prompt_prefix";
    return {key, text: promptValueToText(plan[key] ?? "", key)};
}

export function setSharedPrompt(plan, text) {
    const current = sharedPrompt(plan);
    plan[current.key] = promptTextToLines(text);
}

export function safeShotId(value, fallback) {
    let text = String(value ?? "").trim().replace(/[^A-Za-z0-9._-]+/g, "_");
    text = text.replace(/^[._-]+|[._-]+$/g, "");
    return (text || fallback).slice(0, 96);
}

export function renamePlanShot(plan, index, requestedId) {
    const shots = Array.isArray(plan?.shots) ? plan.shots : [];
    const position = Number(index);
    if (!Number.isInteger(position) || position < 0 || position >= shots.length) {
        throw new Error("Scene rename target is outside the Plan.");
    }
    const shot = shots[position];
    const fallback = `clip_${String(position + 1).padStart(4, "0")}`;
    const previousId = safeShotId(shot?.id, fallback);
    const nextId = safeShotId(requestedId, previousId);
    const duplicate = shots.some((candidate, offset) => (
        offset !== position && safeShotId(
            candidate?.id,
            `clip_${String(offset + 1).padStart(4, "0")}`,
        ) === nextId
    ));
    if (duplicate) {
        throw new Error(`Another scene already uses the ID “${nextId}”.`);
    }
    if (nextId === previousId) return {previousId, id:nextId, changed:false};

    shot.id = nextId;
    for (const chapter of Array.isArray(plan?.chapters) ? plan.chapters : []) {
        if (String(chapter?.start_scene_id ?? "") === previousId) {
            chapter.start_scene_id = nextId;
        }
    }
    // Authored visual-context links use stable scene IDs. Numeric spellings
    // deliberately remain scene indexes and must not be rewritten as IDs.
    if (!/^\d+$/.test(previousId)) {
        for (const candidate of shots) {
            for (const field of [
                "visual_context_source", "visual_context_lead_source",
                "audio_context_source", "audio_context_lead_source",
            ]) {
                if (String(candidate?.[field] ?? "").trim() === previousId) {
                    candidate[field] = nextId;
                }
            }
            for (const block of Array.isArray(
                candidate?.visual_context_blocks,
            ) ? candidate.visual_context_blocks : []) {
                if (String(block?.source ?? "").trim() === previousId) {
                    block.source = nextId;
                }
            }
        }
    }
    return {previousId, id:nextId, changed:true};
}

export function normalizeChapterResolution(value) {
    if (value == null) return null;
    if (typeof value !== "object" || Array.isArray(value)
            || Object.keys(value).length !== 2
            || !["width", "height"].every((key) => (
                Number.isInteger(value[key]) && value[key] >= 32
                && value[key] <= 16384 && value[key] % 32 === 0
            ))) {
        throw new Error("Chapter width and height must be multiples of 32 between 32 and 16384.");
    }
    return {width:value.width, height:value.height};
}

export function safeChapterId(value, fallback = "chapter") {
    return safeShotId(value, fallback);
}

export function orderedChapters(plan) {
    const shots = Array.isArray(plan?.shots) ? plan.shots : [];
    const order = new Map(shots.map((shot, offset) => [
        safeShotId(shot?.id, `clip_${String(offset + 1).padStart(4, "0")}`),
        offset,
    ]));
    return (Array.isArray(plan?.chapters) ? plan.chapters : [])
        .filter((chapter) => order.has(chapter?.start_scene_id))
        .slice()
        .sort((left, right) => (
            order.get(left.start_scene_id) - order.get(right.start_scene_id)
        ));
}

export function uniqueChapterId(chapters, requested = "chapter") {
    const used = new Set((chapters ?? []).map((chapter, offset) => safeChapterId(
        chapter?.id,
        `chapter_${String(offset + 1).padStart(2, "0")}`,
    )));
    const base = safeChapterId(requested, "chapter");
    if (!used.has(base)) return base;
    for (let suffix = 2; suffix <= MAX_CHAPTERS + 1; suffix += 1) {
        const candidate = `${base}_${suffix}`.slice(0, 96);
        if (!used.has(candidate)) return candidate;
    }
    return `${base}_${Date.now()}`.slice(0, 96);
}

export function makeChapter(plan, startIndex = 0) {
    const shots = Array.isArray(plan?.shots) ? plan.shots : [];
    const index = Math.max(0, Math.min(shots.length - 1, Number(startIndex) || 0));
    const startSceneId = safeShotId(
        shots[index]?.id,
        `clip_${String(index + 1).padStart(4, "0")}`,
    );
    const chapters = Array.isArray(plan.chapters) ? plan.chapters : [];
    if (chapters.some((chapter) => chapter?.start_scene_id === startSceneId)) {
        throw new Error(`A chapter already starts before scene ${index + 1}.`);
    }
    const ordinal = chapters.length + 1;
    const chapter = {
        id: uniqueChapterId(chapters, `chapter_${String(ordinal).padStart(2, "0")}`),
        title: `Chapter ${ordinal}`,
        start_scene_id: startSceneId,
        text: "",
    };
    plan.chapters = [...chapters, chapter];
    return chapter;
}

export function removePlanShot(plan, index) {
    const shots = Array.isArray(plan?.shots) ? plan.shots : [];
    if (index < 0 || index >= shots.length) return null;
    const removedId = safeShotId(
        shots[index]?.id,
        `clip_${String(index + 1).padStart(4, "0")}`,
    );
    const [removed] = shots.splice(index, 1);
    if (Array.isArray(plan.chapters)) {
        const replacement = shots[index] ?? shots[index - 1] ?? null;
        const replacementId = replacement ? safeShotId(
            replacement.id,
            `clip_${String(Math.max(1, index + 1)).padStart(4, "0")}`,
        ) : null;
        const occupied = new Set(plan.chapters
            .filter((chapter) => chapter.start_scene_id !== removedId)
            .map((chapter) => chapter.start_scene_id));
        plan.chapters = plan.chapters.filter((chapter) => {
            if (chapter.start_scene_id !== removedId) return true;
            if (!replacementId || occupied.has(replacementId)) return false;
            chapter.start_scene_id = replacementId;
            occupied.add(replacementId);
            return true;
        });
        if (plan.chapters.length === 0) delete plan.chapters;
    }
    return removed;
}

export function uniqueShotId(shots, requested = "scene") {
    const used = new Set(shots.map((shot, offset) => safeShotId(
        shot?.id,
        `clip_${String(offset + 1).padStart(4, "0")}`,
    )));
    const base = safeShotId(requested, "scene");
    if (!used.has(base)) return base;
    for (let suffix = 2; suffix <= MAX_SHOTS + 1; suffix += 1) {
        const tail = `_${suffix}`;
        const candidate = `${base.slice(0, 96 - tail.length)}${tail}`;
        if (!used.has(candidate)) return candidate;
    }
    return `${base}_${Date.now()}`.slice(0, 96);
}

export function makeShot(shots = []) {
    const ordinal = shots.length + 1;
    return {
        id: uniqueShotId(shots, `scene_${String(ordinal).padStart(2, "0")}`),
        prompt: ["Describe this scene."],
    };
}

export function duplicateShot(shots, index) {
    if (!Array.isArray(shots) || !Number.isInteger(index)
            || index < 0 || index >= shots.length) {
        throw new Error("Scene duplication target is outside the Plan.");
    }
    const original = [...shots];
    const ids = original.map((shot, offset) => safeShotId(
        shot.id, `clip_${String(offset + 1).padStart(4, "0")}`,
    ));
    const duplicated = clone(shots[index]);
    duplicated.id = uniqueShotId(shots, `${safeShotId(
        duplicated.id,
        `scene_${String(index + 1).padStart(2, "0")}`,
    )}_copy`);
    // Pin legacy implicit identities before insertion shifts their positions.
    // Existing ID-based links and delegated prompts must keep naming the same
    // original scene, not the new occupant of its old index.
    original.forEach((shot, offset) => {
        if (!String(shot.id ?? "").trim()) shot.id = ids[offset];
    });
    shots.splice(index + 1, 0, duplicated);

    const authored = value => value !== undefined && value !== null
        && !(typeof value === "string" && !value.trim());
    function remap(shot, oldIndex, newIndex) {
        function source(holder, key, followsPrevious = false) {
            const raw = holder[key];
            const numeric = typeof raw === "number" || (
                typeof raw === "string" && /^\d+$/.test(raw.trim())
            );
            let prior;
            if (numeric && Number.isInteger(Number(raw))) prior = Number(raw) - 1;
            else if (typeof raw === "string") {
                if (["", "previous", "immediate"].includes(raw.trim().toLowerCase())) return;
                prior = ids.indexOf(raw.trim());
                if (prior !== ids.lastIndexOf(raw.trim())) return;
            } else return;
            // Do not silently repair invalid authored references.
            if (prior < 0 || prior >= oldIndex || !Number.isInteger(prior)) return;
            const shifted = prior + (prior > index ? 1 : 0);
            const next = followsPrevious && prior === oldIndex - 1
                ? newIndex - 1 : shifted;
            if (numeric) {
                if (next !== prior) holder[key] = typeof raw === "number" ? next + 1 : String(next + 1);
            } else if (followsPrevious && prior === oldIndex - 1
                    && next !== shifted) {
                // Purely numeric IDs are interpreted as positions by the
                // validator, so use a numeric position for those targets.
                const id = safeShotId(
                    shots[next].id, `clip_${String(next + 1).padStart(4, "0")}`,
                );
                holder[key] = /^\d+$/.test(String(id).trim()) ? next + 1 : id;
            }
        }
        for (const kind of ["visual", "audio"]) {
            // A plain predecessor tail follows the newly inserted scene.
            // Explicit windows, composed context and deliberately older
            // sources retain their original clip identities and frame ranges.
            const linear = !authored(shot[`${kind}_context_start_frame`])
                && !authored(shot[`${kind}_context_lead_source`])
                && !Object.hasOwn(shot, `${kind}_context_blocks`);
            source(shot, `${kind}_context_source`, linear);
            source(shot, `${kind}_context_lead_source`);
        }
        if (Array.isArray(shot.visual_context_blocks)) {
            for (const block of shot.visual_context_blocks) {
                if (!block || typeof block !== "object" || Array.isArray(block)) continue;
                source(block, "source", shot.visual_context_blocks.length === 1
                    && !authored(block.start_frame));
            }
        }
    }
    original.forEach((shot, offset) => remap(
        shot, offset, offset + (offset > index ? 1 : 0),
    ));
    remap(duplicated, index, index + 1);
    return duplicated;
}

export function moveShot(shots, from, to) {
    if (from === to || from < 0 || from >= shots.length || to < 0 || to >= shots.length) {
        return;
    }
    const [shot] = shots.splice(from, 1);
    shots.splice(to, 0, shot);
}

export function h3FrameLength(seconds) {
    const numeric = Number(seconds);
    if (!Number.isFinite(numeric) || numeric <= 0) {
        throw new Error("Duration must be a finite positive number.");
    }
    const requested = Math.max(5, Math.ceil(numeric * FPS - 1e-9));
    const length = requested + ((5 - (requested % 17)) % 17);
    if (length > MAX_H3_FRAMES) {
        throw new Error(
            `Duration rounds to ${length} frames; H3's largest valid length is ${MAX_H3_FRAMES}.`,
        );
    }
    return length;
}

export function shotLengthMode(shot) {
    if (shot?.length !== undefined || shot?.frames !== undefined) return "frames";
    if (shot?.duration_seconds !== undefined) return "seconds";
    return "default";
}

export function setShotLengthMode(shot, mode, fallbackSeconds = 15) {
    if (!shot || typeof shot !== "object" || Array.isArray(shot)) {
        throw new Error("Scene length settings require a scene object.");
    }
    if (!["default", "seconds", "frames"].includes(mode)) {
        throw new Error(`Unknown scene length mode “${mode}”.`);
    }

    const currentMode = shotLengthMode(shot);
    if (currentMode === mode) return shot;

    let currentSeconds;
    if (currentMode === "frames") {
        const frames = Number(shot.length ?? shot.frames);
        currentSeconds = Number.isFinite(frames) && frames > 0
            ? frames / FPS : Number(fallbackSeconds);
    } else if (currentMode === "seconds") {
        currentSeconds = Number(shot.duration_seconds);
    } else {
        currentSeconds = Number(fallbackSeconds);
    }
    if (!Number.isFinite(currentSeconds) || currentSeconds <= 0) {
        currentSeconds = 15;
    }

    // Compute the replacement before deleting the active representation. If
    // conversion fails (for example an out-of-range duration), the scene stays
    // untouched and the editor can continue to report the original problem.
    const replacement = mode === "frames" ? h3FrameLength(currentSeconds)
        : mode === "seconds" ? currentSeconds : null;
    delete shot.length;
    delete shot.frames;
    delete shot.duration_seconds;
    if (mode === "frames") shot.length = replacement;
    if (mode === "seconds") shot.duration_seconds = replacement;
    return shot;
}

export function sceneContinuationMode(shot, planDefault = "guide") {
    const rawFallback = String(planDefault ?? "guide");
    const fallback = RETIRED_CONTINUATION_MODES[rawFallback] ?? rawFallback;
    if (!CONTINUATION_MODES.includes(fallback)) {
        throw new Error(`Unknown Plan continuation mode “${fallback}”.`);
    }
    const rawMode = shot?.continuation_mode ?? fallback;
    const mode = RETIRED_CONTINUATION_MODES[rawMode] ?? rawMode;
    if (!CONTINUATION_MODES.includes(mode)) {
        throw new Error(`Unknown scene continuation mode “${String(mode)}”.`);
    }
    return mode;
}

export function sceneLoRARoute(shot) {
    const route = String(shot?.lora_route ?? "base").trim().toLowerCase()
        || "base";
    if (!SCENE_LORA_ROUTES.includes(route)) {
        throw new Error(
            `Scene LoRA route must be one of ${SCENE_LORA_ROUTES.join(", ")}.`,
        );
    }
    return route;
}

export function sceneContextLength(shot, planDefault = 22) {
    const fallback = Number(planDefault);
    if (!H3_CONTEXT_LENGTHS.includes(fallback)) {
        throw new Error(`Unknown Plan context length “${String(planDefault)}”.`);
    }
    const value = shot?.context_length;
    if (value === undefined || value === null
            || (typeof value === "string" && !value.trim())) return fallback;
    const resolved = Number(value);
    if (typeof value === "boolean" || !Number.isInteger(resolved)
            || (resolved !== 0 && !H3_CONTEXT_LENGTHS.includes(resolved))) {
        throw new Error(
            `Scene context length must be 0 or one of ${H3_CONTEXT_LENGTHS.join(", ")}.`,
        );
    }
    return resolved;
}

function resolvePriorSceneSource(plan, index, raw, field, defaultPrevious) {
    const shots = plan?.shots;
    const target = Number(index);
    if (!Array.isArray(shots) || !Number.isInteger(target)
            || target < 1 || target > shots.length) {
        throw new Error(`${field} has an invalid target scene.`);
    }
    if (target === 1) return null;
    if (raw === undefined || raw === null
            || (typeof raw === "string" && [
                "", "previous", "immediate",
            ].includes(raw.trim().toLowerCase()))) {
        return defaultPrevious ? target - 1 : null;
    }
    if (typeof raw === "boolean") {
        throw new Error(
            `${field} must name an earlier scene ID or index.`,
        );
    }
    let source = null;
    const numeric = Number(raw);
    if ((typeof raw === "number" || (typeof raw === "string"
            && /^\d+$/.test(raw.trim()))) && Number.isInteger(numeric)) {
        source = numeric;
    } else if (typeof raw === "string") {
        const wanted = raw.trim();
        const matches = shots.map((shot, offset) => safeShotId(
            shot?.id, `clip_${String(offset + 1).padStart(4, "0")}`,
        ) === wanted ? offset + 1 : null).filter(Boolean);
        if (matches.length === 1) source = matches[0];
    }
    if (source === null) {
        throw new Error(
            `${field} “${String(raw)}” does not match a scene ID or index.`,
        );
    }
    if (source < 1 || source >= target) {
        throw new Error(
            `${field} for scene ${target} must point to an earlier scene.`,
        );
    }
    return source;
}

export function sceneVisualContextSource(plan, index) {
    return resolvePriorSceneSource(
        plan, index, plan?.shots?.[Number(index) - 1]?.visual_context_source,
        "Visual context source", true,
    );
}

export function sceneVisualContextLeadSource(plan, index) {
    return resolvePriorSceneSource(
        plan, index,
        plan?.shots?.[Number(index) - 1]?.visual_context_lead_source,
        "Composed context lead source", false,
    );
}

export function sceneVisualContextLeadFrames(shot, contextLength) {
    const raw = shot?.visual_context_lead_frames;
    if (raw === undefined || raw === null
            || (typeof raw === "string" && !raw.trim())) return 0;
    const resolved = Number(raw);
    const allowed = visualContextLeadFrameOptions(contextLength);
    if (typeof raw === "boolean" || !Number.isInteger(resolved)
            || !allowed.includes(resolved)) {
        throw new Error(
            `Composed context lead frames must be one of ${allowed.join(", ")} and smaller than this scene's ${contextLength}-frame total.`,
        );
    }
    return resolved;
}

export function visualContextBoundaryFrames(contextLength) {
    const total = Number(contextLength);
    if (!Number.isInteger(total) || total < 1) return Object.freeze([]);
    const boundaries = [];
    for (let frame = 1; frame < total; frame += 1) {
        if (h3NativeFrameBoundaryStep(frame) !== null) boundaries.push(frame);
    }
    return Object.freeze(boundaries);
}

export function visualContextMaximumBlocks(contextLength) {
    const total = Number(contextLength);
    if (!Number.isInteger(total) || total < 1) return 0;
    return visualContextBoundaryFrames(total).length + 1;
}

export function visualContextPartitionFromBoundaries(
    contextLength, rawBoundaries,
) {
    const total = Number(contextLength);
    const boundaries = Array.from(rawBoundaries ?? [], Number);
    if (!Number.isInteger(total) || total < 1) {
        throw new Error("Visual context partition requires a positive total.");
    }
    let previous = 0;
    const frames = [];
    const allowed = new Set(visualContextBoundaryFrames(total));
    for (const boundary of boundaries) {
        if (!Number.isInteger(boundary) || boundary <= previous
                || boundary >= total || !allowed.has(boundary)) {
            throw new Error(
                `Visual context boundary ${String(boundary)} is not an ordered H3 latent boundary inside ${total} frames.`,
            );
        }
        frames.push(boundary - previous);
        previous = boundary;
    }
    frames.push(total - previous);
    return Object.freeze(frames);
}

export function visualContextDefaultPartition(contextLength, blockCount) {
    const total = Number(contextLength);
    const count = Number(blockCount);
    const candidates = visualContextBoundaryFrames(total);
    if (!Number.isInteger(count) || count < 1
            || count > candidates.length + 1) {
        throw new Error(
            `${total}-frame visual context supports between 1 and ${candidates.length + 1} ordered blocks.`,
        );
    }
    if (count === 1) return Object.freeze([total]);
    // Choose monotonic H3 boundaries nearest evenly spaced target positions.
    // This gives a stable, balanced starting point without enumerating the
    // combinatorial set of every possible N-way partition.
    const selected = [];
    let minimumIndex = 0;
    for (let cut = 1; cut < count; cut += 1) {
        const remainingCuts = count - cut - 1;
        const maximumIndex = candidates.length - remainingCuts - 1;
        const wanted = total * cut / count;
        let chosen = minimumIndex;
        for (let index = minimumIndex; index <= maximumIndex; index += 1) {
            if (Math.abs(candidates[index] - wanted)
                    < Math.abs(candidates[chosen] - wanted)) chosen = index;
        }
        selected.push(candidates[chosen]);
        minimumIndex = chosen + 1;
    }
    return visualContextPartitionFromBoundaries(total, selected);
}

function visualContextBlockStartValue(
    value, rawFrames, deliveredFrames, spanFrames, prefixFrames, label,
) {
    const holder = {};
    if (value !== undefined && value !== null
            && !(typeof value === "string" && !value.trim())) {
        holder.visual_context_start_frame = value;
    }
    try {
        return sceneVisualContextStartFrame(
            holder, rawFrames, deliveredFrames, spanFrames, false,
            prefixFrames,
        );
    } catch (error) {
        throw new Error(`${label}: ${error.message}`);
    }
}

export function sceneVisualContextBlocks(plan, index, contextLength) {
    const shots = plan?.shots;
    const target = Number(index);
    const total = Number(contextLength);
    if (!Array.isArray(shots) || !Number.isInteger(target)
            || target < 1 || target > shots.length) {
        throw new Error("Visual context blocks have an invalid target scene.");
    }
    if (!Number.isInteger(total) || total < 0) {
        throw new Error("Visual context blocks require a valid context total.");
    }
    const shot = shots[target - 1] ?? {};
    if (target === 1 || total === 0) {
        if (Object.hasOwn(shot, "visual_context_blocks")) {
            throw new Error(
                "visual_context_blocks requires a scene after scene 1 with positive visual context.",
            );
        }
        return Object.freeze([]);
    }
    if (Object.hasOwn(shot, "visual_context_blocks")) {
        const legacyFields = [
            "visual_context_source", "visual_context_start_frame",
            "visual_context_lead_source", "visual_context_lead_frames",
            "visual_context_lead_start_frame",
        ].filter((field) => Object.hasOwn(shot, field));
        if (legacyFields.length) {
            throw new Error(
                `visual_context_blocks cannot be combined with legacy fields: ${legacyFields.join(", ")}.`,
            );
        }
        if (!Array.isArray(shot.visual_context_blocks)
                || !shot.visual_context_blocks.length) {
            throw new Error(
                "visual_context_blocks must contain at least one ordered block.",
            );
        }
        const blocks = [];
        let consumed = 0;
        for (let offset = 0; offset < shot.visual_context_blocks.length;
            offset += 1) {
            const raw = shot.visual_context_blocks[offset];
            if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
                throw new Error(
                    `Visual context block ${offset + 1} must be an object.`,
                );
            }
            const frames = Number(raw.frames);
            if (!Number.isInteger(frames) || frames < 1) {
                throw new Error(
                    `Visual context block ${offset + 1} frames must be a positive integer.`,
                );
            }
            const source = resolvePriorSceneSource(
                plan, target, raw.source,
                `Visual context block ${offset + 1} source`, true,
            );
            const startValue = raw.start_frame;
            if (startValue !== undefined && startValue !== null
                    && (!(Number.isInteger(Number(startValue)))
                        || Number(startValue) < 0)) {
                throw new Error(
                    `Visual context block ${offset + 1} start_frame must be a non-negative integer.`,
                );
            }
            consumed += frames;
            if (consumed < total
                    && h3NativeFrameBoundaryStep(consumed) === null) {
                throw new Error(
                    `Visual context block ${offset + 1} ends at frame ${consumed}, which is not an H3 latent boundary.`,
                );
            }
            blocks.push(Object.freeze({
                source,
                sourceId:safeShotId(
                    shots[source - 1]?.id,
                    `clip_${String(source).padStart(4, "0")}`,
                ),
                frames,
                startFrame:startValue === undefined || startValue === null
                    || (typeof startValue === "string" && !startValue.trim())
                    ? null : Number(startValue),
            }));
        }
        if (consumed !== total) {
            throw new Error(
                `Visual context blocks total ${consumed} frames; this scene requires ${total}.`,
            );
        }
        if (total !== 1 && h3NativeFrameBoundaryStep(total) === null) {
            throw new Error(
                `${total}-frame visual context does not end on H3's latent lattice.`,
            );
        }
        return Object.freeze(blocks);
    }

    // Released one/two-block plans remain byte-for-byte unchanged on disk.
    // Expose them as a list only to the new planner/runtime adapter.
    const recentSource = sceneVisualContextSource(plan, target);
    const leadSource = sceneVisualContextLeadSource(plan, target);
    const leadFrames = leadSource === null
        ? 0 : sceneVisualContextLeadFrames(shot, total);
    const result = [];
    if (leadSource !== null) result.push(Object.freeze({
        source:leadSource,
        sourceId:safeShotId(
            shots[leadSource - 1]?.id,
            `clip_${String(leadSource).padStart(4, "0")}`,
        ),
        frames:leadFrames,
        startFrame:Object.hasOwn(shot, "visual_context_lead_start_frame")
            ? Number(shot.visual_context_lead_start_frame) : null,
    }));
    result.push(Object.freeze({
        source:recentSource,
        sourceId:safeShotId(
            shots[recentSource - 1]?.id,
            `clip_${String(recentSource).padStart(4, "0")}`,
        ),
        frames:total - leadFrames,
        startFrame:Object.hasOwn(shot, "visual_context_start_frame")
            ? Number(shot.visual_context_start_frame) : null,
    }));
    return Object.freeze(result);
}

export function h3NativeFrameBoundaryStep(frame) {
    const resolved = Number(frame);
    if (!Number.isInteger(resolved) || resolved < 0) return null;
    if (resolved % 17 === 0) return 5 * (resolved / 17);
    if (resolved >= 5 && (resolved - 5) % 17 === 0) {
        return 2 + 5 * ((resolved - 5) / 17);
    }
    return null;
}

export function nativeContextWindowStarts(
    rawFrames, deliveredFrames, spanFrames, prefixFrames = 0,
) {
    const raw = Number(rawFrames);
    const delivered = Number(deliveredFrames);
    const span = Number(spanFrames);
    const prefix = Number(prefixFrames);
    if (!Number.isInteger(raw) || !Number.isInteger(delivered)
            || !Number.isInteger(span) || !Number.isInteger(prefix)
            || raw < delivered || delivered < 1 || span < 1
            || span > delivered || prefix < 0) return Object.freeze([]);
    const latest = delivered - span;
    if (span === 1 && prefix === 0) return Object.freeze([latest]);
    const targetStart = h3NativeFrameBoundaryStep(prefix);
    const targetEnd = h3NativeFrameBoundaryStep(prefix + span);
    if (targetStart === null || targetEnd === null) return Object.freeze([]);
    const expectedSteps = targetEnd - targetStart;
    const trim = raw - delivered;
    const starts = [];
    for (let start = 0; start <= latest; start += 1) {
        const sourceStart = h3NativeFrameBoundaryStep(trim + start);
        const sourceEnd = h3NativeFrameBoundaryStep(trim + start + span);
        if (sourceStart !== null && sourceEnd !== null
                && sourceStart % 5 === targetStart % 5
                && sourceEnd - sourceStart === expectedSteps) {
            starts.push(start);
        }
    }
    return Object.freeze(starts);
}

export function nearestNativeContextWindowStart(starts, requested) {
    const options = Array.isArray(starts) ? starts : [];
    if (!options.length) return null;
    const value = Number(requested);
    if (!Number.isFinite(value)) return options.at(-1);
    return options.reduce((nearest, candidate) => (
        Math.abs(candidate - value) < Math.abs(nearest - value)
            ? candidate : nearest
    ), options[0]);
}

export function sceneVisualContextStartFrame(
    shot, rawFrames, deliveredFrames, spanFrames, lead = false,
    prefixFrames = 0,
) {
    const field = lead
        ? "visual_context_lead_start_frame"
        : "visual_context_start_frame";
    const rawFramesResolved = Number(rawFrames);
    const delivered = Number(deliveredFrames);
    const span = Number(spanFrames);
    if (!Number.isInteger(rawFramesResolved) || rawFramesResolved < delivered
            || !Number.isInteger(delivered) || delivered < 0
            || !Number.isInteger(span) || span < 0) {
        throw new Error(`${field} requires valid raw, delivered, and context frame counts.`);
    }
    if (span < 1) {
        if (Object.hasOwn(shot ?? {}, field)) {
            throw new Error(`${field} requires a positive visual context span.`);
        }
        return delivered;
    }
    const latest = delivered - span;
    if (latest < 0) {
        throw new Error(
            `${field} requests ${span} frames from a source delivering only ${delivered}.`,
        );
    }
    const starts = nativeContextWindowStarts(
        rawFramesResolved, delivered, span, prefixFrames,
    );
    if (!starts.length) {
        throw new Error(
            `${field} has no native latent-aligned ${span}-frame window inside the source's ${rawFramesResolved} raw / ${delivered} delivered frames.`,
        );
    }
    const authored = shot?.[field];
    if (authored === undefined || authored === null
            || (typeof authored === "string" && !authored.trim())) return starts.at(-1);
    const resolved = Number(authored);
    if (typeof authored === "boolean" || !Number.isInteger(resolved)
            || resolved < 0 || resolved > latest) {
        throw new Error(
            `${field} must be between 0 and ${latest} so its ${span}-frame window fits inside the source's ${delivered} delivered frames.`,
        );
    }
    if (!starts.includes(resolved)) {
        const nearest = nearestNativeContextWindowStart(starts, resolved);
        throw new Error(
            `${field}=${resolved} is not on H3's native temporal latent lattice. Use ${nearest} (nearest aligned start).`,
        );
    }
    return resolved;
}

export function visualContextLeadFrameOptions(contextLength) {
    const total = Number(contextLength);
    if (!Number.isInteger(total)) return Object.freeze([]);
    const values = new Set();
    for (const nativeRun of H3_CONTEXT_LENGTHS) {
        if (nativeRun < 5 || nativeRun >= total) continue;
        values.add(nativeRun);
        const inverse = total - nativeRun;
        if (inverse >= 5 && inverse < total) values.add(inverse);
    }
    return Object.freeze([...values].sort((left, right) => left - right));
}

export function visualContextCompositions() {
    const compositions = [];
    for (const total of H3_CONTEXT_LENGTHS) {
        for (const lead of visualContextLeadFrameOptions(total)) {
            compositions.push(Object.freeze({
                total,
                lead,
                recent: total - lead,
                value: `${total}:${lead}`,
                label: `${total} total · ${lead} + ${total - lead}`,
            }));
        }
    }
    return Object.freeze(compositions);
}

export function sceneAudioContextLength(
    shot, planDefault = 22, videoContextLength = 22,
) {
    const fallback = Number(planDefault);
    if (!Number.isInteger(fallback) || fallback < 0 || fallback > 240) {
        throw new Error("Plan audio context length must be between 0 and 240 frames.");
    }
    const value = shot?.audio_context_length;
    if (value === undefined || value === null
            || (typeof value === "string" && !value.trim())) {
        return fallback || Number(videoContextLength);
    }
    const resolved = Number(value);
    if (typeof value === "boolean" || !Number.isInteger(resolved)
            || resolved < 0 || resolved > 240) {
        throw new Error("Scene audio context length must be between 0 and 240 frames.");
    }
    return resolved;
}

export function sceneAudioContextUnlocked(shot) {
    const value = shot?.audio_context_unlocked ?? false;
    if (typeof value !== "boolean") {
        throw new Error("audio_context_unlocked must be true or false.");
    }
    return value;
}

export function sceneAudioContextSource(plan, index) {
    return resolvePriorSceneSource(
        plan, index,
        plan?.shots?.[Number(index) - 1]?.audio_context_source,
        "Audio context source", true,
    );
}

export function sceneAudioContextLeadSource(plan, index) {
    return resolvePriorSceneSource(
        plan, index,
        plan?.shots?.[Number(index) - 1]?.audio_context_lead_source,
        "Composed audio lead source", false,
    );
}

export function audioContextLeadFrameOptions(contextLength) {
    const total = Number(contextLength);
    if (!Number.isInteger(total) || total < 2) return Object.freeze([]);
    const expected = Math.round(total / FPS * 40);
    return Object.freeze(Array.from({length:total - 1}, (_item, offset) => (
        offset + 1
    )).filter((lead) => (
        Math.round(lead / FPS * 40)
        + Math.round((total - lead) / FPS * 40) === expected
    )));
}

export function sceneAudioContextLeadFrames(shot, contextLength) {
    const raw = shot?.audio_context_lead_frames;
    if (raw === undefined || raw === null
            || (typeof raw === "string" && !raw.trim())) return 0;
    const resolved = Number(raw);
    const allowed = audioContextLeadFrameOptions(contextLength);
    if (typeof raw === "boolean" || !Number.isInteger(resolved)
            || !allowed.includes(resolved)) {
        throw new Error(
            `Composed audio lead frames must be an exact 40 Hz split between 1 and ${Math.max(0, Number(contextLength) - 1)}.`,
        );
    }
    return resolved;
}

export function audioContextWindowStarts(
    rawFrames, deliveredFrames, spanFrames,
) {
    const raw = Number(rawFrames);
    const delivered = Number(deliveredFrames);
    const span = Number(spanFrames);
    if (!Number.isInteger(raw) || !Number.isInteger(delivered)
            || !Number.isInteger(span) || raw < delivered || delivered < 1
            || span < 1 || span > delivered) return Object.freeze([]);
    const trim = raw - delivered;
    const expected = Math.round(span / FPS * 40);
    const starts = [];
    for (let start = 0; start <= delivered - span; start += 1) {
        const rawStart = trim + start;
        if (Math.round((rawStart + span) / FPS * 40)
                - Math.round(rawStart / FPS * 40) === expected) {
            starts.push(start);
        }
    }
    return Object.freeze(starts);
}

export function sceneAudioContextStartFrame(
    shot, rawFrames, deliveredFrames, spanFrames, lead = false,
) {
    const field = lead
        ? "audio_context_lead_start_frame" : "audio_context_start_frame";
    const starts = audioContextWindowStarts(
        rawFrames, deliveredFrames, spanFrames,
    );
    if (!starts.length) {
        throw new Error(
            `${field} has no exact ${spanFrames}-frame/40 Hz window in this source.`,
        );
    }
    const authored = shot?.[field];
    if (authored === undefined || authored === null
            || (typeof authored === "string" && !authored.trim())) {
        return starts.at(-1);
    }
    const resolved = Number(authored);
    const latest = Number(deliveredFrames) - Number(spanFrames);
    if (typeof authored === "boolean" || !Number.isInteger(resolved)
            || resolved < 0 || resolved > latest) {
        throw new Error(
            `${field} must be between 0 and ${latest} so its ${spanFrames}-frame window fits.`,
        );
    }
    if (!starts.includes(resolved)) {
        const nearest = nearestNativeContextWindowStart(starts, resolved);
        throw new Error(
            `${field}=${resolved} does not preserve the exact 40 Hz duration. Use ${nearest} (nearest).`,
        );
    }
    return resolved;
}

export function sceneVideoBlendFrames(
    shot, planDefault = 0, videoContextLength = 22,
) {
    const fallback = Number(planDefault);
    const context = Number(videoContextLength);
    if (!Number.isInteger(fallback) || fallback < 0) {
        throw new Error("Plan video blend frames must be a non-negative integer.");
    }
    if (!Number.isInteger(context) || context < 0) {
        throw new Error("Scene video context must be a non-negative integer.");
    }
    const value = shot?.video_blend_frames;
    if (value === undefined || value === null
            || (typeof value === "string" && !value.trim())) {
        return Math.min(fallback, context);
    }
    const resolved = Number(value);
    if (typeof value === "boolean" || !Number.isInteger(resolved)
            || resolved < 0 || resolved > context) {
        throw new Error(
            `Scene video blend frames must be between 0 and its context length (${context}).`,
        );
    }
    return resolved;
}

export function validateH3Length(value) {
    const length = Number(value);
    if (!Number.isInteger(length) || length < 5 || length > MAX_H3_FRAMES || length % 17 !== 5) {
        throw new Error(
            `Exact length must be 5..${MAX_H3_FRAMES} frames with length % 17 == 5.`,
        );
    }
    return length;
}

function sceneRawFrames(shot, defaultDuration) {
    if (shot.length !== undefined && shot.length !== null && shot.length !== "") {
        return validateH3Length(shot.length);
    }
    if (shot.frames !== undefined && shot.frames !== null && shot.frames !== "") {
        return validateH3Length(shot.frames);
    }
    const duration = shot.duration_seconds ?? defaultDuration;
    return h3FrameLength(duration);
}

function validateSeed(seed) {
    if (seed === undefined || seed === null || seed === "") return;
    let numeric;
    try {
        numeric = BigInt(String(seed));
    } catch (_error) {
        throw new Error("Seed must be an unsigned 64-bit integer.");
    }
    if (numeric < 0n || numeric > MAX_SEED) {
        throw new Error("Seed is outside the unsigned 64-bit range.");
    }
}

export function calculatePlanTiming(plan, settings = {}) {
    const errors = [];
    const rows = [];
    const contextLength = Number(settings.contextLength ?? 22);
    const audioContextLength = Number(settings.audioContextLength ?? 22);
    const videoBlendFrames = Number(settings.videoBlendFrames ?? 0);
    const encodeMode = settings.encodeMode ?? "video";
    const anchorMode = settings.anchorMode ?? "head";
    const planContinuationMode = settings.continuationMode ?? "guide";
    const generatedContinuity = String(
        settings.generatedContinuity ?? "on",
    );
    const sourceAudioTarget = String(settings.sourceAudioTarget ?? "off");
    const nodeDefaultDuration = Number(settings.defaultDurationSeconds ?? 15);
    const planDefaultDuration = Number(plan?.defaults?.duration_seconds ?? nodeDefaultDuration);
    const defaultSteps = Number(plan?.defaults?.steps ?? settings.defaultSteps ?? 20);
    const hasSharedPrompt = sharedPrompt(plan ?? {}).text.trim().length > 0;

    if (!H3_CONTEXT_LENGTHS.includes(contextLength)) {
        errors.push(`Context length must be one of ${H3_CONTEXT_LENGTHS.join(", ")}.`);
    }
    if (!Number.isInteger(audioContextLength)
            || audioContextLength < 0 || audioContextLength > 240) {
        errors.push("Audio context length must be between 0 and 240 frames.");
    }
    if (!Number.isInteger(videoBlendFrames) || videoBlendFrames < 0
            || videoBlendFrames > contextLength) {
        errors.push(
            `Video blend frames must be between 0 and context length (${contextLength}).`,
        );
    }
    if (!Number.isFinite(planDefaultDuration) || planDefaultDuration <= 0) {
        errors.push("Default duration must be a finite positive number.");
    }
    if (!Number.isInteger(defaultSteps) || defaultSteps < 1 || defaultSteps > 10000) {
        errors.push("Default steps must be between 1 and 10000.");
    }

    const ids = new Set();
    let stitchedFrames = 0;
    for (let offset = 0; offset < (plan?.shots?.length ?? 0); offset += 1) {
        const shot = plan.shots[offset];
        const index = offset + 1;
        const fallback = `clip_${String(index).padStart(4, "0")}`;
        const id = safeShotId(shot.id, fallback);
        const rowErrors = [];
        if (ids.has(id)) rowErrors.push(`Duplicate normalized scene id “${id}”.`);
        ids.add(id);
        if (!promptValueToText(shot.prompt, `Scene ${index} prompt`).trim()
            && !hasSharedPrompt) {
            rowErrors.push("Scene and shared prompts are both empty.");
        }

        const steps = Number(shot.steps ?? defaultSteps);
        if (!Number.isInteger(steps) || steps < 1 || steps > 10000) {
            rowErrors.push("Steps must be between 1 and 10000.");
        }
        try {
            validateSeed(shot.seed);
        } catch (error) {
            rowErrors.push(error.message);
        }

        let loraRoute = "base";
        try {
            loraRoute = sceneLoRARoute(shot);
        } catch (error) {
            rowErrors.push(error.message);
        }

        let sceneContext = contextLength;
        try {
            sceneContext = sceneContextLength(shot, contextLength);
        } catch (error) {
            rowErrors.push(error.message);
        }
        if (index === 1 && (Object.hasOwn(
            shot, "visual_context_start_frame",
        ) || Object.hasOwn(shot, "visual_context_blocks"))) {
            rowErrors.push(
                "Scene 1 cannot select a saved-scene context window.",
            );
        }

        let visualContextSource = index > 1 ? index - 1 : null;
        let visualContextLeadSource = null;
        let visualContextLeadFrames = 0;
        let visualContextBlocks = [];
        try {
            visualContextBlocks = sceneVisualContextBlocks(
                plan, index, sceneContext,
            );
            if (visualContextBlocks.length) {
                visualContextSource = visualContextBlocks.at(-1).source;
            }
            if (visualContextBlocks.length === 2) {
                visualContextLeadSource = visualContextBlocks[0].source;
                visualContextLeadFrames = visualContextBlocks[0].frames;
            }
        } catch (error) {
            rowErrors.push(error.message);
        }

        let sceneAudioContext = audioContextLength || sceneContext;
        try {
            sceneAudioContext = sceneAudioContextLength(
                shot, audioContextLength, sceneContext,
            );
        } catch (error) {
            rowErrors.push(error.message);
        }

        let sceneBlendFrames = Math.min(
            Math.max(0, videoBlendFrames), Math.max(0, sceneContext),
        );
        try {
            sceneBlendFrames = sceneVideoBlendFrames(
                shot, videoBlendFrames, sceneContext,
            );
            if (sceneBlendFrames > 0 && anchorMode !== "head") {
                rowErrors.push("Video blending requires head anchor mode.");
            }
            if (index > 1 && sceneBlendFrames > 0 && (
                visualContextBlocks.length !== 1
                || visualContextBlocks.some((block) => (
                    block.source !== index - 1 || block.startFrame !== null
                ))
            )) {
                rowErrors.push(
                    "Non-linear, composed, or windowed visual context requires 0 assembly blend frames; the timeline still cuts from the immediately previous scene.",
                );
            }
        } catch (error) {
            rowErrors.push(error.message);
        }

        let continuationMode = "guide";
        try {
            continuationMode = sceneContinuationMode(
                shot, planContinuationMode,
            );
            if (sceneContext > 0 && [
                "masked_av", "tapered_av", "feathered_av",
                "audio_feathered_av", "drift_control_av",
                "color_stable_drift_av",
            ].includes(
                continuationMode,
            )) {
                const preservesGeneratedAudioPrefix = (
                    generatedContinuity === "on"
                    && sourceAudioTarget !== "locked"
                    && sceneAudioContext > 0
                );
                if ([
                    "masked_av", "feathered_av", "audio_feathered_av",
                ].includes(continuationMode)
                        && !VIDEO_ONLY_AV_CONTEXT_LENGTHS.includes(sceneContext)) {
                    rowErrors.push(
                        "Video-only AV continuation requires at least 5 context frames.",
                    );
                }
                if (preservesGeneratedAudioPrefix
                        && !AV_CONTEXT_LENGTHS.includes(sceneContext)) {
                    rowErrors.push(
                        "AV generated-audio continuity requires an exact shared video/audio boundary: 39, 90, 141, 192, or 243 context frames. Turn generated continuity off (or set scene audio context to 0) for a video-only AV test.",
                    );
                }
                if (encodeMode !== "video") {
                    rowErrors.push("AV mask continuation requires video encode mode.");
                }
                if (anchorMode !== "head") {
                    rowErrors.push("AV mask continuation requires head anchor mode.");
                }
                if (continuationMode === "tapered_av" && sceneContext !== 39) {
                    rowErrors.push(
                        "Detail AV currently requires exactly 39 context frames.",
                    );
                }
                if (["drift_control_av", "color_stable_drift_av"].includes(
                    continuationMode,
                ) && sceneContext !== 39) {
                    rowErrors.push(
                        "Drift-Control AV and Color-Stable Drift AV currently require exactly 39 context frames.",
                    );
                }
            }
            if (sceneContext > 0 && continuationMode === "latent_guide") {
                if (sceneContext < 5) {
                    rowErrors.push(
                        "Latent Guide requires a context length of at least 5 frames.",
                    );
                }
                if (encodeMode !== "video") {
                    rowErrors.push("Latent Guide requires video encode mode.");
                }
            }
        } catch (error) {
            rowErrors.push(error.message);
        }

        let audioContextUnlocked = false;
        let audioContextSource = index > 1 ? index - 1 : null;
        let audioContextLeadSource = null;
        let audioContextLeadFrames = 0;
        const effectiveAudioSelectionSpan = [
            "masked_av", "tapered_av", "feathered_av",
            "audio_feathered_av", "drift_control_av",
            "color_stable_drift_av",
        ].includes(continuationMode) && sceneAudioContext > 0
            ? sceneContext : sceneAudioContext;
        try {
            audioContextUnlocked = sceneAudioContextUnlocked(shot);
            if (audioContextUnlocked) {
                if (index === 1) {
                    rowErrors.push(
                        "Scene 1 cannot unlock saved-scene audio context.",
                    );
                }
                if (sourceAudioTarget === "locked") {
                    rowErrors.push(
                        "Unlocked audio context cannot be combined with Lip-sync to source audio.",
                    );
                } else if (generatedContinuity !== "on") {
                    rowErrors.push(
                        "Unlocked audio context requires Generated continuity for this scene.",
                    );
                }
                if (effectiveAudioSelectionSpan <= 0) {
                    rowErrors.push(
                        "Unlocked audio context requires a positive audio context length.",
                    );
                }
                audioContextSource = sceneAudioContextSource(plan, index);
                audioContextLeadSource = sceneAudioContextLeadSource(
                    plan, index,
                );
                if (audioContextLeadSource !== null) {
                    audioContextLeadFrames = sceneAudioContextLeadFrames(
                        shot, effectiveAudioSelectionSpan,
                    );
                } else if (Object.hasOwn(
                    shot, "audio_context_lead_frames",
                )) {
                    rowErrors.push(
                        "Composed audio lead frames require an audio lead source.",
                    );
                } else if (Object.hasOwn(
                    shot, "audio_context_lead_start_frame",
                )) {
                    rowErrors.push(
                        "Composed audio lead start requires an audio lead source.",
                    );
                }
            } else if ([
                "audio_context_source", "audio_context_start_frame",
                "audio_context_lead_source", "audio_context_lead_frames",
                "audio_context_lead_start_frame",
            ].some((field) => Object.hasOwn(shot, field))) {
                rowErrors.push(
                    "Independent audio fields require Audio context to be unlocked.",
                );
            }
        } catch (error) {
            rowErrors.push(error.message);
        }

        const contextSpatialProxy = String(
            shot.context_spatial_proxy ?? "off",
        ).trim().toLowerCase() || "off";
        if (!CONTEXT_SPATIAL_PROXY_MODES.includes(contextSpatialProxy)) {
            rowErrors.push(
                `Unknown boundary spatial proxy “${contextSpatialProxy}”.`,
            );
        } else if (contextSpatialProxy !== "off") {
            if (sceneContext <= 0) {
                rowErrors.push(
                    "Boundary spatial proxy requires positive video context.",
                );
            }
            if (contextSpatialProxy === "rgb_5_6" && ![
                "guide", "tone_carry_guide", "tapered_guide",
            ].includes(continuationMode)) {
                rowErrors.push(
                    "Low-grid 5/6 boundary proxy requires Guide, Tone Carry Guide, or Detail Guide.",
                );
            }
            if (contextSpatialProxy === "latent_5_6" && ![
                "masked_av", "tapered_av", "feathered_av",
                "audio_feathered_av", "drift_control_av",
                "color_stable_drift_av",
            ].includes(continuationMode)) {
                rowErrors.push(
                    "Latent 5/6 boundary proxy requires an AV continuation mode.",
                );
            }
            if (index === 1) {
                rowErrors.push(
                    "Scene 1 cannot use a 5/6 boundary proxy because imported context has no sampled predecessor latent.",
                );
            }
        }

        let rawFrames = 0;
        try {
            rawFrames = sceneRawFrames(shot, planDefaultDuration);
        } catch (error) {
            rowErrors.push(error.message);
        }

        let deliveredFrames = rawFrames;
        let generationStartFrame = stitchedFrames;
        if (index > 1 && anchorMode === "head") {
            if (sceneContext > 0 && rawFrames <= sceneContext) {
                rowErrors.push(
                    `${rawFrames} raw frames are not longer than the ${sceneContext}-frame overlap.`,
                );
            }
            deliveredFrames = Math.max(0, rawFrames - sceneContext);
            generationStartFrame = stitchedFrames - sceneContext;
        }

        rows.push({
            index,
            id,
            rawFrames,
            rawSeconds: rawFrames / FPS,
            deliveredFrames,
            deliveredSeconds: deliveredFrames / FPS,
            generationStartFrame,
            contextLength: sceneContext,
            visualContextSource,
            visualContextSourceId: visualContextSource === null ? null
                : safeShotId(
                    plan.shots[visualContextSource - 1]?.id,
                    `clip_${String(visualContextSource).padStart(4, "0")}`,
                ),
            visualContextLeadSource,
            visualContextLeadSourceId: visualContextLeadSource === null ? null
                : safeShotId(
                    plan.shots[visualContextLeadSource - 1]?.id,
                    `clip_${String(visualContextLeadSource).padStart(4, "0")}`,
                ),
            visualContextLeadFrames,
            visualContextStartFrame:null,
            visualContextLeadStartFrame:null,
            visualContextBlocks,
            audioContextUnlocked,
            audioContextSource,
            audioContextSourceId: audioContextSource === null ? null
                : safeShotId(
                    plan.shots[audioContextSource - 1]?.id,
                    `clip_${String(audioContextSource).padStart(4, "0")}`,
                ),
            audioContextLeadSource,
            audioContextLeadSourceId: audioContextLeadSource === null ? null
                : safeShotId(
                    plan.shots[audioContextLeadSource - 1]?.id,
                    `clip_${String(audioContextLeadSource).padStart(4, "0")}`,
                ),
            audioContextLeadFrames,
            audioContextStartFrame:null,
            audioContextLeadStartFrame:null,
            videoBlendFrames: sceneBlendFrames,
            audioContextLength: [
                "masked_av", "tapered_av", "feathered_av",
                "audio_feathered_av", "drift_control_av",
                "color_stable_drift_av",
            ].includes(
                continuationMode,
            )
                ? ((generatedContinuity === "on"
                    && sourceAudioTarget !== "locked"
                    && sceneAudioContext > 0) ? sceneContext : 0)
                : sceneAudioContext,
            preservesGeneratedAudioPrefix: (
                generatedContinuity === "on"
                && sourceAudioTarget !== "locked"
                && sceneAudioContext > 0
            ),
            continuationMode,
            contextSpatialProxy,
            loraRoute,
            errors: rowErrors,
        });
        stitchedFrames += deliveredFrames;
    }

    for (let offset = 1; offset < rows.length; offset += 1) {
        const target = rows[offset];
        let prefixFrames = 0;
        target.visualContextBlocks = target.visualContextBlocks.map(
            (block, blockOffset) => {
                const source = rows[block.source - 1] ?? null;
                let startFrame = block.startFrame;
                if (!source) {
                    target.errors.push(
                        `Visual context block ${blockOffset + 1} has no source scene.`,
                    );
                } else if (source.deliveredFrames < block.frames) {
                    target.errors.push(
                        `Visual context block ${blockOffset + 1} source scene ${source.index} delivers fewer than ${block.frames} required frames.`,
                    );
                } else {
                    try {
                        startFrame = visualContextBlockStartValue(
                            block.startFrame, source.rawFrames,
                            source.deliveredFrames, block.frames,
                            prefixFrames,
                            `Visual context block ${blockOffset + 1}`,
                        );
                    } catch (error) {
                        target.errors.push(error.message);
                    }
                }
                const resolved = Object.freeze({...block, startFrame});
                prefixFrames += block.frames;
                return resolved;
            },
        );
        const source = target.visualContextBlocks.at(-1) ?? null;
        target.visualContextStartFrame = source?.startFrame ?? null;
        const lead = target.visualContextBlocks.length === 2
            ? target.visualContextBlocks[0] : null;
        target.visualContextLeadStartFrame = lead?.startFrame ?? null;
        if (target.audioContextUnlocked) {
            const audioSource = target.audioContextSource === null ? null
                : rows[target.audioContextSource - 1];
            const recentAudioFrames = target.audioContextLength
                - target.audioContextLeadFrames;
            if (audioSource && audioSource.deliveredFrames < recentAudioFrames) {
                target.errors.push(
                    `Selected audio source scene ${audioSource.index} delivers fewer than ${recentAudioFrames} required frames.`,
                );
            }
            if (audioSource) {
                try {
                    target.audioContextStartFrame = sceneAudioContextStartFrame(
                        plan.shots[offset], audioSource.rawFrames,
                        audioSource.deliveredFrames, recentAudioFrames, false,
                    );
                } catch (error) {
                    target.errors.push(error.message);
                }
            }
            const audioLead = target.audioContextLeadSource === null ? null
                : rows[target.audioContextLeadSource - 1];
            if (audioLead
                    && audioLead.deliveredFrames < target.audioContextLeadFrames) {
                target.errors.push(
                    `Selected composed-audio lead scene ${audioLead.index} delivers fewer than ${target.audioContextLeadFrames} required frames.`,
                );
            }
            if (audioLead) {
                try {
                    target.audioContextLeadStartFrame = sceneAudioContextStartFrame(
                        plan.shots[offset], audioLead.rawFrames,
                        audioLead.deliveredFrames,
                        target.audioContextLeadFrames, true,
                    );
                } catch (error) {
                    target.errors.push(error.message);
                }
            }
        }
    }
    for (const row of rows) {
        for (const error of row.errors) errors.push(`Scene ${row.index}: ${error}`);
    }

    return {
        shots: rows,
        totalFrames: stitchedFrames,
        totalSeconds: stitchedFrames / FPS,
        errors,
    };
}

export function formatClock(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "—";
    const wholeMinutes = Math.floor(seconds / 60);
    const remainder = seconds - wholeMinutes * 60;
    return wholeMinutes
        ? `${wholeMinutes}:${remainder.toFixed(3).padStart(6, "0")}`
        : `${remainder.toFixed(3)}s`;
}
