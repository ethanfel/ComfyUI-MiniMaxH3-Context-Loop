"""Scene-at-a-time PNG sequences on the combined, pinned project store.

Frames remain in one short directory when a scene is appended. Only a genuine
fork independently copies a verified prefix. The mutable logical export.json
is an immutable-versioned control document: replacing a public JSON file on
CIFS is not an authority mechanism. The node reports its accepted index path.
"""
import copy
import json
from pathlib import Path
import re
import uuid

from . import storage_state as state
from . import png_video_export as png
from . import png_export_ownership as owners
from .storage_layout import OrganizedStorageLayout
from .storage_processing import ProcessingSourceDependencies
from .storage_project import _hash_file, payload_catalog
from .storage_resolver import confined

FORMAT = 'h3_storage_png_family_v1'
REQUEST = 'h3_storage_png_scene_v1'


class PNGSequenceExport(ProcessingSourceDependencies):
    def __init__(self, runtime, upscale, incoming, video, *, operation, export_name,
                 output_folder='', first_frame_number=1, png_compression=1,
                 png_bit_depth=8, embed_workflow=True, checkpoint_verification='cached',
                 reuse_existing=True):
        runtime.exports.require_write()
        super().__init__(runtime, upscale)
        self.operation = state._token(operation)
        self.incoming, self.video = incoming, video
        if (incoming.get('_storage_pin') != runtime.pin or incoming.get('run_name') != runtime.run
                or incoming.get('_branch_id', 'main') != runtime.selected
                or incoming.get('profile_config', {}).get('backend') != 'pixel'):
            raise ValueError('PNG sequence requires the exact pinned pixel scene state.')
        self.index = incoming['index']
        self.source = copy.deepcopy(upscale._source_segment(incoming))
        self.manifest = copy.deepcopy(incoming['source_manifest'])
        if (type(self.index) is not int or self.index < 1
                or type(incoming['end_clip']) is not int or incoming['end_clip'] < self.index):
            raise ValueError('Invalid PNG sequence scene range.')
        for source in self.manifest['segments']:
            self.verify_source(source)
        self.contracts = {s['index']: upscale._upscale_source_contract(s) for s in self.manifest['segments']}
        # The ordinary Upscale Adapter carries ownership on source_manifest.
        # Match Segment Save; an explicit outer None must still deny access.
        self.proof = copy.deepcopy(incoming.get('_project_ownership',
                                  self.manifest.get('_project_ownership')))
        self.config = dict(run_name=runtime.run, profile=incoming['profile'], profile_config=incoming['profile_config'],
                           first_frame_number=first_frame_number, png_compression=png_compression,
                           png_bit_depth=png.bit_depth(png_bit_depth), embed_workflow=embed_workflow)
        if (type(first_frame_number) is not int or first_frame_number < 0
                or type(png_compression) is not int or not 0 <= png_compression <= 9
                or type(embed_workflow) is not bool or type(reuse_existing) is not bool
                or checkpoint_verification not in ('cached', 'strict')):
            raise ValueError('Invalid PNG sequence settings.')
        self.reuse, self.verification = reuse_existing, checkpoint_verification
        self.path = png._source_path(video)
        digest, signature = _hash_file(self.path)
        self.video_identity = dict(path=str(self.path.absolute()), sha256=digest, size=signature[2])
        self.owner = owners.owner_key(incoming, self.contracts[self.index])
        self.chain = upscale.chain
        # Keep authored folder names as logical identities, never re-introduce
        # long branch/chapter/profile nesting into physical media storage.
        paths = upscale._state_profile_paths(incoming, self.index)
        profile = runtime.reader.address(paths['root'])
        if incoming['source_manifest_hash'] != upscale._source_hash(self.manifest):
            raise ValueError('PNG source manifest changed after its adapter.')
        config = incoming['profile_config']
        if config.get('config_hash') != self.chain._fingerprint({k:v for k,v in config.items() if k != 'config_hash'}):
            raise ValueError('PNG profile settings differ from their saved hash.')
        default = profile+'/frames/'+self.chain._safe_name(export_name, 'png_sequence')
        if output_folder:
            requested = Path(output_folder)
            if not requested.is_absolute():
                requested = Path(runtime.output)/requested
            try:
                self.base = runtime.reader.address(requested)
            except ValueError:
                raise ValueError('Combined PNG export outside this project needs an external-export port; '
                                 'use the profile destination for now. No external folder was changed.') from None
        else:
            self.base = default
        if not self.base or self.base.endswith(('.json', '.png')):
            raise ValueError('PNG export requires a logical sequence directory.')
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        if prefix and not self.base.startswith(prefix):
            raise ValueError('PNG destination belongs to a different branch.')
        if not prefix and self.base.startswith('branches/'):
            raise ValueError('PNG destination belongs to a different branch.')
        if re.fullmatch(re.escape(prefix)+r'(?:chapters/[^/]+/)?upscaled/[^/]+', profile) is None:
            raise ValueError('PNG processing profile is outside its selected branch.')
        family_id = state._hash(state._encode([runtime.selected, self.base]))[:32]
        self.family_address = prefix+'png_sequences/'+family_id+'.json'
        self.scope = 'exports:png_'+family_id
        self.scopes.add(self.scope)
        self.scopes.add('exports:png_catalog')
        self.job = 'project/jobs/'+self.operation
        self.witness = prefix+'export_records/'+self.operation+'.json'
        self.budget = runtime.store._marker()[0]['path_budget']
        self.payloads = payload_catalog(runtime.base)
        self.archive_tags = {}
        for key, tag in (('api_prompt','prompt'), ('workflow','workflow'), ('plan','h3_plan')):
            value = (self.manifest.get('archives') or {}).get(key)
            if value is not None:
                address = runtime.reader.address(value)
                descriptor = runtime.base.state['documents'].get(address)
                if descriptor is None:
                    raise ValueError('PNG source archive is not accepted: '+key)
                self.scopes.add(descriptor['scope'])
                self.archive_tags[tag] = runtime.base.read(address).decode('utf-8')
        self.request = dict(format=REQUEST, operation=self.operation, input_pin=runtime.pin,
            ownership=self.proof, manifest=self.manifest, index=self.index, end_clip=incoming['end_clip'],
            base=self.base, config=self.config, reuse=self.reuse, video=self.video_identity,
            owner=self.owner, session=incoming.get('png_export_session', ''),
            profile_config=incoming['profile_config'])
        self.request_hash = state._hash(state._encode(self.request))
        with runtime.exports.guard(self.proof):
            state._immutable(runtime.project, self.job+'/sequence-request.json',
                             state._encode(self.request), self.budget)

    def _check_video(self):
        digest, signature = _hash_file(self.path)
        if digest != self.video_identity['sha256'] or signature[2] != self.video_identity['size']:
            raise state.StateConflict('PNG input VIDEO changed during sequence export.')

    def _read(self, address):
        descriptor = self.runtime.base.state['documents'].get(address)
        if descriptor is None:
            return None
        self.scopes.add(descriptor['scope'])
        return state._decode(self.runtime.base.read(address))

    def _frames(self, directory, clips):
        """Verify indexed bytes, not editable stat hints or loose directory scans."""
        found, frame = {}, self.config['first_frame_number']
        expected_scene = clips[0]['index'] if clips else None
        for clip in clips:
            count = clip.get('delivered_frames')
            if (clip.get('index') != expected_scene or type(count) is not int or count < 1
                    or clip.get('source_contract') != self.contracts.get(expected_scene)
                    or clip.get('first_frame_number') != frame
                    or clip.get('last_frame_number') != frame+count-1
                    or not isinstance(clip.get('files'), list) or len(clip['files']) != count):
                raise png.variants.SequenceConflict('PNG sequence source/order changed.')
            for item in clip['files']:
                name = 'frame_%08d.png' % frame
                if item.get('file') != name:
                    raise ValueError('Invalid PNG sequence frame address.')
                address = directory+'/'+name
                record = self.payloads.get(address)
                if (record is None or record['file']['sha256'] != item.get('sha256')
                        or record['file']['size'] != item.get('size')):
                    raise png.variants.SequenceConflict('PNG prefix differs from accepted files.')
                self.scopes.add(record['scope'])
                try:
                    found[name] = self.runtime.store.payload_path(self.runtime.base, address, verify=True)
                except (ValueError, OSError) as error:
                    raise png.variants.SequenceConflict('PNG prefix is missing or changed.') from error
                frame += 1
            expected_scene += 1
        return found

    def _matching(self, clip, files):
        if clip.get('video_sha256') == self.video_identity['sha256']:
            return True
        bits = self.config['png_bit_depth']
        digest = png._pixel_hasher(bits)
        for pixels in png._scene_pixels(self.chain, self.path, self.source['raw_frames'],
                                        self.source['delivered_frames'], bits):
            png._hash_pixels(digest, pixels)
        expected = clip.get('pixel_sha256')
        if not expected:
            import av
            saved = png._pixel_hasher(bits)
            for item in clip['files']:
                with av.open(str(files[item['file']])) as container:
                    pixels = next(container.decode(video=0)).to_ndarray(format='rgb48le' if bits == 16 else 'rgb24')
                    png._hash_pixels(saved, pixels)
            expected = saved.hexdigest()
        return digest.hexdigest() == expected

    def _family(self):
        family = self._read(self.family_address)
        if family is None:
            family = dict(format=FORMAT, base=self.base, variants={}, sessions={})
            # Existing migrated sequences keep their logical identity. Imported
            # records are reused only after validating their accepted files.
            pattern = re.compile(re.escape(self.base)+r'(?:_([2-9]|[1-9][0-9]+))?/export\.json\Z')
            for address in self.runtime.base.state['documents']:
                match = pattern.fullmatch(address)
                if match:
                    ordinal = match.group(1) or '1'
                    family['variants'][ordinal] = address.rsplit('/',1)[0]
        if (not isinstance(family, dict) or family.get('format') != FORMAT
                or family.get('base') != self.base or not isinstance(family.get('variants'), dict)
                or not isinstance(family.get('sessions'), dict)):
            raise ValueError('Unsupported PNG family catalogue; old exports were retained.')
        for ordinal, directory in family['variants'].items():
            if (not re.fullmatch(r'[1-9][0-9]*', ordinal) or directory !=
                    self.base+('' if ordinal == '1' else '_'+ordinal)):
                raise ValueError('Invalid PNG variant identity.')
        return family

    def _binding(self):
        session = self.request['session']
        if not session:
            return None
        # Exact legacy encoding keeps restored workflows' session routing.
        key = state._hash(json.dumps([session,self.config], sort_keys=True).encode())
        return self.base+'/.png_variants/'+key+'.json'

    def _prepared(self):
        path = confined(self.runtime.project, self.job+'/sequence-prepared.json')
        if not path.exists():
            return None
        saved = state._decode(state._read_bytes(path))
        if (set(saved) != {'sha256','value'} or state._hash(state._encode(saved['value'])) != saved['sha256']
                or saved['value'].get('request_sha256') != self.request_hash):
            raise state.StateConflict('Prepared PNG sequence differs from its exact request.')
        return saved['value']

    def _validate_prepared(self, saved):
        """A prepared checksum is corruption detection, not a path/write grant."""
        directory, record = saved['directory'], saved['record']
        if (not re.fullmatch(re.escape(self.base)+r'(?:_[2-9]|_[1-9][0-9]+)?', directory)
                or record.get('format') != png.FORMAT or record.get('settings') != self.config
                or record.get('source_manifest') != self.manifest
                or state._token(saved['export_id']) != record.get('_storage_export_id')
                or not isinstance(record.get('clips'), list) or not record['clips']
                or saved.get('request_sha256') != self.request_hash):
            raise state.StateConflict('Prepared PNG identity differs from its request.')
        frame = self.config['first_frame_number']
        scene = record['clips'][0]['index']
        expected = {}
        sources = {item['index']:item for item in self.manifest['segments']}
        for clip in record['clips']:
            count = clip.get('delivered_frames')
            source = sources.get(scene, {})
            if (type(scene) is not int or clip.get('index') != scene or type(count) is not int or count <= 0
                    or clip.get('source_contract') != self.contracts.get(scene)
                    or count != source.get('delivered_frames') or clip.get('raw_frames') != source.get('raw_frames')
                    or clip.get('trim_frames') != source.get('raw_frames',0)-count
                    or clip.get('source_revision') != source.get('revision')
                    or clip.get('first_frame_number') != frame or clip.get('last_frame_number') != frame+count-1
                    or not isinstance(clip.get('files'), list) or len(clip['files']) != count):
                raise state.StateConflict('Prepared PNG timing/source changed.')
            for item in clip['files']:
                name = 'frame_%08d.png' % frame
                if (item.get('file') != name or type(item.get('size')) is not int or item['size'] < 1
                        or not re.fullmatch('[0-9a-f]{64}', str(item.get('sha256', '')))):
                    raise state.StateConflict('Prepared PNG frame record is invalid.')
                expected[name] = item
                frame += 1
            scene += 1
        if (record.get('last_scene') != scene-1 or record.get('frame_count') != frame-self.config['first_frame_number']
                or record.get('complete') is not (scene-1 == self.incoming['end_clip'])
                or not any(c['index'] == self.index for c in record['clips'])
                or set(saved['files'])-set(expected)):
            raise state.StateConflict('Prepared PNG range or payload list changed.')
        accepted_sources = {item['file']['path']:item['file'] for item in self.payloads.values()}
        for name,item in expected.items():
            incoming = saved['files'].get(name)
            if incoming is None:
                prior = self.payloads.get(directory+'/'+name)
                if prior is None or any(prior['file'][key] != item[key] for key in ('sha256','size')):
                    raise state.StateConflict('Prepared PNG references an unstaged frame.')
                self.runtime.store.payload_path(self.runtime.base, directory+'/'+name, verify=True)
                continue
            if any(incoming[key] != item[key] for key in ('sha256','size')):
                raise state.StateConflict('Prepared PNG payload differs from its index.')
            source_path = incoming['path']
            private = re.fullmatch(re.escape(self.job)+r'/png-[0-9a-f]{32}/'+re.escape(name), source_path)
            accepted = accepted_sources.get(source_path)
            if not private and (accepted is None or any(accepted[key] != incoming[key] for key in ('sha256','size'))):
                raise state.StateConflict('Prepared PNG payload is outside its job or accepted prefix.')
        # Never let a modified prepared record remove the caller's source or
        # family conflict fences. Additional saved scopes cover copied prefixes.
        if not self.scopes <= set(saved['scopes']):
            raise state.StateConflict('Prepared PNG lost its source dependency scopes.')
        expected_family = self._family()
        ordinal = '1' if directory == self.base else directory[len(self.base)+1:]
        expected_family['variants'][ordinal] = directory
        if self.request['session']:
            key = state._hash(state._encode([self.request['session'],self.config]))
            expected_family['sessions'][key] = ordinal
        if saved['family'] != expected_family:
            raise state.StateConflict('Prepared PNG changed another sequence or session binding.')

    def prepare(self, *, workers=0):
        if self.accepted() is not None or self._prepared() is not None:
            return
        family = self._family()
        session = self.request['session']
        binding = state._hash(state._encode([session,self.config])) if session else None
        ordinal = family['sessions'].get(binding) if binding else None
        if ordinal is None and self._binding():
            legacy_binding = self._read(self._binding())
            if legacy_binding is not None:
                name = legacy_binding.get('directory') if isinstance(legacy_binding,dict) else None
                if not isinstance(name,str) or not re.fullmatch(re.escape(Path(self.base).name)+r'(?:_[2-9]|_[1-9][0-9]+)?', name):
                    raise ValueError('Invalid migrated PNG session binding.')
                ordinal = next((n for n,d in family['variants'].items() if Path(d).name == name), None)
                if ordinal is None:
                    raise ValueError('Migrated PNG session binding has no accepted index.')
        if ordinal is not None and ordinal not in family['variants']:
            raise ValueError('PNG session refers to an unknown sequence.')
        ordinal = ordinal or str(max(map(int,family['variants']), default=1))
        directory = family['variants'].get(ordinal, self.base)
        previous = self._read(directory+'/export.json')
        if directory+'/.png_pending.json' in self.runtime.base.state['documents']:
            raise ValueError('Migrated PNG sequence has an unresolved publication journal; existing files were retained.')
        clips, files, copied = [], {}, {}
        export_id, reused = None, False
        try:
            if previous is not None:
                if not isinstance(previous, dict) or previous.get('format') != png.FORMAT or previous.get('settings') != self.config:
                    raise png.variants.SequenceConflict('PNG settings changed.')
                clips = copy.deepcopy(previous.get('clips'))
                if not isinstance(clips, list) or not clips or previous.get('deleted_scenes'):
                    raise png.variants.SequenceConflict('PNG sequence is empty or contains deleted scenes.')
                files = self._frames(directory, clips)
                if not self.reuse and not any(session and c.get('export_session') == session for c in clips):
                    raise png.variants.SequenceConflict('Reuse is disabled.')
                existing = next((c for c in clips if c['index'] == self.index), None)
                if existing:
                    if not self._matching(existing, files):
                        raise png.variants.SequenceConflict('This scene has different PNGs.')
                    reused = True
                    if self.owner and self.owner not in existing.get('processing_owners', []):
                        if 'processing_owners' not in existing:
                            existing['legacy_unattributed'] = True
                        existing.setdefault('processing_owners', []).append(self.owner)
                elif self.index != clips[-1]['index']+1:
                    raise ValueError('PNG sequence has a scene gap. Resume at scene %d or choose a new folder.' % (clips[-1]['index']+1))
                export_id = previous.get('_storage_export_id')
                if export_id is None:
                    parents = {path.parent for path in files.values()}
                    if len(parents) == 1:
                        parent = next(iter(parents)).relative_to(self.runtime.project)
                        if len(parent.parts) == 3 and parent.parts[:2] == ('exports','png'):
                            export_id = state._token(parent.name)
                descriptor = self.runtime.base.state['documents'][directory+'/export.json']
                if export_id is None or descriptor['immutable']:
                    raise png.variants.SequenceConflict('Migrated PNG index requires a writable sequence variant.')
            if export_id is not None:
                physical = confined(self.runtime.project, 'exports/png/'+export_id)
                tracked = set(files)
                if any(p.name not in tracked for p in physical.glob('frame_*.png')):
                    raise png.variants.SequenceConflict('PNG folder contains untracked frames.')
        except png.variants.SequenceConflict:
            # A mid-sequence fork can retain only the exact earlier source
            # prefix, never scenes from a different branch or explicitly deleted
            # frames. Copies are staged once for the fork, not on every append.
            prefix = []
            if self.reuse and isinstance(previous, dict) and previous.get('settings') == self.config and not previous.get('deleted_scenes'):
                prefix = [copy.deepcopy(c) for c in previous.get('clips', []) if isinstance(c,dict) and c.get('index',self.index) < self.index]
                if prefix and prefix[-1]['index']+1 == self.index:
                    try:
                        copied = self._frames(directory, prefix)
                        selected_owners = {s['index']:s.get('png_export_owner') for s in self.incoming.get('segments',[]) if s.get('png_export_owner')}
                        if any(c['index'] in selected_owners and selected_owners[c['index']] not in c.get('processing_owners',[]) for c in prefix):
                            raise png.variants.SequenceConflict('PNG prefix belongs to another processing take.')
                    except png.variants.SequenceConflict:
                        prefix, copied = [], {}
                else:
                    prefix = []
            clips, files, reused, export_id = prefix, copied, False, None
            ordinal = str(max(map(int,family['variants']), default=1)+1)
            directory = self.base+'_'+ordinal
        export_id = export_id or uuid.uuid5(uuid.UUID(self.operation), 'png-sequence').hex
        first = self.config['first_frame_number']+sum(c['delivered_frames'] for c in clips)
        new_files = dict(copied)
        if not reused:
            address = self.job+'/png-'+uuid.uuid4().hex
            stage = confined(self.runtime.project, address)
            with self.runtime.exports.guard(self.proof):
                OrganizedStorageLayout(str(self.runtime.project), self.budget).check_budget(address+'/frame_99999999.png')
                stage.mkdir()
            metadata = dict(h3_run_name=self.runtime.run, h3_clip_index=str(self.index),
                            h3_png_bit_depth=str(self.config['png_bit_depth']), h3_prompt=str(self.source.get('prompt') or ''))
            if self.config['embed_workflow']:
                metadata.update(self.archive_tags)
                metadata['h3_source_manifest'] = json.dumps(self.manifest, ensure_ascii=False)
                metadata['h3_upscale_profile'] = json.dumps(self.incoming['profile_config'], ensure_ascii=False)
            encoded = png.encode_scene(self.chain, self.path, self.source['raw_frames'], self.source['delivered_frames'],
                self.config, self.chain._png_export_worker_count(workers), stage, first, metadata, png._file_identity(self.path))
            count = self.source['delivered_frames']
            clips.append(dict(encoded, index=self.index, id=self.source.get('id'), source_contract=self.contracts[self.index],
                source_revision=self.source.get('revision'), processing_owners=[self.owner] if self.owner else [],
                export_session=session, video_sha256=self.video_identity['sha256'], raw_frames=self.source['raw_frames'],
                delivered_frames=count, trim_frames=self.source['raw_frames']-count,
                first_frame_number=first, last_frame_number=first+count-1))
            new_files.update({item['file']:stage/item['file'] for item in encoded['files']})
        record = dict(format=png.FORMAT, settings=self.config, clips=clips,
            frame_count=sum(c['delivered_frames'] for c in clips), last_scene=clips[-1]['index'],
            complete=clips[-1]['index'] == self.incoming['end_clip'], source_manifest=self.manifest,
            audio='preserved by the upscale segment saver', _storage_export_id=export_id)
        family['variants'][ordinal] = directory
        if binding:
            family['sessions'][binding] = ordinal
        payloads = {}
        for name,path in new_files.items():
            digest, signature = _hash_file(path)
            payloads[name] = dict(path=path.relative_to(self.runtime.project).as_posix(), sha256=digest, size=signature[2])
        saved = dict(request_sha256=self.request_hash, directory=directory, export_id=export_id,
                     family=family, record=record, files=payloads, scopes=sorted(self.scopes), reused=reused)
        self._check_video()
        self._validate_prepared(saved)
        with self.runtime.exports.guard(self.proof):
            state._immutable(self.runtime.project, self.job+'/sequence-prepared.json',
                state._encode(dict(value=saved, sha256=state._hash(state._encode(saved)))), self.budget)

    def accepted(self):
        with self.runtime.exports.guard(self.proof):
            self._check_video()
            receipt = self.runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            snapshot = self.runtime.store.committed_snapshot(receipt)
            saved = state._decode(snapshot.read(self.witness))
            if saved.get('request_sha256') != self.request_hash:
                raise state.StateConflict('PNG operation was accepted for another input.')
            for clip in saved['record']['clips']:
                for item in clip['files']:
                    self.runtime.store.payload_path(snapshot, saved['directory']+'/'+item['file'], verify=True)
            with state._lock(self.runtime.project):
                self.runtime.store._acknowledge_commit()
            self.runtime.record_commit(receipt)
            return self._result(saved, snapshot)

    def publish(self):
        recovered = self.accepted()
        if recovered is not None:
            return recovered
        saved = self._prepared()
        if saved is None:
            raise ValueError('PNG sequence must be prepared before publication.')
        self._validate_prepared(saved)
        requests = []
        for name,item in saved['files'].items():
            if not re.fullmatch(r'frame_[0-9]{8,}\.png', name):
                raise ValueError('Invalid prepared PNG frame name.')
            path = confined(self.runtime.project, item['path'])
            digest, signature = _hash_file(path)
            if digest != item['sha256'] or signature[2] != item['size']:
                raise state.StateConflict('Prepared PNG sequence bytes changed.')
            requests.append(dict(address=saved['directory']+'/'+name, source=path,
                target='exports/png/'+saved['export_id']+'/'+name, scope=self.scope,
                operation_id=uuid.uuid5(uuid.UUID(self.operation), name).hex))
        with self.runtime.exports.guard(self.proof):
            pass
        def staged_frame(receipt):
            name = receipt['record']['address'].rsplit('/',1)[-1]
            expected = saved['files'][name]
            if any(receipt['record']['file'][key] != expected[key] for key in ('sha256','size')):
                raise state.StateConflict('PNG frame changed during staging.')
            self.chain._png_export_check_interrupted()
            if self.runtime.exports.after_stage:
                self.runtime.exports.after_stage('payload')
        staged = self.runtime.store.stage_payloads(requests, after_stage=staged_frame) if requests else []
        # Re-check referenced old frames after potentially long publication.
        # An editor changing a reused prefix must not be silently accepted.
        self._validate_prepared(saved)
        def change(data, immutable=False):
            return dict(data=state._encode(data), scope=self.scope, category='cuts', immutable=immutable)
        changes = {saved['directory']+'/export.json': change(saved['record']),
                   self.family_address: change(saved['family']), self.witness: change(saved, True)}
        index_address = saved['directory']+'/export.json'
        prior_index = self.runtime.base.state['documents'].get(index_address)
        if prior_index is not None:
            # Imported mutable controls retain their declared contract; a new
            # writer must not reclassify historical scope/category ownership.
            changes[index_address].update(scope=prior_index['scope'], category=prior_index['category'])
        # These small legacy-shaped controls make reverse recovery useful to
        # the normal exporter, not merely a copy of otherwise unreadable bytes.
        marker_address = saved['directory']+'/.png_variant.json'
        if saved['directory'] != self.base and marker_address not in self.runtime.base.state['documents']:
            changes[marker_address] = change(dict(format=png.variants.FORMAT, settings=self.config,
                prefix=None, reason='Organized PNG sequence variant; prefix already materialized.',
                first_changed_scene=self.index), True)
        binding = self._binding()
        if binding:
            selection = dict(directory=Path(saved['directory']).name)
            if self._read(binding) != selection:
                descriptor = self.runtime.base.state['documents'].get(binding)
                changes[binding] = dict(data=state._encode(selection), scope=descriptor['scope'] if descriptor else self.scope,
                                        category='cuts', immutable=False)
        catalogue = self._read(owners.RUNTIME_CATALOG)
        if catalogue is None:
            catalogue = self._read('png_exports.json') or dict(format=owners.FORMAT, run_name=self.runtime.run, directories=[])
        if (catalogue.get('format') != owners.FORMAT or catalogue.get('run_name') != self.runtime.run
                or not isinstance(catalogue.get('directories'), list)
                or any(not isinstance(value,str) for value in catalogue['directories'])):
            raise ValueError('Invalid PNG ownership catalogue; exports were retained.')
        logical = 'h3_chains/'+self.runtime.run+'/'+saved['directory']
        if logical not in catalogue['directories']:
            catalogue['directories'] = sorted(catalogue['directories']+[logical])
        prior = self.runtime.base.state['documents'].get(owners.RUNTIME_CATALOG)
        changes[owners.RUNTIME_CATALOG] = dict(data=state._encode(catalogue), scope=prior['scope'] if prior else 'exports:png_catalog',
                                               category='cuts', immutable=False)
        with self.runtime.exports.guard(self.proof):
            self._check_video()
            receipt = self.runtime.store.commit_artifacts(self.runtime.base, changes, staged,
                operation_id=self.operation, read_scopes=set(saved['scopes']) | self.scopes, after_stage=self.runtime.exports.after_stage)
            self.runtime.record_commit(receipt)
        return self._result(saved, self.runtime.accepted)

    def _result(self, saved, snapshot):
        directory = str(confined(self.runtime.project, 'exports/png/'+saved['export_id']))
        index = str(confined(self.runtime.project, snapshot.state['documents'][saved['directory']+'/export.json']['file']['path']))
        status = '%s PNG scene %d; %d sequence frames; %s -> %s; accepted index -> %s' % (
            'reused' if saved['reused'] else 'saved', self.index, saved['record']['frame_count'],
            saved['directory'].rsplit('/',1)[-1], directory, index)
        return {'ui': {'text':[status]}, 'result': (directory, saved['record']['frame_count'], status, '', self.video)}
