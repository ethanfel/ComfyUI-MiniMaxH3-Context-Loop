// Pins are request snapshots, not permissions. Preserve an operation's original
// pin across retries; never silently retry a conflict against a newer root.
export function storageTransport(fetchApi) {
    const pins = new Map();
    const attempts = new Map();
    return async (route, options = {}) => {
        const url = new URL(route, 'http://h3.local');
        if (!url.pathname.startsWith('/minimax_h3_context_loop/') ||
            url.pathname.endsWith('/storage-session')) return fetchApi(route, options);
        let body = {};
        if (typeof options.body === 'string') {
            try { body = JSON.parse(options.body); } catch { /* original handler validates */ }
        } else if (typeof FormData !== 'undefined' && options.body instanceof FormData) {
            body = Object.fromEntries([...options.body.entries()].filter(([, value]) => typeof value === 'string'));
        }
        const project = url.searchParams.get('project') || body?.project ||
            url.searchParams.get('run_name') || body?.run_name;
        if (!project) return fetchApi(route, options);
        const branch = url.searchParams.get('branch_id') || body?.branch_id || 'main';
        const key = JSON.stringify([project, branch]);
        const post = String(options.method || 'GET').toUpperCase() === 'POST';
        const operation = body?.storage_operation_id || body?.operation_id;
        const attemptKey = operation ? JSON.stringify([key, url.pathname, operation]) : null;
        let pin = attemptKey && attempts.get(attemptKey);
        pin ||= pins.get(key);
        if (post && !pin) {
            const query = new URLSearchParams({run_name: project, branch_id: branch});
            const response = await fetchApi(`/minimax_h3_context_loop/storage-session?${query}`);
            if (!response.ok) return response;
            const session = await response.json();
            if (session.organized) {
                pin = session.pin;
                pins.set(key, pin);
            }
        }
        const headers = new Headers(options.headers || {});
        if (post && pin) {
            if (attemptKey && !attempts.has(attemptKey)) attempts.set(attemptKey, pin);
            headers.set('X-H3-Storage-Pin', JSON.stringify(pin));
        }
        const response = await fetchApi(route, {...options, headers});
        const saved = response.headers.get('X-H3-Storage-Pin');
        if (response.ok && saved) {
            const accepted = JSON.parse(saved);
            if (accepted.run_name !== project || accepted.branch_id !== branch)
                throw new Error('H3 storage response belongs to a different project or branch.');
            pins.set(key, accepted);
        }
        return response;
    };
}
