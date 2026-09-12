"""Typed runtime undo using imported portable quarantine custody."""
from pathlib import PurePosixPath
import uuid

if __package__:
    from . import storage_state as state
    from .storage_project import payload_key
    from .storage_quarantine import _address, _change, STATUS
    from .storage_retention import _operation
    from .storage_retention_portability import PortableQuarantine, address
    from .storage_layout import MEDIA_STAGES
else:
    import storage_state as state
    from storage_project import payload_key
    from storage_quarantine import _address, _change, STATUS
    from storage_retention import _operation
    from storage_retention_portability import PortableQuarantine, address
    from storage_layout import MEDIA_STAGES

FORMAT = 'h3_imported_quarantine_undo_v1'


def _target(reference, operation, logical):
    parts = reference['path'].split('/')
    index = None
    if parts[0] == 'media' and len(parts) >= 4 and parts[1] in MEDIA_STAGES:
        index = 2 if parts[1] in ('generation','alternate') else 3
    elif parts[0] == 'exports' and len(parts) >= 4 and parts[1] in ('png','video','audio'):
        index = 2
    if index is not None and len(parts) > index+1:
        state._token(parts[index])
        parts[index] = uuid.uuid5(uuid.UUID(operation),'owner:'+'/'.join(parts[:index+1])).hex
        return '/'.join(parts)
    # An explicitly classified legacy/optional payload has no inferable model
    # stage. Keep it as recovery data, preserving its extension for consumers.
    suffix = PurePosixPath(reference['path']).suffix or PurePosixPath(logical).suffix or '.bin'
    return 'project/recovery/'+operation+'/'+state._hash(logical.encode())[:32]+suffix


