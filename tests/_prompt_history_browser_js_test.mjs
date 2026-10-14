// Real ancestry renderer and CSS, synthetic revisions only. No ComfyUI or API.
import assert from "node:assert/strict";
import {readFileSync, writeFileSync, mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {spawnSync} from "node:child_process";

const read = name => readFileSync(new URL("../web/" + name, import.meta.url), "utf8");
const source = read("h3_chain_rich_scene_prompt_editor.js");
const extract = (name, indent = "") => {
    const match = source.match(new RegExp("^" + indent + "function " + name + "\\([^]*?^" + indent + "}", "m"));
    assert.ok(match, name);
    return match[0];
};
const production = read("h3_prompt_history_core.mjs").replace(/^export /gm, "") + "\n"
    + ["injectStyles", "element", "button"].map(name => extract(name)).join("\n") + "\n"
    + ["renderHistory", "renderHistoryTree"].map(name => extract(name, "    ")).join("\n");
if (!process.argv.includes("--browser")) {
    console.log("Prompt ancestry fixture extracted; use --browser for isolated Chrome layout/action checks.");
    process.exit(0);
}
const temporary = mkdtempSync(join(tmpdir(), "h3-prompt-history-"));
const file = join(temporary, "fixture.html");
writeFileSync(file, '<!doctype html><meta charset="utf-8"><style>body{margin:8px;background:#171717}</style><body><script>\n'
    + "(" + browserChecks.toString() + ")(" + JSON.stringify(production).replace(/<\/script/gi, "<\\/script") + ");</script>");
const run = spawnSync(process.env.H3_TEST_BROWSER || "/opt/google/chrome/chrome", [
    "--headless", "--disable-gpu", "--no-first-run", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--host-resolver-rules=MAP * ~NOTFOUND",
    "--user-data-dir=" + join(temporary, "profile"), "--window-size=1200,1000",
    "--screenshot=" + join(temporary, "ancestry.png"), "--dump-dom", pathToFileURL(file).href,
], {encoding:"utf8", timeout:25000, maxBuffer:2 * 1024 * 1024});
assert.equal(run.status, 0, run.error?.message || run.stderr);
const encoded = run.stdout.match(/data-report="([^"]+)"/)?.[1];
assert.ok(encoded, "Browser did not finish: " + run.stdout.slice(-1500));
const report = JSON.parse(Buffer.from(encoded, "base64").toString());
console.log(report);
console.log("Isolated screenshot: " + join(temporary, "ancestry.png"));
assert.deepEqual(report.failures, []);

