"""Ownership-checked generation retirement on explicit combined-store copies.

Domain previews classify references before invoking the reversible transaction
kernel. No unlink, directory scan of physical storage slots, or permanent purge
occurs here. Processing/PNG retirement is a separate domain, not inferred from
permission to delete a generation checkpoint.
"""
import copy
from contextlib import contextmanager
import re
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project_reads import ProjectReadView
    from .storage_quarantine import ProjectQuarantine, _address, RECEIPT, STATUS
    from .checkpoint_manager import CheckpointGraphManager, CheckpointDeleteBlocked
    from .project_ownership import project_write_guard
    from .branch_scope import current_branch
    from .artifact_paths import artifact_address
else:
    import storage_state as state
    from storage_project_reads import ProjectReadView
    from storage_quarantine import ProjectQuarantine, _address, RECEIPT, STATUS
    from checkpoint_manager import CheckpointGraphManager, CheckpointDeleteBlocked
    from project_ownership import project_write_guard
    from branch_scope import current_branch
    from artifact_paths import artifact_address

FORMAT = 'h3_generation_retirement_v1'
PROCESSING_FORMAT = 'h3_processing_retirement_v1'
CHAPTER_FORMAT = 'h3_chapter_retirement_v1'
_NAMESPACE = uuid.UUID('8cf2dbb1-b95d-449e-9e66-ce2ee3f7817e')
_POINTER = re.compile(r'(?:branches/([0-9a-f]{32})/)?checkpoints/clip_(\d{4})\.json')
_EDIT = re.compile(r'(?:branches/([0-9a-f]{32})/)?editorial\.json')
_PROCESSING = re.compile(r'(?:branches/[0-9a-f]{32}/)?(?:chapters/[^/]+/)?upscaled/[^/]+/(.+)')
_PROCESSING_FORMATS = {'h3_chain_upscale_segment_v1', 'h3_chain_upscale_manifest_v1',
                       'h3_chain_upscale_partial_manifest_v1'}
_OPAQUE = {'execution', 'prompt', 'scene_prompt', 'scene_prompt_template', 'compiled_prompt',
           'prompt_prefix', 'negative', 'positive', 'workflow', 'api_prompt', 'supersedes'}


def _operation(action, branch, identity, snapshot):
    if not isinstance(snapshot, str) or not re.fullmatch('[0-9a-f]{64}', snapshot):
        raise ValueError('Confirm an exact retention preview before changing saved files.')
    return uuid.uuid5(_NAMESPACE, state._encode([action, branch, identity, snapshot]).decode()).hex


def _mentions_source(value, scene, revision, addresses, *, source=False, identity_addresses=None):
    """Saved source identities/paths, not opaque prompts or execution graphs.

An identically named processed revision in another profile is not this
generation revision. When an address is present it must identify our artifact.
"""
    def owns(raw):
        if not isinstance(raw, str):
            return False
        try:
            return artifact_address(raw) in addresses
        except ValueError:
            return False
    def matches(hints):
        # A matching revision with an unreadable address is unknown, not
        # evidence that the saved processing source belongs elsewhere.
        normalized = [artifact_address(hint) for hint in hints]
        return any(hint in (addresses if identity_addresses is None else identity_addresses) for hint in normalized)
    if isinstance(value, dict):
        if value.get('source_revision') == revision:
            hints = [value[k] for k in ('source_checkpoint', 'source_metadata') if value.get(k)]
            if not hints or matches(hints):
                return True
        if value.get('revision') == revision and value.get('index', value.get('scene')) == scene:
            hints = [value[k] for k in ('revision_metadata', 'metadata_path', 'checkpoint', 'segment')
                     if value.get(k)]
            if matches(hints) or (source and not hints):
                return True
        return any(_mentions_source(item, scene, revision, addresses,
                   source=source or key in ('source_manifest', 'original', 'source_segment'),
                   identity_addresses=identity_addresses)
                   for key, item in value.items() if key not in _OPAQUE)
    if isinstance(value, list):
        return any(_mentions_source(item, scene, revision, addresses, source=source,
                                    identity_addresses=identity_addresses) for item in value)
    return owns(value)