class ImportedQuarantineUndo:
    def __init__(self, retention, operation):
        retention.check()
        self.retention,self.runtime = retention,retention.runtime
        self.operation = state._token(operation)
        self.base = self.runtime.accepted
        self.initial = self.base
        while self.initial.state['parent'] is not None:
            self.initial = state.Snapshot(self.runtime.project,self.initial.state['parent'])
        if not self.initial.state.get('import_source'):
            raise state.StateConflict('Portable quarantine was not imported into this project.')
        for key in (address(operation),_address(operation)):
            descriptor = self.initial.state['documents'].get(key,{})
            if (not descriptor.get('immutable') or descriptor != self.base.state['documents'].get(key)
                    or self.initial.read(key) != self.base.read(key)):
                raise state.StateConflict('Imported quarantine authority changed after its initial copy.')
        self.capsule = PortableQuarantine.from_store(self.runtime.store,self.base,operation)
        self.domain = retention._domain(self.capsule.record)
        self.status = state._decode(self.base.read(_address(operation,'state')))
        if (self.status.get('format') != STATUS or self.status.get('operation_id') != operation
                or self.status.get('status') not in ('quarantined','restored')):
            raise state.StateConflict('Imported quarantine has an invalid state.')

    def _unchanged(self, key):
        descriptor = self.base.state['documents'].get(key)
        if descriptor is None or descriptor != self.initial.state['documents'].get(key):
            raise state.StateConflict('Undo would overwrite later work: '+key)
        expected = self.capsule.published['documents'][key]
        if (any(descriptor[k] != expected[k] for k in ('scope','category','immutable'))
                or self.base.read(key) != self.capsule.data(expected['file'])):
            raise state.StateConflict('Imported undo differs from the accepted retirement: '+key)
        self._owner(descriptor)
        return descriptor

    def _owner(self, descriptor):
        if descriptor['scope'].startswith('branch:') and descriptor['scope'] != 'branch:'+self.runtime.selected:
            raise state.StateConflict('Undo cannot replace another branch\'s controls.')

    def preview_undo(self, operation):
        if operation != self.operation:
            raise state.StateConflict('Portable undo changed its target.')
        blockers = []
        if self.status['status'] != 'quarantined':
            blockers.append('This quarantine is no longer pending undo.')
        else:
            documents = self.base.state['documents']
            for item in self.capsule.preview['items']:
                self._owner(item['descriptor'])
                if item['address'] in documents or payload_key(item['address']) in documents:
                    blockers.append('Undo would overwrite a later assignment or artifact: '+item['address'])
                if 'custody' in item:
                    blockers.append('Portable damaged-payload restoration still requires its custody transaction adapter: '+item['address'])
            for item in self.capsule.preview.get('updates',[])+self.capsule.preview.get('archives',[]):
                self._unchanged(item['address'])
            descriptor = documents[_address(operation,'state')]
            if descriptor['immutable']:
                blockers.append('Imported quarantine state must retain its mutable recovery contract.')
        result = dict(format=FORMAT,operation_id=operation,run_name=self.runtime.run,
            branch_id=self.runtime.selected,scene=self.domain['scene'],revision=self.domain['revision'],
            base=self.base.reference,capsule_sha256=state._hash(self.base.read(address(operation))),
            allowed=not blockers,blockers=blockers,reclaimed_bytes=0)
        return dict(result,snapshot=state._hash(state._encode(result)))

    def undo(self, operation, snapshot, *, proof):
        undo = _operation('portable-undo',self.runtime.selected,operation,snapshot)
        store = self.runtime.store
        with self.retention.guard(proof):
            receipt = self.runtime.accepted.state['operations'].get(undo)
            if receipt is not None:
                saved = state._decode(store.committed_snapshot(receipt).read(_address(undo,'portable_undo')))
                if (saved.get('format') != FORMAT or saved.get('quarantine_operation') != operation
                        or saved.get('snapshot') != snapshot
                        or saved.get('capsule_sha256') != state._hash(self.base.read(address(operation)))):
                    raise state.StateConflict('Portable undo retry differs from its accepted request.')
                store._acknowledge_commit()
                return dict(saved['response'],storage_pin=self.runtime.output_pin)
            preview = self.preview_undo(operation)
            if not preview['allowed'] or snapshot != preview['snapshot']:
                raise state.StateConflict(' '.join(preview['blockers']) or 'Refresh the exact imported undo preview.')
            changes,retiring,replacing = {},{},{}
            for item in self.capsule.preview['items']:
                descriptor = item['descriptor']
                if 'payload' in item:
                    old = item['payload']
                    staged = store.stage_payload(item['address'],self.capsule.file(old['file']),
                        _target(old['file'],undo,item['address']),scope=old['scope'],immutable=old['immutable'],
                        operation_id=uuid.uuid5(uuid.UUID(undo),item['address']).hex)
                    record = store._staged_record(staged)
                    if any(record['file'][k] != old['file'][k] for k in ('sha256','size')):
                        raise state.StateConflict('Restaged payload differs from the retired bytes.')
                    changes[item['key']] = dict(data=state._encode(record),scope=old['scope'],
                                               category='payloads',immutable=old['immutable'])
                else:
                    changes[item['key']] = dict(data=self.capsule.data(descriptor['file']),
                        **{k:descriptor[k] for k in ('scope','category','immutable')})
                if self.retention.after_stage is not None:
                    self.retention.after_stage('restore_staged')
            for item in self.capsule.preview.get('updates',[]):
                key,descriptor = item['address'],item['descriptor']
                current = self._unchanged(key)
                changes[key] = dict(data=self.capsule.data(descriptor['file']),
                    **{k:descriptor[k] for k in ('scope','category','immutable')})
                if current['immutable']:
                    replacing[key] = current
            for item in self.capsule.preview.get('archives',[]):
                retiring[item['address']] = self._unchanged(item['address'])
            key = _address(operation,'state')
            changes[key] = dict(data=state._encode(dict(format=STATUS,status='restored',
                operation_id=operation,undo_operation=undo)),
                **{k:self.base.state['documents'][key][k] for k in ('scope','category','immutable')})
            response = dict(ok=True,operation_id=undo,quarantine_operation=operation,
                            reclaimed_bytes=0,message='Quarantined saved files restored exactly.')
            saved = dict(format=FORMAT,quarantine_operation=operation,snapshot=snapshot,
                         capsule_sha256=preview['capsule_sha256'],response=response)
            changes[_address(undo,'portable_undo')] = _change(state._encode(saved),undo)
            with state._lock(store.project):
                if store.snapshot().reference != self.base.reference:
                    raise state.StateConflict('Project changed before restoration; refresh the undo preview.')
                receipt = store._commit_changes(self.base,changes,operation_id=undo,
                    retire_documents=retiring,replace_documents=replacing,after_stage=self.retention.after_stage)
            self.runtime.record_commit(receipt)
            return dict(response,storage_pin=self.runtime.output_pin)
