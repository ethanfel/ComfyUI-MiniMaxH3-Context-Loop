import {
    analyzeH3Prompt,
    effectiveH3Mode,
    ensureH3Structure,
    H3_MODES,
    h3ModeLabel,
    insertH3Section,
} from "./h3_prompt_schema_core.mjs?v=0.6.8";

function injectStyles() {
    if (document.getElementById("h3-prompt-schema-ui-style")) return;
    const style = document.createElement("style");
    style.id = "h3-prompt-schema-ui-style";
    style.textContent = `
      .h3schema-mode { min-width:112px !important; }
      .h3schema-toggle.h3schema-invalid { border-color:#bd6c6c !important; }
      .h3schema-toggle.h3schema-valid { border-color:#5b9c70 !important; }
      .h3schema-panel { display:none; max-height:300px; overflow:auto; padding:8px; gap:7px;
        border:1px solid var(--h3sp-border,var(--h3rp-border,var(--border-color,#555)));
        border-radius:7px; background:var(--h3sp-panel,var(--h3rp-panel,var(--comfy-input-bg,#171a21)));
        color:var(--h3sp-text,var(--h3rp-text,var(--input-text,#edf2fa)));
        font:12px/1.35 system-ui,sans-serif; }
      .h3schema-panel.h3schema-open { display:flex; flex-direction:column; }
      .h3schema-head,.h3schema-options,.h3schema-section-row {
        display:flex; align-items:center; gap:6px; }
      .h3schema-head { flex-wrap:wrap; }
      .h3schema-title { min-width:180px; flex:1; }
      .h3schema-counts,.h3schema-muted {
        color:var(--h3sp-muted,var(--h3rp-muted,color-mix(in srgb,currentColor 58%,transparent))); }
      .h3schema-counts { white-space:nowrap; font-variant-numeric:tabular-nums; }
      .h3schema-options { flex-wrap:wrap; }
      .h3schema-options label { display:flex; align-items:center; gap:4px;
        color:var(--h3sp-muted,var(--h3rp-muted,color-mix(in srgb,currentColor 58%,transparent))); }
      .h3schema-options input { width:76px; min-height:28px; padding:4px 6px;
        border:1px solid var(--h3sp-border,var(--h3rp-border,var(--border-color,#555)));
        border-radius:5px; background:var(--comfy-input-bg,#171a21); color:inherit; font:inherit; }
      .h3schema-section-list { display:grid; gap:5px; }
      .h3schema-section-row { min-width:0; padding:5px 7px;
        border:1px solid var(--h3sp-border,var(--h3rp-border,var(--border-color,#555)));
        border-radius:6px; background:color-mix(in srgb,var(--comfy-input-bg,#11141a) 88%,transparent); }
      .h3schema-section-row.h3schema-missing { border-style:dashed; color:#e29a9a; }
      .h3schema-mark { flex:0 0 18px; text-align:center; font-weight:800; }
      .h3schema-name { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; font:600 11px/1.3 ui-monospace,SFMono-Regular,Consolas,monospace; }
      .h3schema-panel button { min-height:26px; padding:3px 7px; color:inherit; font:inherit;
        border:1px solid var(--h3sp-border,var(--h3rp-border,var(--border-color,#555)));
        border-radius:5px; background:var(--comfy-input-bg,#171a21); cursor:pointer; }
      .h3schema-problems { display:grid; gap:3px;
        color:var(--h3sp-muted,var(--h3rp-muted,color-mix(in srgb,currentColor 58%,transparent))); }
      .h3schema-problem-error { color:#ffaaaa; }
      .h3schema-problem-warning { color:#e9bd72; }
    `;
    document.head.append(style);
}

function element(tag, className = "", text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
}

function button(label, title, action) {
    const item = element("button", "", label);
    item.type = "button";
    item.title = title;
    item.addEventListener("pointerdown", (event) => event.preventDefault());
    item.addEventListener("click", action);
    return item;
}

function finiteDuration(value, fallback = 6) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.max(0.01, number) : fallback;
}

