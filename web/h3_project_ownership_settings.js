import {app} from "/scripts/app.js";
import {
    loadOwnershipSettings, setOwnershipEnabled, subscribeOwnershipSettings,
} from "./h3_project_ownership.mjs?v=0.7.6";

const ID = "MiniMaxH3ContexLoop.ProjectOwnership.Enabled";
let ready = false;
let confirmed = true;
let reflection = false;
let changes = Promise.resolve();

async function reflect(policy) {
    confirmed = policy.enabled;
    const settings = app.ui?.settings;
    if (settings?.getSettingValue?.(ID) === confirmed) return;
    reflection = true;
    let saved;
    // ComfyUI applies the value and invokes onChange synchronously; do not
    // suppress a user's next toggle while its preference storage is pending.
    try { saved = settings?.setSettingValue?.(ID, confirmed); }
    finally { reflection = false; }
    await saved;
}

function failure(error) {
    console.error("H3 workflow ownership settings:", error);
    app.extensionManager?.toast?.add?.({severity:"error", summary:"Workflow ownership setting",
        detail:error?.message || String(error), life:8000});
}

app.registerExtension({
    name:"minimax_h3_context_loop.project_ownership_settings",
    init() {
        app.ui?.settings?.addSetting?.({
            id:ID,
            name:"Workflow ownership locking (server-wide)",
            category:["MiniMax H3 Context Loop", "Project safety", "Workflow ownership"],
            type:"boolean",
            defaultValue:true,
            tooltip:"On by default: one workflow owns a Run's writes. Off: any workflow can edit the same Run; conflicting edits may overwrite each other. Applies to all tabs/projects on this server's output directory, not just this workflow. File transaction/deletion safeguards remain active. Change while idle: re-enabling needs fresh ownership claims and old queued work may need to be requeued.",
            onChange(value) {
                if (!ready || reflection || typeof value !== "boolean") return;
                // Serialize rapid toggles. A remembered browser/user value is
                // never allowed to overwrite the server policy during startup.
                changes = changes.then(async () => {
                    if (value === confirmed) return;
                    try { await reflect(await setOwnershipEnabled(value)); }
                    catch (error) { await reflect({enabled:confirmed}); failure(error); }
                }).catch(failure);
            },
        });
        subscribeOwnershipSettings(policy => { void reflect(policy).catch(failure); });
    },
    async setup() {
        try { await reflect(await loadOwnershipSettings()); ready = true; }
        catch (error) { failure(error); }
    },
});
