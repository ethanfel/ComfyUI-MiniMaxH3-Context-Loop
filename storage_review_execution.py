"""Actual Save -> Review -> Loop End decisions in an explicit copied runtime.

The save receipt identifies a take; it cannot authorize a changed retry prompt
or candidate batch. Review adds an immutable, host-invocation-bound transition.
The downstream adapter verifies both receipts and the exact incoming state,
including predecessor tensors and candidate inventory. No pointer is guessed
from the newest branch and no live future or ownership credential is archived.
"""
import copy
from contextlib import contextmanager
from pathlib import Path
import re
import uuid

if __package__:
    from . import storage_state as control
    from .storage_continuation import loop_end_inputs, witness_address, state_digest, FORMAT as SAVE_FORMAT
    from .storage_execution_digest import execution_digest
    from .project_ownership import project_write_guard
    from .storage_project import payload_key
    from .storage_resolver import confined
    from .storage_review_selection import CandidateSelection, output_digest, verify_media
    from .storage_review_cleanup import ReviewCleanup, verify_cleanup, status_note, KEY as CLEANUP_KEY
else:
    import storage_state as control
    from storage_continuation import loop_end_inputs, witness_address, state_digest, FORMAT as SAVE_FORMAT
    from storage_execution_digest import execution_digest
    from project_ownership import project_write_guard
    from storage_project import payload_key
    from storage_resolver import confined
    from storage_review_selection import CandidateSelection, output_digest, verify_media
    from storage_review_cleanup import ReviewCleanup, verify_cleanup, status_note, KEY as CLEANUP_KEY

FORMAT = 'h3_review_transition_v1'
KEY = '_storage_review'


def review_inputs(store, values):
    if isinstance(values.get('segment'), dict) and KEY in values['segment']:
        raise ValueError('Review requires a scene-save output, not another Review decision.')
    return loop_end_inputs(store, values)


def _state_pin(value, pin):
    result = dict(value, plan=dict(value['plan'], _storage_pin=copy.deepcopy(pin)))
    if '_storage_pin' in result:
        result['_storage_pin'] = copy.deepcopy(pin)
    return result


def _address(operation):
    return 'jobs/'+control._token(operation)+'/review.json'


def reviewed_loop_inputs(store, values):
    if __package__:
        from .storage_runtime import runtime_access
    else:
        from storage_runtime import runtime_access
    segment, incoming = values.get('segment'), values.get('state')
    proof = segment.get(KEY)
    if not isinstance(proof, dict) or set(proof) != {'receipt', 'witness'}:
        raise ValueError('Reviewed continuation requires its accepted transition receipt.')
    pin = segment.get('_storage_pin')
    if not isinstance(pin, dict):
        raise ValueError('Reviewed continuation is missing its accepted pin.')
    with runtime_access(store, pin=pin, selected=pin.get('branch_id')) as bound:
        accepted = store.committed_snapshot(proof['receipt'])
        successor = accepted
        if CLEANUP_KEY in segment:
            successor, _ = verify_cleanup(store, segment[CLEANUP_KEY], proof['receipt'])
        if successor.reference != bound.base.reference or proof['witness'] != _address(proof['receipt']['operation_id']):
            raise control.StateConflict('Review receipt, witness and successor pin disagree.')
        witness = control._decode(accepted.read(proof['witness']))
        if witness.get('format') != FORMAT or witness.get('outcome') != 'continue':
            raise ValueError('Unsupported reviewed continuation witness.')
        if execution_digest(incoming) != witness['source_state_sha256']:
            raise control.StateConflict('Reviewed continuation changed its input Plan, range, context or candidate batch.')
        source = witness['source_segment']
        if (source.get('_storage_pin') != witness['input_pin']
                or dict(witness['input_pin'], root=pin['root']) != pin):
            raise ValueError('Review transition has a different source project, branch or epoch.')
        source_root = control.Snapshot(store.project, witness['input_pin']['root'])
        store.validate_snapshot(source_root, current=successor)
        selection = witness.get('selection')
        if selection is not None:
            decision = segment.get('_h3_review_decision', {})
            if (decision.get('action') != 'candidate_selected'
                    or decision.get('plan', {}).get('_storage_pin') != pin
                    or output_digest(segment, witness['input_pin']) != selection['output_sha256']):
                raise control.StateConflict('Selected Review output changed its prompt, seed, Plan or context.')
            for address, digest in selection['dependencies'].items():
                if address in selection['controls_sha256']:
                    continue
                if control._hash(successor.read(address)) != digest:
                    raise control.StateConflict('Selected Review dependency differs from its witness.')
            for address, digest in selection['controls_sha256'].items():
                if control._hash(successor.read(address)) != digest:
                    raise control.StateConflict('Selected Review recovery/assignment was not published.')
            verify_media(bound, successor, segment)
        else:
            public = {key: value for key, value in segment.items() if key not in (KEY, CLEANUP_KEY, '_storage_pin')}
            expected = {key: value for key, value in witness['output_segment'].items() if key != '_storage_pin'}
            if control._encode(public) != control._encode(expected):
                raise control.StateConflict('Review output differs from its accepted decision.')
    # Validate the original Save independently, outside the Review runtime.
    # This checks exact immutable metadata, input Plan and media integrity.
    prepared = loop_end_inputs(store, dict(values, segment=source))
    prepared['state'] = _state_pin(prepared['state'], pin)
    prepared['segment'] = segment
    return prepared


