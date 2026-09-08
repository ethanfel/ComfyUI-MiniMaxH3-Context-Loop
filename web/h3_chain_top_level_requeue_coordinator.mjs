export async function classifySubmissionOutcome({outcome, error, accepted, rejected, uncertain}) {
    if (error) {
        if (submissionFailure(error) === "rejected") { await rejected(); return {kind:"rejected"}; }
        await uncertain(); return {kind:"uncertain"};
    }
    const promptId = String(outcome?.promptId ?? outcome?.prompt_id ?? "").trim();
    if (outcome?.accepted === true && promptId || promptId) { await accepted(promptId); return {kind:"accepted", promptId}; }
    if (outcome === false || outcome?.accepted === false || outcome?.kind === "rejected") { await rejected(); return {kind:"rejected"}; }
    await uncertain(); return {kind:"uncertain"};
}

export async function releaseHandoffChecked({api, apiBase, runName, handoffId, reason}) {
    const acceptedRunName = String(runName ?? "").trim();
    const acceptedHandoffId = String(handoffId ?? "").trim();
    if (!acceptedRunName) throw new Error("Releasing a handoff requires a run name.");
    if (!acceptedHandoffId) throw new Error("Releasing a handoff requires a handoff ID.");
    const response = await api.fetchApi(`${apiBase}/handoffs/release`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({run_name: acceptedRunName, handoff_id: acceptedHandoffId, reason}),
    });
    if (response.ok) return;
    let detail = "";
    try {
        const payload = await response.json();
        detail = String(payload?.error ?? payload?.message ?? "").trim();
    } catch (_) { /* HTTP status below is the fallback. */ }
    throw new Error(`Releasing the claimed H3 handoff failed: ${detail || `HTTP ${response.status}`}. The handoff may remain claimed; recover/release it manually.`);
}

export async function handleUncertainSubmission({runName, handoffId, markUncertain}) {
    if (!String(runName ?? "").trim() || !String(handoffId ?? "").trim()) throw new Error("Uncertain delivery requires run and handoff identity.");
    await markUncertain(runName, handoffId, "uncertain");
    return {kind: "uncertain"};
}

export async function handleConfirmedSubmissionRejection({runName, handoffId, releaseHandoff}) {
    if (!String(runName ?? "").trim() || !String(handoffId ?? "").trim()) {
        throw new Error("Confirmed rejection requires run and handoff identity.");
    }
    await releaseHandoff(runName, handoffId);
    return {kind: "rejected", released: true};
}

export async function finalizeAcceptedSubmission({runName, handoffId, promptId, transitionQueued, trackContinuation}) {
    const acceptedPromptId = String(promptId ?? "").trim();
    if (!acceptedPromptId) throw new Error("Accepted submission requires a prompt_id.");
    await transitionQueued(runName, handoffId, "queued", acceptedPromptId);
    await trackContinuation(runName, handoffId, acceptedPromptId);
    return {kind: "accepted", promptId: acceptedPromptId};
}

export function runBeforeQueuedHooks(graph, {isPartialExecution = false} = {}) {
    const seenGraphs = new Set(), seenWidgets = new Set();
    const visit = current => { if (!current || seenGraphs.has(current)) return; seenGraphs.add(current);
        for (const node of current.nodes ?? current._nodes ?? []) { for (const widget of node.widgets ?? []) { if (!seenWidgets.has(widget)) { seenWidgets.add(widget); widget.beforeQueued?.({isPartialExecution}); } } visit(node.subgraph); }
    };
    visit(graph);
}

// Testable delivery primitive shared by the browser coordinator.  It keeps
// prompt submission certainty separate from the UI/event wiring.
export async function submitWithPromptIdentity({app, api, current = () => {}}) {
    const checkPreDelivery = () => {
        try { current(); }
        catch (error) {
            const cancellation = error instanceof Error ? error : new Error(String(error));
            cancellation.preDelivery = true;
            throw cancellation;
        }
    };
    if (typeof app.graphToPrompt === "function" && typeof api.queuePrompt === "function") {
        runBeforeQueuedHooks(app.graph);
        const prompt = await app.graphToPrompt(app.graph);
        checkPreDelivery();
        const result = await api.queuePrompt(0, prompt);
        const promptId = String(result?.prompt_id ?? "");
        return promptId ? {kind: "accepted", promptId} : {kind: "uncertain", promptId: ""};
    }
    checkPreDelivery();
    const result = await app.queuePrompt(0, 1);
    if (result === false) return {kind: "rejected", promptId: ""};
    const promptId = typeof result === "object" ? String(result.prompt_id ?? result.promptId ?? "") : "";
    return promptId ? {kind: "accepted", promptId} : {kind: "uncertain", promptId: ""};
}

