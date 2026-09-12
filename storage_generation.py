"""One accepted publication for a generated take on explicit test copies.

Encoders produce independent staging files. This domain port validates their
saved identities/hashes, stages short organized destinations, then accepts
media, immutable metadata/recovery and branch changes in one control commit.
It does not encode tensors or enable production/node-host access implicitly.
"""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_layout import OrganizedStorageLayout, storage_stage
    from .storage_project import payload_key
    from .branch_scope import current_branch
    from .project_ownership import project_write_guard
    from .storage_legacy import LegacyStoragePaths
    from .storage_resolver import confined
else:
    import storage_state as state
    from storage_layout import OrganizedStorageLayout, storage_stage
    from storage_project import payload_key
    from branch_scope import current_branch
    from project_ownership import project_write_guard
    from storage_legacy import LegacyStoragePaths
    from storage_resolver import confined

_ROLES = {'segment': ('video', 'segments', '.mp4'),
          'checkpoint': ('checkpoint', 'checkpoints', '.safetensors'),
          'prompt_file': ('prompt', 'segments', '.prompt.txt'),
          'generated_audio': ('audio', 'generated_audio', '.wav'),
          'blend_segment': ('overlap', 'blend_segments', '.mp4')}


class RuntimeGeneration:
    def __init__(self, runtime):
        self.runtime = runtime
        self.after_stage = None

    def require_write(self):
        runtime = self.runtime
        runtime.check()
        if not runtime.generation_writes or runtime.node_generation_write.get() is False:
            raise ValueError('Runtime binding is read-only; generation writes were not enabled.')
        if current_branch(runtime.run) != runtime.selected:
            raise ValueError('Scene publication belongs to a different runtime branch.')

    @contextmanager
    def _guard(self, proof):
        self.require_write()
        runtime = self.runtime
        if runtime.has_node_proof and proof != runtime.node_write_proof:
            raise ValueError('Scene publication cannot change the node ownership proof.')
        with project_write_guard(runtime.output, runtime.run, proof, 'publish a scene checkpoint'):
            yield

    def publish(self, metadata, files, *, archives, ownership_proof, operation_id, reference=None,
                continuation=None):
        """Publish one exact save; failures retain staging and previous state.

        ``files`` maps segment field names to independently encoded files.
        ``archives`` maps plan/workflow/api_prompt to exact JSON bytes for a
        base take, or is None for an ALT sharing already accepted archives.
        A retry must retain the same metadata, input pin, files and operation ID.
        """
        self.require_write()
        runtime = self.runtime
        operation = state._token(operation_id)
        metadata = copy.deepcopy(metadata)
        if not isinstance(metadata, dict) or metadata.get('run_name') != runtime.run:
            raise ValueError('Generated metadata belongs to a different project.')
        segment = metadata.get('segment')
        if not isinstance(segment, dict):
            raise ValueError('Generated metadata needs a segment record.')
        revision = state._token(segment.get('revision'))
        scene = segment.get('index')
        if type(scene) is not int or not 1 <= scene <= 9999 or not segment.get('id'):
            raise ValueError('Generated metadata needs a valid scene identity.')
        if segment.get('reference_cache') is not None:
            if __package__:
                from .storage_reference_cache import ReferenceAdoption
            else:
                from storage_reference_cache import ReferenceAdoption
            if (not isinstance(reference, ReferenceAdoption) or reference.runtime is not runtime
                    or reference.operation != operation):
                raise ValueError('Generated references require their own checked adoption in this transaction.')
        elif reference is not None:
            raise ValueError('Reference adoption must be declared by the saved scene.')
        stage = storage_stage(take_kind=segment.get('take_kind'))
        alternate = stage == 'alternate'
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        stem = 'clip_%04d.%s' % (scene, revision)
        metadata_address = 'checkpoints/'+stem+'.json'
        canonical = prefix+'checkpoints/clip_%04d.json' % scene
        output_prefix = 'h3_chains/'+runtime.run+'/'
        if segment.get('revision_metadata') not in (None, output_prefix+metadata_address):
            raise ValueError('Generated revision metadata address does not match its identity.')
        documents = runtime.base.state['documents']
        scopes = {'branch:'+runtime.selected}

        def watch(address):
            if address not in documents:
                raise state.StateConflict('Required scene dependency is not accepted: '+address)
            scopes.add(documents[address]['scope'])
            return runtime.base.read(address)

        previous = state._decode(watch(canonical)) if canonical in documents else None
        if alternate:
            base = previous.get('segment', {}) if isinstance(previous, dict) else {}
            if base.get('revision') != segment.get('alternate_of_revision'):
                raise state.StateConflict('Alternate scene no longer matches its active base revision.')
            if any(base.get(key) != segment.get(key) for key in ('id', 'raw_frames', 'delivered_frames')):
                raise ValueError('Alternate take must preserve its base scene identity and duration.')

        # Queued source revisions stay accepted and unchanged through publish.
        # The caller validates exact visual/audio selection before encoding; the
        # transaction fences its input branch and every declared source archive.
        predecessors = []
        if segment.get('predecessor_revision'):
            predecessors.append((scene-1, segment['predecessor_revision'],
                                 segment.get('predecessor_checkpoint_sha256')))
        for key in ('visual_context_source', 'visual_context_lead_source',
                    'audio_context_source', 'audio_context_lead_source'):
            if segment.get(key+'_revision'):
                hash_key = (key.removesuffix('_source')+'_checkpoint_sha256'
                            if '_lead_source' in key else key+'_checkpoint_sha256')
                predecessors.append((segment.get(key+'_scene'), segment[key+'_revision'],
                                     segment.get(hash_key)))
        for block in segment.get('visual_context_blocks', []):
            predecessors.append((block.get('source_scene'), block.get('source_revision'),
                                 block.get('source_checkpoint_sha256')))
        for index, token, digest in predecessors:
            if type(index) is not int or not 1 <= index < scene:
                raise ValueError('Generated context source must be a previous scene.')
            state._token(token)
            saved = state._decode(watch('checkpoints/clip_%04d.%s.json' % (index, token)))
            source = saved.get('segment', {})
            if source.get('index') != index or source.get('revision') != token:
                raise ValueError('Generated context source has inconsistent metadata.')
            if digest and source.get('checkpoint_sha256') != digest:
                raise state.StateConflict('Generated context checkpoint hash no longer matches.')
            address = runtime.reader.address(source['checkpoint'])
            watch(payload_key(address))
            runtime.store.payload_path(runtime.base, address, verify=True)

        controls = {}
        def control(address, raw, scope, category, immutable):
            if not isinstance(raw, bytes):
                raise ValueError('Scene controls require exact serialized bytes.')
            controls[address] = dict(data=raw, scope=scope, category=category, immutable=immutable)

        saved_archives = metadata.get('archives')
        if not isinstance(saved_archives, dict):
            raise ValueError('Generated metadata needs explicit recovery archives.')
        if alternate:
            if archives is not None:
                raise ValueError('Alternate saves share accepted recovery archives; do not promote new authoring.')
            for key, address in saved_archives.items():
                if key not in ('plan', 'workflow', 'api_prompt'):
                    raise ValueError('Unsupported recovery archive role.')
                watch(runtime.reader.address(address))  # opaque hash-verified bytes
        else:
            if (not isinstance(archives, dict) or 'plan' not in archives
                    or set(archives) != set(saved_archives)
                    or set(archives)-{'plan', 'workflow', 'api_prompt'}):
                raise ValueError('Base saves require their exact recovery Plan and matching archive roles.')
            for key, raw in archives.items():
                address = 'recovery_archives/'+revision+'/'+key+'.json'
                if saved_archives[key] != output_prefix+address:
                    raise ValueError('Recovery archive does not belong to the generated revision.')
                if not isinstance(raw, bytes) or not isinstance(json.loads(raw), dict):
                    raise ValueError('Recovery archive must be a serialized JSON object.')
                control(address, raw, 'archive:'+revision, 'takes', True)
                control(prefix+key+'.json', raw, 'branch:'+runtime.selected, 'branches', False)

        expected_keys = {key for key in _ROLES if segment.get(key)}
        if (not isinstance(files, dict) or set(files) != expected_keys
                or not {'segment', 'checkpoint', 'prompt_file'} <= expected_keys):
            raise ValueError('Encoded files must exactly match the saved scene payload roles.')
        layout = OrganizedStorageLayout(str(runtime.project))
        storage_id = uuid.uuid5(uuid.UUID(operation), 'take:'+revision).hex
        destinations = layout.media(stage, storage_id)
        destinations['prompt'] = layout.take_prompt(storage_id)
        reserved = []
        for key in sorted(files):
            role, folder, suffix = _ROLES[key]
            address = folder+'/'+stem+suffix
            if segment[key] != output_prefix+address:
                raise ValueError('Generated payload does not belong to its revision: '+key)
            digest = segment.get(key+'_sha256')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('Generated payload requires its exact SHA-256: '+key)
            reserved.append((key, address, Path(files[key]), destinations[role], digest))

        # Freeze all intended control bytes before I/O or callbacks. Encode only
        # scene authority strictly; saved workflow extensions remain opaque.
        raw_metadata = state._encode(metadata)
        control(metadata_address, raw_metadata, 'archive:'+revision, 'takes', True)
        transition_address = None
        if continuation is not None:
            if __package__:
                from .storage_continuation import FORMAT, witness_address
            else:
                from storage_continuation import FORMAT, witness_address
            continuation = copy.deepcopy(continuation)
            if (continuation.get('format') != FORMAT
                    or state._encode(continuation.get('input_pin')) != state._encode(runtime.pin)
                    or type(continuation.get('scene')) is not int or continuation['scene'] != scene):
                raise ValueError('Scene-save continuation does not match its input pin and scene.')
            for key in ('state_sha256', 'ownership_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', str(continuation.get(key, ''))):
                    raise ValueError('Scene-save continuation requires exact input digests.')
            continuation['metadata_sha256'] = state._hash(raw_metadata)
            transition_address = witness_address(operation)
            control(transition_address, state._encode(continuation), 'jobs:'+operation, 'jobs', True)
        if not alternate:
            control(canonical, raw_metadata, 'branch:'+runtime.selected, 'branches', False)
            editorial_address = prefix+'editorial.json'
            if editorial_address in documents:
                editorial = state._decode(watch(editorial_address))
                replacements = editorial.get('replacements', [])
                if not isinstance(replacements, list):
                    raise ValueError('Saved editorial replacements must be a list.')
                stale = lambda item: (isinstance(item, dict) and item.get('scene') == scene
                    and (item.get('scene_id') != segment['id'] or item.get('base_revision') != revision))
                retained = [item for item in replacements if not stale(item)]
                draft_stale = stale(editorial.get('alternate_draft'))
                if retained != replacements or draft_stale:
                    editorial['replacements'] = retained
                    if draft_stale:
                        editorial['alternate_draft'] = None
                    editorial['revision'] = uuid.uuid5(uuid.UUID(operation), 'editorial').hex
                    editorial['updated_at'] = str(segment.get('created_at') or '')
                    control(editorial_address, state._encode(editorial),
                            'branch:'+runtime.selected, 'branches', False)

        # No project-wide mutation lock during encoding/staging. Proof is
        # checked before staging and again at the short acceptance boundary.
        with self._guard(ownership_proof):
            pass
        staged = []
        for key, address, path, target, digest in reserved:
            receipt = runtime.store.stage_payload(address, path, target,
                scope='archive:'+revision, operation_id=uuid.uuid5(uuid.UUID(operation), key).hex)
            if receipt['record']['file']['sha256'] != digest:
                raise state.StateConflict('Encoded payload differs from its saved hash: '+key)
            staged.append(receipt)
            if self.after_stage:
                self.after_stage('payload')
        if reference is not None:
            reference.stage(segment['reference_cache'], controls, staged, scopes, self.after_stage)
        with self._guard(ownership_proof):
            receipt = runtime.store.commit_artifacts(runtime.base, controls, staged,
                operation_id=operation, read_scopes=scopes, after_stage=self.after_stage)
            runtime.record_commit(receipt)
        result = dict(metadata=copy.deepcopy(metadata), storage_pin=runtime.output_pin,
                      revision_metadata=output_prefix+metadata_address)
        if transition_address is not None:
            result['transition'] = dict(receipt=copy.deepcopy(receipt), witness=transition_address)
        return result