class ReviewExecution:
    def __init__(self, runtime, chain, operation, inputs, *, display_id=None):
        self.runtime, self.chain = runtime, chain
        self.operation = control._token(operation)
        self.display_id = str(display_id if display_id is not None else inputs.get('unique_id', ''))
        self.inputs = inputs
        self.state, self.segment = inputs['state'], inputs['segment']
        if (self.state['plan'].get('_storage_pin') != runtime.pin
                or self.segment.get('_storage_pin') != runtime.pin):
            raise ValueError('Review requires the verified scene-save input adapter.')
        saved = self.segment.get('_storage_save')
        if not isinstance(saved, dict) or set(saved) != {'receipt', 'witness'}:
            raise ValueError('Review requires a scene-save transition receipt.')
        snapshot = runtime.store.committed_snapshot(saved['receipt'])
        if snapshot.reference != runtime.base.reference or saved['witness'] != witness_address(saved['receipt']['operation_id']):
            raise control.StateConflict('Review input disagrees with its scene-save receipt.')
        witness = control._decode(snapshot.read(saved['witness']))
        self.original = _state_pin(self.state, witness['input_pin'])
        if (witness.get('format') != SAVE_FORMAT or state_digest(self.original) != witness.get('state_sha256')
                or witness.get('scene') != self.state['index']):
            raise control.StateConflict('Review state differs from its saved input witness.')
        self.proof = copy.deepcopy(self.state['plan'].get('_project_ownership'))
        self.digest = execution_digest(inputs)
        self.original_digest = execution_digest(self.original)
        self.request = dict(format=FORMAT, operation=self.operation, input_pin=runtime.pin,
                            inputs_sha256=self.digest, source_state_sha256=self.original_digest)
        self.directory = 'project/jobs/'+self.operation
        self.budget = runtime.store._marker()[0]['path_budget']
        self.working_root = runtime.project if runtime.selected == 'main' else runtime.project/'branches'/runtime.selected
        self.selection = None
        self.prune = None
        with self.guard():
            control._immutable(runtime.project, self.directory+'/review-request.json',
                               control._encode(self.request), self.budget)

    @contextmanager
    def guard(self):
        self.runtime.reviews._require_write(self.runtime.selected)
        if not self.runtime.has_node_proof or self.runtime.node_write_proof != self.proof:
            raise ValueError('Review cannot replace its caller ownership proof.')
        with project_write_guard(self.runtime.output, self.runtime.run, self.proof, 'accept a scene review'):
            yield

    def _unchanged(self):
        if execution_digest(self.inputs) != self.digest:
            raise control.StateConflict('Review inputs changed while awaiting a decision.')

    def _branch_unchanged(self):
        scope = 'branch:'+self.runtime.selected
        expected = self.runtime.base.state['scope_revisions'].get(scope)
        if self.runtime.accepted.state['scope_revisions'].get(scope) != expected:
            raise control.StateConflict('Review source branch changed while awaiting a decision.')

    def accepted(self):
        with self.guard():
            self._unchanged()
            receipt = self.runtime.check().state['operations'].get(self.operation)
            if receipt is None:
                return None
            accepted = self.runtime.store.committed_snapshot(receipt)
            saved = control._decode(accepted.read(_address(self.operation)))
            if saved.get('format') != FORMAT or saved.get('request') != self.request:
                raise control.StateConflict('Review retry differs from its accepted request.')
            # Do not reuse a decision whose original checkpoint/media was damaged.
            for key in ('checkpoint', 'segment', 'generated_audio', 'blend_segment', 'prompt_file'):
                if self.segment.get(key):
                    self.runtime.store.payload_path(accepted, self.runtime.reader.address(self.segment[key]), verify=True)
            with control._lock(self.runtime.project):
                self.runtime.store._acknowledge_commit()
            self.runtime.record_commit(receipt)
            return self._result(saved, receipt)

    def resume(self):
        accepted = self.accepted()
        if accepted is not None:
            return accepted
        path = confined(self.runtime.project, self.directory+'/review-result.json')
        if not path.exists():
            return None
        envelope = control._decode(control._read_bytes(path))
        if (set(envelope) != {'value', 'sha256'}
                or control._hash(control._encode(envelope['value'])) != envelope['sha256']):
            raise control.StateConflict('Prepared Review result failed its checksum.')
        return self._publish(envelope['value'])

    def _result(self, saved, receipt):
        selection = self._selection(saved)
        if selection is not None:
            selection.verify(self.runtime.store.committed_snapshot(receipt), published=True)
        cleanup, status = None, saved['status']
        if saved.get('prune'):
            cleanup, result = ReviewCleanup(self, saved, receipt).run()
            status += status_note(result)
            if selection is not None:
                selection.verify(self.runtime.accepted, published=True)
            else:
                verify_media(self.runtime, self.runtime.accepted, self.segment)
        if saved.get('outcome') in ('stop', 'deferred'):
            if saved['outcome'] == 'deferred':
                from .storage_deferred_review import address, validate_publication
                snapshot = self.runtime.store.committed_snapshot(receipt)
                document = saved['deferred']
                validate_publication(self, document, snapshot)
                if control._decode(snapshot.read(address(self.runtime.selected, self.operation))) != document:
                    raise control.StateConflict('Deferred Review inventory differs from its stopping receipt.')
            if saved['outcome'] == 'stop' and self.inputs['assemble_partial_on_stop']:
                from .storage_review_partial import assemble_partial
                partial, warning = assemble_partial(self, selection, receipt)
                status += '; partial video: '+str(partial['result'][0])
                if warning:
                    status += '; '+warning
                result = dict(ui=dict(partial['ui'], text=[status]),
                              result=(self.chain.ExecutionBlocker(None), status))
            else:
                result = dict(ui=dict(text=[status]), result=(self.chain.ExecutionBlocker(None), status))
            if saved['outcome'] == 'stop':
                self._notify_stop(saved, result)
            return result
        output = selection.output(self.runtime.output_pin) if selection is not None else saved['output_segment']
        segment = dict(output, _storage_pin=self.runtime.output_pin,
                       **{KEY:dict(receipt=receipt, witness=_address(self.operation))})
        if cleanup is not None:
            segment[CLEANUP_KEY] = cleanup
        return dict(ui=dict(text=[status]), result=(segment, status))

    def _notification(self, saved):
        target = saved.get('notification', dict(token=self.operation, node_id=self.display_id))
        if (not isinstance(target, dict) or set(target) != {'token','node_id'}
                or target['token'] != self.operation or not isinstance(target['node_id'], str)):
            raise control.StateConflict('Stopped Review notification differs from its invocation.')
        return target

    def _notify_stop(self, saved, result):
        # A publication may have succeeded before the websocket reply failed.
        # Every exact retry redisplays that Stop, never a new pending gate.
        target = self._notification(saved)
        server = self.chain.PromptServer
        if server is None or getattr(server, 'instance', None) is None:
            return
        payload = dict(target, action='stop', status=result['result'][1])
        if result['ui'].get('images'):
            payload['partial_video'] = result['ui']['images'][0]
        server.instance.send_sync('minimax_h3_context_loop_review_resolved', payload, server.instance.client_id)

    def prepare_prune(self, candidates, kept):
        """Record user retention choices, but do not retire before acceptance."""
        available = [control._token(item['segment']['revision']) for item in candidates]
        batch = self.chain._review_batch_candidates(self.state, self.state['index'],
            self.chain._review_candidate_target(self.inputs['candidate_count']))
        expected = [item['segment']['revision'] for item in batch] + [self.segment['revision']]
        selected = self.selection.segment['revision'] if self.selection is not None else self.segment['revision']
        kept = list(dict.fromkeys(control._token(value) for value in kept))
        if (available != expected or len(set(available)) != len(available)
                or set(kept)-set(available) or selected not in kept):
            raise ValueError('Review cleanup may only retire rejected takes from its exact witnessed batch.')
        if set(available)-set(kept):
            with self.runtime.retention.guard(self.proof):
                self.prune = dict(scene=self.state['index'], branch_id=self.runtime.selected,
                                  available=available, kept=kept)

    def select(self, decision):
        revision = str(decision.get('candidate_revision') or '')
        if not revision or revision == self.segment['revision']:
            return self.segment, self.state
        # Handoff grants do not authorize changing the selected checkpoint.
        self.runtime.generation.require_write()
        self.selection = CandidateSelection(self.runtime, self.chain, self.state, revision)
        self.selection.verify(self.runtime.base)
        return self.selection.segment, self.selection.state

    def _selection(self, saved):
        record = saved.get('selection')
        if record is None:
            return None
        self.runtime.generation.require_write()
        if self.selection is None:
            self.selection = CandidateSelection(self.runtime, self.chain, self.state, record['revision'])
        if (self.selection.record != record
                or self.selection.serialized != saved.get('output_segment')):
            raise control.StateConflict('Selected candidate differs from its prepared Review result.')
        return self.selection

    def defer(self, payload, candidates, status):
        from .storage_deferred_review import normalize_previews
        document = self.chain._deferred_review_document(self.state['plan'], payload, candidates)
        document = normalize_previews(self.runtime, document)
        return self.finish(self.segment, status, deferred=document)

    def finish(self, segment, status, *, stop=False, deferred=None):
        self._unchanged()
        if stop and self.inputs['assemble_partial_on_stop']:
            self.runtime.exports.require_write()
        retry = self.accepted()
        if retry is not None:
            return retry
        decision = segment.get('_h3_review_decision')
        if decision is not None and decision.get('action') not in ('retry', 'candidate_selected'):
            raise ValueError('Selected-candidate Review continuation needs its selection publication.')
        selected = decision is not None and decision.get('action') == 'candidate_selected'
        if selected:
            if (self.selection is None
                    or output_digest(segment, self.runtime.pin) != self.selection.record['output_sha256']):
                raise control.StateConflict('Review cannot substitute unverified candidate continuation data.')
            output = self.selection.serialized
        else:
            base = {key: value for key, value in segment.items() if key != '_h3_review_decision'}
            if self.selection is not None or control._encode(base) != control._encode(self.segment):
                raise control.StateConflict('Review cannot change the saved take without selecting a candidate.')
            output = segment
        saved = dict(format=FORMAT, request=self.request, input_pin=self.runtime.pin,
            source_state_sha256=self.original_digest, source_segment=self.segment,
            output_segment=output, status=status, outcome='deferred' if deferred is not None else 'stop' if stop else 'continue',
            publication_root=self.runtime.accepted.reference)
        if deferred is not None:
            saved['deferred'] = deferred
        if stop:
            saved['notification'] = dict(token=self.operation, node_id=self.display_id)
        if selected:
            saved['selection'] = self.selection.record
        if self.prune is not None:
            saved['prune'] = self.prune
        raw = control._encode(saved)
        with self.guard():
            control._immutable(self.runtime.project, self.directory+'/review-result.json',
                control._encode(dict(value=saved, sha256=control._hash(raw))), self.budget)
        return self._publish(saved)

    def _publish(self, saved):
        self._unchanged()
        if (saved.get('format') != FORMAT or saved.get('request') != self.request
                or saved.get('outcome') not in ('stop', 'continue', 'deferred')
                or saved.get('input_pin') != self.runtime.pin
                or saved.get('source_state_sha256') != self.original_digest
                or saved.get('source_segment') != self.segment):
            raise control.StateConflict('Prepared Review result differs from its exact input request.')
        if saved['outcome'] == 'stop':
            self._notification(saved)
        elif 'notification' in saved:
            raise control.StateConflict('Only a stopped Review can publish its Stop notification.')
        output = saved.get('output_segment')
        selection = self._selection(saved)
        if saved.get('prune'):
            prune = saved['prune']
            self.prepare_prune([dict(segment=dict(revision=revision)) for revision in prune['available']], prune['kept'])
            if prune != self.prune:
                raise control.StateConflict('Prepared cleanup changed its Review batch or branch.')
        if selection is None and (not isinstance(output, dict)
                or {key:value for key,value in output.items() if key != '_h3_review_decision'} != self.segment
                or (output.get('_h3_review_decision') is not None
                    and output['_h3_review_decision'].get('action') != 'retry')):
            raise control.StateConflict('Prepared Review result changes its saved take without a candidate selection.')
        base = control.Snapshot(self.runtime.project, saved['publication_root'])
        self.runtime.store.validate_snapshot(self.runtime.base, current=base)
        self.runtime.store.validate_snapshot(base, current=self.runtime.check())
        scope = 'branch:'+self.runtime.selected
        if base.state['scope_revisions'].get(scope) != self.runtime.base.state['scope_revisions'].get(scope):
            raise control.StateConflict('Review source branch changed before its decision could be published.')
        deferred_changes, deferred_scopes = {}, set()
        if saved['outcome'] == 'deferred':
            from .storage_deferred_review import address, validate_publication
            if selection is not None or saved.get('prune') or output != self.segment:
                raise ValueError('Deferring a batch cannot select, retry or delete candidates.')
            deferred_scopes = validate_publication(self, saved.get('deferred'), base)
            deferred_changes[address(self.runtime.selected, self.operation)] = dict(
                data=control._encode(saved['deferred']), scope='jobs:'+self.operation,
                category='reviews', immutable=True)
        elif 'deferred' in saved:
            raise ValueError('Deferred inventory requires its exact stopping transition.')
        with self.guard():
            self._branch_unchanged()
            changes, scopes = deferred_changes, {'branch:'+self.runtime.selected} | deferred_scopes
            if selection is not None:
                selection.verify(base)
                changes.update(selection.controls)
                scopes.update(selection.scopes)
            changes[_address(self.operation)] = dict(data=control._encode(saved), scope='jobs:'+self.operation,
                category='jobs', immutable=True)
            receipt = self.runtime.store.commit(base, changes, operation_id=self.operation, read_scopes=scopes)
            self.runtime.record_commit(receipt)
        return self._result(saved, receipt)

    def video(self, plan, segment, audio, retain_previous=False):
        """Preview immutable saved media; mux into an optional, indexed artifact."""
        runtime, chain = self.runtime, self.chain
        address = runtime.reader.address(segment['segment'])
        source = runtime.store.payload_path(runtime.base, address, verify=True)
        def item(path):
            relative = Path(path).relative_to(runtime.output)
            return dict(filename=relative.name, subfolder=relative.parent.as_posix(), type='output')
        if audio is None:
            return item(source), False, 'No audio is connected; this review is silent.'
        runtime.exports.require_write()
        frames = int(segment['delivered_frames'])
        waveform, rate = chain._validate_audio(audio, 'H3 Chain Review audio', expected_frames=frames)
        value = dict(waveform=waveform, sample_rate=rate)
        request = dict(input_pin=runtime.pin, revision=segment['revision'],
                       source=address, frames=frames, audio_sha256=execution_digest(value))
        operation = uuid.uuid5(uuid.UUID(self.operation), 'preview:'+control._hash(control._encode(request))).hex
        logical = ('' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/')+'reviews/'+operation+'.mp4'
        witness = 'jobs/'+operation+'/review-preview.json'
        with runtime.exports.guard(self.proof):
            receipt = runtime.check().state['operations'].get(operation)
            if receipt is not None:
                snapshot = runtime.store.committed_snapshot(receipt)
                if control._decode(snapshot.read(witness)) != request:
                    raise control.StateConflict('Review preview retry differs from its accepted request.')
                path = runtime.store.payload_path(snapshot, logical, verify=True)
                with control._lock(runtime.project):
                    runtime.store._acknowledge_commit()
                runtime.record_commit(receipt)
                return item(path), True, ''
            directory = 'project/jobs/'+operation
            control._immutable(runtime.project, directory+'/request.json', control._encode(request), self.budget)
        # No ownership lock while running the encoder. Private failed staging
        # is retained; publication checks the original proof again afterward.
        prepared = confined(runtime.project, directory+'/prepared.json')
        if not prepared.exists():
            attempt = directory+'/e-'+uuid.uuid4().hex[:16]
            # An interrupted attempt is evidence, not an output to adopt or
            # overwrite. A fresh bounded name lets this same job retry safely.
            if __package__:
                from .storage_layout import OrganizedStorageLayout
            else:
                from storage_layout import OrganizedStorageLayout
            layout = OrganizedStorageLayout(str(runtime.project), self.budget)
            for name in ('video.mp4', 'audio.wav'):
                layout.check_budget(attempt+'/'+name)
            control._mkdir(confined(runtime.project, attempt), runtime.project)
            path = confined(runtime.project, attempt+'/video.mp4')
            ffmpeg = chain._usable_ffmpeg()
            if ffmpeg:
                wav = confined(runtime.project, attempt+'/audio.wav')
                chain._write_wav(value, str(wav))
                chain._run_ffmpeg([ffmpeg, '-n', '-i', str(source), '-i', str(wav),
                    '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-t', '%.9f' % (frames/float(chain.FPS)), '-movflags', '+faststart', str(path)], timeout_seconds=60.0)
            else:
                chain._pyav_mux_audio(str(source), value, str(path), 192, frames)
            self._unchanged()
            with runtime.exports.guard(self.proof):
                control._immutable(runtime.project, directory+'/prepared.json',
                    control._encode(dict(request=request, path=attempt+'/video.mp4',
                        sha256=chain._file_sha256(str(path)))), self.budget)
        saved = control._decode(control._read_bytes(prepared))
        if not re.fullmatch(re.escape(directory)+r'/e-[0-9a-f]{16}/video\.mp4', str(saved.get('path'))):
            raise ValueError('Prepared Review preview escapes its exact job.')
        path = confined(runtime.project, saved['path'])
        if saved != dict(request=request, path=saved['path'], sha256=chain._file_sha256(str(path))):
            raise control.StateConflict('Review preview staging differs from its prepared bytes.')
        with runtime.exports.guard(self.proof):
            self._branch_unchanged()
            staged = runtime.store.stage_payload(logical, path, 'project/optional/previews/'+operation+'/video.mp4',
                scope='jobs:'+operation, operation_id=operation)
            document = runtime.base.state['documents'][payload_key(address)]
            receipt = runtime.store.commit_artifacts(runtime.accepted,
                {witness:dict(data=control._encode(request), scope='jobs:'+operation, category='jobs', immutable=True)},
                [staged], operation_id=operation, read_scopes={document['scope'], 'branch:'+runtime.selected})
            runtime.record_commit(receipt)
        return item(runtime.store.payload_path(runtime.accepted, logical, verify=True)), True, ''
