"""Verified save-to-loop transitions on explicitly hosted storage copies.

A successor pin is accepted only with the scene save's accepted receipt and
its immutable input witness. Generic node inputs still reject mixed roots.
No tensors, prompts or ownership credentials are embedded in the witness;
the exact serializable continuation state is represented by its digest.
"""
import copy

if __package__:
    from . import storage_state as control
else:
    import storage_state as control

FORMAT = 'h3_scene_save_transition_v1'
KEY = '_storage_save'
_STATE_FIELDS = ('index', 'range_start', 'end_clip', 'resumed_from', 'segments',
                 'resume_history_verification_disabled', 'external_context')


def state_digest(value):
    if not isinstance(value, dict) or not isinstance(value.get('plan'), dict):
        raise ValueError('Scene continuation requires its exact input state and Plan.')
    plan = {key: item for key, item in value['plan'].items()
            if key not in ('_storage_pin', '_project_ownership')}
    selected = {key: value[key] for key in _STATE_FIELDS if key in value}
    selected['plan'] = plan
    return control._hash(control._encode(selected))


def save_witness(runtime, value):
    """Freeze the actual saver's inputs before encoding/publication."""
    runtime.check()
    return dict(format=FORMAT, input_pin=runtime.pin,
                state_sha256=state_digest(value),
                ownership_sha256=control._hash(control._encode(
                    value['plan'].get('_project_ownership'))), scene=value['index'])


def witness_address(operation):
    return 'jobs/'+control._token(operation)+'/scene-save.json'


def loop_end_inputs(store, values):
    """Host adapter for the actual Loop End; copy envelopes, retain tensors.

    Even an already-advanced pin must prove the same saved state. Merely being
    an ancestor or the most recent save never establishes this relationship.
    Review decisions need their own accepted witness and are not treated as
    ordinary saves here.
    """
    if isinstance(values.get('segment'), dict) and '_storage_review' in values['segment']:
        if __package__:
            from .storage_review_execution import reviewed_loop_inputs
        else:
            from storage_review_execution import reviewed_loop_inputs
        return reviewed_loop_inputs(store, values)
    if __package__:
        from .storage_runtime import runtime_access
    else:
        from storage_runtime import runtime_access
    state, segment = values.get('state'), values.get('segment')
    if not isinstance(state, dict) or not isinstance(segment, dict):
        raise ValueError('Loop End requires saved segment and state carriers.')
    proof = segment.get(KEY)
    if not isinstance(proof, dict) or set(proof) != {'receipt', 'witness'}:
        raise ValueError('Loop End needs an accepted scene-save transition receipt.')
    if segment.get('_h3_review_decision') is not None:
        raise ValueError('Reviewed loop continuation requires its own accepted transition.')
    pin = segment.get('_storage_pin')
    if not isinstance(pin, dict):
        raise ValueError('Scene-save transition is missing its accepted storage pin.')
    with runtime_access(store, pin=pin, selected=pin.get('branch_id')) as bound:
        accepted = store.committed_snapshot(proof['receipt'])
        if accepted.reference != bound.base.reference:
            raise control.StateConflict('Scene-save receipt and successor pin disagree.')
        address = witness_address(proof['receipt']['operation_id'])
        if proof['witness'] != address:
            raise ValueError('Scene-save witness does not belong to this operation.')
        witness = control._decode(accepted.read(address))
        if witness.get('format') != FORMAT:
            raise ValueError('Unsupported scene-save transition witness.')
        incoming = witness['input_pin']
        if control._encode(incoming) != control._encode(dict(bound.pin, root=incoming.get('root'))):
            raise ValueError('Scene-save transition has a different project, branch or epoch.')
        store.validate_snapshot(control.Snapshot(store.project, incoming['root']), current=accepted)
        plan = state.get('plan')
        if not isinstance(plan, dict) or control._encode(plan.get('_storage_pin')) != control._encode(incoming):
            raise control.StateConflict('Loop state is not the witnessed scene-save input pin.')
        if state_digest(state) != witness['state_sha256']:
            raise control.StateConflict('Loop state differs from the saved prompt, settings, range or lineage.')
        if control._hash(control._encode(plan.get('_project_ownership'))) != witness['ownership_sha256']:
            raise control.StateConflict('Loop state changed the scene-save ownership proof.')
        if type(state.get('index')) is not int or state['index'] != witness['scene']:
            raise ValueError('Loop state has a different saved scene index.')
        metadata_address = 'checkpoints/clip_%04d.%s.json' % (
            state['index'], control._token(segment.get('revision')))
        raw = accepted.read(metadata_address)
        if control._hash(raw) != witness['metadata_sha256']:
            raise control.StateConflict('Scene-save witness and accepted metadata disagree.')
        saved = control._decode(raw)['segment']
        # Only host-added execution fields may differ from the immutable take.
        public = {key: item for key, item in segment.items()
                  if key not in (KEY, '_storage_pin', '_branch_id', 'run_name')}
        expected = {key: item for key, item in saved.items()
                    if key not in ('_storage_pin', '_branch_id', 'run_name')}
        if control._encode(public) != control._encode(expected):
            raise control.StateConflict('Loop End segment differs from its accepted immutable take.')
        for key in ('checkpoint', 'segment', 'generated_audio', 'blend_segment', 'prompt_file'):
            if saved.get(key):
                store.payload_path(accepted, bound.reader.address(saved[key]), verify=True)
        # Additional envelopes cannot be silently healed by this narrow adapter.
        # The normal carrier gate checks these again after the state is copied.
        result = dict(values)
        result['state'] = dict(state, plan=dict(plan, _storage_pin=copy.deepcopy(bound.pin)))
        if '_storage_pin' in state:
            if control._encode(state['_storage_pin']) != control._encode(incoming):
                raise control.StateConflict('Loop state envelope has a different input storage pin.')
            result['state']['_storage_pin'] = copy.deepcopy(bound.pin)
        return result
