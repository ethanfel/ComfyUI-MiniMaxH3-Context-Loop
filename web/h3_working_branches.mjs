// Shared request/selection protocol. Branch names are labels, never paths.
export function workingBranchId(value) {
    const id = String(value || "main");
    if (id !== "main" && !/^[0-9a-f]{32}$/.test(id)) throw new Error("Invalid working branch id.");
    return id;
}

export function branchRequestPath(path, id = "main") {
    if (workingBranchId(id) === "main") return path;
    return `${path}${path.includes("?") ? "&" : "?"}branch_id=${encodeURIComponent(workingBranchId(id))}`;
}

export function branchSelectionJson(value, id) {
    if (!value) return value;
    const selection = JSON.parse(value);
    const selected = workingBranchId(id);
    if (selected === "main") delete selection._branch_id;
    else selection._branch_id = selected;
    return JSON.stringify(selection);
}

export function authoringSignature(authoring) {
    if (!authoring) return "";
    const value = structuredClone(authoring);
    const plan = JSON.parse(value.plan_json);
    delete plan._branch_id; // Routing is compared separately from authored settings.
    value.plan_json = plan;
    const ordered = item => Array.isArray(item) ? item.map(ordered)
        : item && typeof item === "object"
            ? Object.fromEntries(Object.keys(item).sort().map(key => [key, ordered(item[key])])) : item;
    return JSON.stringify(ordered(value));
}

export function branchOperationId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
}

// Browser-local recovery, not another writer of the shared branch snapshot.
export class BranchDrafts {
    constructor(storage, client) { Object.assign(this, {storage, client}); }
    key(run, id) { return `h3-branch-draft-v1:${this.client}:${encodeURIComponent(run)}:${workingBranchId(id)}`; }
    read(run, id) {
        const raw = this.storage.getItem(this.key(run, id));
        if (!raw) return null;
        const draft = JSON.parse(raw);
        authoringSignature(draft.authoring);
        return draft;
    }
    save(run, id, draft) {
        const older = draft.older ?? this.read(run, id)?.older;
        this.storage.setItem(this.key(run, id), JSON.stringify({...draft, ...(older ? {older} : {}), updated_at:Date.now()}));
    }
    stash(run, id, draft) {
        const previous = this.read(run, id);
        const older = previous ? [previous, ...(previous.older ?? [])] : [];
        const signature = value => JSON.stringify([authoringSignature(value.authoring), value.recovery ?? null]);
        const seen = new Set([signature(draft)]);
        const kept = older.filter(value => {
            const key = signature(value);
            if (seen.has(key)) return false;
            seen.add(key); return true;
        }).map(({older, ...value}) => value);
        this.save(run, id, {...draft, older:kept});
    }
    pending(value = undefined) {
        const key = `h3-branch-pending-v1:${this.client}`;
        if (value === undefined) return JSON.parse(this.storage.getItem(key) || "null");
        if (value) this.storage.setItem(key, JSON.stringify(value));
        else this.storage.removeItem(key);
    }
}

// Roll back widget values without invoking the callback that just failed.
export function branchWidgetTransaction(nodes, action) {
    const unique = [...new Set(nodes.filter(Boolean))];
    const snapshots = unique.map(node => ({node, properties:structuredClone(node.properties ?? {}),
        widgets:(node.widgets ?? []).filter(w => w.serialize !== false)
            .map(widget => ({widget, value:structuredClone(widget.value)}))}));
    try { return action(); }
    catch (error) {
        for (const {node, properties, widgets} of snapshots) {
            node.properties = properties;
            for (const {widget, value} of widgets) widget.value = value;
        }
        throw error;
    }
}

