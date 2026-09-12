#!/usr/bin/env node

import assert from "node:assert/strict";
import {createHash, webcrypto} from "node:crypto";
import {sha256Bytes, derivedSceneSeed} from "../web/h3_chain_plan_core.mjs";
import {
    PROJECT_ASSET_CATALOG_CHANGED_EVENT,
    publishProjectAssetCatalogChanged,
    serializedProjectAssetCatalog,
    serializedProjectAssetIdentity,
    projectAssetEditAttempts,
} from "../web/h3_project_asset_sync_core.mjs";

const serializedCatalog = serializedProjectAssetCatalog(JSON.stringify({
    project:"episode", revision:"rev-1", assets:[{id:"asset-1"}],
    reference_slots:[],
}), "episode");
assert.equal(serializedCatalog.assets[0].id, "asset-1");
assert.deepEqual(serializedCatalog.folders, []);
assert.equal(serializedProjectAssetCatalog("not json", "episode"), null);
assert.equal(serializedProjectAssetCatalog(JSON.stringify({
    project:"other", assets:[], reference_slots:[],
}), "episode"), null);
assert.equal(serializedProjectAssetIdentity("episode", ""), "episode");
assert.equal(serializedProjectAssetIdentity("h3_project", JSON.stringify({
    project:"restored-run", assets:[], reference_slots:[],
})), "restored-run");
assert.equal(serializedProjectAssetIdentity("", JSON.stringify({
    project:"restored-run", assets:[], reference_slots:[],
})), "restored-run");
assert.equal(serializedProjectAssetIdentity("", "not json"), "");
assert.equal(serializedProjectAssetIdentity("h3_project", JSON.stringify({
    project:"h3_project", assets:[], reference_slots:[],
})), "");

const originalCustomEvent = globalThis.CustomEvent;
const originalDispatchEvent = globalThis.dispatchEvent;
const events = [];
globalThis.CustomEvent = class CustomEvent {
    constructor(type, options = {}) {
        this.type = type;
        this.detail = options.detail;
    }
};
globalThis.dispatchEvent = (event) => {
    events.push(event);
    return true;
};

try {
    const manager = {};
    const catalog = {project:"episode", revision:"revision-1", assets:[]};
    assert.equal(publishProjectAssetCatalogChanged(manager, catalog), true);
    assert.equal(events.length, 1);
    assert.equal(events[0].type, PROJECT_ASSET_CATALOG_CHANGED_EVENT);
    assert.equal(events[0].detail.manager, manager);
    assert.equal(events[0].detail.project, "episode");
    assert.equal(events[0].detail.revision, "revision-1");
    assert.equal(publishProjectAssetCatalogChanged(manager, catalog), false);
    assert.equal(events.length, 1, "unchanged revisions should not wake every editor again");
    assert.equal(publishProjectAssetCatalogChanged(manager, {
        ...catalog, revision:"revision-2",
    }), true);
    assert.equal(events.length, 2);
} finally {
    globalThis.CustomEvent = originalCustomEvent;
    globalThis.dispatchEvent = originalDispatchEvent;
}

console.log("H3 project asset catalog notifications pass");

const attempts = projectAssetEditAttempts(null); // LAN HTTP: no secure-context randomUUID.
const route = "/minimax_h3_context_loop/project-assets/folder";
const request = {method:"POST", body:JSON.stringify({project:"demo",action:"create",name:"Characters"})};
const originalRequest = structuredClone(request);
const first = attempts.prepare(route, request);
const identity = JSON.parse(first.options.body).storage_operation_id;
assert.match(identity, /^[a-f0-9]{32}$/);
assert.deepEqual(request, originalRequest, "never mutate caller request/ownership options");
assert.equal(JSON.parse(attempts.prepare(route, request).options.body).storage_operation_id, identity);
assert.equal(JSON.parse(attempts.prepare(route, {
    method:"POST", body:JSON.stringify({name:"Characters",action:"create",project:"demo"}),
}).options.body).storage_operation_id, identity, "object key order does not change a retry");
assert.notEqual(JSON.parse(attempts.prepare(route, {
    method:"POST", body:JSON.stringify({project:"other",action:"create",name:"Characters"}),
}).options.body).storage_operation_id, identity, "another project is a different edit");
first.accept();
assert.notEqual(JSON.parse(attempts.prepare(route, request).options.body).storage_operation_id, identity);
const upload = {method:"POST",body:{upload:true}};
assert.equal(attempts.prepare("/minimax_h3_context_loop/project-assets/upload",upload).options,upload);
assert.throws(() => attempts.prepare(route,{method:"POST",body:JSON.stringify({storage_operation_id:"bad"})}));
console.log("H3 project asset metadata edit retry identities pass");

for (const size of [0, 1, 55, 56, 63, 64, 65, 1024 * 1024 + 3]) {
    const bytes = new Uint8Array(size).map((_, i) => i % 251);
    const expected = createHash("sha256").update(bytes).digest("hex");
    for (const crypto of [null, webcrypto]) {
        assert.equal(Buffer.from(await sha256Bytes(bytes, crypto)).toString("hex"), expected);
    }
}
assert.equal(await derivedSceneSeed("18446744073709551615", 7, "scene", null),
    await derivedSceneSeed("18446744073709551615", 7, "scene", webcrypto), "seed derivation is unchanged");

const uploadRoute = "/minimax_h3_context_loop/project-assets/upload";
function multipart(bytes, project = "demo") {
    const data = new FormData();
    data.append("file", new Blob([bytes], {type:"image/png"}), "test.png");
    data.append("project", project); // helper must put fields before file
    return {method:"POST", body:data, headers:{"X-H3-Workflow-Owner":"kept"}};
}
for (const crypto of [null, webcrypto]) {
    const uploads = projectAssetEditAttempts(crypto);
    const bytes = new Uint8Array(1024 * 1024 + 7).fill(42);
    const original = multipart(bytes);
    const firstUpload = await uploads.prepare(uploadRoute, original);
    const id = firstUpload.options.body.get("storage_operation_id");
    assert.match(id, /^[a-f0-9]{32}$/);
    assert.equal(original.body.has("storage_operation_id"), false);
    assert.equal(firstUpload.options.headers, original.headers);
    assert.deepEqual([...firstUpload.options.body.keys()], ["project","storage_operation_id","file"]);
    assert.equal((await uploads.prepare(uploadRoute,multipart(bytes))).options.body.get("storage_operation_id"), id,
        "reselected same file keeps the lost-reply retry ID");
    const changed = bytes.slice(); changed[changed.length - 1]++;
    assert.notEqual((await uploads.prepare(uploadRoute,multipart(changed))).options.body.get("storage_operation_id"), id,
        "same name and size with changed final-chunk bytes is a new request");
    assert.notEqual((await uploads.prepare(uploadRoute,multipart(bytes,"other"))).options.body.get("storage_operation_id"), id);
    firstUpload.accept();
    assert.notEqual((await uploads.prepare(uploadRoute,multipart(bytes))).options.body.get("storage_operation_id"), id);
}
const malformed = multipart("test"); malformed.body.append("project","other");
await assert.rejects(attempts.prepare(uploadRoute,malformed), /Duplicate upload field/);
console.log("H3 bounded upload fingerprints and multipart retries pass");
