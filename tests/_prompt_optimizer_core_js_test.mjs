#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    directOptimizerConfigurationError,
    makeDirectPromptOptimizeRequest,
    normalizePromptOptimizerApiFormat,
    normalizePromptOptimizerBackend,
    normalizePromptOptimizerTimeoutSeconds,
} from "../web/h3_prompt_optimizer_core.mjs";

assert.equal(normalizePromptOptimizerBackend(undefined), "direct");
assert.equal(normalizePromptOptimizerBackend("MCP"), "mcp");
assert.equal(normalizePromptOptimizerBackend("unknown"), "direct");
assert.equal(normalizePromptOptimizerApiFormat("RESPONSES"), "responses");
assert.equal(normalizePromptOptimizerApiFormat("unknown"), "openai");

assert.match(directOptimizerConfigurationError({}), /URL/);
assert.match(directOptimizerConfigurationError({api_url:"http://localhost:1234"}), /model/);
assert.match(directOptimizerConfigurationError({
    api_url:"https://example.invalid", model:"gemini-test", api_format:"gemini",
}), /API key/);

const resources = [{type:"image", asset:{filename:"hero.png", storage:"input"}}];
const withoutMedia = makeDirectPromptOptimizeRequest({
    config:{api_format:"openai", api_url:"http://localhost:1234/v1", model:"local", allow_media:false},
    instruction:"Polish only the camera move.",
    context:{source_prompt:"A tracking shot."},
    resources,
});
assert.equal(withoutMedia.allow_media, false);
assert.deepEqual(withoutMedia.resources, []);
assert.equal(withoutMedia.api_format, "openai");
assert.equal(withoutMedia.timeout_seconds, 300, "Older configurations retain the five-minute default");
assert.equal(normalizePromptOptimizerTimeoutSeconds("1800"), 1800);
for (const value of [30, 1800, 7200]) {
    assert.equal(makeDirectPromptOptimizeRequest({
        config:{api_url:"https://example.invalid", model:"local", timeout_seconds:value},
        instruction:"Polish.", context:{source_prompt:"A tracking shot."},
    }).timeout_seconds, value);
}
for (const value of [null, false, true, "", " ", "invalid", 29, 7201, 30.5, NaN, Infinity, {}, []]) {
    assert.throws(() => normalizePromptOptimizerTimeoutSeconds(value), /whole number/);
    assert.match(directOptimizerConfigurationError({
        api_url:"https://example.invalid", model:"local", timeout_seconds:value,
    }), /timeout/);
}

const withMedia = makeDirectPromptOptimizeRequest({
    config:{api_format:"gemini", api_url:"https://example.invalid", api_key:"secret", model:"models/gemini-test", allow_media:true},
    instruction:"Check visual continuity.",
    context:{source_prompt:"@hero walks forward."},
    resources,
});
assert.equal(withMedia.allow_media, true);
assert.deepEqual(withMedia.resources, resources);

const settingsSource = fs.readFileSync(
    new URL("../web/h3_prompt_optimizer_settings.js", import.meta.url), "utf8");
assert.match(settingsSource, /defaultValue: "direct"/);
assert.match(settingsSource, /Comfy\.ShowSettingsDialog/);
assert.match(settingsSource, /telemetry: \{trackChanges: false\}/);

