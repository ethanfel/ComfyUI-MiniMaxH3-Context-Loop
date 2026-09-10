export const EDITORIAL_COMMAND_VERSION = 1;
export async function editorialCommand(api, body, options = {}) {
    const response = await api.fetchApi('/minimax_h3_context_loop/editorial/command', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        ...options, body: JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Cannot manage this saved sequence.');
    if (result.version !== EDITORIAL_COMMAND_VERSION || result.run_name !== body.run_name
            || result.branch_id !== (body.branch_id || 'main')) throw new Error('H3 returned another saved sequence. Refresh the project.');
    return result;
}