class SceneSaveWorkspace:
    """Private encoder paths and exact logical identities for the actual saver.

    These are never accepted media destinations. Failed/uncertain operations
    retain their private files; no legacy cleanup may unlink a committed take.
    Publication still goes through RuntimeGeneration's checked transaction.
    """
    def __init__(self, runtime, scene, operation, input_state=None):
        runtime.generation.require_write()
        self.runtime, self.operation = runtime, state._token(operation)
        self.paths = LegacyStoragePaths(str(runtime.project),
            runtime.reader.working_directory(runtime.run)).generation(scene)
        canonical = runtime.reader.address(self.paths['metadata'])
        self.previous = (runtime.reader.read(self.paths['metadata'])
                         if canonical in runtime.base.state['documents'] else None)
        self.previous_revision = None
        if self.previous is not None:
            saved = self.previous.get('segment', {})
            token = state._token(saved.get('revision'))
            if saved.get('index') != scene:
                raise ValueError('Active scene metadata has an inconsistent scene identity.')
            archive = 'checkpoints/clip_%04d.%s.json' % (scene, token)
            # Do not invent or silently adopt a missing immutable base archive.
            immutable = state._decode(runtime.base.read(archive)).get('segment', {})
            if immutable != saved:
                raise state.StateConflict('Active scene and immutable archive disagree.')
            self.previous_revision = 'h3_chains/'+runtime.run+'/'+archive
        self.logical_paths = {}
        self.reference = None
        self.reference_source = None
        self.request = None
        self.continuation = None
        if input_state is not None:
            if __package__:
                from .storage_continuation import save_witness
            else:
                from storage_continuation import save_witness
            self.continuation = save_witness(runtime, input_state)

    def reserve(self, payloads):
        runtime = self.runtime
        layout = OrganizedStorageLayout(str(runtime.project))
        directory = confined(runtime.project, layout.project_data(
            'jobs', self.operation, 'encode-'+uuid.uuid4().hex))
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()  # exclusive allocation; never reuse an encoder job
        names = {'video': 'video.mp4', 'checkpoint': 'checkpoint.safetensors',
                 'prompt': 'prompt.txt', 'audio': 'audio.wav', 'overlap': 'overlap.mp4'}
        result = {}
        for role, logical in payloads.items():
            path = directory/names[role]
            layout.check_budget(path.relative_to(runtime.project).as_posix())
            result[role] = str(path)
            self.logical_paths[str(path)] = self.logical(logical)
        return result

    def logical(self, path):
        if str(path) in self.logical_paths:
            return self.logical_paths[str(path)]
        return self.runtime.reader.logical_output(path)

    def recovery(self, chain, plan, prompt, extra_pnginfo, *, alternate):
        runtime = self.runtime
        if alternate:
            saved = self.previous or {}
            archives = copy.deepcopy(saved.get('archives') or saved.get('segment', {}).get('archives') or {})
            if not archives:
                directory = runtime.reader.working_directory(runtime.run)
                for key in ('plan', 'workflow', 'api_prompt'):
                    logical = str(Path(directory)/(key+'.json'))
                    if runtime.reader.address(logical) in runtime.base.state['documents']:
                        archives[key] = self.logical(logical)
            documents = {key: json.loads(runtime.base.read(runtime.reader.address(path)))
                         for key, path in archives.items()}
            raw = None
        else:
            documents = chain._run_archive_documents(plan, prompt, extra_pnginfo,
                                                     rehearsal_view=runtime.reader)
            raw = {key: (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode('utf-8')
                   for key, value in documents.items()}
            archives = {key: 'h3_chains/'+runtime.run+'/recovery_archives/'+self.operation+'/'+key+'.json'
                        for key in documents}
        tags = {tag: json.dumps(documents[key], ensure_ascii=False, separators=(',', ':'))
                for key, tag in (('plan', 'h3_plan'), ('workflow', 'workflow'), ('api_prompt', 'prompt'))
                if key in documents}
        return archives, raw, tags

    def publish(self, metadata, payloads, archives, proof, *, prepare=True):
        if self.request is not None:
            self.continuation['save_inputs_sha256'] = self.request.digest
            if prepare:
                self.request.prepare(self, metadata, payloads, archives)
        fields = {key: payloads[role] for key, (role, _, _) in _ROLES.items() if role in payloads}
        return self.runtime.generation.publish(metadata, fields, archives=archives,
            ownership_proof=proof, operation_id=self.operation, reference=self.reference,
            continuation=self.continuation)

    def adopt_reference(self, metadata, scene):
        if __package__:
            from .storage_reference_cache import RuntimeReferenceCache
        else:
            from storage_reference_cache import RuntimeReferenceCache
        self.reference = RuntimeReferenceCache(self.runtime).prepare(metadata, scene, self.operation)
        self.reference_source = copy.deepcopy(metadata)
        return copy.deepcopy(self.reference.metadata)
