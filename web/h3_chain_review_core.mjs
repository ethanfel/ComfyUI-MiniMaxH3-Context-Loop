import {
    MAX_SEED,
    promptValueToText,
    promptTextToLines,
    sceneAudioContextLength,
    sceneContinuationMode,
    sceneContextLength,
    sceneLoRARoute,
    sceneVideoBlendFrames,
    sceneVisualContextBlocks,
    sceneVisualContextLeadFrames,
    sceneVisualContextLeadSource,
    sceneVisualContextSource,
    sharedPrompt,
} from "./h3_chain_plan_core.mjs?v=0.7.9";

const FPS = 24;
const MAX_H3_FRAMES = 3592;

export function reviewSeed(value) {
    let seed;
    try {
        seed = BigInt(String(value));
    } catch (_error) {
        throw new Error("Seed must be an integer.");
    }
    if (seed < 0n || seed > MAX_SEED) {
        throw new Error("Seed is outside the uint64 range.");
    }
    return seed.toString();
}

export function reviewDuration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) {
        throw new Error("Duration must be a finite positive number of seconds.");
    }
    // The field displays six decimals, while most frame/24 values repeat.
    // Tolerate that display rounding so leaving an unchanged duration alone
    // can never jump to the next 17-frame H3 step.
    const requested = Math.max(5, Math.ceil(seconds * FPS - 1e-4));
    const length = requested + ((5 - requested % 17) + 17) % 17;
    if (length > MAX_H3_FRAMES) {
        throw new Error(`Duration is too long; the largest H3 length is ${MAX_H3_FRAMES} frames (${(MAX_H3_FRAMES / FPS).toFixed(3)} seconds).`);
    }
    return {seconds, length};
}

export function reviewDurationText(rawFrames) {
    const length = Number(rawFrames);
    if (!Number.isInteger(length) || length < 5 || length > MAX_H3_FRAMES
            || length % 17 !== 5) {
        throw new Error("The reviewed scene has an invalid H3 frame length.");
    }
    return (length / FPS).toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}

export function reviewPlanScenePrompt(plan, oneBasedIndex, shotId = "") {
    if (!Array.isArray(plan?.shots)) return null;
    const index = Number(oneBasedIndex) - 1;
    const wantedId = String(shotId ?? "").trim();
    const shot = (wantedId
        ? plan.shots.find((item) => String(item?.id ?? "").trim() === wantedId)
        : null) ?? (Number.isInteger(index) ? plan.shots[index] : null);
    if (!shot) return null;
    return promptValueToText(shot.prompt);
}

export function applyReviewEdit(plan, oneBasedIndex, scenePrompt, seed, length = null) {
    const index = Number(oneBasedIndex) - 1;
    if (!Array.isArray(plan?.shots) || index < 0 || index >= plan.shots.length) {
        throw new Error("The reviewed scene does not exist in the plan.");
    }
    const prompt = String(scenePrompt ?? "").replace(/\r\n?/g, "\n").trim();
    if (!prompt && !sharedPrompt(plan).text.trim()) {
        throw new Error("Retry requires a scene prompt or shared prompt.");
    }
    const normalizedSeed = reviewSeed(seed);
    plan.shots[index].prompt = promptTextToLines(prompt);
    plan.shots[index].seed = normalizedSeed;
    if (length !== null && length !== undefined) {
        const normalizedLength = Number(length);
        if (!Number.isInteger(normalizedLength) || normalizedLength < 5
                || normalizedLength > MAX_H3_FRAMES
                || normalizedLength % 17 !== 5) {
            throw new Error("Length must be an H3-valid frame count (17k+5).");
        }
        plan.shots[index].length = normalizedLength;
        delete plan.shots[index].frames;
        delete plan.shots[index].duration_seconds;
    }
    return plan;
}

