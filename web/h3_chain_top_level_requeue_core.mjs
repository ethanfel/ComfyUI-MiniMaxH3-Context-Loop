// Top-level scene requeue: pure helpers for the guarded coordinator.
//
// No ComfyUI imports: this file loads in both the browser and the node
// unit tests (tests/_top_level_requeue_js_test.mjs).
//
// The coordinator queues the SAME existing workflow again as a new top-level
// ComfyUI prompt after the previous heavyweight H3 job reaches terminal
// success. All durable state lives server-side (handoff_state.py under
// output/h3_chains/<run>/orchestration/); these helpers only interpret the
// JSON those routes return.

export const LEGACY_MODE = "recursive_legacy";
export const REQUEUE_MODE = "top_level_requeue";

export function shouldScheduleTopLevelRequeueSuccess(record) {
    return Boolean(record?.runName && record?.loopEndExecuted
        && record.executionMode === REQUEUE_MODE
        && Number(record.clipIndex) < Number(record.endClip || record.clipCount));
}

export function loopEndMatchesObservedCurrent({record, loopEndNode, resolveDisplayNode, findUpstreamCurrent}) {
    const observed = record?.displayNode ? resolveDisplayNode(record.displayNode) : null;
    const upstream = loopEndNode ? findUpstreamCurrent(loopEndNode) : null;
    return Boolean(observed && upstream && observed === upstream);
}

export function topLevelRequeueCompletionMatches(record, payload) {
    return Boolean(record?.runName && String(payload?.handoff_id ?? "").trim()
        && String(payload?.run_name ?? "") === String(record.runName)
        && Number(payload?.predecessor_scene) === Number(record.clipIndex)
        && Number(payload?.scene) === Number(record.clipIndex) + 1
        && Number(payload?.end_clip) === Number(record.endClip)
        && String(payload?.workflow_fingerprint ?? "") === String(record.workflowFingerprint));
}

export function handleTopLevelRequeueSuccessScheduling({record, scheduleRequeue}) {
    if (!shouldScheduleTopLevelRequeueSuccess(record)) return false;
    scheduleRequeue(record);
    return true;
}
export const EXECUTION_MODES = [LEGACY_MODE, REQUEUE_MODE];

export const HANDOFF_API_BASE = "/minimax_h3_context_loop";

// Spec: initial cleanup interval default, measured from terminal success.
export const DEFAULT_CLEANUP_DELAY_MS = 10750;

export function executionModeFromValue(value) {
    return String(value ?? "").trim() === REQUEUE_MODE
        ? REQUEUE_MODE
        : LEGACY_MODE;
}

export function isRequeueMode(value) {
    return executionModeFromValue(value) === REQUEUE_MODE;
}

export function cleanupDelayMs(value) {
    // null/undefined/"" means the setting is unset: fall back to the spec
    // default. An explicit 0 is a user choice (no delay) and stays 0.
    if (value == null || value === "") return DEFAULT_CLEANUP_DELAY_MS;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return DEFAULT_CLEANUP_DELAY_MS;
    return Math.round(number);
}

export function pendingNextSceneHandoffs(body) {
    const list = body?.handoffs;
    if (!Array.isArray(list)) return [];
    return list.filter((item) =>
        item && item.action === "next_scene"
        && item.status === "pending"
        && item.handoff_id != null && String(item.handoff_id) !== "");
}

// A completion may only adopt the transition it produced.  Old pending
// records remain listed for manual recovery, but must never move a run
// backwards merely because they sort first on disk.
export function matchingNextSceneHandoff(body, completed) {
    return pendingNextSceneHandoffs(body).find((item) =>
        String(item.working_branch_id ?? "main") === String(completed?.workingBranchId ?? "main")
        &&
        Number(item.predecessor_scene) === Number(completed?.clipIndex)
        && Number(item.start_clip) === Number(completed?.clipIndex) + 1
        && Number(item.end_clip) === Number(completed?.endClip)
        && String(item.workflow_fingerprint || "") ===
            String(completed?.workflowFingerprint || "")
        // Missing identity is a hard failure, never a wildcard.  The
        // checkpoint listing supplies these authoritative committed values.
        && typeof completed?.sourceRevision === "string"
        && completed.sourceRevision !== ""
        && item.source_revision === completed.sourceRevision
        && typeof completed?.checkpointSha === "string"
        && completed.checkpointSha !== ""
        && item.source_checkpoint_sha256 === completed.checkpointSha
        && (!completed?.handoffId || item.handoff_id === completed.handoffId)
    ) ?? null;
}

export function resumeHint(handoff) {
    const resume = handoff?.resume;
    if (!resume || typeof resume !== "object") return null;
    const startClip = Number(resume.start_clip);
    if (!Number.isInteger(startClip) || startClip < 1) return null;
    return {
        startClip,
        sceneRange: String(resume.scene_range ?? ""),
        endClip: Number(resume.end_clip ?? 0),
        totalScenes: Number(resume.total_scenes ?? 0),
    };
}

// The checkpoint that must be ready before the handoff's start scene can
// safely be generated. Scene 0 means "no predecessor" (first scene).
export function predecessorScene(resume) {
    if (!resume || !Number.isInteger(resume.startClip)) return 0;
    return resume.startClip > 1 ? resume.startClip - 1 : 0;
}

export function checkpointPredecessorReady(checkpoints, scene) {
    if (!Number.isInteger(scene) || scene < 1) return true;
    if (!Array.isArray(checkpoints)) return false;
    return checkpoints.some((item) =>
        item && item.ready === true && Number(item.scene) === scene);
}

// The queue is safe only when ComfyUI reports both a running AND a pending
// list and both are empty. An unknown/malformed body is unsafe: the
// coordinator must keep the durable handoff and not queue.
export function isQueueSafe(queue) {
    const running = queue?.queue_running;
    const pending = queue?.queue_pending;
    if (!Array.isArray(running) || !Array.isArray(pending)) return false;
    return running.length === 0 && pending.length === 0;
}
