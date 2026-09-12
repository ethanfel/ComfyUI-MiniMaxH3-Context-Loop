"""Receipt-verified processing Save → Handoff → Advance transitions."""
import copy

if __package__:
    from . import storage_state as control
    from .storage_execution_digest import execution_digest
else:
    import storage_state as control
    from storage_execution_digest import execution_digest

FORMAT = 'h3_processing_save_transition_v1'
KEY = '_processing_save'
FIELDS = ('run_name', 'profile', 'profile_config', 'source_manifest_hash', 'index',
          'segments', 'end_clip', 'range_start', 'start_mode')


def source_document(source):
    return {key:value for key,value in source.items()
            if key not in ('_storage_pin', '_branch_id', '_project_ownership')}


def state_digest(incoming):
    selected = {key:incoming[key] for key in FIELDS if key in incoming}
    selected['source_manifest'] = source_document(incoming['source_manifest'])
    return control._hash(control._encode(selected))


def ownership_proof(incoming, witness):
    source = incoming['source_manifest']
    proof = incoming.get('_project_ownership', source.get('_project_ownership'))
    if (control._hash(control._encode(proof)) != witness['ownership_sha256'] or
            ('_project_ownership' in incoming and '_project_ownership' in source
             and incoming['_project_ownership'] != source['_project_ownership'])):
        raise control.StateConflict('Processing state changed its ownership proof.')
    return proof


def witness_address(operation):
    return 'jobs/'+control._token(operation)+'/processing-save.json'


def save_witness(runtime, incoming, images, latent, upscale):
    source = upscale._source_segment(incoming)
    raw, delivered = int(source['raw_frames']), int(source['delivered_frames'])
    pixels = images[raw-delivered:raw]
    length = min(int(incoming['source_manifest'].get('compatibility', {}).get('context_length', 0)), delivered)
    previous_frames = pixels[-length:] if length else pixels[:0]
    return dict(format=FORMAT, input_pin=runtime.pin,
        state_sha256=state_digest(incoming), scene=incoming['index'],
        ownership_sha256=control._hash(control._encode(incoming.get('_project_ownership',
            incoming['source_manifest'].get('_project_ownership')))),
        tensors_sha256=execution_digest(dict(images=images, upscaled_latent=latent)),
        context_sha256=execution_digest(dict(previous_frames=previous_frames,
                                             previous_latent=context_latent(latent, upscale))))


def context_latent(latent, upscale):
    """Same normalization as the handoff, without cloning the large HQ tensor."""
    if latent is None:
        return None
    samples = latent.get('samples') if isinstance(latent, dict) else None
    if samples is None:
        raise ValueError('Upscale latent has no samples value.')
    streams = [samples] if upscale.chain.torch.is_tensor(samples) else upscale.chain._streams_from_latent(latent)
    return {'samples': upscale._packed_samples(streams)}


def accepted_save(bound, segment):
    proof = segment.get(KEY)
    if not isinstance(proof, dict) or set(proof) != {'receipt', 'witness'}:
        raise ValueError('Processing continuation needs an accepted save receipt and witness.')
    accepted = bound.store.committed_snapshot(proof['receipt'])
    if accepted.reference != bound.base.reference or segment.get('_storage_pin') != bound.pin:
        raise control.StateConflict('Processing receipt differs from its accepted successor pin.')
    address = witness_address(proof['receipt']['operation_id'])
    if proof['witness'] != address:
        raise ValueError('Processing witness belongs to a different operation.')
    witness = control._decode(accepted.read(address))
    if witness.get('format') != FORMAT:
        raise ValueError('Unknown processing continuation witness.')
    incoming = witness['input_pin']
    if control._encode(incoming) != control._encode(dict(bound.pin, root=incoming.get('root'))):
        raise ValueError('Processing input belongs to a different branch, project or epoch.')
    bound.store.validate_snapshot(control.Snapshot(bound.project, incoming['root']), current=accepted)
    raw = accepted.read(bound.reader.address(segment['revision_metadata']))
    if control._hash(raw) != witness['metadata_sha256']:
        raise ValueError('Processing continuation metadata differs from its accepted witness.')
    saved = control._decode(raw)['segment']
    public = {key:value for key,value in segment.items() if not key.startswith('_')}
    if control._encode(public) != control._encode(saved):
        raise ValueError('Processing continuation segment differs from its immutable take.')
    for key in ('segment', 'checkpoint', 'generated_audio', 'prompt_file'):
        if saved.get(key):
            bound.store.payload_path(accepted, bound.reader.address(saved[key]), verify=True)
    return witness


