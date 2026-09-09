// Presentation only. Edges come from recorded paths, never per-scene recency.
export function checkpointGraphKey(stage, entry, profile = "") {
    return stage === "original" ? `${Number(entry.scene)}:${String(entry.revision).toLowerCase()}`
        : JSON.stringify([profile, Number(entry.scene), entry.metadata_path ?? entry.key,
            entry.revision, entry.checkpoint_sha256]);
}

export const checkpointGraphEdgeKey = (from, to) => JSON.stringify([from, to]);

// Saved timestamps describe save order, not lineage or necessarily generation:
// assigning an existing clip creates a new saved revision without a new render.
export function checkpointSaveOrder(records, stage = "original") {
    const scenes = new Map(), result = new Map();
    for (const record of records) {
        const scene = Number(record.scene);
        if (!scenes.has(scene)) scenes.set(scene, new Map());
        const key = checkpointGraphKey(stage, record, record.profile_path);
        scenes.get(scene).set(key, record);
    }
    for (const recordsByKey of scenes.values()) {
        const dated = [...recordsByKey].map(([key, record]) => ({key,
            time:record.created_at ? Date.parse(record.created_at) : NaN}));
        const known = dated.filter(item => Number.isFinite(item.time)).sort((a, b) => a.time - b.time);
        const complete = known.length === dated.length;
        const ranks = new Map();
        known.forEach((item, index) => {
            if (!ranks.has(item.time)) ranks.set(item.time, {first:index + 1, last:index + 1});
            ranks.get(item.time).last = index + 1;
        });
        for (const item of dated) {
            if (!Number.isFinite(item.time)) {
                result.set(item.key, {label:"Save order unknown", latest:false});
                continue;
            }
            const {first, last} = ranks.get(item.time);
            const latest = last === known.length;
            result.set(item.key, {
                label:`${complete ? "Save" : "Dated save"} #${first === last ? first : `${first}–${last}`} of ${known.length}`
                    + (first !== last ? " · same time" : "")
                    + (latest ? complete ? " · Latest" : " · Latest dated" : ""),
                latest,
            });
        }
    }
    return result;
}

export function checkpointForkGraph(rows, stage = "original") {
    const nodes = new Map(), edges = new Map(), paths = [];
    let nextLane = 0;
    for (const row of rows) {
        const entries = stage === "original" ? row.revisions : row.entries;
        if (!entries?.length) continue;
        const keys = entries.map(entry => checkpointGraphKey(stage, entry, row.profile_path));
        const path = {row, keys, index:paths.length};
        paths.push(path);
        const lane = nextLane;
        let added = false;
        entries.forEach((entry, index) => {
            const key = keys[index];
            if (!nodes.has(key)) {
                nodes.set(key, {key, scene:Number(entry.scene), entry, lane, paths:[], ends:[]});
                added = true;
            }
            const node = nodes.get(key);
            node.paths.push(path.index);
            if (index === entries.length - 1) node.ends.push(path);
            // Do not draw a cycle or imply backward chronology for malformed history.
            if (index && Number(entries[index - 1].scene) < node.scene) {
                const from = keys[index - 1];
                const edgeKey = checkpointGraphEdgeKey(from, key);
                if (!edges.has(edgeKey)) edges.set(edgeKey, {key:edgeKey, from, to:key, paths:[]});
                edges.get(edgeKey).paths.push(path.index);
            }
        });
        if (added) nextLane++;
    }
    // Reuse is a proposed next-scene attachment, never a saved lineage edge.
    // A shared tip can already have a continuation in its lane: allocate a free
    // lane in that case instead of placing two cards in the same grid cell.
    const slots = new Map();
    const occupied = new Set([...nodes.values()].map(item => `${item.scene}:${item.lane}`));
    if (stage === "original") for (const path of paths) {
        const slot = path.row.attribution_slot, parent = nodes.get(path.keys.at(-1));
        const scene = Number(slot?.scene);
        if (!slot || !(slot.candidates?.length || slot.blocked_candidates?.length)
                || !Number.isInteger(scene) || scene !== parent.scene + 1
                || (slot.parent_scene != null && Number(slot.parent_scene) !== parent.scene)
                || (slot.parent_revision && String(slot.parent_revision).toLowerCase() !== String(parent.entry.revision).toLowerCase())) continue;
        const key = JSON.stringify(["reuse", parent.key, scene]);
        if (slots.has(key)) continue;
        const lane = occupied.has(`${scene}:${parent.lane}`) ? nextLane++ : parent.lane;
        occupied.add(`${scene}:${lane}`);
        slots.set(key, {key, scene, lane, parent:parent.entry, slot});
        const edgeKey = checkpointGraphEdgeKey(parent.key, key);
        edges.set(edgeKey, {key:edgeKey, from:parent.key, to:key, kind:"reuse", paths:[]});
    }
    const scenes = [...new Set([...nodes.values(), ...slots.values()].map(item => item.scene))].sort((a, b) => a - b);
    const columns = new Map(scenes.map((scene, index) => [scene, index]));
    return {nodes:[...nodes.values()].map(item => ({...item, column:columns.get(item.scene)})),
        slots:[...slots.values()].map(item => ({...item, column:columns.get(item.scene)})),
        edges:[...edges.values()], paths, columns:scenes.length, lanes:nextLane};
}

