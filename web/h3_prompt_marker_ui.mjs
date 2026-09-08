import {
    promptBracketReplacementQuery,
    promptCompletionQuery,
    promptRetentionReplacementQuery,
    promptTokenReplacementQuery,
} from "./h3_prompt_completion_core.mjs?v=0.6.8";

/** Attach only to the current scene's input; never touch Plan or workflow state.
 * Existing reference chips handle their own clicks before they bubble here.
 * Read live DOM offsets, not offsets saved when chips were last decorated.
 */
export function bindPromptMarkerInteractions({
    input, completion, getText, getCaret, readFragment, restoreCaret,
    getEnabled = () => true, getRecords = () => [], beforeOpen = () => {},
}) {
    function prefixOffset(node, offset, before = false) {
        const range = document.createRange();
        range.selectNodeContents(input);
        if (before) range.setEndBefore(node);
        else range.setEnd(node, offset);
        return readFragment(range.cloneContents()).length;
    }
    const onClick = (event) => {
        if (!getEnabled() || input.disabled || input.readOnly
                || input.getAttribute("contenteditable") === "false") return;
        const text = getText();
        const token = event.target?.closest?.("[data-h3-prompt-marker]");
        let query = null;
        if (token && input.contains(token)) {
            const start = prefixOffset(token, 0, true);
            query = promptTokenReplacementQuery(text, start,
                start + String(token.dataset.token ?? "").length);
        } else if (event.ctrlKey || event.metaKey) {
            let position = getCaret();
            if (input.isContentEditable) {
                const point = document.caretPositionFromPoint?.(event.clientX, event.clientY);
                const fallback = point ? null : document.caretRangeFromPoint?.(event.clientX, event.clientY);
                const node = point?.offsetNode ?? fallback?.startContainer;
                const offset = point?.offset ?? fallback?.startOffset;
                if (node && offset != null && (node === input || input.contains(node))) {
                    position = prefixOffset(node, offset);
                }
            }
            query = promptBracketReplacementQuery(text, position)
                ?? promptRetentionReplacementQuery(text, position, getRecords());
            if (!query) {
                const candidate = promptCompletionQuery(text, position);
                if (candidate?.replacement) query = candidate;
            }
        }
        if (!query) return;
        event.preventDefault();
        beforeOpen();
        restoreCaret(query.end);
        completion.open(query);
    };
    input.addEventListener("click", onClick);
    const cleanup = () => input.removeEventListener("click", onClick);
    const destroy = completion.destroy;
    completion.destroy = () => { cleanup(); destroy(); };
    return cleanup;
}
