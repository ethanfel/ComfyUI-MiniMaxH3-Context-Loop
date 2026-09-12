"""Undo an ordinary recovered quarantine after importing that tree again.

The initial import is the custody boundary. Immutable approval, file inventory,
and completion witnesses are checked before restoring anything. Files publish
atomically with the restored marker; retained bytes and previous pins survive.
This adapter does not import an older organized store's external authority.
"""
import copy
import uuid

from . import storage_state as state
from .storage_project import payload_key, PREFIX
from .storage_quarantine import _address, _change
from .storage_recovered_review import RecoveredReview, FORMAT, _hash
from .storage_deferred_review import address, validate_identity
from .storage_deferred_finalization import decision_address, project_decision
from .storage_resolver import confined
from .storage_layout import OrganizedStorageLayout
from .storage_retention import _operation

UNDO_RECEIPT = 'h3_imported_recovered_undo_v1'


class ImportedRecoveredUndo(RecoveredReview):
    """Typed retention transaction, not a legacy file-moving writer."""

    def __init__(self, retention, operation, *, base=None):
        from . import chain_nodes
        self.retention, self.runtime = retention, retention.runtime
        retention.check()
        self.chain = chain_nodes
        self.run, self.branch = self.runtime.run, self.runtime.selected
        self.root = self.runtime.project
        self.base = base or self.runtime.accepted
        self.original_operation = state._token(operation)
        self.initial = self.base
        self.joined = None
        while self.initial.state['parent'] is not None:
            parent = state.Snapshot(self.root,self.initial.state['parent'])
            if parent.state['generation'] != self.initial.state['generation']-1:
                raise state.StateConflict('Imported quarantine has inconsistent accepted ancestry.')
            if (any(key.startswith(PREFIX) for key in self.initial.state['documents'])
                    and not any(key.startswith(PREFIX) for key in parent.state['documents'])
                    and self.initial.state['epoch'] == parent.state['epoch']+1):
                self.joined = self.initial
            self.initial = parent
        if not self.initial.state.get('import_source'):
            raise state.StateConflict('Quarantine was not part of this project import.')
        self.plan = self.imported(self._key(operation,'plan'))
        self.token = state._token(self.plan.get('token'))
        self.document = validate_identity(self.imported(address(self.branch,self.token)),
            self.run,self.branch,self.token)
        self.decision = self.imported(decision_address(self.branch,self.token))
        project_decision(self.document,self.decision,None)
        self.request = self.decision['request']
        self.candidates = {state._token(c['segment']['revision']):c['segment'] for c in self.document['candidates']}
        if len(self.candidates) != len(self.document['candidates']):
            raise state.StateConflict('Imported review repeats a candidate.')
        self.scene = self.document['scene']
        self._validate_plan(self.plan,operation)
        self.fingerprint = dict(format=FORMAT,plan_sha256=state._hash(state._encode(self.plan)))
        if self.imported(self._key(operation,'complete')) != self.fingerprint:
            raise state.StateConflict('Finish the interrupted recovered quarantine before undo.')
        self.restored = self.optional(self._key(operation,'restored'))
        if self.restored is not None and self.restored != self.fingerprint:
            raise state.StateConflict('Imported quarantine undo witness changed.')

    def read(self, key):
        return state._decode(self.base.read(key))

    def optional(self, key):
        return self.read(key) if key in self.base.state['documents'] else None

    def imported(self, key):
        descriptor = self.initial.state['documents'].get(key,{})
        if (not descriptor.get('immutable') or descriptor != self.base.state['documents'].get(key)
                or self.initial.read(key) != self.base.read(key)):
            raise state.StateConflict('Imported quarantine evidence changed: '+key)
        return state._decode(self.initial.read(key))

    def _source(self, key):
        documents = self.base.state['documents']
        if key in documents:
            self.base.read(key)
            return confined(self.root,documents[key]['file']['path']),key
        indexed = payload_key(key)
        if indexed in documents:
            return self.runtime.store.payload_path(self.base,key,verify=True),indexed
        return None,None

    def _locations(self, item):
        present = []
        for name in ('path','target'):
            source,key = self._source(item[name])
            if source is None:
                continue
            if _hash(source) != {k:item[k] for k in ('sha256','size')}:
                raise state.StateConflict('Imported quarantine bytes changed: '+item[name])
            if name == 'target':
                origin = self.joined if key.startswith(PREFIX) else self.initial
                descriptor = origin.state['documents'].get(key,{}) if origin else {}
                if not descriptor.get('immutable') or descriptor != self.base.state['documents'].get(key):
                    raise state.StateConflict('Quarantine payload was not retained from the initial import.')
            present.append(source)
        if len(present) != 1:
            raise state.StateConflict('Quarantine file is missing or its restore destination is occupied.')
        return present

    def preview_undo(self, operation):
        if state._token(operation) != self.original_operation:
            raise state.StateConflict('Imported undo changed its quarantine target.')
        for item in self.plan['files']:
            self._locations(item)
            original,_ = self._source(item['path'])
            if (original is not None) != (self.restored is not None):
                raise state.StateConflict('Imported undo destination differs from its completion witness.')
        value = dict(format=FORMAT,run_name=self.run,branch_id=self.branch,operation_id=operation,
            allowed=True,blockers=[],restored=self.restored is not None,base=self.base.reference,
            plan_sha256=self.fingerprint['plan_sha256'],files=copy.deepcopy(self.plan['files']),reclaimed_bytes=0)
        return dict(value,snapshot=state._hash(state._encode(value)))

    def _restore_changes(self, undo):
        """Verify independent staging, then build the exact catalogue delta."""
        store = self.runtime.store
        scope = 'archive:'+self.plan['revision']
        controls,retiring = {},{}
        storage_id = uuid.uuid5(uuid.UUID(undo),'restored:'+self.plan['revision']).hex
        policy = OrganizedStorageLayout(str(self.root),store._marker()[0]['path_budget'])
        media = policy.media('generation',storage_id)
        prefix = 'h3_chains/'+self.run+'/'
        segment = self.candidates[self.plan['revision']]
        destinations = {segment[field][len(prefix):]:media[role]
            for field,role in (('segment','video'),('checkpoint','checkpoint'),
                               ('generated_audio','audio'),('blend_segment','overlap')) if segment.get(field)}
        if segment.get('prompt_file'):
            destinations[segment['prompt_file'][len(prefix):]] = policy.take_prompt(storage_id)
        metadata = 'checkpoints/clip_%04d.%s.json' % (self.scene,self.plan['revision'])
        for item in self.plan['files']:
            source,key = self._source(item['target'])
            if source is None or self._source(item['path'])[0] is not None:
                raise state.StateConflict('Quarantine restore source or destination changed.')
            retiring[key] = self.base.state['documents'][key]
            if item['path'] == metadata or item['path'].startswith('recovery_archives/'):
                raw = state._read_bytes(source)
                if dict(sha256=state._hash(raw),size=len(raw)) != {k:item[k] for k in ('sha256','size')}:
                    raise state.StateConflict('Quarantine control changed during restoration.')
                controls[item['path']] = dict(data=raw,scope=scope,category='takes',immutable=True)
            else:
                # Generated media retains the normal layout. Auxiliary review
                # previews stay optional and never gain checkpoint authority.
                target = destinations.get(item['path']) or 'project/optional/reviews/'+storage_id+'/'+state._hash(item['path'].encode())+'.mp4'
                staged = store.stage_payload(item['path'],source,target,scope=scope,
                    operation_id=uuid.uuid5(uuid.UUID(undo),item['path']).hex)
                record = store._staged_record(staged)
                if {k:record['file'][k] for k in ('sha256','size')} != {k:item[k] for k in ('sha256','size')}:
                    raise state.StateConflict('Restaged quarantine payload differs from its accepted witness.')
                controls[payload_key(item['path'])] = dict(data=state._encode(record),scope=scope,
                    category='payloads',immutable=True)
            if self.retention.after_stage is not None:
                self.retention.after_stage('restore_staged')
        return controls,retiring

    def undo(self, operation, snapshot, *, proof):
        undo = _operation('imported-recovered-undo',self.branch,operation,snapshot)
        store = self.runtime.store
        with self.retention.guard(proof):
            receipt = self.runtime.accepted.state['operations'].get(undo)
            if receipt is not None:
                saved = state._decode(store.committed_snapshot(receipt).read(_address(undo,'recovered_undo')))
                if (saved.get('format') != UNDO_RECEIPT or saved.get('action') != 'imported_recovered_undo'
                        or saved.get('quarantine_operation') != operation or saved.get('snapshot') != snapshot
                        or saved.get('plan_sha256') != self.fingerprint['plan_sha256']):
                    raise state.StateConflict('Imported undo retry differs from the accepted restoration.')
                store._acknowledge_commit()
                return saved['response']
            preview = self.preview_undo(operation)
            if snapshot != preview['snapshot']:
                raise state.StateConflict('Project changed; confirm this exact imported undo preview.')
            response = dict(ok=True,operation_id=undo,quarantine_operation=operation,restored=True,
                            reclaimed_bytes=0,message='Quarantined saved files restored exactly.')
            if self.restored is not None:
                return response
            changes,retiring = self._restore_changes(undo)
            record = dict(format=UNDO_RECEIPT,action='imported_recovered_undo',operation_id=undo,
                quarantine_operation=operation,snapshot=snapshot,base=self.base.reference,
                plan_sha256=self.fingerprint['plan_sha256'],response=response)
            # This is not a damaged-payload custody exception. All removed
            # and introduced payloads still undergo ordinary hash validation.
            changes[_address(undo,'recovered_undo')] = _change(state._encode(record),undo)
            changes[self._key(operation,'restored')] = _change(state._encode(self.fingerprint),operation)
            with state._lock(store.project):
                if store.snapshot().reference != self.base.reference:
                    raise state.StateConflict('Project changed before restoration; refresh the undo preview.')
                # Keep retained physical slots for old pins. Only catalogue
                # identities move, atomically with the portable restored mark.
                receipt = store._commit_changes(self.base,changes,operation_id=undo,
                    retire_documents=retiring,after_stage=self.retention.after_stage)
            self.runtime.record_commit(receipt)
            return response
