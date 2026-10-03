import {parsePlanJson} from "./h3_chain_plan_core.mjs?v=0.7.11";

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
    // Compare the same representation Studio actually loads. Legacy string
    // prompts and current line arrays are equivalent; preserve exact seeds.
    const plan = parsePlanJson(value.plan_json);
    delete plan._branch_id; // Routing is compared separately from authored settings.
    value.plan_json = plan;
    // The base-seed widget can serialize a safe integer as either a number
    // or decimal text. Compare those equally without rounding uint64 seeds.
    const seed = value.base_seed;
    if ((typeof seed === "number" && Number.isSafeInteger(seed) && seed >= 0)
            || (typeof seed === "string" && /^\d+$/.test(seed.trim()))) {
        const exact = BigInt(typeof seed === "string" ? seed.trim() : seed);
        if (exact <= 18446744073709551615n) value.base_seed = exact.toString();
    }
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
    parse(raw) {
        if (!raw) return null;
        const draft = JSON.parse(raw);
        authoringSignature(draft.authoring);
        return draft;
    }
    async read(run, id) { return this.parse(await this.storage.getItem(this.key(run, id))); }
    update(key, transform) {
        // IndexedDB performs read/modify/write in one transaction, including
        // across tabs. Keep injected Storage-compatible test adapters ordered.
        if (this.storage.updateItem) return this.storage.updateItem(key, transform);
        const write = (this.writing ?? Promise.resolve()).then(async () => {
            const next = transform(await this.storage.getItem(key));
            if (next == null) await this.storage.removeItem(key);
            else await this.storage.setItem(key, next);
        });
        this.writing = write.catch(() => {});
        return write;
    }
    save(run, id, draft) {
        return this.update(this.key(run, id), raw => {
            const older = draft.older ?? this.parse(raw)?.older;
            return JSON.stringify({...draft, ...(older ? {older} : {}), updated_at:Date.now()});
        });
    }
    revise(run, id, transform) {
        return this.update(this.key(run, id), raw => {
            const current = this.parse(raw);
            return current ? JSON.stringify({...transform(current), updated_at:Date.now()}) : raw;
        });
    }
    stash(run, id, draft) {
        return this.update(this.key(run, id), raw => {
            const previous = this.parse(raw);
            const older = previous ? [previous, ...(previous.older ?? [])] : [];
            const signature = value => JSON.stringify([authoringSignature(value.authoring), value.recovery ?? null]);
            const seen = new Set([signature(draft)]);
            const kept = older.filter(value => {
                const key = signature(value);
                if (seen.has(key)) return false;
                seen.add(key); return true;
            }).map(({older, ...value}) => value);
            return JSON.stringify({...draft, older:kept, updated_at:Date.now()});
        });
    }
    async pending(value = undefined) {
        const key = `h3-branch-pending-v1:${this.client}`;
        if (value === undefined) return JSON.parse(await this.storage.getItem(key) || "null");
        return this.update(key, () => value ? JSON.stringify(value) : null);
    }
}

// Live frontend data can be reactive proxies, which structuredClone rejects.
// Copy plain data recursively instead of JSON-round-tripping it: rollback must
// preserve undefined, exact seeds, sparse arrays, aliases and circular values.
// Callbacks and opaque UI objects retain their identity; they are not authored
// data and the branch transaction does not mutate their internals.
function snapshotWidgetValue(value, seen) {
    if (value === null || typeof value !== "object") return value;
    if (seen.has(value)) return seen.get(value);
    const prototype = Object.getPrototypeOf(value);
    const array = Array.isArray(value);
    const plain = prototype === null || prototype === Object.prototype
        || (Object.getPrototypeOf(prototype) === null && prototype.constructor?.name === "Object");
    if (!array && !plain) {
        // Retain the existing copy behavior for cloneable built-ins (dates,
        // typed arrays, etc.), but do not try to duplicate DOM/UI instances.
        try {
            const copy = structuredClone(value);
            seen.set(value, copy);
            return copy;
        } catch (error) {
            if (error?.name !== "DataCloneError") throw error;
            return value;
        }
    }
    const copy = array ? new Array(value.length) : Object.create(prototype === null ? null : Object.prototype);
    seen.set(value, copy);
    for (const key of Object.keys(value)) {
        Object.defineProperty(copy, key, {value:snapshotWidgetValue(value[key], seen),
            enumerable:true, configurable:true, writable:true});
    }
    return copy;
}

// Roll back widget values without invoking the callback that just failed.
export function branchWidgetTransaction(nodes, action) {
    const unique = [...new Set(nodes.filter(Boolean))];
    const seen = new WeakMap();
    const snapshots = unique.map(node => ({node, properties:snapshotWidgetValue(node.properties ?? {}, seen),
        widgets:(node.widgets ?? []).filter(w => w.serialize !== false && w.options?.serialize !== false)
            .map(widget => ({widget, value:snapshotWidgetValue(widget.value, seen)}))}));
    try { return action(); }
    catch (error) {
        for (const {node, properties, widgets} of snapshots) {
            node.properties = properties;
            for (const {widget, value} of widgets) widget.value = value;
        }
        throw error;
    }
}

