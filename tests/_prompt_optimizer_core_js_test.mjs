#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import {
    directOptimizerConfigurationError,
    makeDirectPromptOptimizeRequest,
    normalizePromptOptimizerApiFormat,
    normalizePromptOptimizerBackend,
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
assert.equal(visible.size, 7, "All seven optimizer controls must survive settings-tree grouping");
assert.deepEqual(new Set(visible.values()), new Set(saved.keys()));
assert.equal(settings.promptOptimizerBackend(), "mcp");
assert.equal(settings.promptOptimizerMcpProvider(), "ollama");
assert.deepEqual(settings.promptOptimizerDirectConfig(), {
    api_format:"responses", api_url:"https://example.invalid/v1",
    api_key:"synthetic-test-key", model:"test-model", allow_media:true,
});
assert.deepEqual(saved, before, "Registration and reads must not mutate saved settings");
const apiKeyDefinition = definitions.get("MiniMaxH3ContexLoop.PromptOptimizer.ApiKey");
assert.equal(apiKeyDefinition.attrs.type, "password");
assert.equal(apiKeyDefinition.telemetry.trackChanges, false);
for (const definition of definitions.values()) definition.onChange("test", "previous");
assert.deepEqual(notifications, Array(7).fill("h3-prompt-optimizer-settings-changed"));
assert.equal(await settings.openPromptOptimizerSettings(), true);
assert.deepEqual(commands, ["Comfy.ShowSettingsDialog"]);
saved.clear();
assert.equal(settings.promptOptimizerBackend(), "direct");
assert.equal(settings.promptOptimizerMcpProvider(), "codex");
assert.deepEqual(settings.promptOptimizerDirectConfig(), {
    api_format:"openai", api_url:"", api_key:"", model:"", allow_media:false,
});

console.log("H3 Direct prompt optimizer: settings and request contract pass");
