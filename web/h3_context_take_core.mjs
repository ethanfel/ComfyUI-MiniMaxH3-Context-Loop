// Exact saved context identity; no path assignment, editorial selection or seed edits.
import {safeShotId} from "./h3_chain_plan_core.mjs?v=0.7.12";

export function applyContextTake(plan, scene, revision = null) {
    const source = Number(scene);
    if (!Number.isInteger(source) || source < 1 || source >= (plan?.shots?.length ?? 0)) {
        throw new Error(`Add scene ${source + 1} to the connected Plan before choosing its context take.`);
    }
    if (revision !== null && !/^[0-9a-f]{32}$/i.test(String(revision))) {
        throw new Error("Choose an exact saved checkpoint revision.");
    }
    const result = structuredClone(plan);
    const shot = result.shots[source];
    if (revision === null) {
        delete shot.context_take;
    } else {
        const id = safeShotId(result.shots[source - 1].id, "");
        shot.context_take = {source:id && !/^\d+$/.test(id) ? id : source, revision:String(revision).toLowerCase()};
        // Use this complete take as the source; context length and mode remain
        // the user's settings. Discard windows/masks authored for another take.
        for (const field of ["visual_context_blocks", "visual_context_source", "visual_context_start_frame",
            "visual_context_lead_source", "visual_context_lead_frames", "visual_context_lead_start_frame",
            "audio_context_source", "audio_context_start_frame", "audio_context_lead_source",
            "audio_context_lead_frames", "audio_context_lead_start_frame"]) delete shot[field];
    }
    return result;
}