export class StudioBranches {
    constructor({request, capture, apply, flush, changed, selected = "main",
        binding = null, rememberBinding = () => {}, drafts = null, settle = async () => {},
        isCurrent = () => true, captureRecovery = () => null, restoreRecovery = async () => {}, editStamp = () => 0}) {
        Object.assign(this, {request, capture, apply, flush, changed, binding, rememberBinding, drafts, settle, isCurrent,
            captureRecovery, restoreRecovery, editStamp});
        this.selected = workingBranchId(selected);
        this.records = [];
        this.defaultBranch = "main";
        this.busy = false;
        this.error = "";
        this.run = "";
        this.epoch = 0;
        this.ready = false;
        this.conflict = "";
        this.savedSignature = "";
        this.draftRecovery = null;
        this.draftStatus = "";
        this.pending = null;
        this.switchTarget = null;
        try { this.pending = drafts?.pending() ?? null; }
        catch (error) { this.draftStatus = `Local recovery unavailable: ${error.message}`; }
    }

    async refresh(run) {
        const epoch = ++this.epoch;
        if (run !== this.run) this.switchTarget = null;
        this.run = run;
        this.ready = false;
        const selected = this.selected;
        const data = await this.request({action:"list", run_name:run});
        const record = await this.request({action:"load", run_name:run, branch_id:selected});
        if (run !== this.run || epoch !== this.epoch || selected !== this.selected || !this.isCurrent(run, selected)) return;
        this.records = data.branches;
        this.defaultBranch = data.default_branch;
        const known = this.binding?.run_name === run && this.binding.branch_id === selected
            && this.binding.revision === record.revision;
        const same = authoringSignature(record.authoring) === authoringSignature(this.capture());
        this.conflict = known || same || !record.authoring ? ""
            : "This workflow differs from the saved branch. Reload saved branch, or keep these edits as a new empty branch.";
        if (!this.conflict) this.adopt(record);
        this.ready = true;
        this.readDraft();
        this.changed();
    }

    adopt(record) {
        this.binding = {run_name:this.run, branch_id:record.id, revision:record.revision ?? ""};
        this.rememberBinding(structuredClone(this.binding));
        this.savedSignature = authoringSignature(record.authoring);
        const existing = this.records.find(item => item.id === record.id);
        if (existing) {
            delete existing.authoring_recovery;
            Object.assign(existing, record);
        }
        else this.records.push(record);
        this.conflict = "";
    }

    readDraft() {
        this.draftRecovery = null;
        try {
            const saved = this.drafts?.read(this.run, this.selected);
            const draft = [saved, ...(saved?.older ?? [])].find(value => value &&
                (value.recovery || authoringSignature(value.authoring) !== authoringSignature(this.capture())));
            if (draft) {
                this.draftRecovery = draft;
                this.draftStatus = "A local recovery draft is available; restore it before editing, or reload the saved branch.";
            }
        } catch (error) { this.draftStatus = `Local recovery unavailable: ${error.message}`; }
    }

    preserveDraft() {
        if (!this.drafts || !this.run || this.draftRecovery) return;
        const binding = this.binding?.run_name === this.run && this.binding.branch_id === this.selected
            ? this.binding : null;
        this.drafts.save(this.run, this.selected, {authoring:this.capture(), revision:binding?.revision ?? null,
            recovery:this.captureRecovery()});
        this.draftStatus = "Recovery draft saved in this browser.";
    }

    preserveNavigationDraft() {
        if (!this.drafts && !this.captureRecovery() && authoringSignature(this.capture()) === this.savedSignature) return;
        if (!this.drafts) throw new Error("Browser recovery is unavailable. Save or export your local edits before switching without saving.");
        const binding = this.binding?.run_name === this.run && this.binding.branch_id === this.selected ? this.binding : null;
        // Keep an older recovery draft too: navigation must never replace it
        // with the saved settings currently on screen.
        this.drafts.stash(this.run, this.selected, {authoring:this.capture(),
            revision:binding?.revision ?? null, recovery:this.captureRecovery()});
        this.draftStatus = "Local prompts, settings and pending edits saved in browser recovery.";
    }

    observe() {
        if (this.busy || !this.ready || this.draftRecovery) return;
        try {
            const signature = authoringSignature(this.capture());
            if (signature === this.observedSignature) return;
            if (signature !== this.savedSignature) this.preserveDraft();
            this.observedSignature = signature;
        } catch (error) { this.draftStatus = `Draft not saved: ${error.message}`; }
    }