function browserChecks(production) {
    const report = {checks:0, failures:[]};
    const check = (value, message) => { report.checks++; if (!value) report.failures.push(message); };
    try {
        const actions = [];
        const selectHistoryRevision = id => actions.push(["select", id]);
        const mutateHistoryRevision = (...args) => actions.push(args);
        window.prompt = () => "New label";
        window.confirm = () => true;
        // Include a 20-deep chain, a deletable leaf, archived and active takes,
        // and both normal prose and an unbroken string wider than the node.
        const revisions = Array.from({length:21}, (_, i) => ({
            id:`r${i}`, parent_id:i ? `r${i-1}` : null,
            label:i === 1 ? "Long revision label ".repeat(20) : `Revision ${i}`,
            created_at:`2026-10-14T10:00:${String(i).padStart(2,"0")}Z`,
            basic_prompt:i % 3 === 0 ? "" : "A long original prompt. ".repeat(20) + "X".repeat(200),
        }));
        revisions.push({id:"archived", parent_id:"r0", label:"Archived revision", archived_at:"2026-10-14"});
        const data = {active_revision:"r0", revisions};
        const snapshot = JSON.stringify(data);
        const state = {history:{data, treeOpen:true, showArchived:false}};
        // Only persistence and browser dialogs are stubbed: controls, layout,
        // navigation, permission guards and ancestry come from production.
        const mount = new Function("state", "selectHistoryRevision", "mutateHistoryRevision", production + `
            injectStyles();
            const root = element("div", "h3rp-root");
            root.style.height = "720px";
            document.body.append(root);
            const shell = element("div", "h3rp-editor-shell");
            const footer = element("div", "h3rp-footer");
            state.history.host = element("div", "h3rp-history");
            footer.append(element("span", "", "Scene 1/1 · scene_1"), state.history.host,
                element("span", "h3rp-footer-status", "Synchronized with Plan"));
            root.append(shell, footer);
            renderHistory();
            return {root, renderHistory};
        `)(state, selectHistoryRevision, mutateHistoryRevision);
        const root = mount.root;
        for (const width of [800, 523, 420, 280]) {
            for (const zoom of [1, 0.65, 1.3]) {
                root.style.width = `${width}px`;
                root.style.zoom = String(zoom);
                const panel = root.querySelector(".h3rp-history-tree-panel");
                const rootBox = root.getBoundingClientRect(), panelBox = panel.getBoundingClientRect();
                check(root.querySelector(".h3rp-footer > span").getBoundingClientRect().width > 20,
                    `${width}px/${zoom}: ancestry does not squeeze the scene identity to zero width`);
                check(panelBox.left >= rootBox.left && panelBox.right <= rootBox.right,
                    `${width}px/${zoom}: popup stays inside node, not centered off its edge`);
                check(panel.scrollWidth <= panel.clientWidth + 1,
                    `${width}px/${zoom}: long text and deep ancestry never cause horizontal overflow`);
                check(panel.clientHeight <= 310 && panel.scrollHeight > panel.clientHeight,
                    `${width}px/${zoom}: long ancestry stays vertically scrollable`);
                for (const row of panel.querySelectorAll(".h3rp-history-tree-row")) {
                    const original = row.querySelector(".h3rp-history-tree-basic-prompt");
                    if (original) {
                        const controlsBottom = Math.max(...[...row.querySelectorAll("button")].map(item => item.getBoundingClientRect().bottom));
                        check(original.getBoundingClientRect().top >= controlsBottom - 1,
                            `${width}px/${zoom}: basic prompt is below, not beside controls`);
                    }
                    for (const control of row.querySelectorAll(".h3rp-history-tree-action")) {
                        check(control.scrollWidth <= control.clientWidth + 1,
                            `${width}px/${zoom}: ${control.textContent} text fits its button`);
                        control.scrollIntoView({block:"center", inline:"nearest"});
                        const box = control.getBoundingClientRect();
                        check(box.left >= panelBox.left && box.right <= panelBox.right,
                            `${width}px/${zoom}: ${control.textContent} is horizontally reachable`);
                        const hit = document.elementFromPoint((box.left+box.right)/2, (box.top+box.bottom)/2);
                        check(hit === control || control.contains(hit),
                            `${width}px/${zoom}: ${control.textContent} is not clipped or covered`);
                    }
                }
            }
        }
        root.style.zoom = "1"; root.style.width = "420px";
        let rows = root.querySelectorAll(".h3rp-history-tree-row");
        check(!rows[0].querySelector(".h3rp-history-tree-action[title^='Archive']"), "Active take cannot be archived");
        check(rows[0].querySelector(".h3rp-history-tree-select").disabled, "Active take cannot be reactivated");
        rows[1].querySelector(".h3rp-history-tree-select").click();
        rows[1].querySelector(".h3rp-history-tree-action").click();
        rows[1].querySelector("[title^='Archive']").click();
        check(!rows[1].querySelector("[title^='Delete']"), "Non-leaf revision has no delete action");
        rows[rows.length-1].querySelector("[title^='Delete']").click();
        root.querySelector(".h3rp-history-tree-tools button").click();
        rows = root.querySelectorAll(".h3rp-history-tree-row");
        check(rows.length === revisions.length && state.history.showArchived, "Show archived preserves all revisions");
        root.querySelector("[title^='Restore']").click();
        check(JSON.stringify(actions) === JSON.stringify([
            ["select", "r1"], ["label", "r1", {label:"New label"}],
            ["archive", "r1", {archived:true}], ["delete", "r20"], ["archive", "archived", {archived:false}],
        ]), "Label, archive, delete, restore and select keep their exact targets and payloads");
        check(JSON.stringify(data) === snapshot, "Rendering never changes saved prompts or ancestry");
        root.querySelector(".h3rp-history-tree-panel").scrollTop = 0;
    } catch (error) { report.failures.push(error.stack || String(error)); }
    document.body.dataset.report = btoa(JSON.stringify(report));
}