// Highlight the execution widget, not the preview cursor or newest saved take.
export function checkpointGraphOutput(value, run, branch = "main", stage = "original") {
    const empty = () => ({nodes:new Set(), edges:new Set(), tip:null});
    try {
        const selection = typeof value === "string" ? JSON.parse(value) : value;
        if (!selection || selection.run_name !== run || (selection._branch_id ?? "main") !== branch) return empty();
        let lineage = selection.lineage;
        let profile = "";
        if (stage !== "original") {
            if (stage !== "derope" || selection.processing_source?.stage !== stage) return empty();
            lineage = selection.processing_source.branch?.lineage;
            profile = selection.processing_source.profile_path;
        }
        if (!Array.isArray(lineage) || !lineage.length || lineage.some((entry, index) =>
            !Number.isInteger(entry?.scene) || entry.scene < 1 || !/^[0-9a-f]{32}$/i.test(entry.revision)
            || (index && entry.scene <= lineage[index - 1].scene))) return empty();
        const start = selection.output_scope === "chapter" ? Number(selection.scope_start_scene) : 1;
        const end = Number(selection.scope_end_scene ?? selection.lineage?.at(-1)?.scene);
        if (!Number.isFinite(start) || !Number.isFinite(end)) return empty();
        const scoped = lineage.filter(item => item.scene >= start && item.scene <= end);
        const keys = scoped.map(item => checkpointGraphKey(stage, item, profile));
        return {nodes:new Set(keys), tip:keys.at(-1) ?? null,
            edges:new Set(keys.slice(1).map((key, index) => checkpointGraphEdgeKey(keys[index], key)))};
    } catch { return empty(); }
}

// Measure in graph-local coordinates so ComfyUI canvas zoom does not move the
// connectors away from the cards. Reflow/resize is observed; no fixed row height.
export function mountCheckpointGraphEdges(host, model, cards, output, doc = document, win = window) {
    if (!doc.createElementNS || !win.requestAnimationFrame) return () => {};
    const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "h3cm-fork-edges");
    svg.setAttribute("aria-hidden", "true");
    host.prepend(svg);
    let frame = null, disposed = false;
    const draw = () => {
        frame = null;
        if (disposed || !host.isConnected || !host.offsetWidth || !host.offsetHeight) return;
        const base = host.getBoundingClientRect();
        const sx = base.width / host.offsetWidth, sy = base.height / host.offsetHeight;
        if (!sx || !sy) return;
        svg.setAttribute("width", host.offsetWidth);
        svg.setAttribute("height", host.offsetHeight);
        svg.setAttribute("viewBox", `0 0 ${host.offsetWidth} ${host.offsetHeight}`);
        svg.replaceChildren();
        for (const edge of [...model.edges].sort((a, b) => Number(output.edges.has(a.key)) - Number(output.edges.has(b.key)))) {
            const from = cards.get(edge.from)?.getBoundingClientRect();
            const to = cards.get(edge.to)?.getBoundingClientRect();
            if (!from || !to) continue;
            const x1 = (from.right - base.left) / sx, y1 = (from.top + from.height / 2 - base.top) / sy;
            const x2 = (to.left - base.left) / sx, y2 = (to.top + to.height / 2 - base.top) / sy;
            const bend = Math.max(12, (x2 - x1) / 2);
            const path = doc.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("class", "h3cm-fork-edge" + (edge.kind === "reuse" ? " h3cm-fork-edge-reuse"
                : output.edges.has(edge.key) ? " h3cm-fork-edge-output" : ""));
            path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2 - 5} ${y2} M ${x2 - 9} ${y2 - 4} L ${x2 - 3} ${y2} L ${x2 - 9} ${y2 + 4}`);
            path.dataset.from = edge.from; path.dataset.to = edge.to;
            svg.append(path);
        }
    };
    const schedule = () => { if (!disposed && frame === null) frame = win.requestAnimationFrame(draw); };
    const observer = win.ResizeObserver ? new win.ResizeObserver(schedule) : null;
    observer?.observe(host);
    for (const card of cards.values()) observer?.observe(card);
    win.addEventListener?.("resize", schedule);
    schedule();
    return () => {
        disposed = true;
        observer?.disconnect();
        if (frame !== null) win.cancelAnimationFrame(frame);
        win.removeEventListener?.("resize", schedule);
    };
}
