// Frame capture must never guess a project from an unrelated node on canvas.
const CAROUSEL = "MiniMaxH3ProjectAssetManager";
const PLANS = new Set(["MiniMaxH3ChainPlan", "MiniMaxH3ChainPlanModern"]);
const type = (node) => node?.comfyClass ?? node?.type ?? node?.constructor?.type;

export function captureCarousels(graph, seen = new Set()) {
    if (!graph || seen.has(graph)) return [];
    seen.add(graph);
    return (graph._nodes ?? []).flatMap((node) => [
        ...(type(node) === CAROUSEL ? [node] : []),
        ...captureCarousels(node.subgraph, seen),
    ]);
}

export function carouselProject(node) {
    return String(node?._h3ProjectAssetCurrentProject?.()
        ?? node?.widgets?.find((widget) => widget.name === "run_name")?.value ?? "").trim();
}

export function captureTargetProject(start) {
    const queue = [start], seen = new Set(), carousels = [], plans = [];
    while (queue.length) {
        const node = queue.shift();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (type(node) === CAROUSEL) carousels.push(node);
        if (PLANS.has(type(node))) plans.push(node);
        for (const input of node.inputs ?? []) {
            const link = node.graph?.links?.[input.link];
            if (link) queue.push(node.graph?.getNodeById?.(link.origin_id));
        }
    }
    // Multiple upstream projects (or an unresolved Carousel) require an
    // explicit destination. A disconnected/other-tab Carousel is never used.
    const projects = carousels.length
        ? carousels.map(carouselProject)
        : plans.map((node) => String(node.widgets?.find(
            (widget) => widget.name === "run_name")?.value ?? "").trim());
    return projects.length && projects.every((project) => project === projects[0])
        ? projects[0] : "";
}

export function canCaptureFrame(video) {
    return Boolean(video.h3CaptureItem?.filename && video.readyState >= 2
        && !video.seeking && !video.error && video.videoWidth > 0 && video.videoHeight > 0
        && Number.isFinite(video.currentTime) && video.currentTime >= 0);
}
