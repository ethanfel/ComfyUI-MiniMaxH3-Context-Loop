#!/usr/bin/env node

import assert from "node:assert/strict";
import {createNotificationStack, dockedBottomOffset}
    from "../web/h3_notification_stack_core.mjs";

function makeElement(tag) {
    const node = {
        tagName: String(tag).toUpperCase(),
        children: [],
        parentNode: null,
        hidden: false,
        textContent: "",
        className: "",
        style: {},
        attributes: {},
        appendChild(child) {
            child.parentNode = node;
            node.children.push(child);
            return child;
        },
        append(...children) {
            for (const child of children) node.appendChild(child);
        },
        remove() {
            if (!node.parentNode) return;
            const list = node.parentNode.children;
            const index = list.indexOf(node);
            if (index >= 0) list.splice(index, 1);
            node.parentNode = null;
        },
        addEventListener(name, callback) { node._listeners ??= {}; node._listeners[name] = callback; },
        click() { node._listeners?.click?.(); },
        setAttribute(name, value) {
            node.attributes[name] = String(value);
        },
        getAttribute(name) {
            return node.attributes[name] ?? null;
        },
        getBoundingClientRect() {
            return node._rect ?? {
                top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0,
            };
        },
    };
    return node;
}

function makeDocument(anchorState) {
    const head = makeElement("head");
    const body = makeElement("body");
    const anchor = makeElement("div");
    anchor.className = "h3cr-root";
    const state = anchorState ?? {visible: true, top: 612, height: 126};
    anchor._rect = () => null;
    anchor.getBoundingClientRect = () => state.visible ? ({
        top: state.top,
        left: 1680,
        width: 380,
        height: state.height,
        right: 2060,
        bottom: state.top + state.height,
    }) : ({top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0});
    const document = {
        head,
        body,
        createElement: makeElement,
        querySelector(selector) {
            return selector === ".h3cr-root" ? anchor : null;
        },
        getElementById(id) {
            return [...head.children, ...body.children].find((item) => item.id === id) ?? null;
        },
    };
    return {document, state, anchor, head, body};
}

class NoopObserver {
    constructor(callback) {
        this.callback = callback;
    }
    observe() {}
    disconnect() {}
}

const window = {
    innerHeight: 1000,
    ResizeObserver: NoopObserver,
    MutationObserver: NoopObserver,
    requestAnimationFrame(callback) { callback(); },
    setTimeout(callback) { callback(); return 1; },
    addEventListener() {},
};

const env = makeDocument();
const stack = createNotificationStack({document: env.document, window});

assert.equal(dockedBottomOffset({top: 612, width: 100, height: 40}, 1000, 12, 18), 400);
assert.equal(dockedBottomOffset({top: 0, width: 0, height: 0}, 1000, 12, 18), 18);

stack.show("transient", "Waiting for a safe queue state…", "info");
assert.equal(stack.root.hidden, false);
const transient = stack.root.children[0];
assert.equal(transient.children[1].tagName, "BUTTON");
assert.equal(transient.children[1].getAttribute("aria-label"), "Dismiss notification");
assert.equal(stack.root.children.length, 1);
assert.match(stack.root.children[0].className, /h3mh-notification--info/);
assert.equal(stack.root.children[0].children[0].textContent, "Waiting for a safe queue state…");
assert.equal(stack.root.style.bottom, "400px");

stack.show("transient", "Checking the workflow and predecessor checkpoint…", "info");
assert.equal(stack.root.children.length, 1, "transient updates must reuse the same entry");
assert.equal(stack.root.children[0].children[0].textContent,
    "Checking the workflow and predecessor checkpoint…");

stack.show("warning", "The handoff was already claimed; nothing was queued.", "warning");
assert.equal(stack.root.children.length, 2);
assert.match(stack.root.children[1].className, /h3mh-notification--warning/);

stack.show("error", "Top-level requeue did not queue: boom", "error");
assert.equal(stack.root.children.length, 3);
assert.match(stack.root.children[2].className, /h3mh-notification--error/);
assert.equal(stack.root.children[2].attributes.role, "alert");
assert.equal(stack.root.children[2].attributes["aria-live"], "assertive");

env.state.top = 520;
stack.refreshPosition();
assert.equal(stack.root.style.bottom, "492px");

env.state.visible = false;
stack.refreshPosition();
assert.equal(stack.root.style.bottom, "18px");

stack.clear("transient");
stack.clear("warning");
stack.clear("error");
assert.equal(stack.root.children.length, 0);
assert.equal(stack.root.hidden, true);

console.log("H3 notification stack: update-in-place, stacked notices, and anchor-aware positioning pass");
