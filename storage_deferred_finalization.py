"""Deferred approval: exact recovery publication, then resumable quarantine.

The caller activates the chosen lineage first. Acceptance never reassigns it.
An immutable decision fences the choice before any rejected take is retired;
individual confirmed previews and a completion receipt make retries exact.
"""
import copy
from contextlib import contextmanager
import uuid

from . import storage_state as control
from .storage_deferred_review import address, validate_identity, _check
from .storage_review_selection import candidate_recovery_controls, verify_media
from .storage_resolver import confined
from .project_ownership import project_write_guard
from .storage_quarantine import _address as quarantine_address, RECEIPT
from .storage_retention import FORMAT as RETENTION_FORMAT

FORMAT = 'h3_deferred_finalization_v1'
COMPLETION = 'h3_deferred_cleanup_v1'
IMPORTED = 'h3_imported_deferred_approval_v1'


def decision_address(branch, token, *, complete=False):
    folder = 'completed' if complete else 'decisions'
    return address(branch, token).rsplit('/', 1)[0]+'/'+folder+'/'+control._token(token)+'.json'


def project_decision(document, decision, completed):
    """Portable read projection; completion never resurrects after recovery."""
    if decision is None:
        if completed is not None:
            raise control.StateConflict('Deferred cleanup has no accepted approval record.')
        return document
    branch = document['plan'].get('_branch_id', 'main')
    request = decision.get('request', {})
    revisions = [item['segment']['revision'] for item in document['candidates']]
    if (decision.get('format') != FORMAT or request.get('format') != FORMAT
            or request.get('run_name') != document['run_name']
            or request.get('branch_id') != branch or request.get('token') != document['token']
            or request.get('scene') != document['scene']
            or request.get('pending_sha256') != control._hash(control._encode(document))
            or request.get('revision') not in revisions
            or not isinstance(request.get('kept'), list)
            or request['revision'] not in request['kept']
            or set(request['kept'])-set(revisions)):
        raise control.StateConflict('Deferred approval does not match its exact pending batch.')
    if completed is not None and (completed.get('format') != COMPLETION
            or completed.get('request') != request):
        raise control.StateConflict('Deferred completion does not match its accepted approval.')
    projected = copy.deepcopy(document)
    status = dict(status='complete' if completed is not None else 'cleanup_pending',
        candidate_revision=request['revision'], kept_candidate_revisions=request['kept'])
    projected['_finalization'] = status
    projected['public']['finalization'] = status
    return projected


def accepted_projection(runtime, document):
    descriptors = runtime.base.state['documents']
    def optional(complete):
        key = decision_address(runtime.selected, document['token'], complete=complete)
        if key not in descriptors:
            return None
        try:
            return control._decode(runtime.base.read(key))
        except FileNotFoundError as exc:
            raise control.StateConflict('Accepted deferred approval/completion bytes are missing.') from exc
    return project_decision(document, optional(False), optional(True))


def check_choice(document, revision, kept):
    accepted = document.get('_finalization')
    if accepted and (accepted['candidate_revision'] != revision
                     or sorted(accepted['kept_candidate_revisions']) != sorted(kept)):
        raise control.StateConflict('This pending batch already accepted another choice or keep list.')


