"""Atomic, retryable prompt history on explicitly hosted storage copies.

The existing history methods own editing semantics. This port only stages
their logical I/O and publishes revision/index/receipt together. Imported
immutable revision descriptors are versioned with a witness, never overwritten
on disk; executed prompt content remains protected while labels may change.
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
    from .storage_resolver import confined
else:
    import storage_state as state
    from branch_scope import current_branch
    from project_ownership import project_write_guard
    from storage_resolver import confined


FORMAT = 'h3_prompt_history_operation_v1'
METHODS = ('save_draft', 'activate', 'set_label', 'set_archived', 'delete_draft', 'mark_executed')


class RuntimePromptHistory:
    def __init__(self, runtime):
        self.runtime = runtime
        self.after_stage = None

    def require_write(self):
        runtime = self.runtime
        runtime.check()
        if not runtime.history_writes or runtime.node_history_write.get() is False:
            raise ValueError('Runtime binding is read-only; prompt history writes were not enabled.')
        if current_branch(runtime.run) != runtime.selected:
            raise ValueError('Prompt history write belongs to a different runtime branch.')

    @contextmanager
    def guard(self, proof):
        self.require_write()
        runtime = self.runtime
        if runtime.has_node_proof:
            if proof is not None and proof != runtime.node_write_proof:
                raise ValueError('Prompt history cannot change node ownership proof.')
            proof = runtime.node_write_proof
        with project_write_guard(runtime.output, runtime.run, proof, 'change prompt history'):
            yield
            self.require_write()

    def mutate(self, method, inputs, operation_id, proof):
        if __package__:
            from .prompt_history import PromptHistoryStore, _strict_run_name, _safe_component, _timestamp
        else:
            from prompt_history import PromptHistoryStore, _strict_run_name, _safe_component, _timestamp
        runtime = self.runtime
        self.require_write()
        operation = state._token(operation_id)
        if method.__name__ not in METHODS or getattr(PromptHistoryStore, method.__name__).__wrapped__ is not method:
            raise ValueError('Unknown prompt-history mutation.')
        inputs = copy.deepcopy(inputs)
        if _strict_run_name(inputs['run_name']) != runtime.run:
            raise ValueError('Prompt history belongs to a different project.')
        scene = _safe_component(inputs['scene_id'], 'scene ID')
        prefix = '' if runtime.selected == 'main' else 'branches/'+runtime.selected+'/'
        directory = prefix+'prompt_history/'+scene+'/'
        witness_address = prefix+'prompt_history/.operations/'+operation+'.json'
        digest = state._hash(state._encode(dict(method=method.__name__, inputs=inputs,
                                               input_pin=runtime.pin)))
        with self.guard(proof):
            current = runtime.check()
            receipt = current.state['operations'].get(operation)
            if receipt is not None:
                saved = runtime.store.committed_snapshot(receipt)
                witness = state._decode(saved.read(witness_address))
                if (witness.get('format') != FORMAT or witness.get('operation_id') != operation
                        or witness.get('request_sha256') != digest or witness.get('input_pin') != runtime.pin):
                    raise state.StateConflict('History operation identity was reused with different inputs.')
                # A delayed acknowledgement may not make newer edits look like
                # the active draft. Other branches can advance independently.
                for scope, revision in receipt['scope_revisions'].items():
                    if current.state['scope_revisions'].get(scope) != revision:
                        raise state.StateConflict('Prompt history changed after this operation; reload before retry.')
                runtime.store._acknowledge_commit()
                runtime.record_commit(receipt)
                return copy.deepcopy(witness['result'])

            base = runtime.accepted
            runtime.store.validate_snapshot(base, current=current)
            documents = base.state['documents']
            if runtime.selected != 'main' and prefix+'branch.json' not in documents:
                raise ValueError('Prompt-history branch is unavailable at the input pin.')
            # One timestamp/ID for every retry, even after a staging interruption.
            # No prompt or ownership secret is put in the request intent.
            path = confined(runtime.project, 'project/jobs/history-'+operation+'.json')
            expected = dict(format=FORMAT, operation_id=operation, request_sha256=digest,
                            input_pin=runtime.pin, base=base.reference)
            if path.exists():
                envelope = state._decode(state._read_bytes(path))
                intent = envelope.get('value')
                if (not isinstance(intent, dict) or state._hash(state._encode(intent)) != envelope.get('sha256')
                        or set(intent) != set(expected)|{'timestamp'}
                        or {key:intent[key] for key in expected} != expected
                        or not isinstance(intent['timestamp'], str)):
                    raise state.StateConflict('Prepared history request differs from this operation.')
            else:
                intent = dict(expected, timestamp=_timestamp())
                state._immutable(runtime.project, path.relative_to(runtime.project).as_posix(),
                    state._encode(dict(value=intent, sha256=state._hash(state._encode(intent)))),
                    runtime.store._marker()[0]['path_budget'])

            documents_port = _HistoryDocuments(runtime, base, directory, intent['timestamp'], operation)
            history = PromptHistoryStore(runtime.output)
            history._history_documents = documents_port
            result = method(history, **inputs)
            witness = dict(expected, result=result,
                retired=documents_port.retired, replaced=documents_port.replaced)
            changes = documents_port.changes
            changes[witness_address] = dict(data=state._encode(witness),
                scope='history:'+runtime.selected, category='history', immutable=True)
            self.require_write()
            receipt = runtime.store._commit_changes(base, changes, operation_id=operation,
                read_scopes=documents_port.reads, retire_documents=documents_port.retired,
                replace_documents=documents_port.replaced, after_stage=self.after_stage)
            runtime.record_commit(receipt)
            return result


class _HistoryDocuments:
    def __init__(self, runtime, base, directory, timestamp, operation):
        self.runtime, self.base, self.directory = runtime, base, directory
        self.timestamp = timestamp
        self.revision_id = uuid.uuid5(uuid.UUID(operation), 'prompt-revision').hex
        self.documents = base.state['documents']
        self.changes, self.retired, self.replaced = {}, {}, {}
        # Branch changes/activation/deletion cannot race a queued history edit.
        self.reads = {'branch:'+runtime.selected, 'history:'+runtime.selected}

    def address(self, path):
        self.runtime.history.require_write()
        address = Path(path).relative_to(self.runtime.project).as_posix()
        state._logical(address)
        if not address.startswith(self.directory) or not re.fullmatch(
                r'(?:index|[0-9a-f]{32})\.json', address[len(self.directory):]):
            raise ValueError('History transaction escapes its selected scene.')
        return address

    def contract(self, address):
        descriptor = self.documents.get(address)
        if descriptor is not None:
            if (descriptor['category'] != 'history'
                    or descriptor['scope'] not in ('history:'+self.runtime.selected, 'branch:'+self.runtime.selected)
                    or (address.endswith('/index.json') and descriptor['immutable'])):
                raise ValueError('Unsupported prompt-history import contract.')
            self.reads.add(descriptor['scope'])
            return {key:descriptor[key] for key in ('scope','category','immutable')}
        return dict(scope='history:'+self.runtime.selected, category='history',
                    immutable=not address.endswith('/index.json'))

    def read(self, path):
        address = self.address(path)
        self.contract(address)
        if address in self.retired:
            raise FileNotFoundError('Prompt revision was deleted in this transaction.')
        if address in self.changes:
            return state._decode(self.changes[address]['data'])
        if address not in self.documents:
            raise FileNotFoundError('Missing prompt-history document: '+address)
        try:
            return state._decode(self.base.read(address))
        except FileNotFoundError as exc:
            raise state.StateConflict('Accepted prompt-history bytes are missing: '+address) from exc

    def write(self, path, value):
        address = self.address(path)
        contract = self.contract(address)
        if address in self.retired:
            raise ValueError('Cannot write and delete the same prompt revision.')
        if address in self.documents:
            before = state._decode(self.base.read(address))
            if not address.endswith('/index.json') and before.get('executed_at'):
                for key in ('id','prompt','prompt_sha256','parent_id','created_at','executed_at'):
                    if before.get(key) != value.get(key):
                        raise state.StateConflict('Executed prompt content is immutable.')
            if contract['immutable']:
                self.replaced[address] = self.documents[address]
        self.changes[address] = dict(data=state._encode(value), **contract)

    def delete(self, path):
        address = self.address(path)
        if address.endswith('/index.json') or address in self.changes:
            raise ValueError('Only an existing inactive draft can be deleted.')
        revision = self.read(path)
        index = self.read(self.runtime.project/(self.directory+'index.json'))
        if (revision.get('executed_at') or index.get('active_revision') == revision.get('id')
                or any(item.get('id') == revision.get('id') or item.get('parent_id') == revision.get('id')
                       for item in index['revisions'])):
            raise ValueError('Executed, active or referenced prompt revisions cannot be deleted.')
        self.retired[address] = self.documents[address]