function finiteShot(value) {
    const number = Math.trunc(Number(value));
    return Number.isFinite(number) ? Math.max(1, Math.min(99, number)) : 1;
}

/**
 * Shared strict-schema inspector used by both Motion Context prompt editors.
 * The host retains ownership of text, undo, Plan persistence, and focus.
 */
export function createH3PromptSchemaController({
    node,
    propertyPrefix,
    getText,
    replaceText,
    focusAt,
    getRecords = () => [],
    defaultDuration = 6,
    defaultMode = "auto",
    scopeKey = "",
    markDirty = () => {},
    onAnalysis = () => {},
} = {}) {
    if (!node || typeof getText !== "function" || typeof replaceText !== "function") return null;
    injectStyles();
    node.properties ??= {};
    const prefix = String(propertyPrefix || "h3_prompt");
    const property = {
        mode:`${prefix}_schema_mode`,
        open:`${prefix}_structure_open`,
        alignment:`${prefix}_schema_alignment_by_scene`,
    };
    const selected = String(node.properties[property.mode] ?? defaultMode);
    const alignment = node.properties[property.alignment]?.[String(scopeKey)] ?? {};
    const state = {
        mode:H3_MODES.some((item) => item.id === selected) ? selected : "auto",
        open:Boolean(node.properties[property.open]),
        duration:finiteDuration(alignment.duration, finiteDuration(defaultDuration)),
        finalShot:finiteShot(alignment.finalShot),
        analysis:null,
    };

    const modeSelect = element("select", "h3schema-mode");
    modeSelect.title = "Choose the strict H3 prompt schema; Auto detects it from the prompt";
    modeSelect.setAttribute("aria-label", "H3 prompt schema");
    for (const mode of H3_MODES) {
        const option = element("option", "", mode.label);
        option.value = mode.id;
        modeSelect.append(option);
    }
    modeSelect.value = state.mode;

    const toggle = button("Sections", "Show strict H3 categories and validation", () => {
        state.open = !state.open;
        node.properties[property.open] = state.open;
        panel.classList.toggle("h3schema-open", state.open);
        refresh();
        markDirty();
    });
    toggle.className = "h3schema-toggle";
    toggle.setAttribute("aria-expanded", String(state.open));

    const panel = element("div", "h3schema-panel");
    panel.classList.toggle("h3schema-open", state.open);
    const counts = element("span", "h3schema-counts");

    function structureOptions() {
        return {duration:state.duration, finalShot:state.finalShot};
    }

    function persistAlignment() {
        const alignments = {
            ...(node.properties[property.alignment]
                && typeof node.properties[property.alignment] === "object"
                ? node.properties[property.alignment] : {}),
        };
        alignments[String(scopeKey)] = {
            duration:state.duration,
            finalShot:state.finalShot,
        };
        node.properties[property.alignment] = alignments;
    }

    function commitResult(result, message) {
        replaceText({
            text:String(result.text ?? ""),
            caret:Number.isFinite(result.caret) ? result.caret : String(result.text ?? "").length,
        }, message);
    }

    function addSection(section) {
        const text = String(getText() ?? "");
        const mode = effectiveH3Mode(text, state.mode);
        const result = insertH3Section(text, section, mode);
        if (result.added) commitResult(result, `Added ${section}:`);
        else focusAt?.(result.caret);
    }

    function normalizeStructure() {
        const text = String(getText() ?? "");
        const mode = effectiveH3Mode(text, state.mode);
        const result = ensureH3Structure(text, mode, structureOptions());
        const message = result.added.length
            ? `Added ${result.added.length} missing section${result.added.length === 1 ? "" : "s"}`
            : `Normalized ${h3ModeLabel(result.mode)} alignment`;
        commitResult(result, message);
    }

    function optionInput(label, value, {min, max, step}, action) {
        const host = element("label");
        host.append(document.createTextNode(label));
        const input = element("input");
        input.type = "number";
        input.min = String(min);
        input.max = String(max);
        input.step = String(step);
        input.value = String(value);
        input.addEventListener("change", () => action(input.value));
        host.append(input);
        return host;
    }

    function renderPanel(text, analysis) {
        if (!state.open) return;
        panel.replaceChildren();
        const words = text.trim() ? text.trim().split(/\s+/).length : 0;
        const head = element("div", "h3schema-head");
        head.append(
            element("strong", "h3schema-title", `${h3ModeLabel(analysis.mode)} strict section order`),
            element("span", "h3schema-counts", `${words} words · ${text.length} chars`),
            button(
                analysis.missing.length ? "Add missing" : "Normalize",
                "Add absent categories in H3 order and write the exact mode alignment line",
                normalizeStructure,
            ),
        );
        panel.append(head);

        if (["i2va", "fl2va", "l2va"].includes(analysis.mode)) {
            const options = element("div", "h3schema-options");
            if (["fl2va", "l2va"].includes(analysis.mode)) {
                options.append(
                    optionInput("Duration (s)", state.duration, {min:0.01, max:999, step:0.01}, (value) => {
                        state.duration = finiteDuration(value);
                        persistAlignment();
                        refresh();
                        markDirty();
                    }),
                    optionInput("Final shot", state.finalShot, {min:1, max:99, step:1}, (value) => {
                        state.finalShot = finiteShot(value);
                        persistAlignment();
                        refresh();
                        markDirty();
                    }),
                );
            } else {
                options.append(element(
                    "span", "h3schema-muted",
                    "I2VA uses <Picture 1> at 0.00 seconds in [Shot 1].",
                ));
            }
            panel.append(options);
        }

        const list = element("div", "h3schema-section-list");
        for (const section of analysis.required) {
            const record = analysis.records.find((item) => item.name === section);
            const row = element("div", `h3schema-section-row${record ? "" : " h3schema-missing"}`);
            row.append(
                element("span", "h3schema-mark", record ? "✓" : "+"),
                element("span", "h3schema-name", `${section}:`),
                button(record ? "Go" : "Add", record
                    ? `Go to ${section}:` : `Add ${section}: in H3 order`, () => {
                    if (record) focusAt?.(record.contentStart);
                    else addSection(section);
                }),
            );
            list.append(row);
        }
        panel.append(list);

        const problems = element("div", "h3schema-problems");
        if (!analysis.problems.length) {
            problems.append(element(
                "div", "",
                "✓ Exact section names, ordering, alignment, and dialogue balance pass.",
            ));
        } else {
            for (const problem of analysis.problems) {
                problems.append(element(
                    "div", `h3schema-problem-${problem.severity}`,
                    `${problem.severity === "error" ? "×" : "!"} ${problem.message}`,
                ));
            }
        }
        panel.append(problems);
    }

    function refresh() {
        const text = String(getText() ?? "");
        const words = text.trim() ? text.trim().split(/\s+/).length : 0;
        counts.textContent = `${words} words · ${text.length} chars`;
        state.analysis = analyzeH3Prompt(text, state.mode, {
            ...structureOptions(),
            connectedReferences:getRecords(),
        });
        const errors = state.analysis.problems.filter((item) => item.severity === "error").length;
        toggle.classList.toggle("h3schema-invalid", errors > 0);
        toggle.classList.toggle("h3schema-valid", errors === 0);
        toggle.title = errors
            ? `Show strict H3 categories and validation · ${errors} structure error${errors === 1 ? "" : "s"}`
            : "Show strict H3 categories and validation · structure valid";
        toggle.setAttribute("aria-expanded", String(state.open));
        renderPanel(text, state.analysis);
        onAnalysis(state.analysis);
        return state.analysis;
    }

    modeSelect.addEventListener("change", () => {
        state.mode = modeSelect.value;
        node.properties[property.mode] = state.mode;
        refresh();
        markDirty();
    });

    refresh();
    return {
        modeSelect,
        toggle,
        panel,
        counts,
        refresh,
        getMode:() => state.mode,
        get analysis() { return state.analysis; },
        destroy() {},
    };
}