    async mutation(body) {
        if (this.pending) throw new Error("An earlier save may have succeeded. Retry pending operation before making another change.");
        this.pending = {...structuredClone(body), operation_id:branchOperationId()};
        return this.sendPending();
    }

    async sendPending() {
        const body = this.pending;
        try { this.drafts?.pending(body); }
        catch (error) { this.draftStatus = `Pending request is only in memory: ${error.message}`; }
        for (let attempt = 0; attempt < 2; attempt++) {
            try {
                const result = await this.request(structuredClone(body));
                this.pending = null;
                try { this.drafts?.pending(null); } catch { /* Replaying the same ID is safe. */ }
                return result;
            } catch (error) {
                if (error.status >= 400 && error.status < 500) {
                    this.pending = null;
                    try { this.drafts?.pending(null); } catch { /* Preserve the original error. */ }
                    throw error;
                }
                if (attempt) throw new Error(`Request outcome is uncertain. Use Retry pending operation. ${error.message}`);
            }
        }
    }

    async retryPending() {
        if (this.busy || !this.pending) return;
        this.busy = true; this.changed();
        const body = this.pending, epoch = this.epoch;
        try {
            const record = await this.sendPending();
            this.error = "";
            if (epoch !== this.epoch || body.run_name !== this.run || !this.isCurrent(this.run, this.selected)) return;
            if (body.action === "save" && body.branch_id === this.selected) this.adopt(record);
            else if (body.action === "create" && !this.records.some(item => item.id === record.id)) this.records.push(record);
            this.error = "";
        } catch (error) { this.error = error.message; }
        finally { this.busy = false; this.changed(); }
    }

    async save(assertCurrent = () => {}) {
        if (!this.ready) throw new Error("Wait for working branches to load.");
        if (this.conflict) throw new Error(this.conflict);
        if (this.draftRecovery) throw new Error("Resolve the local recovery draft before saving.");
        if (this.binding?.run_name !== this.run || this.binding.branch_id !== this.selected) {
            throw new Error("Branch binding changed; reload the saved branch before saving.");
        }
        const authoring = this.capture();
        const saved = await this.mutation({action:"save", run_name:this.run,
            branch_id:this.selected, revision:this.binding.revision, authoring});
        assertCurrent();
        this.adopt(saved);
        return authoringSignature(authoring);
    }

    async perform(action, {save = true, flush = true, requireDraft = !save, navigation = false} = {}) {
        if (this.busy) return;
        const run = this.run, epoch = this.epoch, selected = this.selected;
        const assertCurrent = () => {
            if (this.run !== run || this.epoch !== epoch || this.selected !== selected || !this.isCurrent(run, selected)) throw new Error("Project or branch changed during the operation; refresh before continuing.");
        };
        this.busy = true; this.error = ""; this.changed();
        try {
            assertCurrent();
            if (!this.ready) throw new Error("Wait for working branches to load.");
            if (this.pending) throw new Error("Retry pending operation before continuing.");
            if (save && this.conflict) throw new Error(this.conflict);
            const editStamp = this.editStamp();
            if (requireDraft && !this.drafts && authoringSignature(this.capture()) !== this.savedSignature) {
                throw new Error("Browser recovery is unavailable. Save these edits as a new empty branch before reloading.");
            }
            try {
                if (navigation) this.preserveNavigationDraft();
                else this.preserveDraft();
            }
            catch (error) {
                this.draftStatus = `Local draft not saved: ${error.message}`;
                if (requireDraft) throw error;
                // Saving to the server is still possible when browser storage
                // is full. Never make local recovery a barrier to a real save.
            }
            if (flush) await this.flush();
            else await this.settle();
            assertCurrent();
            const signature = save ? await this.save(assertCurrent) : authoringSignature(this.capture());
            assertCurrent();
            const assertUnedited = () => {
                assertCurrent();
                if (signature !== authoringSignature(this.capture()) || (navigation && editStamp !== this.editStamp())) {
                    if (navigation) this.preserveNavigationDraft();
                    else this.preserveDraft();
                    throw new Error("Edits arrived during the switch. They were kept; switch again when editing is finished.");
                }
            };
            assertUnedited();
            await action(assertUnedited);
        } catch (error) {
            this.error = error?.message || String(error);
        } finally {
            this.busy = false; this.changed();
        }
    }

