export const PROMPT_OPTIMIZER_BACKENDS = Object.freeze(["direct", "mcp", "disabled"]);
export const PROMPT_OPTIMIZER_API_FORMATS = Object.freeze(["openai", "responses", "gemini"]);

export function normalizePromptOptimizerBackend(value) {
    const backend = String(value ?? "direct").trim().toLowerCase();
    return PROMPT_OPTIMIZER_BACKENDS.includes(backend) ? backend : "direct";
}

export function normalizePromptOptimizerApiFormat(value) {
    const format = String(value ?? "openai").trim().toLowerCase();
    return PROMPT_OPTIMIZER_API_FORMATS.includes(format) ? format : "openai";
}

export function normalizePromptOptimizerTimeoutSeconds(value = 300) {
    const seconds = typeof value === "number" || (typeof value === "string" && value.trim())
        ? Number(value) : NaN;
    if (!Number.isInteger(seconds) || seconds < 30 || seconds > 7200) {
        throw new Error("Direct API timeout must be a whole number between 30 and 7200 seconds.");
    }
    return seconds;
}

export function directOptimizerConfigurationError(config = {}) {
    if (!String(config.api_url ?? "").trim()) return "Direct API URL is not configured.";
    if (!String(config.model ?? "").trim()) return "Direct API model is not configured.";
    if (normalizePromptOptimizerApiFormat(config.api_format) === "gemini"
            && !String(config.api_key ?? "").trim()) {
        return "Gemini Native requires an API key.";
    }
    try { normalizePromptOptimizerTimeoutSeconds(config.timeout_seconds); }
    catch (error) { return error.message; }
    return "";
}

export function makeDirectPromptOptimizeRequest({config, instruction, context, resources = []}) {
    const error = directOptimizerConfigurationError(config);
    if (error) throw new Error(`${error} Open ComfyUI Settings → MiniMax H3 Context Loop → Prompt optimizer.`);
    return {
        api_format: normalizePromptOptimizerApiFormat(config.api_format),
        api_url: String(config.api_url ?? "").trim(),
        api_key: String(config.api_key ?? ""),
        model: String(config.model ?? "").trim(),
        timeout_seconds: normalizePromptOptimizerTimeoutSeconds(config.timeout_seconds),
        allow_media: config.allow_media === true,
        resources: config.allow_media === true ? resources : [],
        instruction: String(instruction ?? "").trim(),
        context,
    };
}
