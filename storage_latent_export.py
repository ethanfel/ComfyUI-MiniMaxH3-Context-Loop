"""Checkpoint PNG/WAV export, using the original renderer in a private job.

The logical sequence/index remains compatible with legacy recovery. Frames
append to a short physical directory; soundtrack versions never overwrite
accepted bytes. Only the combined commit makes new frames, WAV and index live.
"""
import copy
from pathlib import Path
import re
import uuid

from . import storage_state as state
from .storage_layout import OrganizedStorageLayout
from .storage_processing import ProcessingSourceDependencies
from .storage_project import _hash_file, payload_catalog
from .storage_resolver import confined
from .storage_export_names import reserve_base, frame_directory, pending_legacy

FORMAT = 'h3_storage_latent_export_v1'
FAMILY = 'h3_storage_latent_export_family_v1'


class LatentPNGExport(ProcessingSourceDependencies):
    def __init__(self, runtime, upscale, manifest, *, operation, export_name,
                 video_vae=None, audio_vae=None, first_frame_number=1,
                 png_compression=1, png_bit_depth=8, embed_workflow=True,
                 checkpoint_verification='cached', reuse_existing=True):
        runtime.exports.require_write()
        super().__init__(runtime, upscale)
        self.chain, self.operation = upscale.chain, state._token(operation)
        if (manifest.get('_storage_pin') != runtime.pin or manifest.get('run_name') != runtime.run
                or manifest.get('_branch_id', 'main') != runtime.selected):
            raise ValueError('Checkpoint export requires its exact project/branch input pin.')
        if video_vae is None and audio_vae is None:
            raise ValueError('H3 PNG/WAV export needs video_vae, audio_vae, or both.')
        if (type(first_frame_number) is not int or first_frame_number < 0
                or type(png_compression) is not int or not 0 <= png_compression <= 9
                or png_bit_depth not in (8,16) or type(embed_workflow) is not bool
                or type(reuse_existing) is not bool or checkpoint_verification not in ('cached','strict')):
            raise ValueError('Invalid checkpoint PNG/WAV export settings.')
        self.manifest = copy.deepcopy(manifest)
        self.proof = copy.deepcopy(manifest.get('_project_ownership'))
        self.video_vae, self.audio_vae = video_vae, audio_vae
        self.settings = dict(first_frame_number=first_frame_number, png_compression=png_compression,
            png_bit_depth=png_bit_depth, embed_workflow=embed_workflow,
            checkpoint_verification=checkpoint_verification, reuse_existing=reuse_existing)
        chain = self.chain
        audio, pictures, self.editorial, sources = chain._checkpoint_export_views(self.manifest, rehearsal_view=runtime.reader)
        if manifest.get('editorial') is None:
            self.scopes.add('branch:'+runtime.selected)
        self.sources = copy.deepcopy(sources)
        for source in self.sources:
            self.verify_source(source)
        # An explicit ALT input still owns only picture; don't decode its audio.
        self.audio_segments, self.picture_segments = copy.deepcopy(audio), copy.deepcopy(pictures)
        self.frames = sum(chain._editorial_segment_delivered_frames(s) for s in self.picture_segments)
        self.incremental = reuse_existing and manifest.get('format') == chain.CHAPTER_MANIFEST_FORMAT
        self.identity = chain._png_export_incremental_identity(self.manifest, self.audio_segments,
            self.picture_segments, video_vae, audio_vae, first_frame_number, png_compression,
            embed_workflow, png_bit_depth, rehearsal_view=runtime.reader)
        self.archives, self.archive_tags = copy.deepcopy(manifest.get('archives') or {}), {}
        if not isinstance(self.archives, dict):
            raise ValueError('Checkpoint export archives must be accepted archive addresses.')
        for key, value in self.archives.items():
            if value is None:
                continue
            address = runtime.reader.address(value)
            descriptor = runtime.base.state['documents'].get(address)
            if descriptor is None:
                raise ValueError('Checkpoint export archive is not accepted: '+key)
            self.scopes.add(descriptor['scope'])
            raw = runtime.base.read(address)
            tag = {'api_prompt':'prompt', 'workflow':'workflow', 'plan':'h3_plan'}.get(key)
            if tag:
                self.archive_tags[tag] = raw.decode('utf-8')
        root = runtime.reader.address(runtime.reader.working_directory(runtime.run))
        if isinstance(manifest.get('chapter'), dict):
            root = (root+'/' if root else '')+'chapters/'+chain._chapter_directory_name(manifest['chapter'])
        self.base = (root+'/' if root else '')+'frames/'+chain._safe_name(export_name, 'png_sequence')
        branch = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        if not self.base.startswith(branch):
            raise ValueError('Checkpoint export belongs to another branch.')
        family_id = state._hash(self.base.encode())[:32]
        self.family_address = branch+'latent_exports/'+family_id+'.json'
        self.scope = 'exports:latent_'+family_id
        self.scopes.add(self.scope)
        self.job = 'project/jobs/'+self.operation
        self.witness = branch+'export_records/'+self.operation+'.json'
        self.budget = runtime.store._marker()[0]['path_budget']
        self.payloads = payload_catalog(runtime.base)
        self.request = dict(format=FORMAT, operation=self.operation, manifest=self.manifest,
            ownership=self.proof, sources=self.sources, editorial=self.editorial,
            identity=self.identity, settings=self.settings, base=self.base)
        self.request_hash = state._hash(state._encode(self.request))
        with runtime.exports.guard(self.proof):
            state._immutable(runtime.project, self.job+'/latent-request.json', state._encode(self.request), self.budget)

    def _read(self, address):
        descriptor = self.runtime.base.state['documents'].get(address)
        if descriptor is None:
            return None
        self.scopes.add(descriptor['scope'])
        return state._decode(self.runtime.base.read(address))

    def _family(self):
        family = self._read(self.family_address)
        if family is None:
            family = dict(format=FAMILY, base=self.base, variants={})
            pattern = re.compile(re.escape(self.base)+r'(?:_([0-9]{4,}))?/export\.json\Z')
            for address in self.runtime.base.state['documents']:
                match = pattern.fullmatch(address)
                if match:
                    family['variants'][str(int(match[1] or '1'))] = address.rsplit('/',1)[0]
        if (not isinstance(family, dict) or family.get('format') != FAMILY or family.get('base') != self.base
                or not isinstance(family.get('variants'), dict)):
            raise ValueError('Invalid checkpoint export family; old exports were retained.')
        for ordinal, directory in family['variants'].items():
            if (not re.fullmatch('[1-9][0-9]*', ordinal)
                    or directory != self.base+('' if ordinal == '1' else '_%04d' % int(ordinal))):
                raise ValueError('Invalid checkpoint export variant address.')
        return family

    def _accepted_file(self, directory, item):
        name = item.get('file')
        if not isinstance(name,str) or not re.fullmatch(r'frame_[0-9]{8,}\.png|audio\.wav', name):
            raise ValueError('Invalid checkpoint export payload name.')
        address = directory+'/'+name
        record = self.payloads.get(address)
        if (record is None or any(record['file'][key] != item.get(key) for key in ('sha256','size'))):
            raise state.StateConflict('Checkpoint export prefix differs from accepted media.')
        self.scopes.add(record['scope'])
        return self.runtime.store.payload_path(self.runtime.base, address, verify=True)

    def _previous_files(self, directory, record):
        prior = record.get('incremental') or {}
        sources, clips = prior.get('sources') or [], record.get('clips') or []
        if (record.get('format') != 'h3_chain_png_export_v1' or record.get('complete') is not True
                or prior.get('format') != self.identity['format'] or prior.get('settings') != self.identity['settings']
                or not sources or not self.chain._png_export_sources_match(sources, self.identity['sources'][:len(sources)])
                or len(sources) != len(clips)):
            raise ValueError('Checkpoint export does not match this unchanged chapter prefix.')
        count = sum(s['frames'] for s in sources)
        first = self.settings['first_frame_number']
        expected = ['frame_%08d.png' % n for n in range(first, first+count)] if self.video_vae is not None else []
        files = record.get('frame_files') or []
        if (record.get('frame_count') != len(expected) or record.get('timeline_frame_count') != count
                or record.get('first_frame_number') != first or [f['file'] for f in files] != expected):
            raise ValueError('Checkpoint export has invalid frame numbering/counts.')
        cursor = first
        for source, clip in zip(sources, clips):
            if (clip.get('index') != source['index'] or clip.get('delivered_frames') != source['frames']
                    or (self.video_vae is not None and (clip.get('first_frame_number') != cursor
                        or clip.get('last_frame_number') != cursor+source['frames']-1))):
                raise ValueError('Checkpoint export has invalid scene timing.')
            cursor += source['frames']
        result = {f['file']:self._accepted_file(directory, f) for f in files}
        if self.audio_vae is not None:
            result['audio.wav'] = self._accepted_file(directory, record.get('audio') or {})
        return result

    def _select(self):
        family, previous, prior_dir, paths = self._family(), {}, None, {}
        if self.incremental:
            for ordinal, directory in sorted(family['variants'].items(), key=lambda i:int(i[0]), reverse=True):
                record = self._read(directory+'/export.json')
                if self._read(directory+'/export.partial.json') is not None:
                    continue
                try:
                    paths = self._previous_files(directory, record or {})
                except (ValueError, KeyError, TypeError, OSError):
                    continue  # Incomplete, edited or different exports stay untouched.
                previous, prior_dir = record, directory
                break
        physical, directory = None, prior_dir
        if previous:
            descriptor = self.runtime.base.state['documents'][directory+'/export.json']
            groups = {self.payloads[directory+'/'+name]['file']['path'].rsplit('/',1)[0]
                      for name in paths if name != 'audio.wav'}
            candidate = previous.get('_storage_export_id')
            audio_can_append = (self.audio_vae is None or len(previous['clips']) == len(self.sources)
                                or not self.payloads[directory+'/audio.wav']['immutable'])
            if (not descriptor['immutable'] and isinstance(candidate,str) and re.fullmatch('[0-9a-f]{32}',candidate)
                    and audio_can_append
                    and (not groups or groups == {frame_directory(self.payloads, directory)})):
                physical = candidate
            # An immutable imported index cannot become a mutable current one.
            # Its verified prefix can still be independently copied, not decoded.
        if physical is None:
            directory = None
            for ordinal in range(1,10001):
                candidate = self.base+('' if ordinal == 1 else '_%04d' % ordinal)
                occupied = any(a.startswith(candidate+'/') for a in self.runtime.base.state['documents'])
                occupied |= any(a.startswith(candidate+'/') for a in self.payloads)
                if str(ordinal) not in family['variants'] and not occupied:
                    directory = candidate
                    family['variants'][str(ordinal)] = directory
                    break
            if directory is None:
                raise ValueError('No unused checkpoint export variant is available.')
            physical = self.operation
        return dict(directory=directory, export_id=physical, previous_directory=prior_dir,
                    previous=previous, family=family), paths

    def _prepared(self):
        path = confined(self.runtime.project, self.job+'/latent-prepared.json')
        if not path.exists():
            return None
        saved = state._decode(state._read_bytes(path))
        if (set(saved) != {'value','sha256'} or state._hash(state._encode(saved['value'])) != saved['sha256']
                or saved['value'].get('request_sha256') != self.request_hash):
            raise state.StateConflict('Prepared checkpoint export differs from its exact request.')
        return saved['value']

    def prepare(self, node, *, workers=0):
        if self.accepted() is not None or self._prepared() is not None:
            return
        self.selection, self.prior_paths = self._select()
        self.previous = copy.deepcopy(self.selection['previous'])
        layout = OrganizedStorageLayout(str(self.runtime.project), self.budget)
        with self.runtime.exports.guard(self.proof):
            # The full operation ID already isolates this job. Bounded attempt
            # counters avoid nesting another UUID into every temporary PNG name.
            for attempt in range(1,10001):
                self.buffer = self.job+'/decode%04d' % attempt
                last_name = 'frame_%08d.png' % (self.settings['first_frame_number']+self.frames-1)
                layout.check_budget(self.buffer+'/'+last_name+'.'+'f'*32+'.tmp')
                path = confined(self.runtime.project, self.buffer)
                try:
                    path.mkdir()
                    break
                except FileExistsError:
                    continue
            else:
                raise ValueError('Checkpoint export has too many interrupted private attempts.')
        self.render_directory = str(path)
        self.cache_path = str(path/'hash-cache.json')
        self.hash_cache = dict(format=self.chain.PNG_EXPORT_HASH_CACHE_FORMAT, entries={})
        self.progress_sequence = 0
        node._export(self.manifest, self.video_vae, Path(self.base).name,
            self.settings['first_frame_number'], self.settings['png_compression'], self.settings['embed_workflow'],
            workers, self.settings['checkpoint_verification'], self.audio_vae, self.incremental,
            self.settings['png_bit_depth'], _workspace=self)

    def write_json(self, path, value):
        # Durable private progress/cache events, never CIFS replace-in-place.
        if str(path) not in (self.cache_path, str(Path(self.render_directory)/'export.partial.json')):
            raise ValueError('Checkpoint renderer attempted a write outside its private progress journal.')
        self.progress_sequence += 1
        with self.runtime.exports.guard(self.proof):
            state._immutable(self.runtime.project, self.buffer+'/progress/%08d.json' % self.progress_sequence,
                state._encode(dict(kind=Path(path).name, value=value)), self.budget)

    def load_tensors(self, segment, stream):
        if segment.get('processing_source'):
            return self.upscale._load_source_tensors(segment, (stream,))
        # Preserve the exporter's sampled-checkpoint route, not a preview or a
        # decoded video re-encode. audio_segments already point to ALT's base.
        return self.chain._st_load(self.path(segment['checkpoint']))

    def path(self, address):
        return str(self.runtime.reader.path(address))

    def verify_checkpoint(self, path, expected, mode, cache):
        return self.chain._verify_png_export_checkpoint(path, expected, mode, cache,
            cache_key=self.runtime.reader.logical_output(path))

    def finish(self, record):
        record = copy.deepcopy(record)
        record['_storage_export_id'] = self.selection['export_id']
        # Full-run exports also need hashes for transactional acceptance/recovery.
        files = list(record.get('frame_files') or [])
        if not self.incremental:
            first = self.settings['first_frame_number']
            files = [self.chain._png_export_file_record(str(Path(self.render_directory)/('frame_%08d.png' % n)))
                     for n in range(first, first+record['frame_count'])]
        record['frame_files'] = files
        payloads = {}
        entries = files+([record['audio']] if record.get('audio') else [])
        for item in entries:
            name = item['file']
            private = Path(self.render_directory)/name
            source = private if private.is_file() else self.prior_paths.get(name)
            if source is None:
                raise ValueError('Checkpoint renderer omitted an output: '+name)
            digest, signature = _hash_file(source)
            if digest != item['sha256'] or (item.get('size') is not None and signature[2] != item['size']):
                raise state.StateConflict('Checkpoint renderer output changed before preparation.')
            item.update(size=signature[2], mtime_ns=signature[3])
            payloads[name] = dict(path=source.relative_to(self.runtime.project).as_posix(),
                                  sha256=digest, size=signature[2], reused=source != private)
        saved = dict(request_sha256=self.request_hash, selection=self.selection, record=record, files=payloads)
        self._validate(saved)
        with self.runtime.exports.guard(self.proof):
            raw = state._encode(saved)
            state._immutable(self.runtime.project, self.job+'/latent-prepared.json',
                state._encode(dict(value=saved, sha256=state._hash(raw))), self.budget)

    def _validate(self, saved):
        selection, paths = self._select()
        record = saved.get('record') or {}
        if (saved.get('request_sha256') != self.request_hash or saved.get('selection') != selection
                or record.get('format') != 'h3_chain_png_export_v1' or record.get('complete') is not True
                or record.get('_storage_export_id') != selection['export_id']
                or record.get('run_name') != self.runtime.run or record.get('archives') != self.archives
                or record.get('timeline_frame_count') != self.frames
                or record.get('frame_count') != (self.frames if self.video_vae is not None else 0)
                or (self.incremental and record.get('incremental') != self.identity)):
            raise state.StateConflict('Prepared checkpoint export identity or selection changed.')
        expected_header = dict(source_manifest_format=self.manifest.get('format'), source_plan_hash=self.manifest.get('plan_hash'),
            chapter=self.manifest.get('chapter'), chapter_manifest_id=self.manifest.get('chapter_manifest_id'),
            chapter_manifest_path=self.manifest.get('chapter_manifest_path'),
            reused_clip_count=len(selection['previous'].get('clips') or []),
            reused_frame_count=selection['previous'].get('frame_count',0),
            last_frame_number=(self.settings['first_frame_number']+self.frames-1 if self.video_vae is not None else None))
        expected_header['new_frame_count'] = record['frame_count']-expected_header['reused_frame_count']
        if any(record.get(k) != v for k,v in expected_header.items()):
            raise state.StateConflict('Prepared checkpoint export header/provenance changed.')
        settings = record.get('settings') or {}
        expected_settings = dict(png_compression=self.settings['png_compression'], png_bit_depth=self.settings['png_bit_depth'],
            export_video=self.video_vae is not None, export_audio=self.audio_vae is not None, reuse_existing=self.incremental,
            checkpoint_verification=self.settings['checkpoint_verification'])
        if any(settings.get(k) != v for k,v in expected_settings.items()):
            raise state.StateConflict('Prepared checkpoint export settings changed.')
        first, clips = self.settings['first_frame_number'], record.get('clips') or []
        if len(clips) != len(self.picture_segments) or record.get('first_frame_number') != first:
            raise state.StateConflict('Prepared checkpoint export scene range changed.')
        cursor = first if self.video_vae is not None else 0
        for clip, source in zip(clips, self.picture_segments):
            count = self.chain._editorial_segment_delivered_frames(source)
            if (any(clip.get(k) != source.get(k) for k in ('index','id','checkpoint','seed'))
                    or clip.get('prompt') != str(source.get('prompt') or '')
                    or clip.get('prompt_hash') != str(source.get('prompt_hash') or '')
                    or clip.get('delivered_frames') != count or clip.get('trim_frames') != source['raw_frames']-count):
                raise state.StateConflict('Prepared checkpoint export scene provenance changed.')
            begin, end = ('first_frame_number','last_frame_number') if self.video_vae is not None else ('timeline_first_frame','timeline_last_frame')
            if clip.get(begin) != cursor or clip.get(end) != cursor+count-1:
                raise state.StateConflict('Prepared checkpoint export scene numbering changed.')
            cursor += count
        frame_files = record.get('frame_files') or []
        names = ['frame_%08d.png' % n for n in range(first, first+record['frame_count'])]
        if [f['file'] for f in frame_files] != names:
            raise state.StateConflict('Prepared checkpoint export frame index changed.')
        audio = record.get('audio')
        if bool(audio) != (self.audio_vae is not None):
            raise state.StateConflict('Prepared checkpoint export audio mode changed.')
        if audio:
            rate = self.chain._png_export_audio_sample_rate(self.audio_vae)
            if (audio.get('file') != 'audio.wav' or audio.get('sample_rate') != rate
                    or audio.get('samples') != self.chain.sample_boundary_from_frames(self.frames, rate, self.chain.FPS)):
                raise state.StateConflict('Prepared checkpoint export WAV timing changed.')
            names.append('audio.wav')
        if set(saved.get('files') or {}) != set(names):
            raise state.StateConflict('Prepared checkpoint export payload set changed.')
        for item in frame_files+([audio] if audio else []):
            name = item['file']
            payload = saved['files'][name]
            if any(payload.get(k) != item.get(k) for k in ('sha256','size')):
                raise state.StateConflict('Prepared checkpoint export index checksum changed.')
            source = confined(self.runtime.project, payload['path'])
            if payload.get('reused') is True:
                if source != paths.get(name):
                    raise ValueError('Prepared export refers to another reused source.')
            elif not re.fullmatch(re.escape(self.job)+r'/decode[0-9]{4,}/'+re.escape(name), payload['path']):
                raise ValueError('Prepared export escaped its private encoder job.')
            digest, signature = _hash_file(source)
            if digest != payload['sha256'] or signature[2] != payload['size']:
                raise state.StateConflict('Prepared checkpoint export bytes changed.')

    def accepted(self):
        with self.runtime.exports.guard(self.proof):
            receipt = self.runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            snapshot = self.runtime.store.committed_snapshot(receipt)
            saved = state._decode(snapshot.read(self.witness))
            if saved.get('request_sha256') != self.request_hash:
                raise state.StateConflict('Checkpoint export operation was accepted for another input.')
            for name, item in saved['files'].items():
                path = self.runtime.store.payload_path(snapshot, saved['selection']['directory']+'/'+name, verify=True)
                digest, signature = _hash_file(path)
                if digest != item['sha256'] or signature[2] != item['size']:
                    raise state.StateConflict('Accepted checkpoint export bytes changed.')
            with state._lock(self.runtime.project):
                self.runtime.store._acknowledge_commit()
            self.runtime.record_commit(receipt)
            return self._result(saved, snapshot)

    def publish(self):
        result = self.accepted()
        if result is not None:
            return result
        saved = self._prepared()
        if saved is None:
            raise ValueError('Checkpoint export must be prepared before publication.')
        self._validate(saved)
        selection = saved['selection']
        directory = selection['directory']
        layout = OrganizedStorageLayout(str(self.runtime.project), self.budget)
        requests = []
        with self.runtime.exports.guard(self.proof):
            png_directory = None
            if self.video_vae is not None:
                png_directory = frame_directory(self.payloads, directory)
                legacy = layout.export('png', selection['export_id'])
                if png_directory is None:
                    png_directory = legacy if pending_legacy(self.runtime.project, self.operation, saved['files'], legacy) else reserve_base(
                        self.runtime.project, 'png', selection['export_id'], Path(self.base).name, budget=self.budget)
            audio_base = None
            if any(name == 'audio.wav' and not (item['reused'] and directory == selection['previous_directory'])
                   for name, item in saved['files'].items()):
                if pending_legacy(self.runtime.project, self.operation, ('audio.wav',), layout.export('audio', selection['export_id'])):
                    audio_base = layout.export_file('audio', selection['export_id'], 'audio', revision=self.operation)[:-4]
                else:
                    audio_base = reserve_base(self.runtime.project, 'audio', self.operation,
                        Path(self.base).name, suffixes=('.wav',), budget=self.budget)
        for name, item in saved['files'].items():
            if item['reused'] and directory == selection['previous_directory']:
                continue
            old = self.payloads.get(directory+'/'+name)
            target = audio_base+'.wav' if name == 'audio.wav' else png_directory+'/'+name
            requests.append(dict(address=directory+'/'+name, source=confined(self.runtime.project, item['path']),
                target=target, scope=old['scope'] if old else self.scope, immutable=name != 'audio.wav',
                operation_id=uuid.uuid5(uuid.UUID(self.operation), name).hex))
        with self.runtime.exports.guard(self.proof):
            pass
        def staged_file(receipt):
            name = receipt['record']['address'].rsplit('/',1)[-1]
            if any(receipt['record']['file'][k] != saved['files'][name][k] for k in ('sha256','size')):
                raise state.StateConflict('Checkpoint export bytes changed while staging.')
            self.chain._png_export_check_interrupted()
            if self.runtime.exports.after_stage:
                self.runtime.exports.after_stage('payload')
        staged = self.runtime.store.stage_payloads(requests, after_stage=staged_file) if requests else []
        self._validate(saved)
        for source in self.sources:
            self.verify_source(source)
        def change(value, immutable=False):
            return dict(data=state._encode(value), scope=self.scope, category='cuts', immutable=immutable)
        index = directory+'/export.json'
        changes = {index:change(saved['record']), self.family_address:change(selection['family']), self.witness:change(saved, True)}
        prior = self.runtime.base.state['documents'].get(index)
        if prior:
            changes[index].update(scope=prior['scope'], category=prior['category'])
        if selection['previous']:
            history = directory+'/export.history/'+self.chain._fingerprint(selection['previous'])+'.json'
            if history not in self.runtime.base.state['documents']:
                changes[history] = change(selection['previous'], True)
        with self.runtime.exports.guard(self.proof):
            receipt = self.runtime.store.commit_artifacts(self.runtime.base, changes, staged,
                operation_id=self.operation, read_scopes=self.scopes, after_stage=self.runtime.exports.after_stage)
            self.runtime.record_commit(receipt)
        return self._result(saved, self.runtime.accepted)

    def _result(self, saved, snapshot):
        selection, record = saved['selection'], saved['record']
        audio = (str(self.runtime.store.payload_path(snapshot, selection['directory']+'/audio.wav'))
                 if record['audio'] else '')
        directory = (self.runtime.store.payload_path(snapshot,
            selection['directory']+'/'+record['frame_files'][0]['file']).parent
            if record['frame_files'] else Path(audio).parent)
        index = confined(self.runtime.project, snapshot.state['documents'][selection['directory']+'/export.json']['file']['path'])
        status = 'Checkpoint export: %d scenes / %d PNGs / %d timeline frames; %s; accepted index -> %s' % (
            len(record['clips']), record['frame_count'], record['timeline_frame_count'], Path(selection['directory']).name, index)
        return {'ui':{'text':[status]}, 'result':(str(directory), record['frame_count'], status, audio, None)}
