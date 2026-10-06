import {app} from "/scripts/app.js";
import {api} from "/scripts/api.js";

const ROUTE = "/minimax_h3_context_loop/project-ownership";
const SETTINGS_ROUTE = `${ROUTE}/settings`;
const HEARTBEAT_MS = 25000;
const controllers = new Set();
const subscribers = new Set();
const graphIds = new WeakMap();
let graphSerial = 0;
let policy = {enabled:true, epoch:0};
const policySubscribers = new Set();

export function subscribeOwnershipSettings(callback) {
    policySubscribers.add(callback);
    return () => policySubscribers.delete(callback);
}

function applyOwnershipSettings(payload, source = null) {
    if (typeof payload?.enabled !== "boolean" || !Number.isInteger(payload.epoch)
            || payload.epoch < policy.epoch) return;
    const changed = payload.epoch !== policy.epoch || payload.enabled !== policy.enabled;
    policy = {enabled:payload.enabled, epoch:payload.epoch};
    for (const callback of policySubscribers) callback(policy);
    if (!changed) return;
    for (const controller of controllers) {
        if (controller === source || controller.disposed) continue;
        controller.requestSerial++;
        controller.status = null;
        controller.owned = false;
        controller.epoch = null;
        controller.schedule();
        if (!controller.runName) continue;
        if (!policy.enabled) {
            controller.accept({run_name:controller.runName, locking_enabled:false,
                policy_epoch:policy.epoch, owned_by_requester:false, available:true, epoch:0});
        } else {
            controller.notify(null);
            // A new policy epoch requires a fresh claim, never an old proof.
            controller.request("claim").catch(() => {});
        }
    }
}

