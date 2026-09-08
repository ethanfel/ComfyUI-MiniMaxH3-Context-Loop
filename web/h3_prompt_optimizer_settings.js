import {app} from "/scripts/app.js";
import {
    normalizePromptOptimizerApiFormat,
    normalizePromptOptimizerBackend,
} from "./h3_prompt_optimizer_core.mjs?v=0.6.8";

export const PROMPT_OPTIMIZER_SETTING_IDS = Object.freeze({
    backend: "MiniMaxH3ContexLoop.PromptOptimizer.Backend",
    mcpProvider: "MiniMaxH3ContexLoop.PromptOptimizer.McpProvider",
    serverProfile: "MiniMaxH3ContexLoop.PromptOptimizer.ServerProfile",
    apiFormat: "MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat",
    apiUrl: "MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl",
    extraAllowedOrigins: "MiniMaxH3ContexLoop.PromptOptimizer.ExtraAllowedOrigins",
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

const CATEGORY_ROOT = ["MiniMax H3 Context Loop", "Prompt optimizer"];

const API_FORMATS_JSON_PATH = "h3_prompt_optimizer_api_formats.json";
const MCP_PROVIDERS_JSON_PATH = "h3_prompt_optimizer_mcp_providers.json";
const SERVER_PROFILES_JSON_PATH = "h3_prompt_optimizer_server_profiles.json";

// Kept only as a last resort if the JSON file can't be fetched (moved file,
// read-only install, etc.) so the settings still register with something.
const FALLBACK_API_FORMATS = [
    {value: "openai", label: "OpenAI-compatible Chat Completions",
        endpoint: "POST {Direct API URL}/v1/chat/completions",
        compatibility: "OpenAI, most local servers, and most proxies.",
        media: "Reference media: images only."},
    {value: "responses", label: "OpenAI Responses",
        endpoint: "POST {Direct API URL}/v1/responses",
        compatibility: "OpenAI's newer Responses API shape.",
        media: "Reference media: images only."},
    {value: "gemini", label: "Gemini Native",
        endpoint: "POST {Direct API URL}/v1beta/models/{Direct API model}:generateContent",
        compatibility: "Google's native Gemini request/response shape.",
        media: "Reference media: images, video, and audio."},
];
const FALLBACK_MCP_PROVIDERS = [
    "codex", "claude", "gemini", "hermes", "kimi", "moonshot", "glm",
    "minimax", "ollama", "openrouter", "lmstudio", "llamacpp", "custom",
].map((value) => ({value, label: value}));
const FALLBACK_SERVER_PROFILES = [
    {value: "custom", label: "Custom / manual", api_format: null,
        default_url: "", notes: "No auto-fill. Set Direct API format and Direct API URL yourself."},
];

async function loadJson(filename, fallback) {
    try {
        const response = await fetch(new URL(`./${filename}`, import.meta.url));
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data) || !data.length) throw new Error("empty or malformed list");
        return data;
    } catch (error) {
        console.warn(`[H3 prompt optimizer] failed to load ${filename}, using built-in defaults:`, error);
        return fallback;
    }
}

function apiFormatTooltip(entries) {
    const bullets = entries.map((entry) =>
        `• ${entry.label} — ${entry.endpoint}. ${entry.compatibility} ${entry.media}`);
    return "Which endpoint and request shape the Direct API call uses:\n\n" +
        bullets.join("\n\n") +
        `\n\nDefined in web/${API_FORMATS_JSON_PATH} inside this node pack's folder — ` +
        "edit that file to add or adjust entries.";
}

function mcpProviderTooltip(entries) {
    const bullets = entries.map((entry) =>
        `• ${entry.label}${entry.note ? ` — ${entry.note}` : ""}`);
    return "Used only when Prompt optimizer backend is MCP agent. The connected " +
        "comfyui-mcp bridge validates live whether a provider is actually installed " +
        "and authenticated; this list is only the set of recognized names:\n\n" +
        bullets.join("\n") +
        `\n\nDefined in web/${MCP_PROVIDERS_JSON_PATH} inside this node pack's folder — ` +
        "edit that file to add or adjust entries.";
}

function serverProfileTooltip(entries) {
    const bullets = entries
        .filter((entry) => entry.value !== "custom")
        .map((entry) => {
            const endpointCount = Array.isArray(entry.endpoints) ? entry.endpoints.length : 0;
            const endpointNote = endpointCount
                ? ` ${endpointCount} documented endpoint${endpointCount === 1 ? "" : "s"} ` +
                  `(chat, models, unload, etc. where supported) listed in this preset's JSON entry.`
                : "";
            return `• ${entry.label} (${entry.api_format ?? "manual"}` +
                `${entry.default_url ? `, default ${entry.default_url}` : ""}) — ` +
                `${entry.notes}${endpointNote}`;
        });
    return "Sets Direct API format (and, where confidently known, Direct API URL) " +
        "to match a known local server — picking one overwrites whatever those two " +
        "fields currently hold. Purely a convenience preset — you can still edit " +
        "Direct API format/URL by hand afterward instead. This does NOT bypass the " +
        "server's origin allow-list: OpenAI, Gemini, and OpenRouter are permitted " +
        "by default, so a local server still needs its exact origin added via " +
        "H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS before starting ComfyUI, or the call " +
        "will fail with \"Direct API origin ... is not allowed by this server\" " +
        "regardless of this setting.\n\n" +
        bullets.join("\n\n") +
        `\n\nDefined in web/${SERVER_PROFILES_JSON_PATH} inside this node pack's folder — ` +
        "edit that file to add or adjust entries.";
}

