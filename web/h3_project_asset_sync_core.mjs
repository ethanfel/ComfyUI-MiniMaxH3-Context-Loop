export const PROJECT_ASSET_CATALOG_CHANGED_EVENT =
    "minimax-h3-project-assets-changed";

export function projectAssetEditAttempts(cryptoSource = globalThis.crypto) {
    // Transport retry identities only; never ownership credentials or paths.
    // Failed exact requests retain their ID while this Carousel is mounted.
    const pending = new Map();
    const fileDigests = new WeakMap();
    const routes = new Set(["update", "duplicate", "folder", "reorder", "import", "derive", "capture-frame", "repair-inputs", "delete"]);
    function ordered(value) {
        if (Array.isArray(value)) return value.map(ordered);
        if (!value || typeof value !== "object") return value;
        return Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])]));
    }
    function attempt(route, body, options, serialize) {
        const key = JSON.stringify([route, ordered(body)]);
        let identity = pending.get(key) ?? body.storage_operation_id;
        if (!identity) {
            const bytes = new Uint8Array(16);
            if (typeof cryptoSource?.getRandomValues === "function") cryptoSource.getRandomValues(bytes);
            else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
            identity = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
        }
        if (!/^[a-f0-9]{32}$/.test(identity)) throw new Error("Invalid asset edit retry identity.");
        pending.set(key, identity);
        return {
            options: {...options, body: serialize(identity)},
            accept() { if (pending.get(key) === identity) pending.delete(key); },
        };
    }
    async function uploadAttempt(route, options) {
        const body = Object.create(null);
        let file;
        for (const [key, value] of options.body.entries()) {
            if (Object.hasOwn(body, key)) throw new Error("Duplicate upload field.");
            if (key === "file") {
                if (typeof value === "string") throw new Error("Upload requires a file.");
                file = value;
            } else if (typeof value !== "string") throw new Error("Unexpected upload file field.");
            body[key] = value;
        }
        if (!file) throw new Error("Upload requires a file.");
        if (!fileDigests.has(file)) {
            const promise = (async () => {
                // Fixed 1 MiB chunks keep large videos bounded in memory and
                // work on LAN HTTP without WebCrypto. This chained fingerprint
                // is a browser retry key, NOT the server's whole-file SHA-256.
                let digest = new Uint8Array(32);
                for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
                    const bytes = new Uint8Array(await file.slice(offset, offset + 1024 * 1024).arrayBuffer());
                    const combined = new Uint8Array(64);
                    combined.set(digest);
                    combined.set(await sha256Bytes(bytes, cryptoSource), 32);
                    digest = await sha256Bytes(combined, cryptoSource);
                }
                return Array.from(digest, b => b.toString(16).padStart(2, "0")).join("");
            })();
            fileDigests.set(file, promise);
            promise.catch(() => fileDigests.delete(file));
        }
        body.file = {name:file.name, size:file.size, type:file.type,
            fingerprint:await fileDigests.get(file)};
        return attempt(route, body, options, identity => {
            const data = new FormData();
            // Server streams once, so every control field must precede file.
            for (const [key,value] of options.body.entries()) {
                if (key !== "file" && key !== "storage_operation_id") data.append(key,value);
            }
            data.append("storage_operation_id", identity);
            data.append("file", file, file.name);
            return data;
        });
    }
    return {
        prepare(route, options) {
            if (route === "/minimax_h3_context_loop/project-assets/upload" && options.method === "POST"
                    && typeof FormData !== "undefined" && options.body instanceof FormData) {
                return uploadAttempt(route, options);
            }
            if (!route.startsWith("/minimax_h3_context_loop/project-assets/")
                    || !routes.has(route.split("/").at(-1))
                    || options.method !== "POST" || typeof options.body !== "string") {
                return {options, accept() {}};
            }
            const body = JSON.parse(options.body);
            return attempt(route, body, options, identity => JSON.stringify({...body,storage_operation_id:identity}));
        },
    };
}

export function serializedProjectAssetCatalog(value, requestedProject = "") {
    let catalog = value;
    if (typeof catalog === "string") {
        try { catalog = JSON.parse(catalog); }
        catch (_error) { return null; }
    }
    if (!catalog || typeof catalog !== "object" || Array.isArray(catalog)) {
        return null;
    }
    const project = String(catalog.project ?? "").trim();
    const requested = String(requestedProject ?? "").trim();
    if (requested && project && requested !== project) return null;
    if (!Array.isArray(catalog.assets)
            || !Array.isArray(catalog.reference_slots ?? [])) return null;
    return {
        ...catalog,
        project:project || requested,
        assets:[...catalog.assets],
        reference_slots:[...(catalog.reference_slots ?? [])],
        folders:Array.isArray(catalog.folders) ? [...catalog.folders] : [],
    };
}

export function serializedProjectAssetIdentity(runNameValue, catalogValue) {
    const configured = String(runNameValue ?? "").trim();
    if (configured && configured !== "h3_project") return configured;
    const catalog = serializedProjectAssetCatalog(catalogValue);
    const catalogProject = String(catalog?.project ?? "").trim();
    return catalogProject === "h3_project" ? "" : catalogProject;
}

export function publishProjectAssetCatalogChanged(manager, catalog) {
    const project = String(catalog?.project ?? "").trim();
    const revision = String(catalog?.revision ?? "").trim();
    const signature = `${project}\u0000${revision}`;
    if (manager?._h3ProjectAssetPublishedSignature === signature) return false;
    if (manager) manager._h3ProjectAssetPublishedSignature = signature;
    if (typeof globalThis.dispatchEvent !== "function"
            || typeof globalThis.CustomEvent !== "function") return false;
    globalThis.dispatchEvent(new CustomEvent(
        PROJECT_ASSET_CATALOG_CHANGED_EVENT,
        {detail: {manager, project, revision}},
    ));
    return true;
}
import {sha256Bytes} from "./h3_chain_plan_core.mjs?v=0.7.8";