async function settingsRequest(enabled) {
    const response = await api.fetchApi(SETTINGS_ROUTE, enabled === undefined
        ? {cache:"no-store"}
        : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({enabled})});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Ownership settings: HTTP ${response.status}`);
    if (typeof payload.enabled !== "boolean" || !Number.isInteger(payload.epoch) || payload.epoch < 0) {
        throw new Error("The server did not return valid workflow ownership settings. Update/restart ComfyUI.");
    }
    applyOwnershipSettings(payload);
    return policy;
}

export function loadOwnershipSettings() { return settingsRequest(); }
export function setOwnershipEnabled(enabled) {
    if (typeof enabled !== "boolean") throw new Error("Workflow ownership enabled must be a boolean.");
    return settingsRequest(enabled);
}

function rootGraph(node) {
    return node?.graph?.rootGraph ?? node?.graph ?? app.graph ?? null;
}

function activeWorkflowDescriptor() {
    const workflow = app.extensionManager?.workflow?.activeWorkflow;
    const key = workflow?.activeState?.id
        ?? workflow?.path
        ?? workflow?.filename
        ?? "legacy-workflow";
    const rawLabel = workflow?.filename
        ?? workflow?.path
        ?? workflow?.activeState?.id
        ?? "ComfyUI workflow";
    const label = String(rawLabel).split(/[\\/]/).at(-1) || "ComfyUI workflow";
    return {key:String(key), label};
}

function ephemeralGraphKey(node) {
    const graph = rootGraph(node);
    if (!graph) return "detached";
    if (!graphIds.has(graph)) graphIds.set(graph, `graph-${++graphSerial}`);
    return graphIds.get(graph);
}

function sessionOwnerId(node) {
    const workflow = activeWorkflowDescriptor();
    const key = `h3-project-owner-v1:${workflow.key}`;
    let value = "";
    try { value = globalThis.sessionStorage?.getItem(key) ?? ""; } catch (_error) { /* unavailable */ }
    if (!/^[A-Za-z0-9._:-]{16,192}$/.test(value)) {
        const random = globalThis.crypto?.randomUUID?.()
            ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
        value = `h3:${random}:${ephemeralGraphKey(node)}`.replace(/[^A-Za-z0-9._:-]/g, "-");
        try { globalThis.sessionStorage?.setItem(key, value); } catch (_error) { /* unavailable */ }
    }
    return {ownerId:value, ownerLabel:workflow.label.slice(0, 120)};
}

async function command(body) {
    const response = await api.fetchApi(ROUTE, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const error = new Error(payload.error || `HTTP ${response.status}`);
        error.status = response.status;
        error.code = payload.code;
        error.payload = payload;
        throw error;
    }
    return payload;
}

function ownershipHeaders(controller) {
    if (!controller?.owned || !Number.isInteger(Number(controller.epoch))) return {};
    return {
        "X-H3-Workflow-Owner": controller.ownerId,
        "X-H3-Ownership-Epoch": String(controller.epoch),
    };
}

function controllerFor(node, runName) {
    const graph = rootGraph(node);
    const wanted = String(runName || "").trim();
    for (const controller of controllers) {
        if (!controller.disposed && controller.graph === graph
                && controller.runName === wanted) return controller;
    }
    return null;
}

export function subscribeProjectOwnership(node, onChange) {
    const subscriber = {node, onChange};
    subscribers.add(subscriber);
    return () => subscribers.delete(subscriber);
}

export function isProjectReadOnlyError(error, runName) {
    const message = String(error?.message ?? error ?? "");
    return message.startsWith(`Project ${runName} is read-only here;`)
        || message.startsWith(`Project ${runName} is protected by workflow ownership.`);
}

function notifyOwnershipSubscribers(controller, payload) {
    for (const subscriber of subscribers) {
        if (rootGraph(subscriber.node) !== controller.graph) continue;
        try { subscriber.onChange(payload); }
        catch (error) { console.warn("H3 ownership UI refresh failed:", error); }
    }
}

export function registerProjectOwnership(node, onChange = null) {
    const identity = sessionOwnerId(node);
    const controller = {
        node,
        graph:rootGraph(node),
        ownerId:identity.ownerId,
        ownerLabel:identity.ownerLabel,
        runName:"",
        status:null,
        owned:false,
        epoch:null,
        timer:null,
        disposed:false,
        requestSerial:0,
        notify(payload) {
            onChange?.(payload);
            notifyOwnershipSubscribers(this, payload);
        },
        accept(payload) {
            this.status = payload;
            this.owned = payload.owned_by_requester === true && payload.locking_enabled !== false;
            this.epoch = Number.isInteger(Number(payload.epoch)) ? Number(payload.epoch) : null;
            this.schedule();
            this.notify(payload);
        },
        async request(action = "status") {
            if (this.disposed || !this.runName) return null;
            const requestedRun = this.runName;
            const serial = ++this.requestSerial;
            const payload = await command({
                action, run_name:requestedRun, owner_id:this.ownerId,
                owner_label:this.ownerLabel, epoch:this.epoch,
            });
            if (this.disposed || this.runName !== requestedRun || serial !== this.requestSerial
                    || (Number.isInteger(payload.policy_epoch) && payload.policy_epoch < policy.epoch)) return payload;
            if (typeof payload.locking_enabled === "boolean" && Number.isInteger(payload.policy_epoch)) {
                applyOwnershipSettings({enabled:payload.locking_enabled, epoch:payload.policy_epoch}, this);
            }
            this.accept(payload);
            return payload;
        },
        async select(runName) {
            const next = String(runName || "").trim();
            if (next === this.runName && this.status) return this.status;
            this.runName = next;
            this.requestSerial++;
            this.status = null;
            this.owned = false;
            this.epoch = null;
            this.schedule();
            if (!next) {
                onChange?.(null);
                return null;
            }
            return await this.request("claim");
        },
        async force() { return await this.request("force"); },
        async release() { return await this.request("release"); },
        schedule() {
            if (this.timer) clearTimeout(this.timer);
            this.timer = null;
            if (!this.disposed && this.owned && this.runName) {
                this.timer = setTimeout(() => {
                    this.request("heartbeat").catch(() => {
                        // A transient connection failure does not surrender
                        // the persistent server lock. Keep the proof and retry;
                        // an actual takeover is learned from the next response
                        // or ownership websocket event.
                        this.schedule();
                    });
                }, HEARTBEAT_MS);
            }
        },
        proof() {
            return this.owned ? {
                owner_id:this.ownerId, epoch:this.epoch,
            } : null;
        },
        dispose() {
            this.disposed = true;
            if (this.timer) clearTimeout(this.timer);
            this.timer = null;
            controllers.delete(this);
        },
    };
    controllers.add(controller);
    return controller;
}

function ownershipDenied(message) {
    // Match a server-side refusal, but also record that the guarded mutation
    // was never sent. Save/recovery code must not treat this as a lost reply.
    return Object.assign(new Error(message), {
        status:423, code:"h3_project_read_only", requestNotSent:true,
    });
}

async function claimForWrite(controller) {
    try {
        if (!controller.owned) await controller.request("claim");
    } catch (error) {
        // Even if the ownership request itself failed in transit, the caller
        // has not yet submitted its project mutation.
        error.requestNotSent = true;
        throw error;
    }
}

export async function projectMutationOptions(node, runName, options = {}) {
    const controller = controllerFor(node, runName);
    if (!controller) return options;
    if (controller.status?.locking_enabled === false) return options;
    await claimForWrite(controller);
    if (controller.status?.locking_enabled === false) return options;
    if (!controller.owned) {
        const owner = controller.status?.owner_label || "another workflow";
        throw ownershipDenied(
            `Project ${runName} is read-only here; it is owned by ${owner}. ` +
            "Use Force ownership in this workflow's Project Asset Carousel.",
        );
    }
    return {
        ...options,
        headers:{...(options.headers ?? {}), ...ownershipHeaders(controller)},
    };
}

export async function queuedProjectOwnership(node, runName) {
    const controller = controllerFor(node, runName);
    if (!controller) return "";
    if (controller.status?.locking_enabled === false) return "";
    await claimForWrite(controller);
    if (controller.status?.locking_enabled === false) return "";
    if (!controller.owned) {
        const owner = controller.status?.owner_label || "another workflow";
        throw ownershipDenied(
            `Project ${runName} is read-only here; it is owned by ${owner}. ` +
            "Use Force ownership in the Project Asset Carousel.",
        );
    }
    return JSON.stringify(controller.proof());
}

api.addEventListener?.("minimax_h3_project_ownership", (event) => {
    const runName = String(event?.detail?.run_name ?? "");
    for (const controller of controllers) {
        if (!controller.disposed && controller.runName === runName) {
            controller.request("status").catch(() => {});
        }
    }
});

api.addEventListener?.("minimax_h3_project_ownership_settings", (event) => {
    applyOwnershipSettings(event?.detail);
});
