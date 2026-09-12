"""Atomic processing saves on explicitly authorized combined-storage copies.

The encoder writes private files; publication accepts payloads, immutable take
metadata, the resume pointer and a delivery snapshot together. Logical legacy
addresses remain stable, but no legacy profile directory is written or adopted.
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
    from .storage_layout import OrganizedStorageLayout, storage_stage
    from .storage_project import payload_key
    from .storage_resolver import confined
else:
    import storage_state as state
    from branch_scope import current_branch
    from project_ownership import project_write_guard
    from storage_layout import OrganizedStorageLayout, storage_stage
    from storage_project import payload_key
    from storage_resolver import confined

ROLES = {'segment': ('video', 'segments', '.mp4'),
         'checkpoint': ('checkpoint', 'checkpoints', '.safetensors'),
         'prompt_file': ('prompt', 'prompts', '.txt'),
         'generated_audio': ('audio', 'audio', '.wav')}


class RuntimeProcessing:
    def __init__(self, runtime):
        self.runtime = runtime
        self.after_stage = None

    def require_write(self):
        runtime = self.runtime
        runtime.check()
        if not runtime.processing_writes or runtime.node_processing_write.get() is False:
            raise ValueError('Runtime binding is read-only; processing writes were not enabled.')
        if current_branch(runtime.run) != runtime.selected:
            raise ValueError('Processing publication belongs to a different runtime branch.')

    @contextmanager
    def guard(self, proof):
        self.require_write()
        runtime = self.runtime
        if runtime.has_node_proof and proof != runtime.node_write_proof:
            raise ValueError('Processing publication cannot change the node ownership proof.')
        with project_write_guard(runtime.output, runtime.run, proof, 'publish a processing checkpoint'):
            yield

    def publish(self, workspace, metadata, files):
        self.require_write()
        runtime = self.runtime
        if not isinstance(workspace, ProcessingSaveWorkspace) or workspace.runtime is not runtime:
            raise ValueError('Processing publication requires its validated encoder workspace.')
        metadata = copy.deepcopy(metadata)
        incoming, upscale = workspace.incoming, workspace.upscale
        segment = metadata.get('segment', {})
        source = upscale._source_segment(incoming)
        index = incoming['index']
        revision = state._token(segment.get('revision'))
        if revision != workspace.operation or segment.get('index') != index:
            raise ValueError('Processing revision does not match its encoder operation.')
        if (metadata.get('format') != 'h3_chain_upscale_segment_v1'
                or metadata.get('run_name') != runtime.run
                or metadata.get('profile') != incoming['profile']
                or metadata.get('profile_config') != incoming['profile_config']
                or metadata.get('profile_config_hash') != incoming['profile_config']['config_hash']
                or metadata.get('source_manifest_hash') != incoming['source_manifest_hash']):
            raise ValueError('Processing metadata differs from its input profile/source.')
        upscale._verify_upscale_source(metadata, source, index)
        range_start = (incoming['source_manifest'].get('upscale_range') or {}).get('scene_start')
        if metadata.get('upscale_range_start') != range_start:
            raise ValueError('Processing metadata belongs to a different scene range.')
        expected = {key for key in ROLES if segment.get(key)}
        if set(files) != expected or not {'segment', 'checkpoint', 'prompt_file'} <= expected:
            raise ValueError('Processing files must exactly match their declared payload roles.')
        prefix = 'h3_chains/'+runtime.run+'/'
        stem = 'clip_%04d.%s' % (index, revision)
        address = workspace.profile+'/checkpoints/'+stem+'.json'
        canonical = runtime.reader.address(workspace.paths['metadata'])
        if (segment.get('revision_metadata') != prefix+address
                or segment.get('metadata') != prefix+canonical):
            raise ValueError('Processing metadata address does not match its scene/profile.')
        if segment.get('supersedes') != workspace.previous_revision:
            raise ValueError('Processing supersedes pointer differs from the pinned profile.')
        from_module = __package__+'.checkpoint_variants' if __package__ else 'checkpoint_variants'
        import importlib
        lineage = importlib.import_module(from_module).processing_lineage
        segments = list(incoming['segments']) + [segment]
        if metadata.get('processing_lineage') != lineage(segments):
            raise ValueError('Processing lineage differs from its saved prefix.')
        complete = index == upscale._source_bounds(incoming['source_manifest'])[1]
        manifest = upscale._upscale_manifest(incoming, segments, complete)
        if __package__:
            from .storage_processing_continuation import source_document, witness_address
        else:
            from storage_processing_continuation import source_document, witness_address
        manifest['source_manifest'] = source_document(manifest['source_manifest'])
        manifest_address = workspace.profile+'/partial/through_clip_%04d.%s.manifest.json' % (index, revision)
        controls = {}

        def control(name, value, immutable):
            controls[name] = dict(data=state._encode(value), scope=workspace.scope,
                                  category='passes', immutable=immutable)

        control(address, metadata, True)
        control(canonical, metadata, False)
        # Imported partial manifests may be immutable. Never overwrite them;
        # every new delivery has its own revision and an explicit latest pointer.
        control(manifest_address, manifest, True)
        control(workspace.profile+'/latest_manifest.json', manifest, False)
        if complete:
            target = workspace.profile+'/upscale_manifest.json'
            prior = runtime.base.state['documents'].get(target)
            if prior is None or not prior['immutable']:
                control(target, manifest, False)
        pass_document = dict(format='h3_storage_processing_pass_v1',
            branch_id=runtime.selected, profile=workspace.profile, stage=workspace.stage,
            config_hash=incoming['profile_config']['config_hash'], pass_id=workspace.pass_id)
        control(workspace.profile+'/passes/'+workspace.pass_id+'.json', pass_document, True)
        if workspace.witness is None:
            raise ValueError('Processing publication requires its encoder input witness.')
        witness = dict(workspace.witness, metadata_sha256=state._hash(state._encode(metadata)),
                       manifest_address=manifest_address)
        witness_path = witness_address(revision)
        controls[witness_path] = dict(data=state._encode(witness), scope='jobs:'+revision,
                                      category='jobs', immutable=True)

        layout = OrganizedStorageLayout(str(runtime.project))
        targets = layout.media(workspace.stage, revision, pass_id=workspace.pass_id)
        targets['prompt'] = layout.take_prompt(revision)
        reserved = []
        for key in sorted(files):
            role, folder, suffix = ROLES[key]
            logical = workspace.profile+'/'+folder+'/'+stem+suffix
            if segment[key] != prefix+logical:
                raise ValueError('Processing payload address differs from its revision: '+key)
            digest = segment.get(key+'_sha256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('Processing payload requires an exact SHA-256: '+key)
            path = Path(files[key])
            if workspace.encoded.get(role) != str(path):
                raise ValueError('Processing payload is outside its encoder workspace.')
            reserved.append((key, logical, path, targets[role], digest))

        with self.guard(workspace.proof):
            pass
        staged = []
        for key, logical, path, target, digest in reserved:
            saved = runtime.store.stage_payload(logical, path, target,
                scope=workspace.scope, operation_id=uuid.uuid5(uuid.UUID(revision), key).hex)
            if saved['record']['file']['sha256'] != digest:
                raise state.StateConflict('Encoded processing file changed: '+key)
            staged.append(saved)
            if self.after_stage:
                self.after_stage('payload')
        with self.guard(workspace.proof):
            receipt = runtime.store.commit_artifacts(runtime.base, controls, staged,
                operation_id=revision, read_scopes=workspace.scopes, after_stage=self.after_stage)
            runtime.record_commit(receipt)
        return dict(metadata=metadata, manifest=manifest, manifest_path=prefix+manifest_address,
                    receipt=receipt, witness=witness_path, storage_pin=runtime.output_pin)


class ProcessingSourceDependencies:
    """Read-only source validation shared by processing and export writers.

    Constructing a verifier never grants permission to save a processing take.
    Its exact source scopes become dependencies of the caller's publication.
    """
    def __init__(self, runtime, upscale):
        self.runtime, self.upscale = runtime, upscale
        self.scopes = set()

    def read(self, path, *, immutable=False):
        address = self.runtime.reader.address(path)
        descriptor = self.runtime.base.state['documents'].get(address)
        if descriptor is None or (immutable and not descriptor['immutable']):
            raise ValueError('Processing dependency requires accepted immutable metadata: '+address)
        self.scopes.add(descriptor['scope'])
        return state._decode(self.runtime.base.read(address))

    def verify_files(self, segment):
        for key in ROLES:
            if not segment.get(key):
                continue
            address = self.runtime.reader.address(segment[key])
            documents = self.runtime.base.state['documents']
            descriptor = documents.get(payload_key(address)) or documents.get(address)
            if descriptor is None:
                raise ValueError('Processing source file is not accepted: '+address)
            self.scopes.add(descriptor['scope'])
            path = self.runtime.reader.path(segment[key])
            if self.upscale.chain._file_sha256(str(path)) != segment.get(key+'_sha256'):
                raise ValueError('Processing source file failed SHA-256 verification: '+key)

    def verify_source(self, source, depth=0):
        if depth > 8:
            raise ValueError('Processing source ancestry is cyclic or too deep.')
        chain = self.upscale.chain
        metadata = self.read(source['revision_metadata'], immutable=True)
        saved = metadata.get('segment', {})
        if saved.get('revision') != source.get('revision') or saved.get('index') != source.get('index'):
            raise ValueError('Processing source differs from its immutable revision.')
        self.verify_files(saved)
        expected = dict(saved)
        processing, presentation = source.get('processing_source'), source.get('presentation_source')
        if processing:
            if presentation or processing.get('stage') != 'derope':
                raise ValueError('Invalid nested processing source route.')
            original = self.verify_source(processing['original'], depth+1)
            if (saved.get('source_revision') not in (original.get('revision'), original.get('adopted_from_revision'))
                    or saved.get('source_checkpoint_sha256') != original.get('checkpoint_sha256')
                    or storage_stage(profile_config=metadata.get('profile_config')) != 'derope'):
                raise ValueError('Processing source does not belong to its declared original.')
            profile = str(Path(source['revision_metadata']).parent.parent)
            if processing.get('profile_path') != profile:
                raise ValueError('Processing source profile differs from its saved revision.')
            expected = dict(original)
            for key in ('blend_segment', 'blend_segment_sha256', 'blend_frames',
                        'generated_audio', 'generated_audio_sha256', 'resolution', 'presentation_source'):
                expected.pop(key, None)
            expected.update(saved)
            expected['processing_source'] = copy.deepcopy(processing)
        elif presentation:
            original = self.verify_source(presentation['original'], depth+1)
            if (presentation.get('mode') != 'picture_only' or saved.get('take_kind') != 'editorial_alternate'
                    or saved.get('alternate_of_revision') != original.get('revision')
                    or any(saved.get(k) != original.get(k) for k in ('index', 'id', 'raw_frames', 'delivered_frames'))):
                raise ValueError('Processing ALT source does not match its original audio owner.')
            compatibility = metadata.get('compatibility') or {}
            geometry = chain.saved_resolution(saved) or compatibility
            if geometry.get('width') and geometry.get('height'):
                expected['resolution'] = {k:int(geometry[k]) for k in ('width','height')}
            expected['generation_fingerprint'] = str(compatibility.get('generation_fingerprint') or '')
            expected['sample_rate'] = original.get('sample_rate', 0)
            expected['presentation_source'] = copy.deepcopy(presentation)
        if self.upscale._upscale_source_contract(expected) != self.upscale._upscale_source_contract(source):
            raise ValueError('Processing source timing/settings differ from its immutable metadata.')
        for key in ROLES:
            if source.get(key) != saved.get(key):
                raise ValueError('Processing source artifact address differs from its immutable metadata.')
        return source


class ProcessingSaveWorkspace(ProcessingSourceDependencies):
    def __init__(self, runtime, incoming, upscale, operation):
        runtime.processing.require_write()
        self.runtime, self.upscale, self.operation = runtime, upscale, state._token(operation)
        # Do not copy transient image/latent/model state, which can be many GiB.
        fields = ('run_name', 'profile', 'profile_config', 'source_manifest', 'source_manifest_hash',
                  'index', 'segments', 'end_clip', 'range_start', 'start_mode')
        self.incoming = copy.deepcopy({key: incoming[key] for key in fields if key in incoming})
        self.proof = copy.deepcopy(incoming.get('_project_ownership',
                                  incoming.get('source_manifest', {}).get('_project_ownership')))
        if incoming.get('_storage_pin') != runtime.pin or incoming.get('run_name') != runtime.run:
            raise ValueError('Processing save requires its exact project/input pin.')
        self.scopes = set()
        self.paths = upscale._state_profile_paths(self.incoming, incoming['index'])
        self.profile = runtime.reader.address(self.paths['root'])
        branch_prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        pattern = re.escape(branch_prefix)+r'(?:chapters/[^/]+/)?upscaled/[^/]+'
        if re.fullmatch(pattern, self.profile) is None:
            raise ValueError('Processing profile is outside its selected branch.')
        config = self.incoming['profile_config']
        chain = upscale.chain
        if self.incoming['source_manifest_hash'] != upscale._source_hash(self.incoming['source_manifest']):
            raise ValueError('Processing source manifest changed after its adapter.')
        if config.get('config_hash') != chain._fingerprint({k:v for k,v in config.items() if k != 'config_hash'}):
            raise ValueError('Processing profile config hash differs from its settings.')
        self.stage = storage_stage(profile_config=config)
        default_scope = 'pass:'+state._hash(state._encode([runtime.selected, self.profile]))[:32]
        contracts = {d['scope'] for p,d in runtime.base.state['documents'].items()
                     if p.startswith(self.profile+'/') and d['category'] == 'passes'}
        if len(contracts) > 1 or any(not scope.startswith('pass:') for scope in contracts):
            raise ValueError('Processing profile has conflicting accepted ownership scopes.')
        self.scope = next(iter(contracts), default_scope)
        self.scopes.add(self.scope)
        self.pass_id = state._hash(state._encode([runtime.selected, self.profile, config['config_hash']]))[:32]
        first, last = upscale._source_bounds(self.incoming['source_manifest'])
        index = self.incoming['index']
        if type(index) is not int or not first <= index <= self.incoming['end_clip'] <= last:
            raise ValueError('Processing save is outside its selected scene range.')
        if [s.get('index') for s in self.incoming['segments']] != list(range(first, index)):
            raise ValueError('Processing prefix is not contiguous from its selected range.')
        # Verify every selected source against accepted immutable metadata, not
        # just media hashes: a forged seed/prompt must not become saved provenance.
        for source in self.incoming['source_manifest']['segments']:
            self.verify_source(source)
        for segment in self.incoming['segments']:
            metadata = self.read(segment['revision_metadata'], immutable=True)
            if upscale._public_upscale_segment(metadata['segment']) != upscale._public_upscale_segment(segment):
                raise ValueError('Processing prefix differs from its immutable saved metadata.')
            if metadata.get('profile_config_hash') != config['config_hash']:
                raise ValueError('Processing prefix has different profile settings.')
            upscale._verify_upscale_source(metadata, upscale._source_segment(self.incoming, segment['index']), segment['index'])
            self.verify_files(segment)
        canonical = runtime.reader.address(self.paths['metadata'])
        self.previous = self.read(self.paths['metadata']) if canonical in runtime.base.state['documents'] else None
        self.previous_revision = None
        if self.previous is not None:
            segment = self.previous['segment']
            saved = self.read(segment['revision_metadata'], immutable=True)
            if saved != self.previous:
                raise ValueError('Processing resume pointer differs from its immutable metadata.')
            self.previous_revision = segment['revision_metadata']
        self.encoded, self.logical_paths = {}, {}
        self.witness = None
        self.request = None
        with runtime.processing.guard(self.proof):
            pass

    def reserve(self, payloads):
        layout = OrganizedStorageLayout(str(self.runtime.project))
        directory = confined(self.runtime.project, layout.project_data(
            'jobs', self.operation, 'encode-'+uuid.uuid4().hex))
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()  # exclusive private job; never overwrite an old encoder
        names = dict(video='video.mp4', checkpoint='checkpoint.safetensors', prompt='prompt.txt', audio='audio.wav')
        for role, logical in payloads.items():
            path = directory/names[role]
            layout.check_budget(path.relative_to(self.runtime.project).as_posix())
            self.encoded[role] = str(path)
            self.logical_paths[str(path)] = self.logical(logical)
        return dict(self.encoded)

    def logical(self, path):
        return self.logical_paths.get(str(path)) or self.runtime.reader.logical_output(path)

    def publish(self, metadata, *, prepare=True):
        if self.request is not None and prepare:
            self.request.prepare(self, metadata)
        files = {key:self.encoded[role] for key,(role,_,_) in ROLES.items() if role in self.encoded}
        return self.runtime.processing.publish(self, metadata, files)