export function reviewCountdown(deadlineSeconds, nowMilliseconds = Date.now()) {
    if (deadlineSeconds === null || deadlineSeconds === undefined || deadlineSeconds === "") {
        return null;
    }
    const deadline = Number(deadlineSeconds);
    if (!Number.isFinite(deadline)) return null;
    const seconds = Math.max(0, Math.ceil(deadline - Number(nowMilliseconds) / 1000));
    const minutes = Math.floor(seconds / 60);
    const remainder = String(seconds % 60).padStart(2, "0");
    return {seconds, text: `${minutes}:${remainder}`};
}

export function reviewLocalDeadline(
    deadlineSeconds,
    serverNowSeconds,
    clientNowMilliseconds = Date.now(),
) {
    if (deadlineSeconds === null || deadlineSeconds === undefined || deadlineSeconds === "") {
        return null;
    }
    const deadline = Number(deadlineSeconds);
    const serverNow = Number(serverNowSeconds);
    if (!Number.isFinite(deadline) || !Number.isFinite(serverNow)) return null;
    return Number(clientNowMilliseconds) / 1000 + Math.max(0, deadline - serverNow);
}

export function acceptedPreviewDisposition(pin, payload) {
    if (!pin) return "none";
    const pinnedRun = String(pin.runName ?? "");
    const incomingRun = String(payload?.run_name ?? "");
    if (pinnedRun && incomingRun && pinnedRun !== incomingRun) return "release";
    const pinnedScene = Number(pin.clipIndex);
    const incomingScene = Number(payload?.clip_index);
    if (!Number.isInteger(pinnedScene) || !Number.isInteger(incomingScene)) {
        return "hold";
    }
    if (incomingScene < pinnedScene) return "ignore";
    if (incomingScene > pinnedScene) return "release";
    const pinnedToken = String(pin.token ?? "");
    const incomingToken = String(payload?.token ?? "");
    return pinnedToken && incomingToken && pinnedToken !== incomingToken
        ? "release" : "hold";
}

export function checkpointResumeOptions(checkpoints, clipCount) {
    const total = Number(clipCount);
    if (!Number.isInteger(total) || total < 1) return [];
    const byResumeScene = new Map();
    for (const item of checkpoints ?? []) {
        const savedScene = Number(item?.scene);
        const resumeScene = Number(item?.resume_scene ?? savedScene + 1);
        if (!item?.ready || !Number.isInteger(savedScene) || savedScene < 1
            || !Number.isInteger(resumeScene) || resumeScene < 2
            || resumeScene > total) continue;
        byResumeScene.set(resumeScene, {
            savedScene,
            resumeScene,
            sceneId: String(item.scene_id ?? `clip_${String(savedScene).padStart(4, "0")}`),
            video: item.video ?? null,
            partialVideo: item.partial_video ?? null,
        });
    }
    return [...byResumeScene.values()].sort((left, right) =>
        left.resumeScene - right.resumeScene);
}

export function checkpointRevisionChain(revisions, resumeScene) {
    const nextScene = Number(resumeScene);
    if (!Number.isInteger(nextScene) || nextScene < 2) return [];
    const grouped = new Map();
    for (const item of revisions ?? []) {
        const scene = Number(item?.scene);
        const revision = String(item?.revision ?? "");
        if (!item?.ready || !Number.isInteger(scene) || scene < 1
                || scene >= nextScene || !/^[0-9a-f]{32}$/.test(revision)) {
            continue;
        }
        const normalized = {
            scene,
            sceneId: String(item.scene_id ?? `clip_${String(scene).padStart(4, "0")}`),
            revision,
            active: Boolean(item.active),
            createdAt: String(item.created_at ?? ""),
            seed: String(item.seed ?? ""),
            sizeBytes: Math.max(0, Number(item.size_bytes) || 0),
            promptPreview: String(item.prompt_preview ?? ""),
            video: item.preview_video ?? item.video ?? null,
        };
        const entries = grouped.get(scene) ?? [];
        entries.push(normalized);
        grouped.set(scene, entries);
    }
    const chain = [];
    for (let scene = 1; scene < nextScene; scene += 1) {
        const entries = grouped.get(scene) ?? [];
        entries.sort((left, right) => {
            if (left.active !== right.active) return left.active ? -1 : 1;
            return right.createdAt.localeCompare(left.createdAt)
                || right.revision.localeCompare(left.revision);
        });
        if (!entries.length) return [];
        chain.push({scene, revisions: entries});
    }
    return chain;
}

