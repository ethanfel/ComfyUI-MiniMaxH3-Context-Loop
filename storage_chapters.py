"""Pinned chapter seals and read-only recovery from accepted logical controls.

Chapter media stays with its immutable takes. A seal, its latest selector and
the exact node-invocation receipt publish together, never through legacy paths.
"""
import copy
from datetime import datetime, timezone
import re

from . import storage_state as state
from .storage_execution_digest import execution_digest
from .storage_processing import ProcessingSourceDependencies
from .storage_resolver import confined
from . import storage_chapter_index as chapter_index

FORMAT = 'h3_storage_chapter_seal_v1'


def identity(document):
    result = copy.deepcopy(document)
    keys = ['sealed_at', 'chapter_manifest_id', 'chapter_manifest_path']
    if result.get('storage_chapter_version') == 1:
        keys += ['_storage_pin', '_project_ownership']
    for key in keys:
        result.pop(key, None)
    return result


def body(document):
    result = identity(document)
    result.pop('_storage_pin', None)
    result.pop('_project_ownership', None)
    return result


class ChapterSnapshots:
    def __init__(self, runtime, chain, upscale):
        self.runtime, self.chain, self.upscale = runtime, chain, upscale
        self.prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'

    def _address(self, document, token):
        return self.prefix+'chapters/'+self.chain._chapter_directory_name(document['chapter'])+'/manifests/'+token+'.json'

    def _retired(self, address):
        retired = address.replace('/manifests/', '/retired_manifests/')
        current = self.runtime.check().state['documents']
        if retired in current:
            raise state.StateConflict('This chapter snapshot was retired; select an updated chapter.')

    def _read(self, snapshot, address):
        pattern = re.escape(self.prefix)+r'chapters/[^/]+/manifests/([0-9a-f]{32})\.json'
        match = re.fullmatch(pattern, address)
        if match is None:
            raise ValueError('Chapter snapshot belongs to another branch or invalid address.')
        self._retired(address)
        document = state._decode(snapshot.read(address))
        if (document.get('format') != self.chain.CHAPTER_MANIFEST_FORMAT
                or document.get('run_name') != self.runtime.run
                or document.get('_branch_id', self.runtime.selected) != self.runtime.selected
                or document.get('chapter_manifest_id') != match[1]
                or self.chain._chapter_manifest_digest(document) != match[1]
                or self._address(document, match[1]) != address
                or document.get('chapter_manifest_path') != 'h3_chains/'+self.runtime.run+'/'+address):
            raise state.StateConflict('Sealed chapter failed its identity check.')
        return document

    def _verify_sources(self, document):
        runtime, chain = self.runtime, self.chain
        dependencies = ProcessingSourceDependencies(runtime, self.upscale)
        with runtime.reader.track_reads() as reads:
            chain._validate_manifest(document, rehearsal_view=runtime.reader)
            # Resolution can reload canonical ALT metadata. Validate supplied
            # inputs first so it cannot hide a forged prompt/seed/artifact.
            for source in document['segments']:
                dependencies.verify_source(source)
            _audio, _pictures, _editorial, sources = chain._checkpoint_export_views(
                document, rehearsal_view=runtime.reader)
            for source in sources:
                dependencies.verify_source(source)
            chain._manifest_media_metadata(document, rehearsal_view=runtime.reader)
        descriptors = runtime.base.state['documents']
        return dependencies.scopes | {descriptors[key]['scope'] for key in reads} | {'branch:'+runtime.selected}

    def _output(self, document, address, snapshot, proof=None):
        # A persisted owner/pin is never authority for the next workflow.
        result = copy.deepcopy(document)
        result.pop('_project_ownership', None)
        result['_storage_pin'] = self.runtime.output_pin
        result['_branch_id'] = self.runtime.selected
        if proof is not None:
            result['_project_ownership'] = copy.deepcopy(proof)
        path = confined(self.runtime.project, snapshot.state['documents'][address]['file']['path'])
        return result, str(path)

    def seal(self, incoming, number, operation):
        runtime, chain = self.runtime, self.chain
        runtime.exports.require_write()
        if operation is None:
            raise ValueError('Chapter sealing requires its exact host operation ID.')
        operation = state._token(operation)
        if (incoming.get('_storage_pin') != runtime.pin or incoming.get('run_name') != runtime.run
                or incoming.get('_branch_id', 'main') != runtime.selected):
            raise ValueError('Chapter sealing requires its exact project/branch input pin.')
        proof = copy.deepcopy(incoming.get('_project_ownership'))
        input_hash = execution_digest(dict(manifest=incoming, chapter_number=number))
        witness = self.prefix+'chapter_seals/'+operation+'.json'
        with runtime.exports.guard(proof):
            receipt = runtime.check().state['operations'].get(operation)
            if receipt is not None:
                accepted = runtime.store.committed_snapshot(receipt)
                record = state._decode(accepted.read(witness))
                if (record.get('format') != FORMAT or record.get('input_sha256') != input_hash):
                    raise state.StateConflict('Chapter seal operation was accepted for different inputs.')
                document = self._read(accepted, record['manifest'])
                if state._hash(accepted.read(record['manifest'])) != record['sha256']:
                    raise state.StateConflict('Chapter seal receipt disagrees with its manifest.')
                self._verify_sources(document)
                with state._lock(runtime.project):
                    runtime.store._acknowledge_commit()
                runtime.record_commit(receipt)
                return self._output(document, record['manifest'], accepted, proof)

        source = copy.deepcopy(incoming)
        source.pop('_storage_pin', None)
        source.pop('_project_ownership', None)
        selected, _ = chain._chapter_manifest_from_manifest(
            source, number, persist=False, rehearsal_view=runtime.reader)
        selected['_branch_id'] = runtime.selected
        documents = runtime.base.state['documents']
        existing = None
        if selected.get('chapter_manifest_id'):
            original_address = self._address(selected, state._token(selected['chapter_manifest_id']))
            self._retired(original_address)
            if original_address in documents:
                original = self._read(runtime.base, original_address)
                if body(original) == body(selected):
                    existing = original
        if existing is None:
            selected = dict(body(selected), storage_chapter_version=1)
            token = chain._chapter_manifest_digest(selected)
            address = self._address(selected, token)
            self._retired(address)
            if address in documents:
                existing = self._read(runtime.base, address)
                if body(existing) != body(selected):
                    raise state.StateConflict('Chapter content identity collision.')
        else:
            token = existing['chapter_manifest_id']
            address = self._address(existing, token)
        scopes = self._verify_sources(existing or selected)
        request_path = 'project/jobs/'+operation+'/chapter-request.json'
        budget = runtime.store._marker()[0]['path_budget']
        with runtime.exports.guard(proof):
            path = confined(runtime.project, request_path)
            request = state._decode(state._read_bytes(path)) if path.exists() else dict(
                format=FORMAT, input_sha256=input_hash, ownership=proof,
                sealed_at=datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z'))
            if (set(request) != {'format','input_sha256','ownership','sealed_at'}
                    or request['format'] != FORMAT or request['input_sha256'] != input_hash
                    or request['ownership'] != proof):
                raise state.StateConflict('Chapter seal operation differs from its prepared request.')
            datetime.fromisoformat(request['sealed_at'].replace('Z', '+00:00'))
            state._immutable(runtime.project, request_path, state._encode(request), budget)
        document = existing or dict(selected, chapter_manifest_id=token,
            chapter_manifest_path='h3_chains/'+runtime.run+'/'+address, sealed_at=request['sealed_at'])
        raw = state._encode(document) if existing is None else runtime.base.read(address)
        def change(data, immutable=True):
            return dict(data=data, scope='branch:'+runtime.selected, category='cuts', immutable=immutable)
        changes = {witness:change(state._encode(dict(format=FORMAT, input_sha256=input_hash,
                                                   manifest=address, sha256=state._hash(raw))))}
        if existing is None:
            changes[address] = change(raw)
            number = document['chapter']['number']
            head = chapter_index.head(self.prefix, number)
            history = chapter_index.history(runtime.reader.read(runtime.project/head), self.prefix, number) if head in documents else []
            history.append(chapter_index.entry(address, raw))
            changes[head] = change(state._encode(chapter_index.record(number, history)), False)
        with runtime.exports.guard(proof):
            receipt = runtime.store.commit_artifacts(runtime.base, changes, [], operation_id=operation,
                read_scopes=scopes, after_stage=runtime.exports.after_stage)
            runtime.record_commit(receipt)
        return self._output(document, address, runtime.accepted, proof)

    def load(self, number, token='', *, proof=None):
        runtime = self.runtime
        number = int(number)
        if not 1 <= number <= self.chain.MAX_SHOTS:
            raise ValueError('Chapter number is outside the supported range.')
        token = str(token or '').strip().lower()
        if token:
            state._token(token)
        documents = runtime.base.state['documents']
        head = self.prefix+'chapter_heads/%04d.json' % number
        if not token and head in documents:
            record = runtime.reader.read(runtime.project/head)
            def read(address):
                if address not in documents:
                    raise FileNotFoundError(address)
                return runtime.base.read(address)
            address, selected = chapter_index.select(record, self.prefix, number, runtime.run, read)
            self._retired(address)
        else:
            # Older imports have no publication selector. Use their saved UTC
            # seal times, never the new blob mtimes or a legacy directory scan.
            candidates = []
            pattern = re.escape(self.prefix)+r'chapters/'+('%02d_' % number)+r'[^/]+/manifests/([0-9a-f]{32})\.json'
            for address in sorted(documents):
                match = re.fullmatch(pattern, address)
                if match is None or (token and match[1] != token):
                    continue
                candidate = self._read(runtime.base, address)
                sealed = datetime.fromisoformat(candidate['sealed_at'].replace('Z', '+00:00'))
                if sealed.tzinfo is None:
                    raise ValueError('Chapter seal time must include its timezone.')
                candidates.append((sealed, address, candidate))
            if not candidates:
                raise FileNotFoundError('No sealed Chapter %d%s exists for this branch.' %
                                        (number, ' snapshot '+token if token else ''))
            candidates.sort(key=lambda row: (row[0], row[1]))
            if len(candidates) > 1 and candidates[-1][0] == candidates[-2][0]:
                raise ValueError('Imported chapter snapshots have the same seal time; choose an exact snapshot ID.')
            _, address, selected = candidates[-1]
        self._verify_sources(selected)
        return self._output(selected, address, runtime.base, proof)
