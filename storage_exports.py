"""Immutable finished PNG deliveries on explicitly authorized project copies.

The shared encoder produces private files. This port accepts a complete export,
its readable export.json and catalogue entry in one root. It does not implement
incremental VIDEO folder selection, append or deletion; those callers must not
use a finished snapshot as an implicit replacement for their mutable sequence.
"""
import copy
from contextlib import contextmanager
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
    from .branch_scope import current_branch
    from .project_ownership import project_write_guard
    from .storage_layout import OrganizedStorageLayout
    from .storage_processing import ProcessingSourceDependencies
    from .storage_project import _hash_file
    from .storage_resolver import confined
else:
    import storage_state as state
    from branch_scope import current_branch
    from project_ownership import project_write_guard
    from storage_layout import OrganizedStorageLayout
    from storage_processing import ProcessingSourceDependencies
    from storage_project import _hash_file
    from storage_resolver import confined

FORMAT = 'h3_storage_finished_png_v1'
CATALOG = 'h3_storage_export_catalog_v1'


class RuntimeExports:
    def __init__(self, runtime):
        self.runtime, self.after_stage = runtime, None

    def require_write(self):
        runtime = self.runtime
        runtime.check()
        if not runtime.export_writes or runtime.node_export_write.get() is False:
            raise ValueError('Runtime binding is read-only; export writes were not enabled.')
        if current_branch(runtime.run) != runtime.selected:
            raise ValueError('Export publication belongs to a different runtime branch.')

    @contextmanager
    def guard(self, proof):
        self.require_write()
        runtime = self.runtime
        if runtime.has_node_proof and proof != runtime.node_write_proof:
            raise ValueError('Export publication cannot change the node ownership proof.')
        with project_write_guard(runtime.output, runtime.run, proof, 'publish an export'):
            yield