export function applyCheckpointRevisionSet(plan, revisions, {
    useEffectivePrompts = false,
    useTipSharedPrompt = false,
} = {}) {
    if (!plan || !Array.isArray(plan.shots)) {
        throw new Error("The active Plan has no scenes.");
    }
    let shared = null;
    for (const revision of revisions ?? []) {
        const scene = Number(revision?.scene);
        const index = scene - 1;
        if (!Number.isInteger(scene) || index < 0 || index >= plan.shots.length) {
            throw new Error("A restored checkpoint scene is outside the active Plan.");
        }
        const length = Number(revision.raw_frames);
        if (!Number.isInteger(length) || length < 5 || length > MAX_H3_FRAMES
                || length % 17 !== 5) {
            throw new Error(`Restored scene ${scene} has an invalid H3 frame length.`);
        }
        const steps = Number(revision.steps);
        if (!Number.isInteger(steps) || steps < 1) {
            throw new Error(`Restored scene ${scene} has an invalid step count.`);
        }
        const prefix = String(revision.prompt_prefix ?? "");
        if (shared === null || useTipSharedPrompt) shared = prefix;
        if (!useTipSharedPrompt && prefix !== shared) {
            throw new Error("Restored checkpoint revisions use different shared prompts.");
        }
        const shot = plan.shots[index];
        if (revision.scene_id) shot.id = String(revision.scene_id);
        const prompt = useEffectivePrompts
            ? revision.effective_scene_prompt ?? revision.scene_prompt ?? ""
            : revision.scene_prompt ?? revision.effective_scene_prompt ?? "";
        shot.prompt = promptTextToLines(prompt);
        shot.seed = reviewSeed(revision.seed);
        shot.length = length;
        shot.steps = steps;
        if (Object.hasOwn(revision, "context_length")) {
            shot.context_length = Number(revision.context_length);
            sceneContextLength(shot);
        } else {
            delete shot.context_length;
        }
        if (Object.hasOwn(revision, "audio_context_length")) {
            shot.audio_context_length = Number(revision.audio_context_length);
            sceneAudioContextLength(
                shot, 22, sceneContextLength(shot),
            );
        } else {
            delete shot.audio_context_length;
        }
        if (Array.isArray(revision.visual_context_blocks)) {
            shot.visual_context_blocks = revision.visual_context_blocks.map(
                (block) => ({
                    source:String(block?.source ?? ""),
                    frames:Number(block?.frames),
                    ...(Object.hasOwn(block ?? {}, "start_frame")
                        ? {start_frame:Number(block.start_frame)} : {}),
                }),
            );
            delete shot.visual_context_source;
            delete shot.visual_context_start_frame;
            delete shot.visual_context_lead_source;
            delete shot.visual_context_lead_frames;
            delete shot.visual_context_lead_start_frame;
            sceneVisualContextBlocks(
                plan, scene, sceneContextLength(shot),
            );
        } else {
            if (Object.hasOwn(revision, "visual_context_source")) {
                shot.visual_context_source = String(
                    revision.visual_context_source,
                );
                sceneVisualContextSource(plan, scene);
            } else {
                delete shot.visual_context_source;
            }
            if (Object.hasOwn(revision, "visual_context_start_frame")) {
                const start = Number(revision.visual_context_start_frame);
                if (!Number.isInteger(start) || start < 0) {
                    throw new Error(
                        `Restored scene ${scene} has an invalid visual context start frame.`,
                    );
                }
                shot.visual_context_start_frame = start;
            } else {
                delete shot.visual_context_start_frame;
            }
            if (Object.hasOwn(revision, "visual_context_lead_source")) {
                shot.visual_context_lead_source = String(
                    revision.visual_context_lead_source,
                );
                shot.visual_context_lead_frames = Number(
                    revision.visual_context_lead_frames,
                );
                sceneVisualContextLeadSource(plan, scene);
                sceneVisualContextSource(plan, scene);
                sceneVisualContextLeadFrames(shot, sceneContextLength(shot));
                if (Object.hasOwn(
                    revision, "visual_context_lead_start_frame",
                )) {
                    const start = Number(
                        revision.visual_context_lead_start_frame,
                    );
                    if (!Number.isInteger(start) || start < 0) {
                        throw new Error(
                            `Restored scene ${scene} has an invalid composed visual context start frame.`,
                        );
                    }
                    shot.visual_context_lead_start_frame = start;
                } else {
                    delete shot.visual_context_lead_start_frame;
                }
            } else {
                delete shot.visual_context_lead_source;
                delete shot.visual_context_lead_frames;
                delete shot.visual_context_lead_start_frame;
            }
            delete shot.visual_context_blocks;
        }
        if (Object.hasOwn(revision, "continuation_mode")) {
            shot.continuation_mode = String(revision.continuation_mode);
            sceneContinuationMode(shot);
        } else {
            delete shot.continuation_mode;
        }
        if (Object.hasOwn(revision, "video_blend_frames")) {
            shot.video_blend_frames = Number(revision.video_blend_frames);
            sceneVideoBlendFrames(shot, 0, sceneContextLength(shot));
        } else {
            delete shot.video_blend_frames;
        }
        if (Object.hasOwn(revision, "context_spatial_proxy")) {
            const proxy = String(revision.context_spatial_proxy ?? "")
                .trim().toLowerCase();
            if (!["rgb_5_6", "latent_5_6"].includes(proxy)) {
                throw new Error(`Restored scene ${scene} has an invalid context spatial proxy.`);
            }
            shot.context_spatial_proxy = proxy;
        } else {
            delete shot.context_spatial_proxy;
        }
        for (const [key, allowed] of [
            ["source_reference", ["on", "off"]],
            ["generated_continuity", ["on", "off"]],
            ["source_audio_target", ["locked", "off"]],
        ]) {
            if (!Object.hasOwn(revision, key)) {
                delete shot[key];
                continue;
            }
            const value = String(revision[key] ?? "").trim().toLowerCase();
            if (!allowed.includes(value)) {
                throw new Error(`Restored scene ${scene} has an invalid ${key}.`);
            }
            shot[key] = value;
        }
        if (Object.hasOwn(revision, "lora_route")) {
            shot.lora_route = String(revision.lora_route);
            if (sceneLoRARoute(shot) === "base") delete shot.lora_route;
        } else {
            delete shot.lora_route;
        }
        if (Object.hasOwn(revision, "prompt_seed_mode")) {
            const mode = String(revision.prompt_seed_mode ?? "")
                .trim().toLowerCase();
            if (mode === "fixed") {
                shot.prompt_seed_mode = "fixed";
                shot.prompt_seed = reviewSeed(revision.prompt_seed);
            } else if (mode === "randomize") {
                shot.prompt_seed_mode = "randomize";
                delete shot.prompt_seed;
            } else if (mode === "inherit") {
                delete shot.prompt_seed_mode;
                delete shot.prompt_seed;
            } else {
                throw new Error(`Restored scene ${scene} has an invalid prompt seed mode.`);
            }
        } else {
            delete shot.prompt_seed_mode;
            delete shot.prompt_seed;
        }
        delete shot.frames;
        delete shot.duration_seconds;
    }
    if (shared !== null) {
        const current = sharedPrompt(plan);
        plan[current.key] = promptTextToLines(shared);
    }
    return plan;
}

export function applyCheckpointRevisionSeeds(plan, revisions) {
    if (!plan || !Array.isArray(plan.shots)) {
        throw new Error("The active Plan has no scenes.");
    }
    for (const revision of revisions ?? []) {
        const scene = Number(revision?.scene);
        const index = scene - 1;
        if (!Number.isInteger(scene) || index < 0 || index >= plan.shots.length) {
            throw new Error("An activated checkpoint scene is outside the active Plan.");
        }
        plan.shots[index].seed = reviewSeed(revision.seed);
    }
    return plan;
}