class RuntimeRetention:
    def __init__(self, runtime):
        self.runtime = runtime
        self.kernel = ProjectQuarantine(runtime.store)
        self.after_stage = None

    def check(self):
        self.runtime.check()
        if current_branch(self.runtime.run) != self.runtime.selected:
            raise ValueError('Retention belongs to a different runtime branch.')

    @contextmanager
    def guard(self, proof):
        self.check()
        runtime = self.runtime
        if not runtime.retention_writes or runtime.node_retention_write.get() is False:
            raise ValueError('Runtime binding is read-only; retention writes were not enabled.')
        if runtime.has_node_proof and proof != runtime.node_write_proof:
            raise ValueError('Retention cannot change the node ownership proof.')
        with project_write_guard(runtime.output, runtime.run, proof, 'quarantine or restore saved checkpoints'):
            self.check()
            yield

    def _build_generation(self, scene, revision, base):
        self.check()
        if type(scene) is not int or scene < 1:
            raise ValueError('A positive integer scene number is required.')
        revision = state._token(revision)
        runtime = self.runtime
        view = ProjectReadView(runtime.store, base=base)
        manager = CheckpointGraphManager(runtime.output, rehearsal_view=view)
        references, blockers = [], []
        def reference(kind, source, reason, *, blocking=True):
            references.append(dict(kind=kind, source=source, reason=reason, blocking=blocking))
            if blocking:
                blockers.append(reason)
        with view.operation():
            scan = manager._scan(runtime.run, adopt_legacy=False)
            key = (scene, revision)
            record = scan['records'].get(key)
            if record is None:
                raise FileNotFoundError('The selected checkpoint revision is no longer in the catalogue.')
            artifacts = manager._artifacts(scan, record)
            addresses = {item['path'] for item in artifacts}
            owned_artifacts = [item for item in artifacts if item['owned']]
            retiring_addresses = {item['path'] for item in owned_artifacts}
            start, end = manager._chapter_bounds(scan, scene)
            dependents = []
            for child_key in sorted(manager._descendant_keys(scan['records'], key)):
                child = scan['records'][child_key]
                dependents.append(dict(scene=child['scene'], revision=child['revision']))
                reference('required_input', child['revision'],
                          'Saved scene %d revision %s requires this checkpoint.' % (child['scene'], child['revision'][:8]))
            chapters = manager._chapter_references(scan, revision, owned_artifacts)
            for chapter in chapters:
                reference('frozen_cut_pin', chapter.get('path', ''), chapter.get('error') or
                          'Sealed chapter snapshot %s requires this checkpoint.' % chapter['snapshot'][:8])
            own_pointer = None
            documents = base.state['documents']
            assignments = {}
            for address in sorted(documents):
                match = _POINTER.fullmatch(address)
                if match:
                    branch, index = match[1] or 'main', int(match[2])
                    saved = state._decode(base.read(address)).get('segment')
                    if not isinstance(saved, dict) or not saved.get('revision'):
                        raise ValueError('Cannot verify saved branch assignment: '+address)
                    assignments[(branch, index)] = saved['revision']
                    if index == scene and saved['revision'] == revision:
                        own = branch == runtime.selected
                        if own:
                            own_pointer = address
                        reference('selected_assignment', address,
                            'The selected branch pointer will be cleared.' if own else
                            'Retained working branch %s still selects this revision.' % branch,
                            blocking=not own)
                processing = _PROCESSING.fullmatch(address)
                if processing and (re.fullmatch(r'checkpoints/clip_\d{4}(?:\.[0-9a-f]{32})?\.json', processing[1])
                                   or processing[1].endswith('manifest.json')):
                    document = state._decode(base.read(address))
                    if (not isinstance(document, dict) or document.get('format') not in _PROCESSING_FORMATS
                            or document.get('run_name') != runtime.run
                            or (document.get('format') == 'h3_chain_upscale_segment_v1'
                                and not isinstance(document.get('segment'), dict))
                            or (document.get('format') != 'h3_chain_upscale_segment_v1'
                                and not isinstance(document.get('segments'), list))):
                        reference('unknown', address, 'Cannot verify processing recovery metadata: '+address)
                    else:
                        try:
                            required = _mentions_source(document, scene, revision, retiring_addresses,
                                                        identity_addresses=addresses)
                        except ValueError:
                            reference('unknown', address, 'Cannot resolve saved processing source identity: '+address)
                        else:
                            if required:
                                reference('processing_dependency', address,
                                          'Saved processing output requires this generation source: '+address)
            for address in sorted(documents):
                edit = _EDIT.fullmatch(address)
                if not edit:
                    continue
                branch = edit[1] or 'main'
                editorial = state._decode(base.read(address))
                replacements = editorial.get('replacements', []) if isinstance(editorial, dict) else None
                if not isinstance(replacements, list):
                    raise ValueError('Cannot verify saved editorial selection: '+address)
                for item in replacements:
                    if not isinstance(item, dict):
                        raise ValueError('Cannot verify saved editorial selection: '+address)
                    if (item.get('scene') == scene and item.get('base_revision') == assignments.get((branch, scene))
                            and revision in (item.get('base_revision'), item.get('alternate_revision'))):
                        reference('selected_assignment', address,
                                  'A retained final-cut selection requires this revision; change that selection first.')
            if own_pointer and not record['active']:
                reference('selected_assignment', own_pointer,
                          'This pointer is outside the active lineage; assign the intended branch before deleting it.')
            later = [index for (branch,index) in assignments if branch == runtime.selected and scene < index <= end]
            if own_pointer and later:
                reference('selected_assignment', own_pointer,
                          'A later active scene pointer exists at scene %d.' % max(later))
            rollback = bool(own_pointer and not blockers)
            public_files = [{k:v for k,v in item.items() if not k.startswith('_')} for item in artifacts]
            if rollback:
                descriptor = documents[own_pointer]
                public_files.append(dict(kind='active_pointer', label='Selected scene pointer (cleared)',
                    path='h3_chains/'+runtime.run+'/'+own_pointer, exists=True,
                    size_bytes=descriptor['file']['size'], shared=False, owned=True))
            for item in public_files:
                if item['shared'] or not item['owned']:
                    reference('shared_artifact', item['path'], 'Shared or unattributed file remains available.', blocking=False)
                elif not item['exists']:
                    reference('unavailable_artifact', item['path'],
                              'An owned artifact is unavailable; integrity recovery is required before quarantine.')
            for other in scan['records'].values():
                if other['_segment'].get('supersedes') == revision:
                    reference('historical_only', other['revision'], 'Supersedes records history, not an input.', blocking=False)
            reference('recovery_pin', '', 'Existing accepted pins and undo retain immutable file bytes.', blocking=False)
            reference('external_compatibility_pin', '', 'Legacy compatibility copies are not removed.', blocking=False)
            owned = [item for item in public_files if item['owned'] and item['exists']]
            reason = state._encode(dict(format=FORMAT, branch_id=runtime.selected,
                                        scene=scene, revision=revision)).decode()
            transaction = self.kernel.preview(base,
                [item['path'].removeprefix('h3_chains/'+runtime.run+'/') for item in owned],
                reason=reason) if not blockers else None
            public = dict(ok=True, format=FORMAT, run_name=runtime.run, scene=scene, revision=revision,
                active=record['active'], rollback=rollback, rollback_to_scene=scene-1 if rollback else None,
                scope_start_scene=start, scope_end_scene=end, allowed=not blockers,
                blockers=list(dict.fromkeys(blockers)), references=references, dependents=dependents,
                chapter_references=chapters, files=public_files, owned_file_count=len(owned),
                reclaimed_bytes=0, quarantined_bytes=sum(item['size_bytes'] for item in owned),
                storage_pin=dict(runtime.pin, root=base.reference),
                not_deleted=['Shared files', 'Old accepted pins', 'Legacy compatibility copies',
                             'Assembled exports', 'Project assets and prompt history'])
            public['snapshot'] = state._hash(state._encode(dict(preview=public,
                transaction_sha256=transaction['sha256'] if transaction else None)))
            return public, transaction

    def preview_generation(self, scene, revision):
        return self._build_generation(scene, revision, self.runtime.accepted)[0]

    def _build_processing(self, address, base):
        self.check()
        from .storage_processing_retention import ProcessingSnapshot
        public, files, updates, exceptions = ProcessingSnapshot(self.runtime, base).preview(address)
        public = {key:value for key,value in public.items() if not key.startswith('_') and key != 'snapshot'}
        reason = state._encode(dict(format=PROCESSING_FORMAT, branch_id=self.runtime.selected,
            scene=public['scene'], revision=public['revision'], metadata_path=public['metadata_path'])).decode()
        descriptors = base.state['documents']
        transaction = self.kernel.preview(base, files, reason=reason, updates=updates, payload_exceptions=exceptions,
            replace_immutable=[address for address in updates if descriptors[address]['immutable']]
            ) if public['allowed'] else None
        public.update(format=PROCESSING_FORMAT, storage_pin=dict(self.runtime.pin, root=base.reference),
            quarantined_bytes=public['reclaimed_bytes'], reclaimed_bytes=0,
            control_updates=sorted(updates), references=[dict(kind='processing_dependency',
                source=item['metadata_path'], reason=item['reason'], blocking=True) for item in public['dependents']])
        public['not_deleted'].extend(['Immutable bytes retained for undo and old accepted pins', 'Legacy compatibility copies'])
        public['payload_conditions'] = [dict(path=item['address'], **item['custody'])
            for item in (transaction or {}).get('items', []) if 'custody' in item]
        if public['payload_conditions']:
            public['not_deleted'].append('Recorded output hashes are unchanged; undo preserves edited/missing state, not lost bytes')
        public['snapshot'] = state._hash(state._encode(dict(preview=public,
            transaction_sha256=transaction['sha256'] if transaction else None)))
        return public, transaction

    def preview_processing(self, metadata_path):
        return self._build_processing(artifact_address(metadata_path), self.runtime.accepted)[0]

    def delete_processing(self, metadata_path, expected_snapshot, *, proof):
        address = artifact_address(metadata_path)
        operation = _operation('processing', self.runtime.selected, address, expected_snapshot)
        with self.guard(proof):
            accepted = self._accepted_record(operation)
            base = self.runtime.accepted
            if accepted is not None:
                domain = self._domain(accepted)
                if domain.get('format') != PROCESSING_FORMAT or domain.get('metadata_path') != address:
                    raise ValueError('Processing retirement operation has a different target.')
                base = state.Snapshot(self.runtime.project, accepted['preview']['base'])
            public, transaction = self._build_processing(address, base)
            if not public['allowed'] or public['snapshot'] != expected_snapshot:
                raise CheckpointDeleteBlocked(' '.join(public['blockers']) or
                    'Files or dependencies changed; preview the deletion again.', public)
            receipt = self.kernel.quarantine(transaction, operation_id=operation, after_stage=self.after_stage)
            self._record(receipt)
            current = state._decode(self.runtime.store.snapshot().read(_address(operation, 'state')))
            return dict(ok=True, run_name=self.runtime.run, metadata_path=address,
                scene=public['scene'], revision=public['revision'], profile=public['profile'],
                operation_id=operation, receipt=copy.deepcopy(receipt), storage_pin=self.runtime.output_pin,
                deleted_files=0, quarantined_files=len(transaction['items']), reclaimed_bytes=0,
                control_updates=public['control_updates'], undo_available=current['status'] == 'quarantined',
                payload_conditions=public['payload_conditions'],
                message='Processed take and exclusively owned PNGs quarantined; immutable bytes retained for undo.'
                    if current['status'] == 'quarantined' else 'This quarantine was already undone; later work is unchanged.')

    def _accepted_record(self, operation):
        receipt = self.runtime.store.snapshot().state['operations'].get(operation)
        return None if receipt is None else state._decode(
            self.runtime.store.committed_snapshot(receipt).read(_address(operation)))

    def _build_chapter(self, address, base):
        from . import storage_chapter_index as index
        self.check()
        runtime = self.runtime
        view = ProjectReadView(runtime.store, base=base)
        with view.operation():
            logical = view.address(address)
            prefix, number, token = index.parts(logical)
            branch = prefix.split('/')[1] if prefix else 'main'
            if branch != runtime.selected:
                raise ValueError('Chapter retirement belongs to another branch.')
            if logical not in base.state['documents']:
                raise FileNotFoundError('The chapter snapshot is no longer available.')
            document = index.validate(base.read(logical), logical, runtime.run)
            active, _ = CheckpointGraphManager(runtime.output, rehearsal_view=view).active_selection(runtime.run)
        segments = document.get('segments')
        if (not isinstance(segments, list) or not segments or any(not isinstance(s, dict)
                or type(s.get('index')) is not int or s['index'] < 1 for s in segments)):
            raise ValueError('Chapter snapshot contains invalid scene identities.')
        retired = logical.replace('/manifests/', '/retired_manifests/')
        reason = dict(format=CHAPTER_FORMAT, branch_id=runtime.selected, scene=segments[0]['index'],
                      revision=token, metadata_path=logical)
        transaction = self.kernel.preview(base, [logical], reason=state._encode(reason).decode(),
                                          archive_controls={logical:retired})
        result = dict(ok=True, allowed=True, run_name=runtime.run,
            path='h3_chains/'+runtime.run+'/'+logical, retired_path='h3_chains/'+runtime.run+'/'+retired,
            chapter_number=number, chapter_title=str(document['chapter'].get('title') or 'Chapter %d' % number),
            chapter_manifest_id=token,
            scenes=[dict(scene=s['index'], revision=s.get('revision',''), active=active.get(s['index']) == s.get('revision'))
                    for s in segments],
            snapshot=transaction['sha256'], deleted_files=0, reclaimed_bytes=0,
            message='Retiring releases only this chapter snapshot\'s recovery pins. Its exact metadata is archived; no clip media or assignments are deleted.')
        return result, transaction

    def preview_chapter(self, address):
        return self._build_chapter(address, self.runtime.accepted)[0]

    def retire_chapter(self, address, expected_snapshot, *, proof):
        address = artifact_address(address)
        operation = _operation('chapter', self.runtime.selected, address, expected_snapshot)
        with self.guard(proof):
            accepted = self._accepted_record(operation)
            base = self.runtime.accepted
            if accepted is not None:
                domain = self._domain(accepted)
                if domain.get('format') != CHAPTER_FORMAT or 'h3_chains/'+self.runtime.run+'/'+domain.get('metadata_path', '') != address:
                    raise ValueError('Chapter retirement operation has a different target.')
                base = state.Snapshot(self.runtime.project, accepted['preview']['base'])
            public, transaction = self._build_chapter(address, base)
            if public['snapshot'] != expected_snapshot:
                raise CheckpointDeleteBlocked('Chapter or project changed; preview retirement again.', public)
            receipt = self.kernel.quarantine(transaction, operation_id=operation, after_stage=self.after_stage)
            self._record(receipt)
            status = state._decode(self.runtime.store.snapshot().read(_address(operation, 'state')))
            return dict(public, operation_id=operation, receipt=copy.deepcopy(receipt),
                storage_pin=self.runtime.output_pin, undo_available=status['status'] == 'quarantined',
                message='Chapter snapshot retired; exact metadata archived for recovery and undo. No clip media deleted.'
                        if status['status'] == 'quarantined' else 'This retirement was already undone; later work is unchanged.')

    def _domain(self, record):
        if record.get('format') != RECEIPT or record.get('action') != 'quarantine':
            raise ValueError('This is not a supported quarantine receipt.')
        reason = state._decode(record['preview']['reason'].encode())
        fields = {'format', 'branch_id', 'scene', 'revision'}
        if isinstance(reason, dict) and reason.get('format') in (PROCESSING_FORMAT, CHAPTER_FORMAT):
            fields.add('metadata_path')
        if (not isinstance(reason, dict) or reason.get('format') not in (FORMAT, PROCESSING_FORMAT, CHAPTER_FORMAT)
                or reason.get('branch_id') != self.runtime.selected or set(reason) != fields):
            raise ValueError('Quarantine receipt belongs to another retention domain or branch.')
        return reason

    def _record(self, receipt):
        # A later retry acknowledges the old operation without rolling back
        # an unrelated commit, an undo, or the caller's current output pin.
        if receipt['operation_id'] not in self.runtime.accepted.state['operations']:
            self.runtime.record_commit(receipt)

    def delete_generation(self, scene, revision, expected_snapshot, *, proof):
        operation = _operation('generation', self.runtime.selected, [scene, revision], expected_snapshot)
        with self.guard(proof):
            accepted = self._accepted_record(operation)
            base = self.runtime.accepted
            if accepted is not None:
                domain = self._domain(accepted)
                if (domain['scene'], domain['revision']) != (scene, revision):
                    raise ValueError('Retirement operation has a different target.')
                base = state.Snapshot(self.runtime.project, accepted['preview']['base'])
            public, transaction = self._build_generation(scene, revision, base)
            if not public['allowed'] or public['snapshot'] != expected_snapshot:
                raise CheckpointDeleteBlocked(' '.join(public['blockers']) or
                    'Files or dependencies changed; preview the deletion again.', public)
            receipt = self.kernel.quarantine(transaction, operation_id=operation, after_stage=self.after_stage)
            self._record(receipt)
            current = state._decode(self.runtime.store.snapshot().read(_address(operation, 'state')))
            return dict(ok=True, run_name=self.runtime.run, scene=scene, revision=revision,
                operation_id=operation, receipt=copy.deepcopy(receipt), storage_pin=self.runtime.output_pin,
                rollback=public['rollback'], rollback_to_scene=public['rollback_to_scene'],
                deleted_files=0, quarantined_files=public['owned_file_count'], reclaimed_bytes=0,
                undo_available=current['status'] == 'quarantined',
                message='Checkpoint quarantined; immutable bytes retained for undo.' if current['status'] == 'quarantined'
                        else 'This quarantine was already undone; later work is unchanged.')

    def _undo_preview(self, operation, base):
        self.check()
        record = state._decode(base.read(_address(operation)))
        domain = self._domain(record)
        status = state._decode(base.read(_address(operation, 'state')))
        snapshot = base.state
        descriptors = snapshot['documents']
        result = dict(format=domain['format'], run_name=self.runtime.run, operation_id=operation,
            branch_id=self.runtime.selected, scene=domain['scene'], revision=domain['revision'],
            base=base.reference)
        if status.get('format') != STATUS or status.get('operation_id') != operation:
            raise ValueError('Quarantine state belongs to another operation.')
        if status.get('status') == 'restored':
            # A recovered/re-imported closed receipt has no local historical
            # commit to traverse. Reporting it closed requires no byte restore
            # or adoption of the old root. Native lost-reply undo retries still
            # use their original pending base through the accepted undo receipt.
            result.update(allowed=False, blockers=['This quarantine is no longer pending undo.'])
            return dict(result, snapshot=state._hash(state._encode(result)))
        if status.get('status') != 'quarantined':
            raise ValueError('Quarantine has an unknown recovery state.')
        blockers = []
        for item in record['preview']['items']:
            if item['key'] in descriptors:
                blockers.append('Undo would overwrite a later assignment or artifact: '+item['address'])
        accepted = self.runtime.store.committed_snapshot(snapshot['operations'][operation])
        published = accepted.state['documents']
        for item in record['preview'].get('updates', []):
            if descriptors.get(item['address']) != published.get(item['address']):
                blockers.append('Undo would overwrite a later PNG index/ownership edit: '+item['address'])
        for item in record['preview'].get('archives', []):
            if descriptors.get(item['address']) != published.get(item['address']):
                blockers.append('Archived metadata changed; refresh before undo: '+item['address'])
        self.kernel._preview(record['preview'])  # Verify retained descriptors and bytes.
        result.update(allowed=not blockers, blockers=blockers)
        conditions = [dict(path=item['address'], **item['custody']) for item in record['preview']['items'] if 'custody' in item]
        if conditions:
            result['payload_conditions'] = conditions
            result['warning'] = 'Undo restores the pre-deletion edited/missing state; it does not recover missing bytes or repair output hashes.'
        result['snapshot'] = state._hash(state._encode(result))
        return result

    def preview_undo(self, operation):
        operation = state._token(operation)
        if (operation not in self.runtime.accepted.state['operations']
                and 'retention/'+operation+'/portable.json' in self.runtime.accepted.state['documents']):
            if __package__:
                from .storage_portable_retention import ImportedQuarantineUndo
            else:
                from storage_portable_retention import ImportedQuarantineUndo
            return ImportedQuarantineUndo(self,operation).preview_undo(operation)
        if 'retention/'+operation+'/recovered_plan.json' in self.runtime.accepted.state['documents']:
            from .storage_recovered_retention import ImportedRecoveredUndo
            return ImportedRecoveredUndo(self,operation).preview_undo(operation)
        return self._undo_preview(operation, self.runtime.accepted)

    def undo(self, operation, expected_snapshot, *, proof):
        operation = state._token(operation)
        if (operation not in self.runtime.accepted.state['operations']
                and 'retention/'+operation+'/portable.json' in self.runtime.accepted.state['documents']):
            if __package__:
                from .storage_portable_retention import ImportedQuarantineUndo
            else:
                from storage_portable_retention import ImportedQuarantineUndo
            return ImportedQuarantineUndo(self,operation).undo(operation,expected_snapshot,proof=proof)
        if 'retention/'+operation+'/recovered_plan.json' in self.runtime.accepted.state['documents']:
            from .storage_recovered_retention import ImportedRecoveredUndo
            return ImportedRecoveredUndo(self,operation).undo(operation,expected_snapshot,proof=proof)
        undo = _operation('undo', self.runtime.selected, operation, expected_snapshot)
        with self.guard(proof):
            accepted = self._accepted_record(undo)
            base = self.runtime.accepted
            if accepted is not None:
                if accepted.get('action') != 'undo' or accepted.get('quarantine_operation') != operation:
                    raise ValueError('Undo operation has a different quarantine target.')
                base = state.Snapshot(self.runtime.project, accepted['base'])
            public = self._undo_preview(operation, base)
            if not public['allowed'] or public['snapshot'] != expected_snapshot:
                raise CheckpointDeleteBlocked(' '.join(public['blockers']) or 'Refresh the undo preview.', public)
            receipt = self.kernel.undo(base, operation, operation_id=undo, after_stage=self.after_stage)
            self._record(receipt)
            return dict(ok=True, operation_id=undo, quarantine_operation=operation,
                        storage_pin=self.runtime.output_pin, message='Quarantined saved files restored exactly.')
