// A callable surface over the mounted Studio's existing controller. This does
// not create another branch writer, draft store, or continuation scheduler.
export function studioBranchCommands(branches, {owner, available = () => ""}) {
    let busy = false, last = "", revision = 0;
    function snapshot() {
        const reason = available();
        const pending = branches.pending ? Object.fromEntries(
            ["action", "run_name", "branch_id", "operation_id"].map(key => [key, branches.pending[key]])) : null;
        const state = {
            version:1, run_name:branches.run, selected:branches.selected,
            default_branch:branches.defaultBranch, ready:branches.ready,
            busy:busy || branches.busy, available:!reason, reason,
            saved_revision:branches.binding?.revision ?? "",
            dirty:branches.observedSignature == null ? null : branches.observedSignature !== branches.savedSignature,
            conflict:branches.conflict, error:branches.error,
            draft_available:Boolean(branches.draftRecovery), draft_status:branches.draftStatus,
            pending,
            branches:branches.records.map(item => Object.fromEntries(
                ["id", "name", "revision", "source_branch", "fork_scene", "created_at"].filter(key => key in item).map(key => [key, item[key]]))),
        };
        const serialized = JSON.stringify(state);
        if (serialized !== last) { last = serialized; revision++; }
        return {...state, revision};
    }
    async function command(action, args = {}, expected, assertCurrent = () => {}) {
        const before = snapshot();
        if (!before.available) throw new Error(before.reason);
        if (before.busy) throw new Error("Another native branch action is in progress.");
        if (!expected || expected.run_name !== before.run_name || expected.selected !== before.selected || expected.revision !== before.revision) {
            throw new Error("Branch state changed. Refresh before continuing.");
        }
        const actions = {
            refresh:() => branches.refresh(before.run_name),
            save:() => branches.perform(async () => {}),
            switch:() => branches.switchTo(args.target, {save:args.save !== false}),
            create:() => branches.create(args.name, args.through_scene ?? 0, args.new_seeds === true),
            reload:() => branches.reloadSaved(),
            "restore-draft":() => branches.restoreDraft(),
            retry:() => branches.retryPending(),
            default:() => branches.makeDefault(),
        };
        if (!Object.hasOwn(actions, action)) throw new Error("Unsupported native branch action.");
        if (action === "switch" && typeof args.target !== "string") throw new Error("Choose a target branch.");
        if (action === "create" && (typeof args.name !== "string" || !args.name.trim() || args.name.trim().length > 120
            || !Number.isInteger(args.through_scene ?? 0) || (args.through_scene ?? 0) < 0)) throw new Error("Choose a branch name and a valid fork scene.");
        if (action === "retry" && !before.pending) throw new Error("There is no pending branch operation.");
        if (action === "restore-draft" && !before.draft_available) throw new Error("There is no native recovery draft.");
        assertCurrent();
        busy = true;
        const original = branches.isCurrent;
        branches.isCurrent = (...values) => {
            try { assertCurrent(); return original(...values); } catch { return false; }
        };
        try {
            await actions[action]();
            assertCurrent();
            return {state:snapshot(), ...(branches.error ? {warning:branches.error} : {})};
        } finally { branches.isCurrent = original; busy = false; }
    }
    return Object.freeze({version:1, get owner() { return owner(); }, snapshot,
        async command(...args) { const result = await command(...args); return {...result, state:snapshot()}; }});
}
