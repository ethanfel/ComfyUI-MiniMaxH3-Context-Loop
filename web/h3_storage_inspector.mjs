// On-demand project-wide evidence. Never changes node selections or Plan state.
export function mountStorageInspector(host, {request, currentRun}) {
    const el = (tag, text = "") => {
        const value = document.createElement(tag);
        value.textContent = text;
        return value;
    };
    const bytes = value => value == null ? "unknown" : value < 1024 ? `${value} B`
        : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KiB`
        : value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MiB`
        : `${(value / 1024 ** 3).toFixed(2)} GiB`;
    const style = el("style", `
        .h3-storage {padding:12px;border:1px solid #626b7b;border-radius:6px;min-width:0;}
        .h3-storage[hidden] {display:none;}
        .h3-storage h3 {margin:0 0 8px;}
        .h3-storage p {margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere;}
        .h3-storage .h3-storage-tools {display:flex;gap:8px;flex-wrap:wrap;margin:8px 0;}
        .h3-storage .h3-storage-scroll {max-height:420px;overflow:auto;}
        .h3-storage table {border-collapse:collapse;width:100%;font-size:12px;}
        .h3-storage th,.h3-storage td {padding:6px;text-align:left;border-bottom:1px solid #454951;vertical-align:top;}
        .h3-storage td {overflow-wrap:anywhere;}
        .h3-storage td:first-child {overflow-wrap:anywhere;max-width:400px;}
        .h3-storage input {min-width:180px;flex:1;background:var(--h3cm-panel,#15171d);
            color:var(--h3cm-text,#edf1f8);border:1px solid var(--h3cm-border,#586174);
            padding:5px 7px;border-radius:6px;font:inherit;}
        .h3-storage summary {cursor:pointer;}
    `);
    host.classList.add("h3-storage");
    const title = el("h3", "Storage Inspector · read-only");
    const note = el("p", "All working branches in this project. No repair, deletion, migration, media hashing or tensor loading.");
    const status = el("p");
    status.setAttribute("role", "status");
    const controls = el("div"); controls.className = "h3-storage-tools";
    const refresh = el("button", "Rescan");
    const download = el("button", "Download inventory JSON");
    const close = el("button", "Close");
    controls.append(refresh, download, close);
    const contents = el("div");
    host.append(style, title, note, controls, status, contents);
    let report = null;
    let run = "";
    let token = 0;
    let controller = null;

    function dismiss() {
        token++;
        controller?.abort();
        controller = null;
        report = null;
        host.hidden = true;
        contents.replaceChildren();
        download.disabled = true;
    }
    function syncRun() { if (run !== currentRun()) dismiss(); }
    async function open() {
        run = currentRun();
        if (!run) return;
        controller?.abort();
        controller = new AbortController();
        const mine = ++token;
        const selectedRun = run;
        report = null;
        host.hidden = false;
        download.disabled = true;
        refresh.disabled = true;
        contents.replaceChildren();
        status.textContent = `${run} · inspecting project files…`;
        try {
            const result = await request(`/minimax_h3_context_loop/storage-inventory?run_name=${encodeURIComponent(run)}`,
                {method:"GET", signal:controller.signal});
            if (mine !== token || selectedRun !== currentRun()) return;
            if (result.format !== "h3_storage_inventory_v1" || result.run_name !== selectedRun) {
                throw new Error("Unexpected storage inventory response.");
            }
            report = result;
            download.disabled = false;
            render();
        } catch (error) {
            if (mine === token && selectedRun === currentRun()) {
                status.textContent = `Storage inspection failed: ${error.message}`;
            }
        } finally {
            if (mine === token) refresh.disabled = false;
        }
    }
    function table(headers, rows) {
        const value = el("table");
        const head = el("tr");
        for (const label of headers) head.append(el("th", label));
        const thead = el("thead"); thead.append(head); value.append(thead);
        const body = el("tbody");
        for (const row of rows) {
            const tr = el("tr");
            for (const field of row) tr.append(el("td", String(field ?? "unknown")));
            body.append(tr);
        }
        value.append(body);
        return value;
    }
    function details(label, text) {
        const result = el("details");
        result.append(el("summary", label), el("p", text));
        return result;
    }
    function render() {
        const data = report;
        status.textContent = `${data.run_name} · ${data.totals.files} files · ${bytes(data.totals.logical_bytes)} logical · `
            + `${bytes(data.totals.allocated_bytes)} reported allocation${data.totals.allocation_unknown_files ? " (partial)" : ""}`
            + ` · ${data.scan_complete ? "scan completed" : "PARTIAL / changed during scan"}`;
        contents.replaceChildren();
        contents.append(table(["Category", "Files", "Logical", "Reported allocation"],
            Object.entries(data.categories).map(([name, row]) => [name.replaceAll("_", " "), row.files,
                bytes(row.logical_bytes), `${bytes(row.allocated_bytes)}${row.allocation_unknown_files ? " (partial)" : ""}`])));
        contents.append(details("Limits and migration status (not assessed)", data.limitations.join("\n")));
        contents.append(details("Longest paths", data.longest_paths.map(item =>
            `${item.absolute_chars} absolute characters · ${item.path}`).join("\n") || "No files."));
        const issueCount = Object.values(data.issue_counts).reduce((a, b) => a + b, 0);
        contents.append(details(`Inspection notices · ${issueCount}`, data.issues.map(item =>
            `${item.code} · ${item.path}\n${item.message}`).join("\n\n")
            + (data.issues_omitted ? `\n${data.issues_omitted} additional notices omitted; counts included in JSON.` : "")
            || "No scan notices. Content integrity and migration readiness have NOT been verified."));
        const filterRow = el("div"); filterRow.className = "h3-storage-tools";
        const filter = el("input"); filter.placeholder = "Filter paths, branch IDs or profile names";
        filter.setAttribute("aria-label", "Filter storage inventory");
        const category = el("select"); category.setAttribute("aria-label", "Storage category");
        const all = el("option", "All categories"); all.value = ""; category.append(all);
        for (const name of Object.keys(data.categories)) {
            const option = el("option", name.replaceAll("_", " ")); option.value = name; category.append(option);
        }
        filterRow.append(filter, category);
        const pageRow = el("div"); pageRow.className = "h3-storage-tools";
        const previous = el("button", "Previous"); const next = el("button", "Next"); const range = el("span");
        pageRow.append(previous, range, next);
        const scroll = el("div"); scroll.className = "h3-storage-scroll";
        let page = 0;
        const records = new Map((data.records ?? []).map(row => [row.metadata, row]));
        function drawFiles() {
            const query = filter.value.toLowerCase();
            const rows = data.files.filter(row => (!category.value || row.category === category.value)
                && `${row.path} ${row.branch_id} ${row.profile ?? ""}`.toLowerCase().includes(query));
            const start = page * 50;
            range.textContent = `${rows.length ? start + 1 : 0}–${Math.min(start + 50, rows.length)} of ${rows.length}`;
            previous.disabled = page === 0; next.disabled = start + 50 >= rows.length;
            scroll.replaceChildren(table(["Path", "Logical", "Storage scope / pass", "Observed references / state", "Saved take declarations"],
                rows.slice(start, start + 50).map(row => [row.path, bytes(row.logical_bytes),
                    `${row.branch_id === "main" ? "Project / Original" : row.branch_id}${row.profile ? " / " + row.profile : ""}`,
                    `${row.referenced_by.length} metadata documents · ${row.flags.join(", ").replaceAll("_", " ")}`,
                    records.has(row.path) ? `Scene ${records.get(row.path).scene} · ${records.get(row.path).revision}`
                        + ` · source ${records.get(row.path).source_revision ?? "unknown"}`
                        + ` · full latent: ${records.get(row.path).full_latent.replaceAll("_", " ")}` : "—"])));
        }
        previous.addEventListener("click", () => {page--; drawFiles();});
        next.addEventListener("click", () => {page++; drawFiles();});
        for (const input of [filter, category]) input.addEventListener("input", () => {page = 0; drawFiles();});
        contents.append(filterRow, pageRow, scroll);
        contents.append(el("p", "Unreferenced candidate means no reference was observed by this limited scan—not unused or safe to delete. Per-file references, source revisions and declared latent availability are included in the JSON report."));
        drawFiles();
    }
    refresh.addEventListener("click", () => void open());
    close.addEventListener("click", dismiss);
    download.addEventListener("click", () => {
        if (!report || report.run_name !== currentRun()) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:"application/json"}));
        const link = el("a");
        link.href = url; link.download = `${report.run_name}.storage-inventory.json`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    return {open, dismiss, syncRun};
}
