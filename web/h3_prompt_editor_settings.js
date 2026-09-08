import {app} from "/scripts/app.js";
import {
    H3_PROMPT_EDITOR_SETTING_DEFINITIONS,
    h3PromptEditorPreferences,
} from "./h3_prompt_editor_settings_core.mjs?v=0.6.8";

export function promptEditorPreferences() {
    return h3PromptEditorPreferences((id, fallback) =>
        app.ui?.settings?.getSettingValue?.(id) ?? fallback);
}

app.registerExtension({
    name:"minimax_h3_context_loop.prompt_editor_settings",
    init() {
        for (const definition of H3_PROMPT_EDITOR_SETTING_DEFINITIONS) {
            app.ui?.settings?.addSetting?.({
                ...definition,
                onChange() {
                    globalThis.dispatchEvent?.(new CustomEvent("h3-prompt-editor-settings-changed"));
                },
            });
        }
    },
});