class FinishedPNGExport(ProcessingSourceDependencies):
    def __init__(self, runtime, upscale, incoming, *, operation, label, settings, videos):
        runtime.exports.require_write()
        super().__init__(runtime, upscale)
        self.operation = state._token(operation)
        if (incoming.get('_storage_pin') != runtime.pin
                or incoming.get('run_name') != runtime.run
                or incoming.get('_branch_id', 'main') != runtime.selected):
            raise ValueError('PNG export requires its exact project/branch input pin.')
        self.proof = copy.deepcopy(incoming.get('_project_ownership'))
        self.sources = copy.deepcopy(incoming.get('segments'))
        if (not isinstance(self.sources, list) or not self.sources
                or any(not isinstance(s, dict) or type(s.get('index')) is not int for s in self.sources)):
            raise ValueError('PNG export requires an explicit nonempty saved scene range.')
        indices = [s['index'] for s in self.sources]
        if indices[0] < 1 or indices != list(range(indices[0], indices[-1]+1)):
            raise ValueError('PNG export scenes must be a contiguous selected range.')
        if not isinstance(label, str) or not label.strip() or len(label) > 256:
            raise ValueError('PNG export requires a bounded display label.')
        self.label, self.settings = label, copy.deepcopy(settings)
        if (not isinstance(settings, dict)
                or set(settings) != {'first_frame_number', 'png_bit_depth', 'png_compression', 'embed_workflow'}
                or type(settings['first_frame_number']) is not int or settings['first_frame_number'] < 0
                or type(settings['png_bit_depth']) is not int or settings['png_bit_depth'] not in (8, 16)
                or type(settings['png_compression']) is not int or not 0 <= settings['png_compression'] <= 9
                or type(settings['embed_workflow']) is not bool):
            raise ValueError('Invalid finished PNG export settings.')
        # Validate exact immutable provenance, including ALT picture/base audio
        # and selected DeRoPE ancestry, without acquiring a processing grant.
        for source in self.sources:
            self.verify_source(source)
        self.archives = copy.deepcopy(incoming.get('archives') or {})
        if not isinstance(self.archives, dict):
            raise ValueError('PNG source archives must be an accepted archive mapping.')
        self.archive_tags = {}
        for key, tag in (('api_prompt', 'prompt'), ('workflow', 'workflow'), ('plan', 'h3_plan')):
            value = self.archives.get(key)
            if value is not None:
                address = runtime.reader.address(value)
                descriptor = runtime.base.state['documents'].get(address)
                if descriptor is None:
                    raise ValueError('PNG source archive is not accepted: '+key)
                self.scopes.add(descriptor['scope'])
                self.archive_tags[tag] = runtime.base.read(address).decode('utf-8')
        if __package__:
            from .png_video_export import _source_path
        else:
            from png_video_export import _source_path
        if not isinstance(videos, dict) or set(videos) != set(indices):
            raise ValueError('PNG export requires exactly one file-backed VIDEO per selected scene.')
        self.videos = {index: _source_path(videos[index]) for index in indices}
        identities = {}
        for index, path in self.videos.items():
            digest, stat = _hash_file(path)
            identities[str(index)] = dict(path=str(path.absolute()), sha256=digest, size=stat[2])
        self.prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        self.logical = self.prefix+'frames/'+self.operation
        self.record_address = self.prefix+'export_records/'+self.operation+'.json'
        self.catalog_address = self.prefix+'export_catalog.json'
        self.scope = 'exports:'+self.operation
        self.scopes.add('exports:'+runtime.selected)
        self.directory = 'project/jobs/'+self.operation
        self.budget = runtime.store._marker()[0]['path_budget']
        self.request = dict(format=FORMAT, input_pin=runtime.pin, operation=self.operation,
                            label=label, settings=self.settings, sources=self.sources,
                            ownership=self.proof, videos=identities, archives=self.archives)
        self.request_hash = state._hash(state._encode(self.request))
        with runtime.exports.guard(self.proof):
            state._immutable(runtime.project, self.directory+'/export-request.json',
                             state._encode(self.request), self.budget)

    def _check_videos(self):
        for index, path in self.videos.items():
            digest, stat = _hash_file(path)
            expected = self.request['videos'][str(index)]
            if digest != expected['sha256'] or stat[2] != expected['size']:
                raise state.StateConflict('PNG input VIDEO changed during export.')

    def encode(self, *, workers=0):
        """Use the real bounded-memory encoder; an exact prepared retry skips it."""
        if self.accepted() is not None or self._prepared() is not None:
            return
        if __package__:
            from . import png_video_export as png
        else:
            import png_video_export as png
        chain = self.upscale.chain
        first = self.settings['first_frame_number']
        clips, files = [], {}
        for source in self.sources:
            index = source['index']
            directory = self.scene_directory(index)
            metadata = dict(h3_run_name=self.runtime.run, h3_clip_index=str(index),
                            h3_png_bit_depth=str(self.settings['png_bit_depth']),
                            h3_prompt=str(source.get('prompt') or ''))
            if self.settings['embed_workflow']:
                # Exact archive bytes from this pin; never a resolver fallback
                # that silently drops archived prompts in the new layout.
                metadata.update(self.archive_tags)
            path = self.videos[index]
            encoded = png.encode_scene(chain, path, source['raw_frames'], source['delivered_frames'],
                self.settings, chain._png_export_worker_count(workers), directory, first,
                metadata, png._file_identity(path))
            count = source['delivered_frames']
            clips.append(dict(encoded, index=index, id=source.get('id'),
                source_contract=self.upscale._upscale_source_contract(source),
                source_revision=source.get('revision'), raw_frames=source['raw_frames'],
                delivered_frames=count, trim_frames=source['raw_frames']-count,
                video_sha256=self.request['videos'][str(index)]['sha256'],
                first_frame_number=first, last_frame_number=first+count-1))
            files.update({item['file']:directory/item['file'] for item in encoded['files']})
            first += count
        self._check_videos()
        self.prepare(clips, files)

    def scene_directory(self, index):
        """A fresh private scene buffer; never reuse partially encoded frames."""
        if index not in [s['index'] for s in self.sources]:
            raise ValueError('PNG staging scene is outside the selected range.')
        with self.runtime.exports.guard(self.proof):
            address = self.directory+'/png-'+uuid.uuid4().hex
            OrganizedStorageLayout(str(self.runtime.project), self.budget).check_budget(address+'/frame_99999999.png')
            path = confined(self.runtime.project, address)
            path.mkdir()  # parent exists from the exact request, exclusive buffer
            return path

    def _prepared(self):
        path = confined(self.runtime.project, self.directory+'/export-prepared.json')
        if not path.exists():
            return None
        saved = state._decode(state._read_bytes(path))
        if (set(saved) != {'sha256', 'value'}
                or state._hash(state._encode(saved['value'])) != saved['sha256']
                or saved['value'].get('request_sha256') != self.request_hash):
            raise state.StateConflict('Prepared PNG export differs from its exact request/checksum.')
        return saved['value']

    def prepare(self, clips, files):
        """Bind all encoded bytes before staging any visible delivery files."""
        self._check_videos()
        clips = copy.deepcopy(clips)
        expected, first = {}, self.settings['first_frame_number']
        if not isinstance(clips, list) or len(clips) != len(self.sources):
            raise ValueError('Finished PNG export must cover every selected scene.')
        for source, clip in zip(self.sources, clips):
            count = source['delivered_frames']
            if (not isinstance(clip, dict) or clip.get('index') != source['index']
                    or clip.get('source_contract') != self.upscale._upscale_source_contract(source)
                    or clip.get('source_revision') != source.get('revision')
                    or clip.get('raw_frames') != source['raw_frames']
                    or clip.get('delivered_frames') != count
                    or clip.get('trim_frames') != source['raw_frames']-count
                    or clip.get('first_frame_number') != first
                    or clip.get('last_frame_number') != first+count-1
                    or not isinstance(clip.get('files'), list) or len(clip['files']) != count):
                raise ValueError('PNG scene timing, provenance or numbering differs from its source.')
            for offset, item in enumerate(clip['files']):
                name = 'frame_%08d.png' % (first+offset)
                if (not isinstance(item, dict) or item.get('file') != name
                        or type(item.get('size')) is not int or item['size'] < 1
                        or not re.fullmatch('[0-9a-f]{64}', str(item.get('sha256', '')))):
                    raise ValueError('Invalid PNG frame address or checksum.')
                expected[name] = item
            first += count
        if set(files) != set(expected):
            raise ValueError('PNG payloads must exactly match the numbered index.')
        payloads = {}
        for name, value in files.items():
            path = Path(value)
            try:
                address = path.relative_to(self.runtime.project).as_posix()
            except ValueError:
                raise ValueError('PNG payload is outside its private encoder job.') from None
            if not re.fullmatch(re.escape(self.directory)+r'/png-[0-9a-f]{32}/'+re.escape(name), address):
                raise ValueError('PNG payload is outside its private encoder job.')
            digest, stat = _hash_file(confined(self.runtime.project, address))
            if digest != expected[name]['sha256'] or stat[2] != expected[name]['size']:
                raise state.StateConflict('Encoded PNG changed before preparation: '+name)
            payloads[name] = dict(path=address, sha256=digest, size=stat[2])
        record = dict(format=FORMAT, run_name=self.runtime.run, branch_id=self.runtime.selected,
            export_id=self.operation, label=self.label, settings=self.settings, clips=clips,
            frame_count=first-self.settings['first_frame_number'], complete=True,
            source_pin=self.runtime.pin, sources=self.sources, archives=self.archives)
        index_raw = state._encode(record)
        index_address = self.directory+'/export-index.json'
        saved = dict(request_sha256=self.request_hash, record=record, files=payloads)
        with self.runtime.exports.guard(self.proof):
            state._immutable(self.runtime.project, index_address, index_raw, self.budget)
            saved['files']['export.json'] = dict(path=index_address, sha256=state._hash(index_raw), size=len(index_raw))
            raw = state._encode(saved)
            state._immutable(self.runtime.project, self.directory+'/export-prepared.json',
                             state._encode(dict(value=saved, sha256=state._hash(raw))), self.budget)
        return saved

    def accepted(self):
        runtime = self.runtime
        with runtime.exports.guard(self.proof):
            self._check_videos()
            receipt = runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            snapshot = runtime.store.committed_snapshot(receipt)
            witness = state._decode(snapshot.read(self.record_address))
            if witness.get('request_sha256') != self.request_hash:
                raise state.StateConflict('PNG retry differs from its accepted request.')
            for name, item in witness['files'].items():
                path = runtime.store.payload_path(snapshot, self.logical+'/'+name, verify=True)
                digest, stat = _hash_file(path)
                if digest != item['sha256'] or stat[2] != item['size']:
                    raise state.StateConflict('PNG retry differs from its accepted payload.')
            with state._lock(runtime.project):
                runtime.store._acknowledge_commit()
            runtime.record_commit(receipt)
            return self._result(witness, receipt)

    def publish(self):
        recovered = self.accepted()
        if recovered is not None:
            return recovered
        saved = self._prepared()
        if saved is None:
            raise ValueError('PNG export must be fully prepared before publication.')
        runtime = self.runtime
        layout = OrganizedStorageLayout(str(runtime.project), self.budget)
        directory = layout.export('png', self.operation)
        requests = []
        with runtime.exports.guard(self.proof):
            pass
        # Human/third-party folder readers must not see a finished index while
        # its frames are still absent. The authoritative root is committed later.
        for name, item in sorted(saved['files'].items(), key=lambda item: (item[0] == 'export.json', item[0])):
            if name != 'export.json' and not re.fullmatch(r'frame_[0-9]{8,}\.png', name):
                raise ValueError('Prepared PNG contains an invalid payload name.')
            expected = (self.directory+'/export-index.json' if name == 'export.json' else
                        re.escape(self.directory)+r'/png-[0-9a-f]{32}/'+re.escape(name))
            if (name == 'export.json' and item['path'] != expected) or (
                    name != 'export.json' and not re.fullmatch(expected, item['path'])):
                raise ValueError('Prepared PNG escaped its private encoder job.')
            path = confined(runtime.project, item['path'])
            digest, stat = _hash_file(path)
            if digest != item['sha256'] or stat[2] != item['size']:
                raise state.StateConflict('Prepared PNG file changed: '+name)
            requests.append(dict(address=self.logical+'/'+name, source=path, target=directory+'/'+name,
                scope=self.scope, operation_id=uuid.uuid5(uuid.UUID(self.operation), name).hex))

        def staged_file(receipt):
            name = receipt['record']['address'].rsplit('/',1)[-1]
            item = saved['files'][name]
            if (receipt['record']['file']['sha256'] != item['sha256']
                    or receipt['record']['file']['size'] != item['size']):
                raise state.StateConflict('PNG changed while staging: '+name)
            if runtime.exports.after_stage:
                runtime.exports.after_stage('payload')
        staged = runtime.store.stage_payloads(requests, after_stage=staged_file)
        prior = runtime.base.state['documents'].get(self.catalog_address)
        catalog = (state._decode(runtime.base.read(self.catalog_address)) if prior else
                   dict(format=CATALOG, exports={}))
        if not isinstance(catalog, dict) or catalog.get('format') != CATALOG or not isinstance(catalog.get('exports'), dict):
            raise ValueError('Unsupported export catalogue; existing data was retained.')
        if self.operation in catalog['exports']:
            raise state.StateConflict('Export identity already exists without its matching acceptance.')
        catalog['exports'][self.operation] = dict(kind='png', label=self.label,
            metadata=self.record_address, directory=directory,
            scene_start=self.sources[0]['index'], scene_end=self.sources[-1]['index'],
            frame_count=saved['record']['frame_count'])
        changes = {
            self.record_address: dict(data=state._encode(saved), scope=self.scope, category='cuts', immutable=True),
            self.catalog_address: dict(data=state._encode(catalog), scope='exports:'+runtime.selected,
                                      category='cuts', immutable=False)}
        with runtime.exports.guard(self.proof):
            self._check_videos()
            receipt = runtime.store.commit_artifacts(runtime.base, changes, staged,
                operation_id=self.operation, read_scopes=self.scopes, after_stage=runtime.exports.after_stage)
            runtime.record_commit(receipt)
        return self._result(saved, receipt)

    def _result(self, saved, receipt):
        return dict(directory=str(confined(self.runtime.project,
                    OrganizedStorageLayout(str(self.runtime.project)).export('png', self.operation))),
                    frame_count=saved['record']['frame_count'], metadata=self.record_address,
                    receipt=receipt, storage_pin=self.runtime.output_pin)
