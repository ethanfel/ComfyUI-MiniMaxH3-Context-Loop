import {MAX_SEED} from "./h3_chain_plan_core.mjs?v=0.7.8";

function normalizedPositiveInteger(value, label) {
    const number = Number(value);
    if (!Number.isInteger(number) || number < 1) {
        throw new Error(`${label} must be a positive integer.`);
    }
    return number;
}

export function applySceneReroll(plan, oneBasedIndex, seed) {
    const scene = normalizedPositiveInteger(oneBasedIndex, "Scene index");
    if (!Array.isArray(plan?.shots) || scene > plan.shots.length) {
        throw new Error(`Scene ${scene} does not exist in the Plan.`);
    }
    let normalizedSeed;
    try {
        normalizedSeed = BigInt(String(seed));
    } catch (_error) {
        throw new Error("Reroll seed must be an unsigned 64-bit integer.");
    }
    if (normalizedSeed < 0n || normalizedSeed > MAX_SEED) {
        throw new Error("Reroll seed is outside the unsigned 64-bit range.");
    }
    plan.shots[scene - 1].seed = normalizedSeed.toString();
    return plan;
}

export function resumeSelection(oneBasedIndex, endClip, clipCount) {
    const scene = normalizedPositiveInteger(oneBasedIndex, "Scene index");
    const end = normalizedPositiveInteger(endClip, "Range end");
    const total = normalizedPositiveInteger(clipCount, "Scene count");
    if (scene > end || end > total) {
        throw new Error(`Invalid reroll range ${scene}:${end} for ${total} scenes.`);
    }
    return {
        startClip: scene,
        sceneRange: end < total
            ? scene === end ? String(scene) : `${scene}:${end}`
            : "",
    };
}

export function activeSceneFromOutput(output) {
    const values = output?.h3_chain_active_scene;
    const value = Array.isArray(values) ? values.at(-1) : null;
    if (!value || typeof value !== "object") return null;
    const clipIndex = Number(value.clip_index);
    const clipCount = Number(value.clip_count);
    const endClip = Number(value.end_clip ?? clipCount);
    const branchId = String(value._branch_id ?? "main");
    if (!Number.isInteger(clipIndex) || clipIndex < 1
        || !Number.isInteger(clipCount) || clipCount < clipIndex
        || !Number.isInteger(endClip) || endClip < clipIndex || endClip > clipCount
        || (branchId !== "main" && !/^[0-9a-f]{32}$/.test(branchId))) {
        return null;
    }
    return {
        runName: String(value.run_name ?? "").trim(),
        branchId,
        clipIndex,
        clipCount,
        endClip,
        shotId: String(value.shot_id ?? `scene_${clipIndex}`),
        seed: String(value.seed ?? ""),
        workflowFingerprint: String(value.workflow_fingerprint ?? ""),
    };
}