class DeferredFinalization:
    def __init__(self, runtime, chain, token, revision, kept, proof, response):
        self.runtime, self.chain, self.proof = runtime, chain, copy.deepcopy(proof)
        _check(runtime)
        self.token, self.revision = control._token(token), control._token(revision)
        self.key = address(runtime.selected, self.token)
        self.document = validate_identity(control._decode(runtime.base.read(self.key)),
            runtime.run, runtime.selected, self.token)
        self.available = [control._token(item['segment']['revision']) for item in self.document['candidates']]
        self.kept = sorted(set(control._token(value) for value in kept))
        if (len(set(self.available)) != len(self.available) or self.revision not in self.available
                or self.revision not in self.kept or set(self.kept)-set(self.available)):
            raise ValueError('Deferred approval must keep its selected member of the exact batch.')
        self.scene = self.document['scene']
        self.request = dict(format=FORMAT, run_name=runtime.run, branch_id=runtime.selected,
            token=self.token, scene=self.scene, revision=self.revision, kept=self.kept,
            pending_sha256=control._hash(control._encode(self.document)))
        self.operation = uuid.uuid5(uuid.UUID(self.token), 'finalize:'+runtime.selected).hex
        self.completion_operation = uuid.uuid5(uuid.UUID(self.operation), 'cleanup').hex
        self.decision_key = decision_address(runtime.selected, self.token)
        self.completion_key = decision_address(runtime.selected, self.token, complete=True)
        self.directory = 'project/jobs/'+self.operation
        self.budget = runtime.store._marker()[0]['path_budget']
        self.response = copy.deepcopy(response)

    @contextmanager
    def guard(self):
        runtime = self.runtime
        _check(runtime)
        runtime.reviews._require_write(runtime.selected)
        runtime.generation.require_write()
        if runtime.has_node_proof and runtime.node_write_proof != self.proof:
            raise ValueError('Deferred approval cannot replace caller ownership.')
        with project_write_guard(runtime.output, runtime.run, self.proof, 'finalize a pending scene review'):
            if set(self.available)-set(self.kept):
                with runtime.retention.guard(self.proof):
                    yield
            else:
                yield

    def _record(self, receipt):
        if receipt['operation_id'] not in self.runtime.accepted.state['operations']:
            self.runtime.record_commit(receipt)

    def _accepted(self):
        runtime = self.runtime
        receipt = runtime.check().state['operations'].get(self.operation)
        if receipt is None:
            return self._adopt_imported()
        accepted = runtime.store.committed_snapshot(receipt)
        value = control._decode(accepted.read(self.decision_key))
        if value.get('format') != FORMAT or value.get('request') != self.request:
            raise control.StateConflict('Deferred approval retry changed its accepted request.')
        imported = self._import_origin()
        if imported is not None:
            key, binding = self._import_binding(imported)
            if accepted.read(key) != control._encode(binding):
                raise control.StateConflict('Imported approval retry differs from its original import witness.')
        # A retry must not restore old settings over later edits of this branch.
        scope = 'branch:'+runtime.selected
        if runtime.check().state['scope_revisions'].get(scope) != accepted.state['scope_revisions'].get(scope):
            raise control.StateConflict('The finalized branch changed; reload it before another review action.')
        for key, digest in value['controls_sha256'].items():
            if control._hash(accepted.read(key)) != digest:
                raise control.StateConflict('Deferred recovery settings were not accepted together.')
        self._record(receipt)
        return receipt, value

    def _import_origin(self):
        """Only the exact initial import can introduce an older approval.

        An arbitrary decision-shaped document written later cannot bypass
        normal acceptance or become authority through an API retry.
        """
        current = self.runtime.base
        if self.decision_key not in current.state['documents']:
            return None
        initial = current
        while initial.state['parent'] is not None:
            parent = control.Snapshot(self.runtime.project,initial.state['parent'])
            if parent.state['generation'] != initial.state['generation']-1:
                raise control.StateConflict('Imported approval has inconsistent accepted ancestry.')
            initial = parent
        if not initial.state.get('import_source') or self.decision_key not in initial.state['documents']:
            return None
        for key in (self.key,self.decision_key):
            descriptor = initial.state['documents'].get(key,{})
            if (not descriptor.get('immutable') or descriptor != current.state['documents'].get(key)
                    or initial.read(key) != current.read(key)):
                raise control.StateConflict('Imported approval differs from its immutable initial copy.')
        return initial

    def _import_binding(self, initial):
        token = initial.reference['path'].rsplit('/',1)[-1][:-5]
        key = 'jobs/'+self.operation+'/imported-decisions/'+token+'.json'
        value = dict(format=IMPORTED,request=self.request,import_root=initial.reference,
                     decision_sha256=control._hash(initial.read(self.decision_key)))
        return key,value

    def _adopt_imported(self):
        runtime = self.runtime
        initial = self._import_origin()
        if initial is None:
            return None
        value,scopes = self._imported_approval(initial)
        key,binding = self._import_binding(initial)
        receipt = runtime.store.commit(runtime.base,{key:dict(data=control._encode(binding),
            scope='jobs:'+self.operation,category='reviews',immutable=True)},
            operation_id=self.operation,read_scopes=scopes)
        self._record(receipt)
        return receipt,value

    def _imported_approval(self, initial):
        runtime = self.runtime
        value = control._decode(initial.read(self.decision_key))
        projected = project_decision(self.document,value,None)
        check_choice(projected,self.revision,self.kept)
        if value.get('request') != self.request:
            raise control.StateConflict('Imported deferred approval differs from the requested batch.')
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        hashes = value.get('controls_sha256')
        if not isinstance(hashes,dict) or prefix+'plan.json' not in hashes:
            raise control.StateConflict('Imported approval has no exact branch-setting witness.')
        with runtime.reader.track_reads() as reads:
            for key,digest in hashes.items():
                if key not in {prefix+name+'.json' for name in ('plan','workflow','api_prompt')}:
                    raise control.StateConflict('Imported approval settings belong to another branch.')
                if control._hash(runtime.base.read(key)) != digest:
                    raise control.StateConflict('Imported branch settings changed after approval; nothing was rewritten.')
                reads.add(key)
            lineage = self.chain._review_candidate_resume_revisions(runtime.run,self.scene,self.revision,
                                                                   rehearsal_view=runtime.reader)
            for item in lineage:
                key = prefix+'checkpoints/clip_%04d.json' % item['scene']
                active = control._decode(runtime.base.read(key))
                reads.add(key)
                metadata,_ = self.chain._load_checkpoint_revision(runtime.run,item['scene'],item['revision'],
                                                                  rehearsal_view=runtime.reader)
                if active.get('segment') != metadata['segment']:
                    raise control.StateConflict('Imported selected lineage changed after approval.')
        reads.update((self.key,self.decision_key))
        scopes = {runtime.base.state['documents'][address]['scope'] for address in reads}
        scopes.add('branch:'+runtime.selected)
        return value,scopes

    def _imported_completion(self, initial):
        def read(key):
            descriptor = initial.state['documents'].get(key,{})
            if (not descriptor.get('immutable')
                    or descriptor != self.runtime.base.state['documents'].get(key)
                    or initial.read(key) != self.runtime.base.read(key)):
                raise control.StateConflict('Imported cleanup evidence changed after its initial copy.')
            return control._decode(initial.read(key))
        value = read(self.completion_key)
        project_decision(self.document,read(self.decision_key),value)
        targets = [revision for revision in self.available if revision not in self.kept]
        if [item.get('revision') for item in value.get('steps',[])] != targets:
            raise control.StateConflict('Imported cleanup changed its exact rejected candidate list.')
        for step in value['steps']:
            if step.get('outcome') == 'blocked' and isinstance(step.get('blockers'),list) and step['blockers']:
                continue
            if step.get('outcome') != 'quarantined':
                raise control.StateConflict('Imported cleanup has an unknown outcome.')
            operation = control._token(step.get('operation_id') or step.get('receipt',{}).get('operation_id'))
            if step.get('origin') == 'recovered':
                from .storage_recovered_review import FORMAT as RECOVERED
                plan = read('retention/'+operation+'/recovered_plan.json')
                if (plan.get('format') != RECOVERED or plan.get('operation_id') != operation
                        or plan.get('revision') != step['revision'] or plan.get('request') != self.request):
                    raise control.StateConflict('Imported recovered cleanup belongs to another approval.')
            else:
                record = read(quarantine_address(operation))
                if (record.get('format') != RECEIPT or record.get('action') != 'quarantine'
                        or record.get('operation_id') != operation
                        or control._decode(record['preview']['reason'].encode()) != dict(format=RETENTION_FORMAT,
                            branch_id=self.runtime.selected,scene=self.scene,revision=step['revision'])):
                    raise control.StateConflict('Imported cleanup belongs to another rejected take.')
        return value

    def _publish(self):
        runtime, chain = self.runtime, self.chain
        base = runtime.base
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        with runtime.reader.track_reads() as reads:
            lineage = chain._review_candidate_resume_revisions(runtime.run, self.scene,
                self.revision, rehearsal_view=runtime.reader)
            metadata = None
            for item in lineage:
                key = prefix+'checkpoints/clip_%04d.json' % item['scene']
                active = control._decode(base.read(key))
                reads.add(key)
                if active.get('segment', {}).get('revision') != item['revision']:
                    raise control.StateConflict('Activate the selected checkpoint lineage before finalizing this pending review.')
                metadata, _ = chain._load_checkpoint_revision(runtime.run, item['scene'], item['revision'],
                                                              rehearsal_view=runtime.reader)
                if active.get('segment') != metadata['segment']:
                    raise control.StateConflict('Deferred active assignment differs from immutable saved metadata.')
            segment = metadata['segment']
            if segment.get('take_kind') == 'editorial_alternate':
                raise ValueError('A picture-only alternate cannot be a generation candidate.')
            plan = chain._plan_with_review_revision(self.document['plan'], self.scene,
                str(segment.get('scene_prompt_template', segment.get('scene_prompt')) or ''),
                int(segment.get('seed', 0)), int(segment.get('raw_frames', 0)))
            if (metadata.get('compatibility') != plan.get('compatibility')
                    or segment.get('history_hash') != chain._history_hash(plan, self.scene)):
                raise control.StateConflict('Deferred candidate differs from its saved Plan settings or history.')
            controls = candidate_recovery_controls(runtime, chain, metadata, plan, reads)
            verify_media(runtime, base, segment)
        descriptors = base.state['documents']
        scopes = {descriptors[key]['scope'] for key in reads | {self.key}}
        scopes.add('branch:'+runtime.selected)
        changes = {key:dict(data=raw, scope='branch:'+runtime.selected, category='branches', immutable=False)
                   for key,raw in controls.items()}
        value = dict(format=FORMAT, request=self.request, response=self.response, base=base.reference,
                     controls_sha256={key:control._hash(raw) for key,raw in controls.items()})
        changes[self.decision_key] = dict(data=control._encode(value), scope='jobs:'+self.operation,
                                           category='reviews', immutable=True)
        receipt = runtime.store.commit(base, changes, operation_id=self.operation, read_scopes=scopes)
        self._record(receipt)
        return receipt, value

    def _verify_completion(self, receipt, decision_receipt):
        runtime = self.runtime
        accepted = runtime.store.committed_snapshot(receipt)
        value = control._decode(accepted.read(self.completion_key))
        if (value.get('format') != COMPLETION or value.get('request') != self.request
                or value.get('decision_receipt') != decision_receipt):
            raise control.StateConflict('Deferred cleanup receipt has a different approval.')
        cursor = runtime.store.committed_snapshot(decision_receipt)
        targets = [revision for revision in self.available if revision not in self.kept]
        if [step.get('revision') for step in value.get('steps', [])] != targets:
            raise control.StateConflict('Deferred cleanup changed its rejected candidate list.')
        for step in value['steps']:
            if step.get('base') != cursor.reference:
                raise control.StateConflict('Deferred cleanup replaced its accepted predecessor.')
            if step.get('outcome') == 'blocked' and step.get('blockers'):
                continue
            if step.get('outcome') != 'quarantined':
                raise control.StateConflict('Deferred cleanup has an unknown outcome.')
            if step.get('origin') in ('recovered','portable'):
                if self._recovered_step(revision=step['revision'],base=cursor) != step:
                    raise control.StateConflict('Imported quarantine differs from its cleanup step.')
                continue
            successor = runtime.store.committed_snapshot(step['receipt'])
            record = control._decode(successor.read(quarantine_address(step['receipt']['operation_id'])))
            if (record.get('format') != RECEIPT or record.get('action') != 'quarantine'
                    or record['preview']['base'] != cursor.reference
                    or control._decode(record['preview']['reason'].encode()) != dict(format=RETENTION_FORMAT,
                        branch_id=runtime.selected, scene=self.scene, revision=step['revision'])):
                raise control.StateConflict('Deferred cleanup receipt belongs to another target.')
            runtime.store.validate_snapshot(cursor, current=successor)
            cursor = successor
        if value.get('base') != cursor.reference:
            raise control.StateConflict('Deferred cleanup completion replaced its last accepted root.')
        runtime.store.validate_snapshot(cursor, current=accepted)
        return value

    def _recovered_step(self, revision, base):
        """A completed ordinary quarantine stays completed after re-import.

        Its portable file/approval witnesses replace neither a decision nor a
        branch assignment. An already undone rejection must not be deleted
        again just because final completion acknowledgement was interrupted.
        """
        operation = uuid.uuid5(uuid.UUID(self.token),
            'recovered-cleanup:'+self.runtime.selected+':'+revision).hex
        if 'retention/'+operation+'/recovered_plan.json' not in base.state['documents']:
            return self._portable_step(revision,base)
        from .storage_recovered_retention import ImportedRecoveredUndo
        service = ImportedRecoveredUndo(self.runtime.retention,operation,base=base)
        if service.request != self.request or service.plan['revision'] != revision:
            raise control.StateConflict('Imported quarantine belongs to another rejected candidate.')
        service.preview_undo(operation)  # Full custody verification, no restore.
        return dict(revision=revision,base=base.reference,outcome='quarantined',
                    origin='recovered',operation_id=operation)

    def _portable_step(self, revision, base):
        from .storage_retention_portability import PortableQuarantine
        expected = dict(format=RETENTION_FORMAT,branch_id=self.runtime.selected,scene=self.scene,revision=revision)
        candidates = []
        for key in base.state['documents']:
            parts = key.split('/')
            if len(parts) != 3 or parts[0] != 'retention' or parts[2] != 'portable.json':
                continue
            record = control._decode(base.read(quarantine_address(parts[1])))
            try:
                reason = control._decode(record['preview']['reason'].encode())
            except ValueError:
                continue  # A kernel's free-form reason is not this domain's approval.
            if reason != expected:
                continue
            capsule = PortableQuarantine.from_store(self.runtime.store,base,parts[1])
            before_decision = capsule.before['documents'].get(self.decision_key,{}).get('file',{})
            if before_decision.get('sha256') != control._hash(base.read(self.decision_key)):
                raise control.StateConflict('Portable cleanup predates or differs from this accepted approval.')
            metadata_key = 'checkpoints/clip_%04d.%s.json' % (self.scene,revision)
            metadata = capsule.document(capsule.before['documents'][metadata_key]['file'])
            saved = next(c['segment'] for c in self.document['candidates'] if c['segment']['revision'] == revision)
            if self.chain._public_segment(metadata.get('segment',{})) != saved:
                raise control.StateConflict('Portable cleanup differs from the accepted review candidate.')
            status = control._decode(base.read(quarantine_address(parts[1],'state')))
            if status.get('status') not in ('quarantined','restored') or status.get('operation_id') != parts[1]:
                raise control.StateConflict('Portable cleanup has an unknown current state.')
            candidates.append(parts[1])
        if len(candidates) > 1:
            raise control.StateConflict('More than one retirement matches this review; do not guess which cleanup to resume.')
        if not candidates:
            return None
        return dict(revision=revision,base=base.reference,outcome='quarantined',origin='portable',
                    operation_id=candidates[0])

    def _cleanup(self, decision_receipt):
        runtime = self.runtime
        existing = runtime.check().state['operations'].get(self.completion_operation)
        if existing:
            value = self._verify_completion(existing, decision_receipt)
            self._record(existing)
            return value
        cursor = runtime.store.committed_snapshot(decision_receipt)
        steps = []
        for revision in self.available:
            if revision in self.kept:
                continue
            recovered = self._recovered_step(revision,cursor)
            if recovered is not None:
                steps.append(recovered)
                continue
            path = confined(runtime.project, self.directory+'/'+revision+'.json')
            preview, _ = runtime.retention._build_generation(self.scene, revision, cursor)
            prepared = dict(request=self.request, revision=revision, base=cursor.reference,
                            snapshot=preview['snapshot'], allowed=preview['allowed'], blockers=preview['blockers'])
            if not path.exists() and runtime.check().reference != cursor.reference:
                raise control.StateConflict('Project changed before deferred cleanup; no new deletion was adopted.')
            if path.exists() and path.read_bytes() != control._encode(prepared):
                raise control.StateConflict('Deferred cleanup retry changed its prepared deletion preview.')
            control._immutable(runtime.project, path.relative_to(runtime.project).as_posix(),
                               control._encode(prepared), self.budget)
            step = dict(revision=revision, base=cursor.reference)
            if not preview['allowed']:
                step.update(outcome='blocked', blockers=preview['blockers'])
            else:
                deleted = runtime.retention.delete_generation(self.scene, revision, preview['snapshot'], proof=self.proof)
                step.update(outcome='quarantined', receipt=deleted['receipt'])
                cursor = runtime.store.committed_snapshot(deleted['receipt'])
            steps.append(step)
        value = dict(format=COMPLETION, request=self.request, decision_receipt=decision_receipt,
                     base=cursor.reference, steps=steps)
        with control._lock(runtime.project):
            if runtime.check().reference != cursor.reference:
                raise control.StateConflict('Project changed before deferred cleanup completion.')
            receipt = runtime.store.commit(cursor, {self.completion_key:dict(data=control._encode(value),
                scope='jobs:'+self.completion_operation, category='reviews', immutable=True)},
                operation_id=self.completion_operation)
            self._record(receipt)
        return self._verify_completion(receipt, decision_receipt)

    def run(self):
        with self.guard():
            initial = self._import_origin()
            if initial is not None and self.completion_key in initial.state['documents']:
                accepted,_ = self._imported_approval(initial)
                completed = self._imported_completion(initial)
            else:
                receipt, accepted = self._accepted() or self._publish()
                completed = self._cleanup(receipt)
            with control._lock(self.runtime.project):
                self.runtime.store._acknowledge_commit()
            quarantined = [item for item in completed['steps'] if item['outcome'] == 'quarantined']
            warnings = [' '.join(item['blockers']) for item in completed['steps'] if item['outcome'] == 'blocked']
            return dict(accepted['response'], deleted_candidate_count=0,
                quarantined_candidate_count=len(quarantined), cleanup_warnings=warnings, reclaimed_bytes=0,
                undo_operations=[item.get('operation_id') or item['receipt']['operation_id'] for item in quarantined],
                storage_pin=self.runtime.output_pin, finalization_complete=True)