app.registerExtension({
    name: "minimax_h3_context_loop.prompt_optimizer_settings",
    async init() {
        const [apiFormats, mcpProviders, serverProfiles] = await Promise.all([
            loadJson(API_FORMATS_JSON_PATH, FALLBACK_API_FORMATS),
            loadJson(MCP_PROVIDERS_JSON_PATH, FALLBACK_MCP_PROVIDERS),
            loadJson(SERVER_PROFILES_JSON_PATH, FALLBACK_SERVER_PROFILES),
        ]);
        // Every setting needs its own distinct third category segment. The
        // settings panel's default (unsearched) view renders one row per
        // unique category path; six settings sharing the same literal leaf
        // ("Connection") collapsed onto a single visible row, so the other
        // five only ever turned up via search. Unique leaves fixed that.
        //
        // The panel also renders settings within a category in the REVERSE
        // of their addSetting() registration order (verified empirically
        // against this ComfyUI frontend build) rather than any documented
        // "order" field, so the add() calls below are deliberately listed
        // bottom-of-display-first to land in the intended top-to-bottom
        // order: Backend, API format, API URL, API key, MCP agent provider,
        // API model, Reference media. Re-check this ordering after a
        // ComfyUI frontend upgrade in case that rendering detail changes.
        const WIDE_FIELD = {style: "width: 100%; max-width: 480px;"};
        const add = (leaf, definition) => app.ui?.settings?.addSetting?.({
            category: [...CATEGORY_ROOT, leaf],
            ...definition,
            attrs: {...WIDE_FIELD, ...definition.attrs},
            onChange(value, previous) {
                definition.onChange?.(value, previous);
                notifyChanged();
            },
        });
        add("Reference media", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.allowMedia,
            name: "Allow Direct API to read reference media",
            tooltip: "Off by default. Accessible reference files up to 32 MB are attached only when the selected API format supports their modality. OpenAI-compatible and Responses attach images; Gemini Native can attach image, video, and audio.",
            type: "boolean",
            defaultValue: false,
        });
        add("Direct API model", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.model,
            name: "Direct API model",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "model identifier"},
        });
        add("MCP agent provider", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.mcpProvider,
            name: "MCP agent provider",
            tooltip: mcpProviderTooltip(mcpProviders),
            type: "combo",
            defaultValue: "codex",
            options: mcpProviders.map((entry) => entry.value),
            attrs: {editable: true, filter: true},
        });
        add("Direct API key", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiKey,
            name: "Direct API key",
            tooltip: "Stored in ComfyUI user settings, never in workflow JSON. Local endpoints require an exact server-side origin allow-list entry; Gemini Native requires a key.",
            type: "text",
            defaultValue: "",
            attrs: {type: "password", autocomplete: "off"},
            telemetry: {trackChanges: false},
        });
        add("Additional allowed Direct API origins", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.extraAllowedOrigins,
            name: "Additional allowed Direct API origins",
            tooltip: "Comma-separated origins (e.g. http://127.0.0.1:1234,http://127.0.0.1:11434) " +
                "allowed in addition to OpenAI, Gemini, and OpenRouter — the same format as the " +
                "server operator's H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS environment variable, but " +
                "set per-user through Settings instead of requiring a ComfyUI restart with a special " +
                "environment variable. Read server-side from your own signed-in ComfyUI settings — " +
                "a workflow JSON can never add to or read this list itself.",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "http://127.0.0.1:1234,http://127.0.0.1:11434"},
        });
        add("Direct API URL", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiUrl,
            name: "Direct API URL",
            tooltip: "A provider base URL or complete supported endpoint. OpenAI, Gemini, and OpenRouter are allowed by default. Server operators can add exact origins through H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS, or add your own through \"Additional allowed Direct API origins\" below.",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "https://api.example.com/v1"},
        });
        add("Direct API format", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiFormat,
            name: "Direct API format",
            tooltip: apiFormatTooltip(apiFormats),
            type: "combo",
            defaultValue: "openai",
            options: apiFormats.map((entry) => ({text: entry.label, value: entry.value})),
        });
        add("Local server preset", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.serverProfile,
            name: "Local server preset",
            tooltip: serverProfileTooltip(serverProfiles),
            type: "combo",
            defaultValue: "custom",
            options: serverProfiles.map((entry) => ({text: entry.label, value: entry.value})),
            onChange(value) {
                const profile = serverProfiles.find((entry) => entry.value === value);
                if (!profile || profile.value === "custom") return;
                if (profile.api_format) {
                    app.ui?.settings?.setSettingValue?.(
                        PROMPT_OPTIMIZER_SETTING_IDS.apiFormat, profile.api_format);
                }
                if (profile.default_url) {
                    app.ui?.settings?.setSettingValue?.(
                        PROMPT_OPTIMIZER_SETTING_IDS.apiUrl, profile.default_url);
                }
            },
        });
        add("Backend", {
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
    },
});
