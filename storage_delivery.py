"""Accepted generation/ALT manifests; imported delivery files stay immutable.

Each manifest describes a verified contiguous branch prefix. New immutable
delivery documents and their branch-local latest pointer are published together,
without touching generation assignments or legacy manifest paths. ALT acceptance
also publishes the selected branch's final-cut choice in that same commit.
"""
import copy
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project import payload_key
else:
    import storage_state as state
    from storage_project import payload_key

_NAMESPACE = uuid.UUID('8cc977b5-2baa-48b2-a897-b33b853bc8a1')


def delivery_carrier(manifest, plan):
    """Carry only the caller's ownership to downstream nodes, not archives.

    Reading an old manifest grants no authority. An explicitly denied/stale
    caller stays denied/stale; never look up a replacement current owner.
    """
    result = dict(manifest)
    result.pop('_project_ownership', None)
    if '_project_ownership' in plan:
        result['_project_ownership'] = copy.deepcopy(plan['_project_ownership'])
    return result


class RuntimeDelivery:
    def __init__(self, runtime):
        self.runtime = runtime
        self.after_stage = None

    def publish(self, plan, manifest, *, handoff_factory=None, alternate_normalizer=None):
        runtime = self.runtime
        runtime.generation.require_write()
        if handoff_factory is not None:
            if not callable(handoff_factory):
                raise ValueError('Delivery handoff construction requires a host-side callable.')
            runtime.handoffs._require_write()
        if (plan.get('run_name') != runtime.run or plan.get('_branch_id', 'main') != runtime.selected
                or manifest.get('run_name') != runtime.run
                or manifest.get('_branch_id', 'main') != runtime.selected):
            raise ValueError('Delivery Plan and manifest must belong to the selected branch.')
        if ('_project_ownership' in manifest
                and manifest['_project_ownership'] != plan.get('_project_ownership')):
            raise ValueError('Delivery manifest conflicts with its caller ownership proof.')
        manifest = copy.deepcopy(manifest)
        manifest.pop('_project_ownership', None)
        complete = manifest.get('format') == 'h3_chain_manifest_v3'
        alternate = manifest.get('format') == 'h3_chain_alternate_manifest_v1'
        if alternate:
            if not callable(alternate_normalizer) or handoff_factory is not None:
                raise ValueError('Alternate delivery needs its host editorial normalizer, not a next-scene handoff.')
        elif (alternate_normalizer is not None or manifest.get('format') not in
              ('h3_chain_manifest_v3', 'h3_chain_partial_manifest_v3')):
            raise ValueError('Unsupported generation delivery manifest or alternate publisher.')
        segments, shots = manifest.get('segments'), plan.get('shots')
        if not isinstance(segments, list) or not segments or not isinstance(shots, list):
            raise ValueError('Generation delivery requires its Plan and saved scene prefix.')
        if any(not isinstance(s, dict) or type(s.get('delivered_frames')) is not int
               or s['delivered_frames'] < 1 for s in segments):
            raise ValueError('Generation delivery requires positive, exact scene frame counts.')
        count = len(segments)
        descriptor = plan.get('alternate_take') if alternate else None
        if alternate and (not isinstance(descriptor, dict)
                or descriptor.get('enabled', True) is not True
                or type(descriptor.get('scene')) is not int or descriptor['scene'] != count
                or descriptor.get('scene_id') != segments[-1].get('id')
                or descriptor.get('media_mode') != 'picture_only'
                or state._encode(manifest.get('alternate_take')) != state._encode(descriptor)):
            raise ValueError('Alternate delivery differs from its exact picture-only Plan target.')
        if handoff_factory is not None and count >= len(shots):
            raise ValueError('A completed generation cannot create a next-scene handoff.')
        if (count > len(shots) or (complete and count != len(shots))
                or type(manifest.get('clip_count')) is not int or manifest['clip_count'] != count
                or manifest.get('plan_hash') != plan.get('plan_hash')
                or manifest.get('compatibility') != plan.get('compatibility')):
            raise ValueError('Generation delivery does not match its Plan or saved scene count.')
        if not complete and (manifest.get('last_completed_clip') != count
                or manifest.get('planned_clip_count') != len(shots)):
            raise ValueError('Partial delivery must state its saved and planned scene counts.')
        frames = sum(s['delivered_frames'] for s in segments)
        if (type(manifest.get('total_delivered_frames')) is not int
                or manifest['total_delivered_frames'] != frames
                or manifest.get('duration_seconds') != frames/24.0):
            raise ValueError('Generation delivery has an inconsistent frame count.')
        documents = runtime.base.state['documents']
        scopes = {'branch:'+runtime.selected}
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'

        def watch(address):
            if address not in documents:
                raise state.StateConflict('Delivery dependency is not accepted: '+address)
            scopes.add(documents[address]['scope'])
            return runtime.base.read(address)

        def verify_media(saved, key):
            address = runtime.reader.address(saved[key])
            indexed = payload_key(address)
            if indexed in documents:
                record = state._decode(watch(indexed))
                if saved.get(key+'_sha256') != record.get('file', {}).get('sha256'):
                    raise state.StateConflict('Delivery saved checksum differs from its accepted payload: '+key)
                runtime.store.payload_path(runtime.base, address, verify=True)
            elif key == 'prompt_file' and address in documents:
                # Legacy prompt sidecars may be imported as exact text controls.
                if state._hash(watch(address)) != saved.get('prompt_file_sha256'):
                    raise state.StateConflict('Delivery prompt differs from its saved checksum.')
            else:
                raise state.StateConflict('Delivery payload is not accepted: '+address)

        for index, segment in enumerate(segments, 1):
            if type(segment.get('index')) is not int or segment['index'] != index:
                raise ValueError('Generation delivery scene indexes must be contiguous from one.')
            revision = state._token(segment.get('revision'))
            assigned_metadata = state._decode(watch(prefix+'checkpoints/clip_%04d.json' % index))
            assigned = assigned_metadata['segment']
            saved = state._decode(watch('checkpoints/clip_%04d.%s.json' % (index, revision)))['segment']
            if alternate and index == count:
                base_revision = state._token(descriptor.get('base_revision'))
                if (assigned.get('index') != index or assigned.get('revision') != base_revision
                        or saved.get('take_kind') != 'editorial_alternate'
                        or saved.get('alternate_of_revision') != base_revision
                        or saved.get('alternate_media_mode') != 'picture_only'
                        or assigned.get('checkpoint_sha256') != descriptor.get('base_checkpoint_sha256')):
                    raise state.StateConflict('Alternate delivery no longer matches its accepted base checkpoint.')
                base = state._decode(watch('checkpoints/clip_%04d.%s.json' % (index, base_revision)))['segment']
                if state._encode(base) != state._encode(assigned):
                    raise state.StateConflict('Alternate delivery base assignment differs from its immutable take.')
                if any(saved.get(key) != base[key] for key in
                       ('id', 'raw_frames', 'delivered_frames', 'resolution') if key in base):
                    raise ValueError('Alternate delivery must preserve its base identity, duration and canvas.')
                if any(saved.get(key) != shots[index-1].get(key) for key in ('id', 'prompt', 'seed')):
                    raise ValueError('Alternate delivery prompt or seed differs from its queued Plan.')
                # Final-cut alternates retain the original audio. Verify that
                # base media as well as the candidate remains accepted/readable.
                for key in ('checkpoint', 'segment', 'generated_audio'):
                    if base.get(key):
                        verify_media(base, key)
            elif assigned.get('revision') != revision or assigned.get('index') != index:
                raise state.StateConflict('Delivery scene differs from its accepted branch assignment.')
            # Public manifests may add recovered fields absent from old takes,
            # but cannot alter any field that the immutable take actually saved.
            if any(state._encode(value) != state._encode(saved[key])
                   for key, value in segment.items() if key in saved):
                raise state.StateConflict('Delivery scene differs from its immutable saved metadata.')
            if any(segment.get(key) != saved.get(key) for key in
                   ('segment', 'checkpoint', 'generated_audio', 'blend_segment', 'prompt_file', 'revision_metadata')):
                raise state.StateConflict('Delivery cannot introduce media absent from its saved take.')
            for key in ('segment', 'checkpoint', 'generated_audio', 'blend_segment', 'prompt_file'):
                if saved.get(key):
                    verify_media(saved, key)
            for address in (segment.get('archives') or {}).values():
                watch(runtime.reader.address(address))
        for address in (manifest.get('archives') or {}).values():
            watch(runtime.reader.address(address))

        editorial = None
        if alternate:
            editorial_address = prefix+'editorial.json'
            prior = state._decode(watch(editorial_address)) if editorial_address in documents else {}
            # The exact host-provided legacy normalizer retains its historical
            # defaults/validation. Nothing from workflow JSON becomes callable.
            editorial = copy.deepcopy(alternate_normalizer(prior, runtime.run))
            replacement = dict(scene=count, scene_id=descriptor['scene_id'],
                base_revision=descriptor['base_revision'], alternate_revision=segments[-1]['revision'],
                media_mode='picture_only')
            editorial['replacements'] = [item for item in editorial.get('replacements', [])
                if item.get('scene_id') != descriptor['scene_id']] + [replacement]
            editorial['alternate_draft'] = None
            editorial['updated_at'] = str(segments[-1].get('created_at') or '1970-01-01T00:00:00Z')
            editorial['revision'] = uuid.uuid5(_NAMESPACE, 'alternate-cut:'+runtime.run+':'+runtime.selected+
                ':'+runtime.base.reference['sha256']+':'+segments[-1]['revision']).hex
            manifest['editorial_replacement'] = replacement
            manifest['editorial'] = editorial

        # The stored source pin refers to an already accepted root, never to a
        # yet-to-be-created commit. Returned carriers receive the result pin.
        manifest['_storage_pin'] = runtime.pin
        raw = state._encode(manifest)
        digest = state._hash(raw)
        directory = 'deliveries/'+runtime.selected+'/'
        if alternate:
            directory += 'alternates/scene_%04d/' % count
        address = directory+digest+'.json'
        pointer = dict(format='h3_chain_delivery_pointer_v1', manifest=address,
                       sha256=digest, complete=complete, through_scene=count,
                       source_pin=runtime.pin)
        scope = 'exports:'+runtime.selected
        controls = {
            address: dict(data=raw, scope=scope, category='cuts', immutable=True),
            directory+'latest.json': dict(data=state._encode(pointer), scope=scope,
                                          category='cuts', immutable=False),
        }
        if editorial is not None:
            controls[editorial_address] = dict(data=state._encode(editorial),
                scope='branch:'+runtime.selected, category='branches', immutable=False)
        handoff = None
        if handoff_factory is not None:
            with runtime.handoffs.staged(runtime.base) as session:
                handoff = handoff_factory()
                if (not isinstance(handoff, dict) or handoff.get('run_name') != runtime.run
                        or handoff.get('working_branch_id', 'main') != runtime.selected
                        or handoff.get('action') != 'next_scene'
                        or handoff.get('predecessor_scene') != count or handoff.get('scene') != count+1
                        or handoff.get('source_revision') != segments[-1]['revision']
                        or handoff.get('seed') != shots[count]['seed']):
                    raise ValueError('Delivery handoff differs from its accepted scene boundary.')
                target = 'orchestration/'+str(handoff.get('handoff_id'))+'.json'
                runtime.handoffs.contract(target)
                if set(session['changes']) - {target}:
                    raise ValueError('Delivery cannot batch unrelated handoff mutations.')
                runtime.handoffs.claim_dependencies(handoff)
                controls.update(session['changes'])
                scopes.update(session['reads'])
        kind = 'alternate' if alternate else ('requeue' if handoff_factory is not None else 'manifest')
        operation = uuid.uuid5(_NAMESPACE, runtime.run+':'+runtime.selected+':'+kind+':'+digest).hex
        with runtime.generation._guard(plan.get('_project_ownership')):
            if handoff_factory is not None:
                runtime.handoffs._require_write()
            receipt = runtime.store.commit(runtime.base, controls, operation_id=operation,
                                          read_scopes=scopes, after_stage=self.after_stage)
            runtime.record_commit(receipt)
        return dict(manifest=delivery_carrier(manifest, plan), address=address, receipt=receipt, handoff=handoff)
