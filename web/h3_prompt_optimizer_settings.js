import {app} from "/scripts/app.js";
import {
    normalizePromptOptimizerApiFormat,
    normalizePromptOptimizerBackend,
} from "./h3_prompt_optimizer_core.mjs?v=0.7.0";

export const PROMPT_OPTIMIZER_SETTING_IDS = Object.freeze({
    backend: "MiniMaxH3ContexLoop.PromptOptimizer.Backend",
    mcpProvider: "MiniMaxH3ContexLoop.PromptOptimizer.McpProvider",
    apiFormat: "MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat",
    apiUrl: "MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl",
    apiKey: "MiniMaxH3ContexLoop.PromptOptimizer.ApiKey",
    model: "MiniMaxH3ContexLoop.PromptOptimizer.Model",
    allowMedia: "MiniMaxH3ContexLoop.PromptOptimizer.AllowMedia",
});

function settingValue(id, fallback) {
    return app.ui?.settings?.getSettingValue?.(id) ?? fallback;
}

export function promptOptimizerBackend() {
    return normalizePromptOptimizerBackend(
        settingValue(PROMPT_OPTIMIZER_SETTING_IDS.backend, "direct"));
}

export function promptOptimizerMcpProvider() {
    const value = String(settingValue(
        PROMPT_OPTIMIZER_SETTING_IDS.mcpProvider, "codex") ?? "").trim().toLowerCase();
    return /^[a-z][a-z0-9_-]*$/.test(value) ? value : "codex";
}

export function promptOptimizerDirectConfig() {
    return {
        api_format: normalizePromptOptimizerApiFormat(settingValue(
            PROMPT_OPTIMIZER_SETTING_IDS.apiFormat, "openai")),
        api_url: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.apiUrl, "") ?? ""),
        api_key: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.apiKey, "") ?? ""),
        model: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.model, "") ?? ""),
        allow_media: settingValue(PROMPT_OPTIMIZER_SETTING_IDS.allowMedia, false) === true,
    };
}

export async function openPromptOptimizerSettings() {
    const command = app?.extensionManager?.command;
    if (!command || typeof command.execute !== "function") return false;
    try {
        await command.execute("Comfy.ShowSettingsDialog");
        return true;
    } catch {
        return false;
    }
}

function notifyChanged() {
    globalThis.dispatchEvent?.(new CustomEvent("h3-prompt-optimizer-settings-changed"));
}

const category = ["MiniMax H3 Context Loop", "Prompt optimizer"];

app.registerExtension({
    name: "minimax_h3_context_loop.prompt_optimizer_settings",
    init() {
        const add = (definition) => app.ui?.settings?.addSetting?.({
            // ComfyUI keys the display tree by category path, not setting ID.
            // Give each control its own leaf without changing persisted IDs.
            category: [...category, definition.id.split(".").pop()],
            ...definition,
            onChange(value, previous) {
                definition.onChange?.(value, previous);
                notifyChanged();
            },
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.backend,
            name: "Prompt optimizer backend",
            tooltip: "Direct API works without comfyui-mcp and is the portable default. MCP agent uses the separately installed compatible orchestrator. Disabled hides optimizer execution.",
            type: "combo",
            defaultValue: "direct",
            options: [
                {text: "Direct API (default)", value: "direct"},
                {text: "MCP agent", value: "mcp"},
                {text: "Disabled", value: "disabled"},
            ],
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.mcpProvider,
            name: "MCP agent provider",
            tooltip: "Used only when Prompt optimizer backend is MCP agent. The compatible comfyui-mcp bridge validates whether the provider is installed and authenticated.",
            type: "combo",
            defaultValue: "codex",
            options: ["codex", "claude", "gemini", "hermes", "kimi", "moonshot", "glm", "minimax", "ollama", "openrouter", "lmstudio", "llamacpp", "custom"],
            attrs: {editable: true, filter: true},
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiFormat,
            name: "Direct API format",
            type: "combo",
            defaultValue: "openai",
            options: [
                {text: "OpenAI-compatible Chat Completions", value: "openai"},
                {text: "OpenAI Responses", value: "responses"},
                {text: "Gemini Native", value: "gemini"},
            ],
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiUrl,
            name: "Direct API URL",
            tooltip: "A provider base URL or complete supported endpoint. OpenAI, Gemini, and OpenRouter are allowed by default. Server operators can add exact origins through H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS.",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "https://api.example.com/v1"},
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiKey,
            name: "Direct API key",
            tooltip: "Stored in ComfyUI user settings, never in workflow JSON. Local endpoints require an exact server-side origin allow-list entry; Gemini Native requires a key.",
            type: "text",
            defaultValue: "",
            attrs: {type: "password", autocomplete: "off"},
            telemetry: {trackChanges: false},
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.model,
            name: "Direct API model",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "model identifier"},
        });
        add({
            id: PROMPT_OPTIMIZER_SETTING_IDS.allowMedia,
            name: "Allow Direct API to read reference media",
            tooltip: "Off by default. Accessible reference files up to 32 MB are attached only when the selected API format supports their modality. OpenAI-compatible and Responses attach images; Gemini Native can attach image, video, and audio.",
            type: "boolean",
            defaultValue: false,
        });
    },
});
