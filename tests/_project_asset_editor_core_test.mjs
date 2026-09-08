import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

import {
    coupledOutputDimensions,
    dimensionsForMegapixels,
    formatMegapixels,
    imageMegapixels,
} from "../web/h3_project_asset_editor_core.mjs";

assert.equal(imageMegapixels(2000, 1000), 2);
assert.deepEqual(dimensionsForMegapixels(2, 2), {width: 2000, height: 1000});
assert.deepEqual(
    dimensionsForMegapixels(1, 1344 / 768, 8),
    {width: 1320, height: 752},
);
assert.deepEqual(
    dimensionsForMegapixels(1, 1344 / 768, 32),
    {width: 1312, height: 768},
);
assert.deepEqual(
    dimensionsForMegapixels(1, 16 / 9, 8),
    {width: 1336, height: 752},
);
assert.deepEqual(
    dimensionsForMegapixels(1, 16 / 9, 32),
    {width: 1344, height: 736},
);
assert.deepEqual(
    dimensionsForMegapixels(1, 1344 / 768, 1),
    {width: 1323, height: 756},
);
assert.deepEqual(
    coupledOutputDimensions(2400, 500, "width", 2, true),
    {width: 2400, height: 1200},
);
assert.deepEqual(
    coupledOutputDimensions(500, 1200, "height", 2, true),
    {width: 2400, height: 1200},
);
assert.deepEqual(
    coupledOutputDimensions(2400, 500, "width", 2, false),
    {width: 2400, height: 500},
);
assert.deepEqual(
    coupledOutputDimensions(1341, 500, "width", 1.75, true, 8),
    {width: 1344, height: 768},
);
assert.equal(formatMegapixels(4), "4.00");
assert.equal(formatMegapixels(0), "0.000");

// Awkward photo/crop ratios must never inflate the MP request to obtain an
// exact rational ratio. Each dimension stays within half a snapping step.
for (const ratio of [4031 / 3023, 3023 / 4031, 16 / 9, 1344 / 768, 1]) {
    for (const mp of [0.01, 0.25, 1, 2, 8]) {
        for (const multiple of [8, 16, 32, 64]) {
            const {width, height} = dimensionsForMegapixels(mp, ratio, multiple);
            assert.equal(width % multiple, 0);
            assert.equal(height % multiple, 0);
            assert.ok(Math.abs(width - Math.sqrt(mp * 1e6 * ratio)) <= multiple);
            assert.ok(Math.abs(height - Math.sqrt(mp * 1e6 / ratio)) <= multiple);
        }
    }
}
assert.deepEqual(coupledOutputDimensions(1000, 500, "width", 4031 / 3023, true, 32),
    {width: 992, height: 736});

// Exercise the actual editor event handlers with tiny input stubs. In
// particular, changing the multiple must not feed rounded output MP back
// into the user's request (nor drift the ratio with the lock switched off).
const editor = readFileSync(new URL("../web/h3_project_asset_manager.js", import.meta.url), "utf8");
const handlers = ["currentTargetSize", "setTargetSize", "outputRatio", "applyOutputMultiple",
    "applyMegapixelTarget", "updateSizeSummary"].map((name) => {
    const match = editor.match(new RegExp(`        function ${name}\\([^]*?\\n        }`));
    assert.ok(match, name);
    return match[0];
}).join("\n");
const createEditor = new Function("dimensionsForMegapixels", "imageMegapixels", "formatMegapixels", `
    let requestedMegapixels = 1, outputMultiple = 8;
    let lockedRatio = 4031 / 3023, unlockedRatio = lockedRatio;
    const ratioLock = {checked: true}, megapixelInput = {value: "1"};
    const cropInputs = {targetWidth: {value: "1"}, targetHeight: {value: "1"}};
    const crop = {width: 4031, height: 3023};
    const cropStatus = {}, sizeStatus = {}, modelButton = {};
    function modelConnected() {return false;}
    function isFullCrop() {return true;}
    function updateSnapButtons() {}
    function draw() {}
    function syncInputs() {updateSizeSummary();}
    ${handlers}
    return {ratioLock, megapixelInput, applyMegapixelTarget, applyOutputMultiple, currentTargetSize};
`);
for (const locked of [true, false]) {
    const ui = createEditor(dimensionsForMegapixels, imageMegapixels, formatMegapixels);
    ui.ratioLock.checked = locked;
    for (const mp of [1, 0.123456, 8]) {
        ui.megapixelInput.value = String(mp);
        ui.applyMegapixelTarget();
        for (const multiple of [64, 8, 1, 32, 16, 64, 8]) {
            ui.applyOutputMultiple(multiple);
            assert.equal(ui.megapixelInput.value, String(mp));
            assert.deepEqual(ui.currentTargetSize(), dimensionsForMegapixels(mp, 4031 / 3023, multiple));
        }
    }
}

console.log("H3 Project Asset editor: megapixel sizing and locked output dimensions pass");
