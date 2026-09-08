import {promptValueToText, sharedPrompt} from "./h3_chain_plan_core.mjs?v=0.6.8";

export const PROMPT_ASSIST_MODES = Object.freeze([
    {id: "rewrite", label: "Rewrite"},
    {id: "continuity", label: "Continuity"},
    {id: "shorten", label: "Shorten"},
    {id: "critique", label: "Critique"},
    {id: "discuss", label: "Discuss"},
]);

export const PROMPT_ASSIST_DEFAULT_INSTRUCTIONS = Object.freeze({
    rewrite: "Rewrite this scene prompt for clarity, controllability, and strong MiniMax H3 results.",
    continuity: "Improve the opening and ending continuity with the adjacent scenes.",
    shorten: "Make this scene prompt shorter without losing references, dialogue, timing, or continuity.",
    critique: "Critique this scene prompt and identify the highest-impact improvements.",
    discuss: "Help me think through this scene prompt.",
});

function fnv1a(text) {
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
}

export function promptSourceRevision(sceneId, prompt) {
    const source = `${String(sceneId ?? "")}\u0000${String(prompt ?? "")}`;
    return `${String(sceneId ?? "scene")}:${source.length}:${fnv1a(source)}`;
}

export function promptSceneKey(sceneId, sceneIndex) {
    return `${Number(sceneIndex) || 0}:${String(sceneId ?? "scene")}`;
}

function promptAt(plan, index) {
    const shot = plan?.shots?.[index];
    return shot ? promptValueToText(shot.prompt, `Scene ${index + 1} prompt`) : "";
}

export function buildPromptAssistantContext(plan, sceneIndex, sourcePrompt, options = {}) {
    const shot = plan?.shots?.[sceneIndex] ?? {};
    const sceneId = String(shot.id || `clip_${String(sceneIndex + 1).padStart(4, "0")}`);
    const includeShared = options.includeShared !== false;
    const includeAdjacent = options.includeAdjacent !== false;
    const context = {
        generation_mode: "h3_chain_scene",
        scene_id: sceneId,
        scene_index: sceneIndex,
        scene_count: plan?.shots?.length ?? 1,
        source_prompt: String(sourcePrompt ?? ""),
    };
    const selectedText = String(options.selectedText ?? "");
    if (selectedText) context.selected_text = selectedText;
    if (includeShared) {
        const shared = sharedPrompt(plan).text;
        if (shared.trim()) context.shared_prompt = shared;
    }
    if (includeAdjacent) {
        const previous = sceneIndex > 0 ? promptAt(plan, sceneIndex - 1) : "";
        const next = sceneIndex + 1 < (plan?.shots?.length ?? 0)
            ? promptAt(plan, sceneIndex + 1) : "";
        if (previous.trim()) context.previous_prompt = previous;
        if (next.trim()) context.next_prompt = next;
    }
    const duration = Number(shot.duration_seconds ?? plan?.defaults?.duration_seconds);
    const frames = Number(shot.length ?? shot.frames);
    if (Number.isFinite(duration) && duration > 0) context.duration_seconds = duration;
    if (Number.isFinite(frames) && frames > 0) context.frames = Math.trunc(frames);
    return context;
}

export function draftConflict(draft, sceneId, currentPrompt) {
    if (!draft) return {stale: false, reason: ""};
    if (String(draft.sceneId) !== String(sceneId)) {
        return {stale: true, reason: "This draft belongs to a different scene."};
    }
    const revision = promptSourceRevision(sceneId, currentPrompt);
    if (draft.sourceRevision !== revision) {
        return {
            stale: true,
            reason: "The scene prompt changed after this draft was requested.",
        };
    }
    return {stale: false, reason: ""};
}

export function makePromptAssistRequest({
    requestId,
    conversationId,
    provider,
    mode,
    instruction,
    context,
}) {
    return {
        type: "prompt_assist_request",
        request_id: requestId,
        conversation_id: conversationId,
        provider,
        mode,
        instruction: String(instruction ?? "").trim()
            || PROMPT_ASSIST_DEFAULT_INSTRUCTIONS[mode]
            || PROMPT_ASSIST_DEFAULT_INSTRUCTIONS.rewrite,
        source_revision: promptSourceRevision(context.scene_id, context.source_prompt),
        context,
    };
}
