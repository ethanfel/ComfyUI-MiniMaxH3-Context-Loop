const DEFAULT_RIGHT_PX = 18;
const DEFAULT_GAP_PX = 12;
const DEFAULT_FALLBACK_BOTTOM_PX = 18;
const DEFAULT_MAX_WIDTH = "min(390px,calc(100vw - 36px))";

function isVisibleRect(rect) {
    return Boolean(rect)
        && Number(rect.width) > 0
        && Number(rect.height) > 0
        && Number(rect.top) >= 0;
}

export function dockedBottomOffset(anchorRect, viewportHeight,
        gapPx = DEFAULT_GAP_PX, fallbackBottomPx = DEFAULT_FALLBACK_BOTTOM_PX) {
    const height = Number(viewportHeight);
    if (!isVisibleRect(anchorRect) || !Number.isFinite(height) || height <= 0) {
        return Math.max(0, Math.round(fallbackBottomPx));
    }
    const bottom = height - Number(anchorRect.top) + Number(gapPx);
    return Math.max(0, Math.round(bottom));
}

export function createNotificationStack({
    document = globalThis.document,
    window = globalThis.window,
    anchorSelector = ".h3cr-root",
    rightPx = DEFAULT_RIGHT_PX,
    gapPx = DEFAULT_GAP_PX,
    fallbackBottomPx = DEFAULT_FALLBACK_BOTTOM_PX,
    maxWidth = DEFAULT_MAX_WIDTH,
    rootClass = "h3mh-notification-stack",
    itemClass = "h3mh-notification",
} = {}) {
    if (!document?.createElement) {
        throw new Error("A document with createElement is required.");
    }
    const items = new Map();
    const dismissTimers = new Map();
    let root = null;
    let anchor = null;
    let anchorResizeObserver = null;
    let anchorMutationObserver = null;
    let bodyMutationObserver = null;
    let observersReady = false;
    let refreshPending = false;

    function ensureStyle() {
        if (document.getElementById("h3mh-notification-stack-style")) return;
        const style = document.createElement("style");
        style.id = "h3mh-notification-stack-style";
        style.textContent = `
            .${rootClass} { position:fixed; right:${rightPx}px; bottom:${fallbackBottomPx}px;
                z-index:10030; display:flex; flex-direction:column; align-items:flex-end;
                gap:${gapPx}px; max-width:${maxWidth}; pointer-events:none; }
            .${rootClass}[hidden] { display:none; }
            .${itemClass} { pointer-events:auto; display:flex; align-items:flex-start;
                gap:7px; padding:8px 12px; border:1px solid #4a4f5d; border-radius:9px;
                background:#181a20ee; color:#d9dce5; box-shadow:0 3px 12px #0008;
                font:12px/1.35 system-ui,sans-serif; box-sizing:border-box;
                max-width:${maxWidth}; min-width:0; text-align:right;
                white-space:pre-wrap; overflow-wrap:anywhere; }
            .${itemClass}__message { flex:1; min-width:0; }
            .${itemClass}__dismiss { border:0; background:transparent; color:inherit; cursor:pointer; padding:0 0 0 5px; font:inherit; font-size:16px; line-height:1; }
            .${itemClass}--warning { color:#fff1cb; border-color:#8f7242; }
            .${itemClass}--error { color:#ffd0bc; border-color:#a86148; }
            .${itemClass}--info { color:#d9dce5; }
        `;
        document.head.appendChild(style);
    }

    function ensureRoot() {
        if (root) return root;
        ensureStyle();
        root = document.createElement("div");
        root.className = rootClass;
        root.hidden = true;
        root.setAttribute("aria-live", "polite");
        root.setAttribute("aria-atomic", "false");
        document.body.appendChild(root);
        return root;
    }

    function detachAnchorObservers() {
        anchorResizeObserver?.disconnect?.();
        anchorResizeObserver = null;
        anchorMutationObserver?.disconnect?.();
        anchorMutationObserver = null;
    }

    function detachBodyObserver() {
        bodyMutationObserver?.disconnect?.();
        bodyMutationObserver = null;
    }

    function scheduleRefresh() {
        if (refreshPending) return;
        refreshPending = true;
        const raf = window?.requestAnimationFrame ?? ((fn) => window?.setTimeout(fn, 16));
        raf(() => {
            refreshPending = false;
            refreshPosition();
        });
    }

    function bindAnchor(nextAnchor) {
        if (anchor === nextAnchor) return;
        detachAnchorObservers();
        anchor = nextAnchor || null;
        if (anchor && window?.ResizeObserver) {
            anchorResizeObserver = new window.ResizeObserver(() => scheduleRefresh());
            anchorResizeObserver.observe(anchor);
        }
        if (anchor && window?.MutationObserver) {
            anchorMutationObserver = new window.MutationObserver(() => scheduleRefresh());
            anchorMutationObserver.observe(anchor, {
                attributes: true,
                attributeFilter: ["hidden", "style", "class"],
            });
        }
        if (anchor) {
            detachBodyObserver();
        }
        scheduleRefresh();
    }

    function refreshAnchor() {
        const next = document.querySelector?.(anchorSelector) ?? null;
        bindAnchor(next);
        if (!anchor && window?.MutationObserver && !bodyMutationObserver) {
            bodyMutationObserver = new window.MutationObserver(() => {
                const found = document.querySelector?.(anchorSelector) ?? null;
                if (found) bindAnchor(found);
            });
            bodyMutationObserver.observe(document.body, {childList: true, subtree: true});
        }
    }

    function refreshPosition() {
        if (!root) return;
        if (!anchor) {
            refreshAnchor();
        }
        const rect = anchor?.getBoundingClientRect?.();
        const bottom = dockedBottomOffset(
            rect,
            window?.innerHeight,
            gapPx,
            fallbackBottomPx,
        );
        root.style.right = `${rightPx}px`;
        root.style.bottom = `${bottom}px`;
        root.style.maxWidth = maxWidth;
        root.hidden = items.size === 0;
    }

    function ensureItem(key, tone = "info", role = "status", live = "polite") {
        ensureRoot();
        let item = items.get(key);
        if (!item) {
            item = document.createElement("div");
            item.className = `${itemClass} ${itemClass}--${tone}`;
            item.setAttribute("role", role);
            item.setAttribute("aria-live", live);
            item.setAttribute("aria-atomic", "true");
            const message = document.createElement("span");
            message.className = `${itemClass}__message`;
            const dismiss = document.createElement("button");
            dismiss.type = "button";
            dismiss.className = `${itemClass}__dismiss`;
            dismiss.textContent = "×";
            dismiss.setAttribute("aria-label", "Dismiss notification");
            dismiss.addEventListener?.("click", () => clear(key));
            item.append(message, dismiss);
            item._h3Message = message;
            items.set(key, item);
            root.appendChild(item);
        } else {
            item.className = `${itemClass} ${itemClass}--${tone}`;
            item.setAttribute("role", role);
            item.setAttribute("aria-live", live);
        }
        root.hidden = false;
        refreshPosition();
        return item;
    }

    function show(key, message, tone = "info", {
        role = tone === "error" ? "alert" : "status",
        live = tone === "error" ? "assertive" : "polite",
        durationMs = 0,
    } = {}) {
        const item = ensureItem(key, tone, role, live);
        item._h3Message.textContent = String(message);
        const priorTimer = dismissTimers.get(key);
        if (priorTimer != null) window?.clearTimeout?.(priorTimer);
        dismissTimers.delete(key);
        if (Number(durationMs) > 0) {
            const timer = (window?.setTimeout ?? globalThis.setTimeout)(() => {
                dismissTimers.delete(key);
                clear(key);
            }, Number(durationMs));
            dismissTimers.set(key, timer);
        }
        return item;
    }

    function clear(key) {
        const priorTimer = dismissTimers.get(key);
        if (priorTimer != null) window?.clearTimeout?.(priorTimer);
        dismissTimers.delete(key);
        const item = items.get(key);
        if (!item) return;
        item.remove();
        items.delete(key);
        if (!items.size && root) {
            root.hidden = true;
        }
    }

    function clearAll() {
        for (const key of [...items.keys()]) clear(key);
        if (root) root.hidden = true;
    }

    function destroy() {
        clearAll();
        detachAnchorObservers();
        detachBodyObserver();
        root?.remove?.();
        root = null;
        anchor = null;
    }

    if (window?.addEventListener) {
        window.addEventListener("resize", scheduleRefresh);
        window.addEventListener("scroll", scheduleRefresh, true);
    }

    return {
        show,
        clear,
        clearAll,
        destroy,
        refresh: scheduleRefresh,
        refreshPosition,
        get root() {
            ensureRoot();
            return root;
        },
        get anchor() {
            return anchor;
        },
        get count() {
            return items.size;
        },
    };
}