def loop_end_inputs(store, values):
    """Lazy End expands links; only its resolved Handoff consumes the data.

    ComfyUI can call End with absent *or cached* lazy inputs. Neither is used
    by End's graph path, so do not bind a storage root from those placeholders.
    The expanded Handoff must separately receive the strict loop_inputs grant.
    Direct Python End calls still require the complete accepted transition.
    """
    dynprompt, unique_id = values.get('dynprompt'), values.get('unique_id')
    if dynprompt is None or unique_id is None:
        return loop_inputs(store, values)
    if dynprompt.get_node(str(unique_id)).get('class_type') != 'MiniMaxH3ChainUpscaleLoopEnd':
        raise ValueError('Lazy processing boundary is not an Upscale Loop End.')
    return {key: None if key in ('state', 'images', 'segment', 'upscaled_latent') else value
            for key, value in values.items()}


def loop_inputs(store, values):
    """Explicit host adapter only; generic mixed-root execution stays forbidden."""
    if __package__:
        from .storage_runtime import runtime_access
    else:
        from storage_runtime import runtime_access
    incoming, segment = values.get('state'), values.get('segment')
    if not isinstance(incoming, dict) or not isinstance(segment, dict):
        raise ValueError('Processing continuation requires state and saved segment.')
    if segment.get('_h3_upscale_decision') is not None:
        raise ValueError('Processing review decisions require their own accepted transition.')
    pin = segment.get('_storage_pin')
    if not isinstance(pin, dict):
        raise ValueError('Processing continuation needs its accepted storage pin.')
    with runtime_access(store, pin=pin, selected=pin.get('branch_id')) as bound:
        witness = accepted_save(bound, segment)
        original = witness['input_pin']
        if (control._encode(incoming.get('_storage_pin')) != control._encode(original)
                or control._encode(incoming['source_manifest'].get('_storage_pin')) != control._encode(original)):
            raise control.StateConflict('Processing state is not the witnessed save input pin.')
        if state_digest(incoming) != witness['state_sha256']:
            raise control.StateConflict('Processing state differs from the accepted source, settings or prefix.')
        ownership_proof(incoming, witness)
        tensors = dict(images=values.get('images'), upscaled_latent=values.get('upscaled_latent'))
        if execution_digest(tensors) != witness['tensors_sha256']:
            raise control.StateConflict('Processing continuation tensors differ from the saved encoder inputs.')
        result = dict(values)
        result['state'] = dict(incoming, _storage_pin=copy.deepcopy(pin),
            source_manifest=dict(incoming['source_manifest'], _storage_pin=copy.deepcopy(pin)))
        return result


def delivery(bound, next_state, upscale):
    """Read the saver's delivery snapshot; never rewrite a legacy manifest."""
    transition = next_state.get('_processing_continuation')
    if not isinstance(transition, dict):
        raise ValueError('Upscale continuation needs its verified processing save.')
    witness = accepted_save(bound, transition)
    index = next_state.get('index')
    if type(index) is not int or index != witness['scene']+1:
        raise ValueError('Processing continuation has a different next scene index.')
    previous = dict(next_state, index=index-1, segments=next_state['segments'][:-1])
    if state_digest(previous) != witness['state_sha256']:
        raise control.StateConflict('Processing continuation changed the saved source, range or prefix.')
    proof = ownership_proof(next_state, witness)
    context = {key:next_state.get(key) for key in ('previous_frames','previous_latent')}
    if execution_digest(context) != witness['context_sha256']:
        raise control.StateConflict('Processing continuation changed the saved HQ context.')
    manifest = bound.reader.read('h3_chains/'+bound.run+'/'+witness['manifest_address'])
    expected = upscale._upscale_manifest(previous, next_state['segments'],
        index-1 == upscale._source_bounds(previous['source_manifest'])[1])
    expected['source_manifest'] = source_document(expected['source_manifest'])
    if control._encode(expected) != control._encode(manifest):
        raise control.StateConflict('Processing continuation differs from its accepted delivery.')
    # Persistence intentionally strips session authority. The next node still
    # needs the caller's receipt-verified proof (not current-owner credentials
    # read from disk). Add it only to this returned envelope, never the archive.
    return dict(manifest, _project_ownership=copy.deepcopy(proof))
