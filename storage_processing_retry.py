"""Durable exact processing-save retries with host-issued operation identities."""
import copy
from pathlib import Path
import re
from types import SimpleNamespace

if __package__:
    from . import storage_state as control
    from . import processing_execution
    from .storage_processing_continuation import FORMAT, witness_address, accepted_save
    from .storage_execution_digest import execution_digest
    from .storage_project_reads import ProjectReadView
    from .storage_resolver import confined
else:
    import storage_state as control
    import processing_execution
    from storage_processing_continuation import FORMAT, witness_address, accepted_save
    from storage_execution_digest import execution_digest
    from storage_project_reads import ProjectReadView
    from storage_resolver import confined


class ProcessingSaveRequest:
    def __init__(self, runtime, operation, inputs, upscale):
        self.runtime, self.operation, self.upscale = runtime, control._token(operation), upscale
        self.inputs = inputs
        incoming = inputs['state']
        if control._encode(incoming.get('_storage_pin')) != control._encode(runtime.pin):
            raise ValueError('Retryable processing save requires its explicit input storage pin.')
        self.proof = copy.deepcopy(incoming.get('_project_ownership',
                                   incoming['source_manifest'].get('_project_ownership')))
        self.scene = incoming['index']
        self.digest = self.input_digest()
        self.directory = 'project/jobs/'+self.operation
        self.budget = runtime.store._marker()[0]['path_budget']
        self.intent = dict(format='h3_processing_save_request_v1', operation=self.operation,
                           input_pin=runtime.pin, inputs_sha256=self.digest)
        with runtime.processing.guard(self.proof):
            control._immutable(runtime.project, self.directory+'/request.json',
                               control._encode(self.intent), self.budget)

    def input_digest(self):
        # Capture exactly what the saver archives. Unused UI bookkeeping is not
        # authority, and opaque non-finite widget sentinels stay serialized.
        documents = processing_execution.capture(self.upscale.chain,
            self.inputs.get('prompt'), self.inputs.get('extra_pnginfo'))
        values = {key:self.inputs.get(key) for key in ('state','images','upscaled_latent','recovered_audio')}
        values['execution_json'] = processing_execution.serialized(documents)
        return execution_digest(values)

    def accepted(self):
        runtime = self.runtime
        with runtime.processing.guard(self.proof):
            receipt = runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            saved = runtime.store.committed_snapshot(receipt)
            witness = control._decode(saved.read(witness_address(self.operation)))
            if (witness.get('format') != FORMAT or witness.get('save_inputs_sha256') != self.digest
                    or control._encode(witness.get('input_pin')) != control._encode(runtime.pin)
                    or type(witness.get('scene')) is not int or witness['scene'] != self.scene):
                raise control.StateConflict('Processing retry differs from its accepted input witness.')
            paths = self.upscale._state_profile_paths(self.inputs['state'], self.scene)
            address = runtime.reader.address(self.upscale.chain._versioned_path(paths['metadata'], self.operation))
            raw = saved.read(address)
            if control._hash(raw) != witness['metadata_sha256']:
                raise control.StateConflict('Processing retry metadata differs from its accepted witness.')
            metadata = control._decode(raw)
            pin = dict(runtime.pin, root=saved.reference)
            publication = dict(metadata=metadata, receipt=receipt, witness=witness_address(self.operation),
                               storage_pin=pin, manifest_path='h3_chains/'+runtime.run+'/'+witness['manifest_address'])
            view = ProjectReadView(runtime.store, base=saved)
            with view.operation():
                accepted_save(SimpleNamespace(store=runtime.store, base=saved, pin=pin,
                    project=runtime.project, reader=view), result_segment(publication))
                saved.read(witness['manifest_address'])
                execution = processing_execution.from_metadata(metadata)
                if metadata['segment'].get('execution_hash'):
                    if not isinstance(execution, dict) or self.upscale.chain._fingerprint(execution) != metadata['segment']['execution_hash']:
                        raise control.StateConflict('Processing retry execution snapshot differs from its hash.')
            with control._lock(runtime.project):
                runtime.store._acknowledge_commit()
            runtime.record_commit(receipt)
            return publication

    def prepare(self, workspace, metadata):
        if self.input_digest() != self.digest:
            raise control.StateConflict('Processing save inputs changed while encoding; no take was accepted.')
        workspace.witness['save_inputs_sha256'] = self.digest
        value = dict(format='h3_processing_save_prepared_v1', request=self.intent,
            metadata=metadata, witness=workspace.witness,
            payloads={role:Path(path).relative_to(self.runtime.project).as_posix()
                      for role,path in workspace.encoded.items()})
        with self.runtime.processing.guard(self.proof):
            control._immutable(self.runtime.project, self.directory+'/prepared.json',
                control._encode(dict(value=value, sha256=control._hash(control._encode(value)))), self.budget)

    def resume(self, workspace):
        path = confined(self.runtime.project, self.directory+'/prepared.json')
        if not path.exists():
            return None
        envelope = control._decode(control._read_bytes(path))
        if (set(envelope) != {'value','sha256'}
                or control._hash(control._encode(envelope['value'])) != envelope['sha256']):
            raise control.StateConflict('Prepared processing record failed its checksum.')
        saved = envelope['value']
        if (saved.get('format') != 'h3_processing_save_prepared_v1'
                or control._encode(saved.get('request')) != control._encode(self.intent)):
            raise control.StateConflict('Prepared processing save differs from its immutable request.')
        witness = dict(workspace.witness, save_inputs_sha256=self.digest)
        if control._encode(witness) != control._encode(saved.get('witness')):
            raise control.StateConflict('Prepared processing continuation differs from its current inputs.')
        names = dict(video='video.mp4', checkpoint='checkpoint.safetensors', prompt='prompt.txt', audio='audio.wav')
        payloads = saved.get('payloads')
        if not isinstance(payloads, dict) or not {'video','checkpoint','prompt'} <= set(payloads) <= set(names):
            raise ValueError('Prepared processing save has invalid encoder roles.')
        for role,address in payloads.items():
            if not isinstance(address, str) or not re.fullmatch(
                    re.escape(self.directory)+r'/encode-[0-9a-f]{32}/'+re.escape(names[role]), address):
                raise ValueError('Prepared processing payload escapes its private encoder job.')
        workspace.encoded = {role:str(confined(self.runtime.project, address)) for role,address in payloads.items()}
        workspace.witness = witness
        return workspace.publish(saved['metadata'], prepare=False)

    def result(self, publication):
        runtime = self.runtime
        segment = result_segment(publication)
        status = 'Recovered exact saved upscale scene %d revision %s; no new take created.' % (
            segment['index'], segment['revision'])
        view = ProjectReadView(runtime.store, base=runtime.accepted)
        with view.operation():
            preview = self.upscale.chain._video_output_item(segment['segment'], rehearsal_view=view)
        return {'ui':dict(text=[status], images=[preview], animated=(True,)), 'result':(segment,status)}


def result_segment(publication):
    return dict(publication['metadata']['segment'], _storage_pin=copy.deepcopy(publication['storage_pin']),
        _processing_save=dict(receipt=copy.deepcopy(publication['receipt']), witness=publication['witness']),
        _processing_manifest=publication['manifest_path'])
