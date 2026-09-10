// Native saved-delivery protocol. Serialize with ComfyUI first; retain exact
// native node payloads and full workflow metadata, and substitute only the
// selected assembly's source with its prepared immutable manifest loader.
export const DELIVERY_VERSION = 1;
const sourceClass = 'MiniMaxH3ChainDeliverySource';
const link = value => Array.isArray(value) && value.length === 2 && typeof value[0] === 'string' && Number.isInteger(value[1]);
// Additional native input producers need a contract before isolated delivery
// can promise to avoid generation. Unknown graph branches are never discarded.
const ancillaryClasses = new Set(['VAELoader', 'LoadAudio', 'VHS_LoadAudioUpload', 'MiniMaxH3AudioTracks', 'MiniMaxH3SourceTimeline', 'MiniMaxH3ProjectAssetManager', 'LoadVideo', 'VHS_LoadVideo', 'VHS_LoadVideoFFmpeg']);
export async function prepareDelivery(api, selection, editorialRevision) {
    const response = await api.fetchApi('/minimax_h3_context_loop/delivery/prepare', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({selection, editorial_revision:editorialRevision}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Cannot prepare this saved delivery.');
    if (result.version !== 1 || typeof result.snapshot_json !== 'string' || !/^[0-9a-f]{64}$/.test(result.snapshot_id)
            || result.summary?.run_name !== selection.run_name || result.summary.branch_id !== (selection._branch_id || 'main')
            || result.summary.final_cut_branch_id !== selection.final_cut_branch_id
            || result.summary.editorial_revision !== editorialRevision) throw new Error('H3 returned a different delivery source. Refresh and prepare again.');
    return result;
}
export function deliveryPrompt(serialized, target, snapshotJson, settings = {}) {
    const nodes = serialized?.output, assembly = nodes?.[target];
    if (assembly?.class_type !== 'MiniMaxH3ChainAssemble') throw new Error('The selected native assembly output is unavailable.');
    if (typeof snapshotJson !== 'string' || !snapshotJson) throw new Error('Prepare a saved delivery source first.');
    const allowedSettings = new Set(['audio_source','filename','audio_bitrate','copy_to_output','output_subfolder','blend_schedule','boundary_tone_match','color_stabilization']);
    for (const key of Object.keys(settings)) if (!allowedSettings.has(key) || link(assembly.inputs[key])) throw new Error(`Delivery setting ${key} is unknown or connected. Edit its native source before preparing delivery.`);
    const inputs = {...assembly.inputs, ...settings};
    if (inputs.overwrite_existing) throw new Error('Turn off overwrite_existing before isolated delivery; completed outputs must be preserved.');
    let sourceId = 'h3_saved_delivery'; while (nodes[sourceId]) sourceId += '_';
    inputs.manifest = [sourceId, 0];
    const output = {[target]: {...assembly, inputs}, [sourceId]: {class_type:sourceClass, inputs:{snapshot_json:snapshotJson}}};
    const seen = new Set([target, sourceId]);
    const visit = id => {
        if (seen.has(id)) return; seen.add(id);
        const node = nodes[id];
        if (!node || !ancillaryClasses.has(node.class_type)) throw new Error(`Delivery input #${id} (${node?.class_type || 'missing'}) needs an isolated source adapter. Use a dedicated saved-delivery workflow for this source.`);
        output[id] = node;
        for (const value of Object.values(node.inputs || {})) if (link(value)) visit(value[0]);
    };
    for (const value of Object.values(inputs)) if (link(value)) visit(value[0]);
    return {prompt:{...serialized, output}, target};
}
