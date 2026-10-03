import assert from "node:assert/strict";
import vm from "node:vm";
import {branchWidgetTransaction} from "../web/h3_working_branches.mjs";

// Model the nested reactive values exposed by the frontend without depending
// on a particular Vue build. The real browser test also includes DOM widgets.
const proxies = new WeakMap();
function reactive(value) {
    if (!value || typeof value !== "object") return value;
    if (!proxies.has(value)) proxies.set(value, new Proxy(value, {
        get(target, key, receiver) { return reactive(Reflect.get(target, key, receiver)); },
    }));
    return proxies.get(value);
}

for (const wrap of [value => value, reactive]) {
    const callback = () => {};
    const data = {nested:{prompt:"keep", seed:"18446744073709551615"},
        exact:18446744073709551615n, unset:undefined, frames:[1, , 3], callback};
    data.self = data;
    data.alias = data.nested;
    const value = wrap(data);
    const ignored = {serialize:false, get value() { throw Error("UI widget must not be read"); }};
    const optionsIgnored = {options:{serialize:false}, get value() { throw Error("DOM widget must not be read"); }};
    const node = {properties:wrap({branch:"main", payload:value}), widgets:[
        {name:"data", value}, {name:"button", value:callback}, ignored, optionsIgnored,
    ]};
    const companion = {properties:wrap({prompt:"original"}), widgets:[{value:"old prompt"}]};
    const error = new Error("callback failed after partial apply");
    let calls = 0;
    assert.throws(() => branchWidgetTransaction([node, null, node, companion], () => {
        calls++;
        node.properties.branch = "other";
        value.nested.prompt = "changed";
        value.frames.push(4);
        node.widgets[0].value = "replaced";
        companion.properties.prompt = "changed";
        companion.widgets[0].value = "changed";
        throw error;
    }), thrown => thrown === error);
    assert.equal(calls, 1);
    assert.equal(node.properties.branch, "main");
    const restored = node.widgets[0].value;
    assert.equal(restored.nested.prompt, "keep");
    assert.equal(restored.nested.seed, "18446744073709551615");
    assert.equal(restored.exact, 18446744073709551615n);
    assert.ok(Object.hasOwn(restored, "unset"));
    assert.equal(restored.unset, undefined);
    assert.deepEqual(restored.frames, [1, , 3]);
    assert.equal(restored.self, restored);
    assert.equal(restored.alias, restored.nested);
    assert.equal(restored.callback, callback);
    assert.equal(node.widgets[1].value, callback);
    assert.equal(companion.properties.prompt, "original");
    assert.equal(companion.widgets[0].value, "old prompt");
    assert.notEqual(restored, value);
    value.nested.prompt = "later edit to abandoned state";
    assert.equal(restored.nested.prompt, "keep", "rollback must restore an independent snapshot");
    assert.equal(branchWidgetTransaction([node], () => {
        node.properties.branch = "saved";
        return 42;
    }), 42);
    assert.equal(node.properties.branch, "saved", "successful actions must not be rolled back");
}

// Foreign-realm and null-prototype data are still data; __proto__ is an own
// field, never an instruction to change the snapshot's prototype.
for (const properties of [
    vm.runInNewContext('({nested:{prompt:"original"}})'),
    Object.assign(Object.create(null), {nested:{prompt:"original"}}),
    JSON.parse('{"nested":{"prompt":"original"},"__proto__":{"polluted":true}}'),
]) {
    const node = {properties:reactive(properties), widgets:[]};
    assert.throws(() => branchWidgetTransaction([node], () => {
        node.properties.nested.prompt = "changed";
        throw Error("rollback");
    }), /rollback/);
    assert.equal(node.properties.nested.prompt, "original");
    assert.equal(node.properties.polluted, undefined);
    if (Object.hasOwn(properties, "__proto__")) {
        assert.ok(Object.hasOwn(node.properties, "__proto__"));
        assert.equal(node.properties.__proto__.polluted, true);
    }
}

// Snapshot failures are not permission to apply a branch without a backup.
{
    const failure = new Error("property unavailable");
    const node = {properties:{get broken() { throw failure; }}, widgets:[]};
    let applied = false;
    assert.throws(() => branchWidgetTransaction([node], () => { applied = true; }), error => error === failure);
    assert.equal(applied, false);
}
console.log("Branch widget snapshots: reactive data, callbacks, rollback, exact seeds and fail-closed capture pass");