    async switchTo(id, {save = true} = {}) {
        id = workingBranchId(id);
        if (id === this.selected) return;
        if (this.busy) return;
        this.switchTarget = id;
        return this.perform(async (assertCurrent) => {
            const record = await this.request({action:"load", run_name:this.run, branch_id:id});
            assertCurrent();
            if (!record.authoring) throw new Error("This branch has no saved authoring snapshot yet.");
            await this.apply(record);
            this.selected = id;
            this.switchTarget = null;
            this.adopt(record);
            this.observedSignature = null;
            this.readDraft();
        }, {save, flush:save, navigation:!save});
    }

    async reloadSaved() {
        return this.perform(async assertCurrent => {
            const record = await this.request({action:"load", run_name:this.run, branch_id:this.selected});
            assertCurrent();
            if (!record.authoring) throw new Error("This branch has no saved authoring snapshot yet.");
            await this.apply(record);
            this.adopt(record);
            this.draftRecovery = null;
            this.observedSignature = authoringSignature(record.authoring);
            this.draftStatus = "Saved branch loaded. Previous local edits remain in browser recovery.";
        }, {save:false, flush:false, navigation:true});
    }

    async restoreDraft() {
        if (!this.draftRecovery) return;
        const draft = this.draftRecovery;
        return this.perform(async assertCurrent => {
            assertCurrent();
            await this.apply({id:this.selected, authoring:draft.authoring});
            await this.restoreRecovery(draft.recovery);
            if (draft.recovery && this.drafts) {
                const stored = this.drafts.read(this.run, this.selected);
                const consumed = value => value && authoringSignature(value.authoring) === authoringSignature(draft.authoring)
                    && JSON.stringify(value.recovery) === JSON.stringify(draft.recovery)
                    ? {...value, recovery:null} : value;
                this.drafts.save(this.run, this.selected, {...consumed(stored), older:(stored?.older ?? []).map(consumed)});
            }
            const record = this.records.find(item => item.id === this.selected);
            this.binding = {run_name:this.run, branch_id:this.selected, revision:draft.revision};
            this.rememberBinding(structuredClone(this.binding));
            this.conflict = draft.revision === record?.revision ? ""
                : "Recovered draft differs from the saved branch. Keep these edits as a new empty branch, or reload saved branch.";
            this.draftRecovery = null;
            this.observedSignature = null;
        }, {save:false, flush:false});
    }

    async create(name, throughScene = 0, newSeeds = false) {
        if (this.conflict && throughScene) {
            this.error = "Resolve the stale branch before forking saved clips; an empty recovery branch is still available.";
            this.changed();
            return;
        }
        return this.perform(async (assertCurrent) => {
            const run = this.run;
            const authoring = this.capture();
            if (newSeeds) {
                const plan = JSON.parse(authoring.plan_json);
                for (const shot of plan.shots) {
                    const words = crypto.getRandomValues(new Uint32Array(2));
                    shot.seed = ((BigInt(words[0]) << 32n) | BigInt(words[1])).toString();
                }
                authoring.plan_json = JSON.stringify(plan, null, 2);
            }
            const record = await this.mutation({action:"create", run_name:this.run,
                branch_id:this.selected, name, through_scene:throughScene, authoring});
            // Keep successfully published branches discoverable if UI application fails.
            if (run === this.run && !this.records.some(item => item.id === record.id)) this.records.push(record);
            assertCurrent();
            await this.apply(record);
            this.selected = record.id;
            this.adopt(record);
            this.draftRecovery = null;
            this.observedSignature = null;
        }, {save:!this.conflict, flush:!this.conflict, requireDraft:false});
    }

    async makeDefault() {
        return this.perform(async (assertCurrent) => {
            const result = await this.request({action:"default", run_name:this.run, branch_id:this.selected});
            assertCurrent();
            this.defaultBranch = result.default_branch;
        });
    }
}
