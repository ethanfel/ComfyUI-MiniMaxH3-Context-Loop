import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deliveryPrompt, prepareDelivery } from '../web/h3_delivery_core.mjs';

function fixture() {
    return {workflow: {id: 'original', extra: {native: true}}, output: {
        assemble: {class_type: 'MiniMaxH3ChainAssemble', inputs: {
            manifest: ['loop', 0], blend_video_vae: ['vae', 0], filename: 'native', audio_bitrate: 192}},
        vae: {class_type: 'VAELoader', inputs: {vae_name: 'native.safetensors'}},
        loop: {class_type: 'MiniMaxH3ChainLoopEnd', inputs: {}},
        other: {class_type: 'SaveImage', inputs: {images: ['sampler', 0]}},
    }};
}

test('delivery substitutes only the saved source and retains serialized ancillary inputs and metadata', () => {
    const graph = fixture(), before = JSON.stringify(graph);
    const result = deliveryPrompt(graph, 'assemble', 'exact JSON with 18446744073709551615', {filename: 'saved'});
    assert.equal(result.prompt.workflow, graph.workflow);
    assert.equal(result.prompt.output.vae, graph.output.vae);
    assert.deepEqual(Object.keys(result.prompt.output).sort(), ['assemble', 'h3_saved_delivery', 'vae']);
    assert.equal(result.prompt.output.h3_saved_delivery.inputs.snapshot_json, 'exact JSON with 18446744073709551615');
    assert.equal(result.prompt.output.assemble.inputs.audio_bitrate, 192);
    assert.equal(JSON.stringify(graph), before);
});

test('generating ancillary branches, connected overrides and overwrite are rejected', () => {
    const graph = fixture();
    graph.output.vae.class_type = 'UnknownSampler';
    assert.throws(() => deliveryPrompt(graph, 'assemble', 'saved'), /source adapter/);
    graph.output.vae.class_type = 'VAELoader';
    graph.output.assemble.inputs.audio_bitrate = ['vae', 0];
    assert.throws(() => deliveryPrompt(graph, 'assemble', 'saved', {audio_bitrate: 256}), /connected/);
    graph.output.assemble.inputs.overwrite_existing = true;
    assert.throws(() => deliveryPrompt(graph, 'assemble', 'saved'), /overwrite/);
});

test('preparation requires the exact requested branch and editorial revision', async () => {
    const selection = {run_name: 'film', _branch_id: 'a'.repeat(32), final_cut_branch_id: 'main'};
    const summary = {run_name: 'film', branch_id: selection._branch_id, final_cut_branch_id: 'main', editorial_revision: 'cut'};
    const api = {async fetchApi(path, options) {
        assert.equal(path, '/minimax_h3_context_loop/delivery/prepare');
        assert.deepEqual(JSON.parse(options.body), {selection, editorial_revision: 'cut'});
        return Response.json({version: 1, snapshot_json: 'exact native text', snapshot_id: 'd'.repeat(64), summary});
    }};
    assert.equal((await prepareDelivery(api, selection, 'cut')).snapshot_json, 'exact native text');
    summary.branch_id = 'main';
    await assert.rejects(prepareDelivery(api, selection, 'cut'), /different delivery source/);
});
