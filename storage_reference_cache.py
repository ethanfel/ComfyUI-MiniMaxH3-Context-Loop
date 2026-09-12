"""Pinned reference-cache reads and scene-transaction adoption on test copies.

No global bundle is removed. New run-local objects and their manifest become
visible together with the scene that references them, never in a prior commit.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project import payload_key
    from .storage_layout import OrganizedStorageLayout
    from .storage_resolver import confined, artifact_address
    from .reference_cache_store import FORMAT, objects_digest, tensor_digest
else:
    import storage_state as state
    from storage_project import payload_key
    from storage_layout import OrganizedStorageLayout
    from storage_resolver import confined, artifact_address
    from reference_cache_store import FORMAT, objects_digest, tensor_digest


def descriptor(metadata):
    if not isinstance(metadata, dict) or metadata.get('format') not in (
            FORMAT, 'h3_reference_cache_v1', 'h3_reference_cache_v2'):
        raise ValueError('Invalid reference cache format.')
    fields = ('format', 'signature', 'reference_fingerprint', 'metadata', 'tensors_sha256')
    if metadata['format'] != FORMAT:
        fields += ('tensors',)
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in fields):
        raise ValueError('Invalid reference cache descriptor.')
    for key in ('signature', 'tensors_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', metadata[key]):
            raise ValueError('Invalid reference cache checksum identity.')
    return {key: metadata[key] for key in fields}


class PinnedTensorStore:
    def __init__(self, runtime, metadata):
        self.runtime = runtime
        self.directory = str(Path(metadata['metadata']).parent)+'/objects/'

    def verify(self, record):
        objects_digest({'object':record})
        expected = self.directory+record['tensor_sha256']+'.safetensors'
        if record['tensors'] != expected:
            raise ValueError('H3 reference tensor object is outside its cache store.')
        address = self.runtime.reader.address(expected)
        path = self.runtime.store.payload_path(self.runtime.base, address, verify=True)
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() != record['tensors_sha256']:
                raise ValueError('H3 reference tensor object failed SHA-256 integrity checks.')
        return str(path)

    def load(self, record):
        from safetensors.torch import load_file
        tensors = load_file(self.verify(record))
        if set(tensors) != {'data'} or tensor_digest(tensors['data']) != record['tensor_sha256']:
            raise ValueError('H3 reference tensor object content failed integrity checks.')
        return tensors['data']


class RuntimeReferenceCache:
    def __init__(self, runtime):
        self.runtime = runtime

    def owns(self, metadata):
        value = metadata.get('metadata') if isinstance(metadata, dict) else None
        return isinstance(value, str) and value.startswith('h3_chains/'+self.runtime.run+'/reference_cache/')

    def load(self, value):
        wanted = descriptor(value)
        if not self.owns(wanted):
            raise ValueError('Reference cache does not belong to this pinned project.')
        metadata = self.runtime.reader.read(wanted['metadata'])
        if descriptor(metadata) != wanted:
            raise ValueError('H3 checkpoint reference-cache descriptor does not match accepted storage.')
        if metadata['format'] == FORMAT:
            if objects_digest(metadata.get('tensor_objects')) != metadata['tensors_sha256']:
                raise ValueError('H3 reference object manifest failed SHA-256 integrity checks.')
            store = PinnedTensorStore(self.runtime, metadata)
            for record in metadata['tensor_objects'].values():
                store.verify(record)
        else:
            address = self.runtime.reader.address(metadata['tensors'])
            path = self.runtime.store.payload_path(self.runtime.base, address, verify=True)
            with path.open('rb') as handle:
                if hashlib.file_digest(handle, 'sha256').hexdigest() != metadata['tensors_sha256']:
                    raise ValueError('H3 checkpoint reference-cache tensors failed integrity checks.')
        return metadata

    def prepare(self, metadata, scene, operation):
        wanted = descriptor(metadata)
        runtime = self.runtime
        if type(metadata.get('scene')) is not int or metadata['scene'] != scene:
            raise ValueError('Reference cache belongs to a different generated scene.')
        if self.owns(wanted):
            source = self.load(wanted)
            if source != metadata:
                raise ValueError('Reference source differs from its accepted manifest.')
            local = copy.deepcopy(source)
            source_path = lambda value: runtime.reader.path(value)
        else:
            address = artifact_address(wanted['metadata'])
            if not address.startswith('h3_reference_cache/'):
                raise ValueError('Reference adoption requires the global cache or this accepted project.')
            source = state._decode(confined(runtime.output, address).read_bytes())
            if source != metadata:
                raise ValueError('Reference source differs from its persisted manifest.')
            local = copy.deepcopy(source)
            suffix = '.converted.json' if source.get('legacy_conversion') else '.json'
            local.update(run_name=runtime.run, metadata='h3_chains/'+runtime.run+
                         '/reference_cache/scene_%04d.%s%s' % (scene, wanted['signature'][:24], suffix))
            def source_path(value):
                address = artifact_address(value)
                if not address.startswith('h3_reference_cache/'):
                    raise ValueError('Global reference payload escapes its cache.')
                return confined(runtime.output, address)
        files = {}
        local_root = str(Path(local['metadata']).parent)
        if source['format'] == FORMAT:
            if objects_digest(source.get('tensor_objects')) != source['tensors_sha256']:
                raise ValueError('H3 reference object manifest failed SHA-256 integrity checks.')
            source_root = str(Path(source['metadata']).parent)
            if str(Path(source_root).parent) == 'h3_reference_cache':
                source_root = 'h3_reference_cache'
            for key, record in source['tensor_objects'].items():
                if record['tensors'] != source_root+'/objects/'+record['tensor_sha256']+'.safetensors':
                    raise ValueError('Reference tensor object is outside its cache store.')
                address = local_root+'/objects/'+record['tensor_sha256']+'.safetensors'
                files[address] = (source_path(record['tensors']), record['tensors_sha256'], record['tensor_sha256'])
                local['tensor_objects'][key] = {**record, 'tensors':address}
        else:
            local['tensors'] = local['metadata'].removesuffix('.json')+'.safetensors'
            files[local['tensors']] = (source_path(source['tensors']), source['tensors_sha256'], source['tensors_sha256'])
        return ReferenceAdoption(runtime, local, files, operation)


class ReferenceAdoption:
    def __init__(self, runtime, metadata, files, operation):
        self.runtime, self.metadata, self.files = runtime, metadata, files
        self.operation = state._token(operation)

    def stage(self, saved_descriptor, controls, staged, scopes, after_stage):
        runtime = self.runtime
        runtime.generation.require_write()
        if descriptor(self.metadata) != saved_descriptor:
            raise ValueError('Saved scene reference descriptor differs from its adopted cache.')
        layout = OrganizedStorageLayout(str(runtime.project))
        for logical, (source, digest, identity) in sorted(self.files.items()):
            address = runtime.reader.address(logical)
            key = payload_key(address)
            scope = 'cache:'+identity
            scopes.add(scope)
            if key in runtime.base.state['documents']:
                scopes.add(runtime.base.state['documents'][key]['scope'])
                record = state._decode(runtime.base.read(key))
                runtime.store.payload_path(runtime.base, address, verify=True)
                if record['file']['sha256'] != digest:
                    raise ValueError('Existing reference object differs from its expected bytes.')
                continue
            target = layout.project_data('reference_cache', 'objects',
                uuid.uuid5(uuid.NAMESPACE_URL, 'h3:reference:'+address).hex+'.safetensors')
            receipt = runtime.store.stage_payload(address, source, target, scope=scope,
                operation_id=uuid.uuid5(uuid.UUID(self.operation), 'cache:'+address).hex)
            if receipt['record']['file']['sha256'] != digest:
                raise ValueError('Copied reference object failed SHA-256 integrity checks.')
            staged.append(receipt)
            if after_stage:
                after_stage('reference_payload')
        address = runtime.reader.address(self.metadata['metadata'])
        scope = 'cache:'+self.metadata['signature']
        scopes.add(scope)
        if address in runtime.base.state['documents']:
            scopes.add(runtime.base.state['documents'][address]['scope'])
            if state._decode(runtime.base.read(address)) != self.metadata:
                raise state.StateConflict('A different reference manifest occupies this cache identity.')
        else:
            controls[address] = dict(data=state._encode(self.metadata), scope=scope,
                                     category='reference_cache', immutable=True)
