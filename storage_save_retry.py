"""Durable retries of an explicitly identified copied-runtime scene save."""
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace

if __package__:
    from . import storage_state as control
    from .storage_continuation import FORMAT, witness_address
    from .storage_execution_digest import execution_digest
    from .storage_project_reads import ProjectReadView
    from .storage_reference_cache import RuntimeReferenceCache
    from .storage_resolver import confined
else:
    import storage_state as control
    from storage_continuation import FORMAT, witness_address
    from storage_execution_digest import execution_digest
    from storage_project_reads import ProjectReadView
    from storage_reference_cache import RuntimeReferenceCache
    from storage_resolver import confined


class SceneSaveRequest:
    def __init__(self, runtime, operation, inputs):
        self.runtime, self.operation = runtime, control._token(operation)
        plan = inputs['state']['plan']
        if control._encode(plan.get('_storage_pin')) != control._encode(runtime.pin):
            raise ValueError('A retryable scene save requires its explicit input storage pin.')
        self.proof = copy.deepcopy(plan.get('_project_ownership'))
        self.scene = inputs['state']['index']
        self.inputs = inputs
        self.digest = self.input_digest()
        self.directory = 'project/jobs/'+self.operation
        self.intent = dict(format='h3_scene_save_request_v1', operation=self.operation,
                           input_pin=runtime.pin, inputs_sha256=self.digest)
        self.budget = runtime.store._marker()[0]['path_budget']
        with runtime.generation._guard(self.proof):
            control._immutable(runtime.project, self.directory+'/request.json',
                               control._encode(self.intent), self.budget)

    def input_digest(self):
        # ComfyUI's real cache adds is_changed=[NaN] to queued API nodes;
        # canvas widgets can also use non-finite sentinels. These two opaque
        # archive inputs are not storage authority. Preserve their exact JSON
        # meaning as strings, while leaving Plan/state validation strict.
        # Finite inputs retain their old digest so earlier copied jobs retry.
        values = dict(self.inputs)
        opaque = False
        for key in ('prompt', 'extra_pnginfo'):
            try:
                json.dumps(values.get(key), allow_nan=False)
            except ValueError:
                values[key] = json.dumps(values[key], ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':'))
                opaque = True
        return execution_digest(('h3_scene_save_opaque_metadata_v1', values) if opaque else values)

    def accepted(self):
        """Recover only a matching committed witness, never merely a staged job."""
        runtime = self.runtime
        with runtime.generation._guard(self.proof):
            receipt = runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            accepted = runtime.store.committed_snapshot(receipt)
            witness = control._decode(accepted.read(witness_address(self.operation)))
            if (witness.get('format') != FORMAT or witness.get('save_inputs_sha256') != self.digest
                    or control._encode(witness.get('input_pin')) != control._encode(runtime.pin)
                    or type(witness.get('scene')) is not int or witness['scene'] != self.scene):
                raise control.StateConflict('Scene-save retry differs from its accepted input witness.')
            address = 'checkpoints/clip_%04d.%s.json' % (self.scene, self.operation)
            raw = accepted.read(address)
            if control._hash(raw) != witness['metadata_sha256']:
                raise control.StateConflict('Scene-save retry metadata disagrees with its accepted witness.')
            metadata = control._decode(raw)
            segment = metadata['segment']
            view = ProjectReadView(runtime.store, base=accepted)
            with view.operation():
                for key in ('checkpoint', 'segment', 'generated_audio', 'blend_segment', 'prompt_file'):
                    if segment.get(key):
                        path = runtime.store.payload_path(accepted, view.address(segment[key]), verify=True)
                        from hashlib import file_digest
                        with path.open('rb') as handle:
                            if file_digest(handle, 'sha256').hexdigest() != segment[key+'_sha256']:
                                raise control.StateConflict('Saved scene retry checksum differs: '+key)
                for address in metadata['archives'].values():
                    accepted.read(view.address(address))
                if segment.get('reference_cache'):
                    reader = SimpleNamespace(run=runtime.run, store=runtime.store,
                                             reader=view, base=accepted)
                    RuntimeReferenceCache(reader).load(segment['reference_cache'])
            # A successful log publication can have lost its acknowledgement.
            # Complete that acknowledgement under the same writer lock used by
            # normal commits; never write a second acceptance or reset a pointer.
            with control._lock(runtime.project):
                runtime.store._acknowledge_commit()
            runtime.record_commit(receipt)
            return dict(metadata=metadata, storage_pin=runtime.output_pin,
                        transition=dict(receipt=receipt, witness=witness_address(self.operation)))

    def prepared(self):
        path = confined(self.runtime.project, self.directory+'/prepared.json')
        if not path.exists():
            return None
        envelope = control._decode(control._read_bytes(path))
        if (set(envelope) != {'sha256', 'value'}
                or control._hash(control._encode(envelope['value'])) != envelope['sha256']):
            raise control.StateConflict('Prepared scene-save record failed its checksum.')
        saved = envelope['value']
        if (saved.get('format') != 'h3_scene_save_prepared_v1'
                or saved.get('request') != self.intent):
            raise control.StateConflict('Prepared scene save differs from its immutable request.')
        payloads = {}
        for role, address in saved['payloads'].items():
            if not re.fullmatch(re.escape(self.directory)+
                    r'/encode-[0-9a-f]{32}/(?:video\.mp4|checkpoint\.safetensors|prompt\.txt|audio\.wav|overlap\.mp4)', address):
                raise ValueError('Prepared scene payload escapes its private encoder job.')
            payloads[role] = str(confined(self.runtime.project, address))
        return dict(saved, payloads=payloads)

    def prepare(self, workspace, metadata, payloads, archives):
        if self.input_digest() != self.digest:
            raise control.StateConflict('Scene-save inputs changed while encoding; no take was accepted.')
        saved = dict(format='h3_scene_save_prepared_v1', request=self.intent,
            metadata=metadata, payloads={key: Path(value).relative_to(self.runtime.project).as_posix()
                                        for key, value in payloads.items()},
            archives={key: raw.decode('utf-8') for key, raw in archives.items()} if archives is not None else None,
            reference_source=workspace.reference_source)
        with self.runtime.generation._guard(self.proof):
            control._immutable(self.runtime.project, self.directory+'/prepared.json',
                               control._encode(dict(value=saved, sha256=control._hash(control._encode(saved)))), self.budget)

    def resume(self, workspace):
        saved = self.prepared()
        if saved is None:
            return None
        if saved['reference_source'] is not None:
            workspace.adopt_reference(saved['reference_source'], self.scene)
        archives = saved['archives']
        return workspace.publish(saved['metadata'], saved['payloads'],
            {key: raw.encode('utf-8') for key, raw in archives.items()} if archives is not None else None,
            self.proof, prepare=False)

    def result(self, chain, publication, dynprompt, unique_id):
        runtime = self.runtime
        segment = publication['metadata']['segment']
        status = 'Recovered exact saved scene %d revision %s; no new take created.' % (
            segment['index'], segment['revision'])
        ui = {'text': [status]}
        if not chain._has_downstream_review_gate(dynprompt, unique_id):
            view = ProjectReadView(runtime.store, base=runtime.accepted)
            with view.operation():
                ui.update(images=[chain._video_output_item(segment['segment'], rehearsal_view=view)],
                          animated=(True,))
        result = dict(segment, run_name=runtime.run, _branch_id=runtime.selected,
                      _storage_pin=runtime.output_pin, _storage_save=publication['transition'])
        return {'ui': ui, 'result': (result, status)}