// Execute the real settings registration with a synthetic ComfyUI settings
// store. Never read user settings, contact a provider or persist any values.
const saved = new Map([
    ["MiniMaxH3ContexLoop.PromptOptimizer.Backend", "mcp"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.McpProvider", "ollama"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat", "responses"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl", "https://example.invalid/v1"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.ApiKey", "synthetic-test-key"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.Model", "test-model"],
    ["MiniMaxH3ContexLoop.PromptOptimizer.TimeoutSeconds", 1800],
    ["MiniMaxH3ContexLoop.PromptOptimizer.AllowMedia", true],
]);
const before = new Map(saved);
const definitions = new Map();
const notifications = [];
const commands = [];
const mockApp = {
    ui:{settings:{
        addSetting(definition) {
            assert.ok(!definitions.has(definition.id), "Setting IDs must be unique");
            definitions.set(definition.id, definition);
        },
        getSettingValue:id => saved.get(id) ?? definitions.get(id)?.defaultValue,
        setSettingValue() { assert.fail("Registration must not overwrite saved settings"); },
    }},
    registerExtension(extension) { extension.init(); },
    extensionManager:{command:{async execute(command) { commands.push(command); }}},
};
const settings = new Function("app", "normalizePromptOptimizerApiFormat",
    "normalizePromptOptimizerBackend", "globalThis", "CustomEvent",
    settingsSource.replace(/^import[\s\S]*?from ["'][^"']+["'];\s*/gm, "")
        .replace(/^export /gm, "")
        + "\nreturn {promptOptimizerBackend, promptOptimizerMcpProvider, promptOptimizerDirectConfig, openPromptOptimizerSettings};")(
    mockApp, normalizePromptOptimizerApiFormat, normalizePromptOptimizerBackend,
    {dispatchEvent:event => notifications.push(event.type)},
    class { constructor(type) { this.type = type; } },
);
assert.deepEqual([...definitions.keys()], [...saved.keys()], "Keep all existing persisted IDs");

// ComfyUI's settings tree keys leaves by category path. Reusing the whole
// path silently replaces earlier controls with the last registered one.
const visible = new Map();
for (const definition of definitions.values()) {
    assert.deepEqual(definition.category.slice(0, 2), ["MiniMax H3 Context Loop", "Prompt optimizer"]);
    assert.equal(definition.category.length, 3);
    assert.ok(definition.category[2]);
    visible.set(JSON.stringify(definition.category), definition.id);
}
assert.equal(visible.size, 8, "All optimizer controls must survive settings-tree grouping");
assert.deepEqual(new Set(visible.values()), new Set(saved.keys()));
assert.equal(settings.promptOptimizerBackend(), "mcp");
assert.equal(settings.promptOptimizerMcpProvider(), "ollama");
assert.deepEqual(settings.promptOptimizerDirectConfig(), {
    api_format:"responses", api_url:"https://example.invalid/v1",
    api_key:"synthetic-test-key", model:"test-model", allow_media:true,
    timeout_seconds:1800,
});
assert.deepEqual(saved, before, "Registration and reads must not mutate saved settings");
const apiKeyDefinition = definitions.get("MiniMaxH3ContexLoop.PromptOptimizer.ApiKey");
assert.equal(apiKeyDefinition.attrs.type, "password");
assert.equal(apiKeyDefinition.telemetry.trackChanges, false);
for (const definition of definitions.values()) definition.onChange("test", "previous");
assert.deepEqual(notifications, Array(8).fill("h3-prompt-optimizer-settings-changed"));
assert.equal(await settings.openPromptOptimizerSettings(), true);
assert.deepEqual(commands, ["Comfy.ShowSettingsDialog"]);
saved.clear();
assert.equal(settings.promptOptimizerBackend(), "direct");
assert.equal(settings.promptOptimizerMcpProvider(), "codex");
assert.deepEqual(settings.promptOptimizerDirectConfig(), {
    api_format:"openai", api_url:"", api_key:"", model:"", allow_media:false,
    timeout_seconds:300,
});

// Exercise the production cancellation/late-result guard with a deliberately
// delayed mock request. Aborting the browser must not apply its late result.
const editorSource = fs.readFileSync(new URL("../web/h3_chain_rich_scene_prompt_editor.js", import.meta.url), "utf8");
const optimizeDirect = editorSource.match(/^    async function optimizeDirect\([^]*?^    }/m)?.[0];
const stopOptimizer = editorSource.match(/^    function stopOptimizer\([^]*?^    }/m)?.[0];
assert.ok(optimizeDirect && stopOptimizer);
let finish, sent;
const mockApi = {fetchApi(_url, options) {
    sent = options;
    return new Promise(resolve => { finish = resolve; });
}};
const runner = new Function("api", "makeDirectPromptOptimizeRequest", `
    const state = {optimizer:{}};
    const applied = [];
    const refreshOptimizerUi = () => {};
    const applyOptimizerResponse = (...args) => applied.push(args);
    ${optimizeDirect}
    ${stopOptimizer}
    return {state, applied, optimizeDirect, stopOptimizer};
`)(mockApi, makeDirectPromptOptimizeRequest);
const config = {api_url:"https://example.invalid", model:"test", timeout_seconds:1800};
const run = runner.optimizeDirect("one", "Rewrite.", {source_prompt:"Unchanged."}, [], {}, config);
assert.equal(JSON.parse(sent.body).timeout_seconds, 1800);
runner.stopOptimizer();
assert.equal(sent.signal.aborted, true);
assert.match(runner.state.optimizer.message, /not changed/);
finish({ok:true, json:async () => ({prompt:"Late replacement"})});
await run;
assert.deepEqual(runner.applied, []);
const next = runner.optimizeDirect("two", "Rewrite.", {source_prompt:"Unchanged."}, [], {}, config);
finish({ok:false, json:async () => ({error:"Direct API request timed out (1800 seconds). See Settings."})});
await next;
assert.match(runner.state.optimizer.error, /1800 seconds/);
assert.equal(runner.state.optimizer.requestId, null);
assert.deepEqual(runner.applied, []);

console.log("H3 Direct prompt optimizer: settings and request contract pass");
