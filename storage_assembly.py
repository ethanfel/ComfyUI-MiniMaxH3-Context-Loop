"""Final assembly in a private encoder job, accepted with all its sidecars.

The ordinary renderer owns editorial, sound, blend and codec behavior. This
workspace owns destinations, exact retries and one immutable-root publication.
No accepted media is replaced and no preview exposes a private encoder file.
"""
import copy
from pathlib import Path
import re
import uuid

from . import storage_state as state
from .storage_execution_digest import execution_digest
from .storage_layout import OrganizedStorageLayout
from .storage_processing import ProcessingSourceDependencies
from .storage_project import _hash_file, payload_catalog
from .storage_resolver import confined
from .storage_export_names import reserve_base, pending_legacy

FORMAT = 'h3_storage_assembly_v1'
FILES = dict(video='video.mp4', audio='audio.wav', subtitles='subtitles.srt', metadata='video.json')
SUFFIXES = dict(video='.mp4', audio='.generated.wav', subtitles='.srt', metadata='.json')


class AssemblyExport(ProcessingSourceDependencies):
    def __init__(self, runtime, upscale, manifest, *, operation, **settings):
        runtime.exports.require_write()
        super().__init__(runtime, upscale)
        self.chain, self.operation = upscale.chain, state._token(operation)
        if (manifest.get('_storage_pin') != runtime.pin or manifest.get('run_name') != runtime.run
                or manifest.get('_branch_id', 'main') != runtime.selected):
            raise ValueError('Assembly requires its exact project/branch input pin.')
        self.manifest = copy.deepcopy(manifest)
        self.proof = copy.deepcopy(manifest.get('_project_ownership',
                                  manifest.get('source_manifest', {}).get('_project_ownership')))
        self.settings = dict(settings)  # Never clone multi-GiB AUDIO/VAE objects.
        self.input_hash = self._inputs()
        self.job = 'project/jobs/'+self.operation
        self.branch = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        self.witness = self.branch+'export_records/'+self.operation+'.json'
        self.budget = runtime.store._marker()[0]['path_budget']
        self.documents = runtime.base.state['documents']
        self.payloads = payload_catalog(runtime.base)
        self.layout = OrganizedStorageLayout(str(runtime.project), self.budget)
        self.scopes.add('branch:'+runtime.selected)
        # Freeze date expansion once per operation, including a retry after midnight.
        request_path = confined(runtime.project, self.job+'/assembly-request.json')
        self.request = state._decode(state._read_bytes(request_path)) if request_path.exists() else dict(
            format=FORMAT, operation=self.operation, input_sha256=self.input_hash,
            ownership=self.proof, final_name=self.chain._safe_name(
                self.chain._expand_filename_date(settings['filename']), 'final'),
            **({'copy_subfolder':self.chain._safe_output_subfolder(settings.get('output_subfolder','')).replace('\\','/')}
               if settings.get('copy_to_output') else {}))
        if (self.request.get('format') != FORMAT or self.request.get('operation') != self.operation
                or self.request.get('input_sha256') != self.input_hash or self.request.get('ownership') != self.proof
                or self.chain._safe_name(self.request.get('final_name'), 'final') != self.request.get('final_name')):
            raise state.StateConflict('Assembly operation differs from its exact input request.')
        if settings.get('copy_to_output'):
            from .storage_output_copy import validate_folder
            validate_folder(runtime.output,self.request.get('copy_subfolder'),self.budget)
        self.final_name = self.request['final_name']
        source = manifest.get('source_manifest') if str(manifest.get('format', '')).startswith('h3_chain_upscale_') else manifest
        folder = self.branch
        if isinstance(source.get('chapter'), dict):
            folder += 'chapters/'+self.chain._chapter_directory_name(source['chapter'])+'/'
        if source is not manifest:
            profile = manifest.get('profile')
            if self.chain._safe_name(profile, '') != profile or not profile:
                raise ValueError('Assembly profile is not a safe logical name.')
            folder += 'upscaled/'+profile+'/'
        self.final_directory = str(runtime.project/(folder+'final'))
        self.expected_base = folder+'final/'+self.final_name
        self.request_hash = state._hash(state._encode(self.request))
        with runtime.exports.guard(self.proof):
            state._immutable(runtime.project, self.job+'/assembly-request.json', state._encode(self.request), self.budget)

    def _inputs(self):
        values = {k:v for k,v in self.settings.items() if k != 'blend_video_vae'}
        vae = self.settings.get('blend_video_vae')
        values['blend_video_vae'] = None if vae is None else type(vae).__module__+'.'+type(vae).__qualname__
        return execution_digest(dict(manifest=self.manifest, settings=values))

    def _sources(self):
        manifest = self.manifest
        if str(manifest.get('format', '')).startswith('h3_chain_upscale_'):
            sources = manifest['source_manifest']['segments']
            self.audio_segments = manifest['segments']
            for segment in manifest['segments']:
                metadata = self.read(segment['revision_metadata'], immutable=True)
                if self.upscale._public_upscale_segment(metadata['segment']) != self.upscale._public_upscale_segment(segment):
                    raise ValueError('Assembly processing source differs from its immutable metadata.')
                if metadata.get('profile_config_hash') != manifest['profile_config']['config_hash']:
                    raise ValueError('Assembly processing profile differs from saved settings.')
                source = next(s for s in sources if s['index'] == segment['index'])
                self.upscale._verify_upscale_source(metadata, source, segment['index'])
                self.verify_files(segment)
        else:
            self.audio_segments, _pictures, _editorial, sources = self.chain._checkpoint_export_views(
                manifest, rehearsal_view=self.runtime.reader)
        for source in sources:
            self.verify_source(source)

    def _prepared(self):
        path = confined(self.runtime.project, self.job+'/assembly-prepared.json')
        if not path.exists():
            return None
        saved = state._decode(state._read_bytes(path))
        if (set(saved) != {'value', 'sha256'} or state._hash(state._encode(saved['value'])) != saved['sha256']
                or saved['value'].get('request_sha256') != self.request_hash):
            raise state.StateConflict('Prepared assembly differs from its exact request.')
        return saved['value']

    def prepare(self, node):
        if self.accepted() is not None or self._prepared() is not None:
            return
        with self.runtime.reader.track_reads() as dependencies:
            self._sources()
            with self.runtime.exports.guard(self.proof):
                for attempt in range(1,10001):
                    self.buffer = self.job+'/assemble%04d' % attempt
                    self.layout.check_budget(self.buffer+'/.metadata.tmp.txt')
                    directory = confined(self.runtime.project, self.buffer)
                    try:
                        directory.mkdir()
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ValueError('Too many interrupted private assembly attempts.')
            self.render_directory = str(directory)
            self.dependencies = dependencies
            node._assemble(self.manifest, **dict(self.settings,copy_to_output=False), _workspace=self)

    def _selection(self, base, roles):
        # A sidecar collision occupies the whole family, not only the MP4 name.
        for ordinal in range(10001):
            stem = base+('_%03d' % ordinal if ordinal else '')
            paths = {key:stem+suffix for key,suffix in SUFFIXES.items()}
            occupied = [self.documents.get(p) or self.payloads.get(p) for p in paths.values()]
            if any(occupied):
                if not self.settings.get('overwrite_existing') or ordinal:
                    continue
                if any(d and d['immutable'] for d in occupied):
                    continue  # An imported immutable export always keeps its name.
                # Don't silently leave an older WAV/SRT attached to a new MP4.
                if any(d and role not in roles for role,d in zip(paths, occupied)):
                    continue
            return {role:paths[role] for role in roles}
        raise ValueError('No unused final assembly filename is available.')

    def reserve(self, outputs):
        if not {'video'} <= set(outputs) <= set(FILES):
            raise ValueError('Invalid assembly output roles.')
        video = self.runtime.reader.address(outputs['video'])
        if not video.startswith(self.branch) or not video.endswith('/'+self.final_name+'.mp4'):
            raise ValueError('Assembly destination differs from its selected branch/name.')
        # Only the ordinary renderer's final/profile folder can own this family.
        folder = video.rsplit('/',1)[0]
        pattern = re.escape(self.branch)+r'(?:chapters/[^/]+/)?(?:upscaled/[^/]+/)?final'
        if re.fullmatch(pattern, folder) is None:
            raise ValueError('Assembly destination is not a branch final folder.')
        self.base = video[:-4]
        if self.base != self.expected_base:
            raise ValueError('Assembly destination differs from its source profile/chapter.')
        self.logical = self._selection(self.base, outputs)
        self.scope = 'exports:assembly_'+state._hash(folder.encode())[:32]
        self.scopes.add(self.scope)
        self.output_files = {role:str(Path(self.render_directory)/FILES[role]) for role in outputs}
        return dict(self.output_files)

    def logical_file(self, path):
        role = next((role for role,value in self.output_files.items() if value == str(path)), None)
        if role is None:
            raise ValueError('Assembly metadata refers outside its private outputs.')
        return 'h3_chains/'+self.runtime.run+'/'+self.logical[role]

    def write_text(self, path, text):
        if str(path) not in (*self.output_files.values(), str(Path(self.render_directory)/'.metadata.tmp.txt')):
            raise ValueError('Assembly text write escaped its private job.')
        with self.runtime.exports.guard(self.proof):
            relative = Path(path).relative_to(self.runtime.project).as_posix()
            state._immutable(self.runtime.project, relative, text.encode('utf-8'), self.budget)

    def write_json(self, path, value):
        self.write_text(path, state._encode(value).decode('utf-8'))

    def _verify_dependencies(self, keys):
        for key in keys:
            descriptor = self.documents.get(key)
            if descriptor is None:
                raise state.StateConflict('Assembly dependency is not accepted: '+key)
            self.scopes.add(descriptor['scope'])
            raw = self.runtime.base.read(key)
            value = state._decode(raw) if key.startswith('__storage__/') else None
            if value and value.get('address'):
                self.runtime.store.payload_path(self.runtime.base, value['address'], verify=True)

    def finish(self, outputs, manifest, status, frame_count):
        if outputs != self.output_files or self.input_hash != self._inputs():
            raise state.StateConflict('Assembly inputs changed during rendering.')
        files = {}
        for role, name in outputs.items():
            path = Path(name)
            digest, signature = _hash_file(path)
            files[role] = dict(path=path.relative_to(self.runtime.project).as_posix(), sha256=digest, size=signature[2])
        saved = dict(format=FORMAT, request_sha256=self.request_hash, base=self.base, logical=self.logical,
            files=files, dependencies=sorted(self.dependencies), frame_count=frame_count, status=status,
            manifest=copy.deepcopy(manifest), scope=self.scope)
        self._validate(saved)
        with self.runtime.exports.guard(self.proof):
            state._immutable(self.runtime.project, self.job+'/assembly-prepared.json',
                state._encode(dict(value=saved, sha256=state._hash(state._encode(saved)))), self.budget)

    def _validate(self, saved):
        if (saved.get('format') != FORMAT or saved.get('request_sha256') != self.request_hash
                or saved.get('base') != self.expected_base
                or self.input_hash != self._inputs() or not {'video'} <= set(saved.get('files',{})) <= set(FILES)
                or saved.get('logical') != self._selection(saved['base'], saved['files'])
                or type(saved.get('frame_count')) is not int or saved['frame_count'] < 1):
            raise state.StateConflict('Prepared assembly identity/selection changed.')
        # Rebuild the renderer's metadata-only view from the pinned input.
        # Prepared JSON is checksummed, not trusted authority: a recomputed
        # checksum must not turn a changed prompt, duration or project into an
        # accepted export. No video encoding or VAE decoding happens here.
        expected = copy.deepcopy(self.manifest)
        if str(expected.get('format', '')).startswith('h3_chain_upscale_'):
            expected = self.upscale._assembly_manifest(expected, expected['segments'])
        geometry = self.chain.common_saved_resolution(expected['segments'], 'H3 Chain Assemble')
        if geometry:
            expected['compatibility'] = dict(expected.get('compatibility') or {}, **geometry)
        if saved.get('manifest') != expected:
            raise state.StateConflict('Prepared assembly manifest/provenance changed.')
        with self.runtime.reader.track_reads() as required:
            self.chain._validate_manifest(expected, rehearsal_view=self.runtime.reader)
            prelude = self.chain._validate_prelude(expected, rehearsal_view=self.runtime.reader)
            editorial, _records, frames = self.chain._editorial_timeline_records(self.runtime.run,
                expected['segments'], self.chain._manifest_editorial(expected))
            self.chain._manifest_media_metadata(expected, rehearsal_view=self.runtime.reader)
            subtitles = self.chain._editorial_subtitle_cues(self.runtime.run,editorial,frames,
                timeline_origin_frames=int((expected.get('chapter') or {}).get('editorial_origin_frame',0)),
                rehearsal_view=self.runtime.reader)
        if saved['frame_count'] != frames+(prelude['frame_count'] if prelude else 0):
            raise state.StateConflict('Prepared assembly timeline changed.')
        if bool(subtitles) != ('subtitles' in saved['files']):
            raise state.StateConflict('Prepared assembly subtitle selection changed.')
        expected_scope = 'exports:assembly_'+state._hash(saved['base'].rsplit('/',1)[0].encode())[:32]
        if saved.get('scope') != expected_scope:
            raise state.StateConflict('Prepared assembly scope changed.')
        self.scopes.add(expected_scope)
        self._verify_dependencies(set(saved['dependencies']) | required)
        for role, item in saved['files'].items():
            if not re.fullmatch(re.escape(self.job)+r'/assemble[0-9]{4,}/'+re.escape(FILES[role]), item['path']):
                raise ValueError('Prepared assembly escaped its private job.')
            path = confined(self.runtime.project, item['path'])
            digest, signature = _hash_file(path)
            if digest != item['sha256'] or signature[2] != item['size']:
                raise state.StateConflict('Prepared assembly bytes changed.')

    def accepted(self):
        with self.runtime.exports.guard(self.proof):
            receipt = self.runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            snapshot = self.runtime.store.committed_snapshot(receipt)
            saved = state._decode(snapshot.read(self.witness))
            if saved.get('request_sha256') != self.request_hash:
                raise state.StateConflict('Assembly operation was accepted for another input.')
            self._verify_outputs(saved, snapshot)
            with state._lock(self.runtime.project):
                self.runtime.store._acknowledge_commit()
            self.runtime.record_commit(receipt)
            return self._result(saved, snapshot)

    def _verify_outputs(self, saved, snapshot):
        for role, item in saved['files'].items():
            if role == 'metadata':
                raw = snapshot.read(saved['logical'][role])
                digest, size = state._hash(raw), len(raw)
            else:
                path = self.runtime.store.payload_path(snapshot, saved['logical'][role], verify=True)
                digest, signature = _hash_file(path)
                size = signature[2]
            if digest != item['sha256'] or size != item['size']:
                raise state.StateConflict('Accepted assembly bytes changed.')

    def _companion_controls(self, saved):
        """Internal typed export variants may publish their own exact index."""
        return {}

    def publish(self):
        result = self.accepted()
        if result is not None:
            return result
        saved = self._prepared()
        if saved is None:
            raise ValueError('Assembly must be prepared before publication.')
        self._validate(saved)
        self._sources()
        def change(address, raw, immutable=False):
            prior = self.documents.get(address)
            return dict(data=raw, scope=prior['scope'] if prior else saved['scope'],
                category=prior['category'] if prior else 'cuts', immutable=immutable)
        requests, controls = [], {}
        legacy = pending_legacy(self.runtime.project, self.operation, saved['files'],
                                self.layout.export('video', self.operation))
        with self.runtime.exports.guard(self.proof):
            export_base = None if legacy else reserve_base(self.runtime.project, 'video', self.operation,
                self.final_name, suffixes=tuple(SUFFIXES.values()), budget=self.budget)
        for role, item in saved['files'].items():
            path, address = confined(self.runtime.project, item['path']), saved['logical'][role]
            if role == 'metadata':
                controls[address] = change(address, state._read_bytes(path))
                continue
            prior = self.payloads.get(address)
            requests.append(dict(address=address, source=path,
                target=self.layout.export_file('video', self.operation, role) if legacy else export_base+SUFFIXES[role],
                scope=prior['scope'] if prior else saved['scope'], immutable=False,
                operation_id=uuid.uuid5(uuid.UUID(self.operation), role).hex))
        def staged_file(receipt):
            address = receipt['record']['address']
            role = next(key for key,value in saved['logical'].items() if value == address)
            if any(receipt['record']['file'][key] != saved['files'][role][key] for key in ('sha256','size')):
                raise state.StateConflict('Assembly bytes changed while staging.')
            self.chain._png_export_check_interrupted()
            if self.runtime.exports.after_stage:
                self.runtime.exports.after_stage('payload')
        with self.runtime.exports.guard(self.proof):
            pass
        staged = self.runtime.store.stage_payloads(requests, after_stage=staged_file)
        self._validate(saved)
        controls[self.witness] = change(self.witness, state._encode(saved), True)
        index = saved['base'].rsplit('/',1)[0]+'/assembly_exports/'+self.operation+'.json'
        controls[index] = change(index, state._encode(dict(format=FORMAT, operation=self.operation,
            outputs=saved['logical'], frame_count=saved['frame_count'], request=self.request)), True)
        companions = self._companion_controls(saved)
        if set(companions) & set(controls):
            raise state.StateConflict('Assembly companion controls collide with its output records.')
        controls.update(companions)
        with self.runtime.exports.guard(self.proof):
            receipt = self.runtime.store.commit_artifacts(self.runtime.base, controls, staged,
                operation_id=self.operation, read_scopes=self.scopes, after_stage=self.runtime.exports.after_stage)
            self.runtime.record_commit(receipt)
        return self._result(saved, self.runtime.accepted)

    def _result(self, saved, snapshot):
        status = saved['status']
        for role,item in saved['files'].items():
            if role == 'metadata':
                continue
            path = str(self.runtime.store.payload_path(snapshot, saved['logical'][role], verify=True))
            status = status.replace(str(self.runtime.project/item['path']), path)
            if role == 'video':
                video = path
        published_video = video
        if self.settings.get('copy_to_output'):
            from .storage_output_copy import OutputCopy
            copies = OutputCopy(self,saved,snapshot).publish()
            published_video = copies['video']
            status += '; output copy -> '+published_video
            if copies.get('subtitles'):
                status += '; subtitle copy -> '+copies['subtitles']
        self.chain._LOG.info('H3 Chain %s', status)
        # The accepted path is outside the input pin; avoid resolving it as an
        # old logical source for either the Review Gate or ordinary UI output.
        relative = Path(published_video).relative_to(self.runtime.output)
        item = dict(filename=relative.name, subfolder=relative.parent.as_posix(), type='output')
        self.chain._publish_final_review_preview(saved['manifest'], published_video, status, video_item=item)
        return dict(ui=dict(text=[status], images=[item], animated=(True,)), result=(video,))
