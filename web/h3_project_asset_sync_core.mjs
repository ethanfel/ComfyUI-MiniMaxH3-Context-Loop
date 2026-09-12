export const PROJECT_ASSET_CATALOG_CHANGED_EVENT =
    "minimax-h3-project-assets-changed";

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