// Shared async cancellation boundary.  Browser wiring supplies the concrete
// queue/checkpoint/workflow adapters; Node tests use deterministic fakes.
export async function runRequeueLifecycle({current, waitSafe, cleanup, resolveRun, loadCheckpoint, listHandoffs, matchHandoff, claimHandoff, prepareResume, select, claim, prepare, submit, release, uncertain}) {
    current(); await waitSafe(); current(); await cleanup(); current();
    if (resolveRun) {
        const context = await resolveRun();
        const runName = context?.runName;
        if (!runName || context?.runtimeRunName && context.runtimeRunName !== runName) return null;
        const checkpoint = await loadCheckpoint(runName, context);
        const handoff = matchHandoff(await listHandoffs(runName), {...context, sourceRevision:String(checkpoint?.revision || ""), checkpointSha:String(checkpoint?.metadata_sha256 || "")});
        if (!handoff) return null;
        current(); await claimHandoff(runName, handoff, context);
        try { current(); } catch (error) {
            await release?.(handoff, runName);
            return {kind: "cancelled", runName, handoff, checkpoint, context, cancellationError: error};
        }
        await prepareResume?.(runName, handoff, context);
        try { current(); } catch (error) {
            await release?.(handoff, runName);
            return {kind: "cancelled", runName, handoff, checkpoint, context, cancellationError: error};
        }
        if (submit) {
            try { return {runName, handoff, checkpoint, context, submission: await submit(runName, handoff, context)}; }
            catch (submissionError) {
                if (submissionError?.preDelivery === true) {
                    await release?.(handoff, runName);
                    return {kind: "cancelled", runName, handoff, checkpoint, context, cancellationError: submissionError};
                }
                return {runName, handoff, checkpoint, context, submissionError};
            }
        }
        return {runName, handoff, checkpoint, context};
    }
    const handoff = await select();
    if (!handoff) return null;
    await claim(handoff);
    try {
        current();
        await prepare(handoff); current();
        const result = await submit(handoff);
        if (result?.kind === "rejected") { await release(handoff); return result; }
        if (result?.kind !== "accepted") { await uncertain(handoff); return {kind: "uncertain"}; }
        return result;
    } catch (error) {
        if (error?.preDelivery === true || submissionFailure(error) === "rejected") { await release(handoff); return {kind: "rejected"}; }
        await uncertain(handoff); return {kind: "uncertain"};
    }
}

// Identity selection is kept in the coordinator boundary so callers cannot
// accidentally fall back to list order. `match` is the shared strict matcher.
export function authoritativeRunName(planNode) {
    const widget = (node, name) => node?.widgets?.find((item) => item.name === name)?.value;
    const input = planNode?.inputs?.find((item) => item.name === "project_assets");
    const link = input?.link != null ? planNode?.graph?.links?.[input.link] : null;
    const manager = link ? planNode?.graph?.getNodeById?.(link.origin_id) : null;
    const managed = manager?.comfyClass === "MiniMaxH3ProjectAssetManager"
        ? String(widget(manager, "run_name") ?? "").trim() : "";
    return managed || String(widget(planNode, "run_name") ?? "").trim();
}

export const resolveProjectRun = authoritativeRunName;

export async function selectAndClaim({current = () => {}, planNode, record, loadCheckpoint = async () => ({revision: record.sourceRevision, metadata_sha256: record.checkpointSha}), loadHandoffs, handoffs, match, resolveRun, claim}) {
    loadHandoffs ??= async () => handoffs;
    resolveRun ??= () => authoritativeRunName(planNode);
    current();
    const runName = await resolveRun(record);
    if (!runName || runName !== record.runName) return null;
    const checkpoint = await loadCheckpoint(runName, record);
    const completed = {...record, sourceRevision: String(checkpoint?.revision || ""), checkpointSha: String(checkpoint?.metadata_sha256 || "")};
    const handoff = match(await loadHandoffs(runName), completed);
    if (!handoff) return null;
    current();
    await claim(runName, handoff.handoff_id);
    current();
    return Object.assign(handoff, {_resolvedRunName: runName, _completed: completed});
}

export async function deliverClaimed({current, handoff, prepare, submit, release, transition, track}) {
    try {
        current(); await prepare(handoff); current();
        const result = await submit(handoff);
        if (result?.kind === "accepted" && result.promptId) {
            await transition(handoff, "queued", result.promptId); track?.(result.promptId); return result;
        }
        if (result?.kind === "rejected") { await release(handoff); return result; }
        await transition(handoff, "uncertain"); return {kind:"uncertain"};
    } catch (error) {
        if (error?.preDelivery === true || submissionFailure(error) === "rejected") { await release(handoff); return {kind:"rejected"}; }
        await transition(handoff, "uncertain"); return {kind:"uncertain"};
    }
}

export function submissionFailure(error) {
    return Number(error?.status) >= 400 && Number(error?.status) < 500
        ? "rejected" : "uncertain";
}

// Prompt-specific continuation bookkeeping is deliberately tiny and has no
// browser dependency, so the production wrapper and deterministic tests share
// the same acceptance/start/failure identity rules.
export function createContinuationTracker({transition, reportError = () => {}}) {
    let waiting = null;
    const earlyStarts = new Set();
    const remember = promptId => { earlyStarts.delete(promptId); earlyStarts.add(promptId); if (earlyStarts.size > 32) earlyStarts.delete(earlyStarts.values().next().value); };
    const consume = async current => { try { await transition(current.runName, current.handoffId, "consumed"); return true; } catch (error) { reportError(error); return false; } };
    return {
        async track(runName, handoffId, promptId) {
            const id = String(promptId); const current = {runName, handoffId, promptId:id};
            if (earlyStarts.delete(id)) return consume(current);
            waiting = current;
            return false;
        },
        async started(promptId) {
            const id = String(promptId);
            if (!waiting || waiting.promptId !== id) { remember(id); return false; }
            const current = waiting;
            waiting = null;
            return consume(current);
        },
        failed(promptId) {
            if (!waiting || waiting.promptId !== String(promptId)) return null;
            const current = waiting;
            waiting = null;
            return current;
        },
        current() { return waiting; },
    };
}