export function visibleWorkingBranches(records, selected, defaultBranch) {
    // A hidden Original remains usable by an already-open workflow. Never
    // silently switch that workflow's Plan or lose its unsaved edits.
    return records.filter(item => !item.hidden || item.id === selected || item.id === defaultBranch);
}

export function emptyBranchKeepTarget(records, selected, defaultBranch, activeBranch = null) {
    if (activeBranch != null) return activeBranch !== selected
        && records.some(item => item.id === activeBranch && !item.hidden) ? activeBranch : null;
    return records.find(item => item.id !== selected && !item.hidden && item.id === defaultBranch)?.id
        ?? records.find(item => item.id !== selected && !item.hidden)?.id ?? null;
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
    }

    async refresh(run) {
        const epoch = ++this.epoch;
        if (run !== this.run) this.switchTarget = null;
        this.run = run;
        this.ready = false;
        const selected = this.selected;
        try { this.pending ||= await this.drafts?.pending() ?? null; }
        catch (error) { this.draftStatus = `Local recovery unavailable: ${error.message}`; }
        const data = await this.request({action:"list", run_name:run});
        const record = await this.request({action:"load", run_name:run, branch_id:selected});
        if (run !== this.run || epoch !== this.epoch || selected !== this.selected || !this.isCurrent(run, selected)) return;
        this.records = data.branches;
        this.defaultBranch = data.default_branch;
        const known = this.binding?.run_name === run && this.binding.branch_id === selected
            && this.binding.revision === record.revision;
        const same = authoringSignature(record.authoring) === authoringSignature(this.capture());
        this.conflict = known || same || !record.authoring ? ""
            : "This workflow differs from the saved branch. Update active branch to keep the displayed edits, reload saved branch, or create an empty branch.";
        if (!this.conflict) this.adopt(record);
        await this.readDraft();
        if (run !== this.run || epoch !== this.epoch || selected !== this.selected || !this.isCurrent(run, selected)) return;
        this.ready = true;
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

    async readDraft({includeResolved = false} = {}) {
        const run = this.run, selected = this.selected, epoch = this.epoch;
        const token = this.draftReadToken = (this.draftReadToken ?? 0) + 1;
        this.draftReading = true;
        try {
            const saved = await this.drafts?.read(run, selected);
            if (token !== this.draftReadToken || run !== this.run || selected !== this.selected || epoch !== this.epoch || !this.isCurrent(run, selected)) return;
            this.draftRecovery = null;
            this.draftStatus = "";
            const revision = this.records.find(record => record.id === this.selected)?.revision;
            const candidates = [saved, ...(saved?.older ?? [])];
            const resolved = value => !includeResolved && value?.resolved_revision
                && value.resolved_revision === revision;
            const signature = authoringSignature(this.capture());
            const draft = candidates.find(value => value && !resolved(value) &&
                (value.recovery || authoringSignature(value.authoring) !== signature));
            if (draft) {
                this.draftRecovery = draft;
                this.draftStatus = "A local recovery draft is available; restore it before editing, or reload the saved branch.";
            } else if (candidates.some(resolved)) {
                this.draftStatus = "Previous local edits remain in browser recovery.";
            }
            if (this.drafts?.storage.warning) this.draftStatus = this.drafts.storage.warning;
        } catch (error) {
            if (token === this.draftReadToken && run === this.run && selected === this.selected && epoch === this.epoch) this.draftStatus = `Local recovery unavailable: ${error.message}`;
        } finally {
            if (token === this.draftReadToken) this.draftReading = false;
        }
    }

    async preserveDraft() {
        if (!this.drafts || !this.run || this.draftRecovery) return;
        const binding = this.binding?.run_name === this.run && this.binding.branch_id === this.selected
            ? this.binding : null;
        await this.drafts.save(this.run, this.selected, {authoring:this.capture(), revision:binding?.revision ?? null,
            recovery:this.captureRecovery()});
        this.draftStatus = "Recovery draft saved in this browser.";
    }

    async preserveNavigationDraft() {
        if (!this.drafts && !this.captureRecovery() && authoringSignature(this.capture()) === this.savedSignature) return;
        if (!this.drafts) throw new Error("Browser recovery is unavailable. Save or export your local edits before switching without saving.");
        const binding = this.binding?.run_name === this.run && this.binding.branch_id === this.selected ? this.binding : null;
        // Keep an older recovery draft too: navigation must never replace it
        // with the saved settings currently on screen.
        await this.drafts.stash(this.run, this.selected, {authoring:this.capture(),
            revision:binding?.revision ?? null, recovery:this.captureRecovery()});
        this.draftStatus = "Local prompts, settings and pending edits saved in browser recovery.";
    }

    observe() {
        if (this.observing) return this.observing;
        this.observing = this.observeDraft().finally(() => { this.observing = null; });
        return this.observing;
    }

    async observeDraft() {
        if (this.busy || !this.ready || this.draftRecovery || this.draftReading) return;
        try {
            const signature = authoringSignature(this.capture());
            const recovery = this.captureRecovery();
            const scope = JSON.stringify([this.run, this.selected]);
            const observed = JSON.stringify([scope, signature, recovery]);
            if (observed === this.observedSignature) return;
            // Do not retry the same failed write every 500ms. A new edit or an
            // explicit branch action may retry; failures stay visible inline.
            this.observedSignature = observed;
            // Gaps/trims are editorial-only: their Plan signature is unchanged.
            // Also retire the pending recovery once its server save completes.
            const hadRecovery = this.observedRecoveryScope === scope;
            this.observedRecoveryScope = recovery ? scope : null;
            if (recovery || hadRecovery || signature !== this.savedSignature) await this.preserveDraft();
        } catch (error) { this.draftStatus = `Draft not saved: ${error.message}`; }
    }

    async mutation(body) {
        if (this.pending) throw new Error("An earlier save may have succeeded. Retry pending operation before making another change.");
        this.pending = {...structuredClone(body), operation_id:branchOperationId()};
        return this.sendPending();
    }

    async sendPending() {
        const body = this.pending;
        try { await this.drafts?.pending(body); }
        catch (error) { this.draftStatus = `Pending request is only in memory: ${error.message}`; }
        for (let attempt = 0; attempt < 2; attempt++) {
            try {
                const result = await this.request(structuredClone(body));
                this.pending = null;
                try { await this.drafts?.pending(null); } catch { /* Replaying the same ID is safe. */ }
                return result;
            } catch (error) {
                if (error.status >= 400 && error.status < 500) {
                    this.pending = null;
                    try { await this.drafts?.pending(null); } catch { /* Preserve the original error. */ }
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
            if (body.action === "save" && body.branch_id === this.selected) {
                this.adopt(record);
                await this.resolveDrafts(record.revision);
            }
            else if (body.action === "create" && !this.records.some(item => item.id === record.id)) this.records.push(record);
            this.error = "";
        } catch (error) { this.error = error.message; }
        finally { this.busy = false; this.changed(); }
    }

    async save(assertCurrent = () => {}) {
        if (!this.ready || this.draftReading) throw new Error("Wait for working branches and recovery to load.");
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

    async resolveDrafts(revision) {
        // A choice resolves the warning, not the backup. Restore local draft
        // can still retrieve these versions explicitly after a workflow reload.
        this.draftRecovery = null;
        this.draftStatus = "Previous local edits remain in browser recovery.";
        try {
            const run = this.run, selected = this.selected;
            const draft = await this.drafts?.read(run, selected);
            if (draft) {
                const signature = value => JSON.stringify([authoringSignature(value.authoring), value.recovery ?? null]);
                const acknowledged = new Set([draft, ...(draft.older ?? [])].map(signature));
                const resolve = value => acknowledged.has(signature(value)) ? {...value, resolved_revision:revision} : value;
                await this.drafts.revise(run, selected, current => ({
                    ...resolve(current), older:(current.older ?? []).map(resolve),
                }));
            }
        } catch (error) {
            this.draftStatus += ` Recovery acknowledgement could not be saved: ${error.message}`;
        }
    }

    async updateActive(confirmUpdate = () => false) {
        if (this.busy) return;
        const run = this.run, selected = this.selected, epoch = this.epoch;
        const assertCurrent = () => {
            if (this.run !== run || this.selected !== selected || this.epoch !== epoch || !this.isCurrent(run, selected)) {
                throw new Error("Project or branch changed during the update; try again on the intended branch.");
            }
        };
        this.busy = true; this.error = ""; this.changed();
        try {
            assertCurrent();
            if (!this.ready || this.draftReading) throw new Error("Wait for working branches and recovery to load.");
            if (this.pending) throw new Error("Retry pending operation before continuing.");
            const authoring = structuredClone(this.capture());
            const signature = authoringSignature(authoring);
            const record = await this.request({action:"load", run_name:run, branch_id:selected});
            assertCurrent();
            if (!await confirmUpdate({
                name:record.name || (selected === "main" ? "Original" : selected),
                displayedScenes:parsePlanJson(authoring.plan_json).shots.length,
                savedScenes:record.authoring ? parsePlanJson(record.authoring.plan_json).shots.length : 0,
                hasRecovery:Boolean(this.draftRecovery),
            })) return;
            assertCurrent();
            if (signature !== authoringSignature(this.capture())) {
                throw new Error("Prompts or settings changed while confirming. Nothing was saved; update again to include those edits.");
            }
            try {
                if (record.authoring) await this.drafts?.stash(run, selected, {
                    authoring:record.authoring, revision:record.revision, recovery:null,
                });
                await this.drafts?.stash(run, selected, {authoring, revision:this.binding?.revision ?? null,
                    recovery:this.captureRecovery()});
            } catch (error) {
                // A full browser must not prevent an explicitly confirmed save.
                this.draftStatus = `Local recovery unavailable: ${error.message}`;
            }
            assertCurrent();
            // Save only authoring, using the revision the user just confirmed.
            // No reload, checkpoint reassignment, fork, cut/history flush or
            // forced revision bypass. Pending local edits remain in the editor.
            const saved = await this.mutation({action:"save", run_name:run,
                branch_id:selected, revision:record.revision, authoring});
            assertCurrent();
            this.adopt(saved);
            await this.resolveDrafts(saved.revision);
            assertCurrent();
            this.observedSignature = signature;
            if (signature !== authoringSignature(this.capture())) await this.preserveDraft();
        } catch (error) {
            this.error = error?.message || String(error);
        } finally {
            this.busy = false; this.changed();
        }
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
            if (!this.ready || this.draftReading) throw new Error("Wait for working branches and recovery to load.");
            if (this.pending) throw new Error("Retry pending operation before continuing.");
            if (save && this.conflict) throw new Error(this.conflict);
            const editStamp = this.editStamp();
            if (requireDraft && !this.drafts && authoringSignature(this.capture()) !== this.savedSignature) {
                throw new Error("Browser recovery is unavailable. Save these edits as a new empty branch before reloading.");
            }
            try {
                if (navigation) await this.preserveNavigationDraft();
                else await this.preserveDraft();
            }
            catch (error) {
                this.draftStatus = `Local draft not saved: ${error.message}`;
                if (requireDraft) throw error;
                // Saving to the server is still possible when browser storage
                // is full. Never make local recovery a barrier to a real save.
            }
            assertCurrent();
            if (flush) await this.flush();
            else await this.settle();
            assertCurrent();
            const signature = save ? await this.save(assertCurrent) : authoringSignature(this.capture());
            assertCurrent();
            const assertUnedited = async () => {
                assertCurrent();
                if (signature !== authoringSignature(this.capture()) || (navigation && editStamp !== this.editStamp())) {
                    if (navigation) await this.preserveNavigationDraft();
                    else await this.preserveDraft();
                    throw new Error("Edits arrived during the switch. They were kept; switch again when editing is finished.");
                }
            };
            await assertUnedited();
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
            await assertCurrent();
            if (!record.authoring) throw new Error("This branch has no saved authoring snapshot yet.");
            await this.apply(record);
            this.selected = id;
            this.switchTarget = null;
            this.adopt(record);
            this.observedSignature = null;
            await this.readDraft();
        }, {save, flush:save, navigation:!save});
    }

    async reloadSaved() {
        return this.perform(async assertCurrent => {
            const record = await this.request({action:"load", run_name:this.run, branch_id:this.selected});
            await assertCurrent();
            if (!record.authoring) throw new Error("This branch has no saved authoring snapshot yet.");
            await this.apply(record);
            this.adopt(record);
            await this.resolveDrafts(record.revision);
            this.observedSignature = authoringSignature(record.authoring);
            this.draftStatus = `Saved branch loaded. ${this.draftStatus}`;
        }, {save:false, flush:false, navigation:true});
    }

    async restoreDraft() {
        if (!this.draftRecovery) return;
        const draft = this.draftRecovery;
        return this.perform(async assertCurrent => {
            await assertCurrent();
            await this.apply({id:this.selected, authoring:draft.authoring});
            await this.restoreRecovery(draft.recovery);
            if (draft.recovery && this.drafts) {
                const run = this.run, selected = this.selected;
                const consumed = value => value && authoringSignature(value.authoring) === authoringSignature(draft.authoring)
                    && JSON.stringify(value.recovery) === JSON.stringify(draft.recovery)
                    ? {...value, recovery:null} : value;
                await this.drafts.revise(run, selected, current => ({...consumed(current), older:(current.older ?? []).map(consumed)}));
            }
            const record = this.records.find(item => item.id === this.selected);
            this.binding = {run_name:this.run, branch_id:this.selected, revision:draft.revision};
            this.rememberBinding(structuredClone(this.binding));
            this.conflict = draft.revision === record?.revision ? ""
                : "Recovered draft differs from the saved branch. Update active branch to keep it, create an empty branch, or reload saved branch.";
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
            await assertCurrent();
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
            await assertCurrent();
            this.defaultBranch = result.default_branch;
        });
    }
}
