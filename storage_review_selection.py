"""Prepare candidate assignment and recovery controls for one Review commit.

Hydrated tensors and the caller's ownership proof stay in memory. The durable
record binds their digest to accepted metadata/media; retries reload those same
bytes, never whatever happens to be the current checkpoint assignment.
"""
import copy
import json

if __package__:
    from . import storage_state as control
    from .storage_execution_digest import execution_digest
    from .storage_project import payload_key
    from .storage_layout import storage_stage
else:
    import storage_state as control
    from storage_execution_digest import execution_digest
    from storage_project import payload_key
    from storage_layout import storage_stage


def verify_media(runtime, snapshot, segment):
    descriptors = snapshot.state['documents']
    for key in ('checkpoint', 'segment', 'generated_audio', 'blend_segment', 'prompt_file'):
        if not segment.get(key):
            continue
        address = runtime.reader.address(segment[key])
        # Imported prompt sidecars are controls; new saves index them as media.
        if key == 'prompt_file' and address in descriptors and payload_key(address) not in descriptors:
            raw = snapshot.read(address)
            if segment.get(key+'_sha256') and control._hash(raw) != segment[key+'_sha256']:
                raise control.StateConflict('Selected candidate prompt sidecar hash changed.')
        else:
            runtime.store.payload_path(snapshot, address, verify=True)


def output_digest(segment, input_pin):
    value = {key:item for key,item in segment.items()
             if key not in ('_storage_pin', '_storage_review', '_storage_review_cleanup')}
    decision = value.get('_h3_review_decision')
    if isinstance(decision, dict) and decision.get('action') == 'candidate_selected':
        value['_h3_review_decision'] = dict(decision,
            plan=dict(decision['plan'], _storage_pin=copy.deepcopy(input_pin)))
    return execution_digest(value)


def candidate_recovery_controls(runtime, chain, metadata, plan, reads):
    """Exact archived bytes, or the established pre-snapshot Plan fallback."""
    prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
    archives = metadata.get('archives') or {}
    if not isinstance(archives, dict) or set(archives)-{'plan', 'workflow', 'api_prompt'}:
        raise ValueError('Candidate has invalid recovery archive roles.')
    legacy = not archives or all(runtime.reader.address(value) in
        (key+'.json', prefix+key+'.json') for key,value in archives.items())
    if legacy:
        documents = chain._run_archive_documents(plan, rehearsal_view=runtime.reader)
        return {prefix+key+'.json':json.dumps(document, ensure_ascii=False).encode('utf-8')
                for key, document in documents.items()}
    if 'plan' not in archives:
        raise ValueError('Candidate recovery snapshot has no Plan.')
    origin = control._token(metadata['segment']['revision'])
    adoption = metadata.get('adoption')
    if isinstance(adoption, dict) and adoption.get('shared_artifacts') is True:
        origin = control._token(adoption.get('source_revision'))
        if (adoption.get('version') != 1 or adoption.get('source_scene') != metadata['segment']['index']
                or metadata['segment'].get('adopted_from_revision') != origin):
            raise ValueError('Candidate recovery adoption is inconsistent.')
    result = {}
    for key, value in archives.items():
        source = runtime.reader.address(value)
        if source != 'recovery_archives/'+origin+'/'+key+'.json':
            raise ValueError('Candidate recovery archive belongs to a different revision.')
        raw = runtime.base.read(source)
        reads.add(source)
        if not isinstance(json.loads(raw), dict):
            raise ValueError('Candidate recovery archive must be a JSON object.')
        result[prefix+key+'.json'] = raw
    return result


class CandidateSelection:
    def __init__(self, runtime, chain, state, revision):
        self.runtime = runtime
        revision = control._token(revision)
        batch = state.get('candidate_batch')
        if (not isinstance(batch, dict) or batch.get('scene') != state['index']
                or not any(isinstance(item, dict)
                    and isinstance(item.get('segment'), dict)
                    and item['segment'].get('revision') == revision
                    for item in batch.get('candidates', []))):
            raise ValueError('Selected take is not a candidate in this witnessed Review batch.')
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        self.scope = 'branch:'+runtime.selected
        self.controls = {}
        with runtime.reader.track_reads() as reads:
            metadata, self.segment, self.state = chain._prepare_review_candidate(
                state, revision, rehearsal_view=runtime.reader)
            if storage_stage(take_kind=metadata['segment'].get('take_kind')) != 'generation':
                raise ValueError('A picture-only alternate cannot replace a generation candidate.')
            address = 'checkpoints/clip_%04d.%s.json' % (state['index'], revision)
            raw = runtime.base.read(address)
            reads.add(address)
            self._control(prefix+'checkpoints/clip_%04d.json' % state['index'], raw)
            for address, raw in candidate_recovery_controls(runtime, chain, metadata, self.state['plan'], reads).items():
                self._control(address, raw)
        self.dependencies = {address:control._hash(runtime.base.read(address)) for address in sorted(reads)}
        descriptors = runtime.base.state['documents']
        self.scopes = {descriptors[address]['scope'] for address in reads}
        self.scopes.add(self.scope)
        # No tensors, ownership proof or host pin in this serializable output.
        self.serialized = dict(self.segment, _h3_review_decision={
            'action':'candidate_selected', 'revision':revision})
        self.record = dict(revision=revision, dependencies=self.dependencies,
            output_sha256=output_digest(self.segment, runtime.pin),
            controls_sha256={address:control._hash(value['data'])
                             for address,value in self.controls.items()})

    def _control(self, address, raw):
        self.controls[address] = dict(data=raw, scope=self.scope, category='branches', immutable=False)

    def verify(self, snapshot, *, published=False):
        for address, digest in self.dependencies.items():
            if published and address in self.controls:
                continue  # This transaction intentionally replaced that control.
            if control._hash(snapshot.read(address)) != digest:
                raise control.StateConflict('Selected candidate dependency changed: '+address)
        if published:
            for address, value in self.controls.items():
                if snapshot.read(address) != value['data']:
                    raise control.StateConflict('Selected candidate assignment/recovery was not accepted together.')
        # Re-read all media rather than trusting only the payload index hash.
        verify_media(self.runtime, snapshot, self.segment)

    def output(self, pin):
        decision = self.segment['_h3_review_decision']
        return dict(self.segment, _h3_review_decision=dict(decision,
            plan=dict(decision['plan'], _storage_pin=copy.deepcopy(pin))))
